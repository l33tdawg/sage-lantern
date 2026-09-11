import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from sage_lantern.power_settings import PowerSettingsStore, PowerSettingsError, PowerRevisionConflict, LockSettingsStore


class PowerSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve() / "power"
        self.store = PowerSettingsStore(self.directory)

    def update(self, **changes):
        values = dict(expected_revision=0, display_off_seconds=60, deep_sleep_seconds=600)
        values.update(changes)
        return self.store.update(**values)

    def test_defaults_restart_and_exact_schema(self):
        self.assertEqual(self.store.load(), dict(revision=0, display_off_seconds=30,
                                               deep_sleep_seconds=300, deep_sleep_available=False))
        result = self.update()
        self.assertEqual(result["revision"], 1)
        self.assertEqual(PowerSettingsStore(self.directory).load(), result)
        self.assertEqual(stat.S_IMODE(self.directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((self.directory / "power.json").stat().st_mode), 0o600)
        self.assertNotIn("token", (self.directory / "power.json").read_text())

    def test_lock_policy_is_separate_durable_and_strict(self):
        store = LockSettingsStore(self.directory)
        self.assertEqual(store.load(), dict(revision=0, lock_seconds=300))
        power = self.update()
        result = store.update(expected_revision=0, lock_seconds=600)
        self.assertEqual(LockSettingsStore(self.directory).load(), result)
        self.assertEqual(self.store.load(), power)
        self.assertEqual(stat.S_IMODE((self.directory / "lock.json").stat().st_mode), 0o600)
        with self.assertRaises(PowerRevisionConflict):
            store.update(expected_revision=0, lock_seconds=60)
        for delay in (True, 0, 301, "300", 300.0):
            with self.assertRaises(PowerSettingsError):
                store.update(expected_revision=1, lock_seconds=delay)
        with self.assertRaises(PowerSettingsError):
            store.update(expected_revision=1, lock_seconds=60, authorize=lambda: False)
        self.assertEqual(store.load(), result)
        (self.directory / "lock.json").write_text('{"version":1,"revision":1,"lock_seconds":true}')
        with self.assertRaises(PowerSettingsError):
            store.load()

    def test_strict_bounded_values(self):
        for changes in ({"expected_revision": True}, {"expected_revision": -1},
                        {"display_off_seconds": True}, {"display_off_seconds": 31},
                        {"deep_sleep_seconds": "600"}, {"deep_sleep_seconds": 600.0},
                        {"display_off_seconds": 300, "deep_sleep_seconds": 300}):
            with self.subTest(changes=changes), self.assertRaises(PowerSettingsError):
                self.update(**changes)
        self.assertEqual(self.store.load()["revision"], 0)

    def test_cross_instance_cas_one_winner(self):
        self.store.load()
        barrier = threading.Barrier(2)
        results = []

        def attempt():
            store = PowerSettingsStore(self.directory)
            barrier.wait()
            try:
                store.update(expected_revision=0, display_off_seconds=60, deep_sleep_seconds=600)
                results.append("won")
            except PowerRevisionConflict:
                results.append("conflict")

        workers = [threading.Thread(target=attempt) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(2)
            self.assertFalse(worker.is_alive())
        self.assertCountEqual(results, ["won", "conflict"])
        self.assertEqual(self.store.load()["revision"], 1)

    def test_safe_integer_revision_boundary_and_exhaustion(self):
        maximum = (1 << 53) - 1
        snapshot = self.update()
        path = self.directory / "power.json"
        path.write_text(json.dumps(dict(snapshot, version=1, revision=maximum - 1)))
        result = self.update(expected_revision=maximum - 1)
        self.assertEqual(result["revision"], maximum)
        self.assertEqual(PowerSettingsStore(self.directory).load(), result)
        original = path.read_bytes()
        for revision in (maximum, maximum + 1):
            with self.subTest(revision=revision), self.assertRaises(PowerSettingsError):
                self.update(expected_revision=revision)
            self.assertEqual(path.read_bytes(), original)
        path.write_text(json.dumps(dict(snapshot, version=1, revision=maximum + 1)))
        invalid = path.read_bytes()
        with self.assertRaises(PowerSettingsError):
            self.store.load()
        self.assertEqual(path.read_bytes(), invalid)

    def test_corruption_unknown_schema_and_duplicates_never_reset(self):
        self.store.load()
        path = self.directory / "power.json"
        for raw in (b"", b"garbage", b'{"version":2}', b'{"version":1,"version":1}', b"x" * 1025):
            path.write_bytes(raw)
            path.chmod(0o600)
            with self.assertRaises(PowerSettingsError):
                self.store.load()
            with self.assertRaises(PowerSettingsError):
                self.update()
            self.assertEqual(path.read_bytes(), raw)

    def test_unsafe_file_refused_without_chmod(self):
        self.update()
        path = self.directory / "power.json"
        path.chmod(0o644)
        with self.assertRaises(PowerSettingsError):
            self.store.load()
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)

    def test_symlinks_hardlinks_and_fifo_refused(self):
        self.store.load()
        path = self.directory / "power.json"
        target = self.directory / "sentinel"
        target.write_bytes(b"untouched")
        target.chmod(0o600)
        path.symlink_to(target)
        with self.assertRaises(PowerSettingsError):
            self.update()
        path.unlink()
        os.link(target, path)
        with self.assertRaises(PowerSettingsError):
            self.store.load()
        path.unlink()
        os.mkfifo(path, 0o600)
        with self.assertRaises(PowerSettingsError):
            self.store.load()
        self.assertEqual(target.read_bytes(), b"untouched")

    def test_parent_alias_refused(self):
        alias = self.directory.parent / "alias"
        alias.symlink_to(self.directory.parent, target_is_directory=True)
        with self.assertRaises(PowerSettingsError):
            PowerSettingsStore(alias / "power").load()

    def test_precommit_fault_preserves_snapshot_and_cleans_temporary(self):
        original = self.update()
        with patch("sage_lantern.power_settings.os.replace", side_effect=OSError("private path")):
            with self.assertRaises(PowerSettingsError) as caught:
                self.update(expected_revision=1)
        self.assertEqual(str(caught.exception), "power_settings_unavailable")
        self.assertEqual(self.store.load(), original)
        self.assertEqual(list(self.directory.glob(".power-*")), [])

    def test_final_guard_denies_commit(self):
        with self.assertRaises(PowerSettingsError):
            self.update(authorize=lambda: False)
        self.assertEqual(self.store.load()["revision"], 0)

    def test_lock_contention_is_bounded(self):
        self.store.load()
        descriptor = os.open(self.directory / "power.lock", os.O_RDWR)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            started = time.monotonic()
            with self.assertRaises(PowerSettingsError):
                self.store.load()
            self.assertLess(time.monotonic() - started, 1)
        finally:
            os.close(descriptor)

    def test_postreplace_fault_reconciles_whole_snapshot(self):
        real_fsync = os.fsync
        calls = []

        def fault(descriptor):
            calls.append(descriptor)
            if len(calls) == 2:
                raise OSError("uncertain durability")
            real_fsync(descriptor)

        with patch("sage_lantern.power_settings.os.fsync", side_effect=fault):
            with self.assertRaises(PowerSettingsError):
                self.update()
        self.assertEqual(self.store.load(), dict(revision=1, display_off_seconds=60,
                                               deep_sleep_seconds=600, deep_sleep_available=False))


if __name__ == "__main__":
    unittest.main()
