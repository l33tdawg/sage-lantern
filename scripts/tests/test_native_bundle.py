"""Exact native bundle manifest and closure controls."""
from pathlib import Path
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "software/security/desktop_loader_candidate/native_bundle.py"
SPEC = importlib.util.spec_from_file_location("native_bundle_tested", MODULE_PATH)
B = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = B
SPEC.loader.exec_module(B)
SOURCE = "a" * 40


class NativeBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.root.chmod(0o700)
        for relative, mode in B.REQUIRED.items():
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((relative + "\n").encode())
            path.chmod(mode)

    def test_manifest_roundtrip_and_exact_verification(self):
        value = B.build(self.root, SOURCE)
        B.write(self.root, value)
        self.assertEqual(B.verify(self.root), value)

    def test_changed_missing_extra_and_symlink_refuse(self):
        value = B.build(self.root, SOURCE)
        B.write(self.root, value)
        target = self.root / "libexec/native_probe.py"
        target.write_bytes(b"changed\n")
        with self.assertRaisesRegex(B.BundleRefused, "bundle_manifest_mismatch"):
            B.verify(self.root)
        target.write_bytes(b"libexec/native_probe.py\n")
        (self.root / "extra.txt").write_text("extra")
        with self.assertRaisesRegex(B.BundleRefused, "bundle_file_set_mismatch"):
            B.verify(self.root)
        (self.root / "extra.txt").unlink()
        (self.root / "libexec/link.py").symlink_to("native_probe.py")
        with self.assertRaisesRegex(B.BundleRefused, "bundle_symlink_present"):
            B.build(self.root, SOURCE)

    def test_mode_change_refuses(self):
        value = B.build(self.root, SOURCE)
        (self.root / "etc/capture-policy.json").chmod(0o644)
        with self.assertRaisesRegex(B.BundleRefused, "bundle_file_mode"):
            B.build(self.root, SOURCE)

    def test_manifest_cannot_omit_or_add_required_paths(self):
        value = B.build(self.root, SOURCE)
        value["files"].pop("bin/sage-lantern-desktop-loader")
        with self.assertRaisesRegex(B.BundleRefused, "bundle_manifest_schema_invalid"):
            B.write(self.root, value)

    def test_review_verifier_accepts_exact_bundle(self):
        B.write(self.root, B.build(self.root, SOURCE))
        result = subprocess.run([
            sys.executable,
            str(ROOT / "scripts/lantern/verify-native-bundle.py"),
            str(self.root), "--expected-uid", str(os.geteuid())],
            check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), SOURCE)

    def test_stage_script_seals_once(self):
        script = ROOT / "scripts/lantern/stage-native-bundle.py"
        command = [
            sys.executable, str(script), str(self.root),
            "--source-commit", SOURCE, "--expected-uid", str(os.geteuid())]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), SOURCE)
        self.assertTrue((self.root / B.MANIFEST).is_file())
        again = subprocess.run(command, capture_output=True, text=True)
        self.assertEqual(again.returncode, 1)
        self.assertIn("bundle_manifest_already_present", again.stderr)


if __name__ == "__main__":
    unittest.main()
