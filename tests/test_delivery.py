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
assert sync.collect()['pending']==2, status()
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
    except BaseException as e:errors.append(str(e))
threads=[threading.Thread(target=run) for _ in range(2)]
for t in threads:t.start()
for t in threads:t.join()
assert all(e=='sender_already_running' for e in errors),errors
assert len(calls)==1 and status()['pending']==0
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
sys.path.insert(0,str(Path(sync.__file__).resolve().parents[1]/'tests'))
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
try:sender.send_once(lambda *args:called.append(args) or True,now=10060)
except DeliveryError as e:assert str(e)=='corrupt_frozen_delivery'
else:raise AssertionError('corrupt frozen delivery incorrectly accepted')
assert not called
assert status()['pending']==1 and status()['frozen']
''')


    def test_corrupt_frozen_body_and_queue_payload_never_transmit_or_ack(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args:False,now=10000)['error']=='delivery_unconfirmed'
with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as db:
    original_body=db.execute('SELECT body FROM delivery').fetchone()[0]
    db.execute("UPDATE delivery SET body=body||'tampered' WHERE singleton=1")
    db.commit()
calls=[]
try:sender.send_once(lambda *args:calls.append(args) or True,now=10060)
except DeliveryError as e:assert str(e)=='corrupt_frozen_delivery'
else:raise AssertionError('altered frozen body was accepted')
assert not calls and status()['pending']==1
with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as db:
    db.execute("UPDATE delivery SET body=? WHERE singleton=1",(original_body,))
    db.execute("UPDATE messages SET raw='[]' WHERE pk=102")
    db.commit()
try:sender.send_once(lambda *args:calls.append(args) or True,now=10060)
except DeliveryError as e:assert str(e)=='corrupt_frozen_delivery'
else:raise AssertionError('modified queued message was accepted')
assert not calls and status()['pending']==1
''')

    def test_rotated_token_is_used_after_freezing_before_post(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
original=DeliveryState.freeze
def rotate(q,body,ids):
    original(q,body,ids)
    config['transport']['token']='SYNTHETIC_ROTATED_TOKEN'
    CONFIG_FILE.write_text(json.dumps(config))
tokens=[]
with patch.object(DeliveryState,'freeze',rotate):
    assert sender.send_once(
        lambda endpoint,token,body,maxid: tokens.append(token) or True,
        now=10000)['sent']
assert tokens==['SYNTHETIC_ROTATED_TOKEN']
assert status()['pending']==0
''')

    def test_end_to_end_payload_can_be_sorted_by_time_not_primary_key(self):
        self.case(r'''
sys.path.insert(0,str(Path(sync.__file__).resolve().parents[1]/'tests'))
import test_receiver_contract
sync.collect()
with closing(sqlite3.connect(database)) as db:
    db.execute('INSERT INTO ZMESSAGE VALUES(?,?,?,?,?)',
        (102,1735689720000,2,1,'older PK but newer time'))
    db.execute('INSERT INTO ZMESSAGE VALUES(?,?,?,?,?)',
        (103,1735689660000,2,1,'newer PK but older time'))
    db.commit()
assert sync.collect()['pending']==2
with closing(test_receiver_contract.DurableMockReceiver(
        STATE_DIR/'offline_reverse_timestamp.sqlite')) as receiver:
    requests=[]
    def post(endpoint,token,body,maxid):
        import presentation
        records=presentation.decode_named(body.split('\n',1)[1])
        requests.append([int(x['refs']['message_pk']) for x in records])
        assert receiver.accept('OFFLINE_ONLY_TOKEN',maxid,body)=='OK'
        return True
    assert sender.send_once(post,now=10000)['sent']
    assert requests==[[103,102]],requests
    assert receiver.effect_count()==2
assert status()['pending']==0 and status()['acknowledged']==103
''')


    def test_invalid_new_timestamp_keeps_cursor_and_recovers_after_fix(self):
        self.case(r'''
sync.collect()
with closing(sqlite3.connect(database)) as db:
    db.execute('INSERT INTO ZMESSAGE VALUES(?,?,?,?,?)',
        (102,1735689600000.5,2,1,'synthetic fractional millis'))
    db.commit()
try:sync.collect()
except DeliveryError as e:assert str(e)=='invalid_message_timestamp'
else:raise AssertionError('invalid timestamp silently collected')
assert status()['collected']==101 and status()['pending']==0
with closing(sqlite3.connect(database)) as db:
    db.execute('UPDATE ZMESSAGE SET ZTIMESTAMP=? WHERE Z_PK=102',
        (1735689600000,))
    db.commit()
assert sync.collect()['pending']==1
assert status()['collected']==102
assert sender.send_once(lambda *args:True,now=10000)['sent']
''')


    def test_missing_queue_table_is_not_recreated_and_snapshot_can_be_read(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args:False,now=10000)['error']=='delivery_unconfirmed'
path=STATE_DIR/'normal-delivery.sqlite'
snapshot=STATE_DIR/'private-snapshot.sqlite'
with closing(sqlite3.connect(path)) as db:
    with closing(sqlite3.connect(snapshot)) as backup:db.backup(backup)
    db.execute('DROP TABLE meta');db.commit()
    original=db.execute('SELECT body,ids FROM delivery').fetchone()
calls=[]
for operation in (sync.collect, lambda:sender.send_once(
        lambda *args:calls.append(args) or True,now=10060)):
    try:operation()
    except DeliveryError as e:assert str(e)=='unsupported_or_corrupt_delivery_schema'
    else:raise AssertionError('missing table was repaired or accepted')
assert not calls
with closing(sqlite3.connect(path)) as db:
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()
    assert db.execute('SELECT body,ids FROM delivery').fetchone()==original
    assert db.execute('SELECT acked FROM messages WHERE pk=102').fetchone()[0]==0
restored=STATE_DIR/'restored-isolated';restored.mkdir()
snapshot.replace(restored/'normal-delivery.sqlite')
with closing(DeliveryState(restored)) as recovered:
    assert recovered.status()==dict(collected=102,acknowledged=101,pending=1,frozen=True)
    assert recovered.db.execute('SELECT body,ids FROM delivery').fetchone()[:]==original
''')

    def test_changed_queue_constraint_is_rejected_without_repair(self):
        self.case(r'''
sync.collect()
path=STATE_DIR/'normal-delivery.sqlite'
with closing(sqlite3.connect(path)) as db:
    db.execute('DROP TABLE delivery')
    db.execute("""CREATE TABLE delivery(
        singleton INTEGER PRIMARY KEY,body TEXT NOT NULL,ids TEXT NOT NULL,
        batch_id TEXT NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL NOT NULL DEFAULT 0)""")
    db.commit()
    definition=db.execute("SELECT sql FROM sqlite_master WHERE name='delivery'").fetchone()[0]
try:DeliveryState(STATE_DIR)
except DeliveryError as e:assert str(e)=='unsupported_or_corrupt_delivery_schema'
else:raise AssertionError('changed singleton constraint accepted')
with closing(sqlite3.connect(path)) as db:
    assert db.execute("SELECT sql FROM sqlite_master WHERE name='delivery'").fetchone()[0]==definition
''')

    def test_unknown_queue_schema_objects_are_not_executed(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
path=STATE_DIR/'normal-delivery.sqlite'
with closing(sqlite3.connect(path)) as db:
    db.execute("""CREATE TRIGGER unexpected_ack AFTER UPDATE ON messages
        BEGIN DELETE FROM messages; END""")
    db.commit()
calls=[]
try:sender.send_once(lambda *args:calls.append(args) or True,now=10000)
except DeliveryError as e:assert str(e)=='unsupported_or_corrupt_delivery_schema'
else:raise AssertionError('unexpected trigger accepted')
assert not calls
with closing(sqlite3.connect(path)) as db:
    assert db.execute('SELECT COUNT(*) FROM messages WHERE acked=0').fetchone()[0]==1
''')

    def test_unreadable_database_is_not_overwritten(self):
        self.case(r'''
path=STATE_DIR/'normal-delivery.sqlite'
damaged=b'not a SQLite database; synthetic damaged header only'
path.write_bytes(damaged)
try:DeliveryState(STATE_DIR)
except DeliveryError as e:assert str(e)=='unreadable_delivery_database'
else:raise AssertionError('damaged database accepted')
assert path.read_bytes()==damaged
''')

    def test_interrupted_schema_creation_never_leaves_partly_valid_database(self):
        self.case(r'''
import delivery_state
real_connect=sqlite3.connect
class InterruptedConnection(sqlite3.Connection):
    def execute(self,sql,*args,**kwargs):
        if sql==delivery_state.SCHEMA[1]:raise KeyboardInterrupt()
        return super().execute(sql,*args,**kwargs)
def interrupted_connect(*args,**kwargs):
    return real_connect(*args,**dict(kwargs,factory=InterruptedConnection))
with patch.object(delivery_state.sqlite3,'connect',interrupted_connect):
    try:DeliveryState(STATE_DIR)
    except KeyboardInterrupt:pass
    else:raise AssertionError('expected interrupted setup')
path=STATE_DIR/'normal-delivery.sqlite'
with closing(real_connect(path)) as db:
    assert db.execute('PRAGMA application_id').fetchone()[0]==0
    assert not db.execute("SELECT 1 FROM sqlite_master WHERE type='table'").fetchone()
try:DeliveryState(STATE_DIR)
except DeliveryError as e:assert str(e)=='foreign_or_incomplete_delivery_database'
else:raise AssertionError('interrupted setup was silently reset')
''')

    def test_missing_cursor_or_binding_never_reinitializes_pending_queue(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args:False,now=10000)['error']=='delivery_unconfirmed'
path=STATE_DIR/'normal-delivery.sqlite'
calls=[]
for key in ('identity','initial_baseline','acked','collected'):
    with closing(sqlite3.connect(path)) as db:
        value=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()[0]
        db.execute('DELETE FROM meta WHERE key=?',(key,));db.commit()
    for operation in (sync.collect, lambda:sender.send_once(
            lambda *args:calls.append(args) or True,now=10060)):
        try:operation()
        except DeliveryError as e:assert str(e)=='corrupt_delivery_metadata'
        else:raise AssertionError('incomplete metadata accepted')
    with closing(sqlite3.connect(path)) as db:
        assert not db.execute('SELECT 1 FROM meta WHERE key=?',(key,)).fetchone()
        assert db.execute('SELECT acked FROM messages WHERE pk=102').fetchone()[0]==0
        assert db.execute('SELECT COUNT(*) FROM delivery').fetchone()[0]==1
        db.execute('INSERT INTO meta VALUES(?,?)',(key,value));db.commit()
assert not calls and status()['pending']==1 and status()['frozen']
''')

    def test_empty_metadata_with_retained_rows_is_not_a_new_database(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
path=STATE_DIR/'normal-delivery.sqlite'
with closing(sqlite3.connect(path)) as db:
    db.execute('DELETE FROM meta');db.commit()
try:sync.collect()
except DeliveryError as e:assert str(e)=='corrupt_delivery_metadata'
else:raise AssertionError('populated queue treated as uninitialized')
with closing(sqlite3.connect(path)) as db:
    assert db.execute('SELECT COUNT(*) FROM meta').fetchone()[0]==0
    assert db.execute('SELECT acked FROM messages WHERE pk=102').fetchone()[0]==0
''')

    def test_invalid_metadata_values_stop_without_advancing_or_http(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
path=STATE_DIR/'normal-delivery.sqlite'
calls=[]
for key,value in (('identity','invalid'),('collected','102.5'),
                  ('collected','-1'),('collected','100'),
                  ('acked','100'),('acked','9223372036854775808'),
                  ('success_at','nan'),('success_at','inf'),('success_at','-1')):
    with closing(sqlite3.connect(path)) as db:
        old=db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',(key,value));db.commit()
    try:sender.send_once(lambda *args:calls.append(args) or True,now=10000)
    except DeliveryError as e:assert str(e)=='corrupt_delivery_metadata'
    else:raise AssertionError('invalid cursor/binding/clock accepted')
    with closing(sqlite3.connect(path)) as db:
        assert db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone()[0]==value
        if old:db.execute('UPDATE meta SET value=? WHERE key=?',(old[0],key))
        else:db.execute('DELETE FROM meta WHERE key=?',(key,))
        db.commit()
assert not calls and status()['collected']==102 and status()['acknowledged']==101
assert status()['pending']==1
''')

    def test_parseable_but_wrong_frozen_ids_never_ack_unrelated_message(self):
        self.case(r'''
sync.collect();add(102,'synthetic source message');sync.collect()
assert sender.send_once(lambda *args:False,now=10000)['error']=='delivery_unconfirmed'
with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as db:
    db.execute("UPDATE delivery SET ids='[999]' WHERE singleton=1")
    db.commit()
calls=[]
try:sender.send_once(lambda *args:calls.append(args) or True,now=10060)
except DeliveryError as e:assert str(e)=='corrupt_frozen_delivery'
else:raise AssertionError('mismatched but parseable ids were accepted')
assert not calls
assert status()['pending']==1 and status()['acknowledged']==101
''')



    def test_http_wait_does_not_hold_sqlite_write_lock(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
def post(*args):
    with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite',timeout=1)) as db:
        db.execute("INSERT OR REPLACE INTO meta VALUES('probe','concurrent write')")
        db.commit()
    return True
assert sender.send_once(post,now=10000)['sent']
assert status()['pending']==0
''')

    def test_sender_lock_blocks_nested_sender_without_http(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
calls=[]
def post(*args):
    try:sender.send_once(lambda *a:calls.append(a) or True,now=10000)
    except DeliveryError as e:assert str(e)=='sender_already_running'
    else:raise AssertionError('second sender was not blocked')
    return True
assert sender.send_once(post,now=10000)['sent']
assert not calls and status()['pending']==0
''')

    def test_frozen_delivery_changed_during_http_is_not_acked(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
def post(*args):
    with closing(sqlite3.connect(STATE_DIR/'normal-delivery.sqlite')) as db:
        db.execute("UPDATE delivery SET attempts=attempts+1 WHERE singleton=1")
        db.commit()
    return True
try:sender.send_once(post,now=10000)
except DeliveryError as e:assert str(e)=='delivery_changed_during_http'
else:raise AssertionError('concurrent mutation was acknowledged')
assert status()['pending']==1 and status()['frozen']
''')

    def test_sender_lock_file_is_private_regular_file(self):
        self.case(r'''
import stat
sync.collect();add(102);sync.collect()
assert sender.send_once(lambda *args:True,now=10000)['sent']
path=STATE_DIR/'normal-sender.lock'
info=path.stat()
assert stat.S_ISREG(info.st_mode)
assert info.st_nlink==1
if os.name!='nt':assert stat.S_IMODE(info.st_mode)==0o600
''')

    def test_collector_can_add_new_messages_during_http(self):
        self.case(r'''
sync.collect();add(102);sync.collect()
add(103,'arrived before HTTP but not yet collected')
seen=[]
def post(*args):
    seen.append(sync.collect())
    return True
result=sender.send_once(post,now=10000)
assert result['sent'], (result,seen)
assert seen[0]['pending']==2,seen
assert status()['pending']==1 and status()['acknowledged']==102,status()
''')


if __name__ == '__main__':
    unittest.main()
