"""Replay actual loader core. No device access or kernel policy attachment."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate"

class DesktopLoaderCandidateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_failure_identity_cancellation_and_preservation(self):
        with tempfile.TemporaryDirectory(prefix="desktop-loader-replay-") as directory:
            binary = Path(directory) / "replay"
            subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-I" + str(SOURCE), str(SOURCE / "loader.c"),
                str(ROOT / "scripts/tests/native/desktop_loader_replay.c"), "-o", str(binary)],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)
            self.assertRegex(result.stdout.decode(), r"\d+ loader failure/identity checks passed")
            self.assertIn("kernel_enforcement_qualified=false", result.stdout.decode())

    @unittest.skipUnless(shutil.which("cc"), "C compiler required")
    def test_backend_preserves_core_release_uncertainty_through_close(self):
        # Compile the exact lifecycle bodies without Linux/libbpf dependencies.
        # All OS/libbpf functions are inert, so this is deliberately narrower
        # than the separate complete Linux backend build and child replay.
        backend = (SOURCE / "linux_backend.c").read_text()
        start = backend.index("static void fence(")
        end = backend.index("\nconst struct lantern_ops", start)
        with tempfile.TemporaryDirectory(prefix="desktop-loader-fence-") as directory:
            temporary = Path(directory)
            (temporary / "backend_fence_close.inc").write_text(backend[start:end])
            binary = temporary / "replay"
            subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                "-I" + str(SOURCE), "-I" + str(temporary), str(SOURCE / "loader.c"),
                str(ROOT / "scripts/tests/native/desktop_loader_fence_replay.c"), "-o", str(binary)],
                check=True, capture_output=True, timeout=30)
            result = subprocess.run([str(binary)], check=True, capture_output=True, timeout=5)
            self.assertIn("10 extracted backend preservation checks passed", result.stdout.decode())
            self.assertIn("whole_linux_backend_compiled=false", result.stdout.decode())

if __name__ == "__main__":
    unittest.main()
