"""Bounded, single-message lock-screen delivery using the installed RC6 worker."""
from contextlib import closing, contextmanager
import ctypes as C
import json
import os
from pathlib import Path
import plistlib
import signal
import sqlite3
import sys
import time

BASE = Path('/var/mobile/las-keep-lock-power')
LABEL = 'com.sat0t.line-auto-sync.keep-lock-power'
LIBEXEC = '/var/jb/usr/libexec/line-auto-sync'
TEXT = 'ロック中に届くか確認する試験メモ'
SECONDS = 240


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


def run(runtime, sync, sender, decode, log):
    config = runtime.require_enabled()
    chat, = runtime.allowed_chat_pks(config)
    deadline = time.time() + SECONDS
    database = Path(config['line_db']).resolve(strict=True)
    stopped = False

    def request_stop(*_):
        nonlocal stopped
        stopped = True

    def guarded_post(endpoint, token, body, maxid):
        if stopped or time.time() >= deadline:
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
        while not stopped and time.time() < deadline:
            try:
                with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
                    hits = db.execute(
                        'SELECT COUNT(*) FROM ZMESSAGE WHERE ZCHAT=? AND ZTEXT=?',
                        (chat, TEXT)).fetchone()[0]
            except sqlite3.Error as exc:
                log('db_error', error=type(exc).__name__)
                time.sleep(5)
                continue
            log('tick', marker_rows=hits)
            result = sync.collect()
            if result['pending']:
                queue = runtime.STATE_DIR / 'normal-delivery.sqlite'
                with closing(sqlite3.connect(queue.as_uri() + '?mode=ro', uri=True)) as db:
                    rows = db.execute('SELECT raw FROM messages WHERE acked=0 LIMIT 2').fetchall()
                if len(rows) != 1 or json.loads(rows[0][0])[4] != TEXT:
                    log('held', pending=result['pending'])
                    return
                report = sender.send_once(guarded_post)
                log('delivery', sent=report.get('sent', False), pending=report['pending'],
                    reason=report.get('reason') or report.get('error'))
                if report.get('sent'):
                    return
            time.sleep(5)


def worker():
    import cli
    import runner
    import runtime_paths as runtime
    import sender
    import sync
    from presentation import decode_named

    with runner.worker_lock(), (BASE / 'power-lock.jsonl').open('x', encoding='utf-8') as stream:
        def log(event, **fields):
            print(json.dumps(dict(at=int(time.time()), event=event, **fields)), file=stream, flush=True)

        try:
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
    if sys.argv[1:] == ['worker']:
        worker()
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
    spec = {
        'Label': LABEL,
        'ProgramArguments': ['/var/jb/usr/bin/python3', '-c',
                             Path(__file__).read_text(encoding='utf-8'), 'worker'],
        'UserName': 'mobile',
        'RunAtLoad': True,
        'Umask': 0o077,
    }
    with Path(sys.argv[1]).open('xb') as out:
        plistlib.dump(spec, out)
    print('LOCK_JOB_READY')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('LOCK_TRIAL_ERROR:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
