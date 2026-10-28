"""Synthetic-only queue/crash/receiver checks. No external service is contacted."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
import test_offline as fixtures

APP = Path(__file__).resolve().parents[1] / 'normal-only-rc1'
COMMON = r'''
import json, os, sqlite3, socket, threading, time
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import sync, sender
from delivery_state import DeliveryState, DeliveryError
from runtime_paths import STATE_DIR, CONFIG_FILE
socket.socket.connect = lambda *args: (_ for _ in ()).throw(AssertionError('network forbidden'))
config = json.loads(CONFIG_FILE.read_text())
database = Path(config['line_db'])
def add(pk, text='synthetic new'):
    with closing(sqlite3.connect(database)) as db:
        db.execute('INSERT INTO ZMESSAGE VALUES(?,?,?,?,?)', (pk,1735689600000,2,1,text))
        db.commit()
def status():
    q=DeliveryState(STATE_DIR)
    try:return q.status()
    finally:q.close()
'''


class DurableDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ManualCLIIntegration()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root, self.env = self.fixture.root, self.fixture.env
        state, config = self.root / 'state', self.root / 'config'
        state.mkdir(mode=0o700)
        config.mkdir(mode=0o700)
        (state / 'dbpath').write_text(str(self.fixture.db.resolve()), encoding='utf-8')
        settings = {'schema_version': 1, 'source_id': 'OFFLINE_TEST_SOURCE',
                    'enabled': True, 'normal_line': {'enabled': True},
                    'line_db': str(self.fixture.db.resolve()), 'group_db': None,
                    'timestamp_unit': 'unix_ms',
                    'transport': {'endpoint': 'https://example.invalid/never-connected',
                                  'token': 'SYNTHETIC_TOKEN_ONLY'}}
        (config / 'config.json').write_text(json.dumps(settings), encoding='utf-8')

    def case(self, code):
        script = 'import sys;sys.path.insert(0,' + repr(str(APP)) + ')\n' + COMMON + code
        result = subprocess.run([sys.executable, '-c', script], env=self.env,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_first_run_skips_history_then_collects_and_acks_new_text(self):
        self.case(r'''
assert sync.collect()['pending']==0
add(102, 'quotes,"\nJapanese \u65e5\u672c')
add(103, '')
assert sync.collect()['pending']==1
calls=[]
assert sender.send_once(lambda *args: calls.append(args) or True,now=10000)['sent']
assert len(calls)==1 and calls[0][3]==102
assert status()['collected']==103 and status()['acknowledged']==102
assert status()['pending']==0
assert sender.send_once(lambda *args: False,now=10001)['reason']=='hourly_gate'
''')

    def test_timeout_new_arrival_and_retry_keep_identical_batch(self):
        self.case(r'''
sync.collect();add(102);sync.collect();calls=[]
def lost(*args):
    calls.append(args)
    raise TimeoutError('SYNTHETIC_TOKEN_ONLY must not be logged')
assert sender.send_once(lost,now=10000)['error']=='delivery_unconfirmed'
assert status()['acknowledged']==101 and status()['pending']==1
add(103,'arrived while first batch was uncertain');sync.collect()
assert sender.send_once(lambda *args: True,now=10059)['reason']=='retry_wait'
assert sender.send_once(lambda *args: calls.append(args) or True,now=10060)['sent']
assert calls[0]==calls[1] and status()['pending']==1
assert sender.send_once(lambda *args: False,now=10061)['reason']=='hourly_gate'
assert sender.send_once(lambda *args: True,now=13660)['sent']
assert status()['pending']==0 and status()['acknowledged']==103
''')

    def test_crash_after_receiver_acceptance_retries_one_stable_id(self):
        self.case(r'''
sync.collect();add(102);sync.collect();accepted=set();calls=[]
def receiver(*args):
    batch=json.loads(args[2].splitlines()[0])['batch_id']
    accepted.add(batch);calls.append(batch);return True
with patch.object(DeliveryState,'acknowledge',side_effect=KeyboardInterrupt):
    try:sender.send_once(receiver,now=10000)
    except KeyboardInterrupt:pass
assert status()['pending']==1 and status()['frozen']
assert sender.send_once(receiver,now=10001)['sent']
assert len(calls)==2 and len(accepted)==1 and status()['pending']==0
''')

    def test_collector_crash_rolls_back_rows_and_cursor_together(self):
        self.case(r'''
sync.collect();add(102)
original=DeliveryState.insert_rows
def interrupted(q,rows):
    original(q,rows)
    raise KeyboardInterrupt()
with patch.object(DeliveryState,'insert_rows',interrupted):
    try:sync.collect()
    except KeyboardInterrupt:pass
assert status()['collected']==101 and status()['pending']==0
assert sync.collect()['pending']==1
''')

    def test_delete_from_line_after_collection_does_not_lose_pending(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
with closing(sqlite3.connect(database)) as db:
    db.execute('DELETE FROM ZMESSAGE WHERE Z_PK=102');db.commit()
try:sync.collect()
except DeliveryError:pass
else:raise AssertionError('rewind was not detected')
assert sender.send_once(lambda *args: True,now=10000)['sent']
assert status()['pending']==0
''')

    def test_legacy_csv_import_survives_missing_source_row(self):
        self.case(r'''
import csv
(STATE_DIR/'sentid').write_text('100')
with (STATE_DIR/'new.csv').open('w',newline='',encoding='utf-8') as f:
    csv.writer(f).writerow([102,1735689600000,2,1,'retained synthetic CSV only'])
assert sync.collect()['pending']==2
assert (STATE_DIR/'sentid').read_text()=='100'
assert sender.send_once(lambda *args: True,now=10000)['sent']
assert status()['acknowledged']==102 and (STATE_DIR/'sentid').read_text()=='100'
''')

    def test_corrupt_legacy_row_and_ambiguous_baseline_stop(self):
        self.case(r'''
(STATE_DIR/'new.csv').write_text('not,a,valid,row\n')
try:sync.collect()
except RuntimeError:pass
else:raise AssertionError('ambiguous legacy state was accepted')
assert status()['collected']==0
(STATE_DIR/'sentid').write_text('100')
try:sync.collect()
except DeliveryError:pass
else:raise AssertionError('corrupt legacy row was silently skipped')
assert status()['collected']==0
''')

    def test_valid_prefix_precedes_oversized_head_without_skipping(self):
        self.case(r'''
sync.collect();add(102,'small');add(103,'x'*70000);add(104,'later');sync.collect()
assert sender.send_once(lambda *args: True,now=10000)['sent']
assert status()['acknowledged']==102 and status()['pending']==2
try:sender.send_once(lambda *args: True,now=13600)
except RuntimeError:pass
else:raise AssertionError('oversized message was skipped')
assert status()['acknowledged']==102 and status()['pending']==2
''')

    def test_destination_change_cannot_redirect_pending_private_data(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
config['transport']['endpoint']='https://other.example.invalid/never-connected'
CONFIG_FILE.write_text(json.dumps(config))
try:sender.send_once(lambda *args: True,now=10000)
except DeliveryError:pass
else:raise AssertionError('destination changed without migration')
assert status()['pending']==1 and status()['acknowledged']==101
''')

    def test_false_ack_retains_and_response_never_logged(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args: 'OK',now=10000)['error']=='delivery_unconfirmed'
assert status()['pending']==1 and status()['acknowledged']==101
assert sender.send_once(lambda *args: True,now=10060)['sent']
''')

    def test_concurrent_senders_serialize_the_frozen_delivery(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
sender.send_once(lambda *args: False,now=10000)
calls=[];errors=[]
def post(*args):calls.append(args);time.sleep(.15);return True
def run():
    try:sender.send_once(post,now=10060)
    except BaseException as e:errors.append(type(e).__name__)
threads=[threading.Thread(target=run) for _ in range(2)]
for t in threads:t.start()
for t in threads:t.join()
assert not errors and len(calls)==1 and status()['pending']==0
''')

    def test_foreign_sqlite_refused_without_modification(self):
        self.case(r'''
target=STATE_DIR/'normal-delivery.sqlite'
with closing(sqlite3.connect(target)) as db:
    db.execute('CREATE TABLE unrelated(value)');db.commit()
before=target.read_bytes()
try:DeliveryState(STATE_DIR)
except DeliveryError:pass
else:raise AssertionError('foreign database adopted')
assert target.read_bytes()==before
''')

    def test_post_legacy_packet_and_strict_ok_with_mock_response(self):
        self.case(r'''
from unittest.mock import MagicMock
response=MagicMock();response.__enter__.return_value=response
response.read.return_value=b'OK\n'
opener=MagicMock();opener.open.return_value=response
with patch('sender.urllib.request.build_opener',return_value=opener):
    assert sender.post_legacy('https://example.invalid','SYNTHETIC_TOKEN_ONLY','fixture',102)
    packet=json.loads(opener.open.call_args[0][0].data)
    assert packet=={'token':'SYNTHETIC_TOKEN_ONLY','body':'fixture','maxid':'102'}
    response.read.return_value=b'OK extra'
    assert not sender.post_legacy('https://example.invalid','SYNTHETIC_TOKEN_ONLY','fixture',102)
    response.read.return_value=b'OK'+b' '*127
    assert not sender.post_legacy('https://example.invalid','SYNTHETIC_TOKEN_ONLY','fixture',102)
''')

    def test_invalid_endpoint_and_redirects_never_transmit(self):
        self.case(r'''
from runtime_paths import valid_endpoint
from urllib.request import Request
assert not valid_endpoint('https://user:secret@example.invalid/')
assert not valid_endpoint('https://')
assert not valid_endpoint('https://example.invalid/#fragment')
assert not valid_endpoint('https://example.invalid:999999/')
for destination in ('http://example.invalid/', 'https://other.example.invalid/'):
    try:sender.SafeRedirect().redirect_request(Request('https://example.invalid/'),None,302,'',{},destination)
    except DeliveryError:pass
    else:raise AssertionError('unsafe redirect allowed')
''')


    def test_redirect_must_keep_tls_origin_and_only_allow_gas_response_host(self):
        self.case(r'''
from urllib.request import Request
base = Request('https://example.invalid/hook')
for target in ('https://example.invalid:8443/hook',
               'https://script.googleusercontent.com/elsewhere',
               'https://example.invalid:999999/hook',
               'http://example.invalid/hook'):
    try:sender.SafeRedirect().redirect_request(base,None,302,'',{},target)
    except DeliveryError:pass
    else:raise AssertionError('unsafe receiver redirect accepted')
same=sender.SafeRedirect().redirect_request(base,None,302,'',{},'https://example.invalid:443/safe')
assert same.full_url=='https://example.invalid:443/safe'
gas=sender.SafeRedirect().redirect_request(
    Request('https://script.google.com/macros/s/fixture/exec'),None,302,'',{},
    'https://script.googleusercontent.com/macros/echo?user_content_key=fixture')
assert gas.full_url.startswith('https://script.googleusercontent.com/')
''')

    def test_config_and_name_cache_symlink_defenses(self):
        self.case(r'''
from pathlib import Path
from unittest.mock import patch
import legacy_names
from runtime_paths import require_enabled
original=Path.is_symlink
with patch.object(Path,'is_symlink',
    lambda self: self==CONFIG_FILE or original(self)):
    try:require_enabled()
    except RuntimeError as e:assert str(e)=='unsafe_runtime_symlink'
    else:raise AssertionError('symlinked config accepted')
path=STATE_DIR/'named-cache.sqlite'
with patch.object(Path,'is_symlink',
    lambda self: self==path or original(self)):
    try:legacy_names.Session()
    except ValueError as e:assert str(e)=='symlinked_name_cache'
    else:raise AssertionError('symlinked name cache accepted')
assert not path.exists()
''')

    def test_snapshot_backup_retains_frozen_request_and_cursors(self):
        self.case(r'''
sync.collect();add(102,'fixture');sync.collect()
calls=[]
def timeout(*args):
    calls.append(args);raise TimeoutError('synthetic lost connection')
assert sender.send_once(timeout,now=10000)['error']=='delivery_unconfirmed'
with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as original:
    with closing(sqlite3.connect(STATE_DIR/'snapshot.sqlite')) as backup:
        original.backup(backup)
restored=STATE_DIR/'isolated-restored-copy'
restored.mkdir()
(STATE_DIR/'snapshot.sqlite').replace(restored/'normal-delivery.sqlite')
with closing(DeliveryState(restored)) as recovered:
    assert recovered.status()['frozen']
    assert recovered.status()['pending']==1
    assert recovered.status()['collected']==102
    frozen=recovered.db.execute('SELECT * FROM delivery').fetchone()
    assert frozen['body']==calls[0][2]
    assert frozen['ids']=='[102]'
    assert frozen['attempts']==1
assert status()['pending']==1 and status()['frozen']
''')

    def test_receiver_accept_then_lost_response_replay_has_one_fake_effect(self):
        self.case(r'''
import test_receiver_contract
sync.collect();add(102,'only synthetic text');sync.collect()
calls=[]
with closing(test_receiver_contract.DurableMockReceiver(STATE_DIR/'mock_receiver.sqlite')) as receiver:
    def post(endpoint, token, body, maxid):
        assert token=='SYNTHETIC_TOKEN_ONLY'
        calls.append(body)
        assert receiver.accept('OFFLINE_ONLY_TOKEN',maxid,body)=='OK'
        if len(calls)==1:raise TimeoutError('simulated response loss')
        return True
    assert sender.send_once(post,now=10000)['error']=='delivery_unconfirmed'
    assert status()['pending']==1 and status()['frozen']
    assert receiver.effect_count()==1
    assert sender.send_once(post,now=10059)['reason']=='retry_wait'
    assert sender.send_once(post,now=10060)['sent']
    assert len(calls)==2 and calls[0]==calls[1]
    assert receiver.effect_count()==1
assert status()['pending']==0 and not status()['frozen']
''')

    def test_many_queued_rows_are_drained_in_bounded_batches(self):
        self.case(r'''
sync.collect()
with closing(sqlite3.connect(database)) as db:
    db.executemany('INSERT INTO ZMESSAGE VALUES(?,?,?,?,?)',
        [(pk,1735689600000,2,1,'synthetic row %s' % pk) for pk in range(102,702)])
    db.commit()
assert sync.collect()['pending']==600
calls=[]
for i in range(16):
    if status()['pending']==0:break
    result=sender.send_once(
        lambda endpoint,token,body,maxid: calls.append((body,maxid)) or True,
        now=10000+3601*i)
    assert result['sent']
assert status()['pending']==0, status()
assert status()['collected']==701
assert len(calls)>1
assert len({b for b,maxid in calls})==len(calls)
''')

    def test_corrupt_frozen_queue_never_sends_and_keeps_pending(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args:False,now=10000)['error']=='delivery_unconfirmed'
with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as db:
    db.execute("UPDATE delivery SET ids='NOT_JSON' WHERE singleton=1")
    db.commit()
called=[]
result=sender.send_once(lambda *args:called.append(args) or True,now=10060)
assert result['error']=='delivery_unconfirmed' and not called
assert status()['pending']==1 and status()['frozen']
''')


if __name__ == '__main__':
    unittest.main()
