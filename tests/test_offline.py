"""Offline-only integration checks; never access actual LINE DB or network."""
from contextlib import closing
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "normal-only-rc1"
CLI = APP / "cli.py"


class ManualCLIIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="las_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "synthetic.sqlite3"
        with closing(sqlite3.connect(self.db)) as c:
            c.executescript("""
                CREATE TABLE ZMESSAGE(
                    Z_PK INTEGER PRIMARY KEY, ZTIMESTAMP INTEGER,
                    ZCHAT INTEGER, ZSENDER INTEGER, ZTEXT TEXT
                );
                CREATE TABLE ZUSER(
                    Z_PK INTEGER PRIMARY KEY, ZNAME TEXT,
                    ZCUSTOMNAME TEXT, ZMID TEXT
                );
                CREATE TABLE ZCHAT(Z_PK INTEGER PRIMARY KEY, ZMID TEXT);
                CREATE TABLE ZUNIFIEDGROUP(ZID TEXT, ZNAME TEXT);
                INSERT INTO ZUSER VALUES(1, 'Demo User', NULL, 'demo-user');
                INSERT INTO ZCHAT VALUES(2, 'demo-group');
                INSERT INTO ZUNIFIEDGROUP VALUES('demo-group', 'Demo Group');
                INSERT INTO ZMESSAGE VALUES(
                    101, 1735689600000, 2, 1, 'synthetic hello'
                );
            """)
        self.env = os.environ.copy()
        self.env["LINE_AUTO_SYNC_CONFIG_DIR"] = str(self.root / "config")
        self.env["LINE_AUTO_SYNC_STATE_DIR"] = str(self.root / "state")

    def call(self, operation, success=True):
        p = subprocess.run(
            [sys.executable, str(CLI), operation],
            env=self.env, capture_output=True, text=True
        )
        if success:
            self.assertEqual(p.returncode, 0, p.stderr)
        else:
            self.assertNotEqual(p.returncode, 0)
        return p

    def configure(self):
        program = """
import builtins, getpass, runpy, sys
from pathlib import Path
cli, database = sys.argv[1:]
sys.path.insert(0, str(Path(cli).resolve().parent))
answers = iter([
    'https://example.invalid/never-connected',
    'OFFLINE_TEST_SOURCE',
    database
])
builtins.input = lambda prompt='': next(answers)
getpass.getpass = lambda prompt='': 'SYNTHETIC_TOKEN_ONLY'
sys.argv = [cli, 'configure']
runpy.run_path(cli, run_name='__main__')
"""
        p = subprocess.run(
            [sys.executable, "-c", program, str(CLI), str(self.db)],
            env=self.env, capture_output=True, text=True
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_cli_lifecycle_without_network(self):
        self.call("init")
        path = self.root / "config" / "config.json"
        self.assertIs(json.loads(path.read_text())["enabled"], False)
        self.configure()
        conf = json.loads(path.read_text())
        self.assertFalse(conf["enabled"])
        self.assertEqual(conf["line_db"], str(self.db.resolve()))
        self.assertEqual(conf["timestamp_unit"], "unix_ms")
        self.assertIsNone(conf["group_db"])
        self.call("doctor")
        self.call("enable")
        self.assertTrue(json.loads(path.read_text())["enabled"])
        self.call("disable")
        self.call("run", success=False)
        self.assertFalse((self.root / "state" / "sentid").exists())

    def test_name_enrichment_uses_private_state(self):
        self.call("init")
        self.configure()
        program = """
import legacy_names
from pathlib import Path
from runtime_paths import STATE_DIR
record = legacy_names.enrich_legacy({
    'id': '101', 'time_ms': '1735689600000',
    'chat': '2', 'sender': '1', 'text': 'synthetic hello'
})
assert record['id'] == 'OFFLINE_TEST_SOURCE:101'
assert record['chat'] == 'Demo Group'
assert record['sender'] == 'Demo User'
legacy_names.close()
assert (STATE_DIR / 'named-cache.sqlite').is_file()
print('PASS: actual resolver against synthetic SQLite')
"""
        p = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, "
             + repr(str(APP)) + ")\n" + program],
            env=self.env, capture_output=True, text=True
        )
        self.assertEqual(p.returncode, 0, p.stderr)

    def test_schema_failure_blocks_enable(self):
        self.call("init")
        self.configure()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("DROP TABLE ZMESSAGE")
            c.commit()
        self.call("doctor", success=False)
        self.call("enable", success=False)
        config = json.loads(
            (self.root / "config" / "config.json").read_text()
        )
        self.assertFalse(config["enabled"])

    def test_fractional_timestamp_does_not_pass_doctor_or_enable(self):
        self.call("init")
        self.configure()
        with closing(sqlite3.connect(self.db)) as c:
            c.execute("UPDATE ZMESSAGE SET ZTIMESTAMP=? WHERE Z_PK=101",
                      (1735689600000.5,))
            c.commit()
        self.call("doctor", success=False)
        self.call("enable", success=False)
        config = json.loads((self.root / "config" / "config.json").read_text())
        self.assertFalse(config["enabled"])

    def test_oversized_message_fails_closed(self):
        p = subprocess.run(
            [sys.executable, "-c",
             "import sys; sys.path.insert(0," + repr(str(APP)) + "); "
             "from normal_hold import retain; retain([], {})"],
            env=self.env, capture_output=True, text=True
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("Oversized message", p.stderr)


if __name__ == "__main__":
    unittest.main()
