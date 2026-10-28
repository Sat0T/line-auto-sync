"""Legacy token/body/OK protocol with immutable retries; not HMAC v2."""
import json
import math
import sys
import time
import urllib.parse
import urllib.request
from delivery_state import DeliveryError, DeliveryState
import legacy_names
import normal_hold
from runtime_paths import STATE_DIR, require_enabled


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
    queue = DeliveryState(STATE_DIR)
    try:
        # Commit exact content BEFORE HTTP. New messages cannot change retries.
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
        # Serialize attempts; acceptance-before-crash still needs receiver dedupe.
        failed = False
        with queue.transaction():
            current_config = require_enabled()
            queue.bind(current_config)
            active = queue.db.execute('SELECT * FROM delivery').fetchone()
            if active is None:
                return dict(queue.status(), sent=False, reason='already_acknowledged')
            # Local corruption must STOP, not merely schedule another HTTP retry.
            ids = queue.verify_frozen(active, current_config['source_id'])
            if active['next_attempt'] > now:
                return dict(queue.status(), sent=False, reason='retry_wait')
            try:
                if post(current_config['transport']['endpoint'],
                        current_config['transport']['token'],
                        active['body'], max(ids)) is not True:
                    raise DeliveryError('receiver_ack_not_ok')
            except Exception:
                attempt = active['attempts'] + 1
                delay = min(3600, 60 * 2 ** min(attempt - 1, 6))
                queue.db.execute('UPDATE delivery SET attempts=?,next_attempt=? WHERE singleton=1', (attempt, now + delay))
                failed = True
            else:
                queue.acknowledge(active, now)
        return dict(queue.status(), sent=not failed, error='delivery_unconfirmed' if failed else None)
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
