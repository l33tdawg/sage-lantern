"""Bounded root/Linux observations for a separately fenced suspend adapter.

The parent MUST hold the durable journal fence and serialize every competing
sleep/auth/service-start path throughout these calls and subsequent release.
These observations alone do not prevent a new job after the final check.
Unknown evidence never authorizes releasing a fence. There is no resume retry,
automatic service restart, PIN handling or journal clearing here.

Requires systemctl, busctl, cgroup v2 at /sys/fs/cgroup and the kernel success counter
at /sys/power/suspend_stats/success. Missing/inaccessible facilities fail closed.
A synchronous systemctl start is NOT proof: exactly one kernel success increment,
quiescent sleep units and unchanged active model MainPIDs are also required. The
counter is global; correlation depends on the parent's exclusive sleep fence.
The command timeout bounds awake monotonic time, not time spent suspended.
Timed-out systemctl clients are killed, but their submitted jobs may persist.

Injected run(argv, timeout), read_file(path, limit), list_directories(path),
clock(), uid() and platform() are trusted test/construction dependencies, never
request parameters. File readers must reject links; missing cgroups are allowed
only when their prior validated path has actually disappeared after stopping.
An already inactive service with no ControlGroup is supported only in the
default system.slice, whose canonical service cgroup is still checked.
Effective D-Bus Conditions must include a non-trigger, negated path condition
for FENCE. The parent owns creating and validating that root-only fence file;
condition verification does not prove that the file is currently present.
Idle evidence is not resume evidence and never proves a sleep cycle occurred.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import shlex
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable


SYSTEMCTL = "/usr/bin/systemctl"
BUSCTL = "/usr/bin/busctl"
AUTH = "sage-lantern-device-auth.service"
VOICE = "sage-lantern-voice-loop.service"
CAMERA = "sage-lantern-camera.service"
DISPLAY = "sage-lantern-display-power.service"
FLEET_ADMIN = "sage-lantern-fleet-admin.service"
# Auxiliary workers in the reviewed stop/start order (A/B: X11 display power and
# camera). A unit that is not installed cannot be stopped, so a profile may only
# remove workers from this set, never add one.
AUXILIARY_SERVICES = (CAMERA, FLEET_ADMIN, DISPLAY)
STOP_SERVICES = frozenset({AUTH, VOICE, *AUXILIARY_SERVICES})
# C-F runs no camera unit and no X11 display-power unit (read on C, 29 Sep 2026:
# both are not-found there), and the console is fenced as a process group instead
# of a service. The C-F profile therefore stops the three workers it runs.
WAYLAND_STOP_SERVICES = frozenset({AUTH, VOICE, FLEET_ADMIN})
SLEEP_UNITS = (
    "sleep.target", "suspend.target", "hibernate.target", "hybrid-sleep.target",
    "suspend-then-hibernate.target", "systemd-suspend.service", "systemd-hibernate.service",
    "systemd-hybrid-sleep.service", "systemd-suspend-then-hibernate.service",
)
LLM = "sage-lantern-llm.service"
VLM = "sage-lantern-vlm.service"
# Resident model units in reviewed order (D-016: NPU text on 8081, NPU vision on
# 8082 in the same OpenAI-shaped contract). The chat lane is mandatory: the
# appliance is only sleepable while it is resident. A shipped second model unit
# joins the residency contract once the reviewed activation lane has enabled it —
# an enabled unit that is not active is unknown evidence and refuses the call,
# while a unit that was never enabled is not part of this installation's profile.
MODEL_UNITS = (LLM, VLM)
IDLE_SUSPEND = "sage-lantern-idle-suspend.service"
DEPLOYMENT_FENCE = "/run/sage-lantern-deployment.pending"
CGROUP = Path("/sys/fs/cgroup")
COUNTER = Path("/sys/power/suspend_stats/success")
FENCE = "/run/sage-lantern-sleep.fenced"
MAX_OUTPUT = 32768


@dataclass(frozen=True)
class SystemEvidence:
    confirmed: bool
    reason: str
    before_success: int | None = None
    after_success: int | None = None
    units: tuple = ()


class _SleepBusy(Exception):
    pass


def _run(argv, timeout):
    process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    output = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError
                chunk = os.read(process.stdout.fileno(), 8192)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > MAX_OUTPUT:
                    raise ValueError
        code = process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, code, bytes(output))
    finally:
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=2)
        process.stdout.close()


def _safe_path(path):
    for parent in reversed(path.parents):
        if not stat.S_ISDIR(parent.lstat().st_mode):
            raise ValueError


def _read_file(path, limit):
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError
        raw = bytearray()
        while True:
            chunk = os.read(descriptor, limit + 1 - len(raw))
            if not chunk:
                return bytes(raw)
            raw.extend(chunk)
            if len(raw) > limit:
                raise ValueError
    finally:
        os.close(descriptor)


def _list_directories(path):
    _safe_path(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        children = []
        with os.scandir(descriptor) as entries:
            count = 0
            for entry in entries:
                count += 1
                if count > 1024 or entry.is_symlink():
                    raise ValueError
                if entry.is_dir(follow_symlinks=False):
                    children.append(entry.name)
        return children
    finally:
        os.close(descriptor)


class SuspendSystem:
    def __init__(self, *, run: Callable = _run, read_file: Callable = _read_file,
                 list_directories: Callable = _list_directories,
                 clock: Callable = time.monotonic, uid: Callable = os.geteuid,
                 platform: Callable = lambda: sys.platform, timeout_seconds: float = 30,
                 sleep: Callable = time.sleep, stop_services=STOP_SERVICES):
        if type(timeout_seconds) not in (int, float) or not 1 <= timeout_seconds <= 90:
            raise ValueError("invalid_timeout")
        if (type(stop_services) is not frozenset or not stop_services <= STOP_SERVICES
                or not {AUTH, VOICE} <= stop_services):
            raise ValueError("invalid_stop_services")
        self.run = run
        self.read_file = read_file
        self.list_directories = list_directories
        self.clock = clock
        self.sleep = sleep
        self.uid = uid
        self.platform = platform
        self.timeout = timeout_seconds
        self.stop_services = stop_services
        self._lock = threading.Lock()

    def auxiliary_services(self):
        """Workers this installation stops before sleep and starts after wake."""
        return tuple(service for service in AUXILIARY_SERVICES if service in self.stop_services)

    def _remaining(self, deadline):
        remaining = deadline - self.clock()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _command(self, arguments, deadline):
        return self._execute([SYSTEMCTL, *arguments], deadline)

    def _execute(self, argv, deadline):
        result = self.run(argv, timeout=self._remaining(deadline))
        self._remaining(deadline)
        if result.returncode != 0 or type(result.stdout) is not bytes or len(result.stdout) > MAX_OUTPUT:
            raise ValueError
        return result.stdout.decode("utf-8", errors="strict")

    def _show(self, unit, deadline, fields):
        text = self._command(["show", "--no-pager", "--property=" + ",".join(fields), unit], deadline)
        values = {}
        for line in text.splitlines():
            key, separator, value = line.partition("=")
            if not separator or key not in fields or key in values:
                raise ValueError
            values[key] = value
        if set(values) != set(fields):
            raise ValueError
        return values

    @staticmethod
    def _no_job(value):
        return value in ("", "0")

    def _model_units(self, deadline):
        """Resident model units of this installation's profile, fail closed."""
        units = []
        for unit in MODEL_UNITS:
            value = self._show(unit, deadline, ("LoadState", "UnitFileState"))
            if unit != LLM:
                if value["LoadState"] == "not-found":
                    continue
                if value["UnitFileState"] not in ("enabled", "enabled-runtime"):
                    continue
            units.append(unit)
        return tuple(units)

    def _model_pids(self, deadline, *, skip=None):
        """Every resident model instance must be loaded, active and job-free."""
        pids = {}
        for unit in self._model_units(deadline):
            if unit == skip:
                continue
            value = self._show(unit, deadline, ("LoadState", "ActiveState", "Job", "MainPID"))
            if (value["LoadState"] != "loaded" or value["ActiveState"] != "active"
                    or not self._no_job(value["Job"]) or not re.fullmatch(r"[1-9][0-9]{0,9}", value["MainPID"])):
                raise ValueError
            pids[unit] = value["MainPID"]
        return pids

    def model_units(self) -> SystemEvidence:
        """Read-only observation of the resident model set for the parent."""
        def observe(deadline):
            return SystemEvidence(True, "model_units_verified", units=self._model_units(deadline))
        return self._perform(observe)

    def _voice_unit(self, deadline):
        """The voice lane joins the drain contract only once it was enabled.

        Same rule as the second model unit: a shipped worker that was never
        enabled is not part of this installation's profile, and a missing
        optional component must not make the appliance unusable or unsleepable.
        An enabled unit that is not active is unknown evidence and refuses.
        """
        value = self._show(VOICE, deadline, ("LoadState", "UnitFileState"))
        if value["LoadState"] == "not-found":
            return None
        return VOICE if value["UnitFileState"] in ("enabled", "enabled-runtime") else None

    def voice_lane(self) -> SystemEvidence:
        def observe(deadline):
            unit = self._voice_unit(deadline)
            return SystemEvidence(True, "voice_lane_verified", units=(unit,) if unit else ())
        return self._perform(observe)

    def _service(self, service, deadline):
        value = self._show(service, deadline, ("LoadState", "ActiveState", "Job", "ControlGroup", "KillMode", "Slice"))
        if value["LoadState"] != "loaded" or value["KillMode"] != "control-group":
            raise ValueError
        self._guard(service, deadline)
        return value

    def _guard(self, service, deadline, fence=FENCE):
        encoded = "".join(character if character.isascii() and character.isalnum()
                          else f"_{ord(character):02x}" for character in service)
        text = self._execute([
            BUSCTL, "--system", "--no-pager", "get-property", "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1/unit/" + encoded,
            "org.freedesktop.systemd1.Unit", "Conditions",
        ], deadline)
        tokens = shlex.split(text)
        if (len(tokens) < 2 or tokens[0] != "a(sbbsi)"
                or re.fullmatch(r"[0-9]{1,3}", tokens[1]) is None):
            raise ValueError
        count = int(tokens[1])
        if count > 128 or len(tokens) != 2 + count * 5:
            raise ValueError
        guarded = False
        for offset in range(2, len(tokens), 5):
            kind, trigger, negate, parameter, result = tokens[offset:offset + 5]
            if (trigger not in ("true", "false") or negate not in ("true", "false")
                    or re.fullmatch(r"-?[0-9]{1,10}", result) is None
                    or not -(2 ** 31) <= int(result) < 2 ** 31):
                raise ValueError
            if kind == "ConditionPathExists" and trigger == "false" and negate == "true" and parameter == fence:
                guarded = True
        if not guarded:
            raise ValueError

    def verify_stop_guards(self) -> SystemEvidence:
        def verify(deadline):
            for service in sorted(self.stop_services):
                self._service(service, deadline)
            return SystemEvidence(True, "guards_verified")
        return self._perform(verify)

    @staticmethod
    def _cgroup(value):
        path = PurePosixPath(value)
        if (not value or not value.startswith("/") or value.startswith("//") or value == "/" or ".." in path.parts
                or str(path) != value or not re.fullmatch(r"/[A-Za-z0-9_.:@/\\-]{1,1024}", value)):
            raise ValueError
        return CGROUP / value.lstrip("/")

    def _empty_tree(self, root, deadline):
        pending = [root]
        count = 0
        while pending:
            self._remaining(deadline)
            path = pending.pop()
            count += 1
            if count > 128:
                raise ValueError
            try:
                children = self.list_directories(path)
            except FileNotFoundError:
                continue
            raw = self.read_file(path / "cgroup.procs", 8192)
            if raw.strip():
                raise ValueError
            if type(children) not in (list, tuple) or len(children) > 128:
                raise ValueError
            for child in children:
                if type(child) is not str or child in (".", "..") or "/" in child or not child:
                    raise ValueError
                pending.append(path / child)

    def _sleep_idle(self, deadline):
        for unit in SLEEP_UNITS:
            value = self._show(unit, deadline, ("LoadState", "ActiveState", "Job"))
            required = unit in ("sleep.target", "suspend.target", "systemd-suspend.service")
            if (value["LoadState"] not in (("loaded",) if required else ("loaded", "not-found"))
                    or value["ActiveState"] not in ("inactive", "active", "deactivating")
                    or (not self._no_job(value["Job"])
                        and re.fullmatch(r"[1-9][0-9]{0,9}", value["Job"]) is None)):
                raise ValueError
            if value["ActiveState"] != "inactive" or not self._no_job(value["Job"]):
                raise _SleepBusy

    def _wait_sleep_idle(self, deadline):
        while True:
            try:
                self._sleep_idle(deadline)
                return
            except _SleepBusy:
                self.sleep(min(0.1, self._remaining(deadline)))

    def _counter(self):
        raw = self.read_file(COUNTER, 32)
        if type(raw) is not bytes or re.fullmatch(rb"[0-9]{1,19}\n?", raw) is None:
            raise ValueError
        return int(raw)

    def _perform(self, action):
        if not self._lock.acquire(blocking=False):
            return SystemEvidence(False, "busy")
        try:
            if self.uid() != 0 or self.platform() != "linux":
                return SystemEvidence(False, "root_linux_required")
            return action(self.clock() + self.timeout)
        except Exception:
            return SystemEvidence(False, "unknown")
        finally:
            self._lock.release()

    def stop_verified(self, service: str) -> SystemEvidence:
        return self._verify_service(service, stop_requested=True)

    def deployment_stop_verified(self, service, *, expected_pid, expected_invocation):
        """Stop an exact active idle/model invocation; never a generic unit stop."""
        if (service not in (IDLE_SUSPEND, *MODEL_UNITS) or type(expected_pid) is not int
                or expected_pid <= 0 or type(expected_invocation) is not str
                or re.fullmatch(r"[0-9a-f]{32}", expected_invocation) is None):
            return SystemEvidence(False, "invalid_deployment_identity")

        def stop(deadline):
            self._sleep_idle(deadline)
            self.read_file(CGROUP / "cgroup.controllers", 8192)
            before = self._deployment_service(service, deadline)
            if (before["ActiveState"] != "active" or before["MainPID"] != str(expected_pid)
                    or before["InvocationID"] != expected_invocation or not before["ControlGroup"]):
                raise ValueError
            group = self._cgroup(before["ControlGroup"])
            # The stop target itself is excluded; every other resident model unit must
            # come through the stop unchanged.
            skip = service if service in MODEL_UNITS else None
            model_pids = self._model_pids(deadline, skip=skip)
            self._guard(service, deadline, DEPLOYMENT_FENCE if service == IDLE_SUSPEND else FENCE)
            self._command(["stop", service], deadline)
            after = self._deployment_service(service, deadline)
            self._deployment_empty(service, after, group, deadline)
            if after["InvocationID"] not in ("", expected_invocation):
                raise ValueError
            if model_pids and self._model_pids(deadline, skip=skip) != model_pids:
                raise ValueError
            self._sleep_idle(deadline)
            if not hasattr(self, "_deployment_stops"):
                self._deployment_stops = {}
            self._deployment_stops[service] = (after, group)
            return SystemEvidence(True, "deployment_stopped_verified")
        return self._perform(stop)

    def _deployment_service(self, service, deadline):
        value = self._show(service, deadline, ("LoadState", "ActiveState", "Job", "MainPID",
            "InvocationID", "ControlGroup", "KillMode", "Slice"))
        if (value["LoadState"] != "loaded" or value["KillMode"] != "control-group"
                or not self._no_job(value["Job"])):
            raise ValueError
        return value

    def _deployment_empty(self, service, value, group, deadline):
        if value["ActiveState"] != "inactive" or value["MainPID"] != "0":
            raise ValueError
        self._empty_tree(group, deadline)
        if value["ControlGroup"]:
            self._empty_tree(self._cgroup(value["ControlGroup"]), deadline)
        if self._deployment_service(service, deadline) != value:
            raise ValueError

    def deployment_inactive_verified(self, service):
        """Read-only cgroup verification after model shutdown, without an active-LLM assumption."""
        if service not in self.stop_services | {IDLE_SUSPEND, *MODEL_UNITS}:
            return SystemEvidence(False, "service_not_allowed")

        def verify(deadline):
            self.read_file(CGROUP / "cgroup.controllers", 8192)
            self._guard(service, deadline, DEPLOYMENT_FENCE if service == IDLE_SUSPEND else FENCE)
            value = self._deployment_service(service, deadline)
            if service in (IDLE_SUSPEND, *MODEL_UNITS):
                prior, group = self._deployment_stops[service]
                if value != prior:
                    raise ValueError
            elif value["ControlGroup"]:
                group = self._cgroup(value["ControlGroup"])
            elif value["Slice"] == "system.slice":
                group = self._cgroup("/system.slice/" + service)
            else:
                raise ValueError
            self._deployment_empty(service, value, group, deadline)
            return SystemEvidence(True, "deployment_inactive_verified")
        return self._perform(verify)

    def service_inactive_verified(self, service: str) -> SystemEvidence:
        return self.inactive_verified(service)

    def inactive_verified(self, service: str) -> SystemEvidence:
        return self._verify_service(service, stop_requested=False)

    def _verify_service(self, service, *, stop_requested):
        if type(service) is not str or service not in self.stop_services:
            return SystemEvidence(False, "service_not_allowed")

        def stop(deadline):
            model_pids = self._model_pids(deadline)
            self.read_file(CGROUP / "cgroup.controllers", 8192)
            before = self._service(service, deadline)
            if not self._no_job(before["Job"]) or before["ActiveState"] not in ("active", "inactive", "failed"):
                raise ValueError
            if not stop_requested and before["ActiveState"] != "inactive":
                raise ValueError
            if before["ControlGroup"]:
                group = self._cgroup(before["ControlGroup"])
            elif before["ActiveState"] == "inactive" and before["Slice"] == "system.slice":
                group = self._cgroup("/system.slice/" + service)
            else:
                raise ValueError
            if stop_requested:
                self._command(["stop", service], deadline)
            after = self._service(service, deadline)
            if after["ActiveState"] != "inactive" or not self._no_job(after["Job"]):
                raise ValueError
            self._empty_tree(group, deadline)
            if after["ControlGroup"]:
                self._empty_tree(self._cgroup(after["ControlGroup"]), deadline)
            final = self._service(service, deadline)
            if final != after or self._model_pids(deadline) != model_pids:
                raise ValueError
            return SystemEvidence(True, "stopped_verified" if stop_requested else "inactive_verified")
        return self._perform(stop)

    def verify_sleep_idle(self) -> SystemEvidence:
        def verify(deadline):
            self._sleep_idle(deadline)
            return SystemEvidence(True, "sleep_idle_verified")
        return self._perform(verify)

    def suspend_verified(self) -> SystemEvidence:
        def suspend(deadline):
            model_pids = self._model_pids(deadline)
            self._sleep_idle(deadline)
            before = self._counter()
            self._command(["start", "suspend.target"], deadline)
            after = self._counter()
            if after != before + 1:
                return SystemEvidence(False, "unknown", before, after)
            self._wait_sleep_idle(deadline)
            after = self._counter()
            if after != before + 1 or self._model_pids(deadline) != model_pids:
                return SystemEvidence(False, "unknown", before, after)
            return SystemEvidence(True, "resumed_verified", before, after)
        return self._perform(suspend)
