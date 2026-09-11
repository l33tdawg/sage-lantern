"""Pure schema and bounded ASCII expansion checks."""

import copy
import unittest
from uuid import uuid4

from sage_lantern import conversation_records as records
from sage_lantern.conversation_store import ConversationStoreError


class ConversationRecordTests(unittest.TestCase):
    def setUp(self):
        self.actor = dict(chain_id="private", agent_id="a" * 64)
        self.item = dict(session_id=str(uuid4()), created_at="2026-09-06T10:00:00Z",
                         question="Question", answer="Answer", reflection="Unverified reflection", has_photo=True)
        self.record = dict(schema=records.SESSION_SCHEMA, binding=self.actor, session_id=self.item["session_id"],
            item=self.item, item_hash=records.checksum(self.item), consent_generation=str(uuid4()), history_generation=None,
            dispatch_epoch=str(uuid4()), clear_epoch=str(uuid4()), operations={kind: records.new_operation()
                for kind in ("interaction", "reflection")}, media=None)

    def test_ids_bind_actor_and_session_while_legacy_operation_ids_remain_stable(self):
        control = records.record_id(self.actor)
        session = records.record_id(self.actor, self.item["session_id"])
        self.assertNotEqual(control, session)
        self.assertNotEqual(session, records.record_id({**self.actor, "chain_id": "other"}, self.item["session_id"]))
        self.assertNotEqual(records.operation_id(self.item["session_id"], "interaction"),
                            records.operation_id(self.item["session_id"], "reflection"))

    def test_schema_rejects_changed_text_and_unbacked_terminal_state(self):
        records.validate_session(self.record, self.actor)
        changed = copy.deepcopy(self.record)
        changed["item"]["answer"] = "Changed"
        with self.assertRaises(ConversationStoreError):
            records.validate_session(changed, self.actor)
        changed = copy.deepcopy(self.record)
        changed["operations"]["interaction"]["state"] = "accepted"
        with self.assertRaises(ConversationStoreError):
            records.validate_session(changed, self.actor)

    def test_receipt_reserve_budget_covers_worst_escaped_printable_fields(self):
        records.capacity(self.record, reserve_receipts=True)
        for operation in self.record["operations"].values():
            operation.update(state="accepted", owner=str(uuid4()), journal_key="a" * 64, fingerprint="b" * 64,
                             memory_id="\\" * 256, sage_status="committed_unconfirmed", deprecation_status="blocked_permission")
            operation["receipt"] = dict(operation_key="a" * 64, memory_id="\\" * 256, tx_hash="\\" * 256,
                status="committed_unconfirmed", committed=False, committed_height=2 ** 53 - 1,
                projection_confirmed=False, retryable=False, outcome="projection_observed")
        records.validate_session(self.record, self.actor)

    def test_utf8_size_is_not_ascii_encrypted_payload_size(self):
        self.record["item"]["answer"] = "é" * 3000
        records.validate_item(self.record["item"])
        with self.assertRaises(ConversationStoreError):
            records.capacity(self.record, reserve_receipts=True)

    def test_receipt_controls_and_unbounded_fields_rejected(self):
        receipt = dict(operation_key="a" * 64, memory_id="memory", tx_hash="tx", status="proposed", committed=True,
                       committed_height=1, projection_confirmed=False, retryable=False, outcome="acknowledged")
        for field, value in (("tx_hash", "\x00"), ("memory_id", "x" * 257), ("committed_height", True),
                             ("committed_height", 2 ** 53), ("retryable", 1)):
            with self.subTest(field=field), self.assertRaises(ConversationStoreError):
                records.validate_receipt({**receipt, field: value}, "a" * 64)


if __name__ == "__main__":
    unittest.main()
