"""Exact-process kiosk fence for a trusted root suspend controller.

The caller supplies a previously verified launcher PID AND /proc start ticks.
It must first stop/clean authentication, voice, UI, camera and display workers.
This helper never stops X, logs out, rewrites a live shell or reloads its code.
Its success proves the observed dedicated launcher tree is stopped/cleared,
not that every X surface is blank or arbitrary escaped processes are absent.
Legacy launchers need this controller on EVERY sleep cycle until replacement.
"""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import pwd
import re
import select
import shlex
import signal
import stat
import threading
import time


LAUNCHER = "/usr/local/bin/sage-lantern-kiosk"
FENCE = Path("/run/sage-lantern-sleep.fenced")
PROFILE = "--user-data-dir=/home/radxa/.config/sage-lantern-chromium"
BROWSERS = frozenset({"/usr/lib/chromium/chromium", "/usr/lib/chromium/chromium-bin",
                      "/usr/lib/chromium/chrome_crashpad_handler",
                      "/usr/lib/chromium/chromium-crashpad-handler"})
HELPERS = frozenset({"/usr/bin/sleep", "/usr/bin/curl", "/usr/bin/xrandr", "/usr/bin/sed",
                     "/usr/bin/pgrep", "/usr/bin/id", "/usr/bin/unlink", "/usr/bin/xset"})
RETAINED_EXECUTABLES = frozenset({"/usr/bin/ssh-agent", "/usr/bin/kwin_x11"})
RETAINED_ARGV = {
    "/usr/bin/ssh-agent": ("/usr/bin/ssh-agent", "/usr/local/bin/sage-lantern-session"),
    "/usr/bin/kwin_x11": ("kwin_x11", "--replace"),
}


@dataclass(frozen=True)
class KioskBinding:
    account: str
    uid: int
    unit_id: str | None

    def __post_init__(self):
        if (type(self.account) is not str or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", self.account)
                or self.account in {"root", "sage-lantern", "nobody"}
                or type(self.uid) is not int or self.uid <= 0
                or self.unit_id not in {None, "unit-a", "unit-b", "unit-c", "unit-d", "unit-e", "unit-f"}
                or (self.unit_id is None and (self.account, self.uid) != ("radxa", 1000))):
            raise ValueError("invalid kiosk binding")

    @classmethod
    def legacy(cls):
        """Explicit trusted-caller selection; never a missing-config fallback."""
        return cls("radxa", 1000, None)

    @property
    def profile(self):
        if self.unit_id is None:
            return PROFILE
        return f"--user-data-dir=/home/{self.account}/.config/sage-lantern/{self.unit_id}/chromium"

    @property
    def launcher_argv(self):
        suffix = () if self.unit_id is None else ("--profile-dir", self.profile.split("=", 1)[1])
        return tuple((shell, LAUNCHER, *suffix) for shell in ("bash", "/usr/bin/bash", "/bin/bash"))


def _read_binding_file(path):
    _trusted_file(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > 65536):
            raise RuntimeError("untrusted kiosk configuration")
        data = os.read(descriptor, 65537)
        if len(data) > 65536:
            raise RuntimeError("oversize kiosk configuration")
        return data.decode("utf-8")
    finally:
        os.close(descriptor)


def load_kiosk_binding():
    """Bind fresh launcher identity to root-controlled installed unit records."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate kiosk configuration key")
            result[key] = value
        return result

    root = Path("/etc/sage-lantern")
    plan = json.loads(_read_binding_file(root / "appliance-bindings.json"), object_pairs_hook=unique)
    receipt = json.loads(_read_binding_file(root / "appliance-installed.json"), object_pairs_hook=unique)
    if (type(plan) is not dict or plan.get("schema") != "sage.lantern.unit-plan.v1"
            or type(receipt) is not dict or receipt.get("schema") != "sage.lantern.fresh-app-install.v1"
            or receipt.get("installed") is not True or receipt.get("lock_required") is not True
            or plan.get("unit_id") not in {"unit-a", "unit-b", "unit-c", "unit-d", "unit-e", "unit-f"}
            or receipt.get("unit_id") != plan["unit_id"]):
        raise ValueError("installed kiosk binding mismatch")
    metadata = _read_binding_file(root / "unit.env")
    if metadata != f"UNIT_ID={plan['unit_id']}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n":
        raise ValueError("installed kiosk unit mismatch")
    account = plan.get("operator_account")
    KioskBinding(account, 1, plan["unit_id"])
    owner = pwd.getpwnam(account)
    if owner.pw_dir != f"/home/{account}":
        raise ValueError("kiosk home mismatch")
    return KioskBinding(account, owner.pw_uid, plan["unit_id"])


@dataclass(frozen=True)
class Process:
    pid: int
    parent: int
    start_ticks: int
    uid: int
    executable: str
    argv: tuple[str, ...]
    state: str
    pgid: int = 0


def _trusted_file(path):
    for component in (path, *path.parents):
        info = component.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError("untrusted executable path")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise RuntimeError("untrusted executable file")


class LinuxProcesses:
    """Linux-only backend; tests inject a process-table simulator instead."""

    def __init__(self, max_processes=4096):
        self.max_processes = max_processes

    def validate_host(self, executables=(LAUNCHER, "/usr/bin/bash")):
        if os.geteuid() != 0 or not hasattr(os, "pidfd_open") or not hasattr(signal, "pidfd_send_signal"):
            raise RuntimeError("root and Linux pidfds required")
        for executable in executables:
            _trusted_file(Path(executable))

    def fence_state(self):
        parent = FENCE.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid != 0 or parent.st_mode & 0o022:
            return "invalid"
        try:
            info = FENCE.lstat()
        except FileNotFoundError:
            return "absent"
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                or info.st_mode & 0o022 or info.st_size > 4096):
            return "invalid"
        return "present"

    def read(self, pid):
        root = Path("/proc") / str(pid)
        raw = (root / "stat").read_text()
        fields = raw[raw.rfind(")") + 2:].split()
        status = (root / "status").read_text()
        ids = next(line.split()[1:] for line in status.splitlines() if line.startswith("Uid:"))
        if len(set(ids)) != 1:
            uid = -1
        else:
            uid = int(ids[0])
        if fields[0] == "Z":
            executable, argv = "", ()
        else:
            executable = os.readlink(root / "exe")
            with (root / "cmdline").open("rb") as stream:
                data = stream.read(32769)
            if len(data) > 32768:
                raise RuntimeError("oversize process arguments")
            argv = tuple(part.decode("utf-8", errors="strict") for part in data.rstrip(b"\0").split(b"\0"))
        return Process(pid, int(fields[1]), int(fields[19]), uid, executable, argv, fields[0], int(fields[2]))

    def snapshot(self):
        result = {}
        count = 0
        for entry in Path("/proc").iterdir():
            if not entry.name.isdecimal():
                continue
            count += 1
            if count > self.max_processes:
                raise RuntimeError("process scan bound exceeded")
            try:
                process = self.read(int(entry.name))
                result[process.pid] = process
            except (FileNotFoundError, ProcessLookupError):
                continue
        return result

    def open(self, process):
        descriptor = os.pidfd_open(process.pid)
        try:
            current = self.read(process.pid)
            if (current.parent, current.start_ticks, current.uid, current.pgid,
                    current.executable, current.argv) != (
                    process.parent, process.start_ticks, process.uid, process.pgid,
                    process.executable, process.argv):
                raise RuntimeError("process changed while acquiring pidfd")
            _trusted_file(Path(process.executable))
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def send(self, handle, action):
        signal.pidfd_send_signal(handle, action)

    def exited(self, handle):
        poller = select.poll()
        poller.register(handle, select.POLLIN)
        return bool(poller.poll(0))

    def close(self, handle):
        os.close(handle)


def discover_launcher(*, launcher_pid, kiosk_uid=None, backend=None, binding=None):
    """Return one verified Process or raise; never searches by name or guesses PID.

    Pass its start_ticks to KioskFence, which revalidates before signalling.
    Do not log process tables or use a flattened argv to authorize a launcher.
    """
    binding = binding if binding is not None else load_kiosk_binding()
    kiosk_uid = binding.uid if kiosk_uid is None else kiosk_uid
    if (type(launcher_pid) is not int or launcher_pid <= 1 or type(kiosk_uid) is not int
            or kiosk_uid != binding.uid):
        raise ValueError("invalid launcher discovery arguments")
    processes = backend if backend is not None else LinuxProcesses()
    processes.validate_host()
    process = processes.read(launcher_pid)
    if (process.pid != launcher_pid or process.uid != kiosk_uid
            or process.executable != "/usr/bin/bash" or process.start_ticks <= 0
            or process.state == "Z" or process.argv not in binding.launcher_argv):
        raise RuntimeError("launcher identity mismatch")
    handle = processes.open(process)
    try:
        if processes.exited(handle):
            raise RuntimeError("launcher exited during discovery")
        return process
    finally:
        processes.close(handle)


class KioskFence:
    def __init__(self, *, launcher_pid, launcher_start_ticks, auth_locked,
                 kiosk_uid=None, backend=None, max_descendants=128, scan_rounds=6,
                 timeout_seconds=2.0, retained_session=(), binding=None):
        self.binding = binding if binding is not None else load_kiosk_binding()
        kiosk_uid = self.binding.uid if kiosk_uid is None else kiosk_uid
        if (type(launcher_pid) is not int or launcher_pid <= 1
                or type(launcher_start_ticks) is not int or launcher_start_ticks <= 0
                or type(kiosk_uid) is not int or kiosk_uid != self.binding.uid or not callable(auth_locked)
                or type(max_descendants) is not int or not 1 <= max_descendants <= 256
                or type(scan_rounds) is not int or not 2 <= scan_rounds <= 12
                or type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 5):
            raise ValueError("invalid kiosk fence configuration")
        if type(retained_session) is not tuple or len(retained_session) > 2:
            raise ValueError("invalid retained session pins")
        for process in retained_session:
            if (not isinstance(process, Process) or type(process.pid) is not int or process.pid <= 1
                    or type(process.start_ticks) is not int or process.start_ticks <= 0
                    or process.parent != launcher_pid or process.uid != kiosk_uid
                    or process.executable not in RETAINED_EXECUTABLES
                    or process.argv != RETAINED_ARGV.get(process.executable) or process.state == "Z"
                    or type(process.argv) is not tuple or not process.argv
                    or any(type(arg) is not str or "\0" in arg for arg in process.argv)
                    or sum(len(arg) for arg in process.argv) > 32768
                    or process.argv[0] not in (process.executable, Path(process.executable).name)):
                raise ValueError("invalid retained session pin")
        if (len({process.pid for process in retained_session}) != len(retained_session)
                or len({process.executable for process in retained_session}) != len(retained_session)):
            raise ValueError("duplicate retained session pins")
        self.retained_session = retained_session
        self.pid = launcher_pid
        self.start_ticks = launcher_start_ticks
        self.uid = kiosk_uid
        self.auth_locked = auth_locked
        self.backend = backend if backend is not None else LinuxProcesses()
        self.max_descendants = max_descendants
        self.scan_rounds = scan_rounds
        self.timeout = timeout_seconds
        self._launcher = None
        self._children = {}
        self._cleared = False
        self._deadline = None
        self._lock = threading.Lock()

    def _launcher_matches(self, process):
        return (process is not None and process.pid == self.pid and process.start_ticks == self.start_ticks
                and process.uid == self.uid and process.executable == "/usr/bin/bash"
                and process.argv in self.binding.launcher_argv)

    def _allowed(self, process, table=None, kiosk=None):
        if process.uid != self.uid:
            return False
        if process.executable in BROWSERS:
            arguments = shlex.split(process.argv[0]) if len(process.argv) == 1 else process.argv
            profiles = [argument for argument in arguments if argument.startswith("--user-data-dir")]
            if profiles and profiles != [self.binding.profile]:
                return False
            if process.executable in {"/usr/lib/chromium/chromium", "/usr/lib/chromium/chromium-bin"}:
                return profiles == [self.binding.profile] or any(argument.startswith("--type=") for argument in arguments)
            return True
        if process.executable in HELPERS:
            return True
        if process.executable == "/usr/bin/cat":
            if table is None or kiosk is None or process.argv not in (("cat",), ("/usr/bin/cat",), ("/bin/cat",)):
                return False
            ancestor = process.parent
            for _ in range(self.max_descendants):
                if ancestor not in kiosk:
                    return False
                parent = table[ancestor]
                arguments = shlex.split(parent.argv[0]) if len(parent.argv) == 1 else parent.argv
                if (parent.uid == self.uid and parent.executable in {
                        "/usr/lib/chromium/chromium", "/usr/lib/chromium/chromium-bin"}
                        and self.binding.profile in arguments and "--kiosk" in arguments):
                    return True
                ancestor = parent.parent
            return False
        return (process.executable in {"/usr/bin/bash", "/usr/bin/dash"}
                and len(process.argv) >= 2 and process.argv[1] == "/usr/bin/chromium") or (
                    process.executable == "/usr/bin/bash" and process.argv in self.binding.launcher_argv)

    def _wait(self, predicate):
        deadline = time.monotonic() + self.timeout
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return False

    def _stopped(self, pid, start_ticks):
        current = self.backend.snapshot().get(pid)
        return current is not None and current.start_ticks == start_ticks and current.state in {"T", "t", "Z"}

    def _descendants(self, table):
        descendants = set()
        for _ in range(self.max_descendants + 1):
            added = {pid for pid, process in table.items()
                     if process.parent in ({self.pid} | descendants)} - descendants
            if not added:
                return descendants
            descendants.update(added)
            if self.pid in descendants or len(descendants) > self.max_descendants:
                raise RuntimeError("invalid or oversized kiosk process tree")
        raise RuntimeError("kiosk process tree scan bound exceeded")

    def _inventory(self, table):
        launcher = table.get(self.pid)
        if not self._launcher_matches(launcher) or launcher.state == "Z":
            raise RuntimeError("launcher identity mismatch")
        descendants, retained = self._partition(table)
        inventory = {self.pid: launcher}
        for pid in descendants | retained:
            process = table[pid]
            if process.state != "Z":
                if pid not in retained and not self._allowed(process, table, descendants):
                    raise RuntimeError("unsupported kiosk descendant")
                inventory[pid] = process
        if any(process.state != "Z" and process.uid == self.uid and pid not in inventory
               and any(self.binding.profile in argument for argument in process.argv)
               for pid, process in table.items()):
            raise RuntimeError("kiosk profile process outside launcher tree")
        return inventory

    def _partition(self, table):
        descendants = self._descendants(table)
        retained = set()
        for pinned in self.retained_session:
            current = table.get(pinned.pid)
            if (current is None or current.state == "Z" or current.parent != self.pid
                    or (current.start_ticks, current.uid, current.executable, current.argv) != (
                        pinned.start_ticks, pinned.uid, pinned.executable, pinned.argv)):
                raise RuntimeError("retained session instance changed")
            retained.add(pinned.pid)
        for _ in range(self.max_descendants + 1):
            added = {pid for pid in descendants if table[pid].parent in retained} - retained
            if not added:
                break
            retained.update(added)
        for pid in retained:
            process = table[pid]
            if (process.uid != self.uid or process.executable in BROWSERS
                    or any(self.binding.profile in argument for argument in process.argv)):
                raise RuntimeError("kiosk process or foreign UID in retained session tree")
            if process.state != "Z":
                handle = self.backend.open(process)
                try:
                    if self.backend.exited(handle):
                        raise RuntimeError("retained session changed during verification")
                finally:
                    self.backend.close(handle)
        return descendants - retained, retained

    def preflight(self):
        """Signal-free, fence-independent native inventory check, exact bool.

        Validates the launcher, bounded descendants, executable trust via short-
        lived pidfds, and no observed profile owner outside the tree. Requires
        two matching inventories (ignoring scheduling state), not future safety:
        quiesce must still revalidate after the parent stops dependent workers.
        Does not call auth_locked or alter the existing sleep-cycle lease.
        """
        with self._lock:
            deadline = time.monotonic() + self.timeout
            try:
                self.backend.validate_host()
                for _ in range(self.scan_rounds):
                    if time.monotonic() >= deadline:
                        return False
                    inventory = self._inventory(self.backend.snapshot())
                    handles = []
                    try:
                        for process in inventory.values():
                            if time.monotonic() >= deadline:
                                return False
                            handles.append(self.backend.open(process))
                        following = self._inventory(self.backend.snapshot())
                        def identities(records):
                            return {pid: (process.parent, process.start_ticks, process.uid,
                                          process.executable, process.argv)
                                    for pid, process in records.items()}
                        if (identities(inventory) == identities(following)
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
        """Return exact True only after launcher STOP and descendant exit proof.

        Any failure returns False without resuming the launcher. Keep this object
        alive across the sleep cycle; do not replace its pidfd lease with a PID.
        """
        with self._lock:
            self._cleared = False
            self._deadline = time.monotonic() + self.timeout
            try:
                self.backend.validate_host()
                if self.backend.fence_state() != "present":
                    return False
                launcher = self.backend.snapshot().get(self.pid)
                if not self._launcher_matches(launcher):
                    return False
                self._inventory(self.backend.snapshot())
                if self._launcher is None:
                    self._launcher = self.backend.open(launcher)
                if self.backend.exited(self._launcher):
                    return False
                self.backend.send(self._launcher, signal.SIGSTOP)
                if not self._wait(lambda: self._stopped(self.pid, self.start_ticks)):
                    return False
                stable = False
                for _ in range(self.scan_rounds):
                    if time.monotonic() >= self._deadline:
                        return False
                    table = self.backend.snapshot()
                    descendants, retained = self._partition(table)
                    fresh = []
                    for pid in descendants:
                        if time.monotonic() >= self._deadline:
                            return False
                        process = table[pid]
                        if process.state == "Z":
                            continue
                        if not self._allowed(process, table, descendants):
                            return False
                        identity = (pid, process.start_ticks)
                        if identity not in self._children:
                            if len(self._children) >= self.max_descendants:
                                return False
                            handle = self.backend.open(process)
                            self._children[identity] = handle
                            self.backend.send(handle, signal.SIGSTOP)
                            fresh.append(identity)
                    for pid, ticks in fresh:
                        handle = self._children[(pid, ticks)]
                        if not self._wait(lambda: self.backend.exited(handle) or self._stopped(pid, ticks)):
                            return False
                    if not fresh:
                        stable = True
                        break
                if not stable:
                    return False
                table = self.backend.snapshot()
                descendants, retained = self._partition(table)
                if not self._launcher_matches(table.get(self.pid)):
                    return False
                for (pid, ticks), handle in self._children.items():
                    if self.backend.exited(handle):
                        continue
                    current = table.get(pid)
                    if (current is None or current.start_ticks != ticks or current.state not in {"T", "t"}
                            or pid not in descendants or not self._allowed(current, table, descendants)):
                        return False
                for handle in self._children.values():
                    if not self.backend.exited(handle):
                        self.backend.send(handle, signal.SIGKILL)
                if not self._wait(lambda: all(self.backend.exited(handle) for handle in self._children.values())):
                    return False
                table = self.backend.snapshot()
                descendants, retained = self._partition(table)
                if any(process.state != "Z" and (pid in descendants or
                       (process.uid == self.uid and any(self.binding.profile in argument for argument in process.argv)))
                       for pid, process in table.items()):
                    return False
                if (self.backend.fence_state() != "present" or self.backend.exited(self._launcher)
                        or not self._launcher_matches(table.get(self.pid))
                        or not self._stopped(self.pid, self.start_ticks)):
                    return False
                self._cleared = True
                return True
            except Exception:
                return False

    def resume(self):
        """After wake: auth must be locked and controller must remove the fence.

        The old Bash process resumes its old code. This does not deploy/reload
        launcher changes. No automatic SIGCONT on error or object destruction.
        """
        with self._lock:
            self._deadline = time.monotonic() + self.timeout
            try:
                if not self._cleared or self._launcher is None:
                    return False
                self.backend.validate_host()
                if self.auth_locked() is not True or self.backend.fence_state() != "absent":
                    return False
                if self.backend.exited(self._launcher) or not self._launcher_matches(self.backend.snapshot().get(self.pid)):
                    return False
                self._partition(self.backend.snapshot())
                self.backend.send(self._launcher, signal.SIGCONT)
                def running():
                    process = self.backend.snapshot().get(self.pid)
                    return self._launcher_matches(process) and process.state not in {"T", "t", "Z"}
                if not self._wait(running):
                    if not self.backend.exited(self._launcher):
                        self.backend.send(self._launcher, signal.SIGSTOP)
                    return False
                self._cleared = False
                for handle in self._children.values():
                    self.backend.close(handle)
                self._children.clear()
                return True
            except Exception:
                return False

    def close(self):
        """Release descriptors only; never resume a fenced launcher implicitly."""
        with self._lock:
            for handle in self._children.values():
                self.backend.close(handle)
            self._children.clear()
            if self._launcher is not None:
                self.backend.close(self._launcher)
                self._launcher = None
            self._cleared = False
