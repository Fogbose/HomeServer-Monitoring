import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import types
import sys
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch, MagicMock

ROOT=Path(__file__).resolve().parents[1]
def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module
console=load('monitor_console',ROOT/'infrastructure/monitoring/console.py')
with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(flock=lambda *args:None,LOCK_EX=1,LOCK_NB=2)}):
    power=load('power_v2',ROOT/'scripts/debian/homeserver-power-v2.py')


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.configuration=patch.object(console,"local_config",return_value={"allow_actions":True,"hostname":"homeserver"})
        self.configuration.start()
        self.addCleanup(self.configuration.stop)
    def test_disconnect_never_means_powered_off(self):
        alerts=console.evaluate({'connection':'unknown'})
        self.assertEqual(alerts[0]['level'],'critical')
        self.assertIn('indéterminés',alerts[0]['text'])
    def test_stale_data_is_unknown(self):
        alerts=console.evaluate({'connection':'online','observed':1,'disk_percent':1},now=500)
        self.assertIn('périmée',alerts[0]['text'])
    def test_separate_backup_ages(self):
        now=time.time()
        data={'connection':'online','observed':now,'capabilities':{'privileged':True},
              'capture_epoch':now-40*3600,'nextcloud':{'installed':True},
              'windows_backup':{'Result':'Success','CompletedUtc':'2099-01-01T00:00:00Z'}}
        self.assertTrue(any('Capture serveur' in a['text'] for a in console.evaluate(data,now)))
    def test_history_persists(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'history.sqlite'; console.Store(path).save({'received':time.time(),'cpu_percent':12})
            self.assertEqual(console.Store(path).history()[0]['cpu_percent'],12)
    def test_demo_blocks_all_actions(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(console.subprocess,'Popen') as proc:
            monitor=console.Monitor(True,Path(folder))
            with self.assertRaises(ValueError): monitor.action('windows-shutdown','ARRETER CE PC WINDOWS')
            proc.assert_not_called()
    def test_action_needs_exact_confirmation(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(console,'ssh',return_value=['ssh.exe']),patch.object(console.subprocess,'Popen') as proc:
            monitor=console.Monitor(False,Path(folder))
            with self.assertRaises(ValueError): monitor.action('windows-shutdown','yes')
            with self.assertRaises(ValueError): monitor.action('arbitrary-command','whatever')
            proc.assert_not_called()
    def test_remote_actions_require_installed_fresh_bridge(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(console,'ssh',return_value=['ssh.exe']),patch.object(console.subprocess,'Popen') as proc:
            monitor=console.Monitor(False,Path(folder))
            with self.assertRaises(ValueError): monitor.action('server-reboot','REDEMARRER HOMESERVER SANS APPEL')
            proc.assert_not_called()
    def test_only_allowlisted_action_passes_fixed_arguments(self):
        with tempfile.TemporaryDirectory() as folder,patch.object(console,'ssh',return_value=['ssh.exe']),patch.object(console.subprocess,'Popen') as proc:
            monitor=console.Monitor(False,Path(folder)); monitor.action('windows-shutdown','ARRETER CE PC WINDOWS')
            self.assertEqual(proc.call_args.args[0],['shutdown.exe','/s','/t','0'])
    def test_http_auth_origin_and_host_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            monitor=console.Monitor(True,Path(folder)); server=console.ThreadingHTTPServer(('127.0.0.1',0),console.handler(monitor,'test-token'))
            threading.Thread(target=server.serve_forever,daemon=True).start()
            base=f'http://127.0.0.1:{server.server_port}'
            try:
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(base+'/api/status')
                self.assertEqual(error.exception.code,403)
                req=urllib.request.Request(base+'/api/status',headers={'Authorization':'Bearer test-token'})
                self.assertEqual(json.load(urllib.request.urlopen(req))['connection'],'unknown')
                req=urllib.request.Request(base+'/api/refresh',data=b'{}',headers={'Authorization':'Bearer test-token','Origin':'https://attacker.example'})
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
                self.assertEqual(error.exception.code,403)
                req=urllib.request.Request(base+'/',headers={'Host':'attacker.example'})
                with self.assertRaises(urllib.error.HTTPError) as error: urllib.request.urlopen(req)
                self.assertEqual(error.exception.code,403)
            finally: server.shutdown(); server.server_close()
    def test_windows_port_cannot_be_claimed_by_a_second_instance(self):
        server=console.LocalServer(('127.0.0.1',0),console.BaseHTTPRequestHandler)
        try:
            with self.assertRaises(OSError): console.LocalServer(('127.0.0.1',server.server_port),console.BaseHTTPRequestHandler)
        finally: server.server_close()


class PowerV2Tests(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((ROOT/'infrastructure/monitoring/power-policy.json').read_text())
        self.ids={'app':'a','cron':'c','db':'d','redis':'r'}
    def test_memories_override_is_preserved(self):
        with patch.object(power,'run',return_value='') as run:
            power.compose(self.policy['core'],'config','-q')
            self.assertIn('/srv/applications/nextcloud/docker-compose.override.yml',run.call_args.args)
    def test_unknown_container_is_rejected(self):
        def compose(module,*args): return module['project']+args[-1]
        def run(*args):
            if args[0:2]==('docker','inspect'): return json.dumps({'Running':True})
            return 'unregistered-container'
        with patch.object(power,'compose',side_effect=compose),patch.object(power,'run',side_effect=run):
            with self.assertRaisesRegex(RuntimeError,'supplémentaire'): power.containers(self.policy)
    def test_busy_cron_cancels_and_recovers_all_auxiliary_stacks(self):
        with patch.object(power,'stop_module') as stop,patch.object(power,'compose') as compose,patch.object(power,'http_active',return_value=False),patch.object(power,'run',return_value='PID COMMAND\n1 php') as run:
            with self.assertRaisesRegex(RuntimeError,'Cron occupé'): power.transition(self.policy,self.ids,'poweroff')
            self.assertEqual(stop.call_count,2)
            self.assertTrue(any(call.args[1:]==('start',) for call in compose.call_args_list))
            self.assertNotIn(unittest.mock.call('systemctl','poweroff'),run.call_args_list)
    def test_auxiliary_stop_failure_attempts_recovery(self):
        with patch.object(power,'stop_module',side_effect=RuntimeError('stop failed')),patch.object(power,'compose') as compose,patch.object(power,'run') as run:
            with self.assertRaises(RuntimeError): power.transition(self.policy,self.ids,'reboot')
            compose.assert_called_once_with(self.policy['modules'][0],'start')
            run.assert_not_called()
    def test_cron_unknown_is_not_idle(self):
        self.assertFalse(power.cron_idle('PID COMMAND\n1 unknown'))
    def test_talk_needs_human_confirmation(self):
        with patch.object(power,'http_active') as active:
            with self.assertRaisesRegex(RuntimeError,'absence d’appel'): power.talk_idle(self.policy,False)
            active.assert_not_called()
    def test_even_confirmed_shutdown_rejects_signaling_connections(self):
        with patch.object(power,'compose',return_value='hpb-id'),patch.object(power,'http_active',return_value=True):
            with self.assertRaisesRegex(RuntimeError,'signalisation'): power.talk_idle(self.policy,True)
    def test_clean_sequence_requests_only_the_selected_operation(self):
        def run(*args):
            if args[:2]==('docker','top'): return 'PID COMMAND\n1 busybox'
            if args[:2]==('docker','inspect'): return '{"Running":false,"ExitCode":0}'
            return ''
        with patch.object(power,'stop_module') as stop,patch.object(power,'compose'),patch.object(power,'http_active',return_value=False),patch.object(power,'run',side_effect=run) as command:
            power.transition(self.policy,self.ids,'reboot')
            self.assertEqual([c.args[0]['project'] for c in stop.call_args_list],['homeserver-talk-hpb','homeserver-talk'])
            self.assertEqual(command.call_args.args,('systemctl','reboot'))
    def test_forced_core_stop_never_powers_off(self):
        def run(*args):
            if args[:2]==('docker','top'): return 'PID COMMAND\n1 busybox'
            if args[:2]==('docker','inspect'): return '{"Running":false,"ExitCode":137}'
            return ''
        with patch.object(power,'stop_module'),patch.object(power,'compose'),patch.object(power,'http_active',return_value=False),patch.object(power,'run',side_effect=run) as command:
            with self.assertRaisesRegex(RuntimeError,'forcé'): power.transition(self.policy,self.ids,'poweroff')
            self.assertNotIn(unittest.mock.call('systemctl','poweroff'),command.call_args_list)


if __name__=='__main__': unittest.main()
