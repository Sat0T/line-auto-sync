"""Legacy token/body/OK protocol with immutable retries; not HMAC v2."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import math
import sys
import time
import urllib.parse
import urllib.request
from delivery_state import DeliveryError, DeliveryState
import legacy_names
import normal_hold
from runtime_paths import STATE_DIR, require_enabled


@contextmanager
def sender_lock(directory):
    """Independent process lock; never hold a SQLite write transaction over HTTP."""
    path = Path(directory) / 'normal-sender.lock'
    if path.is_symlink():
        raise DeliveryError('unsafe_sender_lock')
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(path), flags, 0o600)
    locked = False
    try:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b'0')
            os.lseek(fd, 0, os.SEEK_SET)
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                raise DeliveryError('sender_already_running') from None
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise DeliveryError('sender_already_running') from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == 'nt':
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old, new = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        # Match both the hostname and port. Redirecting to the same name on a
        # different TLS port can expose the endpoint's authentication token.
        try:
            old_origin = (old.scheme, old.hostname, old.port or 443)
            new_origin = (new.scheme, new.hostname, new.port or 443)
        except ValueError:
            raise DeliveryError('unsafe_receiver_redirect') from None
        same_origin = old_origin == new_origin
        gas_response_origin = (
            old_origin == ('https', 'script.google.com', 443) and
            new_origin == ('https', 'script.googleusercontent.com', 443)
        )
        if (new.scheme != 'https' or not (same_origin or gas_response_origin) or
                new.username or new.password):
            raise DeliveryError('unsafe_receiver_redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def post_legacy(endpoint, token, body, maxid):
    data = json.dumps({'token': token, 'maxid': str(maxid), 'body': body}, ensure_ascii=False).encode()
    request = urllib.request.Request(endpoint, data, {'Content-Type': 'application/json'})
    with urllib.request.build_opener(SafeRedirect()).open(request, timeout=30) as response:
        answer = response.read(129)
        return len(answer) <= 128 and answer.strip() == b'OK'


def send_once(post=None, now=None):
    config = require_enabled()
    now = time.time() if now is None else now
    if not math.isfinite(now):
        raise DeliveryError('invalid_clock')
    post = post_legacy if post is None else post
    # Serialize senders, not collectors. The lock is released on process exit.
    with sender_lock(STATE_DIR):
        queue = DeliveryState(STATE_DIR)
        try:
            with queue.transaction():
                queue.bind(config)
                if queue.get('collected') is None:
                    raise DeliveryError('collect_before_send')
                active = queue.db.execute('SELECT * FROM delivery').fetchone()
                if active is None:
                    if now - float(queue.get('success_at', '0')) < 3600:
                        return dict(queue.status(), sent=False, reason='hourly_gate')
                    rows = queue.pending_rows()
                    if not rows:
                        return dict(queue.status(), sent=False, reason='empty')
                    selection = normal_hold.select_prefix(rows, legacy_names.enrich_legacy)
                    if not selection['lines']:
                        raise DeliveryError('no_deliverable_prefix')
                    body = legacy_names.finalize_body(''.join(selection['lines']))
                    last = int(selection['last_id'])
                    ids = [int(row[0]) for row in rows if int(row[0]) <= last]
                    queue.freeze(body, ids)

            # Freeze and verify under a short transaction; commit before HTTP.
            with queue.transaction():
                current_config = require_enabled()
                queue.bind(current_config)
                active = queue.db.execute('SELECT * FROM delivery').fetchone()
                if active is None:
                    return dict(queue.status(), sent=False, reason='already_acknowledged')
                ids = queue.verify_frozen(active, current_config['source_id'])
                if active['next_attempt'] > now:
                    return dict(queue.status(), sent=False, reason='retry_wait')
                frozen = (active['batch_id'], active['body'], active['ids'],
                          active['attempts'], active['next_attempt'])
                endpoint = current_config['transport']['endpoint']
                token = current_config['transport']['token']
                body = active['body']
                maxid = max(ids)

            # No SQLite write transaction is held during the network wait.
            try:
                accepted = post(endpoint, token, body, maxid) is True
            except Exception:
                accepted = False

            with queue.transaction():
                latest_config = require_enabled()
                queue.bind(latest_config)
                latest = queue.db.execute('SELECT * FROM delivery').fetchone()
                if latest is None or (
                    latest['batch_id'], latest['body'], latest['ids'],
                    latest['attempts'], latest['next_attempt']
                ) != frozen:
                    raise DeliveryError('delivery_changed_during_http')
                queue.verify_frozen(latest, latest_config['source_id'])
                if accepted:
                    queue.acknowledge(latest, now)
                else:
                    attempt = latest['attempts'] + 1
                    delay = min(3600, 60 * 2 ** min(attempt - 1, 6))
                    queue.db.execute(
                        'UPDATE delivery SET attempts=?,next_attempt=? WHERE singleton=1',
                        (attempt, now + delay))
            return dict(queue.status(), sent=accepted,
                        error=None if accepted else 'delivery_unconfirmed')
        finally:
            queue.close()
            legacy_names.close()


if __name__ == '__main__':
    try:
        result = send_once()
        print('DELIVERY', result)
        raise SystemExit(1 if result.get('error') else 0)
    except Exception:
        print('ERROR: delivery_failed', file=sys.stderr)
        raise SystemExit(1)
