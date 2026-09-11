import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from sage_lantern.suspend_system import (
    BUSCTL, CGROUP, COUNTER, FENCE, LLM, MAX_OUTPUT, SLEEP_UNITS, STOP_SERVICES,
    SYSTEMCTL, SuspendSystem, _list_directories, _read_file,
)


SERVICE = "sage-lantern-camera.service"
GROUP = CGROUP / "system.slice" / SERVICE


class FakeSystem:
    def __init__(self):
        self.units = {
            unit: dict(LoadState="loaded", ActiveState="inactive", Job="")
            for unit in SLEEP_UNITS
        }
        self.units[LLM] = dict(LoadState="loaded", ActiveState="active", Job="", MainPID="123")
        for unit in STOP_SERVICES:
            self.units[unit] = dict(
                LoadState="loaded", ActiveState="active", Job="",
                ControlGroup="/system.slice/" + unit, KillMode="control-group",
                Slice="system.slice",
                Conditions=f'a(sbbsi) 1 "ConditionPathExists" false true "{FENCE}" 0',
            )
        self.calls = []
        self.reads = []
        self.files = {CGROUP / "cgroup.controllers": b"cpu memory\n"}
        self.directories = {GROUP: []}
        self.counter = 5
        self.increment = 1
        self.now = 0
        self.pauses = []
        self.on_pause = lambda: None
        self.after_stop = lambda: None
        self.after_sleep = lambda: None
        self.override = None

    def run(self, argv, *, timeout):
        self.calls.append((argv, timeout))
        if self.override:
            return self.override(argv)
        if argv[0] == BUSCTL:
            for unit in STOP_SERVICES:
                encoded = "".join(character if character.isalnum() else f"_{ord(character):02x}" for character in unit)
                if argv[-3] == "/org/freedesktop/systemd1/unit/" + encoded:
                    return subprocess.CompletedProcess(argv, 0, self.units[unit]["Conditions"].encode())
            raise AssertionError("unexpected bus object")
        if argv[1] == "show":
            fields = argv[3].removeprefix("--property=").split(",")
            data = "\n".join(f"{field}={self.units[argv[-1]][field]}" for field in fields)
            return subprocess.CompletedProcess(argv, 0, data.encode())
        if argv[1:] == ["stop", SERVICE]:
            self.units[SERVICE]["ActiveState"] = "inactive"
            self.after_stop()
        elif argv[1:] == ["start", "suspend.target"]:
            self.counter += self.increment
            self.after_sleep()
        else:
            raise AssertionError("unexpected mutation")
        return subprocess.CompletedProcess(argv, 0, b"")

    def read(self, path, limit):
        self.reads.append(path)
        if path in self.files:
            value = self.files[path]
            if isinstance(value, Exception):
                raise value
            return value
        if path == COUNTER:
            return f"{self.counter}\n".encode()
        if path.name == "cgroup.procs":
            return b""
        raise FileNotFoundError(path)

    def dirs(self, path):
        if path not in self.directories:
            raise FileNotFoundError(path)
        return self.directories[path]

    def adapter(self, **overrides):
        options = dict(run=self.run, read_file=self.read, list_directories=self.dirs,
                       clock=lambda: self.now, uid=lambda: 0, platform=lambda: "linux",
                       sleep=self.pause)
        options.update(overrides)
        return SuspendSystem(**options)

    def pause(self, seconds):
        self.pauses.append(seconds)
        self.now += seconds
        self.on_pause()


class SuspendSystemTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeSystem()
        self.system = self.fake.adapter()

    def test_stop_verifies_recursive_empty_and_preserves_llm(self):
        self.fake.directories[GROUP] = ["child"]
        self.fake.directories[GROUP / "child"] = []
        result = self.system.stop_verified(SERVICE)
        self.assertTrue(result.confirmed)
        self.assertIn(GROUP / "child/cgroup.procs", self.fake.reads)
        self.assertEqual([argv[1:] for argv, _ in self.fake.calls if argv[0] == SYSTEMCTL and argv[1] != "show"], [["stop", SERVICE]])
        self.assertTrue(all(argv[0] in (SYSTEMCTL, BUSCTL) and 0 < timeout <= 30 for argv, timeout in self.fake.calls))

    def test_all_guarded_units(self):
        self.assertTrue(self.system.verify_stop_guards().confirmed)
        self.assertEqual({argv[-1] for argv, _ in self.fake.calls if argv[0] == SYSTEMCTL}, STOP_SERVICES)

    def test_public_idle_probe_has_no_mutations_and_is_not_resume(self):
        result = self.system.verify_sleep_idle()
        self.assertTrue(result.confirmed)
        self.assertEqual(result.reason, "sleep_idle_verified")
        self.assertIsNone(result.before_success)
        self.assertTrue(all(argv[1] == "show" for argv, _ in self.fake.calls))
        self.fake.units["suspend.target"]["Job"] = "42"
        self.assertFalse(self.system.verify_sleep_idle().confirmed)

    def test_public_inactive_probe_checks_processes_jobs_without_stopping(self):
        self.assertFalse(self.system.service_inactive_verified(SERVICE).confirmed)
        self.fake.units[SERVICE]["ActiveState"] = "inactive"
        result = self.system.service_inactive_verified(SERVICE)
        self.assertTrue(result.confirmed)
        self.assertEqual(result.reason, "inactive_verified")
        self.fake.units[SERVICE]["Job"] = "42"
        self.assertFalse(self.system.service_inactive_verified(SERVICE).confirmed)
        self.fake.units[SERVICE]["Job"] = ""
        self.fake.files[GROUP / "cgroup.procs"] = b"42\n"
        self.assertFalse(self.system.service_inactive_verified(SERVICE).confirmed)
        self.assertTrue(all(argv[0] == BUSCTL or argv[1] == "show" for argv, _ in self.fake.calls))

    def test_parent_inactive_api_preserves_read_only_verification(self):
        self.assertFalse(self.system.inactive_verified(SERVICE).confirmed)
        self.fake.units[SERVICE]["ActiveState"] = "inactive"
        self.assertEqual(self.system.inactive_verified(SERVICE),
                         self.system.service_inactive_verified(SERVICE))
        self.assertTrue(self.system.inactive_verified(SERVICE).confirmed)
        self.fake.units[SERVICE]["Job"] = "42"
        self.assertFalse(self.system.inactive_verified(SERVICE).confirmed)
        self.assertFalse(self.system.inactive_verified(LLM).confirmed)
        self.assertTrue(all(argv[0] == BUSCTL or argv[1] == "show" for argv, _ in self.fake.calls))

    def test_guard_requires_exact_negative_nontrigger_condition(self):
        for condition in ("", "[unprintable]", f'a(sbbsi) 1 "ConditionPathExists" false false "{FENCE}" 0',
                          f'a(sbbsi) 1 "ConditionPathExists" true true "{FENCE}" 0',
                          f'a(sbbsi) 1 "ConditionPathExists" false true "{FENCE}.other" 0',
                          f'a(sbbsi) 1 "ConditionPathExists" false true "{FENCE}" unknown',
                          f'a(sbbsi) 2 "ConditionPathExists" false true "{FENCE}" 0'):
            with self.subTest(condition=condition):
                self.fake.units[SERVICE]["Conditions"] = condition
                self.assertFalse(self.system.stop_verified(SERVICE).confirmed)
        self.assertFalse(any(argv[1] == "stop" for argv, _ in self.fake.calls))

    def test_guard_accepts_other_conditions_and_previous_results(self):
        for result in ("0", "1", "-1"):
            self.fake.units[SERVICE]["Conditions"] = (
                f'a(sbbsi) 2 "ConditionPathExists" false false "/etc/example" 1 '
                f'"ConditionPathExists" false true "{FENCE}" {result}')
            self.assertTrue(self.system.verify_stop_guards().confirmed)

    def test_root_linux_and_allowlist_prechecks_have_no_effects(self):
        for options in (dict(uid=lambda: 1000), dict(platform=lambda: "darwin")):
            adapter = self.fake.adapter(**options)
            self.assertFalse(adapter.stop_verified(SERVICE).confirmed)
            self.assertFalse(adapter.suspend_verified().confirmed)
        for unit in (LLM, "sage.service", "--help", SERVICE + "; reboot", None, []):
            self.assertFalse(self.system.stop_verified(unit).confirmed)
        self.assertEqual(self.fake.calls, [])

    def test_pending_job_or_unsafe_killmode_prevents_stop(self):
        for key, value in (("Job", "42"), ("KillMode", "process"),
                           ("LoadState", "not-found"), ("ActiveState", "activating")):
            with self.subTest(key=key):
                fake = FakeSystem()
                fake.units[SERVICE][key] = value
                self.assertFalse(fake.adapter().stop_verified(SERVICE).confirmed)
                self.assertFalse(any(argv[1] == "stop" for argv, _ in fake.calls))

    def test_residual_child_process_blocks_confirmation(self):
        self.fake.directories[GROUP] = ["child"]
        self.fake.directories[GROUP / "child"] = []
        self.fake.files[GROUP / "child/cgroup.procs"] = b"456\n"
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)

    def test_post_stop_active_job_and_llm_restart_fail_closed(self):
        for unit, key, value in ((SERVICE, "ActiveState", "active"), (SERVICE, "Job", "15"),
                                 (SERVICE, "KillMode", "mixed"), (LLM, "MainPID", "456")):
            with self.subTest(key=key):
                fake = FakeSystem()
                fake.after_stop = lambda: fake.units[unit].update({key: value})
                self.assertFalse(fake.adapter().stop_verified(SERVICE).confirmed)

    def test_disappeared_validated_cgroup_is_empty(self):
        self.fake.after_stop = lambda: self.fake.directories.clear()
        self.assertTrue(self.system.stop_verified(SERVICE).confirmed)

    def test_unknown_active_group_and_nondefault_empty_slice_rejected(self):
        self.fake.units[SERVICE]["ControlGroup"] = ""
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)
        self.fake.units[SERVICE].update(ActiveState="inactive", Slice="other.slice")
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)
        self.fake.units[SERVICE]["Slice"] = "system.slice"
        self.assertTrue(self.system.stop_verified(SERVICE).confirmed)

    def test_invalid_cgroup_paths(self):
        for path in ("/", "//system.slice/x", "/system.slice/../x", "relative", "/system.slice/./x"):
            self.fake.units[SERVICE]["ControlGroup"] = path
            self.assertFalse(self.system.stop_verified(SERVICE).confirmed)

    def test_missing_cgroup_v2_and_unreadable_processes_fail_closed(self):
        self.fake.files[CGROUP / "cgroup.controllers"] = FileNotFoundError()
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)
        self.fake.files[CGROUP / "cgroup.controllers"] = b"memory"
        self.fake.files[GROUP / "cgroup.procs"] = PermissionError()
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)

    def test_recursion_bounded_and_child_traversal_rejected(self):
        for children in ([".."], ["/tmp"], ["child"] * 129):
            self.fake.directories[GROUP] = children
            self.assertFalse(self.system.stop_verified(SERVICE).confirmed)

    def test_suspend_requires_real_counter_increment(self):
        result = self.system.suspend_verified()
        self.assertTrue(result.confirmed)
        self.assertEqual((result.before_success, result.after_success), (5, 6))
        self.assertEqual([argv[1:] for argv, _ in self.fake.calls if argv[1] != "show"], [["start", "suspend.target"]])

    def test_post_suspend_target_drain_is_polled_until_proven_idle(self):
        self.fake.after_sleep = lambda: self.fake.units["suspend.target"].update(ActiveState="active", Job="12")
        def drain():
            if len(self.fake.pauses) == 1:
                self.fake.units["suspend.target"].update(ActiveState="deactivating", Job="13")
            else:
                self.fake.units["suspend.target"].update(ActiveState="inactive", Job="")
        self.fake.on_pause = drain
        self.assertTrue(self.system.suspend_verified().confirmed)
        self.assertEqual(len(self.fake.pauses), 2)
        self.assertEqual(sum(argv[1] == "start" for argv, _ in self.fake.calls), 1)

    def test_persistent_post_suspend_job_exhausts_original_deadline(self):
        self.fake.after_sleep = lambda: self.fake.units["suspend.target"].update(Job="12")
        self.assertFalse(self.fake.adapter(timeout_seconds=1).suspend_verified().confirmed)
        self.assertAlmostEqual(self.fake.now, 1)
        self.assertLessEqual(len(self.fake.pauses), 11)
        self.assertEqual(sum(argv[1] == "start" for argv, _ in self.fake.calls), 1)

    def test_extra_counter_increment_while_draining_fails_closed(self):
        self.fake.after_sleep = lambda: self.fake.units["suspend.target"].update(ActiveState="deactivating")
        def drain():
            self.fake.units["suspend.target"]["ActiveState"] = "inactive"
            self.fake.counter += 1
        self.fake.on_pause = drain
        self.assertFalse(self.system.suspend_verified().confirmed)

    def test_malformed_post_suspend_observation_does_not_poll(self):
        self.fake.after_sleep = lambda: self.fake.units["suspend.target"].update(Job="unknown")
        self.assertFalse(self.system.suspend_verified().confirmed)
        self.assertEqual(self.fake.pauses, [])

    def test_preexisting_busy_target_is_not_polled_or_submitted(self):
        self.fake.units["suspend.target"]["ActiveState"] = "deactivating"
        self.assertFalse(self.system.suspend_verified().confirmed)
        self.assertEqual(self.fake.pauses, [])
        self.assertFalse(any(argv[1] == "start" for argv, _ in self.fake.calls))

    def test_exit_zero_alone_and_multiple_cycles_not_proof(self):
        for increment in (0, 2, -1):
            self.fake.increment = increment
            self.assertFalse(self.system.suspend_verified().confirmed)

    def test_missing_invalid_counter_prevents_sleep_submission(self):
        for value in (FileNotFoundError(), PermissionError(), b"-1", b"1.0", b"NaN", b"1\n2", b"9" * 33):
            fake = FakeSystem()
            fake.files[COUNTER] = value
            self.assertFalse(fake.adapter().suspend_verified().confirmed)
            self.assertFalse(any(argv[1] == "start" for argv, _ in fake.calls))

    def test_pending_sleep_job_before_and_after_sleep(self):
        for unit in SLEEP_UNITS:
            for before in (True, False):
                with self.subTest(unit=unit, before=before):
                    fake = FakeSystem()
                    if before:
                        fake.units[unit]["Job"] = "42"
                    else:
                        fake.after_sleep = lambda: fake.units[unit].update(Job="42")
                    self.assertFalse(fake.adapter().suspend_verified().confirmed)
                    if before:
                        self.assertFalse(any(argv[1] == "start" for argv, _ in fake.calls))

    def test_active_suspend_service_or_llm_changed_not_resume_proof(self):
        for unit, key, value in (("systemd-suspend.service", "ActiveState", "active"),
                                 (LLM, "MainPID", "456"), (LLM, "ActiveState", "inactive")):
            fake = FakeSystem()
            fake.after_sleep = lambda: fake.units[unit].update({key: value})
            self.assertFalse(fake.adapter().suspend_verified().confirmed)

    def test_nonzero_malformed_output_timeout_are_unknown(self):
        for value in (b"LoadState=loaded\nLoadState=loaded", b"MainPID=123", b"\xff", b"x" * (MAX_OUTPUT + 1)):
            self.fake.override = lambda argv: subprocess.CompletedProcess(argv, 0, value)
            self.assertFalse(self.system.suspend_verified().confirmed)
        self.fake.override = lambda argv: subprocess.CompletedProcess(argv, 1, b"")
        self.assertFalse(self.system.stop_verified(SERVICE).confirmed)
        def timeout(argv):
            raise subprocess.TimeoutExpired(argv, 30)
        self.fake.override = timeout
        self.assertFalse(self.system.suspend_verified().confirmed)

    def test_deadline_after_command_cannot_confirm(self):
        self.fake.after_sleep = lambda: setattr(self.fake, "now", 31)
        self.assertFalse(self.system.suspend_verified().confirmed)
        self.assertEqual(sum(argv[1] == "start" for argv, _ in self.fake.calls), 1)

    def test_concurrent_operation_returns_busy(self):
        with self.system._lock:
            self.assertEqual(self.system.suspend_verified().reason, "busy")
            self.assertEqual(self.system.stop_verified(SERVICE).reason, "busy")
        self.assertEqual(self.fake.calls, [])

    def test_invalid_deadlines_rejected(self):
        for timeout in (True, 0, -1, 91, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                self.fake.adapter(timeout_seconds=timeout)


class SafeFileTests(unittest.TestCase):
    def test_bounded_file_and_symlinks(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            file = root / "value"
            file.write_bytes(b"1234")
            self.assertEqual(_read_file(file, 4), b"1234")
            with self.assertRaises(ValueError):
                _read_file(file, 3)
            link = root / "link"
            link.symlink_to(file)
            with self.assertRaises(OSError):
                _read_file(link, 10)
            with self.assertRaises(ValueError):
                _list_directories(root)
            linked_parent = root / "parent"
            linked_parent.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ValueError):
                _read_file(linked_parent / "value", 10)

    def test_fifo_is_rejected_without_blocking(self):
        with tempfile.TemporaryDirectory() as directory:
            fifo = Path(directory).resolve() / "fifo"
            os.mkfifo(fifo)
            with self.assertRaises(ValueError):
                _read_file(fifo, 10)


if __name__ == "__main__":
    unittest.main()
