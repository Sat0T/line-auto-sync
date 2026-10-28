"""Private SQLite queue. Legacy files are import-only; no LINE DB writes."""
from contextlib import contextmanager
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from checkpoint import scan_low

APPLICATION_ID = 0x4C414E33


class DeliveryError(RuntimeError):
    pass


def normalize_row(row):
    if len(row) != 5:
        raise DeliveryError("invalid_message_row")
    row = ["" if x is None else str(x) for x in row]
    if not re.fullmatch(r"[1-9][0-9]*", row[0]) or not row[4]:
        raise DeliveryError("invalid_message_row")
    try:
        if not math.isfinite(float(row[1])):
            raise ValueError()
    except ValueError:
        raise DeliveryError("invalid_message_timestamp") from None
    if len(json.dumps(row, ensure_ascii=False).encode("utf-8")) > 16 * 1024 * 1024:
        raise DeliveryError("message_exceeds_storage_limit")
    return row


class DeliveryState:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "normal-delivery.sqlite"
        self.db = None
        if not self.directory.is_dir() or self.directory.is_symlink() or self.path.is_symlink():
            raise DeliveryError("unsafe_state_path")
        created = False
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            created = True
        except FileExistsError:
            check = sqlite3.connect(self.path.resolve().as_uri() + "?mode=ro", uri=True)
            try:
                if check.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise DeliveryError("foreign_or_incomplete_delivery_database")
            finally:
                check.close()
        try:
            self.db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS messages(
                    pk INTEGER PRIMARY KEY,raw TEXT NOT NULL,hash TEXT NOT NULL,
                    acked INTEGER NOT NULL DEFAULT 0 CHECK(acked IN (0,1)));
                CREATE TABLE IF NOT EXISTS delivery(
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL,
                    ids TEXT NOT NULL,batch_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt REAL NOT NULL DEFAULT 0);
            """)
            if created:
                self.db.execute("PRAGMA application_id=" + str(APPLICATION_ID))
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.db is not None:
            self.db.close()
            self.db = None

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))

    def bind(self, config):
        database = Path(config["line_db"]).resolve(strict=True)
        stat = database.stat()
        identity = hashlib.sha256(json.dumps([
            config["source_id"], str(database), stat.st_dev, stat.st_ino,
            config["transport"]["endpoint"]
        ], ensure_ascii=False).encode()).hexdigest()
        old = self.get("identity")
        if old is not None and old != identity:
            raise DeliveryError("source_database_or_destination_changed")
        self.set("identity", identity)

    def insert_rows(self, rows):
        count = 0
        for row in rows:
            row = normalize_row(row)
            raw = json.dumps(row, ensure_ascii=False, separators=(",", ":"))
            digest = hashlib.sha256(raw.encode()).hexdigest()
            old = self.db.execute("SELECT hash FROM messages WHERE pk=?", (int(row[0]),)).fetchone()
            if old is not None:
                if old[0] != digest:
                    raise DeliveryError("message_id_content_conflict")
                continue
            self.db.execute("INSERT INTO messages(pk,raw,hash) VALUES(?,?,?)", (int(row[0]), raw, digest))
            count += 1
        return count

    def initialize_cursor(self, high):
        if self.get("collected") is not None:
            return int(self.get("collected"))
        low = scan_low(self.directory, high)
        if low > high:
            raise DeliveryError("source_database_rewound")
        csv_path = self.directory / "new.csv"
        if csv_path.is_symlink():
            raise DeliveryError("symlinked_legacy_csv")
        if csv_path.exists():
            csv.field_size_limit(max(csv.field_size_limit(), 16 * 1024 * 1024))
            with csv_path.open(newline="", encoding="utf-8") as stream:
                for row in csv.reader(stream, strict=True):
                    row = normalize_row(row)
                    if int(row[0]) > low:
                        self.insert_rows([row])
        clock = self.directory / "sent_at"
        if clock.is_symlink():
            raise DeliveryError("symlinked_legacy_clock")
        if clock.exists():
            value = float(clock.read_text(encoding="utf-8"))
            if not math.isfinite(value) or value < 0:
                raise DeliveryError("invalid_legacy_clock")
            self.set("success_at", value)
        self.set("initial_baseline", low)
        self.set("acked", low)
        self.set("collected", low)
        return low

    def pending_rows(self):
        return [json.loads(r[0]) for r in self.db.execute("SELECT raw FROM messages WHERE acked=0 ORDER BY pk LIMIT 500")]

    def freeze(self, body, ids):
        manifest = json.loads(body.splitlines()[0])
        if not ids or len(set(ids)) != len(ids) or manifest.get("count") != len(ids):
            raise DeliveryError("invalid_frozen_delivery")
        self.db.execute("INSERT INTO delivery(singleton,body,ids,batch_id) VALUES(1,?,?,?)",
                        (body, json.dumps(ids), manifest["batch_id"]))

    def acknowledge(self, delivery, now):
        ids = json.loads(delivery["ids"])
        self.db.executemany("UPDATE messages SET acked=1 WHERE pk=?", [(pk,) for pk in ids])
        self.set("acked", max(int(self.get("acked", "0")), max(ids)))
        self.set("success_at", now)
        self.db.execute("DELETE FROM delivery WHERE singleton=1")

    def status(self):
        return {"collected": int(self.get("collected", "0")),
                "acknowledged": int(self.get("acked", "0")),
                "pending": self.db.execute("SELECT COUNT(*) FROM messages WHERE acked=0").fetchone()[0],
                "frozen": bool(self.db.execute("SELECT 1 FROM delivery").fetchone())}
