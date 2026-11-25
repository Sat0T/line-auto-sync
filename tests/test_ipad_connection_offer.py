"""Synthetic owner-side discovery; never reads a device or actual host key."""
import base64
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
sys.path.insert(0, str(TOOLS))
spec = importlib.util.spec_from_file_location('offer', TOOLS / 'ipad_connection_offer.py')
offer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(offer)


class OfferTests(unittest.TestCase):
    def test_socket_ports_are_read_from_installed_plist_not_listening_proof(self):
        self.assertEqual(offer.socket_ports({'Sockets': {'one': {'SockServiceName': 'ssh'},
            'two': [{'SockServiceName': '2222'}, {'SockServiceName': 'bad'}]}}), [22, 2222])

    def test_private_ranges_only(self):
        for address in ('127.0.0.1', '169.254.1.1', '8.8.8.8', '::1'):
            with self.assertRaises((offer.OfferError, ValueError)):
                offer.private_ipv4(address)
        self.assertEqual(offer.private_ipv4('192.168.4.9'), '192.168.4.9')

    def test_offer_is_exclusive_and_never_reads_or_starts_private_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); public = root / 'host.pub'; out = root / 'device-offer.json'
            wire = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
            public.write_bytes(b'ssh-ed25519 ' + base64.b64encode(wire) + b' synthetic\n')
            info = {'uid': 501, 'os_version': '16.7.14', 'components': dict.fromkeys(('sshd', 'launchd_plist', 'public_host_key'), True),
                    'socket_ports_observed': [22], 'server_listening_verified': False}
            calls = []
            def run(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, b'256 SHA256:' + b'A' * 43 + b' synthetic (ED25519)\n', b'')
            with patch.object(offer.shutil, 'which', return_value='existing-ssh-keygen'):
                result = offer.make_offer(out, execute=run, info=info, public_path=public, ipv4='192.168.4.9')
                self.assertFalse(result['ssh_authenticated'])
                self.assertFalse(json.loads(out.read_text())['server_listening_verified'])
                before = out.read_bytes()
                with self.assertRaisesRegex(offer.OfferError, 'new_private'):
                    offer.make_offer(out, execute=run, info=info, public_path=public, ipv4='192.168.4.9')
                self.assertEqual(out.read_bytes(), before)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0][1], '-lf')
            self.assertNotIn('sudo', calls[0])

    def test_missing_components_or_different_device_fail_before_offer(self):
        with tempfile.TemporaryDirectory() as tmp:
            for info in ({'uid': 0, 'os_version': '16.7.14'}, {'uid': 501, 'os_version': '16.7.14', 'components': {}}):
                with self.assertRaises(offer.OfferError):
                    offer.make_offer(Path(tmp) / 'offer.json', info=info)
            self.assertEqual(list(Path(tmp).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
