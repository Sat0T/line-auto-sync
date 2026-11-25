"""Synthetic iCloud staging without a device or any actual public key."""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('stage', Path(__file__).resolve().parents[1] / 'tools/stage_owner_bundle.py')
stage = importlib.util.module_from_spec(spec); spec.loader.exec_module(stage)


class StageTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'cloud' / 'LAS-Private-Onboarding-20261125'; source.mkdir(parents=True)
        names = ['enrollment/pc.pub', 'enrollment/enroll_ipad_key.py', 'enrollment/ipad_connection_offer.py',
                 'kit/tools/root_bootstrap.py'] + ['kit/runtime/synthetic%d.py' % n for n in range(18)]
        manifest = {}
        for name in names:
            p = source / name; p.parent.mkdir(parents=True, exist_ok=True); p.write_bytes(b'synthetic')
            manifest[name] = hashlib.sha256(p.read_bytes()).hexdigest()
        raw = json.dumps(manifest).encode(); (source / 'SHA256.json').write_bytes(raw)
        return source, hashlib.sha256(raw).hexdigest()

    def test_locates_one_fixed_bundle_then_stages_only_verified_files_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, digest = self.fixture(root)
            self.assertEqual(stage.locate(source.name, [source.parent]), source)
            target = root / 'fresh-home'; result = stage.stage(source, target, digest)
            self.assertTrue(result['owner_bundle_verified']); self.assertFalse(result['job_started'])
            self.assertEqual((target / 'enrollment/pc.pub').read_bytes(), b'synthetic')
            with self.assertRaisesRegex(ValueError, 'new_home'):
                stage.stage(source, target, digest)

    def test_hash_mismatch_or_tamper_never_creates_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, digest = self.fixture(root); target = root / 'home'
            with self.assertRaisesRegex(ValueError, 'manifest_version'):
                stage.stage(source, target, 'a' * 64)
            (source / 'enrollment/pc.pub').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError, 'bundle_file_version'):
                stage.stage(source, target, digest)
            self.assertFalse(target.exists())

    def test_traversal_and_private_key_names_are_rejected_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); source, _ = self.fixture(root); target = root / 'home'
            for bad in ('../escape.py', 'enrollment/id_ed25519'):
                manifest = json.loads((source / 'SHA256.json').read_bytes())
                manifest[bad] = 'a' * 64
                raw = json.dumps(manifest).encode(); (source / 'SHA256.json').write_bytes(raw)
                with self.assertRaisesRegex(ValueError, 'unsafe_manifest'):
                    stage.stage(source, target, hashlib.sha256(raw).hexdigest())
                self.assertFalse(target.exists())
                del manifest[bad]; (source / 'SHA256.json').write_text(json.dumps(manifest))

    def test_missing_bundle_never_assumes_cloud_arrival(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, 'one_downloaded'):
                stage.locate('LAS-Private-Onboarding-20261125', [Path(tmp)])


if __name__ == '__main__':
    unittest.main()
