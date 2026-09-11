import json
import tempfile
import unittest
from pathlib import Path

from sage_lantern.public_queue import ProposalOutbox


def envelope(envelope_id: str = "envelope-0001", content: str = "Post office opens at 09:00") -> str:
    return json.dumps(
        {
            "schema": "sage.commons.public-derivative.v1",
            "envelope_id": envelope_id,
            "claim": {"content": content},
            "privacy": {"classification": "PUBLIC"},
            "signature": {"algorithm": "Ed25519", "value": "test-signature"},
        }
    )


class ProposalOutboxTests(unittest.TestCase):
    def test_enqueue_is_durable_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "outbox.sqlite"
            outbox = ProposalOutbox(path)
            first, created = outbox.enqueue(envelope())
            second, created_again = outbox.enqueue(envelope())
            outbox.close()
            reopened = ProposalOutbox(path)
            persisted = reopened.get("envelope-0001")
            reopened.close()
            self.assertTrue(created)
            self.assertFalse(created_again)
            self.assertEqual(first, second)
            self.assertEqual(first, persisted)
            self.assertEqual(first.state, "QUEUED_LOCAL")

    def test_reused_id_with_different_bytes_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbox = ProposalOutbox(Path(tmp) / "outbox.sqlite")
            outbox.enqueue(envelope())
            with self.assertRaisesRegex(ValueError, "different canonical bytes"):
                outbox.enqueue(envelope(content="Changed claim"))
            outbox.close()

    def test_non_public_envelope_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = json.loads(envelope())
            data["privacy"]["classification"] = "PRIVATE"
            outbox = ProposalOutbox(Path(tmp) / "outbox.sqlite")
            with self.assertRaisesRegex(ValueError, "must be PUBLIC"):
                outbox.enqueue(json.dumps(data))
            outbox.close()

    def test_receipt_states_cannot_skip_submission_or_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbox = ProposalOutbox(Path(tmp) / "outbox.sqlite")
            outbox.enqueue(envelope())
            with self.assertRaisesRegex(ValueError, "QUEUED_LOCAL -> COMMITTED"):
                outbox.update_state("envelope-0001", "COMMITTED")
            for state in ("SENT", "RECEIVED", "QUARANTINED", "APPROVED", "SUBMITTED", "COMMITTED"):
                record = outbox.update_state("envelope-0001", state)
            self.assertEqual(record.state, "COMMITTED")
            outbox.close()


if __name__ == "__main__":
    unittest.main()
