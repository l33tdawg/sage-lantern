import json
import ssl
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from sage_lantern.management import DeviceManagementIdentity, Observation, SignedObservation
from sage_lantern.management_transport import MTLSObservationClient, MTLSObservationConfig


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self.body = body

    def read(self, amount=None):
        return self.body[:amount]


class FakeConnection:
    response = None
    request_value = None
    closed = False

    def __init__(self, host, port, **kwargs):
        self.host = host
        self.port = port
        self.kwargs = kwargs

    def request(self, method, url, body, headers):
        type(self).request_value = (method, url, body, headers)

    def getresponse(self):
        return type(self).response

    def close(self):
        type(self).closed = True


class MTLSObservationClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.identity = DeviceManagementIdentity.load_or_create(root / "identity")
        self.ca = root / "ca.pem"
        self.cert = root / "client.pem"
        self.key = root / "client.key"
        for path in (self.ca, self.cert, self.key):
            path.write_text("test", encoding="utf-8")
        self.key.chmod(0o640)
        self.config = MTLSObservationConfig(
            endpoint="https://fleet-authority.example/v1/device/observations",
            ca_certificate=self.ca,
            client_certificate=self.cert,
            client_private_key=self.key,
        )
        observation = Observation.create(
            device_id="lantern-a",
            sequence=8,
            observed_at=datetime(2026, 9, 2, tzinfo=UTC),
            freshness_ttl=timedelta(minutes=5),
            clock_confidence="ntp-synchronized",
            source_fingerprint=self.identity.fingerprint,
            state={"temperature_millicelsius": 48_000},
        )
        self.signed = SignedObservation.create(observation, self.identity)

    def tearDown(self):
        self.temp.cleanup()

    def client(self):
        return MTLSObservationClient(
            self.config,
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            connection_factory=FakeConnection,
        )

    def test_posts_exact_path_without_redirects_and_checks_acknowledgement(self):
        observation = self.signed.observation
        FakeConnection.response = FakeResponse(
            202,
            json.dumps(
                {
                    "accepted": True,
                    "device_id": observation.device_id,
                    "sequence": observation.sequence,
                    "state_hash": observation.state_hash,
                    "ingested_at": "2026-09-02T00:00:01Z",
                }
            ).encode(),
        )
        acknowledgement = self.client().send(self.signed)
        method, path, body, headers = FakeConnection.request_value
        self.assertEqual((method, path), ("POST", "/v1/device/observations"))
        self.assertEqual(json.loads(body)["observation"]["device_id"], "lantern-a")
        self.assertIn("lantern-a:8:", headers["Idempotency-Key"])
        self.assertEqual(acknowledgement.sequence, 8)
        self.assertTrue(FakeConnection.closed)

    def test_rejects_redirect_or_mismatched_acknowledgement(self):
        FakeConnection.response = FakeResponse(302, b"")
        with self.assertRaisesRegex(ValueError, "HTTP 302"):
            self.client().send(self.signed)

        FakeConnection.response = FakeResponse(
            202,
            json.dumps(
                {
                    "accepted": True,
                    "device_id": "lantern-b",
                    "sequence": 8,
                    "state_hash": self.signed.observation.state_hash,
                    "ingested_at": "2026-09-02T00:00:01Z",
                }
            ).encode(),
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            self.client().send(self.signed)

    def test_rejects_insecure_endpoint_and_world_readable_key(self):
        insecure = MTLSObservationConfig(
            endpoint="http://fleet-authority.example/v1/device/observations",
            ca_certificate=self.ca,
            client_certificate=self.cert,
            client_private_key=self.key,
        )
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            insecure.validate()
        self.key.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "other users"):
            self.config.validate()

    def test_public_endpoint_uses_system_server_trust_not_client_issuer_ca(self):
        config = MTLSObservationConfig(
            endpoint=self.config.endpoint,
            ca_certificate=None,
            client_certificate=self.cert,
            client_private_key=self.key,
        )
        context = MagicMock()
        with patch("sage_lantern.management_transport.ssl.create_default_context", return_value=context) as create:
            MTLSObservationClient(config, connection_factory=FakeConnection)
        create.assert_called_once_with(cafile=None)
        context.load_cert_chain.assert_called_once_with(str(self.cert), str(self.key))


if __name__ == "__main__":
    unittest.main()
