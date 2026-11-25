"""Run the RC6 Keep Memo check in a new, offline profile."""
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import sqlite3
import sys
import time
from unittest.mock import patch
from urllib.parse import urlsplit

BASE_MARKER = 'LAS-KEEP-6D19-BASE'
CHECK_MARKER = 'LAS-KEEP-6D19-CHECK'
SEND_MARKER = 'LAS-KEEP-6D19-SEND 2026年11月20日17時までに試験返信を確認する'
LOCK_MARKER = 'LAS-KEEP-6D19-LOCK 2026年11月21日17時までに試験資料を確認する'
AUTO_MARKER = 'LAS-KEEP-6D19-AUTO 2026年11月22日17時までに自動取込の試験結果を確認する'
SOURCE = 'RC6_SCOPE_CHECK'
ENDPOINT = 'https://example.invalid/'


def readonly(path):
    return closing(sqlite3.connect(path.resolve(strict=True).as_uri() + '?mode=ro', uri=True))


def setup(profile, cli, sync):
    paths = {p.read_text(encoding='utf-8').strip()
             for p in Path.home().glob('las-rc5-check.*/state/dbpath') if not p.is_symlink()}
    if len(paths) != 1:
        raise RuntimeError('expected_one_diagnostic_database')
    database = Path(next(iter(paths)))
    with readonly(database) as conn:
        matches = conn.execute('SELECT DISTINCT ZCHAT FROM ZMESSAGE WHERE ZTEXT=?',
                               (BASE_MARKER,)).fetchall()
    if len(matches) != 1 or type(matches[0][0]) is not int or matches[0][0] <= 0:
        raise RuntimeError('keep_marker_missing_or_ambiguous')
    profile.mkdir(mode=0o700)
    cli.initialize()
    with patch('builtins.input', side_effect=[ENDPOINT, SOURCE, str(database)]), \
            patch.object(getpass, 'getpass', return_value='offline-check-only'):
        cli.configure()
    cli.restrict_chats([str(matches[0][0])])
    cli.enable()
    try:
        if sync.collect()['pending'] != 0:
            raise RuntimeError('expected_empty_baseline')
    finally:
        cli.disable()
    print('KEEP_BASELINE_READY')


def check(cli, sync, runtime):
    config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
    chats = runtime.allowed_chat_pks(config)
    if (config.get('source_id') != SOURCE or config.get('enabled') is not False
            or config['transport']['endpoint'] != ENDPOINT or len(chats or ()) != 1):
        raise RuntimeError('expected_disabled_offline_profile')
    cli.enable()
    try:
        collected = sync.collect()
        with readonly(runtime.STATE_DIR / 'normal-delivery.sqlite') as conn:
            rows = [json.loads(row[0]) for row in conn.execute('SELECT raw FROM messages')]
        hits = sum(row[4] == CHECK_MARKER for row in rows)
        allowed = all(row[2] == str(chats[0]) for row in rows)
        print(json.dumps({'pending': collected['pending'], 'keep_marker': hits,
                          'all_rows_in_scope': allowed, 'external_send': False}))
        if hits != 1 or not allowed:
            raise RuntimeError('keep_scope_not_confirmed')
    finally:
        cli.disable()


def prepare_trial(profile, cli, sync, runtime):
    offline = json.loads((Path.home() / 'las-rc6-keep-check/config/config.json').read_text(encoding='utf-8'))
    paths = Path.home().glob('las-pilot.*/config/config.json')
    pilots = [json.loads(path.read_text(encoding='utf-8')) for path in paths if not path.is_symlink()]
    pilots = [p for p in pilots if p.get('source_id') == 'MVP_IPAD_PILOT'
              and p.get('enabled') is False and p.get('line_db') == offline.get('line_db')]
    chats = runtime.allowed_chat_pks(offline)
    if len(pilots) != 1 or offline.get('source_id') != SOURCE or len(chats or ()) != 1:
        raise RuntimeError('expected_isolated_pilot_and_verified_keep')
    transport = pilots[0]['transport']
    url = urlsplit(transport['endpoint'])
    if (url.scheme != 'https' or url.netloc != 'script.google.com'
            or not url.path.startswith('/macros/s/') or not url.path.endswith('/exec')
            or url.query or url.fragment):
        raise RuntimeError('expected_isolated_gas_destination')
    profile.mkdir(mode=0o700)
    cli.initialize()
    with patch('builtins.input', side_effect=[transport['endpoint'], 'MVP_IPAD_LIVE', offline['line_db']]), \
            patch.object(getpass, 'getpass', return_value=transport['token']):
        cli.configure()
    cli.restrict_chats([str(chats[0])])
    cli.enable()
    try:
        if sync.collect()['pending'] != 0:
            raise RuntimeError('expected_empty_trial_baseline')
    finally:
        cli.disable()
    print('KEEP_TRIAL_READY')


def send_trial(cli, runtime, runner, sender, presentation, post=None, *, marker=SEND_MARKER):
    """Run one scheduled, four-minute window; only the exact trial text can leave."""
    config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
    endpoint = config['transport']['endpoint']
    chats = runtime.allowed_chat_pks(config)
    if config.get('source_id') != 'MVP_IPAD_LIVE' or len(chats or ()) != 1:
        raise RuntimeError('expected_scoped_trial')
    post = sender.post_legacy if post is None else post
    deadline = time.monotonic() + 240
    result = None
    batch_id = None

    def guarded_post(destination, token, body, maxid):
        nonlocal batch_id
        header, _, payload = body.partition('\n')
        records = presentation.decode_named(payload)
        if (destination != endpoint or len(records) != 1 or records[0]['text'] != marker
                or records[0]['refs']['chat_pk'] != str(chats[0])):
            return False
        batch_id = json.loads(header)['batch_id']
        return post(destination, token, body, maxid)

    def send_step():
        nonlocal result
        with readonly(runtime.STATE_DIR / 'normal-delivery.sqlite') as conn:
            rows = conn.execute('SELECT raw FROM messages WHERE acked=0 LIMIT 2').fetchall()
        if not rows:
            return {}
        if len(rows) != 1 or json.loads(rows[0][0])[4] != marker:
            result = 'KEEP_TRIAL_HELD'
            runner.request_stop()
            return {'error': True}
        report = sender.send_once(guarded_post)
        if report.get('sent'):
            result = 'KEEP_TRIAL_SENT'
            runner.request_stop()
        return report

    def wait_tick(seconds):
        if time.monotonic() >= deadline:
            runner.request_stop()
        else:
            time.sleep(seconds)

    cli.enable()
    try:
        runner.start(cli.doctor, cli.queue_status, send=True, resume_pending=True,
                     send_step=send_step, sleep=wait_tick)
    finally:
        cli.disable()
    print(json.dumps({'result': result or 'KEEP_TRIAL_STOPPED', 'batch_id': batch_id if result == 'KEEP_TRIAL_SENT' else None}))


def require_held_trial(home):
    old = home / 'las-rc6-keep-trial'
    config = json.loads((old / 'config/config.json').read_text(encoding='utf-8'))
    if config.get('enabled') is not False or config.get('source_id') != 'MVP_IPAD_LIVE':
        raise RuntimeError('previous_trial_must_be_disabled')
    with readonly(old / 'state/normal-delivery.sqlite') as conn:
        pending, sent = conn.execute(
            'SELECT COUNT(*), COALESCE(SUM(acked), 0) FROM messages').fetchone()
        if not pending or sent or conn.execute('SELECT 1 FROM delivery').fetchone():
            raise RuntimeError('previous_delivery_requires_review')


def deliver_held_auto(runtime, runner, sender, post=None):
    """Send the already approved text from the stopped trial without consuming its queue."""
    import legacy_names
    import transport
    from delivery_state import DeliveryState, normalize_row

    with runner.worker_lock(), sender.sender_lock(runtime.STATE_DIR):
        config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
        target = config['transport']
        url = urlsplit(target['endpoint'])
        chats = runtime.allowed_chat_pks(config)
        if (config.get('enabled') is not False or config.get('source_id') != 'MVP_IPAD_LIVE'
                or len(chats or ()) != 1 or url.scheme != 'https' or url.netloc != 'script.google.com'
                or not url.path.startswith('/macros/s/') or not url.path.endswith('/exec')
                or url.query or url.fragment):
            raise RuntimeError('expected_disabled_scoped_auto_trial')
        path = runtime.STATE_DIR / 'normal-delivery.sqlite'
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('existing_queue_required')
        queue = DeliveryState(runtime.STATE_DIR)
        try:
            with queue.transaction():
                queue.bind(config)
                if queue.status()['frozen'] or queue.get('success_at') is not None:
                    raise RuntimeError('prior_delivery_requires_review')
                matches = [row for row in queue.pending_rows() if row[4] == AUTO_MARKER]
                if len(matches) != 1 or str(matches[0][2]) != str(chats[0]):
                    raise RuntimeError('expected_one_exact_approved_marker')
                pk, timestamp, chat, author, text = normalize_row(matches[0])
                row = queue.db.execute('SELECT raw,hash FROM messages WHERE pk=? AND acked=0',
                                       (pk,)).fetchone()
                if row is None or transport.digest(row['raw'].encode()) != row['hash']:
                    raise RuntimeError('invalid_pending_row')
                record = legacy_names.enrich_legacy(dict(id=pk, time_ms=timestamp, chat=chat,
                                                         sender=author, text=text))
                body = legacy_names.finalize_body(transport.canonical(record) + '\n')
                manifest = transport.strict_json(body.partition('\n')[0])
        finally:
            queue.close()
            legacy_names.close()
        if manifest['count'] != 1 or manifest['source'] != config['source_id']:
            raise RuntimeError('invalid_single_message_batch')
        post = sender.post_legacy if post is None else post
        if not post(target['endpoint'], target['token'], body, int(pk)):
            raise RuntimeError('delivery_unconfirmed_check_receiver_before_retry')
        print(json.dumps({'result': 'KEEP_TRIAL_SENT', 'batch_id': manifest['batch_id'],
                          'queue_preserved': True}))


def main():
    actions = ('setup', 'check', 'prepare-trial', 'send-approved-trial', 'retry-approved-trial',
               'prepare-lock', 'send-approved-lock', 'lock-status', 'lock-stop',
               'prepare-auto', 'send-approved-auto', 'auto-status', 'auto-stop', 'deliver-held-auto')
    if len(sys.argv) != 2 or sys.argv[1] not in actions:
        raise ValueError('expected_action')
    action = sys.argv[1]
    home = Path.home()
    if action == 'retry-approved-trial':
        require_held_trial(home)
        profile = home / 'las-rc6-keep-trial-retry'
    elif action in ('prepare-lock', 'send-approved-lock', 'lock-status', 'lock-stop'):
        profile = home / 'las-rc6-keep-lock'
    elif action in ('prepare-auto', 'send-approved-auto', 'auto-status', 'auto-stop', 'deliver-held-auto'):
        profile = home / 'las-rc6-keep-auto'
    else:
        profile = home / ('las-rc6-keep-check' if action in ('setup', 'check') else 'las-rc6-keep-trial')
    if profile.is_symlink():
        raise RuntimeError('symlinked_check_profile')
    for key in ('LINE_AUTO_SYNC_STATE_DIR', 'LINE_AUTO_SYNC_CONFIG_DIR'):
        os.environ.pop(key, None)
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(profile)
    sys.path.insert(0, '/var/jb/usr/libexec/line-auto-sync')
    import cli
    import runtime_paths
    import sync
    if action == 'setup':
        setup(profile, cli, sync)
    elif action == 'check':
        check(cli, sync, runtime_paths)
    elif action in ('prepare-trial', 'prepare-lock', 'prepare-auto'):
        prepare_trial(profile, cli, sync, runtime_paths)
    elif action in ('lock-status', 'auto-status'):
        cli.recovery_check()
    elif action in ('lock-stop', 'auto-stop'):
        cli.disable()
    else:
        import presentation
        import runner
        import sender
        if action == 'deliver-held-auto':
            deliver_held_auto(runtime_paths, runner, sender)
            return
        if action == 'retry-approved-trial':
            prepare_trial(profile, cli, sync, runtime_paths)
        marker = {'send-approved-lock': LOCK_MARKER,
                  'send-approved-auto': AUTO_MARKER}.get(action, SEND_MARKER)
        send_trial(cli, runtime_paths, runner, sender, presentation, marker=marker)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('KEEP_CHECK_ERROR:', type(exc).__name__, file=sys.stderr)
        sys.exit(1)
