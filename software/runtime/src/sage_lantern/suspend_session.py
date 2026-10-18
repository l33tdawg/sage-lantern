"""Wayland/GNOME session profile for the guarded C-F sleep lane.

A/B units run the reviewed X11 profile: `radxa` at uid 1000, a GDM X11 kiosk,
idle seconds read over `DISPLAY=:0`, display power through `xset dpms`, and a
kiosk launcher tree as the fence anchor. C-F units are Wayland by construction:
the operator account comes from the installed unit plan, the console is Chromium
started by `sage-lantern-mode-supervisor` inside a GNOME application scope, and
there is no `radxa` account, no X11 display and no `xset` to drive. Nothing in
the X11 bundle observes idle or fences the console there.

This module supplies exactly the three session observations the root controller
needs on C-F, and nothing else:

* idle seconds from the compositor that owns the session (Mutter IdleMonitor)
  over the operator's session bus;
* a best-effort display blank through the GNOME screen shield. Read on C
  (30 Sep 2026): Mutter refuses a configuration with no logical monitor
  ("Monitors config incomplete"), the DRM connector's `dpms` attribute is
  read-only (0444) even for root, there is no backlight device and Xwayland has
  no DPMS, so there is no verifiable panel-off path on a single-panel C–F unit.
  Blanking is therefore attempted and reported but never a reason to refuse
  sleep: the panel is dark during S3 anyway and the console is fenced. It is
  still never activated when the shield would lock the kiosk;
* a shield is only ever *removed* by the cycle that created it. With
  `lock-enabled=true` (the D-022 policy) this module never activates a shield,
  so an active shield on wake is a login lock or the operator's own blank:
  unblanking it would dismiss somebody else's authentication, so it is
  reported as `preserved_unowned` and left exactly as it is. A shield this
  cycle did confirm is still removed, and a removal that cannot be verified
  fails closed as `shield_unblank_unconfirmed`;
* an exact-process console fence over the mode supervisor and the browser group
  it owns: freeze before the sleep cycle, resume after it.

The controller still owns the durable fence, the journal, the stop set and the
suspend itself. Nothing here stops a service, starts a browser, changes a
display mode or talks to a model. Nothing here is a silent fallback: every
observation either returns verified evidence or raises naming the gate that
refused. `--check` uses the signal-free preflight only.

The fence anchors on the process identity the unit actually runs (29 Sep 2026
read on C): `python3 /usr/local/libexec/sage-lantern-mode-supervisor` as the
MainPID of the GNOME application scope `app-gnome-sage\\x2dlantern\\x2dconsole-<pid>.scope`,
and one Chromium browser, child of that supervisor, in its own process group
with `--user-data-dir=/home/<operator>/.config/sage-lantern/<unit>/chromium`.
Renaming the console autostart entry or the supervisor path is therefore a fence
refusal until this module is updated deliberately.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import subprocess
import time

from .suspend_kiosk import LinuxProcesses, Process, _read_binding_file, load_kiosk_binding


PROFILE_FILE = Path("/etc/sage-lantern/session-profile")
X11_PROFILE = "x11-kiosk"
WAYLAND_PROFILE = "wayland-mutter"
GUARDED_WAYLAND_PROFILE = "wayland-mutter-native-pin"
CF_UNITS = frozenset({"unit-c", "unit-d", "unit-e", "unit-f"})
SUPERVISOR = "/usr/local/libexec/sage-lantern-mode-supervisor"
SUPERVISOR_ARGV = ("python3", SUPERVISOR)
PYTHON_EXECUTABLE = "/usr/bin/python3"
CHROMIUM = "/usr/lib/chromium/chromium"
CHROMIUM_EXECUTABLES = frozenset({"/usr/bin/chromium", CHROMIUM})
CONSOLE_SCOPE = "app.slice/app-gnome-sage\\x2dlantern\\x2dconsole-"
FENCE = Path("/run/sage-lantern-sleep.fenced")
RUNUSER = "/usr/sbin/runuser"
ENV = "/usr/bin/env"
BUSCTL = "/usr/bin/busctl"
IDLE_INTERFACE = ("org.gnome.Mutter.IdleMonitor", "/org/gnome/Mutter/IdleMonitor/Core",
                  "org.gnome.Mutter.IdleMonitor", "GetIdletime")
SHIELD = ("org.gnome.ScreenSaver", "/org/gnome/ScreenSaver", "org.gnome.ScreenSaver")
LOCK_ENABLED = ("org.gnome.desktop.screensaver", "lock-enabled")
GSETTINGS = "/usr/bin/gsettings"
# Selection is read from logind on the *system* transport, as the controller.
# The operator's session bus is not evidence about which session this unit's
# console is in: logind resolves a caller from its own bus credentials, and
# `loginctl` selects the system transport unless told otherwise. Pinning the
# operator's bus address around this query is what made `show-session self`
# look scoped to the operator's console when it never was.
LOGINCTL = "/usr/bin/loginctl"
PROC = Path("/proc")
SEAT_QUERY = ("CanGraphical", "ActiveSession")
# Every field here decides whether the session is the operator's, so one that is
# absent, empty, malformed, or repeated with a different value makes the whole
# observation unknown rather than supplying a default.
SESSION_REQUIRED = ("Id", "Active", "Seat", "Class", "Type", "Remote", "User", "Leader")
SESSION_QUERY = SESSION_REQUIRED + ("LockedHint",)
# The session type each installed profile is the record of. A profile that
# cannot be named here is not one this selector can serve.
SESSION_TYPE_FOR_PROFILE = {X11_PROFILE: "x11", WAYLAND_PROFILE: "wayland",
                            GUARDED_WAYLAND_PROFILE: "wayland"}
MAX_OUTPUT = 32768
MAX_MEMBERS = 128
MAX_IDLE_MILLISECONDS = 4294967295


def _run(argv, timeout=5):
    result = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=True,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"})
    if len(result.stdout) > MAX_OUTPUT:
        raise ValueError("session_output_limit")
    return result.stdout.decode("utf-8", errors="strict").strip()


def installed_profile(path=PROFILE_FILE):
    """The session profile this installation runs; never inferred from the session.

    The A/B bundle predates this record, so an absent file selects the reviewed
    X11 profile: the only profile an installation without the record can have
    been commissioned as. A present record must be a trusted root-owned regular
    file naming exactly one reviewed profile; anything else refuses.
    """
    path = Path(path)
    if not os.path.lexists(path):
        return X11_PROFILE
    try:
        value = _read_binding_file(path)
    except (OSError, RuntimeError) as error:
        raise ValueError("session_profile_untrusted") from error
    if value == X11_PROFILE + "\n":
        return X11_PROFILE
    if value == WAYLAND_PROFILE + "\n":
        return WAYLAND_PROFILE
    if value == GUARDED_WAYLAND_PROFILE + "\n":
        return GUARDED_WAYLAND_PROFILE
    raise ValueError("session_profile_invalid")


@dataclass(frozen=True)
class WaylandBinding:
    unit_id: str
    account: str
    uid: int
    console_profile: Path

    def __post_init__(self):
        if (self.unit_id not in CF_UNITS
                or type(self.account) is not str
                or re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.account) is None
                or type(self.uid) is not int or self.uid <= 0
                or not isinstance(self.console_profile, Path)
                or str(self.console_profile)
                != f"/home/{self.account}/.config/sage-lantern/{self.unit_id}/chromium"):
            raise ValueError("invalid wayland binding")

    @property
    def runtime_directory(self):
        return Path("/run/user") / str(self.uid)

    @property
    def bus_address(self):
        return f"unix:path={self.runtime_directory}/bus"

    @property
    def profile_argument(self):
        return f"--user-data-dir={self.console_profile}"


def load_wayland_binding():
    """Bind the C-F session to the installed unit records, never to a discovery guess."""
    binding = load_kiosk_binding()
    if binding.unit_id not in CF_UNITS:
        raise ValueError("wayland_session_requires_cf_unit")
    if binding.profile != f"--user-data-dir=/home/{binding.account}/.config/sage-lantern/{binding.unit_id}/chromium":
        raise ValueError("console_profile_mismatch")
    owner = pwd.getpwnam(binding.account)
    if owner.pw_uid != binding.uid or owner.pw_dir != f"/home/{binding.account}":
        raise ValueError("operator_account_mismatch")
    return WaylandBinding(binding.unit_id, binding.account, binding.uid,
                          Path(binding.profile.split("=", 1)[1]))


def operator_command(binding, *arguments):
    """One command in the operator's session, with the runtime directory pinned."""
    return [RUNUSER, "-u", binding.account, "--", ENV,
            f"XDG_RUNTIME_DIR={binding.runtime_directory}",
            f"DBUS_SESSION_BUS_ADDRESS={binding.bus_address}", *arguments]


def sole_observation(values):
    """The one value in `values`, or None when there is not exactly one.

    Both fields this module reads can be reported more than once, and a repeated
    field that disagrees is not evidence of either value. Collapsing duplicates
    last-wins made `Id=session-B` followed by `Id=session-A` a verified `A`, so a
    shield could act against a session identity the output never agreed on.
    """
    distinct = set(values)
    return distinct.pop() if len(distinct) == 1 else None


def lock_hint(values):
    """True or False only for a single unambiguous exact yes/no, otherwise unknown.

    `loginctl` reports a lock hint as exactly `yes` or `no`. Every other
    observation — absent, empty, malformed, or repeated with a contradictory
    value — is unknown, and unknown has to stay unknown. Reading an unparseable
    hint as `False` (which `hint == "yes"` did) makes a session reporting
    `LockedHint=garbage` look unlocked, so shield effects act on evidence the
    contract calls unknown (D-022 F07). Unknown is the fail-closed answer here:
    the callers that can act on doubt already treat None as "not verified".
    """
    observed = sole_observation(values)
    if observed == "yes":
        return True
    if observed == "no":
        return False
    return None


def _fields(output):
    """Every `name=value` the query printed, keeping repeats rather than last-wins."""
    observed = {}
    for part in output.split():
        if "=" not in part:
            continue
        name, value = part.split("=", 1)
        observed.setdefault(name, []).append(value)
    return observed


def _single(observed, name):
    """The one unambiguous value of `name`, or None when it is not one value."""
    return sole_observation(observed.get(name, []))


def leader_lifetime(leader):
    """The session leader's own process start time, or None when unobservable.

    `Leader` is a pid and pids are recycled, so a session id plus a leader number
    can be worn by a replacement session. The kernel's start time for that pid is
    unique per process, which makes the pair the lifetime discriminator this
    selector binds to. The reviewed r3 supervisor's fence carries the same pair
    for the X11 profile; this is the C-F side of that contract, not a new one.

    An empty string compares equal to another empty string, so a lifetime this
    host cannot read must not become a value: two different processes would then
    present the same identity and the second would be admitted on the first's
    evidence. None is returned instead, and the selector refuses.
    """
    if type(leader) is not str or not leader.isdigit() or int(leader) <= 0:
        return None
    try:
        stat = (PROC / leader / "stat").read_text()
    except OSError:
        return None
    fields = stat.rsplit(")", 1)[-1].split()
    if len(fields) <= 19:
        return None
    starttime = fields[19]
    return starttime if starttime.isdigit() and int(starttime) > 0 else None


def graphical_seat(run=_run):
    """The unit's one graphical seat and the session active on it, or None.

    The seat is established by enumeration rather than assumed to be `seat0`: a
    host that reports more than one graphical seat cannot name a default reader,
    so it refuses rather than picking one, and a graphical seat whose active
    session is absent, `-`, or reported twice with contradictory values is a fact
    this host did not establish. An unestablished fact is never satisfied.

    Only `CanGraphical` decides which seats are candidates, and it has to be
    *read* before it can decide anything. A seat that reports `no` is a known
    answer and is skipped: it cannot be the console this selector is naming. A
    seat whose fact is absent, malformed, or reported twice with contradictory
    values is not a fact at all, and that refuses the whole selection rather than
    quietly dropping the seat. Collapsing those two cases into `!= "yes"` treated
    an unestablished fact as a determined `no`, which is the same defect this
    selector exists to remove, one level down.
    """
    try:
        listing = run([LOGINCTL, "list-seats", "--no-legend"], timeout=5)
    except Exception:
        return None
    graphical = []
    for line in listing.splitlines():
        parts = line.split()
        if not parts:
            continue
        seat = parts[0]
        command = [LOGINCTL, "show-seat", seat]
        for name in SEAT_QUERY:
            command += ["-p", name]
        try:
            observed = _fields(run(command, timeout=5))
        except Exception:
            return None
        graphical_fact = _single(observed, "CanGraphical")
        if graphical_fact not in ("yes", "no"):
            return None
        if graphical_fact == "no":
            continue
        active = _single(observed, "ActiveSession")
        if not active or active == "-":
            return None
        graphical.append((seat, active))
    if len(graphical) != 1:
        return None
    return graphical[0]


def session_properties(session_id, run=_run):
    """One session's unambiguous selection facts and its lock hint, or None.

    Every field in SESSION_REQUIRED decides whether this is the operator's
    session, so one that is absent, empty, malformed, or repeated with a
    different value makes the whole observation unknown: a selector built on it
    would be admitting a session the host never named. `Id` must come back as the
    session that was asked about, so an answer describing some other session
    cannot stand in for this one.

    `LockedHint` is deliberately not required. Absence there means the lock state
    is unknown, not that the session is unidentified, and the callers that act on
    the lock already treat None as "not verified" (D-022 F07).
    """
    command = [LOGINCTL, "show-session", session_id]
    for name in SESSION_QUERY:
        command += ["-p", name]
    try:
        output = run(command, timeout=5)
    except Exception:
        return None
    observed = _fields(output)
    values = {}
    for name in SESSION_REQUIRED:
        value = _single(observed, name)
        if not value:
            return None
        values[name] = value
    if values["Id"] != session_id:
        return None
    return values, lock_hint(observed.get("LockedHint", []))


def observed_session(binding, run=_run, *, bind_lifetime=False):
    """The operator's own active graphical session, or None when not established.

    Every fact this returns is read from logind and then checked, in one
    selection, against the installed records:

    * the intended operator UID, from the installed unit records, re-checked
      against the session logind reports rather than against a display guess;
    * the unit's active graphical seat, established by enumeration;
    * the session type the installed profile is the record of, so a Wayland unit
      cannot admit an X11 session or the reverse;
    * one unambiguous session, bound to its leader's start time so a recycled pid
      cannot inherit another session's identity.

    `self` is not consulted at all. Rook's static review of the execution
    binding found that logind resolves a caller from its own bus credentials and
    that `loginctl` selects the system transport, so `show-session self` under a
    pinned session-bus address named whatever session that command happened to be
    in - an ssh or root-runner session included - not the operator's console
    session the shield is acting on. Asking for a *named* session and then
    proving it is the operator's is the whole contract, and neither `self` nor
    `auto` establishes it.

    The first element of the result is the session `Id` by default, which is what
    a caller that only wants to name the session needs. `bind_lifetime=True`
    returns the whole identity instead - `Id`, `User`, `Seat`, `Class`, `Type`,
    `Remote`, `Leader` and the leader's start time - for a caller that has to
    recognise the *same* session later rather than merely name it. A session id
    is reusable and a leader pid is recycled, so an Id on its own cannot tell a
    session from a replacement wearing its name; the pair (leader, start time)
    can. The screen shield passes it because that is exactly the question `on()`
    asks about the blank it raised, and a caller that compared only the Id would
    let a replacement session inherit the earlier cycle's ownership.

    Nothing here is a fallback. Unavailability is reported rather than raised:
    `off()` must never refuse the sleep cycle, and the callers that can act on
    doubt already treat None as "not verified". `locked` is True, False or None
    under the same rule as `lock_hint`.
    """
    if type(binding) is not WaylandBinding:
        return None
    try:
        expected_type = SESSION_TYPE_FOR_PROFILE.get(installed_profile())
    except ValueError:
        return None
    if expected_type is None:
        return None
    seat = graphical_seat(run)
    if seat is None:
        return None
    seat_id, session_id = seat
    properties = session_properties(session_id, run)
    if properties is None:
        return None
    values, locked = properties
    lifetime = leader_lifetime(values["Leader"])
    if (values["Active"] != "yes"
            or values["Class"] != "user"
            or values["Type"] != expected_type
            or values["Remote"] != "no"
            or values["Seat"] != seat_id
            or values["User"] != str(binding.uid)
            or lifetime is None):
        return None
    identity = values["Id"]
    if bind_lifetime:
        identity = (values["Id"], values["User"], values["Seat"],
                    values["Class"], values["Type"], values["Remote"],
                    values["Leader"], lifetime)
    return identity, locked


def chromium_arguments(process):
    """Arguments as Chromium rewrites them: one space-joined argv[0] entry.

    Read on C (30 Sep 2026): `/proc/<browser>/cmdline` holds the whole command
    line in a single NUL-terminated entry, so an exact profile match must split
    that entry exactly like the reviewed X11 fence does.
    """
    if len(process.argv) != 1:
        return process.argv
    try:
        return tuple(shlex.split(process.argv[0]))
    except ValueError:
        return ()


class MutterIdle:
    """Idle seconds reported by the compositor, never by a guest display."""

    def __init__(self, binding, run=_run, timeout_seconds=5):
        if type(binding) is not WaylandBinding or not callable(run):
            raise ValueError("invalid idle configuration")
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid idle configuration")
        self.binding = binding
        self.run = run
        self.timeout = timeout_seconds

    def seconds(self):
        output = self.run(operator_command(self.binding, BUSCTL, "--user", "call", *IDLE_INTERFACE),
                          timeout=self.timeout)
        match = re.fullmatch(r"t ([0-9]{1,20})", output)
        if match is None:
            raise ValueError("idle_observation_unavailable")
        milliseconds = int(match.group(1))
        if milliseconds > MAX_IDLE_MILLISECONDS:
            raise ValueError("idle_observation_out_of_range")
        return milliseconds / 1000


class GnomeShield:
    """Best-effort display blank through the GNOME screen shield.

    `off()` never raises: the outcome is reported through `outcome` so the
    controller can record it. It refuses to activate a shield that would lock
    the kiosk (`lock-enabled` not exactly false) and reports that refusal
    instead. It also records whether *this cycle* is the one that put a shield
    up, because that is the only shield `on()` may take down. An active shield
    this cycle did not create is somebody else's: a login lock, the operator's
    own blank, or a blank whose activation was never confirmed. Dismissing one
    would trade a visible appliance for a silently discarded authentication, so
    `on()` reports `preserved_unowned` and changes nothing. A shield this cycle
    did confirm is removed and verified, because a console left behind a shield
    is a real appliance fault the operator must see; when that removal cannot be
    verified the cycle fails closed.

    Ownership is proved, not remembered. A blank this cycle confirmed is only
    taken down while the same session that raised it is still the session being
    acted on, and that session is *observed* through the operator's own session
    bus rather than assumed from an instance or from the environment. The
    instance itself proves nothing: the runner caches one per installed profile
    and reuses it across sleep cycles, so a remembered boolean would let a later
    cycle spend an earlier cycle's ownership on a shield that cycle never raised
    - including on an authentication lock that appeared in between. A cycle that
    cannot show it created the blank, or cannot show the session is still the
    same one, preserves the shield and says which check refused.
    """

    def __init__(self, binding, run=_run, timeout_seconds=5, reader=None):
        if type(binding) is not WaylandBinding or not callable(run):
            raise ValueError("invalid shield configuration")
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 30:
            raise ValueError("invalid shield configuration")
        self.binding = binding
        self.run = run
        self.timeout = timeout_seconds
        self.outcome = "not_attempted"
        self.owned = False
        self.reader = reader if reader is not None else (
            lambda: observed_session(self.binding, self.run, bind_lifetime=True))
        # (session_id, cycle) of the blank this records having raised, and the
        # number of off() calls that have ever run on this instance.
        self._raised = None
        self.cycle = 0

    def same_session(self):
        """The current operator session identity, or None when unverifiable."""
        return self.reader()

    def locks_kiosk(self):
        value = self.run(operator_command(self.binding, GSETTINGS, "get", *LOCK_ENABLED),
                         timeout=self.timeout)
        if value not in ("true", "false"):
            raise ValueError("shield_setting_unavailable")
        return value == "true"

    def active(self):
        value = self.run(operator_command(self.binding, BUSCTL, "--user", "call", *SHIELD, "GetActive"),
                         timeout=self.timeout)
        if value not in ("b true", "b false"):
            raise ValueError("shield_state_unavailable")
        return value == "b true"

    def off(self):
        """Attempt the blank; report the outcome and never refuse the cycle.

        A confirmed blank is this cycle's to remove again. An unconfirmed one is
        not: the request was sent and the shell never said it took, so whatever
        is on screen now is uncertain and is preserved rather than dismissed.

        A shield that was *already* up before this call is not this cycle's
        either, even though the shell will happily confirm it afterwards: the
        blank this records as its own is one that started from an inactive
        shield in a session it could name.
        """
        self.cycle += 1
        self._raised = None
        try:
            if self.locks_kiosk() is True:
                self.outcome = "skipped_lock_enabled"
                self.owned = False
                return self.outcome
            observed = self.same_session()
            if observed is None:
                self.outcome = "session_unverified"
                self.owned = False
                return self.outcome
            session_id, locked = observed
            if locked is not False:
                self.outcome = "preserved_authentication"
                self.owned = False
                return self.outcome
            if self.active() is True:
                # Somebody else's blank, raised before this cycle asked. Sending
                # SetActive(true) would be a no-op the shell still confirms, and
                # that confirmation is exactly what would make it ours to take
                # down later.
                self.outcome = "preserved_preexisting"
                self.owned = False
                return self.outcome
            self._set(active=True)
        except Exception as error:
            self.outcome = "unavailable_" + type(error).__name__
            self.owned = False
            return self.outcome
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.active() is True:
                self.outcome = "confirmed"
                self.owned = True
                self._raised = (session_id, self.cycle)
                return self.outcome
            time.sleep(0.2)
        self.outcome = "unconfirmed"
        self.owned = False
        return self.outcome

    def on(self):
        """Take down this cycle's own blank; never anybody else's shield.

        The ownership boolean is only ever a hint: it is re-proved against the
        live session before the shield is touched, and every path that gives up
        the claim consumes it so a cached instance cannot spend it later.
        """
        if self.active() is False:
            self.owned = False
            self._raised = None
            self.outcome = "not_needed"
            return self.outcome
        if not self.owned:
            self.outcome = "preserved_unowned"
            return self.outcome
        if self._raised is None or self._raised[1] != self.cycle:
            self.owned = False
            self.outcome = "preserved_stale_cycle"
            return self.outcome
        observed = self.same_session()
        if observed is None:
            self.outcome = "preserved_session_unverified"
            return self.outcome
        session_id, locked = observed
        if session_id != self._raised[0]:
            self.owned = False
            self.outcome = "preserved_session_replaced"
            return self.outcome
        if locked is not False:
            # The blank this cycle raised has since become an authentication
            # lock. The operator is being asked to log in; taking it down would
            # answer that challenge on their behalf.
            self.owned = False
            self.outcome = "preserved_authentication"
            return self.outcome
        self._set(active=False)
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            if self.active() is False:
                self.outcome = "restored"
                self.owned = False
                self._raised = None
                return self.outcome
            time.sleep(0.2)
        raise ValueError("shield_unblank_unconfirmed")

    def _set(self, *, active):
        self.run(operator_command(self.binding, BUSCTL, "--user", "call", *SHIELD, "SetActive",
                                  "b", "true" if active else "false"), timeout=self.timeout)


def _python_executable(path):
    """The system interpreter that runs the supervisor, through any point release."""
    if path == PYTHON_EXECUTABLE:
        return True
    suffix = path.removeprefix(PYTHON_EXECUTABLE + ".")
    return suffix.isdigit() and suffix != ""


class NativePinShield:
    """Explicit candidate profile: wake retains the mandatory PIN actor.

    Reports concealment, never physical display power. No stock unlock fallback.
    """
    interface = ('org.gnome.Shell', '/org/sage/Lantern/DesktopGuard',
                 'org.sage.Lantern.DesktopGuard1')

    def __init__(self, binding, run=_run):
        if type(binding) is not WaylandBinding or not callable(run):
            raise ValueError('invalid_native_shield_configuration')
        self.binding, self.run = binding, run
        self.outcome = 'not_attempted'

    def call(self, method):
        result = self.run(operator_command(self.binding, BUSCTL, '--user', 'call',
                         *self.interface, method), timeout=5)
        if result not in ('b true', 'b false'):
            raise ValueError('native_guard_unavailable')
        return result == 'b true'

    def locks_kiosk(self):
        return True

    def active(self):
        return self.call('GetLocked')

    def off(self):
        if self.call('PrepareSleep') is not True or self.active() is not True:
            raise ValueError('native_guard_concealment_unconfirmed')
        self.outcome = 'concealed'
        return self.outcome

    def on(self):
        if self.call('Wake') is not True or self.active() is not True:
            raise ValueError('native_guard_wake_unconfirmed')
        self.outcome = 'restored_guarded'
        return self.outcome


class ConsoleFence:
    """Exact-process fence over the mode supervisor's console browser group.

    The supervisor keeps running: it never touches a model, the app server or
    the private endpoints, and its respawn loop treats a SIGSTOPped browser as
    running. The browser and every process in its group are frozen for the
    sleep cycle and resumed by the same pinned identities, so the console keeps
    its page state instead of reloading. Every failure returns False or raises
    with its gate; a fence is never released implicitly.
    """

    def __init__(self, *, binding, auth_locked, backend=None, timeout_seconds=5,
                 scan_rounds=6, max_members=MAX_MEMBERS, require_native_pin=False):
        if (type(binding) is not WaylandBinding or not callable(auth_locked)
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 10
                or type(scan_rounds) is not int or not 2 <= scan_rounds <= 12
                or type(max_members) is not int or not 1 <= max_members <= MAX_MEMBERS
                or type(require_native_pin) is not bool):
            raise ValueError("invalid console fence configuration")
        self.binding = binding
        self.auth_locked = auth_locked
        self.backend = backend if backend is not None else LinuxProcesses()
        self.timeout = timeout_seconds
        self.scan_rounds = scan_rounds
        self.max_members = max_members
        self.require_native_pin = require_native_pin
        self._supervisor = None
        self._browser = None
        self._pinned_supervisor = None
        self._pinned_browser = None
        self._members = {}
        self._cleared = False
        self._deadline = None

    def _scope(self, pid):
        root = Path("/proc") / str(pid)
        try:
            lines = (root / "cgroup").read_text().splitlines()
        except (FileNotFoundError, ProcessLookupError):
            return False
        if type(lines) is not list or len(lines) != 1:
            return False
        _, separator, path = lines[0].partition("::")
        return bool(separator) and path.endswith(CONSOLE_SCOPE + str(pid) + ".scope")

    def _supervisor_of(self, table):
        matches = []
        expected = (SUPERVISOR_ARGV, (PYTHON_EXECUTABLE, SUPERVISOR))
        if self.require_native_pin:
            expected = tuple(argv + ('--require-native-pin',) for argv in expected)
        for process in table.values():
            if (process.uid == self.binding.uid and process.state != "Z"
                    and _python_executable(process.executable)
                    and process.argv in expected):
                matches.append(process)
        if len(matches) != 1:
            raise ValueError("console_supervisor_ambiguous")
        supervisor = matches[0]
        if supervisor.pid <= 1 or supervisor.start_ticks <= 0 or not self._scope(supervisor.pid):
            raise ValueError("console_supervisor_unscoped")
        return supervisor

    def _browser_of(self, table, supervisor):
        matches = []
        for process in table.values():
            if (process.uid == self.binding.uid and process.state != "Z"
                    and process.parent == supervisor.pid
                    and process.executable in CHROMIUM_EXECUTABLES
                    and self.binding.profile_argument in chromium_arguments(process)):
                matches.append(process)
        if len(matches) != 1:
            raise ValueError("console_browser_ambiguous")
        browser = matches[0]
        if browser.pid <= 1 or browser.start_ticks <= 0 or browser.pgid != browser.pid:
            raise ValueError("console_browser_ungrouped")
        return browser

    def _descendants(self, table, root):
        members = {}
        parents = {root.pid}
        for _ in range(self.max_members + 1):
            added = {process.pid: process for process in table.values()
                     if process.parent in parents and process.pid != root.pid
                     and process.state != "Z" and process.pid not in members}
            if not added:
                return members
            members.update(added)
            parents = set(added)
            if len(members) > self.max_members:
                raise ValueError("console_group_oversized")
        raise ValueError("console_group_scan_bound")

    def _inventory(self, table):
        supervisor = self._supervisor_of(table)
        browser = self._browser_of(table, supervisor)
        members = self._descendants(table, browser)
        for member in members.values():
            if member.uid != self.binding.uid or member.pgid != browser.pid:
                raise ValueError("console_group_foreign_member")
        frozen = {browser.pid, *members}
        for process in table.values():
            if process.uid != self.binding.uid or process.state == "Z":
                continue
            if process.pgid == browser.pid and process.pid not in frozen:
                raise ValueError("console_group_process_outside_fence")
            if process.pid not in frozen and self.binding.profile_argument in chromium_arguments(process):
                raise ValueError("console_profile_process_outside_fence")
        return supervisor, browser, members

    def _stopped(self, pid, start_ticks):
        current = self.backend.snapshot().get(pid)
        return (current is not None and current.start_ticks == start_ticks
                and current.state in {"T", "t"})

    def _wait(self, predicate):
        deadline = time.monotonic() + self.timeout
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def _pin(self, process):
        identity = (process.pid, process.start_ticks)
        if identity not in self._members:
            if len(self._members) >= self.max_members:
                raise ValueError("console_group_oversized")
            self._members[identity] = self.backend.open(process)
        return identity

    @staticmethod
    def _identity(process):
        return (process.parent, process.start_ticks, process.uid, process.pgid,
                process.executable, process.argv)

    def _unchanged(self, pinned, current):
        return (current is not None and pinned is not None
                and (current.pid, current.start_ticks) == pinned)

    def preflight(self):
        """Signal-free inventory proof: identities stable across two scans, nothing frozen."""
        deadline = time.monotonic() + self.timeout
        try:
            self.backend.validate_host(executables=(CHROMIUM,))
            for _ in range(self.scan_rounds):
                if time.monotonic() >= deadline:
                    return False
                supervisor, browser, members = self._inventory(self.backend.snapshot())
                if supervisor.state in {"T", "t"} or browser.state in {"T", "t"}:
                    return False
                handles = []
                try:
                    for process in (browser, *members.values()):
                        if time.monotonic() >= deadline:
                            return False
                        handles.append(self.backend.open(process))
                    following = self._inventory(self.backend.snapshot())
                    if (self._identity(browser) == self._identity(following[1])
                            and self._identity(supervisor) == self._identity(following[0])
                            and {pid: self._identity(process) for pid, process in members.items()}
                            == {pid: self._identity(process) for pid, process in following[2].items()}
                            and not any(self.backend.exited(handle) for handle in handles)
                            and time.monotonic() < deadline):
                        return True
                finally:
                    for handle in handles:
                        self.backend.close(handle)
            return False
        except Exception:
            return False

    def quiesce(self):
        """Return exact True only after the browser group is frozen and cleared."""
        self._deadline = time.monotonic() + self.timeout
        self.refusal = "unknown"
        try:
            self.backend.validate_host(executables=(CHROMIUM,))
            if self.backend.fence_state() != "present":
                self.refusal = "fence_absent"
                return False
            table = self.backend.snapshot()
            supervisor, browser, members = self._inventory(table)
            for process in (supervisor, browser):
                if process.state in {"T", "t"}:
                    # The reviewed transition asks for the same fence twice in
                    # one cycle (request_sleep and resume); a fence this object
                    # already holds stays valid, anything else is unknown.
                    if (self._cleared
                            and self._unchanged(self._pinned_supervisor, supervisor)
                            and self._unchanged(self._pinned_browser, browser)):
                        self.refusal = "still_cleared"
                        return True
                    self.refusal = "already_frozen"
                    return False
            if self._supervisor is None:
                self._supervisor = self.backend.open(supervisor)
            if self._browser is None:
                self._browser = self.backend.open(browser)
            if self.backend.exited(self._supervisor) or self.backend.exited(self._browser):
                self.refusal = "identity_exited"
                return False
            self._pinned_supervisor = (supervisor.pid, supervisor.start_ticks)
            self._pinned_browser = (browser.pid, browser.start_ticks)
            self.backend.send(self._browser, signal.SIGSTOP)
            if not self._wait(lambda: self._stopped(browser.pid, browser.start_ticks)):
                self.refusal = "browser_not_stopped"
                return False
            stable = False
            for _ in range(self.scan_rounds):
                if time.monotonic() >= self._deadline:
                    self.refusal = "scan_deadline"
                    return False
                table = self.backend.snapshot()
                current_supervisor, current_browser, current_members = self._inventory(table)
                if (not self._unchanged(self._pinned_supervisor, current_supervisor)
                        or not self._unchanged(self._pinned_browser, current_browser)):
                    self.refusal = "identity_changed"
                    return False
                fresh = []
                for process in current_members.values():
                    identity = self._pin(process)
                    if not self._stopped(*identity):
                        self.backend.send(self._members[identity], signal.SIGSTOP)
                        fresh.append(identity)
                for identity in fresh:
                    if not self._wait(lambda identity=identity: self._stopped(*identity)):
                        self.refusal = "member_not_stopped"
                        return False
                if not fresh:
                    stable = True
                    break
            if not stable:
                self.refusal = "scan_not_stable"
                return False
            table = self.backend.snapshot()
            supervisor, browser, members = self._inventory(table)
            if (not self._unchanged(self._pinned_supervisor, supervisor)
                    or not self._unchanged(self._pinned_browser, browser)):
                self.refusal = "final_identity_changed"
                return False
            for (pid, start_ticks), handle in self._members.items():
                current = table.get(pid)
                if self.backend.exited(handle):
                    continue
                if (current is None or (current.start_ticks, current.pgid) != (start_ticks, browser.pgid)
                        or current.state not in {"T", "t"}):
                    self.refusal = "member_state_changed"
                    return False
            if (self._stopped(browser.pid, browser.start_ticks) is False
                    or self.backend.exited(self._supervisor) or self.backend.exited(self._browser)
                    or self.backend.fence_state() != "present"):
                self.refusal = "final_fence_state"
                return False
            if self._scope(supervisor.pid) is not True:
                self.refusal = "scope_lost"
                return False
            self._cleared = True
            self.refusal = "cleared"
            return True
        except Exception as error:
            self.refusal = "exception:" + type(error).__name__ + ":" + str(error)[:80]
            return False

    def resume(self):
        """After wake: auth must be locked and the controller's fence removed."""
        self._deadline = time.monotonic() + self.timeout
        try:
            if not self._cleared or self._browser is None or self._supervisor is None:
                return False
            self.backend.validate_host(executables=(CHROMIUM,))
            if self.auth_locked() is not True or self.backend.fence_state() != "absent":
                return False
            table = self.backend.snapshot()
            supervisor, browser, members = self._inventory(table)
            if (not self._unchanged(self._pinned_supervisor, supervisor)
                    or not self._unchanged(self._pinned_browser, browser)):
                return False
            for (pid, start_ticks), handle in self._members.items():
                if self.backend.exited(handle):
                    continue
                current = table.get(pid)
                if (current is None or current.start_ticks != start_ticks
                        or current.state not in {"T", "t"}):
                    return False
                self.backend.send(handle, signal.SIGCONT)
            if self._stopped(browser.pid, browser.start_ticks) is not True:
                return False
            self.backend.send(self._browser, signal.SIGCONT)
            def running():
                current = self.backend.snapshot().get(browser.pid)
                return (current is not None and current.start_ticks == browser.start_ticks
                        and current.state not in {"T", "t", "Z"})
            if not self._wait(running):
                if not self.backend.exited(self._browser):
                    self.backend.send(self._browser, signal.SIGSTOP)
                return False
            if self._scope(supervisor.pid) is not True:
                return False
            self._cleared = False
            for handle in self._members.values():
                self.backend.close(handle)
            self._members.clear()
            return True
        except Exception:
            return False

    def close(self):
        """Release descriptors only; never resume a frozen group implicitly."""
        for handle in self._members.values():
            self.backend.close(handle)
        self._members.clear()
        if self._browser is not None:
            self.backend.close(self._browser)
        if self._supervisor is not None:
            self.backend.close(self._supervisor)
        self._browser = None
        self._supervisor = None
        self._pinned_supervisor = None
        self._pinned_browser = None
        self._cleared = False
