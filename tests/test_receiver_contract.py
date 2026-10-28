"""A disposable *mock*, not a GAS deployment. No network or real messages."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1] / "normal-only-rc1"
sys.path.insert(0, str(APP))
import legacy_names
import presentation
import transport


class ContractError(ValueError):
    pass


class DurableMockReceiver:
    """Model only: atomic accepted batch and side-effect ledger in one SQLite DB.

    Real Gmail/Calendar side effects are NOT transactionally coupled to GAS storage.
    This test cannot demonstrate real exactly-once downstream delivery.
    """
    def __init__(self, db_path):
        self.db = sqlite3.connect(str(db_path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS accepted (
                source TEXT NOT NULL, batch_id TEXT NOT NULL,
                body_hash TEXT NOT NULL,
                PRIMARY KEY(source,batch_id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                message_id TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS effects (
                message_id TEXT PRIMARY KEY
            );
        """)

    def close(self):
        self.db.close()

    def accept(self, token, maxid, body):
        # Only synthetic secrets are used here. Production must authenticate
        # securely and persist accepted state before returning OK.
        if token != "OFFLINE_ONLY_TOKEN" or type(maxid) is not int:
            raise ContractError("unauthorized_or_invalid_request")
        if not isinstance(body, str) or len(body.encode("utf-8")) > 1024 * 1024:
            raise ContractError("invalid_body")
        header, sep, payload = body.partition("\n")
        if not sep or not payload:
            raise ContractError("empty_payload")
        manifest = transport.strict_json(header)
        required = {"v", "kind", "source", "batch_id", "part", "parts", "count", "sha256"}
        if not isinstance(manifest, dict) or set(manifest) != required:
            raise ContractError("invalid_manifest")
        records = presentation.decode_named(payload)
        source = records[0]["id"].rsplit(":", 1)[0]
        ids = [int(r["refs"]["message_pk"]) for r in records]
        digest = transport.digest(transport.records_bytes(records))
        batch_id = transport.digest((source + "\n" + digest).encode("utf-8"))
        if (manifest["v"] != 2 or manifest["kind"] != "line_batch" or
                manifest["source"] != source or
                type(manifest["part"]) is not int or manifest["part"] != 1 or
                type(manifest["parts"]) is not int or manifest["parts"] != 1 or
                type(manifest["count"]) is not int or manifest["count"] != len(records) or
                manifest["sha256"] != digest or manifest["batch_id"] != batch_id or
                len(set(ids)) != len(ids) or maxid != max(ids)):
            raise ContractError("payload_manifest_conflict")
        body_hash = transport.digest(body.encode("utf-8"))
        with self.db:
            prior = self.db.execute(
                "SELECT body_hash FROM accepted WHERE source=? AND batch_id=?",
                (source, batch_id)).fetchone()
            if prior:
                if prior[0] != body_hash:
                    raise ContractError("batch_replay_content_conflict")
                return "OK"
            for record in records:
                ident = record["id"]
                record_hash = transport.digest(transport.canonical(record).encode("utf-8"))
                existing = self.db.execute(
                    "SELECT content_hash FROM messages WHERE message_id=?",
                    (ident,)).fetchone()
                if existing and existing[0] != record_hash:
                    raise ContractError("message_id_content_conflict")
                if not existing:
                    self.db.execute("INSERT INTO messages VALUES (?,?)",
                                    (ident, record_hash))
                    self.db.execute("INSERT INTO effects VALUES (?)", (ident,))
            self.db.execute("INSERT INTO accepted VALUES (?,?,?)",
                            (source, batch_id, body_hash))
        return "OK"

    def effect_count(self):
        return self.db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]


def fixture(pk, text="Synthetic message — not a private chat"):
    return {
        "id": "OFFLINE_SOURCE:" + str(pk),
        "time": "2025-01-01T09:00:00.000+09:00",
        "chat": "Demo Group",
        "sender": "Demo User",
        "text": text,
        "refs": {
            "message_pk": str(pk), "chat_pk": "2", "sender_pk": "1",
            "chat_mid": "demo-group", "sender_mid": "demo-user"
        }
    }


def payload(records):
    return legacy_names.finalize_body(
        "".join(transport.canonical(record) + "\n" for record in records)
    )


class ReceiverContractTests(unittest.TestCase):
    def test_acceptance_before_ack_then_retry_and_restart_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="las_mock_receiver_") as directory:
            path = Path(directory) / "receiver.sqlite"
            body = payload([fixture(101), fixture(102)])
            with closing(DurableMockReceiver(path)) as receiver:
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 102, body), "OK")
                self.assertEqual(receiver.effect_count(), 2)
                # Imagine the client crashed before recording local ACK.
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 102, body), "OK")
                self.assertEqual(receiver.effect_count(), 2)
            with closing(DurableMockReceiver(path)) as receiver:
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 102, body), "OK")
                self.assertEqual(receiver.effect_count(), 2)

    def test_manifest_tampering_and_partial_payload_are_rejected(self):
        with tempfile.TemporaryDirectory(prefix="las_mock_receiver_") as directory:
            body = payload([fixture(101), fixture(102)])
            head, rest = body.split("\n", 1)
            manifest = json.loads(head)
            with closing(DurableMockReceiver(Path(directory) / "receiver.sqlite")) as receiver:
                for changed in ({"sha256": "0" * 64}, {"count": 1},
                                {"batch_id": "f" * 64}, {"source": "WRONG"}):
                    invalid = dict(manifest, **changed)
                    with self.assertRaises(ContractError):
                        receiver.accept("OFFLINE_ONLY_TOKEN", 102,
                                        transport.canonical(invalid) + "\n" + rest)
                with self.assertRaises(ContractError):
                    receiver.accept("WRONG_TOKEN", 102, body)
                with self.assertRaises(ContractError):
                    receiver.accept("OFFLINE_ONLY_TOKEN", 101, body)
                truncated = head + "\n" + rest.rsplit("\n", 2)[0] + "\n"
                with self.assertRaises((ContractError, ValueError)):
                    receiver.accept("OFFLINE_ONLY_TOKEN", 102, truncated)
                self.assertEqual(receiver.effect_count(), 0)

    def test_same_message_id_with_different_text_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="las_mock_receiver_") as directory:
            with closing(DurableMockReceiver(Path(directory) / "receiver.sqlite")) as receiver:
                self.assertEqual(receiver.accept(
                    "OFFLINE_ONLY_TOKEN", 101, payload([fixture(101)])), "OK")
                with self.assertRaises(ContractError):
                    receiver.accept("OFFLINE_ONLY_TOKEN", 101,
                                    payload([fixture(101, "changed text")]))
                self.assertEqual(receiver.effect_count(), 1)

    def test_receiver_can_decode_compact_c1_and_plain_b(self):
        with tempfile.TemporaryDirectory(prefix="las_mock_receiver_") as directory:
            with closing(DurableMockReceiver(Path(directory) / "receiver.sqlite")) as receiver:
                # Multiple shared names are eligible for compact C1.
                body = payload([fixture(i, "hello" * 3) for i in range(101, 110)])
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 109, body), "OK")
                self.assertEqual(receiver.effect_count(), 9)
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 110,
                                                payload([fixture(110)])), "OK")
                self.assertEqual(receiver.effect_count(), 10)


    def test_timestamp_order_is_independent_of_primary_key_order(self):
        with tempfile.TemporaryDirectory(prefix="las_mock_receiver_") as directory:
            older_pk = fixture(101)
            newer_pk = fixture(102)
            older_pk["time"] = "2025-01-01T09:02:00.000+09:00"
            newer_pk["time"] = "2025-01-01T09:01:00.000+09:00"
            body = payload([older_pk, newer_pk])
            decoded = presentation.decode_named(body.split("\n", 1)[1])
            self.assertEqual([r["refs"]["message_pk"] for r in decoded],
                             ["102", "101"])
            with closing(DurableMockReceiver(Path(directory) / "receiver.sqlite")) as receiver:
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 102, body), "OK")
                self.assertEqual(receiver.effect_count(), 2)
                self.assertEqual(receiver.accept("OFFLINE_ONLY_TOKEN", 102, body), "OK")
                self.assertEqual(receiver.effect_count(), 2)


if __name__ == "__main__":
    unittest.main()
