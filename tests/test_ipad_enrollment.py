"""Disposable public-key enrollment checks; no real SSH keys or iPad changes."""
import base64
import importlib.util
import json
import os
from pathlib import Path
import stat
import struct
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('ipad_enrollment', Path(__file__).resolve().parents[1] / 'tools/enroll_ipad_key.py')
enrollment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(enrollment)


def public_bytes(seed=1):
    # Synthetic wire-format data, never a usable or generated private key.
    blob = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes([seed]) * 32
    return b'ssh-ed25519 ' + base64.b64encode(blob) + b' synthetic-only\n'


class PublicKeyValidationTests(unittest.TestCase):
    def test_accepts_one_native_public_line_and_discards_comment(self):
        result = enrollment.parse_public_key(public_bytes())
        self.assertEqual(base64.b64decode(result)[-32:], bytes([1]) * 32)
        self.assertNotIn('synthetic', result)

    def test_rejects_private_other_algorithms_options_multiple_lines_and_bad_wire(self):
        values = [b'SYNTHETIC INVALID PRIVATE KEY\n', b'ssh-rsa AAAA\n',
                  b'restrict ' + public_bytes(), public_bytes() + public_bytes(2),
                  b'ssh-ed25519 AAAA\n', b'ssh-ed25519 not_base64\n', b'x' * 8193]
        for value in values:
            with self.subTest(value=value[:20]):
                with self.assertRaises(enrollment.EnrollmentError):
                    enrollment.parse_public_key(value)

    def test_permissions_owner_and_link_checks_are_fail_closed(self):
        valid = dict(st_mode=stat.S_IFREG | 0o600, st_uid=501, st_nlink=1)
        enrollment.check_file(SimpleNamespace(**valid), 501, authorized=True)
        for change in ({'st_uid': 0}, {'st_nlink': 2}, {'st_mode': stat.S_IFREG | 0o644},
                       {'st_mode': stat.S_IFLNK | 0o600}, {'st_mode': stat.S_IFIFO | 0o600}):
            with self.assertRaises(enrollment.EnrollmentError):
                enrollment.check_file(SimpleNamespace(**dict(valid, **change)), 501, authorized=True)
        directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=501)
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.check_directory(directory, 501, ssh=True)

    def test_doctor_returns_only_availability_and_never_reads_key_bytes(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('doctor read key bytes')):
            report = enrollment.doctor()
        self.assertIn('ready_for_owner_enrollment', report)
        self.assertFalse(report['server_listening_verified'])
        self.assertTrue(all(isinstance(value, bool) for value in report.values()))
        self.assertNotIn('ssh-ed25519', json.dumps(report))

    def test_bounded_read_handles_short_reads_and_refuses_excess(self):
        with patch.object(enrollment.os, 'read', side_effect=[b'ab', b'c', b'']):
            self.assertEqual(enrollment.read_bounded(9, 4), b'abc')
        with patch.object(enrollment.os, 'read', side_effect=[b'abc', b'de']):
            with self.assertRaises(enrollment.EnrollmentError):
                enrollment.read_bounded(9, 4)


@unittest.skipUnless(os.name == 'posix', 'iPad enrollment uses POSIX ownership, mode and dir_fd semantics')
class AppendOnlyEnrollmentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.home = Path(self.tmp.name)
        self.home.chmod(0o700)
        self.public = self.home / 'pc.pub'
        self.public.write_bytes(public_bytes())
        self.public.chmod(0o600)
        self.ssh = self.home / '.ssh'
        self.keys = self.ssh / 'authorized_keys'
        self.ready = patch.object(enrollment, 'doctor', return_value={'ready_for_owner_enrollment': True})
        self.ready.start()
        self.addCleanup(self.ready.stop)

    def existing(self, body=b'# existing owner authorization\r\n'):
        self.ssh.mkdir(mode=0o700)
        self.keys.write_bytes(body)
        self.keys.chmod(0o600)
        return body

    def test_new_restricted_key_is_appended_once_and_old_bytes_are_unchanged(self):
        old = self.existing(b'# existing owner authorization without final newline')
        result = enrollment.enroll(self.public, self.home)
        after = self.keys.read_bytes()
        self.assertTrue(result['changed'])
        self.assertTrue(after.startswith(old + b'\nrestrict ssh-ed25519 '))
        self.assertEqual(stat.S_IMODE(self.ssh.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(self.keys.stat().st_mode), 0o600)
        self.assertEqual(self.keys.stat().st_uid, os.getuid())
        again = enrollment.enroll(self.public, self.home)
        self.assertFalse(again['changed'])
        self.assertTrue(again['restricted'])
        self.assertEqual(after, self.keys.read_bytes())

    def test_new_files_are_private_and_existing_unrestricted_duplicate_requires_review(self):
        result = enrollment.enroll(self.public, self.home)
        self.assertTrue(result['restricted'])
        self.keys.write_bytes(public_bytes())
        before = self.keys.read_bytes()
        result = enrollment.enroll(self.public, self.home)
        self.assertFalse(result['changed'])
        self.assertTrue(result['requires_review'])
        self.assertEqual(before, self.keys.read_bytes())

    def test_missing_server_blocks_before_any_ssh_directory_change(self):
        with patch.object(enrollment, 'doctor', return_value={'ready_for_owner_enrollment': False}):
            with self.assertRaises(enrollment.EnrollmentError):
                enrollment.enroll(self.public, self.home)
        self.assertFalse(self.ssh.exists())

    def test_private_key_is_rejected_before_any_ssh_directory_change(self):
        self.public.write_bytes(b'SYNTHETIC INVALID PRIVATE KEY\nSYNTHETIC\n')
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.enroll(self.public, self.home)
        self.assertFalse(self.ssh.exists())

    def test_wide_existing_modes_are_not_reset(self):
        old = self.existing()
        self.keys.chmod(0o644)
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.enroll(self.public, self.home)
        self.assertEqual(self.keys.read_bytes(), old)
        self.assertEqual(stat.S_IMODE(self.keys.stat().st_mode), 0o644)
        self.keys.chmod(0o600)
        self.ssh.chmod(0o755)
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.enroll(self.public, self.home)
        self.assertEqual(stat.S_IMODE(self.ssh.stat().st_mode), 0o755)

    def test_symlink_and_hardlink_targets_are_refused_without_modification(self):
        external = self.home / 'external'
        external.write_bytes(b'keep unchanged')
        external.chmod(0o600)
        self.ssh.mkdir(mode=0o700)
        self.keys.symlink_to(external)
        with self.assertRaises(OSError):
            enrollment.enroll(self.public, self.home)
        self.assertEqual(external.read_bytes(), b'keep unchanged')
        self.keys.unlink()  # Synthetic fixture only; never real owner data.
        os.link(external, self.keys)
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.enroll(self.public, self.home)
        self.assertEqual(external.read_bytes(), b'keep unchanged')

    def test_source_symlink_or_parent_escape_is_refused(self):
        alias = self.home / 'alias.pub'
        alias.symlink_to(self.public)
        with self.assertRaises(OSError):
            enrollment.enroll(alias, self.home)
        with self.assertRaises(enrollment.EnrollmentError):
            enrollment.enroll(self.home / '..' / 'outside.pub', self.home)
        self.assertFalse(self.ssh.exists())


if __name__ == '__main__':
    unittest.main()
