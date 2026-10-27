"""Read-only LINE name enrichment. Never edits LINE DBs or existing sync state."""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import re
import sqlite3
import sys

JST = dt.timezone(dt.timedelta(hours=9))
SOURCE = re.compile(r"[A-Za-z0-9_.-]{1,64}\Z")


class SafeError(Exception):
    pass


def connect_ro(path):
    p = Path(path).expanduser().resolve(strict=True)
    db = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=3)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("BEGIN")  # A consistent read snapshot within this database.
    return db


def columns(db, table):
    if table not in {"ZMESSAGE", "ZUSER", "ZCHAT", "ZUNIFIEDGROUP"}:
        raise SafeError("unapproved_table")
    return {r[1]: r[2] for r in db.execute('PRAGMA table_info("' + table + '")')}


def sid(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]+", str(value)):
        raise SafeError("invalid_internal_id")
    return str(int(value))


def clean_name(value):
    return value if isinstance(value, str) and value.strip() else None


def unique_map(rows):
    result = {}
    for key, name in rows:
        if key is None or key == "" or not clean_name(name):
            continue
        key = str(key)
        if key in result and result[key] != name:
            result[key] = None  # Ambiguous identity: do not pick a row.
        else:
            result.setdefault(key, name)
    return result


class Resolver:
    def __init__(self, line_db, group_db=None, source="", timestamp_unit=""):
        self.db = None
        self.group_db = None
        try:
            self._initialize(line_db, group_db, source, timestamp_unit)
        except BaseException:
            self.close()
            raise

    def _initialize(self, line_db, group_db, source, timestamp_unit):
        if not SOURCE.fullmatch(source) or "REPLACE" in source:
            raise SafeError("set_stable_unique_source_id_first")
        if timestamp_unit != "unix_ms":
            raise SafeError("timestamp_unit_must_be_verified_unix_ms")
        self.source = source
        self.db = connect_ro(line_db)
        self.group_db = None
        self.mc = columns(self.db, "ZMESSAGE")
        if not {"Z_PK", "ZTIMESTAMP", "ZTEXT", "ZSENDER", "ZCHAT"} <= self.mc.keys():
            self.close()
            raise SafeError("message_schema_not_supported")
        self.uc, self.cc = columns(self.db, "ZUSER"), columns(self.db, "ZCHAT")
        self.users, self.chats = {}, {}
        self.users_by_mid = {}
        if {"Z_PK", "ZNAME"} <= self.uc.keys():
            names = ["Z_PK", "ZNAME"] + [c for c in ("ZCUSTOMNAME", "ZMID") if c in self.uc]
            for r in self.db.execute("SELECT " + ",".join(names) + " FROM ZUSER"):
                key = sid(r["Z_PK"])
                self.users[key] = {
                    "name": clean_name(r["ZCUSTOMNAME"]) if "ZCUSTOMNAME" in names else None,
                    "original": clean_name(r["ZNAME"]),
                    "mid": r["ZMID"] if "ZMID" in names else None,
                }
            self.users_by_mid = unique_map((u["mid"], u["name"] or u["original"]) for u in self.users.values())
        if {"Z_PK", "ZMID"} <= self.cc.keys():
            self.chats = {sid(r["Z_PK"]): r["ZMID"] for r in self.db.execute("SELECT Z_PK,ZMID FROM ZCHAT")}
        # The actual installation may keep groups in a separate database.
        gdb = self.db
        if group_db:
            self.group_db = connect_ro(group_db)
            gdb = self.group_db
        self.gc = columns(gdb, "ZUNIFIEDGROUP")
        self.groups = unique_map(gdb.execute("SELECT ZID,ZNAME FROM ZUNIFIEDGROUP")) if {"ZID", "ZNAME"} <= self.gc.keys() else {}

    def close(self):
        if self.group_db is not None:
            self.group_db.close()
        if self.db is not None:
            self.db.close()

    def enrich(self, row):
        pk, chat_pk, sender_pk = (sid(row[k]) for k in ("Z_PK", "ZCHAT", "ZSENDER"))
        if pk is None:
            raise SafeError("message_pk_missing")
        raw_time = row["ZTIMESTAMP"]
        try:
            # No epoch guessing or silent seconds/milliseconds conversion.
            if isinstance(raw_time, bool) or not re.fullmatch(r"[0-9]+", str(raw_time)):
                raise ValueError()
            seconds, milliseconds = divmod(int(raw_time), 1000)
            instant = dt.datetime.fromtimestamp(seconds, JST) + dt.timedelta(milliseconds=milliseconds)
        except (ValueError, OverflowError, OSError):
            raise SafeError("invalid_unix_ms") from None
        if not 2000 <= instant.year <= 2100:
            raise SafeError("timestamp_epoch_or_unit_not_verified")
        text = row["ZTEXT"]
        if text is not None and not isinstance(text, str):
            raise SafeError("text_is_not_string")
        user = self.users.get(sender_pk, {})
        sender = user.get("name") or user.get("original")
        mid = self.chats.get(chat_pk)
        group = self.groups.get(str(mid)) if mid else None
        direct = self.users_by_mid.get(str(mid)) if mid else None
        # If one MID matches two incompatible namespaces, retain the ID.
        chat = None if group and direct and group != direct else group or direct
        return {
            "id": self.source + ":" + pk,
            "time": instant.isoformat(timespec="milliseconds"),
            "chat": chat or "[chat:" + (chat_pk or "null") + "]",
            "sender": sender or "[sender:" + (sender_pk or "null") + "]",
            "text": text,
            "refs": {"message_pk": pk, "chat_pk": chat_pk, "sender_pk": sender_pk,
                     "chat_mid": mid, "sender_mid": user.get("mid")},
        }

    def messages(self, after, limit):
        if not 1 <= limit <= 5000 or after < 0:
            raise SafeError("invalid_preview_bounds")
        rows = self.db.execute("SELECT Z_PK,ZTIMESTAMP,ZTEXT,ZSENDER,ZCHAT FROM ZMESSAGE WHERE Z_PK>? ORDER BY Z_PK LIMIT ?", (after, limit))
        return [self.enrich(row) for row in rows]

    def report(self):
        totals = self.db.execute("SELECT COUNT(*),MIN(Z_PK),MAX(Z_PK),MIN(ZTIMESTAMP),MAX(ZTIMESTAMP) FROM ZMESSAGE").fetchone()
        mids = [str(x) for x in self.chats.values() if x]
        nulls = self.db.execute("SELECT COUNT(*) FROM ZMESSAGE WHERE ZSENDER IS NULL").fetchone()[0]
        return {"v": 2, "read_only": True, "schema": {"ZMESSAGE": self.mc, "ZUSER": self.uc, "ZCHAT": self.cc, "ZUNIFIEDGROUP": self.gc},
                "messages": {"count": totals[0], "pk_min": totals[1], "pk_max": totals[2], "time_min": totals[3], "time_max": totals[4], "null_sender_count": nulls},
                "name_checks": {"users": len(self.users), "chats": len(self.chats), "groups": len(self.groups),
                                "chat_mid_matches_group_id": sum(bool(self.groups.get(m)) for m in mids),
                                "chat_mid_matches_user_mid": sum(bool(self.users_by_mid.get(m)) for m in mids)},
                "notes": ["No names, MID values, message bodies or secrets in this report.", "Names are current lookup snapshots, not historical identity proof.", "NULL sender never implies self. No OpenChat identity inference.", "Separate DB reads are not an atomic cross-database snapshot."]}


def write_private(path, value):
    path = Path(path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(value)
        f.flush()
        os.fsync(f.fileno())


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("mode", choices=["probe", "preview"])
    p.add_argument("--config", required=True)
    p.add_argument("--after", type=int, default=0)
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    try:
        c = json.loads(Path(args.config).expanduser().read_text(encoding="utf-8"))
        r = Resolver(c["line_db"], c.get("group_db"), c["source_id"], c["timestamp_unit"])
        try:
            if args.mode == "probe":
                value = json.dumps(r.report(), ensure_ascii=False, indent=2) + "\n"
                count = 0
            else:
                records = r.messages(args.after, args.limit)
                value = "".join(json.dumps(x, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n" for x in records)
                count = len(records)
            write_private(args.output, value)
        finally:
            r.close()
        print(json.dumps({"ok": True, "mode": args.mode, "records": count, "line_db_modified": False}))
    except (SafeError, OSError, sqlite3.Error, ValueError, KeyError):
        print('{"ok":false,"error":"probe_or_preview_failed; existing_sync_unchanged"}', file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
