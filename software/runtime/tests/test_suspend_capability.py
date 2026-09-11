import json
import os
from pathlib import Path
import tempfile
import unittest

from sage_lantern.suspend_capability import available


class CapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name).resolve() / "status"
        self.boot = self.path.parent / "boot"
        self.boot.write_text("current-boot")
        self.value = dict(version=1, available=True, boot_id="current-boot", updated_boottime=100)

    def write(self):
        self.path.write_text(json.dumps(self.value))
        self.path.chmod(0o644)

    def check(self, now=105):
        return available(path=self.path, boot_path=self.boot, now=now, owner_uid=os.geteuid())

    def test_current_root_equivalent_receipt(self):
        self.write()
        self.assertTrue(self.check())

    def test_missing_stale_future_and_old_boot_deny(self):
        self.assertFalse(self.check())
        self.write()
        self.assertFalse(self.check(116))
        self.assertFalse(self.check(99))
        self.boot.write_text("another-boot")
        self.assertFalse(self.check())

    def test_invalid_schema_and_unavailable_deny(self):
        for key, value in [("version", True), ("available", 1), ("available", False),
                           ("updated_boottime", True), ("updated_boottime", float("nan")),
                           ("extra", 1)]:
            with self.subTest(key=key, value=value):
                original = dict(self.value)
                self.value[key] = value
                self.write()
                self.assertFalse(self.check())
                self.value = original

    def test_unsafe_modes_links_and_duplicate_keys_deny(self):
        self.write()
        self.path.chmod(0o666)
        self.assertFalse(self.check())
        self.path.chmod(0o644)
        other = self.path.parent / "other"
        os.link(self.path, other)
        self.assertFalse(self.check())
        other.unlink()
        self.path.write_text('{"version":1,"version":1}')
        self.assertFalse(self.check())
        self.path.unlink()
        self.path.symlink_to(self.boot)
        self.assertFalse(self.check())
