"""Foreground periodic worker. Collection-only unless explicitly started with --send."""
import argparse
import json
import os
import time
import uuid

from runtime_paths import STATE_DIR, require_enabled
from sender import send_once, sender_lock
from sync import collect


def worker_lock():
    if not STATE_DIR.is_dir() or STATE_DIR.is_symlink():
        raise RuntimeError('unsafe_worker_state')
    return sender_lock(STATE_DIR, 'normal-worker.lock')


def read_marker(name):
    if STATE_DIR.is_symlink():
        raise RuntimeError('unsafe_worker_state')
    path = STATE_DIR / name
    if path.is_symlink():
        raise RuntimeError('unsafe_worker_marker')
    return json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}


def write_marker(name, value):
    path = STATE_DIR / name
    if STATE_DIR.is_symlink() or path.is_symlink():
        raise RuntimeError('unsafe_worker_marker')
    temp = STATE_DIR / ('.worker-' + uuid.uuid4().hex)
    fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as out:
            json.dump(value, out)
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def worker_status():
    print(json.dumps({'last_recorded': read_marker('worker-status.json')}))


def request_stop():
    status = read_marker('worker-status.json')
    if status.get('state') != 'running':
        print('No running worker recorded.')
        return
    write_marker('worker-stop.json', {'run_id': status['run_id']})
    print('Stop requested. An in-progress network request may finish.')


def start(doctor, queue_status, *, send=False, resume_pending=False,
          collect_step=collect, send_step=send_once, sleep=time.sleep):
    config = require_enabled()
    interval = config.get('schedule', {}).get('interval_seconds', 120)
    if type(interval) is not int or not 60 <= interval <= 3600:
        raise ValueError('invalid_worker_interval')
    with worker_lock():
        doctor()
        queue = queue_status()
        legacy = any((STATE_DIR / name).exists() or (STATE_DIR / name).is_symlink()
                     for name in ('new.csv', 'lastid', 'sentid', 'initial_scan_id', 'sent_at'))
        if send and not resume_pending and (queue.get('pending') or queue.get('frozen') or legacy):
            raise RuntimeError('pending_queue_requires_explicit_resume')
        state = {'run_id': uuid.uuid4().hex, 'state': 'running',
                 'mode': 'send' if send else 'collect', 'updated_at': int(time.time())}
        write_marker('worker-status.json', state)

        def should_stop():
            return read_marker('worker-stop.json').get('run_id') == state['run_id']

        try:
            while not should_stop():
                try:
                    if require_enabled() != config:
                        break
                except RuntimeError:
                    break
                try:
                    collect_step()
                    if send and not should_stop() and require_enabled() == config:
                        report = send_step()
                        state['last_result'] = 'delivery_unconfirmed' if report.get('error') else 'ok'
                    else:
                        state['last_result'] = 'collected'
                except Exception as exc:
                    state['last_result'] = type(exc).__name__
                state['updated_at'] = int(time.time())
                write_marker('worker-status.json', state)
                print('WORKER:', state['last_result'], flush=True)
                for _ in range(interval):
                    if should_stop():
                        break
                    sleep(1)
        except KeyboardInterrupt:
            pass
        finally:
            state.update(state='stopped', updated_at=int(time.time()))
            write_marker('worker-status.json', state)


def start_cli(args, doctor, queue_status):
    parser = argparse.ArgumentParser(prog='line-auto-sync start')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--resume-pending', action='store_true')
    flags = parser.parse_args(args)
    if flags.resume_pending and not flags.send:
        parser.error('--resume-pending requires --send')
    start(doctor, queue_status, send=flags.send, resume_pending=flags.resume_pending)
