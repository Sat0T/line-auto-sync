"""Bounded lock trial guards; disposable SQLite, no Apple frameworks or network."""
from contextlib import closing, nullcontext
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

SPEC = importlib.util.spec_from_file_location(
    'keep_power_lock', Path(__file__).resolve().parents[1] / 'tools/keep_power_lock.py')
trial = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trial)


class LockDeliveryTests(unittest.TestCase):
    def exercise(self, texts, payload_text=None, disabled=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / 'line.sqlite'
            with closing(sqlite3.connect(database)) as db:
                db.execute('CREATE TABLE ZMESSAGE (ZCHAT INTEGER, ZTEXT TEXT)')
                db.execute('INSERT INTO ZMESSAGE VALUES (?,?)', (7, trial.TEXT))
                db.commit()
            with closing(sqlite3.connect(root / 'normal-delivery.sqlite')) as db:
                db.execute('CREATE TABLE messages (raw TEXT, acked INTEGER)')
                db.executemany('INSERT INTO messages VALUES (?,0)',
                               [(json.dumps([1, 1, '7', None, text]),) for text in texts])
                db.commit()
            config = {'line_db': str(database), 'transport': {'endpoint': 'https://example.invalid'}}
            runtime = SimpleNamespace(STATE_DIR=root, allowed_chat_pks=lambda _: [7],
                                      require_enabled=Mock(return_value=config))
            if disabled:
                runtime.require_enabled.side_effect = [config, RuntimeError('disabled')]
            sync = SimpleNamespace(collect=Mock(return_value={'pending': len(texts)}))
            post = Mock(return_value=True)
            def send(guard):
                return {'sent': guard(config['transport']['endpoint'], 'dummy',
                                      '{"batch_id":"synthetic"}\npayload', 1), 'pending': 0}
            sender = SimpleNamespace(post_legacy=post, send_once=Mock(side_effect=send))
            decode = lambda _: [{'text': payload_text or trial.TEXT, 'refs': {'chat_pk': '7'}}]
            log = Mock()
            clock = [0]
            def sleep(_):
                clock[0] += trial.SECONDS + 1
            with patch.object(trial, 'stay_awake', nullcontext), patch.object(trial.signal, 'signal'), \
                    patch.object(trial, 'freeze_trial', return_value=trial.TEXT in texts), \
                    patch.object(trial.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(trial.time, 'sleep', side_effect=sleep):
                if payload_text and payload_text != trial.TEXT or disabled:
                    with self.assertRaises(RuntimeError):
                        trial.run(runtime, sync, sender, decode, log)
                else:
                    trial.run(runtime, sync, sender, decode, log)
            return post.call_count, sync.collect.call_count, log

    def test_only_approved_one_is_delivered(self):
        calls, collected, log = self.exercise([trial.TEXT])
        self.assertEqual((calls, collected), (1, 1))
        self.assertEqual(log.call_args_list[-1].args, ('delivery',))

    def test_mixed_queue_can_deliver_selected_trial(self):
        calls, _, log = self.exercise([trial.TEXT, 'another message'])
        self.assertEqual(calls, 1)
        self.assertEqual(log.call_args_list[-1].args, ('delivery',))

    def test_other_message_does_not_end_trial_early(self):
        calls, _, log = self.exercise(['another message'])
        self.assertEqual(calls, 0)
        self.assertEqual(log.call_args_list[-1].args, ('waiting_for_trial',))

    def test_changed_rendered_payload_is_rejected(self):
        calls, _, _ = self.exercise([trial.TEXT], payload_text='another message')
        self.assertEqual(calls, 0)

    def test_disabling_stops_before_collection_or_send(self):
        calls, collected, _ = self.exercise([trial.TEXT], disabled=True)
        self.assertEqual((calls, collected), (0, 0))

    def test_profile_digest_binds_destination_and_allows_enable_toggle(self):
        config = {'enabled': False, 'transport': {'endpoint': 'https://example.invalid/a'}}
        digest = trial.config_digest(config)
        self.assertEqual(digest, trial.config_digest(dict(config, enabled=True)))
        config['transport']['endpoint'] += 'changed'
        self.assertNotEqual(digest, trial.config_digest(config))

    def test_real_queue_acknowledges_only_selected_trial(self):
        from test_delivery import DurableDeliveryTests
        fixture = DurableDeliveryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        tools_path = str(Path(__file__).resolve().parents[1] / 'tools')
        fixture.case('import sys; sys.path.insert(0, ' + repr(tools_path) + ')\n' + r'''
import keep_power_lock as trial
import runtime_paths as runtime
config['normal_line']['allowed_chat_pks'] = [2]
CONFIG_FILE.write_text(json.dumps(config))
sync.collect()
add(102, 'unapproved extra message')
add(103, trial.TEXT)
sync.collect()
assert trial.freeze_trial(runtime, sender, config)
received = []
report = sender.send_once(lambda endpoint, token, body, last: received.append((body, last)) or True)
assert report['sent'] and report['pending'] == 1 and not report['frozen'], report
assert received[0][1] == 103
from presentation import decode_named
records = decode_named(received[0][0].partition('\n')[2])
assert len(records) == 1 and records[0]['text'] == trial.TEXT
queue = DeliveryState(STATE_DIR)
try:
    assert queue.pending_rows()[0][0] == '102'
    assert len(queue.pending_rows()) == 1
finally:
    queue.close()
''')


if __name__ == '__main__':
    unittest.main()
