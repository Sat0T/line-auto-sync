"""Synthetic SSH-controller recovery and fresh remote staging, without any device."""
import base64
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('manage_ipad', Path(__file__).resolve().parents[1] / 'tools/manage_ipad.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)


class ManagedTrialTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cfg = {'destination': 'mobile@example.invalid', 'port': 2222,
                    'known_hosts': str(self.root / 'known_hosts')}
        (self.root / 'known_hosts').write_text('synthetic trusted fixture only')
        self.journal = self.root / 'journal.json'

    def test_existing_host_trust_required_and_auth_is_noninteractive_strict(self):
        config = self.root / 'connection.json'
        config.write_text(json.dumps(self.cfg))
        loaded = manager.load_config(config)
        argv = manager.ssh_command(loaded)
        for option in ('BatchMode=yes', 'StrictHostKeyChecking=yes', 'ConnectionAttempts=1', 'ConnectTimeout=5'):
            self.assertIn(option, argv)
        self.assertLess(len(subprocess.list2cmdline(argv)), 30000)
        self.assertNotIn('StrictHostKeyChecking=no', argv)
        (self.root / 'known_hosts').unlink()
        with self.assertRaisesRegex(manager.ManagementError, 'existing_trusted'):
            manager.load_config(config)
        bad = dict(self.cfg, destination='-oProxyCommand=bad')
        config.write_text(json.dumps(bad))
        with self.assertRaisesRegex(manager.ManagementError, 'invalid_private_destination'):
            manager.load_config(config)

    def test_start_is_durable_before_ssh_and_lost_result_cannot_restart(self):
        calls = []
        def lost(cfg, request):
            self.assertTrue(json.loads(self.journal.read_text())['start_attempted'])
            calls.append(request['action'])
            raise manager.ManagementError('synthetic_connection_lost')
        with self.assertRaises(manager.ManagementError):
            manager.control(self.cfg, 'start', 'synthetic-n1', self.journal, transport=lost)
        with self.assertRaisesRegex(manager.ManagementError, 'start_already_attempted'):
            manager.control(self.cfg, 'start', 'synthetic-n1', self.journal, transport=lost)
        self.assertEqual(calls, ['start'])
        manager.control(self.cfg, 'status', 'synthetic-n1', self.journal,
                        transport=lambda *_: {'job': {'registered': {'system': False, 'user/foreground': False}}})
        self.assertTrue(json.loads(self.journal.read_text())['start_attempted'])

    def test_journal_cannot_be_reused_for_different_target(self):
        manager.control(self.cfg, 'doctor', 'synthetic-n1', self.journal, transport=lambda *_: {'connected': True})
        with self.assertRaisesRegex(manager.ManagementError, 'journal_target_mismatch'):
            manager.control(dict(self.cfg, port=2223), 'status', 'synthetic-n1', self.journal,
                            transport=lambda *_: self.fail('must not connect'))

    def test_post_unlock_read_is_claimed_once_before_connection(self):
        def lost(cfg, request):
            self.assertTrue(json.loads(self.journal.read_text())['post_unlock_read_attempted'])
            raise manager.ManagementError('synthetic_connection_lost')
        with self.assertRaises(manager.ManagementError):
            manager.control(self.cfg, 'post-unlock-once', 'synthetic-n1', self.journal, transport=lost)
        with self.assertRaisesRegex(manager.ManagementError, 'already_attempted'):
            manager.control(self.cfg, 'post-unlock-once', 'synthetic-n1', self.journal,
                            transport=lambda *_: self.fail('must not re-read after ambiguous outcome'))

    def test_timeout_never_returns_success_or_exposes_stderr(self):
        def timeout(*args, **kwargs):
            raise subprocess.TimeoutExpired('private-host', 95, stderr=b'private credential detail')
        with self.assertRaisesRegex(manager.ManagementError, 'outcome_unknown_reconcile_only') as result:
            manager.remote(self.cfg, {'action': 'start'}, execute=timeout)
        self.assertNotIn('private-host', str(result.exception))
        self.assertNotIn('credential', str(result.exception))
        def refused(*args, **kwargs):
            return subprocess.CompletedProcess([], 255, b'', b'private connection detail')
        with self.assertRaisesRegex(manager.ManagementError, 'reconcile_only') as result:
            manager.remote(self.cfg, {'action': 'doctor'}, execute=refused)
        self.assertNotIn('private connection detail', str(result.exception))

    def test_helper_status_is_retained_privately_not_printed(self):
        private = 'synthetic-target-id-only'
        visible = manager.control(self.cfg, 'status', 'synthetic-n1', self.journal,
                                  transport=lambda *_: {'helper_status_private': private,
                                                         'job': {'unregistered_in_checked_domains': True}})
        self.assertNotIn(private, json.dumps(visible))
        self.assertIn(private, self.journal.read_text())

    def test_every_private_suffix_is_journal_only_including_post_unlock_target(self):
        result = {'post_unlock_read_private': {'target_pk': 'SYNTHETIC_TARGET'},
                  'future_evidence_private': {'hash': 'SYNTHETIC_HASH'}, 'external_send': False}
        visible = manager.control(self.cfg, 'post-unlock-once', 'synthetic-n1', self.journal,
                                  transport=lambda *_: result)
        self.assertEqual(visible, {'external_send': False})
        self.assertNotIn('SYNTHETIC', json.dumps(visible))
        saved = self.journal.read_text()
        self.assertIn('SYNTHETIC_TARGET', saved)
        self.assertIn('SYNTHETIC_HASH', saved)

    def test_fetch_verifies_hash_and_does_not_overwrite_private_evidence(self):
        raw = b'{"event":"synthetic"}\n'
        result = {'files': {'trial.jsonl': {'data': base64.b64encode(raw).decode(),
                                          'sha256': hashlib.sha256(raw).hexdigest()}}}
        dest = self.root / 'logs'
        manager.control(self.cfg, 'fetch', 'synthetic-n1', self.journal, output=dest,
                        transport=lambda *_: result)
        self.assertEqual((dest / 'trial.jsonl').read_bytes(), raw)
        with self.assertRaises(FileExistsError):
            manager.control(self.cfg, 'fetch', 'synthetic-n1', self.journal, output=dest,
                            transport=lambda *_: result)
        self.assertEqual((dest / 'trial.jsonl').read_bytes(), raw)
        result['files']['trial.jsonl']['sha256'] = '0' * 64
        with self.assertRaisesRegex(manager.ManagementError, 'checksum_mismatch'):
            manager.control(self.cfg, 'fetch', 'synthetic-n1', self.journal, output=self.root / 'bad-logs',
                            transport=lambda *_: result)
        self.assertFalse((self.root / 'bad-logs/trial.jsonl').exists())

    def deployment(self):
        bundle = self.root / 'bundle'
        (bundle / 'tools').mkdir(parents=True)
        (bundle / 'runtime').mkdir()
        (bundle / 'tools/keep_lock_delivery.py').write_text('# synthetic helper\n')
        (bundle / 'runtime/sender.py').write_text('# synthetic runtime\n')
        return manager.manifest(bundle)

    def run_remote(self, request, override=None):
        # Execute actual remote staging/duplicate guards in a disposable local
        # root. Only native UID and fixed root are substituted for the fixture.
        code = 'ROOT_ACTION = ' + repr(manager.ROOT_ACTION) + '\n' + manager.REMOTE
        code = code.replace('if os.getuid() != 501:', 'if False:')
        code = code.replace("root=pathlib.Path('/var/mobile/las-managed')", 'root=pathlib.Path(' + repr(str(self.root / 'remote')) + ')')
        if override:
            code = code.replace("action=q['action']", override + "\naction=q['action']", 1)
        return subprocess.run([sys.executable, '-c', code], input=json.dumps(request).encode(),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)

    def test_remote_stage_reconciles_identical_files_but_rejects_modified_version(self):
        request = {'action': 'deploy', 'run': 'synthetic-n1', 'files': self.deployment()}
        first = self.run_remote(request)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertFalse(json.loads(first.stdout)['reconciled'])
        second = self.run_remote(request)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertTrue(json.loads(second.stdout)['reconciled'])
        target = self.root / 'remote/synthetic-n1/runtime/sender.py'
        target.write_text('# modified\n')
        failed = self.run_remote(request)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(b'staged_version_changed', failed.stderr)
        self.assertEqual(target.read_text(), '# modified\n')

    def test_remote_start_marker_prevents_second_bootstrap_even_with_other_pc_journal(self):
        deploy = self.run_remote({'action': 'deploy', 'run': 'synthetic-n1', 'files': self.deployment()})
        self.assertEqual(deploy.returncode, 0, deploy.stderr)
        request = {'action': 'start', 'run': 'synthetic-n1'}
        first = self.run_remote(request, "privileged=lambda action: {'started':True}")
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_remote(request, "privileged=lambda action: {'started':True}")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn(b'start_already_attempted_reconcile_only', second.stderr)

    def test_ui_status_records_clock_and_raw_signal_without_claiming_lock(self):
        files = self.deployment()
        raw = b'class LockSignal:\n def sample(self): return {"available": True, "raw": 123}\n def close(self): pass\n'
        files['tools/observe_line_db.py'] = {'data': base64.b64encode(raw).decode(),
                                           'sha256': hashlib.sha256(raw).hexdigest()}
        deployed = self.run_remote({'action': 'deploy', 'run': 'synthetic-n1', 'files': files})
        self.assertEqual(deployed.returncode, 0, deployed.stderr)
        queried = self.run_remote({'action': 'ui-status', 'run': 'synthetic-n1'})
        self.assertEqual(queried.returncode, 0, queried.stderr)
        result = json.loads(queried.stdout)
        self.assertTrue(result['device_jst'].endswith('+09:00'))
        self.assertEqual(result['lock_signal_observed']['raw'], 123)
        self.assertFalse(result['lock_state_verified'])
        self.assertIn('unsupported', result['unlock_automation'])

    def test_private_package_is_new_and_contains_only_expected_python_sources(self):
        repo = self.root / 'repo'
        (repo / 'tools').mkdir(parents=True)
        (repo / 'normal-only-rc1').mkdir()
        for name in ('keep_lock_delivery.py', 'observe_line_db.py', 'keep_power_lock.py'):
            (repo / 'tools' / name).write_text('# synthetic\n')
        (repo / 'normal-only-rc1/sender.py').write_text('# synthetic\n')
        output = self.root / 'package'
        self.assertEqual(manager.package(repo, output)['packaged_files'], 5)
        files = manager.manifest(output)
        self.assertEqual(set(files), {'tools/keep_lock_delivery.py', 'tools/observe_line_db.py',
                                     'tools/keep_power_lock.py', 'tools/root_bootstrap.py', 'runtime/sender.py'})
        with self.assertRaisesRegex(manager.ManagementError, 'new_private_package'):
            manager.package(repo, output)

    def test_root_privilege_is_only_the_fixed_bridge_command(self):
        calls = []
        for node in ast.walk(ast.parse(manager.REMOTE)):
            if isinstance(node, ast.Call) and node.args and isinstance(node.args[0], ast.List):
                values = [part.value for part in node.args[0].elts if isinstance(part, ast.Constant)]
                if values and values[0] == 'sudo':
                    calls.append(values)
        self.assertEqual(len(calls), 2)
        expected = ['sudo', '-n', '/var/jb/usr/bin/python3', '/var/root/las-managed-control.py']
        self.assertTrue(all(command == expected for command in calls))
        self.assertNotIn('ROOT_ACTION =', manager.ssh_command(self.cfg)[-1])

    def test_bridge_rejects_nonroot_owner_writable_mode_symlink_or_hardlink(self):
        tree = ast.parse(manager.ROOT_ACTION)
        function = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == 'secure_root_file')
        namespace = {'stat': stat}
        exec(compile(ast.Module(body=[function], type_ignores=[]), '<bridge-file-validation>', 'exec'), namespace)
        def path(uid=0, mode=0o600, symlink=False, links=1):
            return SimpleNamespace(is_symlink=lambda: symlink,
                stat=lambda: SimpleNamespace(st_mode=stat.S_IFREG | mode, st_uid=uid, st_nlink=links))
        namespace['secure_root_file'](path(), 0o600)
        for bad in (path(uid=501), path(mode=0o644), path(symlink=True), path(links=2)):
            with self.assertRaises(RuntimeError):
                namespace['secure_root_file'](bad, 0o600)

    def test_exported_bootstrap_pins_bridge_hash_and_exact_sudoers_arguments(self):
        target = self.root / 'root-bootstrap.py'
        result = manager.export_root_setup(target)
        namespace = {'__name__': 'test_setup'}
        exec(compile(target.read_text(), '<generated-root-setup>', 'exec'), namespace)
        self.assertEqual(namespace['BRIDGE_SHA256'], hashlib.sha256(manager.ROOT_ACTION.encode()).hexdigest())
        self.assertEqual(base64.b64decode(namespace['BRIDGE_BASE64']), manager.ROOT_ACTION.encode())
        arguments = namespace['RULE'].partition(b'NOPASSWD:')[2].strip()
        self.assertEqual(arguments, b'/var/jb/usr/bin/python3 /var/root/las-managed-control.py')
        self.assertNotIn(b'*', arguments)
        self.assertNotIn(b'ALL', arguments)
        self.assertFalse(result['applied'])
        self.assertTrue(result['owner_sudo_authentication_required'])
        with self.assertRaises(manager.ManagementError):
            manager.export_root_setup(target)

    def test_missing_visudo_defers_root_setup_before_any_install(self):
        namespace = {'__name__': 'test_setup'}
        exec(compile(manager.root_setup_source(), '<generated-root-setup>', 'exec'), namespace)
        namespace['write_new'] = lambda *_: self.fail('must not install without visudo')
        with patch('os.getuid', return_value=0, create=True), patch.object(Path, 'is_file', return_value=False):
            with self.assertRaisesRegex(RuntimeError, 'visudo_and_sudo_required'):
                namespace['install']()

    def test_bootstrap_configuration_probe_uses_c_locale_without_global_change(self):
        namespace = {'__name__': 'test_setup'}
        exec(compile(manager.root_setup_source(), '<generated-root-setup>', 'exec'), namespace)
        import os
        previous = dict(os.environ)
        with patch('subprocess.run', return_value=subprocess.CompletedProcess([], 0, b'configuration', b'')) as run:
            self.assertEqual(namespace['check'](['synthetic-probe']), 'configuration')
        self.assertEqual(run.call_args.kwargs['env']['LC_ALL'], 'C')
        self.assertEqual(dict(os.environ), previous)

    def test_generate_key_protects_directory_first_and_never_prints_key_contents(self):
        output = self.root / 'dedicated-key'
        protections = []
        def protect(path, directory):
            self.assertTrue(Path(path).exists())
            protections.append((Path(path).name, directory))
        def keygen(argv, **kwargs):
            self.assertEqual(protections, [('dedicated-key', True)])
            self.assertEqual(argv[argv.index('-N') + 1], '')
            (output / 'id_ed25519').write_text('SYNTHETIC_PRIVATE_KEY_NOT_REAL')
            (output / 'id_ed25519.pub').write_text('SYNTHETIC_PUBLIC_KEY_NOT_REAL')
            return subprocess.CompletedProcess(argv, 0, b'secret key output', b'')
        result = manager.generate_key(output, execute=keygen, which=lambda _: 'synthetic-keygen', protect=protect)
        self.assertEqual(protections, [('dedicated-key', True), ('id_ed25519', False), ('id_ed25519.pub', False)])
        self.assertFalse(result['authentication_ready'])
        self.assertNotIn('SYNTHETIC_PRIVATE', json.dumps(result))
        self.assertNotIn('secret key output', json.dumps(result))
        with self.assertRaisesRegex(manager.ManagementError, 'new_private_key_directory'):
            manager.generate_key(output, execute=lambda *_: self.fail('must not overwrite key'))

    def test_unverified_acl_blocks_key_generation(self):
        def unverified(*_):
            raise manager.ManagementError('private_acl_not_verified_no_key_generation')
        output = self.root / 'unverified-key-directory'
        with self.assertRaisesRegex(manager.ManagementError, 'private_directory_acl_not_verified_key_not_generated'):
            manager.generate_key(output, which=lambda _: 'synthetic-keygen', protect=unverified,
                                 execute=lambda *_: self.fail('must not generate a secret before ACL verification'))
        self.assertEqual(list(output.iterdir()), [])

    def test_missing_keygen_is_reported_without_creating_directory(self):
        output = self.root / 'missing-keygen'
        with self.assertRaisesRegex(manager.ManagementError, 'existing_ssh_keygen_required'):
            manager.generate_key(output, which=lambda _: None)
        self.assertFalse(output.exists())

    def test_file_acl_failure_reports_generated_candidate_and_retains_both_files(self):
        output = self.root / 'candidate'
        def protect(path, directory):
            if not directory:
                raise manager.ManagementError('synthetic_post_generation_acl_failure')
        def keygen(argv, **kwargs):
            (output / 'id_ed25519').write_text('SYNTHETIC_PRIVATE_CANDIDATE')
            (output / 'id_ed25519.pub').write_text('ssh-ed25519 SYNTHETIC_PUBLIC_CANDIDATE')
            return subprocess.CompletedProcess(argv, 0, b'', b'')
        with self.assertRaisesRegex(manager.ManagementError, 'generated_key_candidate_retained_file_acl_not_verified'):
            manager.generate_key(output, execute=keygen, which=lambda _: 'synthetic-keygen', protect=protect)
        self.assertTrue((output / 'id_ed25519').is_file())
        self.assertTrue((output / 'id_ed25519.pub').is_file())
        with self.assertRaisesRegex(manager.ManagementError, 'new_private_key_directory_required'):
            manager.generate_key(output, execute=lambda *_: self.fail('must not regenerate'))

    def test_verify_existing_key_is_read_only_and_does_not_claim_device_authentication(self):
        output = self.root / 'existing-candidate'; output.mkdir()
        private, public = output / 'id_ed25519', output / 'id_ed25519.pub'
        private.write_text('SYNTHETIC_PRIVATE_CANDIDATE')
        public.write_text('ssh-ed25519 SYNTHETIC_PUBLIC_CANDIDATE original-comment\n')
        before = (private.read_bytes(), public.read_bytes())
        verified = []
        def keycheck(argv, **kwargs):
            self.assertIn('-y', argv)
            self.assertNotIn('-t', argv)
            return subprocess.CompletedProcess(argv, 0, b'ssh-ed25519 SYNTHETIC_PUBLIC_CANDIDATE other-comment\n', b'')
        result = manager.verify_local_key(output, execute=keycheck, which=lambda _: 'synthetic-keygen',
                                          verify=lambda path, directory: verified.append(directory))
        self.assertEqual(verified, [True, False, False])
        self.assertTrue(result['acl_owner_only_verified'])
        self.assertTrue(result['pair_verified'])
        self.assertFalse(result['authentication_ready'])
        self.assertFalse(result['key_registered_on_ipad'])
        self.assertFalse(result['host_key_verified'])
        self.assertNotIn('SYNTHETIC', json.dumps(result))
        self.assertEqual(before, (private.read_bytes(), public.read_bytes()))

    def test_acl_unverified_or_public_half_mismatch_cannot_pass_existing_key_check(self):
        output = self.root / 'candidate'; output.mkdir()
        private, public = output / 'id_ed25519', output / 'id_ed25519.pub'
        private.write_text('SYNTHETIC_PRIVATE'); public.write_text('ssh-ed25519 SYNTHETIC_A\n')
        def deny(*_):raise manager.ManagementError('synthetic_acl_unverified')
        result = manager.verify_local_key(output, verify=deny,
            execute=lambda *_: self.fail('must not read key before owner ACL verification'))
        self.assertFalse(result['acl_owner_only_verified'])
        self.assertFalse(result['pair_verified'])
        result = manager.verify_local_key(output, verify=lambda *_: None, which=lambda _: 'synthetic-keygen',
            execute=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, b'ssh-ed25519 SYNTHETIC_B\n', b''))
        self.assertTrue(result['acl_owner_only_verified'])
        self.assertFalse(result['pair_verified'])
        before = (private.read_bytes(), public.read_bytes())
        calls = []
        self.assertFalse(manager.protect_existing_key(output, protect=lambda p, d: calls.append(d))['key_regenerated'])
        self.assertEqual(calls, [True, False, False])
        self.assertEqual(before, (private.read_bytes(), public.read_bytes()))

    @unittest.skipUnless(os.name == 'nt', 'Windows-only Access ACL regression')
    def test_windows_owner_read_only_acl_is_restricted_without_owner_or_audit_write(self):
        shell = shutil.which('pwsh') or shutil.which('powershell')
        if not shell:self.skipTest('existing PowerShell unavailable')
        directory = self.root / 'private-native'; directory.mkdir()
        # Elevated Windows runners may create fixtures owned by Administrators.
        # Production deliberately refuses a foreign owner; establish ownership
        # only on this newly-created disposable test directory, never on a key.
        own_fixture = r'''
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=Get-Acl -LiteralPath $env:LAS_PRIVATE_KEY_ITEM
$acl.SetOwner($sid)
Set-Acl -LiteralPath $env:LAS_PRIVATE_KEY_ITEM -AclObject $acl
if((Get-Acl -LiteralPath $env:LAS_PRIVATE_KEY_ITEM).GetOwner([Security.Principal.SecurityIdentifier]).Value -ne $sid.Value){throw 'synthetic_owner_setup_failed'}
'''
        fixture_env = dict(os.environ, LAS_PRIVATE_KEY_ITEM=str(directory.absolute()))
        fixture_env.pop('PSModulePath', None)
        owned = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', own_fixture],
                               env=fixture_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(owned.returncode, 0, 'synthetic directory ownership could not be established')
        manager.protect_private_path(directory, True)
        fixture = directory / 'synthetic.txt'; fixture.write_text('synthetic file, no key')
        setup = r'''
$ErrorActionPreference='Stop'
$sid=[Security.Principal.WindowsIdentity]::GetCurrent().User
$acl=[Security.AccessControl.FileSecurity]::new()
$acl.SetOwner($sid);$acl.SetAccessRuleProtection($true,$false)
$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($sid,[Security.AccessControl.FileSystemRights]::Read,[Security.AccessControl.AccessControlType]::Allow))
foreach($value in @('S-1-5-18','S-1-5-32-544')){
 $generic=[Security.Principal.SecurityIdentifier]::new($value)
 $acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($generic,[Security.AccessControl.FileSystemRights]::FullControl,[Security.AccessControl.AccessControlType]::Allow))
}
Set-Acl -LiteralPath $env:LAS_PRIVATE_KEY_ITEM -AclObject $acl
'''
        environment = dict(os.environ, LAS_PRIVATE_KEY_ITEM=str(fixture.resolve()))
        environment.pop('PSModulePath', None)
        result = subprocess.run([shell, '-NoProfile', '-NonInteractive', '-Command', setup], env=environment,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(result.returncode, 0, 'synthetic ACL fixture could not be created')
        manager.protect_private_path(fixture, False)
        manager.verify_private_path(fixture, False)
        self.assertEqual(fixture.read_text(), 'synthetic file, no key')
        self.assertNotIn('.SetOwner(', manager.WINDOWS_PRIVATE_ACL)

    def test_hardlinked_candidate_is_rejected_before_any_acl_change(self):
        output = self.root / 'candidate'; output.mkdir()
        private = output / 'id_ed25519'; private.write_text('SYNTHETIC_PRIVATE')
        (output / 'id_ed25519.pub').write_text('ssh-ed25519 SYNTHETIC_PUBLIC')
        os.link(private, self.root / 'same-inode')
        with self.assertRaisesRegex(manager.ManagementError, 'hardlink'):
            manager.protect_existing_key(output, protect=lambda *_: self.fail('must reject before changing any ACL'))
        with patch.object(manager, 'windows_acl', side_effect=lambda *_: self.fail('must reject before native ACL write')):
            with self.assertRaisesRegex(manager.ManagementError, 'hardlink'):
                manager.protect_private_path(private, False)
        self.assertEqual(private.read_text(), 'SYNTHETIC_PRIVATE')

    def test_native_acl_check_receives_unresolved_leaf_for_reparse_detection(self):
        leaf = SimpleNamespace(absolute=lambda: self.root / 'unresolved-leaf',
                               resolve=lambda: self.fail('must not hide a leaf junction before native validation'))
        calls = []
        def run(argv, **kwargs):
            calls.append(kwargs['env']['LAS_PRIVATE_KEY_ITEM'])
            return subprocess.CompletedProcess(argv, 0, b'PRIVATE_ACL_VERIFIED\r\n', b'')
        with patch.object(manager.shutil, 'which', return_value='synthetic-pwsh'), patch.object(manager.subprocess, 'run', side_effect=run):
            manager.windows_acl(leaf, True, manager.WINDOWS_VERIFY_ACL)
        self.assertEqual(calls, [str(self.root / 'unresolved-leaf')])

    def root_job_control(self, run, clock):
        tree = ast.parse(manager.ROOT_ACTION)
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in ('registered', 'stop_or_status')]
        namespace = {'subprocess': SimpleNamespace(run=run, PIPE=subprocess.PIPE,
                         DEVNULL=subprocess.DEVNULL, TimeoutExpired=subprocess.TimeoutExpired),
                     'time': SimpleNamespace(monotonic=lambda: clock[0]),
                     'domains': ('system', 'user/foreground'),
                     'label': 'com.sat0t.line-auto-sync.lock-n1'}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<bounded-root-job-control>', 'exec'), namespace)
        return namespace['stop_or_status']

    def test_stop_continues_other_bootout_and_both_queries_after_first_timeout(self):
        clock = [0.0]; calls = []
        def run(argv, **kwargs):
            calls.append((argv[1], argv[2].split('/com.')[0]))
            if argv[1] == 'bootout' and argv[2].startswith('system/'):
                clock[0] += kwargs['timeout']
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            if argv[1] == 'bootout':
                return subprocess.CompletedProcess(argv, 0)
            return subprocess.CompletedProcess(argv, 1, b'', b'Could not find service')
        result = self.root_job_control(run, clock)('stop')
        self.assertEqual(calls, [('bootout', 'system'), ('bootout', 'user/foreground'),
                                 ('print', 'system'), ('print', 'user/foreground')])
        self.assertEqual(result['bootout']['system']['outcome'], 'timeout')
        self.assertEqual(result['bootout']['user/foreground']['outcome'], 'completed')
        self.assertEqual(result['registered'], {'system': False, 'user/foreground': False})
        self.assertTrue(result['unregistered_in_checked_domains'])

    def test_query_failure_is_unknown_and_does_not_skip_other_domain(self):
        for failure in ('timeout', 'os_error', 'inconclusive'):
            with self.subTest(failure=failure):
                clock = [0.0]; calls = []
                def run(argv, **kwargs):
                    calls.append((argv[1], argv[2].split('/com.')[0]))
                    if argv[1] == 'bootout':
                        return subprocess.CompletedProcess(argv, 0)
                    if argv[2].startswith('system/'):
                        if failure == 'timeout':
                            clock[0] += kwargs['timeout']
                            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
                        if failure == 'os_error':
                            raise OSError('synthetic query failed')
                        return subprocess.CompletedProcess(argv, 1, b'', b'synthetic permission failure')
                    return subprocess.CompletedProcess(argv, 1, b'', b'Could not find service')
                result = self.root_job_control(run, clock)('stop')
                self.assertIsNone(result['registered']['system'])
                self.assertIs(result['registered']['user/foreground'], False)
                self.assertIn(('print', 'user/foreground'), calls)
                self.assertFalse(result['unregistered_in_checked_domains'])

    def test_bootout_error_and_remaining_registration_cannot_complete_stop(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[1])
            if argv[1] == 'bootout':
                raise OSError('synthetic launchctl failure')
            if argv[2].startswith('system/'):
                return subprocess.CompletedProcess(argv, 0, b'synthetic running job', b'')
            return subprocess.CompletedProcess(argv, 1, b'', b'Could not find service')
        result = self.root_job_control(run, [0.0])('stop')
        self.assertEqual(calls, ['bootout', 'bootout', 'print', 'print'])
        self.assertTrue(result['registered']['system'])
        self.assertFalse(result['unregistered_in_checked_domains'])
        self.assertTrue(all(item['outcome'] == 'failed' for item in result['bootout'].values()))

    def test_stop_timeout_budget_preserves_existing_privileged_and_ssh_envelopes(self):
        clock = [0.0]; timeouts = []
        def run(argv, **kwargs):
            timeouts.append(kwargs['timeout'])
            clock[0] += kwargs['timeout']
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        result = self.root_job_control(run, clock)('stop')
        self.assertEqual(timeouts, [15, 15, 5, 5])
        self.assertEqual(clock[0], 40)
        self.assertEqual(result['registered'], {'system': None, 'user/foreground': None})
        self.assertFalse(result['unregistered_in_checked_domains'])
        remote_ast = ast.parse(manager.REMOTE)
        privileged = next(n for n in remote_ast.body if isinstance(n, ast.FunctionDef) and n.name == 'privileged')
        limits = [kw.value.value for n in ast.walk(privileged) if isinstance(n, ast.Call)
                  for kw in n.keywords if kw.arg == 'timeout' and isinstance(kw.value, ast.Constant)]
        self.assertEqual(limits, [45])
        captured = []
        def ssh(argv, **kwargs):
            captured.append(kwargs['timeout'])
            return subprocess.CompletedProcess(argv, 0, b'{}', b'')
        manager.remote(self.cfg, {'action': 'status'}, execute=ssh)
        self.assertEqual(captured, [95])

    def test_exhausted_stop_budget_preserves_unknown_without_new_commands(self):
        clock = [0.0]; calls = []
        def run(argv, **kwargs):
            calls.append(argv[1])
            clock[0] += 40  # Simulate exhausted elapsed budget after first failure.
            raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
        result = self.root_job_control(run, clock)('stop')
        self.assertEqual(calls, ['bootout'])
        self.assertEqual(result['bootout']['user/foreground']['outcome'], 'budget_exhausted')
        self.assertEqual(result['query_errors'], {'system': 'budget_exhausted', 'user/foreground': 'budget_exhausted'})
        self.assertFalse(result['unregistered_in_checked_domains'])

    def test_status_query_failure_does_not_imply_absence_or_skip_second_domain(self):
        calls = []
        def run(argv, **kwargs):
            calls.append(argv[2].split('/com.')[0])
            if argv[2].startswith('system/'):
                raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
            return subprocess.CompletedProcess(argv, 1, b'', b'Could not find service')
        result = self.root_job_control(run, [0.0])('status')
        self.assertEqual(calls, ['system', 'user/foreground'])
        self.assertEqual(result['registered'], {'system': None, 'user/foreground': False})
        self.assertFalse(result['unregistered_in_checked_domains'])
        self.assertEqual(result['bootout'], {})


if __name__ == '__main__':
    unittest.main()
