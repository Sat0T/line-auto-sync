"""Offline verifier for one private LINE/GAS/Gmail evidence bundle. Never sends."""
import argparse
import base64
from datetime import datetime, timedelta, timezone
from email.utils import getaddresses
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

APP = Path(__file__).resolve().parents[1] / 'normal-only-rc1'
SPEC = importlib.util.spec_from_file_location('_locked_presentation', APP / 'presentation.py')
PRESENTATION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PRESENTATION)
TRANSPORT = PRESENTATION.t
JST = timezone(timedelta(hours=9))


class EvidenceError(ValueError):
    """Only stable, non-sensitive error codes leave the verifier."""


def require(condition, code):
    if not condition:
        raise EvidenceError(code)


def instant(value):
    require(isinstance(value, str), 'invalid_time')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        raise EvidenceError('invalid_time') from None
    require(parsed.tzinfo is not None, 'timezone_required')
    return parsed.astimezone(JST)


def uncertainty(value):
    require(type(value) in (int, float) and math.isfinite(value) and 0 <= value <= 300,
            'timing_uncertainty_required')
    return timedelta(seconds=value)


def interval(event, begin='read_started_utc', end='read_finished_utc'):
    start, finish = instant(event[begin]), instant(event[end])
    require(start <= finish, 'reversed_interval')
    return start, finish


def within(bounds, window, error):
    return bounds[0] - error >= window[0] and bounds[1] + error <= window[1]


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def decode_body(body):
    require(isinstance(body, str) and len(body.encode('utf-8')) <= 90000,
            'invalid_batch_body')
    body = body.replace('\r\n', '\n')
    header, separator, payload = body.partition('\n')
    require(separator and payload, 'missing_batch_payload')
    manifest = TRANSPORT.strict_json(header)
    require(isinstance(manifest, dict) and set(manifest) == {
        'v', 'kind', 'source', 'batch_id', 'part', 'parts', 'count', 'sha256'},
        'invalid_manifest')
    records = PRESENTATION.decode_named(payload)
    source = records[0]['id'].rsplit(':', 1)[0]
    canonical_hash = TRANSPORT.digest(TRANSPORT.records_bytes(records))
    require(manifest['v'] == 2 and manifest['kind'] == 'line_batch'
            and manifest['source'] == source and type(manifest['part']) is int
            and manifest['part'] == 1 and type(manifest['parts']) is int
            and manifest['parts'] == 1 and type(manifest['count']) is int
            and manifest['count'] == len(records) and manifest['sha256'] == canonical_hash
            and manifest['batch_id'] == digest(source + '\n' + canonical_hash),
            'manifest_integrity_mismatch')
    return manifest, records


def ledger_row(rows, ident, code):
    decoded = []
    for row in rows:
        if 'data_json' in row:
            value = TRANSPORT.strict_json(row['data_json'])
            require(row.get('id') == value.get('id'), 'ledger_wrapper_mismatch')
        else:
            value = row
        if value.get('id') == ident:
            decoded.append(value)
    require(len(decoded) == 1, code)
    return decoded[0]


def plain_parts(part, depth=0):
    require(depth <= 10, 'mime_too_deep')
    if part.get('mimeType') == 'text/plain' and not part.get('filename'):
        raw = part.get('body', {}).get('data')
        if isinstance(raw, str):
            try:
                decoded = base64.b64decode(raw + '=' * (-len(raw) % 4), altchars=b'-_', validate=True)
            except (ValueError, TypeError):
                raise EvidenceError('invalid_gmail_encoding') from None
        elif isinstance(raw, list) and all(type(x) is int and -128 <= x <= 255 for x in raw):
            decoded = bytes(x % 256 for x in raw)
        else:
            raise EvidenceError('missing_gmail_body')
        require(len(decoded) <= 180000, 'gmail_body_too_large')
        yield decoded.decode('utf-8').replace('\r\n', '\n')
    for child in part.get('parts', []):
        yield from plain_parts(child, depth + 1)


def header(mail, name):
    return [h.get('value', '') for h in mail.get('payload', {}).get('headers', [])
            if h.get('name', '').lower() == name]


def own_input(mail, owner):
    if 'SENT' not in mail.get('labelIds', []):
        return False
    for name in ('from', 'to'):
        values = header(mail, name)
        if len(values) != 1:
            return False
        addresses = getaddresses(values)
        if len(addresses) != 1 or addresses[0][1].lower() != owner.lower():
            return False
    return True


def one_event(events, name, code):
    found = [row for row in events if row.get('event') == name]
    require(len(found) == 1, code)
    return found[0]


def _verify(bundle):
    require(bundle.get('version') == 1, 'unsupported_evidence_version')
    expected, device = bundle['expected'], bundle['device']
    pk, source, chat = expected['target_pk'], expected['source'], expected['chat_pk']
    require(isinstance(source, str) and TRANSPORT.SOURCE_RE.fullmatch(source)
            and TRANSPORT._internal_id(chat) and isinstance(expected['text'], str), 'invalid_target')
    events = device['events']
    require(isinstance(events, list) and events, 'missing_device_events')
    run_ids = {row.get('run_id') for row in events}
    require(len(run_ids) == 1 and None not in run_ids, 'mixed_or_missing_run_id')
    snapshots = [row for row in events if row.get('event') == 'snapshot']
    require(snapshots, 'missing_snapshots')
    require(all(type(row.get('marker_rows')) is int and 0 <= row['marker_rows'] <= 2
                for row in snapshots), 'invalid_marker_count')
    snapshots.sort(key=lambda row: instant(row['read_started_utc']))
    positive = [row for row in snapshots if row.get('marker_rows', 0) > 0]
    result = {'evidence_status': 'partial', 'last_confirmed_stage': 'planned',
              'db_visibility': 'unconfirmed', 'application_acceptance': 'unconfirmed',
              'gmail_delivery': 'unconfirmed', 'locked_delivery': 'unproven',
              'chatgpt_natural_execution': 'unverified', 'reasons': []}
    if not positive:
        result['reasons'].append('target_not_observed')
        return result
    require(TRANSPORT._internal_id(pk), 'invalid_adopted_target')
    first = positive[0]
    require(all(row.get('marker_rows') == 1 and row.get('target_pk') == pk for row in positive),
            'target_changed_or_duplicated')
    require(first.get('marker_rows') == 1 and first.get('target_pk') == pk,
            'ambiguous_first_target')
    require(first.get('identity_checked') is True, 'identity_check_not_proven')
    files = first.get('files', {})
    for name in ('db', 'wal', 'shm'):
        value = files.get(name, {})
        require(value.get('readable') is True or (name != 'db' and value.get('exists') is False),
                'first_detection_file_access_unproven')
    first_bounds = interval(first)
    previous = [row for row in snapshots if row.get('marker_rows') == 0
                and interval(row)[1] <= first_bounds[0]]
    require(previous, 'no_prior_absence')
    last_absent = previous[-1]
    require(last_absent.get('identity_checked') is True, 'absence_identity_unproven')
    result.update(last_confirmed_stage='db_visible', db_visibility='confirmed',
                  visibility_window_jst=[interval(last_absent)[0].isoformat(), first_bounds[1].isoformat()])
    # This is a visibility interval, never an exact DB insertion timestamp.
    adopted = [row for row in events if row.get('event') == 'target_adopted']
    if not adopted:
        result['reasons'].append('target_not_adopted')
        return result
    require(len(adopted) == 1, 'multiple_target_adoptions')
    adopted = adopted[0]
    require(adopted.get('target_pk') == pk and adopted.get('source') == source
            and adopted.get('chat_pk') == chat and instant(adopted['at_utc']) >= first_bounds[1],
            'adopted_target_mismatch')
    result['last_confirmed_stage'] = 'target_adopted'
    if not device.get('body'):
        result['reasons'].append('payload_not_frozen')
        return result
    manifest, records = decode_body(device['body'])
    require(len(records) == 1 and manifest['count'] == 1, 'not_one_message')
    record = records[0]
    require(manifest['source'] == source and record['id'] == source + ':' + pk
            and record['refs']['message_pk'] == pk and record['refs']['chat_pk'] == chat
            and record['text'] == expected['text'], 'target_content_mismatch')
    starts = [row for row in events if row.get('event') == 'http_started']
    if not starts:
        result['reasons'].append('http_not_started')
        return result
    require(len(starts) == 1, 'more_than_one_http_attempt')
    start = starts[0]
    require(start.get('target_pk') == pk and start.get('batch_id') == manifest['batch_id']
            and str(start.get('maxid')) == pk and instant(start['at_utc']) >= instant(adopted['at_utc']),
            'http_target_mismatch')
    result['last_confirmed_stage'] = 'http_started'
    finishes = [row for row in events if row.get('event') == 'http_finished']
    if not finishes:
        result['reasons'].append('http_result_unconfirmed')
        return result
    require(len(finishes) == 1, 'multiple_http_finishes')
    finish = finishes[0]
    require(finish.get('target_pk') == pk and finish.get('batch_id') == manifest['batch_id'],
            'http_result_target_mismatch')
    accepted = (type(finish.get('http_status')) is int and 200 <= finish['http_status'] < 300
                and finish.get('response_ok') is True and finish.get('outcome') == 'accepted')
    if not accepted:
        result['reasons'].append('http_or_application_response_unconfirmed')
        return result
    # Failed requests may finish without any response. Their completion timestamp
    # is not evidence of a received response or of application acceptance.
    if finish.get('response_received_utc') is None:
        result['reasons'].append('successful_response_time_missing')
        return result
    response_time = instant(finish['response_received_utc'])
    http_bounds = (instant(start['at_utc']), response_time)
    require(http_bounds[0] <= http_bounds[1], 'reversed_http_interval')
    require(instant(finish['at_utc']) >= response_time, 'response_log_time_precedes_response')
    if finish.get('request_finished_utc') is not None:
        require(response_time <= instant(finish['request_finished_utc']) <= instant(finish['at_utc']),
                'request_completion_time_mismatch')
    application = one_event(events, 'application_acceptance', 'application_acceptance_unconfirmed')
    require(application.get('target_pk') == pk and application.get('batch_id') == manifest['batch_id']
            and application.get('accepted') is True
            and instant(application['at_utc']) >= instant(finish['at_utc']),
            'application_acceptance_mismatch')
    deployment = bundle.get('deployment', {})
    expected_endpoint = deployment.get('expected_endpoint_sha256', '')
    require(re.fullmatch(r'[0-9a-f]{64}', expected_endpoint or '') is not None
            and deployment.get('used_endpoint_sha256') == expected_endpoint
            and start.get('endpoint_sha256') == expected_endpoint,
            'existing_endpoint_match_unproven')
    if (deployment.get('protocol') != 'legacy_token_body_OK'
            or deployment.get('response_contract_verified') is not True):
        result['reasons'].append('deployed_response_contract_unverified')
        return result
    gas = bundle.get('gas', {})
    if not gas.get('batches') or not gas.get('messages'):
        result['reasons'].append('gas_ledger_missing')
        return result
    batch = ledger_row(gas['batches'], manifest['batch_id'], 'gas_batch_missing_or_duplicated')
    message = ledger_row(gas['messages'], record['id'], 'gas_message_missing_or_duplicated')
    require(batch.get('source') == source and batch.get('state') == 'accepted'
            and batch.get('body_hash') == digest(device['body'])
            and message.get('batch_id') == manifest['batch_id']
            and message.get('hash') == digest(TRANSPORT.canonical(record)), 'gas_ledger_mismatch')
    if 'stored_body' in gas:
        require(gas['stored_body'] == device['body'], 'gas_stored_body_mismatch')
    result.update(last_confirmed_stage='application_accepted', application_acceptance='confirmed',
                  acceptance_time_basis='successful_http_interval_and_matching_existing_ledger',
                  acceptance_window_jst=[t.isoformat() for t in http_bounds],
                  gas_exact_acceptance_time='not_available')
    # Existing schema has no accepted_at. Do not invent one, even if an export adds it.
    matches, seen = [], {}
    owner = expected['owner']
    require(isinstance(owner, str) and '@' in owner, 'owner_required')
    for mail in bundle.get('gmail', {}).get('messages', []):
        ident = mail.get('id')
        require(isinstance(ident, str) and ident, 'gmail_message_id_required')
        serialized = TRANSPORT.canonical(mail)
        require(ident not in seen or seen[ident] == serialized, 'gmail_export_id_conflict')
        if ident in seen:
            continue
        seen[ident] = serialized
        if not own_input(mail, owner):
            continue
        subjects = header(mail, 'subject')
        if subjects != ['[LINE-TASK-BATCH] ' + source + ' ' + manifest['batch_id']]:
            continue
        parts = list(plain_parts(mail.get('payload', {})))
        require(len(parts) == 1, 'ambiguous_gmail_plain_body')
        try:
            mail_manifest, mail_records = decode_body(parts[0])
        except (EvidenceError, ValueError):
            continue
        if mail_manifest != manifest or TRANSPORT.records_bytes(mail_records) != TRANSPORT.records_bytes(records):
            continue
        internal = mail.get('internalDate')
        require(isinstance(internal, str) and re.fullmatch(r'[0-9]{1,16}', internal),
                'gmail_internal_time_required')
        try:
            arrived = datetime.fromtimestamp(int(internal) / 1000, JST)
        except (ValueError, OverflowError, OSError):
            raise EvidenceError('invalid_gmail_internal_time') from None
        matches.append((mail, arrived))
    if not matches:
        result['reasons'].append('matching_input_mail_not_found')
        return result
    require(len(matches) == 1, 'duplicate_matching_input_mail')
    result.update(last_confirmed_stage='gmail_delivered', gmail_delivery='confirmed')
    lock = bundle.get('lock_window', {})
    if lock.get('independent_record_reviewed') is not True:
        result['reasons'].append('lock_window_not_independently_established')
        return result
    window = (instant(lock['certain_start']), instant(lock['certain_end']))
    require(window[0] < window[1], 'invalid_lock_window')
    device_error = uncertainty(lock.get('device_uncertainty_seconds'))
    gmail_error = uncertainty(lock.get('gmail_uncertainty_seconds'))
    mail_bounds = (matches[0][1], matches[0][1] + timedelta(milliseconds=1))
    require(mail_bounds[1] + gmail_error >= http_bounds[0] - device_error,
            'gmail_time_precedes_send')
    checks = {'last_absence_read_inside_lock': within(interval(last_absent), window, device_error),
              'first_detection_read_inside_lock': within(first_bounds, window, device_error),
              'acceptance_interval_inside_lock': within(http_bounds, window, device_error),
              'gmail_internal_time_inside_lock': within(mail_bounds, window, gmail_error)}
    result['lock_checks'] = checks
    result['gmail_time_basis'] = 'Gmail internalDate, not subject/header Date'
    if all(checks.values()):
        result.update(evidence_status='confirmed', locked_delivery='confirmed')
    else:
        result['reasons'].append('one_or_more_stages_outside_certain_lock_window')
    return result


def verify(bundle):
    try:
        return _verify(bundle)
    except EvidenceError as exc:
        return {'evidence_status': 'invalid', 'locked_delivery': 'unproven',
                'chatgpt_natural_execution': 'unverified', 'reason': str(exc)}
    except (TRANSPORT.TransportError, PRESENTATION.PresentationError):
        return {'evidence_status': 'invalid', 'locked_delivery': 'unproven',
                'chatgpt_natural_execution': 'unverified', 'reason': 'invalid_batch_payload'}
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, AttributeError):
        return {'evidence_status': 'invalid', 'locked_delivery': 'unproven',
                'chatgpt_natural_execution': 'unverified', 'reason': 'malformed_evidence'}


def from_run_directory(directory, external):
    """Read only; external contains reviewed deployment, owner, ledger, mail and lock evidence."""
    directory = Path(directory)
    def read(name):
        return TRANSPORT.strict_json((directory / name).read_text(encoding='utf-8'))
    plan = read('plan.json')
    events = [TRANSPORT.strict_json(line) for line in (directory / 'events.jsonl').read_text(encoding='utf-8').splitlines() if line]
    target = read('target-private.json') if (directory / 'target-private.json').is_file() else {}
    payload = read('payload-private.json') if (directory / 'payload-private.json').is_file() else {}
    if target:
        require(target.get('source') == plan['source'] and target.get('chat_pk') == plan['chat_pk'],
                'target_export_scope_mismatch')
    if payload:
        require(payload.get('target_pk') == target.get('target_pk'), 'payload_export_target_mismatch')
    starts = [row for row in events if row.get('event') == 'http_started']
    deployment = dict(external.get('deployment', {}))
    deployment.update(expected_endpoint_sha256=plan['endpoint_sha256'],
                      used_endpoint_sha256=starts[0].get('endpoint_sha256') if len(starts) == 1 else None)
    # Do not promote the plan's unverified contract or invent a native execution ID.
    return {'version': 1,
            'expected': {'source': plan['source'], 'chat_pk': plan['chat_pk'],
                         'text': plan['text'], 'target_pk': target.get('target_pk'),
                         'owner': external.get('owner')},
            'device': {'body': payload.get('body'), 'events': events},
            'deployment': deployment, 'gas': external.get('gas', {}),
            'gmail': external.get('gmail', {}), 'lock_window': external.get('lock_window', {})}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('evidence', type=Path, help='Private local export; never publish it')
    parser.add_argument('--run-dir', type=Path, help='Combine a recovered private trial directory with external exports')
    args = parser.parse_args()
    try:
        with args.evidence.open('rb') as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        require(len(data) <= 16 * 1024 * 1024, 'evidence_too_large')
        bundle = TRANSPORT.strict_json(data.decode('utf-8'))
        if args.run_dir is not None:
            bundle = from_run_directory(args.run_dir, bundle)
        result = verify(bundle)
    except (OSError, ValueError, UnicodeError, TRANSPORT.TransportError,
            KeyError, TypeError, AttributeError):
        result = {'evidence_status': 'invalid', 'locked_delivery': 'unproven', 'reason': 'input_unreadable'}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('locked_delivery') == 'confirmed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
