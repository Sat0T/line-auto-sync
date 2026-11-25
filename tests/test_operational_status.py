"""New worker-state regressions with synthetic SQLite and no network."""
import unittest
import test_delivery as fixtures


class OperationalStatusTests(unittest.TestCase):
    def setUp(self):
        self.delivery = fixtures.DurableDeliveryTests()
        self.delivery.setUp()
        self.addCleanup(self.delivery.doCleanups)

    def test_skipped_attempts_and_unknown_results_are_not_reported_as_success(self):
        self.delivery.case(r'''
import cli, runner
sync.collect()
for report, expected in (
    ({'sent': False, 'reason': 'retry_wait'}, 'retry_wait'),
    ({'sent': False, 'reason': 'hourly_gate'}, 'hourly_gate'),
    ({'sent': False, 'reason': 'empty'}, 'empty'),
    ({'sent': False, 'reason': 'already_acknowledged'}, 'already_acknowledged'),
    ({'sent': False, 'reason': 'PRIVATE_SYNTHETIC_REMOTE_TEXT'}, 'delivery_not_attempted'),
    ({'sent': True, 'error': 'delivery_unconfirmed'}, 'delivery_unconfirmed'),
    ({'sent': True}, 'application_accepted'),
):
    def once():
        runner.request_stop()
        return report
    before = cli.queue_status()
    runner.start(cli.doctor, cli.queue_status, send=True, collect_step=lambda: None,
                 send_step=once, sleep=lambda _: None)
    observed = runner.read_marker('worker-status.json')
    assert observed['state'] == 'stopped'
    assert observed['last_result'] == expected, (expected, observed)
    assert 'PRIVATE_SYNTHETIC' not in json.dumps(observed)
    assert cli.queue_status() == before
''')

    def test_real_sender_failure_wait_recovery_and_gate_have_distinct_durable_states(self):
        self.delivery.case(r'''
import cli, runner
sync.collect(); add(102); sync.collect()
times=iter((10000,10001,10060,10061))
cycle=[0]; seen=[]; posted=[]; frozen=[]
def post(*args):
    posted.append(args)
    return len(posted) == 2
def attempt():
    cycle[0] += 1
    result=sender.send_once(post, now=next(times))
    q=DeliveryState(STATE_DIR)
    try:
        row=q.db.execute('SELECT body,ids,batch_id FROM delivery').fetchone()
        frozen.append(tuple(row) if row else None)
    finally:q.close()
    if cycle[0] == 4:runner.request_stop()
    return result
last=[0]
def sleep(_):
    if last[0] != cycle[0]:
        seen.append(runner.read_marker('worker-status.json')['last_result'])
        last[0]=cycle[0]
runner.start(cli.doctor, cli.queue_status, send=True, resume_pending=True,
             send_step=attempt, sleep=sleep)
seen.append(runner.read_marker('worker-status.json')['last_result'])
assert seen == ['delivery_unconfirmed','retry_wait','application_accepted','hourly_gate'], seen
assert len(posted)==2 and posted[0]==posted[1]
assert frozen[0] == frozen[1] and frozen[2:] == [None,None]
q=DeliveryState(STATE_DIR)
try:
    assert q.status()['pending']==0
    assert q.db.execute('SELECT acked FROM messages WHERE pk=102').fetchone()[0]==1
finally:q.close()
''')

    def test_stop_during_retry_wait_retains_pending_frozen_request_without_another_post(self):
        self.delivery.case(r'''
import cli, runner
sync.collect(); add(102); sync.collect()
posts=[]; ticks=[0]
def attempt():
    ticks[0] += 1
    return sender.send_once(lambda *a: posts.append(a) or False,
                            now=10000 + ticks[0]-1)
def sleep(_):
    if ticks[0] == 2:runner.request_stop()
runner.start(cli.doctor, cli.queue_status, send=True, resume_pending=True,
             send_step=attempt, sleep=sleep)
assert len(posts)==1 and ticks[0]==2
assert cli.queue_status()['pending']==1 and cli.queue_status()['frozen'] is True
state=runner.read_marker('worker-status.json')
assert state['state']=='stopped' and state['last_result']=='retry_wait'
q=DeliveryState(STATE_DIR)
try:
    row=q.db.execute('SELECT body,attempts,next_attempt FROM delivery').fetchone()
    assert row['body']==posts[0][2] and row['attempts']==1 and row['next_attempt']==10060
    assert q.db.execute('SELECT acked FROM messages WHERE pk=102').fetchone()[0]==0
finally:q.close()
''')


if __name__ == '__main__':
    unittest.main()
