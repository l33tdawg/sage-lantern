"""Synthetic early-boot admission gate controls."""
from pathlib import Path
import importlib.util
import os
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate/early_boot_admission.py"
SPEC = importlib.util.spec_from_file_location("early_boot_admission_tested", SOURCE)
E = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = E
SPEC.loader.exec_module(E)

BOOT = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


class EarlyBootTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.write("proc/sys/kernel/random/boot_id", BOOT + "\n")
        self.write("proc/self/uid_map", "0 0 4294967295\n")

    def write(self, path, text):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)

    def test_no_uid_user_is_admissible(self):
        receipt = E.observe(self.root, expected_uid=1000, generation=7)
        self.assertEqual(receipt.boot_id, BOOT)
        self.assertEqual(receipt.uid, 1000)
        self.assertFalse(receipt.runtime_authorized)

    def test_existing_uid1000_process_refuses(self):
        self.write("proc/123/status", "Name: shell\nUid:\t1000\t1000\t1000\t1000\n")
        with self.assertRaisesRegex(E.AdmissionRefused, "user_process"):
            E.observe(self.root)

    def test_wrong_boot_or_uid_mapping_refuses(self):
        with self.assertRaisesRegex(E.AdmissionRefused, "boot_identity"):
            self.write("proc/sys/kernel/random/boot_id", "bad\n")
            E.observe(self.root)
        self.write("proc/sys/kernel/random/boot_id", BOOT + "\n")
        self.write("proc/self/uid_map", "1000 0 1\n")
        with self.assertRaisesRegex(E.AdmissionRefused, "initial_uid"):
            E.observe(self.root)

    def test_receipt_roundtrip_and_boot_binding(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        path = state / "admission.json"
        receipt = E.observe(self.root, expected_uid=1000, generation=7)
        E.write_receipt(receipt, path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(
            E.read_receipt(
                path, expected_uid=1000, generation=7, root=self.root),
            receipt)
        self.write("proc/sys/kernel/random/boot_id",
                   "bbbbbbbb-cccc-dddd-eeee-ffffffffffff\n")
        with self.assertRaisesRegex(E.AdmissionRefused, "receipt_binding"):
            E.read_receipt(
                path, expected_uid=1000, generation=7, root=self.root)

    def test_receipt_is_private_bounded_and_not_a_symlink(self):
        state = self.root / "state"
        state.mkdir(mode=0o700)
        path = state / "admission.json"
        receipt = E.observe(self.root, expected_uid=1000, generation=7)
        E.write_receipt(receipt, path)
        alias = state / "alias.json"
        alias.symlink_to(path)
        with self.assertRaisesRegex(E.AdmissionRefused, "receipt_unavailable"):
            E.read_receipt(
                alias, expected_uid=1000, generation=7, root=self.root)
        state.chmod(0o755)
        with self.assertRaisesRegex(E.AdmissionRefused, "receipt_directory"):
            E.read_receipt(
                path, expected_uid=1000, generation=7, root=self.root)

    def test_unit_orders_barrier_before_user_entrypoints(self):
        unit = (ROOT / "software/systemd/"
                "sage-lantern-early-boot-admission.service").read_text()
        self.assertIn("Before=display-manager.service getty.target ssh.service ssh.socket",
                      unit)
        self.assertIn("ConditionPathExists=/etc/sage-lantern/desktop-pin-activation.json",
                      unit)
        self.assertIn("StateDirectory=sage-lantern-early-boot", unit)


if __name__ == "__main__":
    unittest.main()
