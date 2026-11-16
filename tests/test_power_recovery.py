"""Regression models for the actual maintenance/health/timeout incident."""
import importlib.util
import json
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
with patch.dict(sys.modules,{'fcntl':types.SimpleNamespace(flock=lambda *a:None,LOCK_EX=1,LOCK_NB=2)}):
    spec=importlib.util.spec_from_file_location('power_recovery',ROOT/'scripts/debian/homeserver-power-v2.py')
    power=importlib.util.module_from_spec(spec);spec.loader.exec_module(power)


class BootRecoveryTests(unittest.TestCase):
    def setUp(self):self.policy=json.loads((ROOT/'infrastructure/monitoring/power-policy.json').read_text())
    def execute(self,marker,maintenance=True,upgrade=False):
        state={'maintenance':maintenance};calls=[]
        def compose(module,*args,**kwargs):
            calls.append((args,kwargs))
            if args[:1]==('start',) and 'cron' in args and state['maintenance']:raise RuntimeError('real Compose cron dependency rejects maintenance')
            if '--wait' in args and state['maintenance']:raise RuntimeError('real health check rejects maintenance')
            if 'status' in args:return json.dumps({'installed':True,'maintenance':state['maintenance'],'needsDbUpgrade':upgrade,'versionstring':'34.0.5'})
            if 'maintenance:mode' in args:state['maintenance']=False
            return ''
        with patch.object(power,'owned_preparation',return_value=marker),patch.object(power,'compose',side_effect=compose),patch.object(power,'PREPARATION'):
            power.start_registered(self.policy)
        return state,calls
    def test_power_loss_during_our_preparation_recovers_without_health_deadlock(self):
        state,calls=self.execute({'maintenance_owned':True,'version':'34.0.5'})
        self.assertFalse(state['maintenance'])
        self.assertEqual(calls[0][0],('start','db','redis','app'))
        off=next(i for i,(args,kw) in enumerate(calls) if 'maintenance:mode' in args)
        wait=next(i for i,(args,kw) in enumerate(calls) if '--wait' in args)
        self.assertLess(off,wait)
    def test_wait_wrapper_outlasts_compose_wait(self):
        _,calls=self.execute(None,maintenance=False)
        for args,kwargs in calls:
            if '--wait-timeout' in args:
                seconds=int(args[args.index('--wait-timeout')+1])
                self.assertGreater(kwargs['timeout'],seconds)
    def test_foreign_maintenance_is_never_cleared(self):
        with self.assertRaisesRegex(RuntimeError,'non attribuable'):self.execute(None)
    def test_owned_marker_does_not_override_version_change(self):
        with self.assertRaisesRegex(RuntimeError,'non attribuable'):self.execute({'maintenance_owned':True,'version':'other'})
    def test_no_automatic_upgrade_or_maintenance_clear_during_pending_migration(self):
        with self.assertRaisesRegex(RuntimeError,'Migration'):self.execute({'maintenance_owned':True,'version':'34.0.5'},upgrade=True)
    def test_start_never_recreates_or_pulls_containers(self):
        _,calls=self.execute(None,maintenance=False)
        for args,kwargs in calls:
            self.assertNotIn('up',args);self.assertNotIn('pull',args);self.assertNotIn('upgrade',args)


if __name__=='__main__':unittest.main()
