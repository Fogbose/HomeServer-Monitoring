import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import MagicMock,patch

ROOT=Path(__file__).resolve().parents[1]
with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=1,LOCK_NB=2)}):
    spec=importlib.util.spec_from_file_location('sessions',ROOT/'scripts/debian/homeserver-sessions.py')
    sessions=importlib.util.module_from_spec(spec);spec.loader.exec_module(sessions)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.backend=MagicMock();self.saved=sessions.p;sessions.p=self.backend
        self.policy=json.loads((ROOT/'infrastructure/monitoring/power-policy.json').read_text())
    def tearDown(self):sessions.p=self.saved
    def test_preparation_needs_explicit_confirmation(self):
        with self.assertRaisesRegex(RuntimeError,'Confirmation'):sessions.prepare(self.policy,False)
        self.backend.common_checks.assert_not_called()
    def test_current_ssh_is_preserved(self):
        self.backend.run.side_effect=['12',json.dumps([{'session':'12'},{'session':'13'}]),'Service=sshd\nRemote=yes\nClass=user']
        current,others=sessions.ssh_sessions()
        self.assertEqual(current,'12');self.assertEqual(others,['13'])
    def test_unrecognised_local_session_blocks_disconnection(self):
        self.backend.run.side_effect=['12',json.dumps([{'session':'12'},{'session':'13'}]),'Service=login\nRemote=no\nClass=user']
        with self.assertRaisesRegex(RuntimeError,'non SSH'):sessions.ssh_sessions()
    def test_background_sessions_are_not_terminated(self):
        self.backend.run.side_effect=['12',json.dumps([{'session':'12'},{'session':'13'}]),'Service=systemd-user\nRemote=no\nClass=manager']
        self.assertEqual(sessions.ssh_sessions()[1],[])
    def test_untrusted_session_id_is_rejected(self):
        self.backend.run.side_effect=['12',json.dumps([{'session':'12'},{'session':'13;reboot'}])]
        with self.assertRaisesRegex(RuntimeError,'Identifiant'):sessions.ssh_sessions()
    def test_recovery_uses_the_same_guarded_path_as_boot(self):
        with tempfile.TemporaryDirectory() as temporary,patch.object(sessions,'STATE',Path(temporary)/'state'):
            sessions.recover(self.policy)
        self.backend.start_registered.assert_called_once_with(self.policy)
    def test_failed_recovery_preserves_state_for_manual_intervention(self):
        with tempfile.TemporaryDirectory() as temporary:
            state=Path(temporary)/'state';state.write_text('{}')
            with patch.object(sessions,'STATE',state):
                self.backend.start_registered.side_effect=RuntimeError('unavailable')
                with self.assertRaisesRegex(RuntimeError,'unavailable'):sessions.recover(self.policy)
                self.assertTrue(state.exists())
    def test_expired_preparation_never_requests_poweroff(self):
        with patch.object(sessions,'saved',return_value={'phase':'prepared','time':1}),patch.object(sessions.time,'time',return_value=1000):
            with self.assertRaisesRegex(RuntimeError,'ancienne'):sessions.finish(self.policy,'poweroff')
        self.backend.run.assert_not_called()
    def test_new_container_blocks_finishing(self):
        data={'phase':'prepared','time':100,'ids':{'app':'a','cron':'c','db':'d','redis':'r'}}
        self.backend.run.return_value='d\nr\nunknown'
        with patch.object(sessions,'saved',return_value=data),patch.object(sessions.time,'time',return_value=150):
            with self.assertRaisesRegex(RuntimeError,'modifiés'):sessions.finish(self.policy,'poweroff')
        self.backend.compose.assert_not_called()
    def test_prepared_check_does_not_require_running_web_or_cron(self):
        data={'phase':'prepared','time':100,'ids':{'app':'a','cron':'c','db':'d','redis':'r'}}
        def run(*args):
            if args[:2]==('docker','ps'):return 'd\nr'
            cid=args[-1]
            return json.dumps({'Running':cid in ('d','r'),'ExitCode':0,'Paused':False})
        self.backend.run.side_effect=run
        with patch.object(sessions,'saved',return_value=data),patch.object(sessions.time,'time',return_value=150):
            self.assertEqual(sessions.check_prepared(self.policy),data['ids'])
        self.backend.compose.assert_not_called()
    def test_successful_preparation_never_leaves_maintenance_enabled_while_waiting(self):
        events=[];ids={'app':'a','cron':'c','db':'d','redis':'r'}
        self.backend.containers.return_value=ids
        self.backend.POLICY.read_bytes.return_value=b'policy'
        self.backend.configuration_hashes.return_value={}
        self.backend.cron_idle.return_value=True
        def compose(module,*args):
            if 'status' in args:return json.dumps({'installed':True,'maintenance':False,'needsDbUpgrade':False,'versionstring':'34.0.5'})
            if 'maintenance:mode' in args:events.append('maintenance-'+args[-1])
            return ''
        def run(*args):
            if args[:2]==('docker','inspect'):return '{"Running":false,"ExitCode":0}'
            return 'PID COMMAND\n1 busybox'
        self.backend.compose.side_effect=compose;self.backend.run.side_effect=run
        state=MagicMock();state.exists.return_value=False
        def write(data):events.append(dict(data))
        with patch.object(sessions,'STATE',state),patch.object(sessions,'ssh_sessions',return_value=('12',[])),patch.object(sessions,'write_state',side_effect=write),patch.object(sessions.Path,'read_text',return_value='boot-id'):
            sessions.prepare(self.policy,True)
        final=events[-1]
        self.assertEqual(final['phase'],'prepared');self.assertFalse(final['maintenance_owned'])
        self.assertIn('maintenance---off',events[:-1])
        self.assertEqual(final['schema'],2)
        self.assertFalse(any(c.args[:1]==('systemctl',) for c in self.backend.run.call_args_list))


if __name__=='__main__':unittest.main()
