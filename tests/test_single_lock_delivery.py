"""N1 regression checks using disposable LINE-shaped SQLite and HTTP doubles."""
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
import signal
import unittest
from unittest.mock import patch
import test_delivery as fixtures

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
SPEC = importlib.util.spec_from_file_location('single_lock', TOOLS / 'keep_lock_delivery.py')
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)

SETUP = r'''
import keep_lock_delivery as trial
from unittest.mock import Mock
root=STATE_DIR.parent
config.update(source_id='MVP_IPAD_LIVE', enabled=True)
config['normal_line']['allowed_chat_pks']=[2]
config['transport']['endpoint']='https://script.google.com/macros/s/SYNTHETIC_ONLY/exec'
CONFIG_FILE.write_text(json.dumps(config))
baseline=root/'baseline'
(baseline/'config').mkdir(parents=True)
original=dict(config, enabled=False)
raw=json.dumps(original).encode()
(baseline/'config/config.json').write_bytes(raw)
plan=dict(text=trial.TEXT,source='MVP_IPAD_LIVE',chat_pk='2',baseline=str(baseline),
          baseline_config_sha256=trial.digest(raw),initial_baseline=101,code_sha256={},
          endpoint_sha256=trial.digest(config['transport']['endpoint'].encode()))
trial.new_json(root/'plan.json',plan)
def queue_target():
    sync.collect()
    add(102, 'unapproved old text')
    add(103, trial.TEXT)
    with closing(sqlite3.connect(database)) as db:
        db.execute('INSERT INTO ZMESSAGE VALUES(104,1735689600000,9,1,?)',(trial.TEXT,))
        db.commit()
    sync.collect()
    trial.new_json(root/'target-private.json',dict(target_pk='103'))
    return trial.freeze_target(root,config,plan,'103')
'''


class SingleLockDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.DurableDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def case(self, code):
        self.fixture.case('import sys;sys.path.insert(0,' + repr(str(TOOLS)) + ')\n' + SETUP + code)

    def test_final_payload_is_one_exact_target_and_unrelated_queue_is_retained(self):
        self.case(r'''
manifest=queue_target()
body=trial.read_json(root/'payload-private.json')['body']
assert trial.validate_payload(config,plan,'103',body,103)==manifest
calls=[]
result=sender.send_once(lambda endpoint,token,body,maxid:
    calls.append((body,maxid)) or trial.validate_payload(config,plan,'103',body,maxid) is not None)
assert result['sent'] and result['pending']==1
assert calls[0][1]==103 and len(calls)==1
q=DeliveryState(STATE_DIR)
assert q.pending_rows()[0][0]=='102'
assert q.db.execute('SELECT COUNT(*) FROM messages WHERE pk=104').fetchone()[0]==0
assert q.db.execute('SELECT acked FROM messages WHERE pk=103').fetchone()[0]==1
q.close()
''')

    def test_changed_chat_text_id_source_manifest_or_destination_is_rejected(self):
        self.case(r'''
queue_target()
import presentation,transport
frozen=trial.read_json(root/'payload-private.json')
record=presentation.decode_named(frozen['body'].partition('\n')[2])[0]
def encode(r):
    digest=transport.digest(transport.records_bytes([r]))
    header=dict(frozen['manifest'],sha256=digest,batch_id=transport.digest((plan['source']+'\n'+digest).encode()))
    return transport.canonical(header)+'\n'+presentation.encode_named([r])['payload']
import copy
variants=[]
for field,value in [('text','old trial'),('id','MVP_IPAD_LIVE:999')]:
    r=copy.deepcopy(record);r[field]=value;variants.append(r)
for field,value in [('chat_pk','9'),('message_pk','999')]:
    r=copy.deepcopy(record);r['refs'][field]=value
    if field=='message_pk':r['id']='MVP_IPAD_LIVE:999'
    variants.append(r)
for r in variants:
    try:
        body=encode(r)
        trial.validate_payload(config,plan,'103',body,103)
    except Exception:pass
    else:raise AssertionError('changed payload accepted')
wrong=copy.deepcopy(config);wrong['transport']['endpoint']+='changed'
try:trial.validate_payload(wrong,plan,'103',frozen['body'],103)
except RuntimeError:pass
else:raise AssertionError('wrong endpoint accepted')
try:trial.validate_payload(config,plan,'102',frozen['body'],103)
except RuntimeError:pass
else:raise AssertionError('wrong pinned ID accepted')
''')

    def test_scope_snapshot_and_last_absence_do_not_write_line(self):
        self.case(r'''
from observe_line_db import Observer
before=database.read_bytes()
observer=Observer(database,2,trial.TEXT,101)
absent,rows=trial.target_snapshot(observer,2,trial.TEXT)
assert absent['marker_rows']==0 and absent['identity_checked'] and not rows
assert database.read_bytes()==before
add(102,trial.TEXT)
present,rows=trial.target_snapshot(observer,2,trial.TEXT)
assert present['target_pk']=='102' and present['marker_rows']==1
assert present['read_started_utc']>=absent['read_finished_utc']
assert present['files']['db']['readable']
observer.close()
''')

    def test_prepare_preserves_old_profile_and_refuses_existing_or_preexisting_target(self):
        self.case(r'''
old_config=CONFIG_FILE.read_bytes()
original['enabled']=False
import tempfile,atexit
external=tempfile.TemporaryDirectory(prefix='las_new_trial_')
atexit.register(external.cleanup)
new=Path(external.name)/'fresh-trial'
trial.prepare(baseline,new,Path(sync.__file__).parent)
assert CONFIG_FILE.read_bytes()==old_config
assert trial.read_json(new/'config/config.json')['enabled'] is False
assert trial.read_json(new/'plan.json')['send_max']==1
assert trial.read_json(new/'plan.json')['retries']==0
try:trial.prepare(baseline,new,Path(sync.__file__).parent)
except RuntimeError:pass
else:raise AssertionError('existing trial overwritten')
add(102,trial.TEXT)
try:trial.prepare(baseline,Path(external.name)/'another-trial',Path(sync.__file__).parent)
except RuntimeError:pass
else:raise AssertionError('pre-existing text allowed')
assert not (Path(external.name)/'another-trial').exists()
''')

    def test_stop_deadline_and_prior_lease_never_start_another_process(self):
        self.case(r'''
queue_target()
frozen=trial.read_json(root/'payload-private.json')
with patch.object(trial.subprocess,'Popen') as start:
    post=trial.one_post(root,plan,time.monotonic()+10,lambda:True)
    assert post(config['transport']['endpoint'],config['transport']['token'],frozen['body'],103) is False
    post=trial.one_post(root,plan,time.monotonic()-1,lambda:False)
    assert post(config['transport']['endpoint'],config['transport']['token'],frozen['body'],103) is False
    assert start.call_count==0
class Done:
    returncode=0
    def poll(self):return 0
def completed(*args,**kwargs):
    trial.new_json(root/'http-result.json',dict(response_ok=False))
    return Done()
with patch.object(trial.subprocess,'Popen',side_effect=completed) as start:
    post=trial.one_post(root,plan,time.monotonic()+10,lambda:False)
    assert post(config['transport']['endpoint'],config['transport']['token'],frozen['body'],103) is False
    try:post(config['transport']['endpoint'],config['transport']['token'],frozen['body'],103)
    except FileExistsError:pass
    else:raise AssertionError('reserved attempt restarted')
    assert start.call_count==1
''')

    def test_actual_http_child_requires_strict_ok_and_still_does_not_claim_mail_delivery(self):
        self.case(r'''
queue_target()
trial.new_json(root/'http-lease.json',dict(parent_pid=os.getppid(),send_start_deadline_monotonic=time.monotonic()+10,completion_deadline_monotonic=time.monotonic()+30))
calls=[]
class Reply:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,limit):return b'OK\n'
class Opener:
    def open(self,request,timeout):
        value=json.loads(request.data)
        assert value['maxid']=='103'
        trial.validate_payload(config,plan,'103',value['body'],value['maxid'])
        calls.append(value)
        return Reply()
with patch('urllib.request.build_opener',return_value=Opener()):trial.http_child(root)
assert len(calls)==1
assert trial.read_json(root/'http-result.json')['response_ok'] is True
events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
assert events[-1]['event']=='http_finished' and events[-1]['http_status']==200
assert 'gmail_delivered' not in events[-1]
try:
    with patch('urllib.request.build_opener',return_value=Opener()):trial.http_child(root)
except FileExistsError:pass
else:raise AssertionError('network restarted')
assert len(calls)==1
''')

    def test_http_2xx_without_ok_is_not_application_acceptance(self):
        self.case(r'''
queue_target()
trial.new_json(root/'http-lease.json',dict(parent_pid=os.getppid(),send_start_deadline_monotonic=time.monotonic()+10,completion_deadline_monotonic=time.monotonic()+30))
class Reply:
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,_):return b'NOT OK'
class Opener:
    def open(self,*args,**kwargs):return Reply()
with patch('urllib.request.build_opener',return_value=Opener()):trial.http_child(root)
assert trial.read_json(root/'http-result.json')['response_ok'] is False
''')

    def test_delayed_child_cannot_open_http_after_send_start_deadline(self):
        self.case(r'''
queue_target()
trial.new_json(root/'http-lease.json',dict(parent_pid=os.getppid(),send_start_deadline_monotonic=10,completion_deadline_monotonic=30))
clock=[0]
opener=Mock()
def delayed_validation(*args):
    clock[0]=11
    return opener
with patch.object(trial.time,'monotonic',side_effect=lambda:clock[0]),patch('urllib.request.build_opener',side_effect=delayed_validation):
    trial.http_child(root)
assert opener.open.call_count==0
result=trial.read_json(root/'http-result.json')
assert result['response_ok'] is False and result['response_received_utc'] is None
assert result['request_finished_utc']
events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
assert not any(event['event']=='http_started' for event in events)
''')

    def test_connection_failure_does_not_invent_response_received_time(self):
        self.case(r'''
queue_target()
trial.new_json(root/'http-lease.json',dict(parent_pid=os.getppid(),send_start_deadline_monotonic=time.monotonic()+10,completion_deadline_monotonic=time.monotonic()+30))
opener=Mock()
opener.open.side_effect=TimeoutError('synthetic timeout')
with patch('urllib.request.build_opener',return_value=opener):trial.http_child(root)
result=trial.read_json(root/'http-result.json')
assert result['response_ok'] is False and result['response_received_utc'] is None
assert result['request_finished_utc']
''')

    @unittest.skipUnless(os.name == 'posix', 'real parent-symlink containment is checked on POSIX CI')
    def test_prepare_rejects_alias_into_line_database_directory(self):
        self.case(r'''
import tempfile
with tempfile.TemporaryDirectory() as parent:
    alias=Path(parent)/'alias'
    alias.symlink_to(root,target_is_directory=True)
    try:trial.prepare(baseline,alias/'new-trial',Path(sync.__file__).parent)
    except RuntimeError:pass
    else:raise AssertionError('resolved LINE directory containment bypassed')
    assert not (root/'new-trial').exists()
''')

    def test_post_unlock_is_read_only_once_and_cannot_send_or_collect(self):
        self.case(r'''
config['enabled']=False
CONFIG_FILE.write_text(json.dumps(config))
trial.new_json(root/'finished.json',dict(state='stopped'))
add(102,trial.TEXT)
before=CONFIG_FILE.read_bytes()
with patch.object(sender,'send_once',side_effect=AssertionError('no send')),patch.object(sync,'collect',side_effect=AssertionError('no collect')):
    result=trial.post_unlock_once(root)
assert result['target_pk']=='102' and result['marker_rows']==1
assert CONFIG_FILE.read_bytes()==before
try:trial.post_unlock_once(root)
except FileExistsError:pass
else:raise AssertionError('post-unlock read repeated')
''')

    def test_worker_adopts_one_new_id_collects_freezes_and_stops_without_claiming_gmail(self):
        self.case(r'''
from contextlib import nullcontext
sync.collect()
original_snapshot=trial.target_snapshot
ticks=[0]
def incoming(*args,**kwargs):
    ticks[0]+=1
    if ticks[0]==2:
        add(102,'another new message')
        add(103,trial.TEXT)
    return original_snapshot(*args,**kwargs)
received=[]
def factory(base,plan,deadline,stopped):
    def post(endpoint,token,body,maxid):
        assert not stopped()
        trial.validate_payload(config,plan,'103',body,maxid)
        received.append(body)
        return True
    return post
with patch.object(trial,'target_snapshot',side_effect=incoming),patch.object(trial,'one_post',side_effect=factory):
    trial.worker(root,power=nullcontext)
assert len(received)==1
target=trial.read_json(root/'target-private.json')
assert target['target_pk']=='103' and target['last_absent']['marker_rows']==0
assert target['first_present']['target_pk']=='103'
events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
assert events[-1]['reason']=='application_accepted_mail_unverified'
assert events[-2]['gmail_delivery']=='unverified'
assert trial.read_json(CONFIG_FILE)['enabled'] is False
q=DeliveryState(STATE_DIR)
assert q.status()['pending']==1
assert q.pending_rows()[0][0]=='102'
q.close()
''')

    def test_worker_deadline_with_no_target_never_sends_and_keeps_disabled(self):
        self.case(r'''
from contextlib import nullcontext
sync.collect()
clock=[0]
def wait(_):clock[0]=trial.OBSERVE_SECONDS+1
with patch.object(trial.time,'monotonic',side_effect=lambda:clock[0]),patch.object(trial.time,'sleep',side_effect=wait),patch.object(trial,'one_post',side_effect=AssertionError('must not send')):
    trial.worker(root,power=nullcontext)
events=[json.loads(line) for line in (root/'events.jsonl').read_text().splitlines()]
assert events[-1]['reason']=='deadline_no_target'
assert trial.read_json(CONFIG_FILE)['enabled'] is False
assert not (root/'target-private.json').exists()
''')

    def test_stop_during_http_preserves_frozen_queue_and_never_acks(self):
        self.case(r'''
queue_target()
from types import SimpleNamespace
child=SimpleNamespace(poll=lambda:None,kill=Mock(),wait=Mock(return_value=0))
calls=[0]
def stopped():
    calls[0]+=1
    return calls[0]>1
post=trial.one_post(root,plan,time.monotonic()+10,stopped)
with patch.object(trial.subprocess,'Popen',return_value=child) as popen:
    result=sender.send_once(post)
assert not result['sent'] and result['frozen'] and result['pending']==2
assert popen.call_count==1 and child.kill.call_count==1
q=DeliveryState(STATE_DIR)
assert q.db.execute('SELECT acked FROM messages WHERE pk=103').fetchone()[0]==0
assert q.db.execute('SELECT attempts FROM delivery').fetchone()[0]==1
q.close()
''')

    def test_group_cleanup_covers_orphan_http_even_after_worker_leader_exit(self):
        child = SimpleNamespace(pid=45678, poll=lambda: 0, wait=lambda timeout: 0)
        with patch.object(trial.os, 'killpg', create=True) as kill, \
                patch.object(trial.time, 'monotonic', side_effect=[0, 0, 6]), \
                patch.object(trial.time, 'sleep'):
            trial.end_group(child)
        self.assertEqual(kill.call_args_list[0].args, (45678, signal.SIGTERM))
        self.assertEqual(kill.call_args_list[-1].args, (45678, trial.KILL_SIGNAL))


if __name__ == '__main__':
    unittest.main()
