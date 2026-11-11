"""Preview and explicitly submit exactly one queued message using installed RC5."""
import argparse
import json
from pathlib import Path
import sys

if __name__ == '__main__':
    sys.path.insert(0, '/var/jb/usr/libexec/line-auto-sync')

import legacy_names
import normal_hold
import presentation
import sender
import transport
from delivery_state import DeliveryState
from runner import worker_lock
from runtime_paths import STATE_DIR, require_enabled


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
            records = presentation.decode_named(body.partition('\n')[2])
            if len(records) != 1 or not records[0]['id'].startswith(config['source_id'] + ':'):
                raise RuntimeError('unexpected_payload')
            return records[0], fingerprint(config['transport']['endpoint'], body)
    finally:
        queue.close()
        legacy_names.close()


def review(approved=None, post=sender.post_legacy):
    with worker_lock():
        record, expected = prepare()
        if approved is None:
            return {'approval_sha256': expected, 'record': record}
        if approved != expected:
            raise RuntimeError('approval_does_not_match')

        def guarded_post(endpoint, token, body, maxid):
            if fingerprint(endpoint, body) != approved:
                return False
            return post(endpoint, token, body, maxid)

        return sender.send_once(guarded_post)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-approved', metavar='SHA256',
                        help='send only the reviewed record to the reviewed destination')
    args = parser.parse_args()
    try:
        result = review(args.send_approved)
        print(json.dumps(result, ensure_ascii=False))
        if args.send_approved is not None and not result.get('sent'):
            sys.exit(1)
    except Exception as exc:
        print('ERROR: single_message_check_failed (' + type(exc).__name__ + ')', file=sys.stderr)
        sys.exit(1)
