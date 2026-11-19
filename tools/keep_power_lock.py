"""Bounded, single-message lock-screen delivery using the installed RC6 worker."""
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import ctypes as C
import hashlib
import json
import os
from pathlib import Path
import plistlib
import signal
import sqlite3
import sys
import time
from urllib.parse import urlsplit

BASE = Path('/var/mobile/las-keep-lock-power-r2')
LABEL = 'com.sat0t.line-auto-sync.keep-lock-power-r2'
LIBEXEC = '/var/jb/usr/libexec/line-auto-sync'
TEXT = 'ロック中の自動配送を確認する試験メモ'
SECONDS = 240


def config_digest(config):
    return hashlib.sha256(json.dumps(
        {k: v for k, v in config.items() if k != 'enabled'},
        sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def validate_profile(config):
    baseline = json.loads((Path.home() / 'las-rc6-keep-check/config/config.json').read_text(encoding='utf-8'))
    chats = config['normal_line']['allowed_chat_pks']
    url = urlsplit(config['transport']['endpoint'])
    if (config['source_id'] != 'MVP_IPAD_LIVE' or len(chats) != 1
            or baseline['normal_line']['allowed_chat_pks'] != chats
            or baseline['line_db'] != config['line_db']
            or url.scheme != 'https' or url.netloc != 'script.google.com'
            or not url.path.startswith('/macros/s/') or not url.path.endswith('/exec')
            or url.query or url.fragment):
        raise RuntimeError('trial_profile_mismatch')


@contextmanager
def stay_awake():
    framework = '/System/Library/Frameworks'
    cf = C.CDLL(framework + '/CoreFoundation.framework/CoreFoundation')
    io = C.CDLL(framework + '/IOKit.framework/IOKit')
    cf.CFStringCreateWithCString.argtypes = [C.c_void_p, C.c_char_p, C.c_uint32]
    cf.CFStringCreateWithCString.restype = C.c_void_p
    cf.CFRelease.argtypes = [C.c_void_p]
    cf.CFRelease.restype = None
    io.IOPMAssertionCreateWithDescription.argtypes = [C.c_void_p] * 5 + [
        C.c_double, C.c_void_p, C.POINTER(C.c_uint32)]
    io.IOPMAssertionCreateWithDescription.restype = C.c_int
    io.IOPMAssertionRelease.argtypes = [C.c_uint32]
    io.IOPMAssertionRelease.restype = C.c_int
    strings = []
    try:
        for value in (b'PreventUserIdleSystemSleep', b'LINE Auto Sync lock trial', b'TimeoutActionRelease'):
            string = cf.CFStringCreateWithCString(None, value, 0x08000100)
            if not string:
                raise RuntimeError('assertion_string_failed')
            strings.append(string)
        kind, title, timeout = strings
        assertion = C.c_uint32()
        result = io.IOPMAssertionCreateWithDescription(
            kind, title, None, None, None, SECONDS + 15, timeout, C.byref(assertion))
        if result:
            raise RuntimeError(f'assertion_failed_{result & 0xffffffff:08x}')
        try:
            yield
        finally:
            io.IOPMAssertionRelease(assertion)
    finally:
        for string in strings:
            cf.CFRelease(string)


def configure_runtime():
    if os.getuid() != 501:
        raise RuntimeError('mobile_required')
    if BASE.is_symlink():
        raise RuntimeError('unsafe_profile')
    os.umask(0o077)
    for key in ('LINE_AUTO_SYNC_CONFIG_DIR', 'LINE_AUTO_SYNC_STATE_DIR'):
        os.environ.pop(key, None)
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(BASE)
    sys.path.insert(0, LIBEXEC)


def freeze_trial(runtime, sender, config):
    """Freeze only the approved row; leave every other pending row untouched."""
    import legacy_names
    import transport
    from delivery_state import DeliveryState

    with sender.sender_lock(runtime.STATE_DIR):
        queue = DeliveryState(runtime.STATE_DIR)
        try:
            with queue.transaction():
                queue.bind(config)
                active = queue.db.execute('SELECT * FROM delivery').fetchone()
                if active is not None:
                    queue.verify_frozen(active, config['source_id'], runtime.allowed_chat_pks(config))
                    return True
                matches = [row for row in queue.pending_rows() if row[4] == TEXT]
                if not matches:
                    return False
                if len(matches) != 1:
                    raise RuntimeError('duplicate_trial_text')
                pk, timestamp, chat, author, text = matches[0]
                if int(chat) not in runtime.allowed_chat_pks(config):
                    raise RuntimeError('trial_chat_mismatch')
                record = legacy_names.enrich_legacy(dict(id=pk, time_ms=timestamp,
                                                         chat=chat, sender=author, text=text))
                body = legacy_names.finalize_body(transport.canonical(record) + '\n')
                queue.freeze(body, [int(pk)])
                active = queue.db.execute('SELECT * FROM delivery').fetchone()
                queue.verify_frozen(active, config['source_id'], runtime.allowed_chat_pks(config))
                return True
        finally:
            queue.close()
            legacy_names.close()


def run(runtime, sync, sender, decode, log):
    config = runtime.require_enabled()
    chat, = runtime.allowed_chat_pks(config)
    deadline = time.monotonic() + SECONDS
    database = Path(config['line_db']).resolve(strict=True)
    stopped = False

    def request_stop(*_):
        nonlocal stopped
        stopped = True

    def guarded_post(endpoint, token, body, maxid):
        if stopped or time.monotonic() >= deadline:
            return False
        header, _, payload = body.partition('\n')
        records = decode(payload)
        if (endpoint != config['transport']['endpoint'] or len(records) != 1
                or records[0]['text'] != TEXT or records[0]['refs']['chat_pk'] != str(chat)):
            raise RuntimeError('unapproved_payload')
        log('posting', batch=json.loads(header)['batch_id'])
        return sender.post_legacy(endpoint, token, body, maxid)

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    with stay_awake():
        log('awake')
        baseline_hits = None
        while not stopped and time.monotonic() < deadline:
            if runtime.require_enabled() != config:
                raise RuntimeError('configuration_changed')
            try:
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                    hits = db.execute(
                        'SELECT COUNT(*) FROM ZMESSAGE WHERE ZCHAT=? AND ZTEXT=?',
                        (chat, TEXT)).fetchone()[0]
            except sqlite3.Error as exc:
                log('db_error', error=type(exc).__name__)
                time.sleep(5)
                continue
            if baseline_hits is None:
                baseline_hits = hits
            log('tick', marker_rows=hits, new_marker_rows=hits - baseline_hits)
            result = sync.collect()
            if result['pending']:
                if freeze_trial(runtime, sender, config):
                    report = sender.send_once(guarded_post)
                    log('delivery', sent=report.get('sent', False), pending=report['pending'],
                        reason=report.get('reason') or report.get('error'))
                    if report.get('sent'):
                        return
                else:
                    log('waiting_for_trial', other_pending=result['pending'])
            time.sleep(5)


def worker(expected_digest):
    import cli
    import runner
    import runtime_paths as runtime
    import sender
    import sync
    from presentation import decode_named

    with runner.worker_lock(), (BASE / 'power-lock.jsonl').open('x', encoding='utf-8') as stream:
        def log(event, **fields):
            stamp = time.time()
            local = datetime.fromtimestamp(stamp, timezone(timedelta(hours=9))).isoformat(timespec='seconds')
            print(json.dumps(dict(at=int(stamp), jst=local, event=event, **fields)), file=stream, flush=True)

        try:
            config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
            if config_digest(config) != expected_digest or config['enabled'] is not False:
                raise RuntimeError('trial_profile_changed')
            if cli.queue_status().get('pending') != 0:
                raise RuntimeError('expected_empty_trial_queue')
            cli.enable()
            run(runtime, sync, sender, decode_named, log)
        except Exception as exc:
            code = str(exc) if str(exc).startswith('assertion_failed_') else type(exc).__name__
            log('error', error=code)
            raise
        finally:
            cli.disable()
            log('disabled')


def main():
    configure_runtime()
    if len(sys.argv) == 3 and sys.argv[1] == 'worker':
        worker(sys.argv[2])
        return
    if sys.argv[1:] in (['stop'], ['status']):
        import cli
        if sys.argv[1] == 'stop':
            cli.disable()
        import runtime_paths as runtime
        print(json.dumps({'enabled': json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))['enabled']}))
        cli.status()
        if (BASE / 'power-lock.jsonl').is_file():
            print('\n'.join((BASE / 'power-lock.jsonl').read_text(encoding='utf-8').splitlines()[-12:]))
        return
    if len(sys.argv) != 2:
        raise SystemExit('provide a new plist path')
    from rc6_keep_check import prepare_trial
    import cli
    import runtime_paths as runtime
    import sync

    if not BASE.exists():
        prepare_trial(BASE, cli, sync, runtime)
    else:
        cfg = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
        if cfg['enabled'] or cli.queue_status().get('pending') != 0 or (BASE / 'power-lock.jsonl').exists():
            raise RuntimeError('existing_trial_requires_review')
    cfg = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
    validate_profile(cfg)
    spec = {
        'Label': LABEL,
        'ProgramArguments': ['/var/jb/usr/bin/python3', '-c',
                             Path(__file__).read_text(encoding='utf-8'), 'worker', config_digest(cfg)],
        'UserName': 'mobile',
        'RunAtLoad': True,
        'Umask': 0o077,
        'EnvironmentVariables': {'HOME': str(Path.home())},
    }
    with Path(sys.argv[1]).open('xb') as out:
        plistlib.dump(spec, out)
    print('PREPARED_NOT_STARTED')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('LOCK_TRIAL_ERROR:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
