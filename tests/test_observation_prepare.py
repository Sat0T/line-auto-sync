"""Offline launch-plan execution with fake power hold, no worker or iOS calls."""
import ast
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import plistlib
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('prepare_observation', Path(__file__).resolve().parents[1] / 'tools/prepare_db_observation.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class ObservationPreparationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.profile = self.root / 'profile'
        (self.profile / 'config').mkdir(parents=True)
        (self.profile / 'state').mkdir()
        self.database = self.root / 'line' / 'database.sqlite'
        self.database.parent.mkdir()
        with sqlite3.connect(self.database) as db:
            db.execute('CREATE TABLE ZMESSAGE(Z_PK INTEGER,ZCHAT INTEGER,ZTEXT TEXT)')
            db.execute("INSERT INTO ZMESSAGE VALUES(12,7,'synthetic private body')")
        with sqlite3.connect(self.profile / 'state/normal-delivery.sqlite') as db:
            db.execute('CREATE TABLE meta(key TEXT,value TEXT)')
            db.execute("INSERT INTO meta VALUES('collected','11')")
        self.config = self.profile / 'config/config.json'
        self.config.write_text(json.dumps({'enabled': False, 'source_id': 'MVP_IPAD_LIVE',
            'line_db': str(self.database), 'normal_line': {'allowed_chat_pks': [7]}}))
        self.bundle = self.root / 'observation'

    def load_payload(self):
        prepare.prepare(self.profile, self.bundle)
        namespace = {'__name__': 'test_payload', '__file__': str(self.bundle / 'observation.py')}
        exec(compile((self.bundle / 'observation.py').read_text(), '<synthetic-payload>', 'exec'), namespace)
        return namespace

    def test_generated_job_is_bounded_isolated_and_has_no_sender_import(self):
        raw = self.config.read_bytes()
        job = prepare.prepare(self.profile, self.bundle)
        self.assertEqual(plistlib.loads((self.bundle / 'observation.plist').read_bytes()), job)
        self.assertEqual(job['UserName'], 'mobile')
        self.assertFalse(job['KeepAlive'])
        source = (self.bundle / 'observation.py').read_text()
        tree = ast.parse(source)
        imports = [n for n in ast.walk(tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
        self.assertFalse(any('sender' in ast.dump(n) or 'keep_power_lock' in ast.dump(n) for n in imports))
        self.assertIn('SECONDS = 180', source)
        self.assertIn('--scope-only', source)
        self.assertEqual(raw, self.config.read_bytes())
        self.assertFalse((self.bundle / 'observations.jsonl').exists())
        with self.assertRaises(RuntimeError):
            prepare.prepare(self.profile, self.bundle)

    def test_payload_reads_without_writing_profile_queue_or_body_to_log(self):
        ns = self.load_payload()
        queue = self.profile / 'state/normal-delivery.sqlite'
        before = (self.config.read_bytes(), queue.read_bytes(), self.database.read_bytes())
        released = []
        @contextmanager
        def power():
            try:
                yield
            finally:
                released.append(True)
        ns['stay_awake'] = power
        import time
        original = time.monotonic
        # One completed sample then advance time on sleep; preserve real deadlines initially.
        offset = [0]
        with patch('os.getuid', return_value=501, create=True), patch('time.monotonic', side_effect=lambda: original() + offset[0]), patch('time.sleep', side_effect=lambda _: offset.__setitem__(0, 200)):
            ns['observe']()
        self.assertEqual(released, [True])
        self.assertEqual(before, (self.config.read_bytes(), queue.read_bytes(), self.database.read_bytes()))
        rows = [json.loads(line) for line in (self.bundle / 'observations.jsonl').read_text().splitlines()]
        self.assertEqual(rows[0]['scoped_rows'], 1)
        self.assertFalse(rows[0]['marker_tracking'])
        self.assertEqual(rows[-1]['event'], 'stopped')
        self.assertEqual(rows[-1]['reason'], 'deadline')
        self.assertNotIn('synthetic private body', json.dumps(rows))
        self.assertTrue(prepare.status(self.bundle)['stopped'])
        with patch('os.getuid', return_value=501, create=True):
            with self.assertRaisesRegex(RuntimeError, 'already_started'):
                ns['observe']()

    def test_signal_stops_read_only_job_and_releases_power_assertion(self):
        import signal
        ns = self.load_payload()
        released = []
        @contextmanager
        def power():
            try:
                yield
            finally:
                released.append(True)
        ns['stay_awake'] = power
        with patch('os.getuid', return_value=501, create=True), patch('time.sleep', side_effect=lambda _: signal.getsignal(signal.SIGTERM)(signal.SIGTERM, None)):
            ns['observe']()
        self.assertEqual(released, [True])
        rows = [json.loads(line) for line in (self.bundle / 'observations.jsonl').read_text().splitlines()]
        self.assertEqual(rows[-1]['reason'], 'signal')

    def test_changed_profile_blocks_before_power_hold(self):
        ns = self.load_payload()
        self.config.write_text(self.config.read_text() + '\n')
        with patch('os.getuid', return_value=501, create=True):
            with self.assertRaisesRegex(RuntimeError, 'profile_changed'):
                ns['observe']()
        self.assertFalse((self.bundle / 'observations.jsonl').exists())

    def test_bundle_inside_line_or_profile_is_rejected_without_writes(self):
        for target in (self.database.parent / 'probe', self.profile / 'probe'):
            with self.assertRaises(RuntimeError):
                prepare.prepare(self.profile, target)
            self.assertFalse(target.exists())


if __name__ == '__main__':
    unittest.main()
