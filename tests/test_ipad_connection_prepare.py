"""Private transfer/host-key binding fixtures; no SSH, device or real keys."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('prepare_connection', Path(__file__).resolve().parents[1] / 'tools/prepare_ipad_connection.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class ConnectionPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.repo = self.root / 'repo'
        (self.repo / 'tools').mkdir(parents=True)
        (self.repo / 'normal-only-rc1').mkdir()
        for name in prepare.RUNTIME_FILES:
            (self.repo / 'normal-only-rc1' / name).write_bytes(('# reviewed synthetic ' + name + '\n').encode())
        for name in prepare.TOOL_FILES:
            if name != 'root_bootstrap.py':
                (self.repo / 'tools' / name).write_bytes(('# reviewed synthetic ' + name + '\n').encode())
        (self.repo / 'tools/ipad_connection_offer.py').write_bytes(b'# reviewed synthetic offer helper\n')
        self.kit = self.root / 'kit'
        (self.kit / 'runtime').mkdir(parents=True)
        (self.kit / 'tools').mkdir()
        for name in prepare.RUNTIME_FILES:
            (self.kit / 'runtime' / name).write_bytes((self.repo / 'normal-only-rc1' / name).read_bytes())
        for name in prepare.TOOL_FILES:
            raw = (prepare.manager.root_setup_source().encode() if name == 'root_bootstrap.py'
                   else (self.repo / 'tools' / name).read_bytes())
            (self.kit / 'tools' / name).write_bytes(raw)
        # Public test bytes only, never a generated private or real host key.
        public_bytes = bytes.fromhex('d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a')
        wire = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + public_bytes
        self.blob = base64.b64encode(wire).decode()
        self.public = ('ssh-ed25519 ' + self.blob + ' synthetic-test-vector\n').encode()
        self.fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(wire).digest()).decode().rstrip('=')
        self.enrollment = self.root / 'enrollment'; self.enrollment.mkdir()
        (self.enrollment / 'pc.pub').write_bytes(self.public)
        (self.enrollment / 'enroll_ipad_key.py').write_bytes(b'# fixed synthetic enrollment helper\n')
        original = {name: hashlib.sha256((self.enrollment / name).read_bytes()).hexdigest()
                    for name in ('pc.pub', 'enroll_ipad_key.py')}
        (self.enrollment / 'SHA256.json').write_text(json.dumps(original))
        self.identity = self.root / 'existing-identity'
        self.identity.write_bytes(b'SYNTHETIC_PRIVATE_FILE_DO_NOT_TRANSFER')
        self.offer = {'version': 1, 'uid': 501, 'os_version': '16.7.14',
                      'ipv4': '192.168.50.23', 'port': 22,
                      'host_public_key': self.public.decode().rstrip('\n'),
                      'host_key_fingerprint': self.fingerprint,
                      'components': {'sshd': True, 'launchd_plist': True, 'public_host_key': True},
                      'socket_ports_observed': [22], 'server_listening_verified': False,
                      'ssh_authenticated': False, 'device_jst': '2026-11-25T12:00:00+09:00'}
        self.offer_file = self.root / 'device-offer.json'
        self.write_offer()

    def write_offer(self):
        self.offer_file.write_text(json.dumps(self.offer))

    def keygen(self, argv, **kwargs):
        self.assertIn('-lf', argv)
        self.assertNotIn('-y', argv)
        self.assertNotIn(str(self.identity), argv)
        raw = Path(argv[argv.index('-lf') + 1]).read_bytes()
        self.assertEqual(prepare.enrollment.parse_public_key(raw), self.blob)
        return subprocess.CompletedProcess(argv, 0,
            ('256 ' + self.fingerprint + ' private-comment-not-for-console (ED25519)\n').encode(), b'')

    def accept(self, destination, **kwargs):
        options = dict(owner_confirmed=True, execute=self.keygen, which=lambda _: 'synthetic-keygen',
                       protect=lambda *_: None, verify_identity=lambda *_: None)
        options.update(kwargs)
        return prepare.accept_offer(self.offer_file, self.fingerprint, self.identity, destination, **options)

    def package(self, destination, **kwargs):
        return prepare.package(self.enrollment, self.kit, destination, source_root=self.repo,
                               protect=lambda *_: None, **kwargs)

    def snapshot(self, folder):
        return {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}

    def test_fixed_transfer_manifest_and_bytes_preserve_sources(self):
        before = self.snapshot(self.enrollment), self.snapshot(self.kit)
        output = self.root / 'owner-bundle'
        result = self.package(output, offer_helper=self.repo / 'tools/ipad_connection_offer.py')
        self.assertEqual(result['payload_files'], 22)
        self.assertFalse(result['connected'])
        manifest = json.loads((output / 'SHA256.json').read_text())
        self.assertEqual(len(manifest), 22)
        self.assertIn('enrollment/SHA256.json', manifest)
        self.assertIn('enrollment/ipad_connection_offer.py', manifest)
        for path, digest in manifest.items():
            self.assertEqual(hashlib.sha256((output / path).read_bytes()).hexdigest(), digest)
        self.assertEqual(before, (self.snapshot(self.enrollment), self.snapshot(self.kit)))
        self.assertNotIn(self.identity.name, manifest)
        self.assertFalse(any(b'SYNTHETIC_PRIVATE_FILE' in raw for raw in self.snapshot(output).values()))

    def test_enrollment_checksum_mismatch_and_kit_version_mismatch_refuse_before_copy(self):
        (self.enrollment / 'pc.pub').write_bytes(self.public.replace(b'synthetic-test-vector', b'changed'))
        with self.assertRaisesRegex(prepare.PreparationError, 'enrollment_version_mismatch'):
            self.package(self.root / 'bad-enrollment')
        self.assertFalse((self.root / 'bad-enrollment').exists())
        (self.enrollment / 'pc.pub').write_bytes(self.public)
        for relative in ('runtime/sender.py', 'tools/root_bootstrap.py'):
            with self.subTest(relative=relative):
                path = self.kit / relative; old = path.read_bytes(); path.write_bytes(old + b'# stale\n')
                with self.assertRaisesRegex(prepare.PreparationError, 'kit_version_mismatch'):
                    self.package(self.root / 'bad-kit')
                self.assertFalse((self.root / 'bad-kit').exists())
                path.write_bytes(old)

    def test_secret_files_are_rejected_not_transferred(self):
        for folder, name in ((self.enrollment, 'id_ed25519'), (self.kit, 'connection.json')):
            with self.subTest(name=name):
                secret = folder / name; secret.write_bytes(b'SYNTHETIC_SECRET_MUST_NOT_TRANSFER')
                with self.assertRaisesRegex(prepare.PreparationError, 'no_secret_files'):
                    self.package(self.root / 'secret-output')
                self.assertFalse((self.root / 'secret-output').exists())
                secret.unlink()  # This disposable fixture file only.

    def test_existing_output_is_not_overwritten_or_deleted(self):
        output = self.root / 'owner-bundle'; self.package(output)
        before = self.snapshot(output)
        with self.assertRaisesRegex(prepare.PreparationError, 'new_private_output'):
            self.package(output)
        self.assertEqual(before, self.snapshot(output))

    def test_hardlinked_input_and_unreviewed_offer_helper_are_rejected(self):
        source = self.kit / 'runtime/sender.py'
        os.link(source, self.root / 'linked-source')
        with self.assertRaisesRegex(prepare.PreparationError, 'regular_unlinked'):
            self.package(self.root / 'linked-output')
        self.assertFalse((self.root / 'linked-output').exists())
        (self.root / 'linked-source').unlink()
        other = self.root / 'other_helper.py'; other.write_text('# not the selected offer helper')
        with self.assertRaisesRegex(prepare.PreparationError, 'fixed_offer_helper_basename'):
            self.package(self.root / 'other-output', offer_helper=other)

    def test_accept_offer_creates_pinned_known_hosts_without_connecting(self):
        before = self.identity.read_bytes(), self.offer_file.read_bytes()
        output = self.root / 'connection'
        result = self.accept(output)
        cfg = json.loads((output / 'connection.json').read_text())
        self.assertEqual(cfg['destination'], 'mobile@192.168.50.23')
        self.assertEqual(cfg['port'], 22)
        self.assertEqual(cfg['identity_file'], str(self.identity.absolute()))
        self.assertEqual((output / 'known_hosts').read_text(), '192.168.50.23 ssh-ed25519 ' + self.blob + '\n')
        self.assertTrue(result['independent_host_key_verified'])
        self.assertFalse(result['ssh_authenticated'])
        self.assertFalse(result['connected'])
        for private in (self.offer['ipv4'], self.fingerprint, self.blob, 'private-comment-not-for-console'):
            self.assertNotIn(private, json.dumps(result))
        self.assertEqual(before, (self.identity.read_bytes(), self.offer_file.read_bytes()))
        saved = self.snapshot(output)
        with self.assertRaisesRegex(prepare.PreparationError, 'new_private_output'):
            self.accept(output)
        self.assertEqual(saved, self.snapshot(output))

    def test_independent_owner_confirmation_and_all_fingerprints_are_required(self):
        output = self.root / 'rejected'
        with self.assertRaisesRegex(prepare.PreparationError, 'independent_owner'):
            self.accept(output, owner_confirmed=False)
        wrong = 'SHA256:' + 'A' * 43
        with self.assertRaisesRegex(prepare.PreparationError, 'fingerprint_mismatch'):
            prepare.accept_offer(self.offer_file, wrong, self.identity, output, owner_confirmed=True)
        self.offer['host_key_fingerprint'] = wrong; self.write_offer()
        with self.assertRaisesRegex(prepare.PreparationError, 'fingerprint_mismatch'):
            self.accept(output)
        self.offer['host_key_fingerprint'] = self.fingerprint; self.write_offer()
        def wrong_keygen(*args, **kwargs):
            return subprocess.CompletedProcess([], 0, ('256 ' + wrong + ' (ED25519)').encode(), b'')
        with self.assertRaisesRegex(prepare.PreparationError, 'local_public_fingerprint_mismatch'):
            self.accept(output, execute=wrong_keygen)
        self.assertFalse(output.exists())

    def test_only_rfc1918_ipv4_and_valid_environment_port_are_accepted(self):
        for address in ('127.0.0.1', '169.254.1.2', '100.64.1.2', '8.8.8.8', '0.0.0.0',
                        '224.0.0.1', '::1', 'ipad.local', '192.168.1.2/24', 3232235778):
            with self.subTest(address=address):
                self.offer['ipv4'] = address; self.write_offer()
                with self.assertRaisesRegex(prepare.PreparationError, 'rfc1918'):
                    self.accept(self.root / 'bad-address')
        self.offer['ipv4'] = '10.10.20.30'
        for field, value in (('uid', 0), ('version', True), ('os_version', '17.0'), ('port', True), ('port', 0), ('port', 65536)):
            with self.subTest(field=field, value=value):
                old = self.offer[field]; self.offer[field] = value; self.write_offer()
                with self.assertRaises(prepare.PreparationError):
                    self.accept(self.root / 'bad-environment')
                self.offer[field] = old
        self.offer['port'] = 2222; self.write_offer()
        output = self.root / 'alternate-port'; self.accept(output)
        self.assertTrue((output / 'known_hosts').read_text().startswith('[10.10.20.30]:2222 '))

    def test_non_native_or_options_prefixed_public_keys_are_refused(self):
        for value in ('ssh-ed25519 AAAA', 'restrict ' + self.public.decode().strip(),
                      self.public.decode().strip() + '\nssh-ed25519 AAAA', 'SYNTHETIC INVALID PRIVATE KEY'):
            with self.subTest(value=value[:20]):
                self.offer['host_public_key'] = value; self.write_offer()
                with self.assertRaisesRegex(prepare.PreparationError, 'native_ed25519'):
                    self.accept(self.root / 'bad-key')
        self.assertFalse((self.root / 'bad-key').exists())

    def test_partial_output_is_retained_and_never_overwritten_on_retry(self):
        output = self.root / 'partial'
        def fail_protect(path, directory):
            if Path(path).name == 'connection.json':
                raise RuntimeError('synthetic ACL verification failure')
        with self.assertRaises(RuntimeError):
            self.accept(output, protect=fail_protect)
        before = self.snapshot(output)
        self.assertIn('known_hosts', before)
        with self.assertRaisesRegex(prepare.PreparationError, 'new_private_output'):
            self.accept(output)
        self.assertEqual(before, self.snapshot(output))

    def test_existing_native_keygen_checks_only_the_synthetic_public_vector(self):
        keygen = shutil.which('ssh-keygen')
        if not keygen:self.skipTest('existing ssh-keygen unavailable')
        output = self.root / 'native-public-check'
        result = self.accept(output, execute=subprocess.run, which=lambda _: keygen)
        self.assertTrue(result['independent_host_key_verified'])
        self.assertFalse(result['connected'])


if __name__ == '__main__':
    unittest.main()
