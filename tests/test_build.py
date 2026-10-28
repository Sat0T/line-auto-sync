"""Archive-only checks; no installation, maintainer scripts or networking."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('private_builder', ROOT / 'tools' / 'build_private_beta.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


def archive(entries, control=None):
    return (b'!<arch>\n' + builder.make_ar_member('debian-binary', b'2.0\n')
            + builder.make_ar_member('control.tar.gz', builder.make_tar({
                './control': ((control or builder.CONTROL).encode(), 0o644)}))
            + builder.make_ar_member('data.tar.gz', builder.make_tar(entries)))


class PackageChecks(unittest.TestCase):
    def test_same_sources_produce_identical_archive_and_allowlist(self):
        entries = builder.get_payloads()
        self.assertEqual(len(builder.MODULES), 12)
        self.assertEqual(len(entries), 14)
        self.assertTrue(all(path.startswith('./var/jb/') for path in entries))
        self.assertFalse(any('/LaunchDaemons/' in path for path in entries))
        self.assertEqual(archive(entries), archive(builder.get_payloads()))
        builder.verify(archive(entries), entries)

    def test_changed_package_metadata_is_rejected(self):
        entries = builder.get_payloads()
        with self.assertRaises(SystemExit):
            builder.verify(archive(entries, builder.CONTROL.replace('iphoneos-arm64', 'iphoneos-arm')), entries)


if __name__ == '__main__':
    unittest.main()
