"""Exercise retry of one approved Keep message using an isolated RC6 queue."""
import json
import os
from pathlib import Path
import sys

from rc6_keep_check import prepare_trial, readonly

APPROVED_TEXT = '11月24日18時までに明日の持ち物を確認する'
BASE = Path('/var/mobile/las-keep-retry-one')


def attempt(cli, runtime, runner, sender, sync, decode):
    config = json.loads(runtime.CONFIG_FILE.read_text(encoding='utf-8'))
    baseline = json.loads((Path.home() / 'las-rc6-keep-check/config/config.json').read_text(encoding='utf-8'))
    chats = runtime.allowed_chat_pks(config)
    if (config['source_id'] != 'MVP_IPAD_LIVE' or len(chats or ()) != 1
            or list(chats) != baseline['normal_line']['allowed_chat_pks']
            or config['line_db'] != baseline['line_db']):
        raise RuntimeError('unexpected_keep_profile')

    batch = None

    def guarded_post(endpoint, token, body, maxid):
        nonlocal batch
        manifest, _, payload = body.partition('\n')
        records = decode(payload)
        if (endpoint != config['transport']['endpoint'] or len(records) != 1
                or records[0]['text'] != APPROVED_TEXT
                or records[0]['refs']['chat_pk'] != str(chats[0])):
            return False
        batch = json.loads(manifest)['batch_id']
        return sender.post_legacy(endpoint, token, body, maxid)

    with runner.worker_lock():
        cli.enable()
        try:
            sync.collect()
            with readonly(runtime.STATE_DIR / 'normal-delivery.sqlite') as db:
                rows = db.execute('SELECT raw FROM messages WHERE acked=0 LIMIT 2').fetchall()
            if len(rows) != 1 or json.loads(rows[0][0])[4] != APPROVED_TEXT:
                raise RuntimeError('expected_one_approved_pending_message')
            message_pk = json.loads(rows[0][0])[0]
            result = sender.send_once(guarded_post)
            status = cli.queue_status()
            print(json.dumps({
                'pending': status['pending'], 'frozen': status['frozen'],
                'attempts': status['attempts'], 'next_attempt': status['next_attempt'],
                'sent': result.get('sent', False), 'batch_id': batch, 'message_pk': message_pk,
                'reason': result.get('reason') or result.get('error'),
            }))
        finally:
            cli.disable()


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ('prepare', 'attempt', 'status'):
        raise ValueError('expected_prepare_attempt_or_status')
    if BASE.is_symlink():
        raise RuntimeError('unsafe_profile')
    for key in ('LINE_AUTO_SYNC_CONFIG_DIR', 'LINE_AUTO_SYNC_STATE_DIR'):
        os.environ.pop(key, None)
    os.environ['LINE_AUTO_SYNC_BASE_DIR'] = str(BASE)
    sys.path.insert(0, '/var/jb/usr/libexec/line-auto-sync')
    import cli
    import runtime_paths
    import sync
    action = sys.argv[1]
    if action == 'prepare':
        prepare_trial(BASE, cli, sync, runtime_paths)
    elif action == 'status':
        cli.recovery_check()
    else:
        import runner
        import sender
        from presentation import decode_named
        attempt(cli, runtime_paths, runner, sender, sync, decode_named)


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('KEEP_RETRY_ERROR:', str(exc) if type(exc) is RuntimeError and
              str(exc) == 'expected_one_approved_pending_message' else type(exc).__name__)
        sys.exit(1)
