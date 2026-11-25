"""Generated collect-only entry/plist checks without launchd, LINE, or network."""
import hashlib
import ast
import base64
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('daily', ROOT / 'tools/prepare_daily_worker.py')
daily = importlib.util.module_from_spec(spec); spec.loader.exec_module(daily)


class DailyWorkerPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.profile = self.root / 'profile'
        (self.profile / 'config').mkdir(parents=True); (self.profile / 'state').mkdir()
        self.database = self.root / 'source' / 'synthetic.sqlite'
        self.database.parent.mkdir(); self.database.write_bytes(b'not opened by preparation')
        (self.profile / 'state/dbpath').write_text(str(self.database))
        self.config = {'schema_version': 1, 'enabled': False, 'source_id': 'MVP_IPAD_LIVE',
                       'normal_line': {'enabled': True, 'allowed_chat_pks': [2]},
                       'schedule': {'interval_seconds': 60}, 'line_db': str(self.database),
                       'transport': {'endpoint': 'https://example.invalid/never-connected',
                                     'token': 'SYNTHETIC_TOKEN_ONLY'},
                       'synthetic_action': 'stop'}
        self.write_config()
        self.stage = self.root / 'managed-stage'; self.runtime = self.stage / 'runtime'; self.runtime.mkdir(parents=True)
        for name in daily.RUNTIME_FILES:
            (self.runtime / name).write_bytes((ROOT / 'normal-only-rc1' / name).read_bytes())
        # Exercise the real worker loop; only substitute its sleep and external
        # collector/CLI adapters so all exit paths finish offline and promptly.
        p = self.runtime / 'runner.py'
        p.write_text(p.read_text().replace('sleep=time.sleep', 'sleep=lambda _: None'))
        (self.runtime / 'sender.py').write_text('''from contextlib import contextmanager
@contextmanager
def sender_lock(*args):yield
def send_once(*args):raise AssertionError('collect-only must never send')
''')
        (self.runtime / 'cli.py').write_text('''def doctor():pass
def queue_status():return {'pending':1,'frozen':True}
''')
        (self.runtime / 'sync.py').write_text('''import json
from runtime_paths import CONFIG_FILE
def collect():
    import runner
    config=json.loads(CONFIG_FILE.read_text())
    action=config['synthetic_action']
    if action=='stop':runner.request_stop()
    elif action=='disable':
        config['enabled']=False;CONFIG_FILE.write_text(json.dumps(config))
    elif action=='change':
        config['schedule']['interval_seconds']=120;CONFIG_FILE.write_text(json.dumps(config))
    elif action=='crash':raise SystemExit(23)
''')
        self.refresh_manifest()
        self.output = self.root / 'prepared'

    def write_config(self):
        (self.profile / 'config/config.json').write_text(json.dumps(self.config))

    def refresh_manifest(self):
        manifest = {'runtime/' + name: hashlib.sha256((self.runtime / name).read_bytes()).hexdigest()
                    for name in daily.RUNTIME_FILES}
        (self.stage / 'manifest.json').write_text(json.dumps(manifest))

    def prepare(self):return daily.prepare(self.profile, self.stage, self.output)

    def execute(self):
        return subprocess.run([sys.executable, str(self.output / 'daily-collect.py')],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def test_plist_only_restarts_unsuccessful_exits_and_never_enables_sending(self):
        before = (self.profile / 'config/config.json').read_bytes(), self.database.read_bytes()
        result = self.prepare(); spec = plistlib.loads((self.output / 'daily-collect.plist').read_bytes())
        self.assertEqual(spec['KeepAlive'], {'SuccessfulExit': False})
        self.assertEqual(spec['ThrottleInterval'], 60); self.assertEqual(spec['UserName'], 'mobile')
        self.assertTrue(spec['RunAtLoad']); self.assertNotIn('--send', spec['ProgramArguments'])
        self.assertNotIn('StandardOutPath', spec); self.assertNotIn('StandardErrorPath', spec)
        self.assertFalse(result['started']); self.assertFalse(result['external_send'])
        self.assertEqual(before, ((self.profile / 'config/config.json').read_bytes(), self.database.read_bytes()))
        with self.assertRaisesRegex(ValueError, 'new_nonlinked'):
            self.prepare()

    def test_disabled_start_exits_zero_without_importing_worker_or_collecting(self):
        self.prepare()
        (self.runtime / 'runner.py').write_text("raise AssertionError('disabled startup must not import worker')")
        result = self.execute()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'disabled', result.stdout)
        self.assertFalse((self.profile / 'state/worker-status.json').exists())

    def test_real_generated_entry_exits_zero_for_stop_disable_and_config_change(self):
        for action in ('stop', 'disable', 'change'):
            with self.subTest(action=action):
                self.config['enabled'] = False; self.config['synthetic_action'] = action
                self.config['schedule']['interval_seconds'] = 60; self.write_config()
                self.output = self.root / ('prepared-' + action); self.prepare()
                self.config['enabled'] = True; self.write_config()
                result = self.execute()
                self.assertEqual(result.returncode, 0, result.stderr)
                status = json.loads((self.profile / 'state/worker-status.json').read_text())
                self.assertEqual(status['state'], 'stopped')
                self.assertNotIn('application_accepted', result.stdout.decode())

    def test_uncaught_process_failure_is_nonzero_for_launchd_restart(self):
        self.config['synthetic_action'] = 'crash'; self.write_config(); self.prepare()
        self.config['enabled'] = True; self.write_config()
        result = self.execute(); self.assertEqual(result.returncode, 23)
        self.assertEqual(json.loads((self.profile / 'state/worker-status.json').read_text())['state'], 'stopped')

    def test_changed_profile_before_start_is_normal_exit_not_restart_loop(self):
        self.prepare(); self.config['enabled'] = True
        self.config['normal_line']['allowed_chat_pks'] = [3]; self.write_config()
        result = self.execute(); self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(b'profile_changed', result.stdout)
        self.assertFalse((self.profile / 'state/worker-status.json').exists())

    def test_running_profile_bad_pointer_or_runtime_drift_does_not_prepare(self):
        marker = self.profile / 'state/worker-status.json'
        marker.write_text(json.dumps({'state': 'running'}))
        with self.assertRaisesRegex(ValueError, 'worker_stopped'):self.prepare()
        marker.write_text(json.dumps({'state': 'stopped'}))
        (self.runtime / 'sync.py').write_text('# changed after verified deployment')
        with self.assertRaisesRegex(ValueError, 'runtime_version'):self.prepare()
        self.assertFalse(self.output.exists())
        (self.profile / 'state/dbpath').write_text(str(self.profile))
        with self.assertRaisesRegex(ValueError, 'pointer_mismatch'):self.prepare()
        self.assertFalse(self.output.exists())

    def test_overlap_with_profile_or_line_directory_does_not_write_files(self):
        for output in (self.profile / 'prepared', self.database.parent / 'prepared'):
            self.output = output
            with self.assertRaisesRegex(ValueError, 'separate_profile'):self.prepare()
            self.assertFalse(output.exists())

    def sending(self, **options):
        self.config['transport']['endpoint']='https://script.google.com/macros/s/SYNTHETIC/exec'
        self.write_config()
        return daily.prepare(self.profile,self.stage,self.output,send=True,**options)

    def execute_send(self):
        return subprocess.run([sys.executable,str(self.output/'daily-send.py')],
                              stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=10)

    def test_send_is_explicit_opt_in_and_cannot_adopt_old_queue_on_first_activation(self):
        with self.assertRaisesRegex(ValueError,'confirm_actual_receiver'):
            self.sending()
        old=self.profile/'state/normal-delivery.sqlite';old.write_bytes(b'OLD_QUEUE_PRESERVE')
        with self.assertRaisesRegex(ValueError,'fresh_queue'):
            self.sending(confirm_delivery_binding=True)
        self.assertEqual(old.read_bytes(),b'OLD_QUEUE_PRESERVE');self.assertFalse(self.output.exists())

    def test_already_approved_daily_state_can_be_prepared_without_resetting_queue(self):
        self.config['transport']['endpoint']='https://script.google.com/macros/s/SYNTHETIC/exec';self.write_config()
        marker={'mode':'send','profile_binding':daily.normalized_binding(self.config)}
        (self.profile/'state/daily-activation.json').write_text(json.dumps(marker))
        old=self.profile/'state/normal-delivery.sqlite';old.write_bytes(b'APPROVED_QUEUE_PRESERVE')
        result=self.sending(confirm_delivery_binding=True)
        self.assertTrue(result['sending_configured']);self.assertFalse(result['external_send'])
        self.assertEqual(old.read_bytes(),b'APPROVED_QUEUE_PRESERVE')
        plan=json.loads((self.output/'plan.json').read_text())
        self.assertFalse(plan['actual_delivery_verified']);self.assertTrue(plan['operator_confirmed_delivery_binding'])
        spec=plistlib.loads((self.output/'daily-send.plist').read_bytes())
        self.assertEqual(spec['Label'],daily.SEND_LABEL);self.assertEqual(spec['UserName'],'mobile')

    def test_send_entry_recovers_same_approved_profile_after_process_crash(self):
        self.config['synthetic_action']='send_cycle';self.write_config()
        (self.runtime/'sender.py').write_text('''from contextlib import contextmanager
import json
from runtime_paths import STATE_DIR
@contextmanager
def sender_lock(*args):yield
def send_once(*args):
    import runner
    path=STATE_DIR/'synthetic-send-counter.json'
    count=json.loads(path.read_text()) if path.exists() else 0
    count+=1;path.write_text(json.dumps(count))
    if count==1:raise SystemExit(23)
    runner.request_stop()
    return {'sent':True}
''')
        self.refresh_manifest();self.sending(confirm_delivery_binding=True)
        self.config['enabled']=True;self.write_config()
        first=self.execute_send();self.assertEqual(first.returncode,23,first.stderr)
        marker=(self.profile/'state/daily-activation.json').read_bytes()
        second=self.execute_send();self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual((self.profile/'state/daily-activation.json').read_bytes(),marker)
        self.assertEqual(json.loads((self.profile/'state/synthetic-send-counter.json').read_text()),2)
        self.assertEqual(json.loads((self.profile/'state/worker-status.json').read_text())['last_result'],'application_accepted')

    def test_old_queue_appearing_after_prepare_cannot_be_sent_on_first_start(self):
        self.sending(confirm_delivery_binding=True)
        old=self.profile/'state/normal-delivery.sqlite';old.write_bytes(b'OLD_QUEUE_PRESERVE')
        self.config['enabled']=True;self.write_config()
        result=self.execute_send();self.assertNotEqual(result.returncode,0)
        self.assertEqual(old.read_bytes(),b'OLD_QUEUE_PRESERVE')
        self.assertFalse((self.profile/'state/daily-activation.json').exists())
        self.assertFalse((self.profile/'state/worker-status.json').exists())

    def test_capacity_pause_keeps_all_data_and_does_not_attempt_send(self):
        self.sending(confirm_delivery_binding=True,max_state_bytes=1024*1024)
        self.config['enabled']=True;self.write_config()
        marker={'mode':'send','profile_binding':daily.normalized_binding(self.config)}
        (self.profile/'state/daily-activation.json').write_text(json.dumps(marker))
        data=self.profile/'state/named-cache.sqlite';data.write_bytes(b'x'*(2*1024*1024))
        before=hashlib.sha256(data.read_bytes()).hexdigest()
        result=self.execute_send();self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(hashlib.sha256(data.read_bytes()).hexdigest(),before)
        health=self.profile/'state/daily-health.json'
        self.assertEqual(json.loads(health.read_text())['phase'],'capacity_paused')
        self.assertLess(health.stat().st_size,1024)
        self.assertEqual(json.loads((self.profile/'state/worker-status.json').read_text())['state'],'stopped')
        self.assertFalse(any(p.name.startswith('.worker-') for p in (self.profile/'state').iterdir()))

    def root_namespace(self):
        self.sending(confirm_delivery_binding=True)
        scope={'__name__':'synthetic_daily_root'}
        exec(compile((self.output/'daily-send-control.py').read_text(),'<synthetic-daily-root>','exec'),scope)
        return scope

    def test_daily_root_controller_pins_one_mobile_job_and_detects_changed_source(self):
        scope=self.root_namespace();raw=scope['verified_source']()
        self.assertEqual(plistlib.loads(raw)['Label'],daily.SEND_LABEL)
        self.assertEqual(scope['EXPECTED_SPEC']['UserName'],'mobile')
        (self.output/'daily-send.py').write_text('# changed after review')
        with self.assertRaisesRegex(RuntimeError,'source_version'):scope['verified_source']()

    def test_daily_root_cleanup_reuses_independent_domain_handling(self):
        scope=self.root_namespace();calls=[];clock=[0.0]
        def run(argv,**kwargs):
            calls.append((argv[1],argv[2]))
            if argv[1]=='bootout' and argv[2].startswith('system/'):
                clock[0]+=kwargs['timeout'];raise subprocess.TimeoutExpired(argv,kwargs['timeout'])
            if argv[1]=='print':return subprocess.CompletedProcess(argv,1,b'',b'Could not find service')
            return subprocess.CompletedProcess(argv,0)
        scope['subprocess']=SimpleNamespace(run=run,TimeoutExpired=subprocess.TimeoutExpired,
                                            PIPE=subprocess.PIPE,DEVNULL=subprocess.DEVNULL)
        scope['time']=SimpleNamespace(monotonic=lambda:clock[0])
        result=scope['stop_or_status']('stop')
        self.assertTrue(result['unregistered_in_checked_domains'])
        self.assertEqual([x[0] for x in calls],['bootout','bootout','print','print'])
        self.assertTrue(all(d.endswith('/'+daily.SEND_LABEL) for _,d in calls))

    def test_daily_bootstrap_is_self_contained_and_exact_command_scoped(self):
        self.sending(confirm_delivery_binding=True)
        tree=ast.parse((self.output/'daily-root-bootstrap.py').read_text())
        constants={n.targets[0].id:ast.literal_eval(n.value) for n in tree.body[:2]}
        raw=base64.b64decode(constants['BRIDGE_BASE64'])
        self.assertEqual(hashlib.sha256(raw).hexdigest(),constants['BRIDGE_SHA256'])
        self.assertEqual(raw,(self.output/'daily-send-control.py').read_bytes())
        text=(self.output/'daily-root-bootstrap.py').read_text()
        self.assertIn('NOPASSWD: /var/jb/usr/bin/python3 /var/root/las-daily-send-control.py',text)
        self.assertNotIn('las-managed-control',text)
        self.assertNotIn('NOPASSWD: ALL',text)


if __name__ == '__main__':unittest.main()
