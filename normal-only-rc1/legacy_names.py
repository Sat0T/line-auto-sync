from runtime_paths import CONFIG_FILE as _las_config_file
from runtime_paths import STATE_DIR as _las_state_dir
"""Narrow enrichment/presentation adapter for the verified existing sender.

The existing sender retains its CSV, hourly gate, 60KB selection, HTTP call,
strict OK check and successful-send checkpoints. No LINE DB is written.
"""
import atexit
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from resolver import Resolver
import presentation
import transport

ROOT = _las_state_dir
STAGE = ROOT
APP_ID = 0x4C4E4331
_session = None
_fallback = False
_lock = None


def acquire_sender_lock():
    global _lock
    if os.name == 'nt' or _lock is not None:
        return
    import fcntl
    fd = os.open(str(ROOT / '.sender-names.lock'), os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise SystemExit(0)
    _lock = fd


class Session:
    def __init__(self):
        self.resolver = None
        self.cache = None
        try:
            conf = json.loads((_las_config_file).read_text())
            if str(Path((ROOT / 'dbpath').read_text().strip()).resolve()) != conf['line_db']:
                raise ValueError('database_generation_changed')
            self.source = conf['source_id']
            self.resolver = Resolver(conf['line_db'], conf.get('group_db'), self.source, conf['timestamp_unit'])
            path = STAGE / 'named-cache.sqlite'
            if path.exists() and path.stat().st_size:
                check = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
                try:
                    if check.execute('PRAGMA application_id').fetchone()[0] != APP_ID:
                        raise ValueError('foreign_cache')
                finally:
                    check.close()
            self.cache = sqlite3.connect(str(path), timeout=5)
            if os.name != 'nt':
                os.chmod(path, 0o600)
            self.cache.execute('PRAGMA journal_mode=WAL')
            self.cache.execute('PRAGMA synchronous=FULL')
            self.cache.execute('PRAGMA application_id=' + str(APP_ID))
            self.cache.execute('CREATE TABLE IF NOT EXISTS named(id TEXT PRIMARY KEY,raw_hash TEXT NOT NULL,payload TEXT NOT NULL)')
            self.cache.commit()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.cache is not None:
            self.cache.close()
            self.cache = None
        if self.resolver is not None:
            self.resolver.close()
            self.resolver = None

    def enrich(self, raw):
        if set(raw) != {'id', 'time_ms', 'chat', 'sender', 'text'}:
            raise ValueError('unexpected_legacy_shape')
        key = self.source + ':' + str(raw['id'])
        raw_hash = transport.digest(transport.canonical(raw).encode('utf-8'))
        old = self.cache.execute('SELECT raw_hash,payload FROM named WHERE id=?', (key,)).fetchone()
        if old:
            if old[0] != raw_hash:
                raise ValueError('message_id_input_conflict')
            record = transport.strict_json(old[1])
            transport.validate_record(record)
            return record
        record = self.resolver.enrich({'Z_PK': raw['id'], 'ZTIMESTAMP': raw['time_ms'],
            'ZCHAT': raw['chat'], 'ZSENDER': raw['sender'], 'ZTEXT': raw['text']})
        transport.validate_record(record)
        self.cache.execute('INSERT INTO named VALUES(?,?,?)', (key, raw_hash, transport.canonical(record)))
        self.cache.commit()
        return record


def enrich_legacy(raw):
    global _session, _fallback
    if _fallback:
        return raw
    if _session is None:
        try:
            _session = Session()
        except Exception:
            # Do not silently send raw data if name enrichment fails.
            raise RuntimeError('name_enrichment_unavailable') from None
    return _session.enrich(raw)


def finalize_body(body):
    if _fallback:
        return body
    records = [transport.strict_json(line) for line in body.splitlines() if line.strip()]
    if not records:
        raise ValueError('empty_named_batch')
    by_id = {}
    for record in records:
        transport.validate_record(record)
        encoded = transport.canonical(record)
        if record['id'] in by_id and by_id[record['id']] != encoded:
            raise ValueError('duplicate_message_conflict')
        by_id[record['id']] = encoded
    unique = [transport.strict_json(value) for value in by_id.values()]
    unique.sort(key=lambda record: (record['time'], int(record['refs']['message_pk'])))
    if len({record['id'].rsplit(':', 1)[0] for record in unique}) != 1:
        raise ValueError('mixed_sources')
    source = unique[0]['id'].rsplit(':', 1)[0]
    sha = transport.digest(transport.records_bytes(unique))
    manifest = {'v': 2, 'kind': 'line_batch', 'source': source,
        'batch_id': transport.digest((source + '\n' + sha).encode()),
        'part': 1, 'parts': 1, 'count': len(unique), 'sha256': sha}
    encoded = presentation.encode_named(unique)
    result = transport.canonical(manifest) + '\n' + encoded['payload']
    if len(result.encode('utf-8')) > 90000:
        raise ValueError('named_batch_exceeds_legacy_limit')
    return result


def close():
    global _session
    if _session is not None:
        _session.close()
        _session = None


atexit.register(close)
