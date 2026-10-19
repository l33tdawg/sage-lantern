import contextlib
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sage_lantern import suspend_session
from sage_lantern.suspend_kiosk import Process
from sage_lantern.suspend_session import (
    CHROMIUM, ConsoleFence, GnomeShield, MutterIdle, PYTHON_EXECUTABLE, SUPERVISOR,
    WAYLAND_PROFILE, WaylandBinding, X11_PROFILE, installed_profile, load_wayland_binding,
)


PROFILE_ARGUMENT = "--user-data-dir=/home/sage/.config/sage-lantern/unit-c/chromium"


def binding():
    return WaylandBinding("unit-c", "sage", 1000, Path(PROFILE_ARGUMENT.split("=", 1)[1]))


# A session of the operator's shape: on the only seat, active, local, Wayland,
# owned by uid 1000, with a leader this host can actually time.
LEADER_PID = 4242
LEADER_STARTTIME = "987654"
OPERATOR_SESSION = {
    "Id": ["session-A"],
    "Active": ["yes"],
    "Seat": ["seat0"],
    "Class": ["user"],
    "Type": ["wayland"],
    "Remote": ["no"],
    "User": ["1000"],
    "Leader": [str(LEADER_PID)],
    "LockedHint": ["no"],
}


def leader_stat(pid=LEADER_PID, starttime=LEADER_STARTTIME):
    """A `stat` body in the kernel's shape, with `starttime` in field 22.

    Everything after the command name is positional. Splitting off `(comm)`
    leaves state at index 0, so the process start time the module reads is index
    19 of that tail: one state field plus eighteen placeholders puts it exactly
    there.
    """
    return "%d (sage) S %s %s\n" % (pid, " ".join(["0"] * 18), starttime)


def fake_proc(directory, pid=LEADER_PID, body=None):
    """A procfs holding one leader's `stat`, so lifetime is really parsed."""
    root = Path(directory) / str(pid)
    root.mkdir(parents=True, exist_ok=True)
    (root / "stat").write_text(leader_stat(pid) if body is None else body)
    return Path(directory)


@contextlib.contextmanager
def fake_procfs(pid=LEADER_PID, body=None):
    """Point the module's procfs at a temporary tree for the duration."""
    with tempfile.TemporaryDirectory() as directory:
        with patch.object(suspend_session, "PROC", fake_proc(directory, pid, body)):
            yield


def observed_values(value):
    """A property's values as the list the fixture renders, never a bare string."""
    return list(value) if isinstance(value, (list, tuple)) else [value]


class FakeLogind:
    """A logind that answers only the seat and session it was built with.

    The lock-hint controls used to hand the selector one canned dump for every
    command, so they could not tell a query about `self` from a query about the
    operator's session and any selector at all passed them. This seam answers the
    seat and session questions the selector really asks, records every command,
    and refuses to answer `self` or `auto` outright - so a selector that leans on
    logind to name its own session fails here instead of on the unit.
    """

    def __init__(self, session=None, seats=("seat0",), graphical="yes",
                 active_session="session-A", fail=None, seat_facts=None):
        self.session = dict(OPERATOR_SESSION if session is None else session)
        self.seats = tuple(seats)
        self.graphical = observed_values(graphical)
        self.active_session = observed_values(active_session)
        self.fail = fail
        # Per-seat facts for a host with more than one seat. A seat not named
        # here falls back to the single-seat graphical/active_session pair, so
        # every single-seat control is unaffected.
        self.seat_facts = dict(seat_facts or {})
        self.commands = []

    @staticmethod
    def lines(values):
        return "\n".join("%s=%s" % (name, value)
                         for name, observed in values.items() for value in observed)

    def show_session_ids(self):
        return [command[2] for command in self.commands if command[1] == "show-session"]

    def __call__(self, command, timeout=5):
        self.commands.append(list(command))
        if self.fail is not None and command[1] == self.fail:
            raise OSError("logind unavailable")
        if command[1:] == ["list-seats", "--no-legend"]:
            return "\n".join(self.seats)
        if command[1] == "show-seat":
            if command[2] in self.seat_facts:
                return self.lines(self.seat_facts[command[2]])
            return self.lines({"CanGraphical": self.graphical,
                               "ActiveSession": self.active_session})
        if command[1] == "show-session":
            if command[2] in ("self", "auto"):
                raise AssertionError(("the selector asked logind to name a session", command))
            return self.lines(self.session)
        raise AssertionError(("unmodeled command", command))


def session_from(output):
    """A complete session dump whose gaps are the ones the control asked for.

    The fields no lock-hint control varies are supplied so the selector can reach
    its decision, and whatever the output names replaces them. `Id` and
    `LockedHint` are never supplied: absence of the identity is a case the
    contract answers with a refusal, and absence of the hint is a case it answers
    with unknown, so inventing either would erase the control rather than set it
    up.
    """
    values = {name: list(observed) for name, observed in OPERATOR_SESSION.items()
              if name not in ("Id", "LockedHint")}
    replaced = set()
    for part in output.split():
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        if name not in replaced:
            values[name] = []
            replaced.add(name)
        values[name].append(value)
    return values


class FakeProcesses:
    """Process-table simulator for the console fence; never signals a real pid."""

    def __init__(self):
        # Chromium rewrites its command line into one space-joined argv[0] entry
        # (read on C, 30 Sep 2026); the fake carries that shape.
        browser = " ".join((CHROMIUM, "--kiosk", PROFILE_ARGUMENT, "http://127.0.0.1:3100/device"))
        zygote = " ".join((CHROMIUM, "--type=zygote", PROFILE_ARGUMENT))
        gpu = " ".join((CHROMIUM, "--type=gpu-process", PROFILE_ARGUMENT))
        self.table = {
            2225: Process(2225, 1537, 900, 1000, "/usr/bin/python3.14", ("python3", SUPERVISOR), "S", 1753),
            2682: Process(2682, 2225, 901, 1000, CHROMIUM, (browser,), "S", 2682),
            2933: Process(2933, 2682, 902, 1000, CHROMIUM, (zygote,), "S", 2682),
            3466: Process(3466, 2682, 903, 1000, CHROMIUM, (gpu,), "S", 2682),
            1500: Process(1500, 1537, 800, 1000, "/usr/bin/gnome-shell", ("gnome-shell",), "S", 1753),
            3100: Process(3100, 1, 700, 0, "/usr/lib/systemd/systemd", ("systemd",), "S", 1),
        }
        self.fence = "present"
        self.calls = []
        self.closed = []
        self.ignore_stop = False
        self.host_executables = None

    def validate_host(self, executables=None):
        self.host_executables = executables

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

    def close(self, handle):
        self.closed.append(handle)

    def actions(self, action):
        return [handle[0] for handle, recorded in self.calls if recorded == action]


class InstalledProfileTests(unittest.TestCase):
    def test_absent_record_keeps_the_reviewed_x11_profile(self):
        with patch.object(suspend_session.os.path, "lexists", return_value=False):
            self.assertEqual(installed_profile(), X11_PROFILE)

    def test_exact_records_select_their_profile(self):
        for value, expected in (("x11-kiosk\n", X11_PROFILE), ("wayland-mutter\n", WAYLAND_PROFILE)):
            with self.subTest(value=value), \
                    patch.object(suspend_session.os.path, "lexists", return_value=True), \
                    patch.object(suspend_session, "_read_binding_file", return_value=value):
                self.assertEqual(installed_profile(), expected)

    def test_every_other_record_refuses_by_gate(self):
        for value in ("wayland-mutter", "x11-kiosk\r\n", "wayland", "", "wayland-mutter\n\n", "gdm\n"):
            with self.subTest(value=value), \
                    patch.object(suspend_session.os.path, "lexists", return_value=True), \
                    patch.object(suspend_session, "_read_binding_file", return_value=value):
                with self.assertRaisesRegex(ValueError, "session_profile_invalid"):
                    installed_profile()
        with patch.object(suspend_session.os.path, "lexists", return_value=True), \
                patch.object(suspend_session, "_read_binding_file", side_effect=RuntimeError("untrusted")):
            with self.assertRaisesRegex(ValueError, "session_profile_untrusted"):
                installed_profile()


class WaylandBindingTests(unittest.TestCase):
    def test_cf_unit_binds_the_installed_console_profile(self):
        with patch.object(suspend_session, "load_kiosk_binding",
                          return_value=SimpleNamespace(unit_id="unit-c", account="sage", uid=1000,
                                                       profile=PROFILE_ARGUMENT)), \
                patch.object(suspend_session.pwd, "getpwnam",
                             return_value=SimpleNamespace(pw_uid=1000, pw_dir="/home/sage")):
            value = load_wayland_binding()
        self.assertEqual(value.unit_id, "unit-c")
        self.assertEqual(value.account, "sage")
        self.assertEqual(value.uid, 1000)
        self.assertEqual(value.console_profile, Path(PROFILE_ARGUMENT.split("=", 1)[1]))
        self.assertEqual(value.profile_argument, PROFILE_ARGUMENT)
        self.assertEqual(value.runtime_directory, Path("/run/user/1000"))
        self.assertEqual(value.bus_address, "unix:path=/run/user/1000/bus")

    def test_ab_unit_and_broken_records_refuse_by_gate(self):
        base = dict(unit_id="unit-c", account="sage", uid=1000, profile=PROFILE_ARGUMENT)
        for changes, gate in ((dict(unit_id="unit-a"), "wayland_session_requires_cf_unit"),
                              (dict(profile="--user-data-dir=/home/sage/.config/other"), "console_profile_mismatch")):
            with self.subTest(gate=gate), \
                    patch.object(suspend_session, "load_kiosk_binding",
                                 return_value=SimpleNamespace(**(base | changes))), \
                    patch.object(suspend_session.pwd, "getpwnam",
                                 return_value=SimpleNamespace(pw_uid=1000, pw_dir="/home/sage")):
                with self.assertRaisesRegex(ValueError, gate):
                    load_wayland_binding()
        with patch.object(suspend_session, "load_kiosk_binding",
                          return_value=SimpleNamespace(**base)), \
                patch.object(suspend_session.pwd, "getpwnam",
                             return_value=SimpleNamespace(pw_uid=1001, pw_dir="/home/sage")):
            with self.assertRaisesRegex(ValueError, "operator_account_mismatch"):
                load_wayland_binding()

    def test_binding_rejects_units_and_paths_that_are_not_the_cf_shape(self):
        for value in (("unit-a", "sage", 1000, Path("/home/sage/.config/sage-lantern/unit-c/chromium")),
                      ("unit-c", "root", 0, Path("/home/root/.config/sage-lantern/unit-c/chromium")),
                      ("unit-c", "sage", 1000, Path("/home/sage/.config/sage-lantern/unit-d/chromium"))):
            with self.subTest(value=value), self.assertRaises(ValueError):
                WaylandBinding(*value)


class MutterIdleTests(unittest.TestCase):
    def test_idle_milliseconds_become_seconds_over_the_operator_bus(self):
        calls = []

        def run(argv, timeout=5):
            calls.append((argv, timeout))
            return "t 26809003"

        idle = MutterIdle(binding(), run=run)
        self.assertAlmostEqual(idle.seconds(), 26809.003)
        argv, timeout = calls[0]
        self.assertEqual(argv[:4], ["/usr/sbin/runuser", "-u", "sage", "--"])
        self.assertIn("XDG_RUNTIME_DIR=/run/user/1000", argv)
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus", argv)
        self.assertEqual(argv[-7:], ["/usr/bin/busctl", "--user", "call", "org.gnome.Mutter.IdleMonitor",
                                     "/org/gnome/Mutter/IdleMonitor/Core", "org.gnome.Mutter.IdleMonitor",
                                     "GetIdletime"])
        self.assertEqual(timeout, 5)

    def test_unknown_or_out_of_range_observations_refuse(self):
        for value, gate in (("t 1", None), ("u 1000", "idle_observation_unavailable"),
                            ("b true", "idle_observation_unavailable"),
                            ("t 4294967296", "idle_observation_out_of_range")):
            with self.subTest(value=value):
                idle = MutterIdle(binding(), run=lambda argv, timeout=5, value=value: value)
                if gate is None:
                    self.assertEqual(idle.seconds(), 0.001)
                else:
                    with self.assertRaisesRegex(ValueError, gate):
                        idle.seconds()


class GnomeShieldTests(unittest.TestCase):
    def shield(self, *, locked="false", active=False, settle=True, failure=None,
               session="session-A", session_locked=False, session_readable=True):
        state = {"active": active, "locked": locked}
        calls = []

        def run(argv, timeout=5):
            calls.append(argv)
            if failure is not None:
                raise failure
            if argv[-3:] == ["get", "org.gnome.desktop.screensaver", "lock-enabled"]:
                return state["locked"]
            if argv[-1] == "GetActive":
                return "b true" if state["active"] else "b false"
            if argv[-2:] == ["b", "true"]:
                state["active"] = settle
                return ""
            if argv[-2:] == ["b", "false"]:
                state["active"] = False if settle else True
                return ""
            raise AssertionError(argv)

        def reader():
            calls.append(["loginctl", "show-session", "self"])
            if not session_readable:
                return None
            return session, session_locked

        # The session identity is read through a mutable box so a case can model
        # a replacement between two calls on one shield instance, the way the
        # runner's cached instance sees a later cycle.
        observed = {"session": session, "locked": session_locked, "readable": session_readable}

        def live_reader():
            calls.append(["loginctl", "show-session", "self"])
            if not observed["readable"]:
                return None
            return observed["session"], observed["locked"]

        return GnomeShield(binding(), run=run, reader=live_reader), calls, state, observed

    def test_off_confirms_a_blank_that_the_shell_reports(self):
        shield, calls, state, _ = self.shield()
        self.assertEqual(shield.off(), "confirmed")
        self.assertIs(state["active"], True)
        self.assertIn(["b", "true"], [call[-2:] for call in calls])

    def test_off_never_activates_a_shield_that_would_lock_the_kiosk(self):
        shield, calls, _, _ = self.shield(locked="true")
        self.assertEqual(shield.off(), "skipped_lock_enabled")
        self.assertEqual([call[-2:] for call in calls],
                         [["org.gnome.desktop.screensaver", "lock-enabled"]])

    def test_off_reports_rather_than_raises_when_the_shell_ignores_it(self):
        """Read on C, 30 Sep 2026: SetActive succeeds and GetActive stays false."""
        shield, _, _, _ = self.shield(settle=False)
        self.assertEqual(shield.off(), "unconfirmed")
        broken, _, _, _ = self.shield(failure=RuntimeError("no session bus"))
        self.assertEqual(broken.off(), "unavailable_RuntimeError")

    def test_on_restores_a_blank_this_cycle_created(self):
        shield, calls, state, _ = self.shield()
        self.assertEqual(shield.off(), "confirmed")
        self.assertEqual(shield.on(), "restored")
        self.assertIs(state["active"], False)
        idle_shield, _, _, _ = self.shield(active=False)
        self.assertEqual(idle_shield.on(), "not_needed")

    def test_on_preserves_a_shield_this_cycle_did_not_create(self):
        """With locking enabled an active shield is a login, not this blank."""
        shield, calls, state, _ = self.shield(locked="true", active=True)
        self.assertEqual(shield.off(), "skipped_lock_enabled")
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_a_blank_that_was_never_confirmed_is_not_ours_to_remove(self):
        """off() could not prove a blank, so the shield that greets the wake
        is an authentication lock: it is preserved, not dismissed."""
        shield, calls, state, _ = self.shield(settle=False)
        ticks = [0.0]

        def clock():
            ticks[0] += 0.6
            return ticks[0]

        with patch.object(suspend_session, "time",
                          SimpleNamespace(monotonic=clock, sleep=lambda seconds: None)):
            self.assertEqual(shield.off(), "unconfirmed")
        state["active"] = True
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_a_preexisting_blank_is_not_claimed_by_confirming_it(self):
        """The shell confirms an already-active shield; that is not ownership.

        Reading the shield before raising it is what separates "this cycle put a
        blank up" from "a blank was already there and the shell still says yes".
        """
        shield, calls, state, _ = self.shield(active=True)
        self.assertEqual(shield.off(), "preserved_preexisting")
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "true"]], [])
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_an_unverifiable_session_identity_is_not_an_identity(self):
        """A blank is only removed while the session that raised it is proven.

        `loginctl show-session self` is resolved through the session bus the
        command is pinned to. When that cannot be read, or reports a different
        session, the shield stays up: an unprovable claim is not a licence to
        dismiss whatever is on screen.
        """
        shield, calls, state, observed = self.shield(session_readable=False)
        self.assertEqual(shield.off(), "session_unverified")
        state["active"] = True
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

        shield, calls, state, observed = self.shield()
        self.assertEqual(shield.off(), "confirmed")
        observed["session"] = "session-B"  # the same instance after a replacement
        self.assertEqual(shield.on(), "preserved_session_replaced")
        self.assertIs(state["active"], True)
        self.assertFalse(shield.owned)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_a_blank_that_became_an_authentication_lock_is_preserved(self):
        """Locking the console after this cycle's blank is somebody's decision."""
        shield, calls, state, observed = self.shield()
        self.assertEqual(shield.off(), "confirmed")
        observed["locked"] = True
        self.assertEqual(shield.on(), "preserved_authentication")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_offer_refuses_a_shield_that_is_already_an_authentication_lock(self):
        """With locking disabled a live authentication lock is still not ours."""
        shield, calls, state, _ = self.shield(active=True, session_locked=True)
        self.assertEqual(shield.off(), "preserved_authentication")
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "true"]], [])

    def test_a_disappeared_blank_consumes_ownership(self):
        """A blank that vanished is still this cycle's; it must not be spent later.

        The runner caches one shield instance across cycles, so a claim left
        standing after the blank disappeared would be spent on the next cycle's
        independent lock.
        """
        shield, calls, state, _ = self.shield()
        self.assertEqual(shield.off(), "confirmed")
        state["active"] = False
        self.assertEqual(shield.on(), "not_needed")
        self.assertFalse(shield.owned)
        state["active"] = True  # a later authentication lock on the same instance
        self.assertEqual(shield.on(), "preserved_unowned")
        self.assertIs(state["active"], True)
        self.assertEqual([call for call in calls if call[-2:] == ["b", "false"]], [])

    def test_on_raises_when_an_owned_blank_cannot_be_taken_down(self):
        """A blank this cycle confirmed must go; an unverifiable removal fails closed."""
        shield, calls, state, _ = self.shield()
        self.assertEqual(shield.off(), "confirmed")

        def refusing(argv, timeout=5):
            calls.append(argv)
            if argv[-3:] == ["get", "org.gnome.desktop.screensaver", "lock-enabled"]:
                return "false"
            if argv[-1] == "GetActive":
                return "b true"
            if argv[-2:] in (["b", "true"], ["b", "false"]):
                return ""
            raise AssertionError(argv)

        ticks = [0.0]

        def clock():
            ticks[0] += 0.6
            return ticks[0]

        shield.run = refusing
        with patch.object(suspend_session, "time",
                          SimpleNamespace(monotonic=clock, sleep=lambda seconds: None)):
            with self.assertRaisesRegex(ValueError, "shield_unblank_unconfirmed"):
                shield.on()

    def test_unparseable_shield_state_refuses(self):
        shield, _, _, _ = self.shield()
        with patch.object(shield, "run", return_value="b maybe"):
            with self.assertRaisesRegex(ValueError, "shield_state_unavailable"):
                shield.active()
        with patch.object(shield, "run", return_value="yes please"):
            with self.assertRaisesRegex(ValueError, "shield_setting_unavailable"):
                shield.locks_kiosk()


class ConsoleFenceTests(unittest.TestCase):
    def fence(self, backend=None, locked=True):
        backend = backend if backend is not None else FakeProcesses()
        fence = ConsoleFence(binding=binding(), auth_locked=lambda: locked, backend=backend,
                             timeout_seconds=0.05)
        return fence, backend

    def scoped(self, fence):
        return patch.object(ConsoleFence, "_scope", return_value=True)

    def test_preflight_is_signal_free_on_a_well_formed_console(self):
        fence, backend = self.fence()
        with self.scoped(fence):
            self.assertIs(fence.preflight(), True)
        self.assertEqual(backend.calls, [])
        self.assertEqual(backend.host_executables, (CHROMIUM,))

    def test_split_command_lines_are_accepted_and_one_blob_is_split(self):
        backend = FakeProcesses()
        backend.table[2682] = replace(backend.table[2682], argv=(CHROMIUM, "--kiosk", PROFILE_ARGUMENT))
        backend.table[2933] = replace(backend.table[2933], argv=(CHROMIUM, "--type=zygote", PROFILE_ARGUMENT))
        fence, _ = self.fence(backend)
        with self.scoped(fence):
            self.assertIs(fence.preflight(), True)
            self.assertIs(fence.quiesce(), True)
        self.assertEqual(backend.table[2682].state, "T")

    def test_inventory_refuses_ambiguous_or_ungrouped_or_unscoped_browsers(self):
        cases = []
        missing = FakeProcesses()
        del missing.table[2682]
        cases.append((missing, "console_browser_ambiguous", True))
        extra = FakeProcesses()
        extra.table[2683] = replace(extra.table[2682], pid=2683, pgid=2683)
        cases.append((extra, "console_browser_ambiguous", True))
        ungrouped = FakeProcesses()
        ungrouped.table[2682] = replace(ungrouped.table[2682], pgid=1753)
        cases.append((ungrouped, "console_browser_ungrouped", True))
        unscoped = FakeProcesses()
        cases.append((unscoped, "console_supervisor_unscoped", False))
        for backend, gate, scoped in cases:
            with self.subTest(gate=gate), patch.object(ConsoleFence, "_scope", return_value=scoped):
                fence, _ = self.fence(backend)
                with self.assertRaisesRegex(ValueError, gate):
                    fence._inventory(backend.snapshot())

    def test_foreign_group_process_or_profile_owner_refuses(self):
        foreign = FakeProcesses()
        foreign.table[4000] = Process(4000, 1537, 500, 1000, CHROMIUM,
                                      (CHROMIUM, PROFILE_ARGUMENT), "S", 2682)
        profile_owner = FakeProcesses()
        profile_owner.table[4001] = Process(4001, 1537, 501, 1000, CHROMIUM,
                                            (CHROMIUM, PROFILE_ARGUMENT), "S", 1753)
        for backend, gate in ((foreign, "console_group_process_outside_fence"),
                              (profile_owner, "console_profile_process_outside_fence")):
            with self.subTest(gate=gate), self.scoped(None):
                fence, _ = self.fence(backend)
                with self.assertRaisesRegex(ValueError, gate):
                    fence._inventory(backend.snapshot())

    def test_quiesce_freezes_the_whole_group_and_resume_releases_it(self):
        fence, backend = self.fence()
        with self.scoped(fence):
            self.assertIs(fence.preflight(), True)
            self.assertIs(fence.quiesce(), True)
            self.assertEqual(backend.actions(signal.SIGSTOP), [2682, 2933, 3466])
            self.assertEqual({pid: backend.table[pid].state for pid in (2682, 2933, 3466)},
                             {2682: "T", 2933: "T", 3466: "T"})
            self.assertEqual(backend.table[2225].state, "S")
            self.assertEqual(backend.table[1500].state, "S")
            self.assertIs(fence.resume(), False)
            backend.fence = "absent"
            self.assertIs(fence.resume(), True)
            self.assertEqual(backend.actions(signal.SIGCONT), [2933, 3466, 2682])
            self.assertEqual({pid: backend.table[pid].state for pid in (2682, 2933, 3466)},
                             {2682: "S", 2933: "S", 3466: "S"})

    def test_quiesce_requires_the_controllers_fence(self):
        fence, backend = self.fence()
        backend.fence = "absent"
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), False)
        self.assertEqual(backend.calls, [])

    def test_quiesce_is_idempotent_for_the_same_fence_across_one_cycle(self):
        """The reviewed transition asks for the same fence twice per cycle."""
        fence, backend = self.fence()
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), True)
            self.assertIs(fence.quiesce(), True)
            self.assertEqual(fence.refusal, "still_cleared")
            self.assertEqual(backend.actions(signal.SIGSTOP), [2682, 2933, 3466])
            fresh = ConsoleFence(binding=binding(), auth_locked=lambda: True, backend=backend,
                                 timeout_seconds=0.05)
            self.assertIs(fresh.quiesce(), False)
            self.assertEqual(fresh.refusal, "already_frozen")

    def test_quiesce_refuses_when_members_never_stop(self):
        backend = FakeProcesses()
        backend.ignore_stop = True
        fence, _ = self.fence(backend)
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), False)

    def test_resume_refuses_when_the_supervisor_instance_changed(self):
        fence, backend = self.fence()
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), True)
            backend.fence = "absent"
            backend.table[2225] = replace(backend.table[2225], start_ticks=99999)
            self.assertIs(fence.resume(), False)
            self.assertNotIn(signal.SIGCONT, backend.actions(signal.SIGCONT))

    def test_resume_requires_locked_authentication(self):
        fence, backend = self.fence(locked=False)
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), True)
            backend.fence = "absent"
            self.assertIs(fence.resume(), False)

    def test_close_releases_descriptors_without_resuming(self):
        fence, backend = self.fence()
        with self.scoped(fence):
            self.assertIs(fence.quiesce(), True)
            fence.close()
        self.assertEqual(backend.actions(signal.SIGCONT), [])
        self.assertEqual(len(backend.closed), 4)
        self.assertIs(fence.resume(), False)


class LockedHintObservationTests(unittest.TestCase):
    """D-022 F07: only an unambiguous exact yes/no is a known lock state.

    An independent pass found `hint == "yes"` reported every other present value
    as False, so `LockedHint=garbage` read as unlocked and a contradictory
    duplicate resolved last-wins. The documented contract calls both unknown, and
    unknown must not become permission for a shield effect.

    These now run through the default-reader selector rather than a canned dump,
    so the hint is only ever read for a session the selector established first.
    """

    def read(self, output):
        port = FakeLogind(session_from(output))
        with fake_procfs(), \
                patch.object(suspend_session, "installed_profile", lambda: WAYLAND_PROFILE):
            return suspend_session.observed_session(binding(), port)

    def test_exact_no_is_known_unlocked(self):
        self.assertEqual(self.read("Id=session-A\nLockedHint=no\n"), ("session-A", False))

    def test_exact_yes_is_known_locked(self):
        self.assertEqual(self.read("Id=session-A\nLockedHint=yes\n"), ("session-A", True))

    def test_absent_hint_is_unknown(self):
        self.assertEqual(self.read("Id=session-A\n"), ("session-A", None))

    def test_unparseable_hints_never_read_as_unlocked(self):
        # The regression this repairs: every one of these used to come back
        # False, which is the value that permits activating a blank.
        for value in ("garbage", "", "YES", "true", "0", "y", "no-thing"):
            with self.subTest(value=value):
                self.assertEqual(self.read("Id=session-A\nLockedHint=%s\n" % value),
                                 ("session-A", None))

    def test_conflicting_duplicates_are_unknown_in_either_order(self):
        for output in ("Id=session-A\nLockedHint=yes\nLockedHint=no\n",
                       "Id=session-A\nLockedHint=no\nLockedHint=yes\n"):
            with self.subTest(output=output):
                self.assertEqual(self.read(output), ("session-A", None))

    def test_agreeing_duplicates_are_still_unambiguous(self):
        self.assertEqual(self.read("Id=session-A\nLockedHint=yes\nLockedHint=yes\n"),
                         ("session-A", True))
        self.assertEqual(self.read("Id=session-A\nLockedHint=no\nLockedHint=no\n"),
                         ("session-A", False))

    def test_missing_session_id_is_not_an_observation(self):
        self.assertIsNone(self.read("LockedHint=no\n"))

    def test_an_unreadable_query_is_not_an_observation(self):
        def run(command, timeout=5):
            raise OSError("no session bus")
        self.assertIsNone(suspend_session.observed_session(binding(), run))

    def test_the_helper_accepts_only_exact_yes_or_no(self):
        for values, expected in ([["yes"], True], [["no"], False], [[], None],
                                 [["yes", "no"], None], [["yes", "yes"], True],
                                 [["garbage"], None], [[""], None], [["no", ""], None]):
            with self.subTest(values=values):
                self.assertIs(suspend_session.lock_hint(values), expected)

    def test_conflicting_identities_are_not_an_observation(self):
        # Last-wins used to make the second Id the verified session, so a shield
        # could act against an identity the output never agreed on.
        for output in ("Id=session-B\nId=session-A\nLockedHint=no\n",
                       "Id=session-A\nId=session-B\nLockedHint=no\n"):
            with self.subTest(output=output):
                self.assertIsNone(self.read(output))

    def test_agreeing_duplicate_identities_still_resolve(self):
        self.assertEqual(self.read("Id=session-A\nId=session-A\nLockedHint=no\n"),
                         ("session-A", False))

    def test_a_malformed_identity_alongside_a_valid_one_is_not_an_observation(self):
        for output in ("Id=session-A\nId=\n", "Id=session-A\nId=garbage\n"):
            with self.subTest(output=output):
                self.assertIsNone(self.read(output))

    def test_the_identity_helper_needs_exactly_one_value(self):
        for values, expected in ([["session-A"], "session-A"], [[], None],
                                 [["session-A", "session-A"], "session-A"],
                                 [["session-A", "session-B"], None],
                                 [["session-A", ""], None]):
            with self.subTest(values=values):
                self.assertIs(suspend_session.sole_observation(values), expected)


class DefaultReaderSelectorTests(unittest.TestCase):
    """The default reader is established, never assumed.

    Rook's static review of the execution binding (rook.d022, 11 Oct 2026) found
    that logind resolves a caller from its own bus credentials and that `loginctl`
    selects the system transport, so `show-session self` under a pinned
    session-bus address never established that the caller was inside the
    operator's graphical session: an ssh or root-runner session answered just as
    readily. Neither `self` nor `auto` proves the contract.

    The four facts this selector establishes together are the operator UID, the
    unit's active graphical seat, the session and its lifetime, and the rule that
    an unestablished or unreadable fact is never treated as satisfied.
    `FakeLogind` refuses to answer `self` or `auto` at all, so every control here
    also proves the selector names its session rather than asking logind to.
    """

    def select(self, port, profile=WAYLAND_PROFILE):
        with fake_procfs(), \
                patch.object(suspend_session, "installed_profile", lambda: profile):
            return suspend_session.observed_session(binding(), port)

    def variant(self, **overrides):
        session = {name: list(observed) for name, observed in OPERATOR_SESSION.items()}
        session.update(overrides)
        return FakeLogind(session)

    # --- the positive the rest are measured against -------------------------

    def test_the_operators_active_wayland_session_is_selected(self):
        port = self.variant()
        self.assertEqual(self.select(port), ("session-A", False))
        self.assertEqual(port.show_session_ids(), ["session-A"])

    def test_the_selector_never_asks_logind_to_name_its_own_session(self):
        # FakeLogind raises on `self`/`auto`, so a selector that asked would
        # never reach these assertions: this is the regression control for the
        # execution binding Rook left unqualified.
        port = self.variant()
        self.assertEqual(self.select(port), ("session-A", False))
        asked = port.show_session_ids()
        self.assertEqual(asked, ["session-A"])
        self.assertNotIn("self", asked)
        self.assertNotIn("auto", asked)

    # --- the intended operator UID -----------------------------------------

    def test_another_users_session_is_not_the_operators(self):
        for user in ("1001", "0"):
            with self.subTest(user=user):
                self.assertIsNone(self.select(self.variant(User=[user])))

    def test_an_absent_or_empty_operator_uid_is_not_established(self):
        for user in ([], [""]):
            with self.subTest(user=user):
                self.assertIsNone(self.select(self.variant(User=user)))

    # --- the local active graphical seat and its type ----------------------

    def test_a_non_graphical_seat_supplies_no_reader(self):
        self.assertIsNone(self.select(FakeLogind(graphical="no")))

    def two_seats(self, second):
        """seat0 normal, seat1 carrying the fact the case is about."""
        return FakeLogind(seats=("seat0", "seat1"), seat_facts={"seat1": second})

    def test_an_unreadable_graphical_fact_refuses_instead_of_dropping_the_seat(self):
        # "not reported as graphical" and "not a fact" are different answers. A
        # seat whose CanGraphical is absent, malformed, or reported twice with
        # contradictory values cannot be called non-graphical, and dropping it
        # would let the remaining seat decide on evidence this host never made.
        for second in ({}, {"CanGraphical": ["garbage"]}, {"CanGraphical": ["yes", "no"]}):
            with self.subTest(seat1=second):
                self.assertIsNone(self.select(self.two_seats(second)))

    def test_a_seat_that_reports_it_is_not_graphical_is_skipped(self):
        # The other half of the same rule: a seat that says `no` has answered,
        # and skipping a known non-graphical seat is correct rather than unknown.
        port = self.two_seats({"CanGraphical": ["no"], "ActiveSession": ["session-B"]})
        self.assertEqual(self.select(port), ("session-A", False))

    def test_a_seat_without_an_established_active_session_is_not_selected(self):
        for active in ("", "-", ["session-A", "session-B"]):
            with self.subTest(active=active):
                self.assertIsNone(self.select(FakeLogind(active_session=active)))

    def test_two_graphical_seats_are_ambiguous_and_refused(self):
        self.assertIsNone(self.select(FakeLogind(seats=("seat0", "seat1"))))

    def test_no_seat_is_not_a_default_reader(self):
        self.assertIsNone(self.select(FakeLogind(seats=())))

    def test_the_installed_profile_decides_the_session_type(self):
        self.assertEqual(self.select(self.variant(Type=["wayland"]), WAYLAND_PROFILE),
                         ("session-A", False))
        self.assertEqual(self.select(self.variant(Type=["x11"]), X11_PROFILE),
                         ("session-A", False))
        for profile, kind in ((WAYLAND_PROFILE, "x11"), (X11_PROFILE, "wayland")):
            with self.subTest(profile=profile, kind=kind):
                self.assertIsNone(self.select(self.variant(Type=[kind]), profile))

    def test_a_session_on_another_seat_than_the_one_selected_is_refused(self):
        self.assertIsNone(self.select(self.variant(Seat=["seat9"])))

    def test_a_remote_session_is_not_the_console(self):
        self.assertIsNone(self.select(self.variant(Remote=["yes"])))

    def test_a_session_that_is_not_active_is_not_selected(self):
        self.assertIsNone(self.select(self.variant(Active=["no"])))

    def test_a_session_that_is_not_a_user_session_is_not_selected(self):
        for kind in ("greeter", "lock-screen", "manager"):
            with self.subTest(kind=kind):
                self.assertIsNone(self.select(self.variant(Class=[kind])))

    # --- one unambiguous session and its lifetime --------------------------

    def test_an_answer_about_another_session_does_not_stand_in(self):
        self.assertIsNone(self.select(self.variant(Id=["session-B"])))

    def test_an_unverifiable_leader_lifetime_refuses_the_session(self):
        for leader in ([], [""], ["0"], ["not-a-pid"], ["999999"]):
            with self.subTest(leader=leader):
                self.assertIsNone(self.select(self.variant(Leader=leader)))

    def test_the_lifetime_is_the_process_start_time_in_field_22(self):
        with fake_procfs(body=leader_stat(starttime="123456789")):
            self.assertEqual(suspend_session.leader_lifetime(str(LEADER_PID)),
                             "123456789")

    def test_a_truncated_or_zero_stat_is_not_a_lifetime(self):
        for body in (leader_stat()[:20], leader_stat(starttime="0"), "4242 (sage)\n"):
            with self.subTest(body=body):
                with fake_procfs(body=body):
                    self.assertIsNone(suspend_session.leader_lifetime(str(LEADER_PID)))

    def test_leader_lifetime_rejects_what_a_value_could_be_confused_with(self):
        # An empty string compares equal to another empty string, which is how
        # two different processes would otherwise present one identity.
        for leader in (None, "", "0", "-1", "not-a-pid", "4242x", 4242):
            with self.subTest(leader=leader):
                self.assertIsNone(suspend_session.leader_lifetime(leader))

    # --- unknown preservation ----------------------------------------------

    def test_every_required_field_is_required(self):
        for name in suspend_session.SESSION_REQUIRED:
            for missing in ([], [""]):
                with self.subTest(field=name, missing=missing):
                    self.assertIsNone(self.select(self.variant(**{name: missing})))

    def test_an_agreeing_duplicate_field_still_resolves(self):
        for name in suspend_session.SESSION_REQUIRED:
            with self.subTest(field=name):
                values = list(OPERATOR_SESSION[name])
                self.assertEqual(self.select(self.variant(**{name: values + values})),
                                 ("session-A", False))

    def test_a_contradictory_duplicate_field_refuses_the_session(self):
        # The correct value is second, so a last-wins reader would accept this
        # and only an unambiguous-value reader refuses it.
        for name in suspend_session.SESSION_REQUIRED:
            with self.subTest(field=name):
                correct = OPERATOR_SESSION[name][0]
                self.assertIsNone(self.select(self.variant(**{name: ["other", correct]})))

    def test_an_unreadable_query_is_not_an_observation(self):
        for fail in ("list-seats", "show-seat", "show-session"):
            with self.subTest(fail=fail):
                self.assertIsNone(self.select(FakeLogind(fail=fail)))

    def test_an_unusable_installed_profile_is_not_a_selector(self):
        self.assertIsNone(self.select(self.variant(), profile="not-a-profile"))

    def test_a_non_operator_binding_is_not_selected(self):
        with patch.object(suspend_session, "installed_profile", lambda: WAYLAND_PROFILE):
            self.assertIsNone(suspend_session.observed_session(object(), FakeLogind()))


class ShieldLifetimeOwnershipTests(unittest.TestCase):
    """A blank is this cycle's only while the session is the same session.

    Rook's selector review (rook-ember-closure-20261011/selector-review-01) found
    the shield comparing only the session Id. An Id is reusable: a replacement
    session - the same leader pid recycled onto a new process, or a new leader
    entirely - can wear it, and an Id-only comparison lets that replacement
    inherit the previous cycle's ownership and have somebody else's blank taken
    down. The reader is asked for the whole lifetime identity instead, and `on()`
    compares all of it.

    These three run the real selector through the shield's default reader, so the
    identity is the one production builds rather than one a case injected. The
    third is the positive the first two are measured against: without it, a shield
    that never lowered anything would satisfy both.
    """

    def opened(self):
        """A confirmed blank over a live procfs, and the pieces a case mutates."""
        directory = tempfile.TemporaryDirectory()
        root = fake_proc(directory.name)
        port = FakeLogind()
        shell = {"active": False, "lowered": False}
        ticks = [0.0]

        def clock():
            ticks[0] += 0.2
            return ticks[0]

        def run(argv, timeout=5):
            if argv[-3:] == ["get", "org.gnome.desktop.screensaver", "lock-enabled"]:
                return "false"
            if argv[-1] == "GetActive":
                return "b true" if shell["active"] else "b false"
            if argv[-2:] == ["b", "true"]:
                shell["active"] = True
                return ""
            if argv[-2:] == ["b", "false"]:
                shell["active"] = False
                shell["lowered"] = True
                return ""
            return port(argv, timeout)

        return SimpleNamespace(
            directory=directory, root=root, port=port, shell=shell,
            display=GnomeShield(binding(), run=run, timeout_seconds=1),
            time=SimpleNamespace(monotonic=clock, sleep=lambda seconds: None))

    @contextlib.contextmanager
    def running(self, cycle):
        with cycle.directory, \
                patch.object(suspend_session, "PROC", cycle.root), \
                patch.object(suspend_session, "installed_profile", lambda: WAYLAND_PROFILE), \
                patch.object(suspend_session, "time", cycle.time):
            yield cycle

    def test_the_same_session_still_lowers_its_own_blank(self):
        cycle = self.opened()
        with self.running(cycle):
            self.assertEqual(cycle.display.off(), "confirmed")
            self.assertTrue(cycle.display.owned)
            self.assertEqual(cycle.display.on(), "restored")
        self.assertIs(cycle.shell["lowered"], True)
        self.assertIs(cycle.shell["active"], False)

    def test_a_recycled_leader_does_not_inherit_the_raised_blank(self):
        cycle = self.opened()
        with self.running(cycle):
            self.assertEqual(cycle.display.off(), "confirmed")
            self.assertTrue(cycle.display.owned)
            # The same leader pid, a different process wearing it.
            (cycle.root / str(LEADER_PID) / "stat").write_text(leader_stat(starttime="987655"))
            cycle.display.on()
        self.assertIs(cycle.shell["lowered"], False)
        self.assertIs(cycle.shell["active"], True)

    def test_a_new_leader_does_not_inherit_the_raised_blank(self):
        cycle = self.opened()
        with self.running(cycle):
            self.assertEqual(cycle.display.off(), "confirmed")
            self.assertTrue(cycle.display.owned)
            cycle.port.session["Leader"] = ["4243"]
            (cycle.root / "4243").mkdir()
            (cycle.root / "4243" / "stat").write_text(
                leader_stat(pid=4243, starttime="987655"))
            cycle.display.on()
        self.assertIs(cycle.shell["lowered"], False)
        self.assertIs(cycle.shell["active"], True)
