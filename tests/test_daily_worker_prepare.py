"""Generated collect-only entry/plist checks without launchd, LINE, or network."""
import hashlib
import importlib.util
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

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


if __name__ == '__main__':unittest.main()
