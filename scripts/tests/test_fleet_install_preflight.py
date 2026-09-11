import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/install-fleet-admin-local.sh"


class FleetPreflightTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.metadata = self.root / "unit.env"
        self.metadata.write_text("UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
        self.radio = self.root / "meshcore-radio-public.json"
        self.radio_value = {
            "schema": "sage.lantern.meshcore-radio-public.v1", "unit_id": "unit-b",
            "usb": {"serial": "RADIO-B", "vendor_id": "303a", "product_id": "1001",
                    "stable_path": "/dev/serial/by-id/usb-RADIO-B", "managed_path": "/dev/sage-lantern-radio"},
            "radio_identity": {"public_key": "22" * 32,
                               "public_key_sha256": "sha256:" + hashlib.sha256(bytes.fromhex("22" * 32)).hexdigest()},
        }
        self.radio.write_text(json.dumps(self.radio_value))
        binary = self.root / "bin"
        binary.mkdir()
        self.operations = self.root / "operations"
        for command in ("sudo", "systemctl", "npm", "install", "chown", "chmod", "runuser", "udevadm", "hostnamectl", "uname"):
            path = binary / command
            path.write_text(f'#!/bin/sh\necho "{command}" >> "{self.operations}"\nexit 99\n')
            path.chmod(0o755)
        self.environment = {**os.environ, "PATH": str(binary) + os.pathsep + os.environ["PATH"]}

    def run_script(self, *args, script=SCRIPT):
        before = {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run(["bash", str(script), *args], capture_output=True, text=True,
                                env=self.environment, timeout=10)
        self.assertFalse(self.operations.exists(), result.stderr)
        self.assertEqual(before, {path: path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        return result

    def dry_run(self):
        return self.run_script("--dry-run", "--unit-id", "unit-b", "--metadata", str(self.metadata),
                               "--radio-evidence", str(self.radio))

    def test_b_plan_does_not_claim_install_or_probe_target(self):
        result = self.dry_run()
        self.assertEqual(result.returncode, 0, result.stderr)
        plan = json.loads(result.stdout)
        self.assertEqual(plan["hostname"], "sage-lantern-b")
        self.assertFalse(plan["mutations_performed"])
        self.assertFalse(plan["unit_b_activation_supported"])
        self.assertFalse(plan["hardware_verified"])

    def test_a_radio_evidence_cannot_be_stamped_as_b(self):
        self.radio_value["unit_id"] = "unit-a"
        self.radio.write_text(json.dumps(self.radio_value))
        self.assertNotEqual(self.dry_run().returncode, 0)

    def test_mismatched_prepared_unit_fails(self):
        self.metadata.write_text("UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
        self.assertNotEqual(self.dry_run().returncode, 0)

    def test_invalid_key_digest_and_duplicate_metadata_fail(self):
        self.radio_value["radio_identity"]["public_key_sha256"] = "sha256:" + "0" * 64
        self.radio.write_text(json.dumps(self.radio_value))
        self.assertNotEqual(self.dry_run().returncode, 0)
        self.metadata.write_text("UNIT_ID=unit-b\nUNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
        self.assertNotEqual(self.dry_run().returncode, 0)

    def test_parser_and_live_override_rejected_without_mutation(self):
        for args in (("--bogus",), ("--unit-id",), ("--unit-id", "unit-b;id"),
                     ("--dry-run", "--dry-run"), ("--metadata", str(self.metadata)),
                     ("--unit-id", "unit-a", "--unit-id", "unit-b")):
            with self.subTest(args=args):
                self.assertNotEqual(self.run_script(*args).returncode, 0)

    def test_help_does_not_need_metadata(self):
        self.metadata.unlink()
        self.assertEqual(self.run_script("--help").returncode, 0)

    def test_live_b_refuses_before_loading_helpers(self):
        self.metadata.unlink()
        result = self.run_script("--unit-id", "unit-b")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unfinished fresh secure appliance installer", result.stderr)

    def test_guarded_a_and_dangling_marker_refuse_before_loading_helpers(self):
        marker = self.root / "lock.enabled"
        script = self.root / SCRIPT.name
        script.write_text(SCRIPT.read_text().replace(
            "/etc/sage-lantern-device-lock.enabled", str(marker)))
        for dangling in (False, True):
            with self.subTest(dangling=dangling):
                if dangling:
                    marker.unlink()
                    marker.symlink_to(self.root / "missing")
                else:
                    marker.write_text("")
                result = self.run_script("--unit-id", "unit-a", script=script)
                self.assertEqual(result.returncode, 2)
                self.assertIn("Guarded appliance detected", result.stderr)


if __name__ == "__main__":
    unittest.main()
