#!/usr/bin/env python3
"""Root automatic suspend runner. Uncertainty retains the sleep fence.

Two reviewed session profiles share this controller. A/B run the X11 kiosk
profile (`radxa`, `DISPLAY=:0`, `xset dpms`, kiosk launcher tree). C-F run the
GNOME/Wayland profile: compositor idle observation, screen-shield blank/unblank
and the mode supervisor's browser group as the fence anchor. The installed
`/etc/sage-lantern/session-profile` record selects the profile explicitly; the
stop set follows it, because a unit that is not installed cannot be stopped.
"""

import argparse
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import stat
import subprocess
import threading
import time
import uuid

from sage_lantern.idle_suspend import IdleSuspendPolicy
from sage_lantern.inference_guard import pending_count
from sage_lantern.power_settings import validate_policy
from sage_lantern.suspend_session import (
    WAYLAND_PROFILE, GUARDED_WAYLAND_PROFILE, X11_PROFILE, ConsoleFence, GnomeShield,
    NativePinShield, MutterIdle, installed_profile,
    load_wayland_binding,
)
from sage_lantern.suspend_io import FENCE, STATUS, model_idle, public_json, publish, read_json
from sage_lantern.suspend_journal import SuspendJournal
from sage_lantern.suspend_kiosk import KioskFence, LinuxProcesses, RETAINED_ARGV, load_kiosk_binding
from sage_lantern.suspend_runtime import SleepCycle
from sage_lantern.suspend_system import (
    AUTH, CAMERA, DISPLAY, LLM, MODEL_UNITS, VLM, VOICE, WAYLAND_STOP_SERVICES, SuspendSystem,
)


UI = "sage-lantern-fleet-admin.service"
MODEL = LLM
# One reviewed start baseline per resident model unit (D-016: NPU text on 8081,
# NPU vision on 8082); the profile and its participation rule live in
# sage_lantern.suspend_system and are never re-derived here.
MODEL_DIRECTORY = Path("/run/sage-lantern-model")
MODEL_SLOTS = {LLM: (8081, "/slots"), VLM: (8082, "/slots")}
INFERENCE_DIRECTORY = Path("/var/lib/sage-lantern/inference")
AUTH_REPORTS = Path("/var/lib/sage-lantern/device-lock/drain")
VOICE_INSTANCE = Path("/run/sage-lantern-voice/instance.json")
VOICE_RECEIPT = Path("/run/sage-lantern-voice/drain-receipt.json")
VOICE_REQUEST = Path("/run/sage-lantern-voice-drain-request.json")
BOOT = Path("/proc/sys/kernel/random/boot_id")
AUTHORIZATION = Path("/run/sage-lantern-suspend/authorization.json")
SLEEP_GUARD = Path("/etc/systemd/system/systemd-suspend.service.d/90-sage-lantern.conf")
SLEEP_GUARD_TEXT = "[Service]\nExecCondition=/opt/sage-lantern/venv/bin/python /usr/local/libexec/sage-lantern-idle-suspend --sleep-guard\n"


def model_baseline_path(unit):
    return MODEL_DIRECTORY / (unit + ".json")


def model_started(unit):
    """Record the exact model instance systemd started, for the sleep contract."""
    if os.geteuid() != 0 or unit not in MODEL_UNITS:
        return 1
    value = command(["/usr/bin/systemctl", "show", unit, "--property=MainPID,InvocationID,ActiveState"])
    fields = dict(line.split("=", 1) for line in value.splitlines())
    if (fields.get("ActiveState") != "activating"
            or re.fullmatch(r"[1-9][0-9]{0,9}", fields.get("MainPID", "")) is None
            or re.fullmatch(r"[0-9a-f]{32}", fields.get("InvocationID", "")) is None):
        return 1
    pid = int(fields["MainPID"])
    publish(model_baseline_path(unit), dict(version=1, pid=pid, invocation_id=fields["InvocationID"],
              start_ticks=Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19],
              boot_id=BOOT.read_text().strip()))
    return 0


def model_baseline(unit):
    value = read_json(model_baseline_path(unit), owner_uid=0)
    pid, invocation = service_identity(unit)
    return (type(value) is dict and set(value) == {"version", "pid", "invocation_id", "start_ticks", "boot_id"}
            and type(value["version"]) is int and value["version"] == 1
            and type(value["pid"]) is int and value["pid"] == pid and value["invocation_id"] == invocation
            and value["boot_id"] == BOOT.read_text().strip()
            and value["start_ticks"] == Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19])


def resident_models():
    """Resident model units of this installation's profile; never a partial set.

    The chat lane is always resident. The reviewed activation lane enables the
    vision unit, and from then on it must be active with a matching start
    baseline. An unconfirmed observation yields no units, which refuses sleep.
    """
    evidence = SuspendSystem().model_units()
    return evidence.units if evidence.confirmed is True else ()


def voice_lane():
    """The voice lane is part of this installation's profile only once enabled.

    A shipped worker that was never enabled is not part of the profile, exactly
    like the second model unit: a missing optional component must not make the
    appliance unusable or unsleepable. An enabled lane joins the full drain
    contract, and an enabled unit that is not active is unknown evidence.
    """
    evidence = SuspendSystem().voice_lane()
    return evidence.units if evidence.confirmed is True else ()


def model_baseline_ready():
    units = resident_models()
    return bool(units) and all(model_baseline(unit) for unit in units)


def models_idle():
    """Every resident model server reports an idle slot (8081 text, 8082 vision)."""
    units = resident_models()
    if not units:
        return False
    for unit in units:
        port, path = MODEL_SLOTS[unit]
        if not model_idle(public_json(port, path)):
            return False
    return True


def inference_ready():
    if not model_baseline_ready() or pending_count(INFERENCE_DIRECTORY, owner_uid=pwd.getpwnam("sage-lantern").pw_uid) != 0:
        return False
    if not voice_lane():
        return True
    environment = command(["/usr/bin/systemctl", "show", VOICE, "--property=Environment", "--value"])
    return "SAGE_LANTERN_INFERENCE_GUARD=" + str(INFERENCE_DIRECTORY) in shlex.split(environment)


def guard_loaded():
    metadata = SLEEP_GUARD.lstat()
    if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o644 or SLEEP_GUARD.resolve() != SLEEP_GUARD
            or metadata.st_size != len(SLEEP_GUARD_TEXT) or SLEEP_GUARD.read_text() != SLEEP_GUARD_TEXT):
        return False
    fields = dict(line.split("=", 1) for line in command(["/usr/bin/systemctl", "show",
                  "systemd-suspend.service", "--property=DropInPaths,NeedDaemonReload"]).splitlines())
    if fields.get("NeedDaemonReload") != "no" or str(SLEEP_GUARD) not in fields.get("DropInPaths", "").split():
        return False
    value = json.loads(command(["/usr/bin/busctl", "--json=short", "get-property", "org.freedesktop.systemd1",
                 "/org/freedesktop/systemd1/unit/systemd_2dsuspend_2eservice", "org.freedesktop.systemd1.Service", "ExecCondition"]))
    if type(value) is not dict or value.get("type") != "a(sasbttttuii)" or type(value.get("data")) is not list or len(value["data"]) != 1:
        return False
    condition = value["data"][0]
    return (type(condition) is list and len(condition) == 10
            and condition[0] == "/opt/sage-lantern/venv/bin/python"
            and condition[1] == ["/opt/sage-lantern/venv/bin/python", "/usr/local/libexec/sage-lantern-idle-suspend", "--sleep-guard"]
            and condition[2] is False)


def drain_hooks_ready():
    readiness = public_json(3196, "/sleep-ready", limit=256)
    if type(readiness) is not dict or set(readiness) != {"ready"} or readiness["ready"] is not True:
        return False
    owner = pwd.getpwnam("sage-lantern").pw_uid
    _, invocation = service_identity(AUTH)
    try:
        # A missing current-instance receipt (crash between start and the first
        # publish) must refuse like every other unmet condition, not traceback.
        report = read_json(AUTH_REPORTS / (invocation + ".json"), owner_uid=owner, limit=512)
    except Exception:
        return False
    if not prior_auth_drained(invocation, owner):
        return False
    if not voice_lane():
        return True
    voice_pid, voice_invocation = service_identity(VOICE)
    voice = read_json(VOICE_INSTANCE, owner_uid=owner)
    return (type(report) is dict and type(report.get("version")) is int and report["version"] == 1
            and report.get("instance_id") == invocation and report.get("closing") is False
            and type(voice) is dict and type(voice.get("pid")) is int and voice["pid"] == voice_pid
            and voice.get("invocation_id") == voice_invocation and voice.get("boot_id") == BOOT.read_text().strip())


def auth_receipt_confirmed(value, invocation):
    return (type(value) is dict
            and set(value) == {"version", "instance_id", "closing", "drained", "local_revoked", "upstream_cleanup"}
            and type(value["version"]) is int and value["version"] == 1
            and value["instance_id"] == invocation
            and all(value[key] is True for key in ("closing", "drained", "local_revoked"))
            and value["upstream_cleanup"] == "confirmed")


def prior_auth_drained(current, owner):
    directory = AUTH_REPORTS.lstat()
    if (not stat.S_ISDIR(directory.st_mode) or directory.st_uid != owner
            or stat.S_IMODE(directory.st_mode) != 0o700 or AUTH_REPORTS.resolve() != AUTH_REPORTS):
        return False
    count = 0
    for path in AUTH_REPORTS.iterdir():
        count += 1
        if count > 1024 or re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None:
            return False
        if path.name == current + ".json":
            continue
        if not auth_receipt_confirmed(read_json(path, owner_uid=owner, limit=512), path.stem):
            return False
    return True


def sleep_guard():
    try:
        if Path("/run/sage-lantern-deployment.pending").exists():
            return False
        permit = read_json(AUTHORIZATION, owner_uid=0)
        checkpoint = read_json(AUTHORIZATION.parent / "checkpoint.json", owner_uid=0)
        if (type(permit) is not dict or set(permit) != {"pid", "start_ticks", "boot_id", "transition_id", "success_counter"}
                or type(permit["pid"]) is not int or permit["pid"] <= 1
                or permit["boot_id"] != BOOT.read_text().strip()
                or type(permit["success_counter"]) is not int
                or permit["success_counter"] != int(Path("/sys/power/suspend_stats/success").read_text())
                or checkpoint.get("phase") != "sleep_requested"
                or checkpoint.get("transition_id") != permit["transition_id"]
                or read_json(FENCE, owner_uid=0) != {"transition_id": permit["transition_id"]}):
            return False
        process = Path("/proc") / str(permit["pid"])
        fields = process.joinpath("stat").read_text().rsplit(")", 1)[1].split()
        return process.stat().st_uid == 0 and fields[0] not in {"Z", "X"} and fields[19] == permit["start_ticks"]
    except Exception:
        return False


def command(arguments, timeout=5):
    result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, timeout=timeout, check=True,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "SYSTEMD_PAGER": "cat"})
    if len(result.stdout) > 32768:
        raise ValueError("command_output_limit")
    return result.stdout.decode().strip()


def service_identity(service):
    value = command(["/usr/bin/systemctl", "show", service, "--property=MainPID,InvocationID,ActiveState,Job"])
    fields = dict(line.split("=", 1) for line in value.splitlines())
    if (set(fields) != {"MainPID", "InvocationID", "ActiveState", "Job"}
            or fields["ActiveState"] != "active" or fields["Job"]
            or re.fullmatch(r"[1-9][0-9]{0,9}", fields["MainPID"]) is None
            or re.fullmatch(r"[0-9a-f]{32}", fields["InvocationID"]) is None):
        raise ValueError("service_instance_unavailable")
    return int(fields["MainPID"]), fields["InvocationID"]


class X11Session:
    """The reviewed A/B profile: radxa's X11 kiosk, xset and the launcher tree."""

    name = X11_PROFILE
    display_outcome = "x11_dpms"

    def idle_seconds(self):
        raw = command(["/usr/sbin/runuser", "-u", "radxa", "--", "/usr/bin/env", "DISPLAY=:0",
                       "XAUTHORITY=/run/user/1000/gdm/Xauthority", "/usr/bin/python3",
                       "/usr/local/libexec/sage-lantern-display-power", "--query-idle"], timeout=3)
        if re.fullmatch(r"[0-9]{1,10}", raw) is None or int(raw) > 4294967295:
            raise ValueError("idle_unavailable")
        return int(raw) / 1000

    def display_power(self, action):
        if action not in {"on", "off"}:
            raise ValueError("invalid_display_action")
        command(["/usr/sbin/runuser", "-u", "radxa", "--", "/usr/bin/env", "DISPLAY=:0",
                 "XAUTHORITY=/run/user/1000/gdm/Xauthority", "/usr/bin/xset", "dpms", "force", action],
                timeout=3)

    def fence(self):
        binding = load_kiosk_binding()
        backend = LinuxProcesses()
        backend.validate_host()
        table = backend.snapshot()
        matches = [process for process in table.values()
                   if process.uid == binding.uid and process.executable == "/usr/bin/bash"
                   and process.argv in binding.launcher_argv]
        if len(matches) != 1:
            raise ValueError("kiosk_launcher_ambiguous")
        launcher = matches[0]
        retained = tuple(process for process in table.values() if process.parent == launcher.pid
                         and process.uid == binding.uid and process.argv == RETAINED_ARGV.get(process.executable))
        kiosk = KioskFence(launcher_pid=launcher.pid, launcher_start_ticks=launcher.start_ticks,
                           auth_locked=locked, backend=backend, timeout_seconds=5, retained_session=retained,
                           binding=binding)
        if kiosk.preflight() is not True:
            kiosk.close()
            raise ValueError("kiosk_inventory_unverified")
        return kiosk

    def preflight(self):
        self.idle_seconds()


class WaylandSession:
    """The C-F profile: compositor idle observation and the supervisor's browser group."""

    name = WAYLAND_PROFILE

    def __init__(self, binding, idle, display):
        self.binding = binding
        self.idle = idle
        self.display = display
        self.display_outcome = "not_attempted"

    def idle_seconds(self):
        return self.idle.seconds()

    def display_power(self, action):
        if action not in {"on", "off"}:
            raise ValueError("invalid_display_action")
        if action == "off":
            self.display_outcome = self.display.off()
        else:
            self.display.on()
            self.display_outcome = self.display.outcome

    def fence(self):
        options = {'require_native_pin': True} if self.name == GUARDED_WAYLAND_PROFILE else {}
        fence = ConsoleFence(binding=self.binding, auth_locked=locked, **options)
        if fence.preflight() is not True:
            fence.close()
            raise ValueError("console_inventory_unverified")
        return fence

    def preflight(self):
        self.display.locks_kiosk()
        self.display.active()


SESSION = None


def session():
    """The installed session profile; never guessed from the running session.

    The instance is cached for the process, not rebuilt per sleep cycle, so the
    same shield object does serve several cycles. That is why GnomeShield proves
    ownership against the live session before it takes a blank down instead of
    trusting a boolean it remembered: a claim left standing by one cycle would
    otherwise be spent by a later one on a shield that cycle never raised.
    """
    global SESSION
    profile = installed_profile()
    if SESSION is None or SESSION.name != profile:
        if profile in (WAYLAND_PROFILE, GUARDED_WAYLAND_PROFILE):
            binding = load_wayland_binding()
            display = NativePinShield(binding) if profile == GUARDED_WAYLAND_PROFILE else GnomeShield(binding)
            SESSION = WaylandSession(binding, MutterIdle(binding), display)
            SESSION.name = profile
        else:
            SESSION = X11Session()
    return SESSION


def suspend_system():
    """Root observations for the installed profile's stop set."""
    if installed_profile() in (WAYLAND_PROFILE, GUARDED_WAYLAND_PROFILE):
        return SuspendSystem(stop_services=WAYLAND_STOP_SERVICES)
    return SuspendSystem()


def idle_seconds():
    return session().idle_seconds()


def display_power(action):
    session().display_power(action)


def power_policy():
    value = public_json(3196, "/power-policy", limit=512)
    if type(value) is not dict or type(value.get("deep_sleep_available")) is not bool:
        raise ValueError("policy_unavailable")
    return validate_policy({**value, "deep_sleep_available": False})


def locked():
    value = public_json(3196, "/state", limit=256)
    if type(value) is not dict or set(value) != {"locked"} or type(value["locked"]) is not bool:
        raise ValueError("lock_state_unavailable")
    return value["locked"]


def capability(ready):
    publish(STATUS, dict(version=1, available=ready is True, boot_id=BOOT.read_text().strip(),
                         updated_boottime=time.clock_gettime(time.CLOCK_BOOTTIME)), mode=0o644)


def kiosk_fence():
    return session().fence()


class Appliance:
    def __init__(self, system, policy, transition_id, kiosk, cancelled=lambda: False, test_wake_after=None,
                 auxiliary=(CAMERA, UI, DISPLAY)):
        self.system = system
        self.policy = policy
        self.transition_id = transition_id
        self.kiosk = kiosk
        self.cancelled = cancelled
        self.test_wake_after = test_wake_after
        self.auxiliary = auxiliary
        self.wake_alarm = None
        self.owner = pwd.getpwnam("sage-lantern").pw_uid
        self.auth_pid, self.auth_instance = service_identity(AUTH)
        self.voice = bool(voice_lane())
        self.voice_pid = self.voice_invocation = self.voice_identity = None
        if self.voice:
            self.voice_pid, self.voice_invocation = service_identity(VOICE)
            self.voice_identity = read_json(VOICE_INSTANCE, owner_uid=self.owner)
            identity = self.voice_identity
            if (type(identity) is not dict
                    or set(identity) != {"instance_id", "pid", "start_ticks", "boot_id", "invocation_id"}
                    or type(identity["pid"]) is not int or identity["pid"] != self.voice_pid
                    or identity["invocation_id"] != self.voice_invocation
                    or identity["boot_id"] != BOOT.read_text().strip()
                    or type(identity["instance_id"]) is not str
                    or str(uuid.UUID(identity["instance_id"])) != identity["instance_id"]):
                raise ValueError("voice_instance_unavailable")
            ticks = Path(f"/proc/{self.voice_pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
            if identity["start_ticks"] != ticks:
                raise ValueError("voice_instance_changed")
        self.initial_idle = idle_seconds()
        self.sampled_at = time.monotonic()
        self.submitted = False
        self.sleep_proof = None
        self.stopped_auth = False
        self.drained = False
        self.stopped = set()
        publish(AUTHORIZATION.parent / "context.json", dict(transition_id=transition_id,
                auth_pid=self.auth_pid, auth_instance=self.auth_instance, voice_identity=self.voice_identity,
                policy=policy))

    def fenced(self):
        return FENCE.exists() or FENCE.is_symlink()

    def retain_fence(self):
        if self.fenced():
            return read_json(FENCE, owner_uid=0) == {"transition_id": self.transition_id}
        publish(FENCE, {"transition_id": self.transition_id})
        capability(False)
        return True

    def stop_auth(self):
        if not self.stopped_auth:
            if service_identity(AUTH) != (self.auth_pid, self.auth_instance):
                return False
            self.stopped_auth = self.system.stop_verified(AUTH).confirmed is True
        return self.stopped_auth

    def auth_inactive(self):
        return self.stopped_auth and self.system.inactive_verified(AUTH).confirmed is True

    def auth_drained(self):
        value = read_json(AUTH_REPORTS / (self.auth_instance + ".json"), owner_uid=self.owner, limit=512)
        return auth_receipt_confirmed(value, self.auth_instance) and prior_auth_drained(self.auth_instance, self.owner)

    def voice_drained(self):
        if not self.voice:
            return True
        value = read_json(VOICE_RECEIPT, owner_uid=self.owner)
        checks = {"http_drained", "http_outcome_known", "session_finalized", "child_groups_absent",
                  "memory_drained", "community_drained", "history_outcome_known", "deadline_met"}
        return (type(value) is dict and value.get("schema") == "sage.lantern.voice-drain.v1"
                and value.get("transition_id") == self.transition_id
                and all(type(value.get(key)) is type(member) and value[key] == member
                        for key, member in self.voice_identity.items())
                and value.get("drained") is True and value.get("reconcile_required") is False
                and type(value.get("checks")) is dict and set(value["checks"]) == checks
                and all(value["checks"][key] is True for key in checks))

    def stop(self, service):
        if service not in self.stopped:
            if self.system.stop_verified(service).confirmed is not True:
                return False
            self.stopped.add(service)
        return self.system.inactive_verified(service).confirmed is True

    def quiesce(self):
        def refuse(step):
            print("quiesce_refused=" + step, flush=True)
            return False

        if not self.auth_inactive() or not self.auth_drained():
            return refuse("auth")
        from sage_lantern.mesh_voice_ipc import managed as mesh_managed, root_control as mesh_control
        if self.voice:
            if mesh_managed() and VOICE not in self.stopped:
                mesh_control("drain", self.transition_id)
                self.stopped.add(VOICE)
            if VOICE not in self.stopped:
                if service_identity(VOICE) != (self.voice_pid, self.voice_invocation):
                    return refuse("voice_instance_changed")
                publish(VOICE_REQUEST, {**self.voice_identity, "schema": "sage.lantern.voice-drain-request.v1",
                                        "transition_id": self.transition_id}, mode=0o644)
            if not self.stop(VOICE) or not self.voice_drained():
                return refuse("voice_drain")
        if not all(self.stop(service) for service in self.auxiliary):
            return refuse("auxiliary")
        if self.kiosk.quiesce() is not True:
            return refuse("kiosk:" + str(getattr(self.kiosk, "refusal", "unknown")))
        if not inference_ready():
            return refuse("inference")
        deadline = time.monotonic() + 30
        while not models_idle():
            if time.monotonic() >= deadline:
                return refuse("models_busy")
            time.sleep(0.25)
        self.drained = True
        return True

    def idle_eligible(self):
        if getattr(self, "cancelled", lambda: False)():
            return False
        current = idle_seconds()
        expected = self.initial_idle + time.monotonic() - self.sampled_at
        # The X11 idle counter is a millisecond clock; the compositor's is
        # coarser and can lag wall time by seconds (read on C, 30 Sep 2026), so
        # the growth comparison allows that lag while still refusing a counter
        # that went backwards (input).
        return current >= self.policy["deep_sleep_seconds"] and current >= expected - 5.0

    def sleep(self):
        if not self.idle_eligible():
            return False
        if getattr(self, "test_wake_after", None) is not None:
            alarm = Path("/sys/class/rtc/rtc0/wakealarm")
            if alarm.read_text().strip():
                return False
            command(["/sbin/rtcwake", "-d", "/dev/rtc0", "-m", "no", "-s", str(self.test_wake_after)])
            self.wake_alarm = alarm.read_text().strip()
            if re.fullmatch(r"[1-9][0-9]{0,11}", self.wake_alarm) is None:
                return False
        publish(AUTHORIZATION, dict(pid=os.getpid(), boot_id=BOOT.read_text().strip(),
                                   start_ticks=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
                                   transition_id=self.transition_id,
                                   success_counter=int(Path("/sys/power/suspend_stats/success").read_text())))
        self.submitted = True
        try:
            if not self.idle_eligible():
                self.submitted = False
                return False
            # The panel blank belongs next to the suspend, after every idle
            # observation: read on C (30 Sep 2026) the shield perturbs the
            # compositor's idle evidence, so it must never sit between an
            # observation and the decision that depends on it.
            display_power("off")
            print("display_off=" + str(session().display_outcome), flush=True)
            self.sleep_proof = self.system.suspend_verified()
        finally:
            AUTHORIZATION.unlink(missing_ok=True)
        return self.sleep_proof.confirmed is True

    def resumed(self):
        return (self.sleep_proof is not None and self.sleep_proof.confirmed is True
                and self.system.verify_sleep_idle().confirmed is True)

    def aborted(self):
        return not self.submitted and self.system.verify_sleep_idle().confirmed is True

    def release_fence(self):
        if not self.drained or not (self.resumed() or self.aborted()):
            return False
        if read_json(FENCE, owner_uid=0) != {"transition_id": self.transition_id}:
            return False
        if getattr(self, "wake_alarm", None) is not None:
            current_alarm = Path("/sys/class/rtc/rtc0/wakealarm").read_text().strip()
            if current_alarm == self.wake_alarm:
                command(["/sbin/rtcwake", "-d", "/dev/rtc0", "-m", "disable"])
        FENCE.unlink()
        descriptor = os.open("/run", os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return True

    def start_locked(self):
        command(["/usr/bin/systemctl", "start", AUTH], timeout=10)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                if locked():
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            return False
        from sage_lantern.mesh_voice_ipc import managed as mesh_managed, root_control as mesh_control
        if self.voice and mesh_managed():
            mesh_control("resume", self.transition_id)
            command(["/usr/bin/systemctl", "start", *self.auxiliary], timeout=30)
        elif self.voice:
            command(["/usr/bin/systemctl", "start", VOICE, *self.auxiliary], timeout=30)
        else:
            command(["/usr/bin/systemctl", "start", *self.auxiliary], timeout=30)
        if self.kiosk.resume() is not True:
            return False
        # The panel comes back last, and what it came back to is recorded. A
        # shield this cycle did not create is preserved rather than dismissed,
        # so the console may still be behind an operator login: that is a real
        # reported state, not a successful unblank, and the journal has to say
        # which one happened.
        display_power("on")
        print("display_on=" + str(session().display_outcome), flush=True)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--sleep-guard", action="store_true")
    parser.add_argument("--model-started", metavar="UNIT")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--test-wake-after", type=int, choices=(60,))
    args = parser.parse_args()
    if args.test_wake_after is not None and not args.once:
        parser.error("timed test wake requires --once")
    if args.sleep_guard:
        return 0 if sleep_guard() else 1
    if args.model_started is not None:
        return model_started(args.model_started)
    if Path("/run/sage-lantern-deployment.pending").exists():
        raise SystemExit("Deployment recovery is pending")
    if os.geteuid() != 0 or "[deep]" not in Path("/sys/power/mem_sleep").read_text():
        raise SystemExit("Deep suspend prerequisites unavailable")
    system = suspend_system()
    if system.verify_stop_guards().confirmed is not True or not guard_loaded():
        raise SystemExit("Sleep service guards unavailable")
    if args.check:
        if not drain_hooks_ready() or not inference_ready():
            raise SystemExit("Sleep drain hooks unavailable")
        power_policy()
        session().preflight()
        idle_seconds()
        kiosk_fence().close()
        print("Sleep guards, drain hooks and idle observation verified; no suspend requested")
        return 0
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda received, frame: stopped.set())
    eligibility = IdleSuspendPolicy()
    try:
        with SuspendJournal() as journal:
            if journal.load().needs_recovery or FENCE.exists() or FENCE.is_symlink():
                raise RuntimeError("sleep_recovery_required")
            while not stopped.is_set():
                try:
                    policy = power_policy()
                    current_idle = idle_seconds()
                    current_locked = locked()
                    if (system.verify_stop_guards().confirmed is not True
                            or system.verify_sleep_idle().confirmed is not True
                            or not guard_loaded() or not drain_hooks_ready() or not inference_ready()):
                        raise RuntimeError("sleep_guards_unavailable")
                    eligibility.configure(delay_seconds=policy["deep_sleep_seconds"], revision=policy["revision"])
                    capability(True)
                    if eligibility.tick(now=time.monotonic(), idle_seconds=current_idle, locked=current_locked):
                        if policy != power_policy() or not locked() or not models_idle():
                            continue
                        kiosk = kiosk_fence()
                        try:
                            transition_id = str(uuid.uuid4())
                            appliance = Appliance(system, policy, transition_id, kiosk, cancelled=stopped.is_set,
                                                  test_wake_after=args.test_wake_after,
                                                  auxiliary=system.auxiliary_services())
                            result = SleepCycle(journal, appliance, transition_id).run()
                            print("sleep_cycle=" + result.phase.value, flush=True)
                            if args.once:
                                return 0
                        finally:
                            kiosk.close()
                except Exception as error:
                    capability(False)
                    eligibility.tick()
                    if journal.load().needs_recovery or FENCE.exists() or FENCE.is_symlink():
                        print("sleep_recovery_required; " + type(error).__name__ + ":" + str(error)[:120]
                              + "; admission remains fenced", flush=True)
                        return 1
                    print("sleep_deferred; " + type(error).__name__ + ":" + str(error)[:120], flush=True)
                stopped.wait(5)
    finally:
        capability(False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
