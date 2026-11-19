"""Synthetic regressions for committed visibility and scoped cursor reconciliation."""
import unittest
import test_delivery as delivery_fixtures


class ScopedCollectionRegression(unittest.TestCase):
    def setUp(self):
        self.fixture = delivery_fixtures.DurableDeliveryTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)

    def test_scoped_late_pk_and_filled_placeholder_preserve_ack(self):
        self.fixture.case(r'''
config['normal_line']['allowed_chat_pks'] = [2]
CONFIG_FILE.write_text(json.dumps(config))
sync.collect()  # Initial baseline 101 must remain excluded.
with closing(sqlite3.connect(database)) as db:
    db.execute('INSERT INTO ZMESSAGE VALUES(102,1735689600000,2,1,?)', ('',))
    db.execute('INSERT INTO ZMESSAGE VALUES(104,1735689600000,9,1,?)', ('other chat',))
    db.commit()
assert sync.collect()['pending'] == 0
with closing(sqlite3.connect(database)) as db:
    db.execute('UPDATE ZMESSAGE SET ZTEXT=? WHERE Z_PK=102', ('filled later',))
    db.execute('INSERT INTO ZMESSAGE VALUES(103,1735689600000,2,1,?)', ('late lower PK',))
    db.commit()
result = sync.collect()
assert result['pending'] == 2, result
assert sync.collect()['pending'] == 2
assert sender.send_once(lambda *args: True)['sent']
assert status()['pending'] == 0
assert sync.collect()['pending'] == 0  # ACK is not reverted by reconciliation.
add(105, 'new after ACK')
assert sync.collect()['pending'] == 1
queue = DeliveryState(STATE_DIR)
try:
    assert [r[0] for r in queue.pending_rows()] == ['105']
    assert queue.db.execute('SELECT COUNT(*) FROM messages WHERE pk IN (101,104)').fetchone()[0] == 0
finally:
    queue.close()
''')

    def test_fresh_readonly_connection_sees_wal_commit_without_checkpoint(self):
        self.fixture.case(r'''
config['normal_line']['allowed_chat_pks'] = [2]
CONFIG_FILE.write_text(json.dumps(config))
with closing(sqlite3.connect(database)) as writer:
    assert writer.execute('PRAGMA journal_mode=WAL').fetchone()[0] == 'wal'
    writer.execute('PRAGMA wal_autocheckpoint=0')
    sync.collect()
    writer.execute('INSERT INTO ZMESSAGE VALUES(102,1735689600000,2,1,?)', ('uncommitted then committed',))
    assert sync.collect()['pending'] == 0
    writer.commit()
    assert Path(str(database) + '-wal').stat().st_size > 0
    assert sync.collect()['pending'] == 1
    assert sync.collect()['pending'] == 1
''')

    def test_reconciliation_does_not_rewrite_acknowledged_content(self):
        self.fixture.case(r'''
config['normal_line']['allowed_chat_pks'] = [2]
CONFIG_FILE.write_text(json.dumps(config))
sync.collect()
add(102, 'original')
sync.collect()
assert sender.send_once(lambda *args: True)['sent']
with closing(sqlite3.connect(database)) as db:
    db.execute('UPDATE ZMESSAGE SET ZTEXT=? WHERE Z_PK=102', ('changed after ACK',))
    db.commit()
try:
    sync.collect()
    raise AssertionError('conflicting acknowledged content was accepted')
except DeliveryError:
    pass
queue = DeliveryState(STATE_DIR)
try:
    assert queue.status()['pending'] == 0
    row = queue.db.execute('SELECT raw,acked FROM messages WHERE pk=102').fetchone()
    assert row['acked'] == 1 and json.loads(row['raw'])[4] == 'original'
finally:
    queue.close()
''')


if __name__ == '__main__':
    unittest.main()
