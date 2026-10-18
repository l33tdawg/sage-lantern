from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sage_lantern.suspend_kiosk import KioskBinding, KioskFence, LAUNCHER, PROFILE, Process, discover_launcher, load_kiosk_binding
from sage_lantern import suspend_kiosk


class FakeProcesses:
    def __init__(self):
        self.table = {
            1735: Process(1735, 100, 12345, 1000, "/usr/bin/bash", ("bash", LAUNCHER), "S"),
            2000: Process(2000, 1735, 12346, 1000, "/usr/lib/chromium/chromium", ("chromium", PROFILE), "S"),
            2001: Process(2001, 2000, 12347, 1000, "/usr/lib/chromium/chromium", ("chromium", "--type=renderer"), "S"),
            900: Process(900, 100, 12000, 0, "/usr/lib/xorg/Xorg", ("Xorg",), "S"),
            3000: Process(3000, 100, 13000, 1000, "/usr/bin/bash", ("bash",), "S"),
        }
        self.fence = "present"
        self.calls = []
        self.closed = []
        self.fail_host = False
        self.ignore_stop = False
        self.ignore_kill = False

    def validate_host(self):
        if self.fail_host:
            raise RuntimeError("untrusted host")

    def snapshot(self):
        return dict(self.table)

    def read(self, pid):
        return self.table[pid]

    def fence_state(self):
        return self.fence

    def open(self, process):
        return (process.pid, process.start_ticks)

    def exited(self, handle):
        current = self.table.get(handle[0])
        return current is None or current.start_ticks != handle[1]

    def send(self, handle, action):
        self.calls.append((handle, action))
        if self.exited(handle):
            raise ProcessLookupError()
        process = self.table[handle[0]]
        if action == signal.SIGSTOP and not self.ignore_stop:
            self.table[process.pid] = replace(process, state="T")
        elif action == signal.SIGCONT:
            self.table[process.pid] = replace(process, state="S")
        elif action == signal.SIGKILL and not self.ignore_kill:
            del self.table[process.pid]

    def close(self, handle):
        self.closed.append(handle)


class KioskFenceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeProcesses()
        self.locked = True
        self.kiosk = KioskFence(binding=KioskBinding.legacy(), launcher_pid=1735, launcher_start_ticks=12345,
                                auth_locked=lambda: self.locked, backend=self.backend,
                                timeout_seconds=0.03)

    def test_stop_then_clear_exact_tree_preserves_x_and_other_shell(self):
        self.assertIs(self.kiosk.quiesce(), True)
        self.assertEqual(self.backend.calls[0], ((1735, 12345), signal.SIGSTOP))
        killed = {handle[0] for handle, action in self.backend.calls if action == signal.SIGKILL}
        self.assertEqual(killed, {2000, 2001})
        self.assertEqual(self.backend.table[1735].state, "T")
        self.assertEqual(self.backend.table[900].state, "S")
        self.assertEqual(self.backend.table[3000].state, "S")

    def test_resume_requires_locked_auth_and_removed_fence(self):
        self.assertIs(self.kiosk.quiesce(), True)
        self.assertIs(self.kiosk.resume(), False)
        self.backend.fence = "absent"
        for locked in (False, None, 1, "true"):
            self.locked = locked
            self.assertIs(self.kiosk.resume(), False)
        self.locked = True
        self.assertIs(self.kiosk.resume(), True)
        self.assertEqual(self.backend.table[1735].state, "S")
        self.assertIs(self.kiosk.resume(), False)

    def test_pid_reuse_wrong_uid_exe_or_argv_rejected_before_signal(self):
        original = self.backend.table[1735]
        for changes in ({"start_ticks": 999}, {"uid": 0}, {"executable": "/usr/bin/dash"},
                        {"argv": ("bash", "-c", LAUNCHER)}, {"argv": ("bash", "/tmp/kiosk")}):
            with self.subTest(changes=changes):
                self.backend.table[1735] = replace(original, **changes)
                self.assertIs(self.kiosk.quiesce(), False)
                self.assertEqual(self.backend.calls, [])

    def test_missing_invalid_fence_or_untrusted_host_rejected(self):
        for state in ("absent", "invalid"):
            self.backend.fence = state
            self.assertIs(self.kiosk.quiesce(), False)
        self.backend.fence = "present"
        self.backend.fail_host = True
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.calls, [])

    def test_unexpected_descendant_fails_without_resuming_or_killing_it(self):
        self.backend.table[2000] = replace(self.backend.table[2000], executable="/usr/bin/unreviewed")
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.table[1735].state, "S")
        self.assertNotIn(signal.SIGKILL, [action for _, action in self.backend.calls])
        self.assertIs(self.kiosk.resume(), False)

    def test_wrong_uid_descendant_fails_closed(self):
        self.backend.table[2000] = replace(self.backend.table[2000], uid=0)
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.table[1735].state, "S")

    def test_stop_or_kill_timeout_never_reports_clear(self):
        self.backend.ignore_stop = True
        self.assertIs(self.kiosk.quiesce(), False)
        self.backend.ignore_stop = False
        self.backend.ignore_kill = True
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertIs(self.kiosk.resume(), False)

    def test_profile_owner_outside_tree_blocks_clear_without_killing(self):
        self.backend.table[4000] = Process(4000, 1, 15000, 1000, "/usr/lib/chromium/chromium",
                                          ("chromium", PROFILE), "S")
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.table[4000].state, "S")

    def test_descendant_created_during_stop_is_discovered(self):
        original_send = self.backend.send
        def send(handle, action):
            if handle[0] == 2000 and action == signal.SIGSTOP:
                self.backend.table[2002] = Process(2002, 2000, 16000, 1000, "/usr/lib/chromium/chromium",
                                                  ("chromium", "--type=gpu-process"), "S")
            original_send(handle, action)
        self.backend.send = send
        self.assertIs(self.kiosk.quiesce(), True)
        self.assertNotIn(2002, self.backend.table)

    def test_bounded_descendant_limit(self):
        kiosk = KioskFence(binding=KioskBinding.legacy(), launcher_pid=1735, launcher_start_ticks=12345, auth_locked=lambda: True,
                           backend=self.backend, max_descendants=1)
        self.assertIs(kiosk.quiesce(), False)
        self.assertEqual(self.backend.table[1735].state, "S")

    def test_close_never_resumes(self):
        self.assertIs(self.kiosk.quiesce(), True)
        self.kiosk.close()
        self.assertEqual(self.backend.table[1735].state, "T")
        self.assertNotIn(signal.SIGCONT, [action for _, action in self.backend.calls])
        self.assertIs(self.kiosk.resume(), False)

    def test_launcher_reuse_after_quiesce_never_continues_new_process(self):
        self.assertIs(self.kiosk.quiesce(), True)
        self.backend.fence = "absent"
        self.backend.table[1735] = replace(self.backend.table[1735], start_ticks=99999)
        self.assertIs(self.kiosk.resume(), False)
        self.assertNotIn(signal.SIGCONT, [action for _, action in self.backend.calls])

    def test_repeat_sleep_cycle_reuses_verified_launcher(self):
        self.assertIs(self.kiosk.quiesce(), True)
        self.backend.fence = "absent"
        self.assertIs(self.kiosk.resume(), True)
        self.backend.fence = "present"
        self.backend.table[2005] = Process(2005, 1735, 20000, 1000, "/usr/bin/sleep", ("sleep", "2"), "S")
        self.assertIs(self.kiosk.quiesce(), True)
        self.assertNotIn(2005, self.backend.table)

    def test_native_browser_executable_and_flattened_argv(self):
        self.backend.table[2000] = replace(self.backend.table[2000],
                                          executable="/usr/lib/chromium/chromium-bin",
                                          argv=("/usr/lib/chromium/chromium-bin --kiosk " + PROFILE,))
        self.assertIs(self.kiosk.quiesce(), True)

    def test_flattened_profile_outside_tree_blocks_without_broad_kill(self):
        self.backend.table[4000] = Process(4000, 1, 15000, 1000, "/usr/lib/chromium/chromium-bin",
                                          ("chromium-bin --kiosk " + PROFILE,), "S")
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.table[4000].state, "S")

    def test_discovery_returns_verified_start_ticks_without_signalling(self):
        process = discover_launcher(binding=KioskBinding.legacy(), launcher_pid=1735, backend=self.backend)
        self.assertEqual(process.start_ticks, 12345)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.closed, [(1735, 12345)])

    def test_flattened_launcher_cannot_authorize_process_control(self):
        self.backend.table[1735] = replace(self.backend.table[1735], argv=("bash " + LAUNCHER,))
        with self.assertRaises(RuntimeError):
            discover_launcher(binding=KioskBinding.legacy(), launcher_pid=1735, backend=self.backend)
        self.assertIs(self.kiosk.quiesce(), False)

    def test_preflight_has_no_signals_fence_or_auth_requirement(self):
        before = dict(self.backend.table)
        self.backend.fence = "absent"
        def forbidden():
            raise AssertionError("preflight must not inspect auth or fence")
        self.backend.fence_state = forbidden
        self.kiosk.auth_locked = forbidden
        self.assertIs(self.kiosk.preflight(), True)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(before, self.backend.table)
        self.assertEqual(set(self.backend.closed), {(1735, 12345), (2000, 12346), (2001, 12347)})
        self.assertIsNone(self.kiosk._launcher)
        self.assertEqual(self.kiosk._children, {})
        self.assertFalse(self.kiosk._cleared)

    def test_preflight_rejects_unsupported_child_before_any_signal(self):
        self.backend.table[2001] = replace(self.backend.table[2001], executable="/usr/bin/not-reviewed")
        self.assertIs(self.kiosk.preflight(), False)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.backend.table[1735].state, "S")

    def test_preflight_checks_profile_escape_and_descendant_bound(self):
        self.backend.table[4000] = Process(4000, 1, 15000, 1000, "/usr/lib/chromium/chromium-bin",
                                          ("chromium-bin --kiosk " + PROFILE,), "S")
        self.assertIs(self.kiosk.preflight(), False)
        del self.backend.table[4000]
        self.kiosk.max_descendants = 1
        self.assertIs(self.kiosk.preflight(), False)
        self.assertEqual(self.backend.calls, [])

    def test_preflight_validates_executable_trust_and_closes_partial_handles(self):
        original = self.backend.open
        opened = []
        def open_process(process):
            if process.pid != 1735:
                raise RuntimeError("executable is not root-owned")
            handle = original(process)
            opened.append(handle)
            return handle
        self.backend.open = open_process
        self.assertIs(self.kiosk.preflight(), False)
        self.assertEqual(self.backend.closed, opened)
        self.assertEqual(self.backend.calls, [])

    def test_preflight_rejects_launcher_pid_reuse_during_inventory(self):
        original = self.backend.snapshot
        count = 0
        def snapshot():
            nonlocal count
            count += 1
            if count == 2:
                self.backend.table[1735] = replace(self.backend.table[1735], start_ticks=99999)
            return original()
        self.backend.snapshot = snapshot
        self.assertIs(self.kiosk.preflight(), False)
        self.assertEqual(len(self.backend.closed), 3)
        self.assertEqual(self.backend.calls, [])

    def test_preflight_changing_inventory_is_bounded(self):
        original = self.backend.snapshot
        count = 0
        def snapshot():
            nonlocal count
            count += 1
            self.backend.table[2001] = replace(self.backend.table[2001], start_ticks=20000 + count)
            return original()
        self.backend.snapshot = snapshot
        self.assertIs(self.kiosk.preflight(), False)
        self.assertEqual(count, self.kiosk.scan_rounds * 2)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(len(self.backend.closed), self.kiosk.scan_rounds * 3)

    def test_preflight_preserves_existing_quiesced_lease(self):
        self.assertIs(self.kiosk.quiesce(), True)
        lease = self.kiosk._launcher
        children = dict(self.kiosk._children)
        calls = list(self.backend.calls)
        self.assertIs(self.kiosk.preflight(), True)
        self.assertEqual(self.kiosk._launcher, lease)
        self.assertEqual(self.kiosk._children, children)
        self.assertTrue(self.kiosk._cleared)
        self.assertEqual(self.backend.calls, calls)

    def retained_kiosk(self):
        self.backend.table[1763] = Process(1763, 1735, 12350, 1000, "/usr/bin/ssh-agent",
                                          ("/usr/bin/ssh-agent", "/usr/local/bin/sage-lantern-session"), "S")
        self.backend.table[1768] = Process(1768, 1735, 12351, 1000, "/usr/bin/kwin_x11",
                                          ("kwin_x11", "--replace"), "S")
        self.backend.table[2000] = replace(self.backend.table[2000], executable="/usr/lib/chromium/chromium-bin",
                                          argv=("chromium-bin", "--kiosk", PROFILE))
        return KioskFence(binding=KioskBinding.legacy(), launcher_pid=1735, launcher_start_ticks=12345,
                          auth_locked=lambda: self.locked, backend=self.backend,
                          retained_session=(self.backend.table[1763], self.backend.table[1768]),
                          timeout_seconds=0.05)

    def test_native_retained_session_and_browser_cats_never_signal_retained(self):
        kiosk = self.retained_kiosk()
        for pid in (2002, 2003):
            self.backend.table[pid] = Process(pid, 2000, 12500 + pid, 1000, "/usr/bin/cat", ("cat",), "S")
        self.backend.table[1770] = Process(1770, 1768, 12360, 1000, "/usr/bin/session-helper", ("session-helper",), "S")
        self.assertIs(kiosk.preflight(), True)
        self.assertIs(kiosk.quiesce(), True)
        self.backend.fence = "absent"
        self.assertIs(kiosk.resume(), True)
        signalled = {handle[0] for handle, _ in self.backend.calls}
        self.assertTrue({1763, 1768, 1770, 900}.isdisjoint(signalled))
        for pid in (1763, 1768, 1770, 900):
            self.assertEqual(self.backend.table[pid].state, "S")
        self.assertTrue({2000, 2001, 2002, 2003}.isdisjoint(self.backend.table))

    def test_unpinned_compositor_or_agent_fails_without_signals(self):
        self.retained_kiosk()
        self.assertIs(self.kiosk.preflight(), False)
        self.assertIs(self.kiosk.quiesce(), False)
        self.assertEqual(self.backend.calls, [])

    def test_retained_exact_argv_parent_uid_and_ticks_rechecked(self):
        kiosk = self.retained_kiosk()
        original = self.backend.table[1768]
        for changes in ({"argv": ("kwin_x11",)}, {"parent": 1}, {"uid": 0}, {"start_ticks": 99999},
                        {"executable": "/usr/bin/other"}):
            with self.subTest(changes=changes):
                self.backend.table[1768] = replace(original, **changes)
                self.assertIs(kiosk.preflight(), False)
                self.assertIs(kiosk.quiesce(), False)
                self.assertEqual(self.backend.calls, [])

    def test_retained_tree_cannot_hide_browser_or_profile(self):
        kiosk = self.retained_kiosk()
        for executable, argv in (("/usr/lib/chromium/chromium-bin", ("chromium-bin",)),
                                 ("/usr/bin/session-helper", ("helper " + PROFILE,))):
            self.backend.table[1770] = Process(1770, 1768, 12360, 1000, executable, argv, "S")
            self.assertIs(kiosk.preflight(), False)
            self.assertIs(kiosk.quiesce(), False)
            self.assertEqual(self.backend.calls, [])

    def test_new_retained_profile_child_blocks_before_kill(self):
        kiosk = self.retained_kiosk()
        original_send = self.backend.send
        def send(handle, action):
            original_send(handle, action)
            if handle[0] == 1735 and action == signal.SIGSTOP:
                self.backend.table[1770] = Process(1770, 1768, 12360, 1000, "/usr/bin/session-helper",
                                                  ("helper " + PROFILE,), "S")
        self.backend.send = send
        self.assertIs(kiosk.quiesce(), False)
        self.assertEqual(self.backend.calls, [((1735, 12345), signal.SIGSTOP)])
        self.assertEqual(self.backend.table[1768].state, "S")

    def test_cat_requires_verified_kiosk_browser_ancestor_and_plain_argv(self):
        kiosk = self.retained_kiosk()
        cat = Process(2002, 1735, 15000, 1000, "/usr/bin/cat", ("cat",), "S")
        for process in (cat, replace(cat, parent=2000, argv=("cat", "/some/file"))):
            self.backend.table[2002] = process
            self.assertIs(kiosk.preflight(), False)
        self.backend.table[2002] = replace(cat, parent=2000)
        self.backend.table[2000] = replace(self.backend.table[2000], argv=("chromium-bin", PROFILE))
        self.assertIs(kiosk.preflight(), False)
        self.backend.table[2000] = replace(self.backend.table[2000], argv=("chromium-bin --kiosk " + PROFILE,))
        self.assertIs(kiosk.preflight(), True)
        self.assertEqual(self.backend.calls, [])

    def test_retained_change_after_clear_blocks_resume(self):
        kiosk = self.retained_kiosk()
        self.assertIs(kiosk.quiesce(), True)
        self.backend.fence = "absent"
        self.backend.table[1768] = replace(self.backend.table[1768], start_ticks=99999)
        self.assertIs(kiosk.resume(), False)
        self.assertNotIn(signal.SIGCONT, [action for _, action in self.backend.calls])

    def test_retained_constructor_rejects_unreviewed_argv(self):
        self.retained_kiosk()
        pin = replace(self.backend.table[1763], argv=("/usr/bin/ssh-agent", "other-session"))
        with self.assertRaises(ValueError):
            KioskFence(binding=KioskBinding.legacy(), launcher_pid=1735, launcher_start_ticks=12345, auth_locked=lambda: True,
                       backend=self.backend, retained_session=(pin,))


class InstalledBindingTests(unittest.TestCase):
    def records(self, unit="unit-a", account="radxa"):
        return {
            "appliance-bindings.json": json.dumps(dict(schema="sage.lantern.unit-plan.v1",
                                                       unit_id=unit, operator_account=account)),
            "appliance-installed.json": json.dumps(dict(schema="sage.lantern.fresh-app-install.v1",
                                                        unit_id=unit, installed=True, lock_required=True)),
            "unit.env": f"UNIT_ID={unit}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n",
        }

    def load(self, records, account="radxa", uid=1000, home=None):
        with patch.object(suspend_kiosk, "_read_binding_file", side_effect=lambda path: records[path.name]), \
                patch.object(suspend_kiosk.pwd, "getpwnam",
                             return_value=SimpleNamespace(pw_uid=uid, pw_dir=home or f"/home/{account}")):
            return load_kiosk_binding()

    def test_installed_a_and_b_bind_exact_profile_account_and_uid(self):
        for unit, account, uid in (("unit-a", "radxa", 1000), *(("unit-" + letter, "operator", 1001) for letter in "bcdef")):
            with self.subTest(unit=unit):
                binding = self.load(self.records(unit, account), account, uid)
                profile = f"/home/{account}/.config/sage-lantern/{unit}/chromium"
                self.assertEqual(binding.uid, uid)
                self.assertEqual(binding.profile, "--user-data-dir=" + profile)
                self.assertEqual(binding.launcher_argv[0], ("bash", LAUNCHER, "--profile-dir", profile))

    def test_mismatched_invalid_and_duplicate_records_fail_closed(self):
        for filename, value in (
            ("unit.env", "UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n"),
            ("appliance-bindings.json", self.records("unit-b")["appliance-bindings.json"]),
            ("appliance-bindings.json", self.records(account="../radxa")["appliance-bindings.json"]),
            ("appliance-bindings.json", '{"schema":"sage.lantern.unit-plan.v1","unit_id":"unit-a","unit_id":"unit-b"}'),
            ("appliance-installed.json", self.records()["appliance-installed.json"].replace('true', 'false')),
            ("appliance-installed.json", "[]"),
        ):
            with self.subTest(filename=filename, value=value), self.assertRaises(ValueError):
                self.load({**self.records(), filename: value})
        for uid, home in ((0, "/home/radxa"), (1000, "/tmp/radxa")):
            with self.assertRaises(ValueError):
                self.load(self.records(), uid=uid, home=home)

    def test_missing_config_does_not_select_legacy(self):
        with patch.object(suspend_kiosk, "_read_binding_file", side_effect=FileNotFoundError):
            with self.assertRaises(FileNotFoundError):
                KioskFence(launcher_pid=1735, launcher_start_ticks=12345, auth_locked=lambda: True)
            with self.assertRaises(FileNotFoundError):
                discover_launcher(launcher_pid=1735)

    def test_untrusted_path_components_are_rejected(self):
        path = Path("/etc/sage-lantern/appliance-bindings.json")
        for mode, uid in ((stat.S_IFLNK | 0o777, 0), (stat.S_IFREG | 0o664, 0),
                          (stat.S_IFREG | 0o644, 1000)):
            with self.subTest(mode=mode, uid=uid), \
                    patch.object(Path, "lstat", return_value=SimpleNamespace(st_mode=mode, st_uid=uid)), \
                    patch.object(suspend_kiosk.os, "open") as opening:
                with self.assertRaises(RuntimeError):
                    suspend_kiosk._read_binding_file(path)
                opening.assert_not_called()

    def test_opened_config_rechecks_owner_mode_links_type_and_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text("{}")
            baseline = dict(st_mode=stat.S_IFREG | 0o600, st_uid=0, st_nlink=1, st_size=2)
            for changes in ({"st_uid": 1000}, {"st_mode": stat.S_IFREG | 0o666},
                            {"st_nlink": 2}, {"st_size": 65537}, {"st_mode": stat.S_IFIFO | 0o600}):
                with self.subTest(changes=changes), patch.object(suspend_kiosk, "_trusted_file"), \
                        patch.object(os, "fstat", return_value=SimpleNamespace(**(baseline | changes))):
                    with self.assertRaises(RuntimeError):
                        suspend_kiosk._read_binding_file(path)
            with patch.object(suspend_kiosk, "_trusted_file"), \
                    patch.object(os, "fstat", return_value=SimpleNamespace(**baseline)):
                self.assertEqual(suspend_kiosk._read_binding_file(path), "{}")
                path.write_text("x" * 65537)
                with self.assertRaises(RuntimeError):
                    suspend_kiosk._read_binding_file(path)


class FreshKioskTests(unittest.TestCase):
    def fixture(self, unit="unit-a", account="radxa", uid=1000):
        binding = KioskBinding(account, uid, unit)
        backend = FakeProcesses()
        for pid in (1735, 2000, 2001):
            backend.table[pid] = replace(backend.table[pid], uid=uid)
        backend.table[1735] = replace(backend.table[1735], argv=binding.launcher_argv[0])
        backend.table[2000] = replace(backend.table[2000], argv=("chromium", "--kiosk", binding.profile))
        kiosk = KioskFence(binding=binding, launcher_pid=1735, launcher_start_ticks=12345,
                          auth_locked=lambda: True, backend=backend, timeout_seconds=0.05)
        self.addCleanup(kiosk.close)
        return binding, backend, kiosk

    def test_fresh_a_b_discover_preflight_quiesce_and_resume(self):
        for unit, account, uid in (("unit-a", "radxa", 1000), *(("unit-" + letter, "operator", 1001) for letter in "bcdef")):
            with self.subTest(unit=unit):
                binding, backend, kiosk = self.fixture(unit, account, uid)
                self.assertEqual(discover_launcher(binding=binding, launcher_pid=1735, backend=backend).start_ticks, 12345)
                self.assertTrue(kiosk.preflight())
                self.assertEqual(backend.calls, [])
                self.assertTrue(kiosk.quiesce())
                backend.fence = "absent"
                self.assertTrue(kiosk.resume())

    def test_wrong_unit_arbitrary_duplicate_missing_or_flattened_launcher_args_rejected(self):
        binding, backend, kiosk = self.fixture()
        original = backend.table[1735]
        for argv in (("bash", LAUNCHER), (*binding.launcher_argv[0], "extra"),
                     ("bash", LAUNCHER, "--profile-dir", "/tmp/profile"),
                     KioskBinding("radxa", 1000, "unit-b").launcher_argv[0],
                     (" ".join(binding.launcher_argv[0]),),
                     (*binding.launcher_argv[0], *binding.launcher_argv[0][2:])):
            with self.subTest(argv=argv):
                backend.table[1735] = replace(original, argv=argv)
                with self.assertRaises(RuntimeError):
                    discover_launcher(binding=binding, launcher_pid=1735, backend=backend)
                self.assertFalse(kiosk.quiesce())
                self.assertEqual(backend.calls, [])

    def test_wrong_browser_profile_or_duplicate_override_rejected(self):
        binding, backend, kiosk = self.fixture()
        for profiles in ((PROFILE,), (binding.profile, PROFILE), (binding.profile, binding.profile),
                         ("--user-data-dir", binding.profile.split("=", 1)[1])):
            backend.table[2000] = replace(backend.table[2000], argv=("chromium", *profiles))
            self.assertFalse(kiosk.preflight())
            self.assertFalse(kiosk.quiesce())
            self.assertEqual(backend.calls, [])

    def test_fresh_profile_escape_flattened_rejected(self):
        binding, backend, kiosk = self.fixture()
        backend.table[4000] = Process(4000, 1, 15000, binding.uid, "/usr/lib/chromium/chromium",
                                      ("chromium --kiosk " + binding.profile,), "S")
        self.assertFalse(kiosk.preflight())
        self.assertFalse(kiosk.quiesce())
        self.assertEqual(backend.calls, [])

    def test_fresh_retained_tree_cannot_hide_bound_profile(self):
        binding, backend, kiosk = self.fixture()
        retained = Process(1768, 1735, 12351, binding.uid, "/usr/bin/kwin_x11", ("kwin_x11", "--replace"), "S")
        backend.table[1768] = retained
        kiosk.retained_session = (retained,)
        backend.table[1770] = Process(1770, 1768, 12360, binding.uid, "/usr/bin/session-helper",
                                      ("helper " + binding.profile,), "S")
        self.assertFalse(kiosk.preflight())
        self.assertFalse(kiosk.quiesce())
        self.assertEqual(backend.calls, [])

    def test_explicit_legacy_binding_does_not_admit_fresh_launcher(self):
        binding, backend, kiosk = self.fixture()
        with self.assertRaises(RuntimeError):
            discover_launcher(binding=KioskBinding.legacy(), launcher_pid=1735, backend=backend)
        self.assertEqual(backend.calls, [])

    def test_fresh_pid_uid_start_ticks_and_resume_argv_remain_pinned(self):
        binding, backend, kiosk = self.fixture()
        original = backend.table[1735]
        for changes in ({"uid": 0}, {"start_ticks": 999}, {"executable": "/usr/bin/dash"}):
            backend.table[1735] = replace(original, **changes)
            self.assertFalse(kiosk.quiesce())
            self.assertEqual(backend.calls, [])
        backend.table[1735] = original
        self.assertTrue(kiosk.quiesce())
        backend.fence = "absent"
        backend.table[1735] = replace(original, argv=KioskBinding.legacy().launcher_argv[0])
        self.assertFalse(kiosk.resume())
        self.assertNotIn(signal.SIGCONT, [action for _, action in backend.calls])


if __name__ == "__main__":
    unittest.main()
