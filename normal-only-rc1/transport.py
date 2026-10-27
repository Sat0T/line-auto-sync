#!/usr/bin/env python3
"""Opt-in v2 transport; never opens the LINE database or changes launchctl.

Wire contract:
  sha256 = SHA-256(UTF-8(canonical(record) + LF, for each record in order)).
  canonical = JSON with sorted keys, no whitespace and ensure_ascii=False.
  signature = HMAC-SHA256(secret, UTF-8(canonical({manifest, records}))).
  ACK must exactly echo v/source/batch_id/part/parts/count/sha256 and ok:true.
The GAS endpoint MUST implement this contract before --send is used.
"""

import argparse
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from pathlib import Path

APPLICATION_ID = 0x4C535632  # LSV2: reject arbitrary/LINE SQLite files.
MAX_INPUT_BYTES = 16 * 1024 * 1024
DEFAULT_MAX_BYTES = 64 * 1024
MAX_RESPONSE_BYTES = 16 * 1024
SOURCE_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")
ALLOWED_HOSTS = frozenset(("script.google.com", "script.googleusercontent.com"))


class TransportError(Exception):
    """An intentionally non-sensitive, stable error code."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def records_bytes(records):
    return ("".join(canonical(r) + "\n" for r in records)).encode("utf-8")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise TransportError("duplicate_json_key")
        result[key] = value
    return result


def strict_json(text):
    try:
        return json.loads(text, object_pairs_hook=_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(
                              TransportError("invalid_json_number")))
    except (ValueError, UnicodeError):
        raise TransportError("invalid_json") from None


def _positive_int(value):
    return type(value) is int and value > 0


def _internal_id(value):
    # Decimal strings prevent JS/GAS from rounding SQLite's 64-bit IDs.
    return (isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,18}", value)
            and int(value) <= 9223372036854775807)


def validate_record(record):
    if not isinstance(record, dict) or set(record) != {
            "id", "time", "chat", "sender", "text", "refs"}:
        raise TransportError("invalid_record_fields")
    if not all(isinstance(record[k], str) for k in
               ("id", "time", "chat", "sender")):
        raise TransportError("invalid_record_types")
    if record["text"] is not None and not isinstance(record["text"], str):
        raise TransportError("invalid_record_types")
    if not record["chat"] or not record["sender"]:
        raise TransportError("empty_display_name")
    refs = record["refs"]
    if not isinstance(refs, dict) or not {
            "message_pk", "chat_pk", "sender_pk"}.issubset(refs):
        raise TransportError("invalid_refs")
    if set(refs) - {"message_pk", "chat_pk", "sender_pk", "chat_mid", "sender_mid"}:
        raise TransportError("invalid_refs")
    if not _internal_id(refs["message_pk"]):
        raise TransportError("invalid_message_pk")
    for key in ("chat_pk", "sender_pk"):
        if refs[key] is not None and not _internal_id(refs[key]):
            raise TransportError("invalid_internal_id")
    for key in ("chat_mid", "sender_mid"):
        if key in refs and refs[key] is not None and not isinstance(refs[key], str):
            raise TransportError("invalid_internal_id")
    try:
        source, pk = record["id"].rsplit(":", 1)
    except ValueError:
        raise TransportError("invalid_message_id") from None
    if not SOURCE_RE.fullmatch(source) or pk != refs["message_pk"]:
        raise TransportError("invalid_message_id")
    try:
        parsed = dt.datetime.fromisoformat(record["time"])
    except ValueError:
        raise TransportError("invalid_jst_time") from None
    if (not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?\+09:00", record["time"])
            or parsed.utcoffset() != dt.timedelta(hours=9)):
        raise TransportError("invalid_jst_time")
    # Reject surrogate escapes which cannot be faithfully sent as UTF-8.
    try:
        canonical(record).encode("utf-8")
    except UnicodeError:
        raise TransportError("invalid_utf8") from None
    return source, parsed.timestamp()


def read_jsonl(path):
    """A complete producer output is required; malformed input is atomic failure."""
    if str(path) == "-":
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    else:
        with open(path, "rb") as handle:
            raw = handle.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise TransportError("input_too_large")
    if raw and not raw.endswith(b"\n"):
        raise TransportError("incomplete_jsonl")
    records = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            record = strict_json(line.decode("utf-8"))
        except UnicodeError:
            raise TransportError("invalid_utf8") from None
        validate_record(record)
        records.append(record)
    return records


def _endpoint_ok(url):
    try:
        parts = urllib.parse.urlsplit(url)
        return (parts.scheme == "https" and parts.hostname in ALLOWED_HOSTS
                and parts.port in (None, 443) and not parts.username
                and not parts.password and not parts.fragment)
    except (ValueError, TypeError):
        return False


def load_config(path=None):
    """Secrets are never command arguments, status fields, or error text."""
    if path:
        p = Path(path)
        if os.name != "nt" and stat.S_IMODE(p.stat().st_mode) != 0o600:
            raise TransportError("config_requires_mode_0600")
        with p.open("r", encoding="utf-8") as handle:
            conf = strict_json(handle.read())
        if not isinstance(conf, dict):
            raise TransportError("invalid_config")
    else:
        conf = {"url": os.environ.get("LINE_SYNC_URL", ""),
                "secret": os.environ.get("LINE_SYNC_SECRET", ""),
                "key_id": os.environ.get("LINE_SYNC_KEY_ID", "v2")}
    if not _endpoint_ok(conf.get("url")):
        raise TransportError("invalid_https_gas_endpoint")
    if not isinstance(conf.get("secret"), str) or len(conf["secret"]) < 32:
        raise TransportError("secret_requires_32_characters")
    if not isinstance(conf.get("key_id", "v2"), str) or not KEY_ID_RE.fullmatch(
            conf.get("key_id", "v2")):
        raise TransportError("invalid_key_id")
    return {"url": conf["url"], "secret": conf["secret"],
            "key_id": conf.get("key_id", "v2")}


def wire_request(manifest, records, secret, key_id):
    unsigned = {"manifest": manifest, "records": records}
    signature = hmac.new(secret.encode("utf-8"),
                         canonical(unsigned).encode("utf-8"), hashlib.sha256).hexdigest()
    return {"v": 2, "manifest": manifest, "records": records,
            "auth": {"alg": "HMAC-SHA256", "key_id": key_id, "signature": signature}}


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not _endpoint_ok(newurl):
            raise TransportError("unsafe_redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def post_json(url, request, timeout=30):
    # Default urllib redirect handling supports the GAS POST -> content GET flow.
    opener = urllib.request.build_opener(_SafeRedirect())
    req = urllib.request.Request(url, data=canonical(request).encode("utf-8"),
                                 headers={"Content-Type": "application/json; charset=utf-8"},
                                 method="POST")
    try:
        with opener.open(req, timeout=timeout) as response:
            if response.status != 200:
                raise TransportError("http_non_200")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as error:
        raise TransportError("http_status_" + str(error.code)) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise TransportError("network_or_timeout") from None
    if len(raw) > MAX_RESPONSE_BYTES:
        raise TransportError("ack_too_large")
    try:
        return strict_json(raw.decode("utf-8"))
    except UnicodeError:
        raise TransportError("invalid_ack_encoding") from None


class Outbox:
    def __init__(self, path, timeout=5):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.exists() and self.path.stat().st_size:
            # Do not change journaling/permissions on an unrelated database.
            uri = self.path.resolve().as_uri() + "?mode=ro"
            try:
                check = sqlite3.connect(uri, uri=True)
                try:
                    app_id = check.execute("PRAGMA application_id").fetchone()[0]
                finally:
                    check.close()
            except sqlite3.Error:
                raise TransportError("foreign_or_invalid_database") from None
            if app_id != APPLICATION_ID:
                raise TransportError("foreign_or_invalid_database")
        self.db = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None)
        if os.name != "nt":
            os.chmod(self.path, 0o600)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA temp_store=MEMORY")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS records(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, pk INTEGER NOT NULL,
                moment REAL NOT NULL, hash TEXT NOT NULL, payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS batches(
                id TEXT PRIMARY KEY, source TEXT NOT NULL, created REAL NOT NULL,
                count INTEGER NOT NULL, parts INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS batch_records(
                batch_id TEXT NOT NULL REFERENCES batches(id),
                record_id TEXT NOT NULL UNIQUE REFERENCES records(id),
                PRIMARY KEY(batch_id,record_id));
            CREATE TABLE IF NOT EXISTS parts(
                batch_id TEXT NOT NULL REFERENCES batches(id), part INTEGER NOT NULL,
                manifest TEXT NOT NULL, payload TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
                acked REAL, error TEXT, PRIMARY KEY(batch_id,part));
            CREATE TABLE IF NOT EXISTS source_clock(source TEXT PRIMARY KEY, planned REAL NOT NULL);
        """)
        self.db.execute("PRAGMA application_id=" + str(APPLICATION_ID))

    def close(self):
        self.db.close()

    @contextmanager
    def transaction(self):
        # Holding the SQLite write lock across one HTTP attempt avoids concurrent
        # senders delivering the same part; a crash can still cause an idempotent retry.
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.execute("COMMIT")
        except BaseException:
            self.db.execute("ROLLBACK")
            raise

    def ingest(self, records):
        validated = []
        for record in records:
            source, moment = validate_record(record)
            payload = canonical(record)
            validated.append((record["id"], source, int(record["refs"]["message_pk"]),
                              moment, digest(payload.encode("utf-8")), payload))
        inserted = duplicate = 0
        with self.transaction():
            for values in validated:
                previous = self.db.execute("SELECT hash FROM records WHERE id=?", (values[0],)).fetchone()
                if previous:
                    if previous[0] != values[4]:
                        raise TransportError("message_id_content_conflict")
                    duplicate += 1
                    continue
                self.db.execute("INSERT INTO records VALUES(?,?,?,?,?,?)", values)
                inserted += 1
        return {"inserted": inserted, "duplicates": duplicate}

    def plan(self, now=None, interval=3600, max_bytes=DEFAULT_MAX_BYTES, force=False):
        now = time.time() if now is None else now
        # The v2 receiver formats records into a <=90 KiB mail body.
        if type(max_bytes) is not int or not 1024 <= max_bytes <= 80 * 1024:
            raise TransportError("invalid_max_bytes")
        if interval < 3600:
            raise TransportError("interval_minimum_3600_seconds")
        created = []
        with self.transaction():
            sources = self.db.execute("""SELECT DISTINCT source FROM records
                WHERE id NOT IN (SELECT record_id FROM batch_records) ORDER BY source""").fetchall()
            for source_row in sources:
                source = source_row[0]
                clock = self.db.execute("SELECT planned FROM source_clock WHERE source=?", (source,)).fetchone()
                if not force and clock and now - clock[0] < interval:
                    continue
                rows = self.db.execute("""SELECT * FROM records WHERE source=?
                    AND id NOT IN (SELECT record_id FROM batch_records)
                    ORDER BY moment, pk, id""", (source,)).fetchall()
                groups, group = [], []
                for row in rows:
                    record = strict_json(row["payload"])
                    candidate = group + [record]
                    # Nine digits reserve all decimal metadata width; key_id <=32.
                    worst = {"v": 2, "kind": "line_batch", "source": source,
                             "batch_id": "0" * 64, "part": 999999999, "parts": 999999999,
                             "count": 999999999, "sha256": "0" * 64}
                    size = len(canonical(wire_request(worst, candidate, "x" * 32,
                                                      "x" * 32)).encode("utf-8"))
                    if size <= max_bytes and len(candidate) <= 500:
                        group = candidate
                        continue
                    if not group:
                        raise TransportError("single_record_exceeds_part_limit")
                    groups.append(group)
                    group = [record]
                    size = len(canonical(wire_request(worst, group, "x" * 32,
                                                      "x" * 32)).encode("utf-8"))
                    if size > max_bytes:
                        raise TransportError("single_record_exceeds_part_limit")
                if group:
                    groups.append(group)
                hashes = {r["id"]: r["hash"] for r in rows}
                # Receiver permits 100 parts/batch. Large backlogs become several
                # independently identifiable batches without dropping or waiting an hour.
                for offset in range(0, len(groups), 100):
                    batch_groups = groups[offset:offset + 100]
                    batch_records = [r for group in batch_groups for r in group]
                    batch_id = digest((source + "\n" + "".join(
                        r["id"] + ":" + hashes[r["id"]] + "\n" for r in batch_records)).encode("utf-8"))
                    self.db.execute("INSERT INTO batches VALUES(?,?,?,?,?)",
                                    (batch_id, source, now, len(batch_records), len(batch_groups)))
                    self.db.executemany("INSERT INTO batch_records VALUES(?,?)",
                                        [(batch_id, r["id"]) for r in batch_records])
                    for number, records in enumerate(batch_groups, 1):
                        manifest = {"v": 2, "kind": "line_batch", "source": source,
                                    "batch_id": batch_id, "part": number, "parts": len(batch_groups),
                                    "count": len(records), "sha256": digest(records_bytes(records))}
                        self.db.execute("""INSERT INTO parts(batch_id,part,manifest,payload)
                            VALUES(?,?,?,?)""", (batch_id, number, canonical(manifest), canonical(records)))
                    created.append({"batch_id": batch_id, "count": len(batch_records), "parts": len(batch_groups)})
                self.db.execute("INSERT OR REPLACE INTO source_clock VALUES(?,?)", (source, now))
        return {"batches": created, "count": sum(x["count"] for x in created)}

    def status(self):
        scalar = lambda query: self.db.execute(query).fetchone()[0]
        return {"stored_records": scalar("SELECT COUNT(*) FROM records"),
                "unbatched_records": scalar("SELECT COUNT(*) FROM records WHERE id NOT IN (SELECT record_id FROM batch_records)"),
                "pending_parts": scalar("SELECT COUNT(*) FROM parts WHERE acked IS NULL"),
                "acked_parts": scalar("SELECT COUNT(*) FROM parts WHERE acked IS NOT NULL"),
                "failed_parts": scalar("SELECT COUNT(*) FROM parts WHERE acked IS NULL AND error IS NOT NULL")}

    def deliver(self, send=False, config=None, now=None, post=post_json, limit=100):
        if not send:
            return dict(self.status(), dry_run=True, sent_parts=0)
        config = load_config() if config is None else config
        # Do not rely on a caller-supplied unvalidated destination.
        if (not _endpoint_ok(config.get("url")) or
                not isinstance(config.get("secret"), str) or len(config["secret"]) < 32 or
                not isinstance(config.get("key_id"), str) or
                not KEY_ID_RE.fullmatch(config.get("key_id", ""))):
            raise TransportError("invalid_send_config")
        now = time.time() if now is None else now
        sent = 0
        error_code = None
        for _ in range(limit):
            failed = False
            with self.transaction():
                row = self.db.execute("""SELECT p.* FROM parts p JOIN batches b ON b.id=p.batch_id
                    WHERE p.acked IS NULL
                    ORDER BY b.created,b.rowid,p.part LIMIT 1""").fetchone()
                if row is None or row["next_attempt"] > now:
                    break
                manifest, records = strict_json(row["manifest"]), strict_json(row["payload"])
                try:
                    ack = post(config["url"], wire_request(manifest, records, config["secret"],
                                                          config["key_id"]), timeout=30)
                    expected = {"ok": True, "v": 2}
                    expected.update({key: manifest[key] for key in
                                     ("source", "batch_id", "part", "parts", "count", "sha256")})
                    if not isinstance(ack, dict) or any(
                            type(ack.get(k)) is not type(v) or ack.get(k) != v
                            for k, v in expected.items()):
                        raise TransportError("ack_mismatch")
                except Exception as exc:
                    # Never log exception text: it can contain a URL, secret or body.
                    error_code = str(exc) if isinstance(exc, TransportError) else "unexpected_transport_failure"
                    if not re.fullmatch(r"[a-z0-9_]{1,80}", error_code):
                        error_code = "unexpected_transport_failure"
                    attempt = row["attempts"] + 1
                    delay = min(3600, 60 * (2 ** min(attempt - 1, 6)))
                    self.db.execute("""UPDATE parts SET attempts=?,next_attempt=?,error=?
                        WHERE batch_id=? AND part=?""", (attempt, now + delay, error_code,
                                                         row["batch_id"], row["part"]))
                    failed = True
                else:
                    self.db.execute("""UPDATE parts SET attempts=attempts+1,acked=?,error=NULL
                        WHERE batch_id=? AND part=?""", (now, row["batch_id"], row["part"]))
                    sent += 1
            if failed:
                break
        return dict(self.status(), dry_run=False, sent_parts=sent, error=error_code)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Separate v2 outbox; delivery is dry-run by default.")
    parser.add_argument("--db", default=str(Path(__file__).with_name("outbox-v2.sqlite")))
    sub = parser.add_subparsers(dest="command", required=True)
    imp = sub.add_parser("import")
    imp.add_argument("jsonl")
    plan = sub.add_parser("plan")
    plan.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    plan.add_argument("--force", action="store_true", help="Only bypass the hourly planning interval.")
    delivery = sub.add_parser("deliver")
    delivery.add_argument("--send", action="store_true", help="Explicit opt-in, requires compatible GAS v2.")
    delivery.add_argument("--config", help="JSON file with mode 0600; otherwise environment variables.")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    box = None
    try:
        records = read_jsonl(args.jsonl) if args.command == "import" else None
        box = Outbox(args.db)
        if args.command == "import":
            result = box.ingest(records)
        elif args.command == "plan":
            result = box.plan(max_bytes=args.max_bytes, force=args.force)
        elif args.command == "deliver":
            conf = load_config(args.config) if args.send else None
            result = box.deliver(send=args.send, config=conf)
        else:
            result = box.status()
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
        return 1 if result.get("error") else 0
    except (TransportError, OSError, sqlite3.Error) as error:
        code = str(error) if isinstance(error, TransportError) else "local_io_or_database_failure"
        print(json.dumps({"ok": False, "error": code}, ensure_ascii=True))
        return 1
    finally:
        if box is not None:
            box.close()


if __name__ == "__main__":
    sys.exit(main())
