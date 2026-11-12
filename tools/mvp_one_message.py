"""Preview and explicitly submit exactly one queued message using installed RC5."""
import argparse
import hmac
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

if __name__ == '__main__':
    sys.path.insert(0, '/var/jb/usr/libexec/line-auto-sync')

import legacy_names
import normal_hold
import presentation
import sender
import transport
from delivery_state import DeliveryState, normalize_row
from runner import worker_lock
from runtime_paths import CONFIG_FILE, STATE_DIR, require_enabled


def fingerprint(endpoint, body):
    return transport.digest(transport.canonical([endpoint, body]).encode('utf-8'))


def prepare():
    config = require_enabled()
    path = STATE_DIR / 'normal-delivery.sqlite'
    if path.is_symlink() or not path.is_file():
        raise RuntimeError('existing_queue_required')
    queue = DeliveryState(STATE_DIR)
    try:
        with queue.transaction():
            if queue.status()['pending'] != 1 or queue.get('collected') is None:
                raise RuntimeError('exactly_one_pending_message_required')
            queue.bind(config)
            active = queue.db.execute('SELECT * FROM delivery').fetchone()
            if active is not None:
                queue.verify_frozen(active, config['source_id'])
                body = active['body']
            else:
                selection = normal_hold.select_prefix(queue.pending_rows(), legacy_names.enrich_legacy)
                if not selection['lines']:
                    raise RuntimeError('message_not_deliverable')
                body = legacy_names.finalize_body(''.join(selection['lines']))
            header, _, payload = body.partition('\n')
            records = presentation.decode_named(payload)
            if len(records) != 1 or not records[0]['id'].startswith(config['source_id'] + ':'):
                raise RuntimeError('unexpected_payload')
            return {'approval_sha256': fingerprint(config['transport']['endpoint'], body),
                    'batch_id': transport.strict_json(header)['batch_id'], 'record': records[0]}
    finally:
        queue.close()
        legacy_names.close()


def review(approved=None, post=sender.post_legacy):
    with worker_lock():
        preview = prepare()
        if approved is None:
            return preview
        if approved != preview['approval_sha256']:
            raise RuntimeError('approval_does_not_match')

        def guarded_post(endpoint, token, body, maxid):
            if fingerprint(endpoint, body) != approved:
                return False
            return post(endpoint, token, body, maxid)

        return sender.send_once(guarded_post)


def review_pilot(message_pk=None, approved=None, post=sender.post_legacy):
    """Use a separate batch for a selected row; leave the main queue pending."""
    with worker_lock(), sender.sender_lock(STATE_DIR):
        if CONFIG_FILE.is_symlink():
            raise RuntimeError('unsafe_config')
        config = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
        endpoint = config['transport']['endpoint']
        url = urlsplit(endpoint)
        if (config.get('enabled') is not False or config.get('source_id') != 'MVP_IPAD_PILOT'
                or url.scheme != 'https' or url.netloc != 'script.google.com'
                or url.query or url.fragment or not url.path.startswith('/macros/s/')
                or not url.path.endswith('/exec')):
            raise RuntimeError('disabled_isolated_pilot_required')
        path = STATE_DIR / 'normal-delivery.sqlite'
        if path.is_symlink() or not path.is_file():
            raise RuntimeError('existing_queue_required')
        queue = DeliveryState(STATE_DIR)
        try:
            with queue.transaction():
                if queue.status()['frozen']:
                    raise RuntimeError('frozen_batch_requires_review')
                queue.bind(config)
                if message_pk is None:
                    print('端末内の候補。本文を共有する必要はありません。')
                    for row in queue.pending_rows():
                        print(json.dumps({'message_pk': row[0], 'text': row[4][:240]}, ensure_ascii=False))
                    message_pk = int(input('試験対象のmessage_pkを1つ入力: '))
                entry = queue.db.execute('SELECT raw,hash FROM messages WHERE pk=? AND acked=0',
                                         (message_pk,)).fetchone()
                if entry is None or transport.digest(entry['raw'].encode()) != entry['hash']:
                    raise RuntimeError('missing_or_corrupt_message')
                pk, timestamp, chat, author, text = normalize_row(json.loads(entry['raw']))
                record = legacy_names.enrich_legacy(dict(id=pk, time_ms=timestamp, chat=chat,
                                                         sender=author, text=text))
                body = legacy_names.finalize_body(transport.canonical(record) + '\n')
                manifest = transport.strict_json(body.partition('\n')[0])
        finally:
            queue.close()
            legacy_names.close()
        expected = fingerprint(endpoint, body)
        if approved is None:
            return {'approval_sha256': expected, 'batch_id': manifest['batch_id'],
                    'source': manifest['source'], 'destination': endpoint, 'record': record}
        if not hmac.compare_digest(approved, expected):
            raise RuntimeError('approval_does_not_match')
        if not post(endpoint, config['transport']['token'], body, int(pk)):
            raise RuntimeError('delivery_unconfirmed')
        return {'sent': True, 'batch_id': manifest['batch_id'], 'queue_preserved': True}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--choose', action='store_true', help='review a row locally from the pilot queue')
    selection.add_argument('--message-pk', type=int, help='select exactly this pending pilot row')
    parser.add_argument('--send-approved', metavar='SHA256',
                        help='send only the reviewed record to the reviewed destination')
    args = parser.parse_args()
    if args.choose and args.send_approved is not None:
        parser.error('use --message-pk with --send-approved')
    try:
        result = (review_pilot(args.message_pk, args.send_approved)
                  if args.choose or args.message_pk is not None else review(args.send_approved))
        print(json.dumps(result, ensure_ascii=False))
        if args.send_approved is not None and not result.get('sent'):
            sys.exit(1)
    except Exception as exc:
        print('ERROR: single_message_check_failed (' + type(exc).__name__ + ')', file=sys.stderr)
        sys.exit(1)
