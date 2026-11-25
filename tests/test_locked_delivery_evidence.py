"""Synthetic evidence only. No network, mailbox or device operations."""
import base64
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('verify_locked_delivery', Path(__file__).resolve().parents[1] / 'tools/verify_locked_delivery.py')
evidence = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evidence)


def fixture():
    canonical, digest = evidence.TRANSPORT.canonical, evidence.digest
    source, pk = 'OFFLINE_ONLY', '102'
    record = {'id': source + ':' + pk, 'time': '2026-11-20T06:00:40+09:00',
              'chat': 'Synthetic Keep', 'sender': 'Synthetic Owner',
              'text': 'OFFLINE-FIXTURE-ONE 2026年11月25日18時までに架空の結果を確認する',
              'refs': {'message_pk': pk, 'chat_pk': '2', 'sender_pk': '1'}}
    body_records = canonical(record) + '\n'
    sha = digest(body_records)
    manifest = {'v': 2, 'kind': 'line_batch', 'source': source, 'part': 1, 'parts': 1,
                'count': 1, 'sha256': sha, 'batch_id': digest(source + '\n' + sha)}
    body = canonical(manifest) + '\n' + body_records
    files = {name: {'exists': True, 'readable': True} for name in ('db', 'wal', 'shm')}
    def event(name, time, **fields):
        return dict(event=name, run_id='synthetic-run', at_utc='2026-11-20T06:' + time + '+09:00', **fields)
    common = {'target_pk': pk, 'batch_id': manifest['batch_id']}
    events = [
        event('snapshot', '00:30.020', read_started_utc='2026-11-20T06:00:30+09:00',
              read_finished_utc='2026-11-20T06:00:30.020+09:00', marker_rows=0,
              identity_checked=True, files=deepcopy(files)),
        event('snapshot', '00:40.020', read_started_utc='2026-11-20T06:00:40+09:00',
              read_finished_utc='2026-11-20T06:00:40.020+09:00', marker_rows=1,
              target_pk=pk, identity_checked=True, files=deepcopy(files)),
        event('target_adopted', '00:40.030', target_pk=pk, source=source, chat_pk='2'),
        event('http_started', '00:41', maxid=pk, timeout_seconds=30, endpoint_sha256='a' * 64, **common),
        event('http_finished', '00:43', response_received_utc='2026-11-20T06:00:43+09:00',
              request_finished_utc='2026-11-20T06:00:43+09:00',
              http_status=200, response_ok=True, outcome='accepted', **common),
        event('application_acceptance', '00:43.010', accepted=True, mail_delivered='unverified', **common)]
    mail = {'id': 'synthetic-mail-one', 'internalDate': str(int(datetime.fromisoformat('2026-11-20T06:00:42+09:00').timestamp() * 1000)),
            'labelIds': ['SENT', 'INBOX'], 'payload': {'mimeType': 'text/plain',
            'headers': [{'name': 'From', 'value': 'owner@example.invalid'},
                        {'name': 'To', 'value': 'owner@example.invalid'},
                        {'name': 'Subject', 'value': '[LINE-TASK-BATCH] ' + source + ' ' + manifest['batch_id']}],
            'body': {'data': base64.urlsafe_b64encode(body.encode()).decode().rstrip('=')}}}
    return {'version': 1,
            'expected': {'source': source, 'chat_pk': '2', 'target_pk': pk,
                         'text': record['text'], 'owner': 'owner@example.invalid'},
            'device': {'body': body, 'events': events},
            'deployment': {'protocol': 'legacy_token_body_OK', 'response_contract_verified': True,
                           'expected_endpoint_sha256': 'a' * 64, 'used_endpoint_sha256': 'a' * 64},
            'gas': {'batches': [{'id': manifest['batch_id'], 'source': source, 'state': 'accepted',
                                'body_hash': digest(body), 'mail_state': 'sent'}],
                    'messages': [{'id': record['id'], 'batch_id': manifest['batch_id'],
                                  'hash': digest(canonical(record)), 'result_hash': None}]},
            'gmail': {'messages': [mail]},
            'lock_window': {'certain_start': '2026-11-20T06:00:20+09:00',
                            'certain_end': '2026-11-20T06:03:00+09:00',
                            'independent_record_reviewed': True,
                            'device_uncertainty_seconds': 0.1, 'gmail_uncertainty_seconds': 0.1}}


class LockedDeliveryEvidenceTests(unittest.TestCase):
    def test_full_existing_ledger_and_mail_match_confirms_delivery_not_natural_ai(self):
        value = fixture()
        result = evidence.verify(value)
        self.assertEqual(result['locked_delivery'], 'confirmed', result)
        self.assertEqual(result['chatgpt_natural_execution'], 'unverified')
        self.assertEqual(result['gas_exact_acceptance_time'], 'not_available')
        self.assertEqual(result['visibility_window_jst'], ['2026-11-20T06:00:30+09:00', '2026-11-20T06:00:40.020000+09:00'])
        for secret in ('synthetic-mail-one', value['expected']['text'], value['expected']['owner'], 'a' * 64):
            self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))

    def test_http_ok_and_accepted_ledger_without_mail_is_only_partial(self):
        value = fixture(); value['gmail']['messages'] = []
        result = evidence.verify(value)
        self.assertEqual(result['application_acceptance'], 'confirmed')
        self.assertEqual(result['gmail_delivery'], 'unconfirmed')
        self.assertEqual(result['last_confirmed_stage'], 'application_accepted')
        self.assertEqual(result['locked_delivery'], 'unproven')

    def test_http_2xx_without_exact_application_ok_is_not_acceptance(self):
        value = fixture(); value['device']['events'][4]['response_ok'] = False
        result = evidence.verify(value)
        self.assertEqual(result['application_acceptance'], 'unconfirmed')
        self.assertEqual(result['locked_delivery'], 'unproven')

    def test_missing_actual_response_time_is_never_inferred_from_request_or_log_time(self):
        for missing in ('absent', 'null'):
            value = fixture(); finish = value['device']['events'][4]
            finish['request_finished_utc'] = finish['at_utc']
            if missing == 'absent':
                del finish['response_received_utc']
            else:
                finish['response_received_utc'] = None
            result = evidence.verify(value)
            self.assertEqual(result['evidence_status'], 'partial')
            self.assertEqual(result['last_confirmed_stage'], 'http_started')
            self.assertIn('successful_response_time_missing', result['reasons'])
            self.assertNotIn('acceptance_window_jst', result)

    def test_unverified_deployment_and_missing_ledger_hold(self):
        value = fixture(); value['deployment']['response_contract_verified'] = False
        self.assertIn('deployed_response_contract_unverified', evidence.verify(value)['reasons'])
        value = fixture(); value['gas']['messages'] = []
        self.assertIn('gas_ledger_missing', evidence.verify(value)['reasons'])
        value = fixture(); value['device']['events'][3]['endpoint_sha256'] = 'b' * 64
        self.assertEqual(evidence.verify(value)['reason'], 'existing_endpoint_match_unproven')

    def test_wrong_ledger_hash_or_message_batch_cannot_confirm(self):
        for mutate in (lambda v: v['gas']['batches'][0].update(body_hash='b' * 64),
                       lambda v: v['gas']['messages'][0].update(batch_id='wrong-batch')):
            value = fixture(); mutate(value)
            self.assertEqual(evidence.verify(value)['reason'], 'gas_ledger_mismatch')

    def test_subject_and_time_alone_do_not_match_mail_contents(self):
        value = fixture()
        wrong_body = value['device']['body'].replace('OFFLINE-FIXTURE-ONE', 'OFFLINE-FIXTURE-TWO')
        value['gmail']['messages'][0]['payload']['body']['data'] = base64.urlsafe_b64encode(wrong_body.encode()).decode()
        self.assertNotEqual(evidence.verify(value)['locked_delivery'], 'confirmed')

    def test_wrong_owner_and_non_sent_mail_do_not_count(self):
        for mutate in (lambda m: m['payload']['headers'][1].update(value='third-party@example.invalid'),
                       lambda m: m.update(labelIds=['INBOX'])):
            value = fixture(); mutate(value['gmail']['messages'][0])
            result = evidence.verify(value)
            self.assertEqual(result['gmail_delivery'], 'unconfirmed')

    def test_duplicate_delivery_is_flagged_but_duplicate_export_is_deduplicated(self):
        value = fixture(); value['gmail']['messages'].append(deepcopy(value['gmail']['messages'][0]))
        self.assertEqual(evidence.verify(value)['locked_delivery'], 'confirmed')
        value['gmail']['messages'][1]['id'] = 'synthetic-mail-two'
        self.assertEqual(evidence.verify(value)['reason'], 'duplicate_matching_input_mail')

    def test_first_detection_and_absence_must_both_fit_certain_lock_window(self):
        value = fixture(); value['lock_window']['certain_start'] = '2026-11-20T06:00:35+09:00'
        result = evidence.verify(value)
        self.assertEqual(result['gmail_delivery'], 'confirmed')
        self.assertEqual(result['locked_delivery'], 'unproven')
        self.assertFalse(result['lock_checks']['last_absence_read_inside_lock'])
        value = fixture(); value['lock_window']['certain_start'] = '2026-11-20T06:00:40.010+09:00'
        self.assertFalse(evidence.verify(value)['lock_checks']['first_detection_read_inside_lock'])

    def test_uncertainty_and_missing_external_lock_evidence_prevent_pass(self):
        value = fixture(); value['lock_window']['certain_end'] = '2026-11-20T06:00:43.050+09:00'
        self.assertEqual(evidence.verify(value)['locked_delivery'], 'unproven')
        value = fixture(); value['lock_window']['independent_record_reviewed'] = False
        self.assertIn('lock_window_not_independently_established', evidence.verify(value)['reasons'])
        value = fixture(); del value['lock_window']['gmail_uncertainty_seconds']
        self.assertEqual(evidence.verify(value)['reason'], 'timing_uncertainty_required')

    def test_target_identity_extra_attempt_and_file_access_are_rejected(self):
        mutations = [lambda v: v['expected'].update(target_pk='999'),
                     lambda v: v['device']['events'][1].update(marker_rows=True),
                     lambda v: v['device']['events'][2].update(chat_pk='9'),
                     lambda v: v['device']['events'][1]['files']['wal'].update(readable=False),
                     lambda v: v['device']['events'].append(deepcopy(v['device']['events'][3]))]
        for mutate in mutations:
            value = fixture(); mutate(value)
            self.assertEqual(evidence.verify(value)['evidence_status'], 'invalid')

    def test_absent_sidecars_do_not_fail_and_sheet_data_json_exports_are_supported(self):
        value = fixture()
        for name in ('wal', 'shm'):
            value['device']['events'][1]['files'][name] = {'exists': False}
        for table in ('messages', 'batches'):
            value['gas'][table] = [{'id': row['id'], 'data_json': json.dumps(row)} for row in value['gas'][table]]
        self.assertEqual(evidence.verify(value)['locked_delivery'], 'confirmed')

    def test_internal_date_required_and_prior_mail_time_is_not_recreated(self):
        value = fixture(); del value['gmail']['messages'][0]['internalDate']
        self.assertEqual(evidence.verify(value)['reason'], 'gmail_internal_time_required')
        value = fixture(); value['gmail']['messages'][0]['internalDate'] = '1'
        self.assertEqual(evidence.verify(value)['reason'], 'gmail_time_precedes_send')

    def test_no_target_and_malformed_evidence_are_safe_partial_or_invalid(self):
        value = fixture(); value['device']['events'] = value['device']['events'][:1]
        value['expected']['target_pk'] = None; value['device']['body'] = None
        result = evidence.verify(value)
        self.assertEqual(result['last_confirmed_stage'], 'planned')
        self.assertEqual(result['locked_delivery'], 'unproven')
        self.assertEqual(evidence.verify({'private value': 'never echo'})['reason'], 'unsupported_evidence_version')
        self.assertNotIn('never echo', json.dumps(evidence.verify({'private value': 'never echo'})))

    def test_c1_and_advanced_gmail_byte_array_keep_the_same_target(self):
        value = fixture(); manifest, records = evidence.decode_body(value['device']['body'])
        record = records[0]; canonical = evidence.TRANSPORT.canonical
        header = {'format': 'C1', 'source': manifest['source'],
                  'chats': [[record['chat'], '2']], 'senders': [[record['sender'], '1']]}
        body = canonical(manifest) + '\n' + canonical(header) + '\n' + canonical(['102', record['time'], 0, 0, record['text']]) + '\n'
        value['device']['body'] = body; value['gas']['batches'][0]['body_hash'] = evidence.digest(body)
        value['gmail']['messages'][0]['payload']['body']['data'] = list(body.encode())
        self.assertEqual(evidence.verify(value)['locked_delivery'], 'confirmed')

    def test_well_formed_extra_old_or_other_chat_record_is_rejected(self):
        value = fixture(); manifest, records = evidence.decode_body(value['device']['body'])
        extra = deepcopy(records[0]); extra['id'] = 'OFFLINE_ONLY:101'
        extra['refs']['message_pk'] = '101'; extra['refs']['chat_pk'] = '9'; extra['text'] = 'old other-chat text'
        records.append(extra)
        sha = evidence.TRANSPORT.digest(evidence.TRANSPORT.records_bytes(records))
        manifest.update(count=2, sha256=sha, batch_id=evidence.digest(manifest['source'] + '\n' + sha))
        value['device']['body'] = evidence.TRANSPORT.canonical(manifest) + '\n' + evidence.TRANSPORT.records_bytes(records).decode()
        self.assertEqual(evidence.verify(value)['reason'], 'not_one_message')

    def test_adopted_but_unfrozen_state_is_partial_not_a_malformed_failure(self):
        value = fixture(); value['device']['body'] = None
        value['device']['events'] = value['device']['events'][:3]
        result = evidence.verify(value)
        self.assertEqual(result['last_confirmed_stage'], 'target_adopted')
        self.assertIn('payload_not_frozen', result['reasons'])

    def test_recovered_private_run_combines_without_mutating_or_auto_verifying_contract(self):
        value = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = dict(source=value['expected']['source'], chat_pk=value['expected']['chat_pk'],
                        text=value['expected']['text'], endpoint_sha256='a' * 64,
                        deployment_response_contract_verified=True)
            target = dict(target_pk='102', source=plan['source'], chat_pk=plan['chat_pk'])
            for name, data in [('plan.json', plan), ('target-private.json', target),
                               ('payload-private.json', {'body': value['device']['body'], 'target_pk': '102'})]:
                (root / name).write_text(json.dumps(data), encoding='utf-8')
            (root / 'events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in value['device']['events']))
            external = {key: value[key] for key in ('deployment', 'gas', 'gmail', 'lock_window')}
            external['owner'] = value['expected']['owner']
            before = {path.name: path.read_bytes() for path in root.iterdir()}
            combined = evidence.from_run_directory(root, external)
            self.assertEqual(evidence.verify(combined)['locked_delivery'], 'confirmed')
            external = deepcopy(external); del external['deployment']['response_contract_verified']
            held = evidence.verify(evidence.from_run_directory(root, external))
            self.assertIn('deployed_response_contract_unverified', held['reasons'])
            self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})

    def test_recovered_connection_failure_and_parent_timeout_remain_unconfirmed_partial(self):
        for outcome in ('unconfirmed', 'timeout_or_stop'):
            value = fixture()
            finish = value['device']['events'][4]
            finish.update(http_status=None, response_ok=False, outcome=outcome,
                          response_received_utc=None, request_finished_utc='2026-11-20T06:01:11+09:00',
                          at_utc='2026-11-20T06:01:11+09:00')
            if outcome == 'timeout_or_stop':
                # Supervisor-side timeout event has no received-response field.
                finish.pop('response_received_utc')
                finish.pop('request_finished_utc')
            value['device']['events'][5].update(accepted=False, at_utc='2026-11-20T06:01:11.100+09:00')
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                plan = dict(source=value['expected']['source'], chat_pk=value['expected']['chat_pk'],
                            text=value['expected']['text'], endpoint_sha256='a' * 64,
                            deployment_response_contract_verified=False)
                target = dict(target_pk='102', source=plan['source'], chat_pk=plan['chat_pk'])
                for name, data in [('plan.json', plan), ('target-private.json', target),
                                   ('payload-private.json', {'body': value['device']['body'], 'target_pk': '102'})]:
                    (root / name).write_text(json.dumps(data), encoding='utf-8')
                (root / 'events.jsonl').write_text(''.join(json.dumps(row) + '\n' for row in value['device']['events']))
                before = {path.name: path.read_bytes() for path in root.iterdir()}
                external = {'owner': value['expected']['owner']}
                result = evidence.verify(evidence.from_run_directory(root, external))
                self.assertEqual(result['evidence_status'], 'partial', result)
                self.assertEqual(result['last_confirmed_stage'], 'http_started')
                self.assertEqual(result['application_acceptance'], 'unconfirmed')
                self.assertEqual(result['gmail_delivery'], 'unconfirmed')
                self.assertEqual(result['locked_delivery'], 'unproven')
                self.assertIn('http_or_application_response_unconfirmed', result['reasons'])
                self.assertNotIn('acceptance_window_jst', result)
                self.assertEqual(before, {path.name: path.read_bytes() for path in root.iterdir()})


if __name__ == '__main__':
    unittest.main()
