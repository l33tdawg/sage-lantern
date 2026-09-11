import json
import unittest
from datetime import UTC, datetime, timedelta

from sage_lantern.envelope import LanternEnvelope, SCHEMA


class EnvelopeTests(unittest.TestCase):
    def make_envelope(self, **overrides):
        values = {
            "schema": SCHEMA,
            "message_id": "msg-1",
            "source_agent_id": "a" * 64,
            "destination": "b" * 64,
            "sequence": 1,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "payload_type": "knowledge.note",
            "body": "hello",
            "ttl_seconds": 60,
            "integrity": "signature-placeholder",
        }
        values.update(overrides)
        return LanternEnvelope(**values)

    def test_round_trip_and_canonical_order(self):
        envelope = self.make_envelope()
        raw = envelope.canonical_json()
        self.assertEqual(LanternEnvelope.from_json(raw), envelope)
        self.assertEqual(list(json.loads(raw)), sorted(json.loads(raw)))

    def test_rejects_oversize_body(self):
        with self.assertRaisesRegex(ValueError, "byte limit"):
            self.make_envelope(body="x" * 1025).validate()

    def test_rejects_expired_envelope(self):
        created = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
        raw = self.make_envelope(created_at=created, ttl_seconds=30).canonical_json()
        with self.assertRaisesRegex(ValueError, "expired"):
            LanternEnvelope.from_json(raw)

    def test_rejects_unknown_fields(self):
        data = json.loads(self.make_envelope().canonical_json())
        data["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "fields"):
            LanternEnvelope.from_json(json.dumps(data))


if __name__ == "__main__":
    unittest.main()

