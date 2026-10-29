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
SCHEMA = (
    'CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)',
    '''CREATE TABLE messages(
        pk INTEGER PRIMARY KEY,raw TEXT NOT NULL,hash TEXT NOT NULL,
        acked INTEGER NOT NULL DEFAULT 0 CHECK(acked IN (0,1)))''',
    '''CREATE TABLE delivery(
        singleton INTEGER PRIMARY KEY CHECK(singleton=1),body TEXT NOT NULL,
        ids TEXT NOT NULL,batch_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0)''',
)


def schema_signature(statement):
    # SQLite removes IF NOT EXISTS from sqlite_master on some supported builds.
    return re.sub(r'\s+', '', statement).lower().replace('ifnotexists', '').rstrip(';')


def validate_existing(database):
    """Read-only structural check; never recreate missing queue tables.

    This is not a full page/content audit or an authenticated anti-tamper check.
    Unknown layouts require an explicit migration, not CREATE IF NOT EXISTS.
    """
    with_database = None
    try:
        with_database = sqlite3.connect(database.resolve().as_uri() + '?mode=ro', uri=True)
        with_database.execute('PRAGMA query_only=ON')
        with_database.execute('BEGIN')
        if with_database.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID:
            raise DeliveryError('foreign_or_incomplete_delivery_database')
        definitions = with_database.execute(
            "SELECT sql FROM sqlite_master WHERE type IN ('table','view','trigger')").fetchall()
        expected = {schema_signature(sql) for sql in SCHEMA}
        if {schema_signature(row[0] or '') for row in definitions} != expected:
            raise DeliveryError('unsupported_or_corrupt_delivery_schema')
        validate_metadata(with_database)
    except sqlite3.DatabaseError:
        raise DeliveryError('unreadable_delivery_database') from None
    finally:
        if with_database is not None:
            with_database.close()


def validate_metadata(database):
    """Check small cursor/binding metadata without scanning the message queue."""
    meta = dict(database.execute('SELECT key,value FROM meta'))
    if not meta:
        if (database.execute('SELECT 1 FROM messages LIMIT 1').fetchone() or
                database.execute('SELECT 1 FROM delivery LIMIT 1').fetchone()):
            raise DeliveryError('corrupt_delivery_metadata')
        return  # A committed, not-yet-collected new database is legitimate.
    required = {'identity', 'initial_baseline', 'acked', 'collected'}
    if not required.issubset(meta):
        raise DeliveryError('corrupt_delivery_metadata')
    identity = meta['identity']
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{64}', identity):
        raise DeliveryError('corrupt_delivery_metadata')
    cursors = {}
    for key in ('initial_baseline', 'acked', 'collected'):
        value = meta[key]
        if (not isinstance(value, str) or not re.fullmatch('[0-9]{1,19}', value) or
                int(value) > 9223372036854775807):
            raise DeliveryError('corrupt_delivery_metadata')
        cursors[key] = int(value)
    if (cursors['collected'] < cursors['initial_baseline'] or
            cursors['acked'] < cursors['initial_baseline']):
        raise DeliveryError('corrupt_delivery_metadata')
    # Legacy import can contain a queued PK above collected; do not impose
    # acked <= collected, or reinterpret old CSV/ACK positions here.
    if 'success_at' in meta:
        try:
            clock = float(meta['success_at'])
            if not math.isfinite(clock) or clock < 0:
                raise ValueError()
        except (ValueError, TypeError, OverflowError):
            raise DeliveryError('corrupt_delivery_metadata') from None


class DeliveryError(RuntimeError):
    pass


def normalize_row(row):
    if len(row) != 5:
        raise DeliveryError("invalid_message_row")
    row = ["" if x is None else str(x) for x in row]
    if not re.fullmatch(r"[1-9][0-9]*", row[0]) or not row[4]:
        raise DeliveryError("invalid_message_row")
    # Match the resolver's strict Unix-millisecond contract at COLLECTION,
    # before the collected cursor can advance past an unreadable row.
    if (not re.fullmatch(r"[0-9]+", row[1]) or
            not 946684800000 <= int(row[1]) < 4133980800000):
        raise DeliveryError("invalid_message_timestamp")
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
            validate_existing(self.path)
        try:
            self.db = sqlite3.connect(str(self.path), timeout=5, isolation_level=None)
            self.db.row_factory = sqlite3.Row
            if os.name != "nt":
                os.chmod(self.path, 0o600)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            if created:
                with self.transaction():
                    for statement in SCHEMA:
                        self.db.execute(statement)
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

    def verify_frozen(self, delivery, source):
        """Fail closed before HTTP if a persisted request or its queued IDs drift.

        A frozen request can outlive a process and must be independently
        reconciled with the queue; JSON readability alone is not sufficient.
        """
        import presentation
        import transport

        try:
            body = delivery["body"]
            manifest_line, separator, payload = body.partition("\n")
            if not separator or not payload or len(body.encode("utf-8")) > 90000:
                raise ValueError("invalid_frozen_body")
            manifest = transport.strict_json(manifest_line)
            records = presentation.decode_named(payload)
            ids = json.loads(delivery["ids"])
            if (not isinstance(manifest, dict) or
                    set(manifest) != {"v", "kind", "source", "batch_id",
                                     "part", "parts", "count", "sha256"} or
                    type(manifest["v"]) is not int or manifest["v"] != 2 or
                    manifest["kind"] != "line_batch" or
                    manifest["source"] != source or
                    type(manifest["part"]) is not int or manifest["part"] != 1 or
                    type(manifest["parts"]) is not int or manifest["parts"] != 1 or
                    type(manifest["count"]) is not int or
                    manifest["count"] != len(records) or
                    type(ids) is not list or not ids or
                    any(type(pk) is not int or pk <= 0 for pk in ids) or
                    ids != sorted(set(ids))):
                raise ValueError("frozen_manifest_or_ids")
            record_ids = [int(r["refs"]["message_pk"]) for r in records]
            if sorted(record_ids) != ids:
                raise ValueError("frozen_record_id_mismatch")
            digest = transport.digest(transport.records_bytes(records))
            batch_id = transport.digest((source + "\n" + digest).encode("utf-8"))
            if (manifest["sha256"] != digest or
                    manifest["batch_id"] != batch_id or
                    delivery["batch_id"] != batch_id):
                raise ValueError("frozen_content_hash_mismatch")
            for record in records:
                pk = int(record["refs"]["message_pk"])
                row = self.db.execute(
                    "SELECT raw,hash,acked FROM messages WHERE pk=?", (pk,)).fetchone()
                if (row is None or row["acked"] != 0 or
                        hashlib.sha256(row["raw"].encode("utf-8")).hexdigest() != row["hash"]):
                    raise ValueError("frozen_queue_message_missing_or_corrupt")
                original = json.loads(row["raw"])
                if (type(original) is not list or len(original) != 5 or
                        record["text"] != original[4]):
                    raise ValueError("frozen_text_mismatch")
            return ids
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError,
                presentation.PresentationError, transport.TransportError) as exc:
            raise DeliveryError("corrupt_frozen_delivery") from None

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
