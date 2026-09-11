import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sage_lantern.device_agent import DeviceObservationAgent, ObservationOutbox, SanitizedJSONStatusCollector
from sage_lantern.management import DeviceManagementIdentity
from sage_lantern.management_transport import IngestAcknowledgement


NOW = datetime(2026, 9, 2, 10, 0, tzinfo=UTC)


class FakeClient:
    def __init__(self, failures=0):
        self.failures = failures
        self.sent = []

    def send(self, signed):
        self.sent.append(signed)
        if self.failures:
            self.failures -= 1
            raise OSError("offline")
        value = signed.observation
        return IngestAcknowledgement(True, value.device_id, value.sequence, value.state_hash, NOW.isoformat())


class StaticCollector:
    def __init__(self):
        self.calls = 0

    def collect(self):
        self.calls += 1
        return {"status": "ready", "services": {"runtime": "ready"}}


class DeviceAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = DeviceManagementIdentity.load_or_create(self.root / "identity")

    def tearDown(self):
        self.temp.cleanup()

    def test_failure_retries_exact_signed_observation_after_restart(self):
        clock = [NOW]
        collector = StaticCollector()
        client = FakeClient(failures=1)
        path = self.root / "agent" / "observations.sqlite3"
        outbox = ObservationOutbox(path)
        agent = DeviceObservationAgent(
            device_id="lantern-a", identity=self.identity, collector=collector, outbox=outbox,
            client=client, base_backoff_seconds=5, max_backoff_seconds=30, now=lambda: clock[0],
        )
        with self.assertRaises(OSError):
            agent.run_once()
        first_json = client.sent[0].to_json()
        self.assertEqual(outbox.last_sequence, 1)
        outbox.close()

        clock[0] += timedelta(seconds=5)
        reopened = ObservationOutbox(path)
        agent = DeviceObservationAgent(
            device_id="lantern-a", identity=self.identity, collector=collector, outbox=reopened,
            client=client, base_backoff_seconds=5, max_backoff_seconds=30, now=lambda: clock[0],
        )
        self.assertTrue(agent.run_once())
        self.assertEqual(client.sent[1].to_json(), first_json)
        self.assertEqual(collector.calls, 1)
        self.assertIsNone(reopened.pending())
        clock[0] += timedelta(seconds=1)
        self.assertTrue(agent.run_once())
        self.assertEqual(client.sent[2].observation.sequence, 2)
        reopened.close()

    def test_backoff_prevents_early_retry(self):
        clock = [NOW]
        outbox = ObservationOutbox(self.root / "agent" / "observations.sqlite3")
        client = FakeClient(failures=1)
        agent = DeviceObservationAgent(
            device_id="lantern-a", identity=self.identity, collector=StaticCollector(), outbox=outbox,
            client=client, base_backoff_seconds=10, max_backoff_seconds=30, now=lambda: clock[0],
        )
        with self.assertRaises(OSError): agent.run_once()
        clock[0] += timedelta(seconds=9)
        self.assertFalse(agent.run_once())
        self.assertEqual(len(client.sent), 1)
        outbox.close()

    def test_expired_pending_sequence_is_burned_not_reused(self):
        clock = [NOW]
        outbox = ObservationOutbox(self.root / "agent" / "observations.sqlite3")
        collector = StaticCollector()
        client = FakeClient(failures=1)
        agent = DeviceObservationAgent(
            device_id="lantern-a", identity=self.identity, collector=collector, outbox=outbox,
            client=client, freshness_ttl=timedelta(seconds=2), base_backoff_seconds=5,
            max_backoff_seconds=30, now=lambda: clock[0],
        )
        with self.assertRaises(OSError): agent.run_once()
        clock[0] += timedelta(seconds=3)
        self.assertTrue(agent.run_once())
        self.assertEqual([item.observation.sequence for item in client.sent], [1, 2])
        self.assertEqual(outbox.last_sequence, 2)
        outbox.close()

    def test_status_adapter_allowlists_fields_and_rejects_secrets(self):
        path = self.root / "status.json"
        path.write_text(json.dumps({"services":{"runtime":"ready"},"resources":{"memory_mb":512}}))
        self.assertEqual(SanitizedJSONStatusCollector(path).collect()["services"]["runtime"], "ready")
        path.write_text(json.dumps({"services":{"api_token":"nope"}}))
        with self.assertRaisesRegex(ValueError, "prohibited"):
            SanitizedJSONStatusCollector(path).collect()
        path.write_text(json.dumps({"personal_memories":[]}))
        with self.assertRaisesRegex(ValueError, "unsupported"):
            SanitizedJSONStatusCollector(path).collect()


if __name__ == "__main__":
    unittest.main()
