import base64
import json
import tempfile
import unittest
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from nacl.signing import SigningKey

from sage_lantern.management import (
    COMMAND_SCHEMA,
    CommandGate,
    DeviceManagementIdentity,
    ManagementCommand,
    Observation,
    ReplayStore,
    SignedCommand,
    SignedObservation,
    canonical_json,
    sha256_json,
)


NOW = datetime(2026, 9, 2, 8, 0, tzinfo=UTC)


class ManagementContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.identity = DeviceManagementIdentity.load_or_create(self.root / "identity")
        self.authority = SigningKey.generate()
        self.replay = ReplayStore(self.root / "replay.sqlite3")
        self.gate = CommandGate(
            device_id="lantern-a",
            authority_verify_key=self.authority.verify_key,
            replay_store=self.replay,
            identity=self.identity,
        )
        self.state = {"release": "0.1.0", "services": {"runtime": "ready"}}

    def tearDown(self):
        self.replay.close()
        self.temp.cleanup()

    def command(self, *, sequence=1, device_id="lantern-a", expected_state_hash=None, expires_at=None):
        command = ManagementCommand(
            schema=COMMAND_SCHEMA,
            command_id=f"command-{sequence}",
            device_id=device_id,
            sequence=sequence,
            issued_at=NOW.isoformat(),
            expires_at=(expires_at or NOW + timedelta(minutes=10)).isoformat(),
            action="apply_device_profile",
            expected_state_hash=expected_state_hash or sha256_json(self.state),
            desired_revision="bench-5",
            parameters={"profile": "bench", "revision": 5},
        )
        signature = self.authority.sign(canonical_json(asdict(command)).encode()).signature
        return SignedCommand(command, base64.b64encode(signature).decode())

    def test_management_identity_is_stable_and_separate(self):
        second = DeviceManagementIdentity.load_or_create(self.root / "identity")
        self.assertEqual(self.identity.public_key, second.public_key)
        self.assertTrue(self.identity.fingerprint.startswith("sha256:"))
        self.assertEqual((self.root / "identity" / "management-ed25519.key").stat().st_mode & 0o777, 0o600)

    def test_observation_binds_state_hash_freshness_and_signature(self):
        observation = Observation.create(
            device_id="lantern-a",
            sequence=12,
            observed_at=NOW,
            freshness_ttl=timedelta(minutes=5),
            clock_confidence="ntp-synchronized",
            source_fingerprint=self.identity.fingerprint,
            state=self.state,
        )
        signed = SignedObservation.create(observation, self.identity)
        payload = json.loads(signed.to_json())
        self.assertEqual(payload["observation"]["state_hash"], sha256_json(self.state))
        self.identity.signing_key.verify_key.verify(
            canonical_json(payload["observation"]).encode(), base64.b64decode(payload["signature"])
        )

    def test_valid_command_executes_once_and_returns_signed_receipt(self):
        signed = self.command()
        receipt = self.gate.execute(
            signed,
            current_state=self.state,
            handlers={"apply_device_profile": lambda parameters: {**self.state, "profile": parameters}},
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(receipt.receipt.outcome, "succeeded")
        self.identity.signing_key.verify_key.verify(
            canonical_json(asdict(receipt.receipt)).encode(), base64.b64decode(receipt.signature)
        )
        with self.assertRaisesRegex(ValueError, "stale or replayed|already accepted"):
            self.gate.execute(
                signed,
                current_state=self.state,
                handlers={"apply_device_profile": lambda parameters: self.state},
                now=NOW + timedelta(minutes=2),
            )

    def test_rejects_wrong_target_expiry_state_and_signature(self):
        cases = [
            (self.command(device_id="lantern-b"), "different device"),
            (self.command(sequence=2, expires_at=NOW + timedelta(seconds=1)), "expired"),
            (self.command(sequence=3, expected_state_hash="0" * 64), "expected_state_hash"),
        ]
        for signed, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                self.gate.execute(
                    signed,
                    current_state=self.state,
                    handlers={"apply_device_profile": lambda parameters: self.state},
                    now=NOW + timedelta(minutes=1),
                )

        tampered = self.command(sequence=4)
        invalid = SignedCommand(tampered.command, base64.b64encode(b"x" * 64).decode())
        with self.assertRaisesRegex(ValueError, "signature is invalid"):
            self.gate.execute(
                invalid,
                current_state=self.state,
                handlers={"apply_device_profile": lambda parameters: self.state},
                now=NOW + timedelta(minutes=1),
            )

    def test_rejects_unbounded_or_noncanonical_payloads(self):
        with self.assertRaisesRegex(ValueError, "24 hours"):
            ManagementCommand.from_dict(
                {
                    **asdict(self.command().command),
                    "expires_at": (NOW + timedelta(days=2)).isoformat(),
                }
            )
        with self.assertRaises(ValueError):
            canonical_json({"temperature": float("nan")})
        with self.assertRaisesRegex(ValueError, "floating-point"):
            Observation.create(
                device_id="lantern-a",
                sequence=1,
                observed_at=NOW,
                freshness_ttl=timedelta(minutes=1),
                clock_confidence="unknown",
                source_fingerprint=self.identity.fingerprint,
                state={"temperature_celsius": 48.5},
            )

    def test_failed_handler_is_receipted_and_cannot_be_replayed(self):
        def fail(_parameters):
            raise RuntimeError("private diagnostic detail")

        signed = self.command(sequence=20)
        receipt = self.gate.execute(
            signed,
            current_state=self.state,
            handlers={"apply_device_profile": fail},
            now=NOW + timedelta(minutes=1),
        )
        self.assertEqual(receipt.receipt.outcome, "failed")
        self.assertEqual(receipt.receipt.reason_code, "RuntimeError")
        self.assertNotIn("private diagnostic detail", receipt.to_json())
        with self.assertRaises(ValueError):
            self.gate.execute(
                signed,
                current_state=self.state,
                handlers={"apply_device_profile": fail},
                now=NOW + timedelta(minutes=2),
            )


if __name__ == "__main__":
    unittest.main()
