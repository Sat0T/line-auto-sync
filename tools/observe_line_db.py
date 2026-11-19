"""Bounded read-only observations. No collector, sender, network, or config writes."""
import argparse
from contextlib import closing
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import time


class LockSignal:
    """Optional private Darwin signal; raw values require on-device calibration."""
    def __init__(self):
        self.api = None
        self.token = ctypes.c_int()
        self.error = None
        try:
            api = ctypes.CDLL('/usr/lib/system/libsystem_notify.dylib')
            api.notify_register_check.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_int)]
            api.notify_get_state.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint64)]
            api.notify_cancel.argtypes = [ctypes.c_int]
            for name in ('notify_register_check', 'notify_get_state', 'notify_cancel'):
                getattr(api, name).restype = ctypes.c_uint32
            status = api.notify_register_check(b'com.apple.springboard.lockstate', ctypes.byref(self.token))
            if status:
                self.error = 'register_failed'
            else:
                self.api = api
        except (OSError, AttributeError):
            self.error = 'api_unavailable'

    def sample(self):
        value = ctypes.c_uint64()
        if self.api is None:
            return {'available': False, 'reason': self.error}
        if self.api.notify_get_state(self.token.value, ctypes.byref(value)):
            return {'available': False, 'reason': 'read_failed'}
        return {'available': True, 'raw': value.value}

    def close(self):
        if self.api is not None:
            self.api.notify_cancel(self.token.value)
            self.api = None


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def file_metadata(path):
    try:
        stat = path.stat()
        with path.open('rb') as stream:
            stream.read(1)  # Check protected-file access; never output file bytes.
        return {'exists': True, 'readable': True, 'bytes': stat.st_size,
                'mtime_ns': stat.st_mtime_ns}
    except FileNotFoundError:
        return {'exists': False}
    except OSError as exc:
        return {'accessible': False, 'errno': exc.errno}


class Observer:
    def __init__(self, database, chat, marker, anchor=0):
        self.requested_database = Path(database).absolute()
        self.database = self.requested_database.resolve(strict=True)
        self.chat, self.marker, self.anchor = chat, marker, anchor
        stat = self.database.stat()
        self.identity = (stat.st_dev, stat.st_ino)
        self.control = self.connect()
        self.version = self.control.execute('PRAGMA data_version').fetchone()[0]
        self.last_absent_start = None
        self.started = time.monotonic()

    def connect(self):
        connection = sqlite3.connect(self.database.as_uri() + '?mode=ro', uri=True,
                                     isolation_level=None, timeout=2)
        connection.execute('PRAGMA query_only=ON')
        return connection

    def sample(self):
        started = stamp()
        self.check_identity()
        files = {name: file_metadata(Path(str(self.database) + suffix))
                 for name, suffix in (('db', ''), ('wal', '-wal'), ('shm', '-shm'))}
        # One statement, fresh connection: no snapshot spans multiple polls.
        try:
            before = self.control.execute('PRAGMA data_version').fetchone()[0]
            with closing(self.connect()) as db:
                row = db.execute('''SELECT COUNT(*),
                    COALESCE(SUM(ZTEXT IS NULL OR length(ZTEXT)=0),0),
                    COALESCE(SUM(ZTEXT=?),0),
                    COALESCE(SUM(ZTEXT=? AND Z_PK>?),0),
                    COALESCE(SUM(length(ZTEXT)>0 AND Z_PK>?),0)
                    FROM ZMESSAGE WHERE ZCHAT=?''',
                    (self.marker, self.marker, self.anchor, self.anchor, self.chat)).fetchone()
            after = self.control.execute('PRAGMA data_version').fetchone()[0]
        except sqlite3.Error as exc:
            self.check_identity()
            return dict(event='db_read_error', read_started_utc=started,
                        read_finished_utc=stamp(), files=files, error=type(exc).__name__)
        finished = stamp()
        self.check_identity()
        result = dict(event='snapshot', read_started_utc=started, read_finished_utc=finished,
                      marker_tracking=self.marker is not None,
                      elapsed_ms=round((time.monotonic() - self.started) * 1000),
                      scoped_rows=row[0], blank_rows=row[1], marker_rows=row[2],
                      marker_after_anchor=row[3], text_after_anchor=row[4], files=files,
                      commit_since_previous=after != self.version,
                      commit_during_sample=before != after)
        self.version = after
        if row[2] == 0:
            self.last_absent_start = started
        elif self.last_absent_start is not None:
            # Snapshot acquisition is somewhere inside each read window. Do not
            # use the end of the negative read as an exact lower commit bound.
            result['visibility_transition_window_utc'] = [self.last_absent_start, finished]
            self.last_absent_start = None
        return result

    def check_identity(self):
        if self.requested_database.resolve(strict=True) != self.database:
            raise RuntimeError('database_target_changed')
        stat = self.database.stat()
        if (stat.st_dev, stat.st_ino) != self.identity:
            raise RuntimeError('database_identity_changed')

    def close(self):
        self.control.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--profile', type=Path, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--marker')
    mode.add_argument('--scope-only', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--seconds', type=int, default=180)
    parser.add_argument('--interval', type=float, default=5)
    parser.add_argument('--darwin-lock-signal', action='store_true')
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 600 or not 1 <= args.interval <= 30:
        parser.error('invalid observation window')
    config = json.loads((args.profile / 'config/config.json').read_text(encoding='utf-8'))
    chats = config.get('normal_line', {}).get('allowed_chat_pks')
    if (config.get('source_id') != 'MVP_IPAD_LIVE' or not isinstance(chats, list)
            or len(chats) != 1 or type(chats[0]) is not int or chats[0] <= 0):
        parser.error('existing single-chat MVP profile required')
    database = Path(config['line_db']).resolve(strict=True)
    if args.output.parent.resolve() == database.parent:
        parser.error('log must be outside LINE database directory')
    queue = args.profile / 'state/normal-delivery.sqlite'
    with closing(sqlite3.connect(queue.resolve(strict=True).as_uri() + '?mode=ro', uri=True)) as db:
        entry = db.execute("SELECT value FROM meta WHERE key='collected'").fetchone()
    if entry is None:
        parser.error('existing initialized queue required')
    observer = Observer(config['line_db'], chats[0], args.marker, int(entry[0]))
    lock = LockSignal() if args.darwin_lock_signal else None
    stopped = False

    def stop(*_):
        nonlocal stopped
        stopped = True
    try:
        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            deadline = time.monotonic() + args.seconds
            exit_reason = 'deadline'
            try:
                while not stopped and time.monotonic() < deadline:
                    lock_before = lock.sample() if lock else None
                    result = observer.sample()
                    if lock:
                        result['lock_signal_before'] = lock_before
                        result['lock_signal_after'] = lock.sample()
                    print(json.dumps(result), file=out, flush=True)
                    time.sleep(min(args.interval, max(0, deadline - time.monotonic())))
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                exit_reason = 'error'
                # Do not expose exception messages containing paths or SQL data.
                print(json.dumps({'event': 'observation_error', 'at_utc': stamp(),
                                  'error': type(exc).__name__}), file=out, flush=True)
                raise SystemExit(1) from None
            finally:
                print(json.dumps({'event': 'stopped', 'at_utc': stamp(),
                                  'reason': 'signal' if stopped else exit_reason}), file=out, flush=True)
    finally:
        observer.close()
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
