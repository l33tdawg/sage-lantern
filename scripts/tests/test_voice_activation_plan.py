import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import shutil
import sys


SCRIPT = Path(__file__).resolve().parents[1] / "lantern/activate-unit-a-local-voice.sh"


class VoiceActivationPlanTests(unittest.TestCase):
    def planner_arguments(self):
        radio = self.root / "meshcore-radio-public.json"
        public_key = "22" * 32
        fingerprint = "sha256:" + hashlib.sha256(bytes.fromhex(public_key)).hexdigest()
        radio.write_text(json.dumps({
            "schema": "sage.lantern.meshcore-radio-public.v1", "unit_id": "unit-b",
            "usb": {"serial": "RADIO-B", "vendor_id": "303a", "product_id": "1001",
                    "stable_path": "/dev/serial/by-id/usb-RADIO-B", "managed_path": "/dev/sage-lantern-radio"},
            "radio_identity": {"public_key": public_key, "public_key_sha256": fingerprint},
        }))
        self.values["RADIO_IDENTITY_FINGERPRINT"] = fingerprint
        self.write_inventory()
        target = self.root / "home/radxa/sage-commons/evidence/unit-b-provisioning"
        shutil.copytree(self.identity.parent, target)
        return [sys.executable, str(SCRIPT.parent / "lantern-unit-config.py"),
                "--format", "voice-activation", "--unit-id", "unit-b",
                "--prepared-metadata", str(self.prepared), "--radio-inventory", str(radio),
                "--radio-usb-serial", "RADIO-B", "--radio-vendor-id", "303a", "--radio-product-id", "1001",
                "--radio-stable-path", "/dev/serial/by-id/usb-RADIO-B", "--radio-public-fingerprint", fingerprint,
                "--audio-usb-serial", "AUDIO-B", "--audio-stable-path", "/dev/snd/by-id/usb-AUDIO-B-00",
                "--identity-inventory", str(self.inventory), "--activation-staging-root", str(self.root)]

    def test_planner_output_passes_actual_activation_preflight_without_mutation(self):
        arguments = self.planner_arguments()
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run(arguments, env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(before, {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()})
        exported = json.loads(result.stdout)
        self.assertEqual(exported["schema"], "sage-lantern-voice-activation-v1")
        self.assertNotIn("unit-a", result.stdout)
        self.config_path.write_text(result.stdout)
        checked = self.plan_b()
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertFalse(self.operations.exists())

    def test_planner_rejects_inventory_radio_mismatch(self):
        arguments = self.planner_arguments()
        self.values["RADIO_IDENTITY_FINGERPRINT"] = "sha256:" + "f" * 64
        self.write_inventory()
        result = subprocess.run(arguments, env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.operations.exists())

    def test_planner_rejects_inventory_unit_mismatch(self):
        arguments = self.planner_arguments()
        self.values["UNIT_ID"] = "unit-a"
        self.write_inventory()
        result = subprocess.run(arguments, env=self.environment, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        self.assertFalse(self.operations.exists())

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.operations = self.root / "unexpected-operations"
        for name in ("sudo", "systemctl", "rm", "mv", "mktemp", "chmod", "chown", "curl", "runuser", "uname", "sha256sum"):
            executable = self.bin / name
            executable.write_text(f'#!/bin/sh\nprintf "%s\\n" {name} >>"{self.operations}"\nexit 99\n')
            executable.chmod(0o755)
        self.environment = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"]}
        self.prepared = self.root / "unit.env"
        self.prepared.write_text("UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n")
        evidence = self.root / "unit-b-provisioning"
        evidence.mkdir()
        self.identity = evidence / "personal-public-identity.json"
        self.recovery = evidence / "recovery-test"
        self.recovery.mkdir()
        manifest = "".join("0" * 64 + "  " + name + "\n" for name in (
            "backup-manifest.json", "identity-before.json", "identity-after.json", "private-backup.sha256", "upgrade-preflight.txt",
        ))
        (self.recovery / "SHA256SUMS").write_text(manifest)
        digest = hashlib.sha256(manifest.encode()).hexdigest()
        (self.recovery / "RECOVERY_EVIDENCE_SHA256").write_text(digest + "  recovery-evidence-bundle\n")
        self.inventory = self.root / "unit-inventory.env"
        self.values = {
            "INVENTORY_SCHEMA": "sage-lantern-identity-v1", "UNIT_ID": "unit-b", "PERSONAL_CHAIN_ID": "unit-b-personal",
        }
        for index, name in enumerate(("PERSONAL_GENESIS", "COMET_NODE", "PERSONAL_VALIDATOR", "VOICE_IDENTITY",
                                      "PUBLIC_PROPOSER", "TLS_FEDERATION", "RADIO_IDENTITY"), 1):
            self.values[name + "_FINGERPRINT"] = "sha256:" + str(index) * 64
        self.values["RECOVERY_EVIDENCE_FINGERPRINT"] = "sha256:" + digest
        for name in ("IDENTITY_UNIQUENESS", "RECOVERY_TEST", "NO_CLONED_SECRETS", "NO_PUBLIC_VALIDATOR_KEY"):
            self.values[name + "_ATTESTED"] = "PASS"
        self.write_inventory()
        public = {name.lower(): self.values[name] for name in (
            "PERSONAL_CHAIN_ID", "PERSONAL_GENESIS_FINGERPRINT", "COMET_NODE_FINGERPRINT",
            "PERSONAL_VALIDATOR_FINGERPRINT", "VOICE_IDENTITY_FINGERPRINT", "TLS_FEDERATION_FINGERPRINT",
        )}
        public["schema"] = "sage.lantern.personal-public-identity.v1"
        self.identity.write_text(json.dumps(public))
        self.config = {
            "schema": "sage-lantern-voice-activation-v1", "unit_id": "unit-b",
            "prepared_metadata": str(self.prepared), "inventory": str(self.inventory),
            "identity_evidence": str(self.identity), "recovery_dir": str(self.recovery),
            "stable_usb_audio": "/dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_SAGE-LANTERN-B-AUDIO-00",
        }
        self.config_path = self.root / "voice-activation.json"
        self.write_config()

    def write_inventory(self):
        self.inventory.write_text("".join(f"{key}={value}\n" for key, value in self.values.items()))

    def write_config(self):
        self.config_path.write_text(json.dumps(self.config))

    def run_script(self, *arguments):
        before = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        result = subprocess.run(["bash", str(SCRIPT), *arguments], env=self.environment,
                                capture_output=True, text=True, timeout=10)
        after = {str(path): path.read_bytes() for path in self.root.rglob("*") if path.is_file()}
        self.assertFalse(self.operations.exists(), result.stderr)
        self.assertEqual(before, after)
        return result

    def plan_b(self):
        return self.run_script("--dry-run", "--unit-id", "unit-b", "--unit-config", str(self.config_path))

    def test_default_a_plan_does_not_probe_or_mutate(self):
        result = self.run_script("--dry-run")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unit-a", result.stdout)
        self.assertIn("Pending target gates", result.stdout)

    def test_b_public_evidence_plan_no_target_operations(self):
        result = self.plan_b()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("unit-b", result.stdout)
        self.assertNotIn("SAGE-LANTERN-A-AUDIO", result.stdout)

    def test_parser_rejects_unknown_missing_duplicate_and_injection(self):
        for arguments in (("--bogus",), ("--unit-id",), ("--unit-config",),
                          ("--dry-run", "--dry-run"), ("--unit-id", "unit-b"),
                          ("--dry-run", "--unit-id", "unit-b;touch /tmp/no"),
                          ("--unit-id", "unit-a", "--unit-id", "unit-b")):
            with self.subTest(arguments=arguments):
                self.assertNotEqual(self.run_script(*arguments).returncode, 0)

    def test_help_is_nonmutating(self):
        self.assertEqual(self.run_script("--help").returncode, 0)

    def test_metadata_mismatch_or_duplicate_rejected(self):
        for content in ("UNIT_ID=unit-a\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
                        self.prepared.read_text() + "UNIT_ID=unit-b\n"):
            self.prepared.write_text(content)
            self.assertNotEqual(self.plan_b().returncode, 0)

    def test_config_rejects_a_audio_paths_and_unknown_fields(self):
        original = dict(self.config)
        for key, value in (("stable_usb_audio", self.config["stable_usb_audio"].replace("B-AUDIO", "A-AUDIO")),
                           ("stable_usb_audio", "/dev/snd/pcmC0D0c"),
                           ("identity_evidence", str(self.identity).replace("unit-b-provisioning", "unit-a-provisioning")),
                           ("recovery_dir", "/tmp/../keys"), ("unit_id", "unit-a"), ("extra", "bad")):
            with self.subTest(key=key, value=value):
                self.config = {**original, key: value}
                self.write_config()
                self.assertNotEqual(self.plan_b().returncode, 0)

    def test_inventory_attestations_and_distinctness_required(self):
        original = dict(self.values)
        for key, value in (("NO_CLONED_SECRETS_ATTESTED", "FAIL"), ("UNIT_ID", "unit-a"),
                           ("VOICE_IDENTITY_FINGERPRINT", self.values["COMET_NODE_FINGERPRINT"]),
                           ("RADIO_IDENTITY_FINGERPRINT", "missing")):
            with self.subTest(key=key):
                self.values = {**original, key: value}
                self.write_inventory()
                self.assertNotEqual(self.plan_b().returncode, 0)

    def test_public_identity_mismatch_rejected(self):
        self.identity.write_text('{}')
        self.assertNotEqual(self.plan_b().returncode, 0)

    def test_missing_public_evidence_rejected(self):
        self.identity.unlink()
        self.assertNotEqual(self.plan_b().returncode, 0)

    def test_recovery_digest_mismatch_rejected(self):
        (self.recovery / "RECOVERY_EVIDENCE_SHA256").write_text("0" * 64 + "  recovery-evidence-bundle\n")
        self.assertNotEqual(self.plan_b().returncode, 0)

    def test_recovery_manifest_cannot_reference_arbitrary_paths(self):
        (self.recovery / "SHA256SUMS").write_text("0" * 64 + "  /private/key\n")
        self.assertNotEqual(self.plan_b().returncode, 0)

    def test_symlink_and_duplicate_json_rejected(self):
        original = self.config_path.read_text()
        self.config_path.write_text(original[:-1] + ', "unit_id": "unit-b"}')
        self.assertNotEqual(self.plan_b().returncode, 0)
        self.config_path.unlink()
        alternate = self.root / "alternate.json"
        alternate.write_text(original)
        self.config_path.symlink_to(alternate)
        self.assertNotEqual(self.plan_b().returncode, 0)

    def test_non_dry_run_rejects_staged_config_before_mutation(self):
        result = self.run_script("--unit-id", "unit-b", "--unit-config", str(self.config_path))
        self.assertNotEqual(result.returncode, 0)

    def test_syntax(self):
        result = subprocess.run(["bash", "-n", str(SCRIPT)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
