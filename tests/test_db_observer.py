"""Synthetic read-only visibility observations; no device or network use."""
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('db_observer', Path(__file__).resolve().parents[1] / 'tools/observe_line_db.py')
observer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(observer)


class DatabaseObservationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.database = self.root / 'private.sqlite'
        self.writer = sqlite3.connect(self.database)
        self.addCleanup(self.writer.close)
        self.writer.execute('PRAGMA journal_mode=WAL')
        self.writer.execute('PRAGMA wal_autocheckpoint=0')
        self.writer.execute('CREATE TABLE ZMESSAGE (Z_PK INTEGER PRIMARY KEY, ZCHAT INTEGER, ZTEXT TEXT)')
        self.writer.commit()
        self.reader = observer.Observer(self.database, 7, 'private marker', 10)
        self.addCleanup(self.reader.close)

    def test_uncommitted_then_wal_committed_visibility_and_bounds(self):
        self.writer.execute('INSERT INTO ZMESSAGE VALUES(11,7,?)', ('private marker',))
        absent = self.reader.sample()
        self.assertEqual(absent['marker_rows'], 0)
        self.writer.commit()
        present = self.reader.sample()
        self.assertEqual(present['marker_after_anchor'], 1)
        self.assertTrue(present['commit_since_previous'])
        self.assertEqual(present['visibility_transition_window_utc'],
                         [absent['read_started_utc'], present['read_finished_utc']])
        self.assertGreater(present['files']['wal']['bytes'], 0)
        repeat = self.reader.sample()
        self.assertNotIn('visibility_transition_window_utc', repeat)
        self.assertFalse(repeat['commit_since_previous'])
        # No body, chat ID, database path or SQL in the observation output.
        serialized = json.dumps(present)
        for sensitive in ('private marker', str(self.database), 'ZTEXT', 'ZCHAT'):
            self.assertNotIn(sensitive, serialized)
        with self.assertRaises(sqlite3.OperationalError):
            self.reader.control.execute('DELETE FROM ZMESSAGE')
        self.assertEqual(self.writer.execute('SELECT COUNT(*) FROM ZMESSAGE').fetchone()[0], 1)

    def test_preexisting_marker_is_not_reported_as_new_arrival(self):
        self.writer.execute('INSERT INTO ZMESSAGE VALUES(8,7,?)', ('private marker',))
        self.writer.commit()
        result = self.reader.sample()
        self.assertEqual(result['marker_rows'], 1)
        self.assertEqual(result['marker_after_anchor'], 0)
        self.assertNotIn('visibility_transition_window_utc', result)

    def test_database_replacement_stops_instead_of_reassigning_observation(self):
        stat = self.database.stat()
        class Replacement:
            st_dev = stat.st_dev
            st_ino = stat.st_ino + 1
        with patch.object(Path, 'stat', return_value=Replacement()):
            with self.assertRaisesRegex(RuntimeError, 'database_identity_changed'):
                self.reader.sample()

    def test_retargeted_path_stops(self):
        with patch.object(self.reader.requested_database.__class__, 'resolve', return_value=self.root / 'other.sqlite'):
            with self.assertRaisesRegex(RuntimeError, 'database_target_changed'):
                self.reader.sample()

    def test_sidecar_failure_is_distinct_from_empty_or_missing(self):
        missing = observer.file_metadata(self.root / 'missing-wal')
        self.assertEqual(missing, {'exists': False})
        with patch.object(Path, 'open', side_effect=PermissionError(13, 'private path')):
            failed = observer.file_metadata(self.database)
        self.assertEqual(failed, {'accessible': False, 'errno': 13})
        self.assertNotIn('private path', json.dumps(failed))

    def test_read_failure_retains_metadata_without_claiming_absence(self):
        with patch.object(self.reader, 'connect', side_effect=sqlite3.OperationalError('private database path')):
            failed = self.reader.sample()
        self.assertEqual(failed['event'], 'db_read_error')
        self.assertTrue(failed['files']['wal']['exists'])
        self.assertNotIn('marker_rows', failed)
        self.assertNotIn('private database path', json.dumps(failed))

    def test_cli_preserves_profile_queue_and_exclusive_log(self):
        profile = self.root / 'profile'
        (profile / 'config').mkdir(parents=True)
        (profile / 'state').mkdir()
        config_path = profile / 'config/config.json'
        config_path.write_text(json.dumps({'source_id': 'MVP_IPAD_LIVE', 'line_db': str(self.database),
                                          'normal_line': {'allowed_chat_pks': [7]}, 'enabled': False}))
        queue_path = profile / 'state/normal-delivery.sqlite'
        queue = sqlite3.connect(queue_path)
        queue.execute('CREATE TABLE meta (key TEXT, value TEXT)')
        queue.execute("INSERT INTO meta VALUES('collected','10')")
        queue.commit()
        queue.close()
        before = (config_path.read_bytes(), queue_path.read_bytes())
        output = profile / 'observations.jsonl'
        args = ['--profile', str(profile), '--marker', 'private marker', '--output', str(output), '--seconds', '1']
        observer.main(args)
        events = [json.loads(line)['event'] for line in output.read_text().splitlines()]
        self.assertEqual(events, ['snapshot', 'stopped'])
        self.assertEqual(before, (config_path.read_bytes(), queue_path.read_bytes()))
        saved = output.read_bytes()
        with self.assertRaises(FileExistsError):
            observer.main(args)
        self.assertEqual(saved, output.read_bytes())


if __name__ == '__main__':
    unittest.main()
