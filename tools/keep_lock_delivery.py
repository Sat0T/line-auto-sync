"""Isolated single-text trial. Private evidence; one POST, no automatic retry."""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import sqlite3
import subprocess
import sys
import time
from urllib.parse import urlsplit

TEXT = 'LAS-LOCK-20261120-N1 2026年11月25日18時までに配送試験の結果を確認する'
LABEL = 'com.sat0t.line-auto-sync.lock-n1'
OBSERVE_SECONDS = 180
HTTP_SECONDS = 30
HARD_SECONDS = 240
KILL_GRACE_SECONDS = 5
KILL_SIGNAL = getattr(signal, 'SIGKILL', 9)  # POSIX production; portable fake-process tests.
REQUIRED_RUNTIME = ('cli.py', 'delivery_state.py', 'legacy_names.py', 'normal_hold.py',
                    'presentation.py', 'resolver.py', 'runner.py', 'runtime_paths.py',
                    'setup_normal.py', 'sender.py', 'sync.py', 'transport.py')


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(path):
    if path.is_symlink():
        raise RuntimeError('unsafe_symlink')
    return json.loads(path.read_text(encoding='utf-8'))


def new_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as out:
        json.dump(value, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())


def log(base, event, **fields):
    # This is a PRIVATE local log, including evidence IDs; never publish it.
    raw = (json.dumps(dict(event=event, at_utc=stamp(), run_id=base.name, **fields),
                      ensure_ascii=False) + '\n').encode()
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, 'O_NOFOLLOW', 0)
    fd = os.open(base / 'events.jsonl', flags, 0o600)
    try:
        if os.write(fd, raw) != len(raw):
            raise RuntimeError('incomplete_evidence_write')
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_baseline(config):
    chats = config.get('normal_line', {}).get('allowed_chat_pks')
    endpoint = config.get('transport', {}).get('endpoint', '')
    url = urlsplit(endpoint)
    if (config.get('enabled') is not False or config.get('source_id') != 'MVP_IPAD_LIVE'
            or config.get('schema_version') != 1 or config.get('timestamp_unit') != 'unix_ms'
            or config.get('normal_line', {}).get('enabled') is not True
            or not isinstance(chats, list) or len(chats) != 1
            or type(chats[0]) is not int or chats[0] <= 0
            or url.scheme != 'https' or url.netloc != 'script.google.com'
            or not url.path.startswith('/macros/s/') or not url.path.endswith('/exec')
            or url.query or url.fragment or not config['transport'].get('token')):
        raise RuntimeError('expected_disabled_verified_keep_profile')


def configure(base):
    base = Path(base).absolute()
    if base.is_symlink():
        raise RuntimeError('unsafe_profile')
    for key in ('LINE_AUTO_SYNC_CONFIG_DIR', 'LINE_AUTO_SYNC_STATE_DIR'):
        os.environ.pop(key, None)
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(base)
    sys.path.insert(0, str(base / 'runtime'))
    sys.path.insert(0, str(base / 'tools'))
    os.umask(0o077)
    return base


def check_plan(base):
    plan = read_json(base / 'plan.json')
    baseline = Path(plan['baseline']) / 'config/config.json'
    raw = baseline.read_bytes()
    original = json.loads(raw)
    validate_baseline(original)
    if digest(raw) != plan['baseline_config_sha256']:
        raise RuntimeError('baseline_changed')
    current = read_json(base / 'config/config.json')
    current['enabled'] = False
    if current != original or plan['text'] != TEXT or plan['source'] != original['source_id']:
        raise RuntimeError('trial_scope_changed')
    for relative, expected in plan['code_sha256'].items():
        path = base / relative
        if path.is_symlink() or digest(path.read_bytes()) != expected:
            raise RuntimeError('trial_code_changed')
    return plan, original


def target_snapshot(observer, chat, text, lock=None):
    from observe_line_db import file_metadata
    result = dict(event='snapshot', read_started_utc=stamp())
    if lock:
        result['lock_signal_before'] = lock.sample()
    observer.check_identity()
    result['files'] = {name: file_metadata(Path(str(observer.database) + suffix))
                       for name, suffix in (('db', ''), ('wal', '-wal'), ('shm', '-shm'))}
    with closing(observer.connect()) as db:
        db.execute('BEGIN')
        count = db.execute('SELECT COUNT(*) FROM ZMESSAGE WHERE ZCHAT=?', (chat,)).fetchone()[0]
        rows = db.execute('''SELECT Z_PK,ZTIMESTAMP,ZCHAT,ZSENDER,ZTEXT FROM ZMESSAGE
            WHERE ZCHAT=? AND ZTEXT=? ORDER BY Z_PK LIMIT 2''', (chat, text)).fetchall()
    observer.check_identity()
    result.update(read_finished_utc=stamp(), marker_rows=len(rows), scoped_rows=count,
                  target_pk=str(rows[0][0]) if len(rows) == 1 else None, identity_checked=True)
    if lock:
        result['lock_signal_after'] = lock.sample()
    return result, rows


def prepare(baseline, base, runtime):
    baseline = Path(baseline).resolve(strict=True)
    base = Path(base).absolute()
    canonical_base = base.resolve(strict=False)
    if (base.exists() or base.is_symlink() or baseline == canonical_base
            or baseline in canonical_base.parents):
        raise RuntimeError('new_isolated_profile_required')
    raw = (baseline / 'config/config.json').read_bytes()
    config = json.loads(raw)
    validate_baseline(config)
    database = Path(config['line_db']).resolve(strict=True)
    if canonical_base == database.parent or database.parent in canonical_base.parents:
        raise RuntimeError('profile_inside_line_directory')
    # Absence is checked in the existing allowed chat only; never write LINE DB.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=2)) as db:
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute('SELECT COUNT(*) FROM ZMESSAGE WHERE ZCHAT=? AND ZTEXT=?',
                      (config['normal_line']['allowed_chat_pks'][0], TEXT)).fetchone()[0]:
            raise RuntimeError('trial_text_already_present')
        high = db.execute('SELECT COALESCE(MAX(Z_PK),0) FROM ZMESSAGE').fetchone()[0]
    files = {('runtime/' + name): Path(runtime) / name for name in REQUIRED_RUNTIME}
    files.update({('tools/' + name): Path(__file__).resolve().parent / name
                  for name in ('keep_lock_delivery.py', 'keep_power_lock.py', 'observe_line_db.py')})
    contents = {}
    for relative, path in files.items():
        if path.is_symlink():
            raise RuntimeError('runtime_symlink')
        contents[relative] = path.read_bytes()
        compile(contents[relative], '<trial-source>', 'exec')
    os.umask(0o077)
    base.mkdir(mode=0o700)
    for name in ('state', 'config', 'runtime', 'tools'):
        (base / name).mkdir(mode=0o700)
    for relative, data in contents.items():
        with (base / relative).open('xb') as out:
            out.write(data)
    new_json(base / 'config/config.json', config)
    (base / 'state/dbpath').write_text(str(database), encoding='utf-8')
    configure(base)
    from delivery_state import DeliveryState
    queue = DeliveryState(base / 'state')
    try:
        with queue.transaction():
            queue.bind(config)
            queue.initialize_cursor(high)
    finally:
        queue.close()
    plan = dict(version=1, text=TEXT, source=config['source_id'],
                chat_pk=str(config['normal_line']['allowed_chat_pks'][0]),
                baseline=str(baseline), baseline_config_sha256=digest(raw), initial_baseline=high,
                endpoint_sha256=digest(config['transport']['endpoint'].encode()),
                code_sha256={p: digest(data) for p, data in contents.items()},
                observe_seconds=OBSERVE_SECONDS, http_timeout_seconds=HTTP_SECONDS,
                hard_limit_seconds=HARD_SECONDS, kill_grace_seconds=KILL_GRACE_SECONDS,
                stop_upper_seconds=250,
                send_max=1, retries=0, protocol='legacy-OK', prepared_at_utc=stamp(),
                gmail_delivery='unverified', deployment_response_contract_verified=False)
    new_json(base / 'plan.json', plan)
    spec = dict(Label=LABEL, UserName='mobile', RunAtLoad=True, KeepAlive=False,
                Umask=0o077, ExitTimeOut=10,
                EnvironmentVariables={'HOME': '/var/mobile'},
                ProgramArguments=['/var/jb/usr/bin/python3', str(base / 'tools/keep_lock_delivery.py'),
                                  'supervise', '--base', str(base)])
    with (base / 'worker.plist').open('xb') as out:
        plistlib.dump(spec, out)
    log(base, 'prepared', target_absent=True, endpoint_matches_existing=True,
        send_max=1, retries=0, observe_seconds=OBSERVE_SECONDS, stop_upper_seconds=250)
    return plan


def validate_payload(config, plan, target_pk, body, maxid):
    import presentation
    import transport
    header, separator, payload = body.partition('\n')
    manifest = transport.strict_json(header)
    rows = presentation.decode_named(payload) if separator else []
    if (len(rows) != 1 or manifest.get('count') != 1 or manifest.get('source') != plan['source']
            or str(maxid) != str(target_pk) or rows[0]['id'] != plan['source'] + ':' + str(target_pk)
            or rows[0]['refs']['message_pk'] != str(target_pk)
            or rows[0]['refs']['chat_pk'] != plan['chat_pk'] or rows[0]['text'] != plan['text']
            or digest(config['transport']['endpoint'].encode()) != plan['endpoint_sha256']):
        raise RuntimeError('unapproved_payload')
    restored = transport.records_bytes(rows)
    expected = transport.digest(restored)
    if (manifest.get('sha256') != expected or manifest.get('part') != 1 or manifest.get('parts') != 1
            or manifest.get('batch_id') != transport.digest((plan['source'] + '\n' + expected).encode())):
        raise RuntimeError('invalid_payload_manifest')
    return manifest


def freeze_target(base, config, plan, target_pk):
    import legacy_names
    import sender
    import transport
    from delivery_state import DeliveryState
    with sender.sender_lock(base / 'state'):
        queue = DeliveryState(base / 'state')
        try:
            with queue.transaction():
                queue.bind(config)
                if queue.db.execute('SELECT 1 FROM delivery').fetchone():
                    raise RuntimeError('unexpected_existing_delivery')
                record = queue.db.execute('SELECT raw FROM messages WHERE pk=? AND acked=0',
                                          (int(target_pk),)).fetchone()
                if record is None:
                    raise RuntimeError('visible_target_not_collected')
                pk, timestamp, chat, author, text = json.loads(record['raw'])
                enriched = legacy_names.enrich_legacy(dict(id=pk, time_ms=timestamp, chat=chat,
                                                           sender=author, text=text))
                body = legacy_names.finalize_body(transport.canonical(enriched) + '\n')
                manifest = validate_payload(config, plan, target_pk, body, target_pk)
                queue.freeze(body, [int(target_pk)])
                queue.verify_frozen(queue.db.execute('SELECT * FROM delivery').fetchone(),
                                    plan['source'], [int(plan['chat_pk'])])
            new_json(base / 'payload-private.json', dict(body=body, manifest=manifest,
                                                       target_pk=str(target_pk)))
            return manifest
        finally:
            queue.close()
            legacy_names.close()


def http_child(base):
    configure(base)
    plan, _ = check_plan(base)
    lease = read_json(base / 'http-lease.json')
    if (os.getppid() != lease['parent_pid'] or time.monotonic() >= lease['send_start_deadline_monotonic']
            or (base / 'stop-request.json').exists()):
        raise RuntimeError('invalid_http_lease')
    import runtime_paths as runtime
    import sender
    config = runtime.require_enabled()
    target = read_json(base / 'target-private.json')['target_pk']
    frozen = read_json(base / 'payload-private.json')
    manifest = validate_payload(config, plan, target, frozen['body'], target)
    # Durable at-most-one network-start reservation; ambiguity never auto-retries.
    new_json(base / 'network-started.json', dict(at_utc=stamp(), target_pk=target,
                                                meaning='dispatch_reservation_not_delivery'))
    if (base / 'stop-request.json').exists() or time.monotonic() >= lease['send_start_deadline_monotonic']:
        raise RuntimeError('stopped_before_http')
    code, ok, response_at = None, False, None
    try:
        import urllib.request
        data = json.dumps(dict(token=config['transport']['token'], maxid=target,
                               body=frozen['body']), ensure_ascii=False).encode()
        request = urllib.request.Request(config['transport']['endpoint'], data,
                                         {'Content-Type': 'application/json'})
        opener = urllib.request.build_opener(sender.SafeRedirect())
        remaining = lease['completion_deadline_monotonic'] - time.monotonic()
        if ((base / 'stop-request.json').exists()
                or time.monotonic() >= lease['send_start_deadline_monotonic'] or remaining <= 0):
            raise RuntimeError('stopped_before_http_open')
        log(base, 'http_started', target_pk=target, batch_id=manifest['batch_id'],
            maxid=target, timeout_seconds=HTTP_SECONDS, endpoint_matches_existing=True,
            endpoint_sha256=digest(config['transport']['endpoint'].encode()))
        # Evidence fsync can itself be delayed. Never dispatch after that delay.
        if ((base / 'stop-request.json').exists()
                or time.monotonic() >= lease['send_start_deadline_monotonic']):
            log(base, 'http_dispatch_cancelled', reason='stop_or_start_deadline')
            raise RuntimeError('stopped_after_dispatch_boundary_log')
        with opener.open(request, timeout=min(HTTP_SECONDS, remaining)) as response:
            code = response.status
            answer = response.read(129)
            response_at = stamp()
            ok = 200 <= code < 300 and len(answer) <= 128 and answer.strip() == b'OK'
    except Exception:
        pass
    result = dict(target_pk=target, batch_id=manifest['batch_id'], http_status=code,
                  response_ok=ok, outcome='accepted' if ok else 'unconfirmed',
                  response_received_utc=response_at, request_finished_utc=stamp())
    log(base, 'http_finished', **result)
    new_json(base / 'http-result.json', result)


def one_post(base, plan, deadline, stopped):
    def post(endpoint, token, body, maxid):
        check_plan(base)
        current = read_json(base / 'config/config.json')
        if endpoint != current['transport']['endpoint'] or token != current['transport']['token']:
            raise RuntimeError('sender_destination_changed')
        target = read_json(base / 'target-private.json')['target_pk']
        validate_payload(current, plan, target, body, maxid)
        frozen = read_json(base / 'payload-private.json')
        if (body != frozen['body'] or stopped() or time.monotonic() >= deadline
                or (base / 'stop-request.json').exists()):
            return False
        new_json(base / 'http-lease.json', dict(parent_pid=os.getpid(),
                    send_start_deadline_monotonic=deadline,
                    completion_deadline_monotonic=time.monotonic() + HTTP_SECONDS))
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'http-child', '--base', str(base)],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        expires = time.monotonic() + HTTP_SECONDS
        while child.poll() is None:
            if stopped() or (base / 'stop-request.json').exists() or time.monotonic() >= expires:
                child.kill()
                child.wait(timeout=5)
                log(base, 'http_finished', target_pk=target, batch_id=frozen['manifest']['batch_id'],
                    http_status=None, response_ok=False, outcome='timeout_or_stop')
                return False
            time.sleep(0.1)
        result_path = base / 'http-result.json'
        return child.returncode == 0 and result_path.is_file() and read_json(result_path)['response_ok'] is True
    return post


def worker(base, *, power=None):
    configure(base)
    plan, config = check_plan(base)
    import cli
    import runner
    import sync
    import sender
    from observe_line_db import Observer, LockSignal
    import keep_power_lock
    new_json(base / 'worker-started.json', dict(pid=os.getpid(), at_utc=stamp()))
    stopped = False
    def stop(*_):
        nonlocal stopped
        stopped = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    keep_power_lock.SECONDS = HARD_SECONDS
    observer = Observer(config['line_db'], int(plan['chat_pk']), TEXT, plan['initial_baseline'])
    lock = LockSignal()
    deadline = time.monotonic() + OBSERVE_SECONDS
    reason = 'deadline_no_target'
    last_absent = None
    try:
        with runner.worker_lock(), (power or keep_power_lock.stay_awake)():
            first, rows = target_snapshot(observer, int(plan['chat_pk']), TEXT, lock)
            log(base, **first)
            if rows:
                raise RuntimeError('target_present_before_trial')
            last_absent = first
            cli.enable()
            while not stopped and not (base / 'stop-request.json').exists() and time.monotonic() < deadline:
                check_plan(base)
                snapshot, rows = target_snapshot(observer, int(plan['chat_pk']), TEXT, lock)
                log(base, **snapshot)
                if len(rows) > 1:
                    raise RuntimeError('duplicate_trial_text')
                if not rows:
                    last_absent = snapshot
                    time.sleep(min(5, max(0, deadline - time.monotonic())))
                    continue
                target_pk = str(rows[0][0])
                new_json(base / 'target-private.json', dict(target_pk=target_pk, source=plan['source'],
                    chat_pk=plan['chat_pk'], last_absent=last_absent,
                    first_present=snapshot))
                log(base, 'target_adopted', target_pk=target_pk, source=plan['source'], chat_pk=plan['chat_pk'])
                if stopped or time.monotonic() >= deadline:
                    reason = 'target_visible_after_send_deadline'
                    break
                sync.collect()
                freeze_target(base, read_json(base / 'config/config.json'), plan, target_pk)
                log(base, 'target_frozen', target_pk=target_pk)
                report = sender.send_once(one_post(base, plan, deadline, lambda: stopped))
                accepted = report.get('sent') is True
                log(base, 'application_acceptance', target_pk=target_pk,
                    batch_id=read_json(base / 'payload-private.json')['manifest']['batch_id'],
                    accepted=accepted, gas_ledger='unverified',
                    deployment_contract_verified=plan.get('deployment_response_contract_verified') is True,
                    gmail_delivery='unverified')
                reason = 'application_accepted_mail_unverified' if accepted else 'held_delivery_unconfirmed'
                break
            if stopped or (base / 'stop-request.json').exists():
                reason = 'stop_requested'
    except Exception as error:
        reason = 'held_error'
        code = str(error) if isinstance(error, RuntimeError) and re.fullmatch(r'[a-z_]{1,80}', str(error)) else 'operation_failed'
        log(base, 'worker_error', error=type(error).__name__, reason=code)
    finally:
        observer.close()
        lock.close()
        cli.disable()
        log(base, 'worker_stopped', reason=reason, gmail_delivery='unverified')


def end_group(child):
    # A dead worker leader may still have an HTTP child. Kill its process group,
    # not just the leader, even if poll() already reported the leader's exit.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        child.wait(timeout=5)
        return
    expires = time.monotonic() + KILL_GRACE_SECONDS
    while time.monotonic() < expires:
        child.poll()
        try:
            os.killpg(child.pid, 0)
        except ProcessLookupError:
            child.wait(timeout=5)
            return
        time.sleep(0.1)
    try:
        os.killpg(child.pid, KILL_SIGNAL)
    except ProcessLookupError:
        pass
    child.wait(timeout=5)


def supervise(base):
    if os.getuid() != 501:
        raise RuntimeError('mobile_required')
    configure(base)
    check_plan(base)
    new_json(base / 'supervisor-started.json', dict(pid=os.getpid(), at_utc=stamp()))
    child = None
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    reason = 'launch_failed'
    try:
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), 'worker', '--base', str(base)],
                start_new_session=True, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        reason = 'worker_finished'
        deadline = time.monotonic() + HARD_SECONDS
        while child.poll() is None:
            if stopping or (base / 'stop-request.json').exists() or time.monotonic() >= deadline:
                reason = 'stop_requested' if stopping or (base / 'stop-request.json').exists() else 'hard_deadline'
                end_group(child)
                break
            time.sleep(0.2)
        if reason == 'worker_finished' and child.returncode not in (None, 0):
            reason = 'worker_failed'
    finally:
        if child is not None:
            end_group(child)
        config = read_json(base / 'config/config.json')
        config['enabled'] = False
        import cli
        cli.save_config(config)
        new_json(base / 'finished.json', dict(at_utc=stamp(), reason=reason,
                    worker_exit=child.returncode if child else None, state='stopped'))
        log(base, 'supervisor_stopped', reason=reason)


def post_unlock_once(base):
    configure(base)
    plan, config = check_plan(base)
    finished = read_json(base / 'finished.json')
    if finished.get('state') != 'stopped' or read_json(base / 'config/config.json')['enabled'] is not False:
        raise RuntimeError('confirm_trial_stopped_first')
    if (base / 'target-private.json').exists():
        raise RuntimeError('target_was_already_observed_use_existing_evidence')
    # Claim before the read so an interrupted attempt cannot be silently repeated.
    new_json(base / 'post-unlock-claimed.json', dict(at_utc=stamp()))
    from observe_line_db import Observer, LockSignal
    observer = Observer(config['line_db'], int(plan['chat_pk']), TEXT, plan['initial_baseline'])
    lock = LockSignal()
    try:
        result, _ = target_snapshot(observer, int(plan['chat_pk']), TEXT, lock)
        log(base, 'post_unlock_read', observation=result, collection=False, external_send=False)
        return result
    finally:
        observer.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'supervise', 'worker', 'http-child',
                                          'status', 'stop', 'post-unlock-once'))
    parser.add_argument('--base', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    parser.add_argument('--runtime', type=Path, default=Path('/var/jb/usr/libexec/line-auto-sync'))
    args = parser.parse_args()
    base = args.base.absolute()
    if args.action == 'prepare':
        if args.baseline is None:
            parser.error('--baseline required')
        prepare(args.baseline, base, args.runtime)
        print('LOCK_N1_PREPARED_NOT_STARTED')
    elif args.action == 'supervise':
        supervise(base)
    elif args.action == 'worker':
        worker(base)
    elif args.action == 'http-child':
        http_child(base)
    elif args.action == 'stop':
        if not (base / 'stop-request.json').exists():
            new_json(base / 'stop-request.json', dict(at_utc=stamp()))
        print('STOP_REQUESTED_NO_RESTART')
    elif args.action == 'post-unlock-once':
        print(json.dumps(post_unlock_once(base)))
    else:
        print(json.dumps(dict(start_attempted=(base / 'supervisor-started.json').exists(),
            worker_started=(base / 'worker-started.json').exists(),
            network_reserved=(base / 'http-lease.json').exists(),
            network_dispatch_reserved=(base / 'network-started.json').exists(),
            finished=read_json(base / 'finished.json') if (base / 'finished.json').is_file() else None,
            enabled=read_json(base / 'config/config.json')['enabled'])))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(event='trial_command_failed', error=type(error).__name__)))
        raise SystemExit(1) from None
