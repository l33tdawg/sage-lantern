import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "provision-lantern-unit.sh"


def fingerprint(seed: str) -> str:
    return "sha256:" + hashlib.sha256(seed.encode()).hexdigest()


class ProvisionLanternUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def run_script(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(SCRIPT), *arguments, "--staging-root", str(self.root)],
            text=True,
            capture_output=True,
            check=False,
        )

    def verify_arguments(self) -> list[str]:
        names = [
            "personal-genesis",
            "comet-node",
            "personal-validator",
            "voice-identity",
            "public-proposer",
            "tls-federation",
            "radio-identity",
            "recovery-evidence",
        ]
        result = ["verify", "--unit-id", "unit-a", "--personal-chain-id", "personal-unit-a"]
        for name in names:
            result.extend([f"--{name}-fingerprint", fingerprint(name)])
        result.extend(
            [
                "--attest-identity-unique",
                "--attest-recovery-tested",
                "--attest-no-cloned-secrets",
                "--attest-no-public-validator-key",
            ]
        )
        return result

    def test_prepare_is_sterile_and_idempotent(self) -> None:
        for _ in range(2):
            result = self.run_script("prepare", "--unit-id", "unit-a")
            self.assertEqual(result.returncode, 0, result.stderr)

        state = self.root / "var/lib/sage-lantern"
        self.assertFalse((state / "PROVISIONED").exists())
        self.assertTrue((state / "identity/EXTERNAL_IDENTITY_REQUIREMENTS.txt").is_file())
        self.assertEqual(
            (self.root / "etc/sage-lantern/unit.env").read_text().splitlines()[0],
            "UNIT_ID=unit-a",
        )

    def test_target_state_tree_remains_service_owned(self) -> None:
        script = SCRIPT.read_text()
        self.assertIn(
            'install -d -o sage-lantern -g sage-lantern -m 0700 "${state_root}"',
            script,
        )
        self.assertNotIn(
            'install -d -o root -g sage-lantern -m 0700 "${state_root}"',
            script,
        )

    def test_verify_fails_closed_until_all_attestations_exist(self) -> None:
        self.assertEqual(self.run_script("prepare", "--unit-id", "unit-a").returncode, 0)
        arguments = self.verify_arguments()
        arguments.remove("--attest-recovery-tested")
        result = self.run_script(*arguments)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--attest-recovery-tested is required", result.stderr)
        self.assertFalse((self.root / "var/lib/sage-lantern/PROVISIONED").exists())

    def test_verify_rejects_duplicate_identity_fingerprints(self) -> None:
        self.assertEqual(self.run_script("prepare", "--unit-id", "unit-a").returncode, 0)
        arguments = self.verify_arguments()
        voice_index = arguments.index("--voice-identity-fingerprint") + 1
        comet_index = arguments.index("--comet-node-fingerprint") + 1
        arguments[voice_index] = arguments[comet_index]
        result = self.run_script(*arguments)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identity fingerprints must be distinct", result.stderr)
        self.assertFalse((self.root / "var/lib/sage-lantern/PROVISIONED").exists())

    def test_verify_writes_inventory_then_stable_marker(self) -> None:
        self.assertEqual(self.run_script("prepare", "--unit-id", "unit-a").returncode, 0)
        first = self.run_script(*self.verify_arguments())
        self.assertEqual(first.returncode, 0, first.stderr)

        inventory = self.root / "var/lib/sage-lantern/inventory/unit-inventory.env"
        marker = self.root / "var/lib/sage-lantern/PROVISIONED"
        self.assertTrue(inventory.is_file())
        self.assertTrue(marker.is_file())
        digest = hashlib.sha256(inventory.read_bytes()).hexdigest()
        self.assertEqual(marker.read_text(), f"UNIT_ID=unit-a\nINVENTORY_SHA256={digest}\n")
        self.assertEqual(os.stat(marker).st_mode & 0o777, 0o640)
        self.assertNotIn("PRIVATE", inventory.read_text())

        second = self.run_script(*self.verify_arguments())
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(marker.read_text(), f"UNIT_ID=unit-a\nINVENTORY_SHA256={digest}\n")

    def test_prepared_unit_id_cannot_be_changed(self) -> None:
        self.assertEqual(self.run_script("prepare", "--unit-id", "unit-a").returncode, 0)
        result = self.run_script("prepare", "--unit-id", "unit-b")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already prepared as unit-a", result.stderr)

    def test_dry_run_does_not_write(self) -> None:
        result = self.run_script("prepare", "--unit-id", "unit-a", "--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("DRY RUN", result.stdout)
        self.assertEqual(list(self.root.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
