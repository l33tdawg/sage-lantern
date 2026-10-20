"""Synthetic early-boot admission gate controls."""
from pathlib import Path
import importlib.util
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


if __name__ == "__main__":
    unittest.main()
