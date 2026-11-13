"""Synthetic-only check of the isolated GAS HTTPS entry point."""
import argparse
import getpass
import json
from pathlib import Path
import re
import secrets
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'normal-only-rc1'))
from sender import SafeRedirect, exchange_legacy
from transport import canonical, digest, records_bytes

SOURCE = 'MVP_DEMO'
PK = '20261111002'


def fixture():
    record = {'id': SOURCE + ':' + PK, 'time': '2026-11-11T09:01:00+09:00',
              'chat': 'MVP HTTPS合成試験', 'sender': '架空の試験担当者',
              'text': '【合成試験】HTTPS配送を確認するための架空のメッセージです。',
              'refs': {'message_pk': PK, 'chat_pk': '2', 'sender_pk': '1'}}
    payload = records_bytes([record])
    sha = digest(payload)
    batch = digest((SOURCE + '\n' + sha).encode())
    header = {'v': 2, 'kind': 'line_batch', 'source': SOURCE, 'part': 1, 'parts': 1,
              'count': 1, 'sha256': sha, 'batch_id': batch}
    return batch, canonical(header) + '\n' + payload.decode()


def validate_endpoint(endpoint):
    url = urlsplit(endpoint)
    if (url.scheme != 'https' or url.hostname != 'script.google.com'
            or url.port not in (None, 443) or url.username or url.password
            or url.query or url.fragment
            or not re.fullmatch(r'/macros/s/[A-Za-z0-9_-]+/exec', url.path)):
        raise ValueError('expected_google_web_app_exec_url')


class TraceRedirect(SafeRedirect):
    def __init__(self):
        super().__init__()
        self.steps = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        result = super().redirect_request(req, fp, code, msg, headers, newurl)
        if result is not None:
            self.steps.append({'status': code, 'host': urlsplit(result.full_url).hostname,
                               'method': result.get_method()})
        return result


def check(endpoint, token, exchange=exchange_legacy):
    validate_endpoint(endpoint)
    if len(token) < 32:
        raise ValueError('isolated_token_missing')
    batch, body = fixture()
    results = []
    for name, key, expected in (('wrong_token', secrets.token_hex(32), b'ERROR'),
                                ('accepted', token, b'OK'), ('replay', token, b'OK')):
        trace = TraceRedirect()
        answer = exchange(endpoint, key, body, PK, trace)
        matched = len(answer) <= 128 and answer.strip() == expected
        results.append({'check': name, 'expected_response': expected.decode(),
                        'matched': matched, 'redirects': trace.steps})
        if not matched:
            break
    return {'batch_id': batch, 'checks': results,
            'passed': len(results) == 3 and all(item['matched'] for item in results)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='send one new synthetic batch and replay it')
    parser.add_argument('--credentials-file', type=Path, help='private JSON with endpoint and token')
    args = parser.parse_args()
    batch, _ = fixture()
    if not args.live:
        print(json.dumps({'synthetic': True, 'batch_id': batch, 'source': SOURCE,
                          'gmail_subject': '[LINE-TASK-BATCH] ' + SOURCE + ' ' + batch}))
        return 0
    if args.credentials_file:
        conf = json.loads(args.credentials_file.read_text(encoding='utf-8-sig'))
        endpoint, token = conf['endpoint'], conf['token']
    else:
        if not sys.stdin.isatty():
            raise ValueError('use_interactive_terminal_or_private_credentials_file')
        endpoint = getpass.getpass('Isolated deployment HTTPS URL (hidden): ').strip()
        token = getpass.getpass('Isolated GAS token (hidden): ').strip()
    result = check(endpoint, token)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['passed'] else 1


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except Exception as exc:
        print('ERROR: HTTPS check failed (' + type(exc).__name__ + ')', file=sys.stderr)
        raise SystemExit(1)
