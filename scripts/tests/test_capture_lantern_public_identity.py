import base64
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from nacl.signing import SigningKey


SCRIPT = Path(__file__).resolve().parents[1] / "capture-lantern-public-identity.py"


def comet_key(signing_key: SigningKey) -> dict[str, str]:
    private = bytes(signing_key) + bytes(signing_key.verify_key)
    return {
        "type": "tendermint/PrivKeyEd25519",
        "value": base64.b64encode(private).decode(),
    }


def comet_public(signing_key: SigningKey) -> dict[str, str]:
    return {
        "type": "tendermint/PubKeyEd25519",
        "value": base64.b64encode(bytes(signing_key.verify_key)).decode(),
    }


class CaptureLanternPublicIdentityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self.tempdir.name)
        self.comet = self.home / "data/cometbft/config"
        self.comet.mkdir(parents=True)
        (self.home / "certs").mkdir()

        self.node = SigningKey.generate()
        self.validator = SigningKey.generate()
        self.transport = SigningKey.generate()
        self.voice = SigningKey.generate()
        (self.home / "agent.key").write_bytes(bytes(self.transport))
        (self.home / "voice.key").write_bytes(bytes(self.voice))
        (self.home / "certs/ca.crt").write_bytes(b"public ca certificate\n")
        (self.home / "certs/node.crt").write_bytes(b"public node certificate\n")
        (self.comet / "node_key.json").write_text(
            json.dumps({"priv_key": comet_key(self.node)})
        )
        (self.comet / "priv_validator_key.json").write_text(
            json.dumps(
                {
                    "address": "unused",
                    "pub_key": comet_public(self.validator),
                    "priv_key": comet_key(self.validator),
                }
            )
        )
        self.genesis = {
            "chain_id": "sage-personal-test",
            "validators": [{"pub_key": comet_public(self.validator)}],
        }
        (self.comet / "genesis.json").write_text(json.dumps(self.genesis))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--sage-home", str(self.home)],
            text=True,
            capture_output=True,
            check=False,
        )

    def run_script_with_voice(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--sage-home",
                str(self.home),
                "--voice-key",
                str(self.home / "voice.key"),
            ],
            text=True,
            capture_output=True,
            check=False,
        )

    def test_emits_only_public_identity_material(self) -> None:
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["personal_chain_id"], "sage-personal-test")
        self.assertEqual(
            payload["comet_node"]["public_key_hex"], bytes(self.node.verify_key).hex()
        )
        self.assertEqual(
            payload["personal_validator"]["public_key_hex"],
            bytes(self.validator.verify_key).hex(),
        )
        self.assertEqual(
            payload["tls_federation"]["transport_agent_id"],
            bytes(self.transport.verify_key).hex(),
        )
        self.assertNotIn(base64.b64encode(bytes(self.node)).decode(), result.stdout)
        self.assertNotIn(base64.b64encode(bytes(self.validator)).decode(), result.stdout)
        self.assertNotIn(bytes(self.transport).hex(), result.stdout)
        self.assertTrue(payload["personal_genesis_fingerprint"].startswith("sha256:"))

    def test_includes_voice_fingerprint_only_when_requested(self) -> None:
        without_voice = json.loads(self.run_script().stdout)
        self.assertNotIn("voice_identity", without_voice)

        result = self.run_script_with_voice()
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            payload["voice_identity"]["public_key_hex"], bytes(self.voice.verify_key).hex()
        )
        self.assertTrue(payload["voice_identity_fingerprint"].startswith("sha256:"))
        self.assertNotIn(bytes(self.voice).hex(), result.stdout)

    def test_rejects_validator_mismatch_with_genesis(self) -> None:
        other = SigningKey.generate()
        self.genesis["validators"][0]["pub_key"] = comet_public(other)
        (self.comet / "genesis.json").write_text(json.dumps(self.genesis))
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not match genesis", result.stderr)


if __name__ == "__main__":
    unittest.main()
