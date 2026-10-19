import os
import re
from pathlib import Path
import signal
import subprocess
import tempfile
import time
import unittest


KIOSK = Path(__file__).resolve().parents[1] / "lantern/sage-lantern-kiosk"


class KioskSleepFenceTests(unittest.TestCase):
    def test_profile_argument_rejects_traversal_and_other_unit_names(self):
        for profile in ('/home/radxa/.config/sage-lantern/../secrets', '/home/radxa/.config/sage-lantern/unit-g/chromium'):
            result = subprocess.run(['bash', str(KIOSK), '--profile-dir', profile], capture_output=True, timeout=2)
            self.assertEqual(result.returncode, 2)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fence = self.root / "fenced"
        self.log = self.root / "calls"
        self.log.touch()
        self.browser = self.bin / "chromium"
        self.executable("xset", 'echo "xset $*" >> "$TEST_LOG"\nif [ -f "$TEST_ROOT/fence-on-xset" ]; then touch "$TEST_FENCE"; fi\n')
        self.executable("curl", 'echo curl >> "$TEST_LOG"\nexit 0\n')
        self.executable("xrandr", 'echo "Screen 0: current 800 x 480, maximum 800 x 480"\n')
        self.executable("pgrep", "exit 0\n")
        self.executable("sleep", '/bin/sleep 0.02\n')
        self.executable("chromium", 'echo chromium >> "$TEST_LOG"\ntouch "$TEST_FENCE"\nexit 0\n')
        source = KIOSK.read_text()
        source = source.replace("readonly sleep_fence=/run/sage-lantern-sleep.fenced",
                                f"readonly sleep_fence='{self.fence}'")
        source = source.replace("/usr/bin/chromium", str(self.browser))
        source = source.replace("^/home/", "^" + re.escape(str(self.root)) + "/homes/")
        self.fixture = self.root / "kiosk-fixture"
        self.fixture.write_text(source)
        self.process = None
        self.addCleanup(self.stop)

    def executable(self, name, body):
        path = self.bin / name
        path.write_text("#!/bin/sh\n" + body)
        path.chmod(0o755)

    def start(self, profile=None):
        environment = dict(os.environ, PATH=f"{self.bin}:/usr/bin:/bin",
                           TEST_LOG=str(self.log), TEST_FENCE=str(self.fence), TEST_ROOT=str(self.root))
        self.process = subprocess.Popen(["bash", str(self.fixture)] + ([] if profile is None else ["--profile-dir", profile]), env=environment,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                        start_new_session=True)

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGTERM)
            self.process.wait(timeout=3)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.01)
        self.fail("fixture did not reach expected state")

    def test_unit_c_profile_reaches_browser_through_existing_sleep_fence(self):
        self.fence.touch()
        self.start(str(self.root / 'homes/operator/.config/sage-lantern/unit-c/chromium'))
        time.sleep(0.15)
        self.assertEqual(self.log.read_text(), '')
        self.assertIsNone(self.process.poll())
        self.fence.unlink()
        self.wait_for(lambda: 'chromium' in self.log.read_text())

    def test_startup_fence_prevents_display_and_browser_calls(self):
        self.fence.touch()
        self.start()
        time.sleep(0.15)
        self.assertEqual(self.log.read_text(), "")
        self.assertIsNone(self.process.poll())
        self.fence.unlink()
        self.wait_for(lambda: "chromium" in self.log.read_text())

    def test_each_restart_waits_for_fence_removal(self):
        self.start()
        self.wait_for(lambda: self.fence.exists())
        time.sleep(0.15)
        self.assertEqual(self.log.read_text().count("chromium"), 1)
        self.fence.unlink()
        self.wait_for(lambda: self.log.read_text().count("chromium") == 2)
        time.sleep(0.1)
        self.assertEqual(self.log.read_text().count("chromium"), 2)

    def test_broken_symlink_fence_is_fail_closed(self):
        self.fence.symlink_to(self.root / "absent")
        self.start()
        time.sleep(0.15)
        self.assertEqual(self.log.read_text(), "")
        self.assertIsNone(self.process.poll())

    def test_new_fence_during_display_init_prevents_remaining_commands(self):
        (self.root / "fence-on-xset").touch()
        self.start()
        self.wait_for(lambda: self.fence.exists())
        time.sleep(0.1)
        self.assertEqual(self.log.read_text().splitlines(), ["xset s off"])

    def test_fence_before_browser_exec_skips_launch(self):
        self.executable("xrandr", 'touch "$TEST_FENCE"\necho "Screen 0: current 800 x 480, maximum 800 x 480"\n')
        self.start()
        self.wait_for(lambda: self.fence.exists())
        time.sleep(0.1)
        self.assertNotIn("chromium", self.log.read_text())

    def test_fixed_public_fence_and_shell_syntax(self):
        self.assertIn("readonly sleep_fence=/run/sage-lantern-sleep.fenced", KIOSK.read_text())
        result = subprocess.run(["bash", "-n", str(KIOSK)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
