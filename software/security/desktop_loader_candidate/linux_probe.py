"""Disabled Linux probe implementation for lifecycle settlement.

Reads the exact resources recorded for an attempt from procfs and sysfs. It
does not adopt a PID, signal a process, kill a child, write to the unit or
activate the desktop authority. The default instance is not wired to a
service.

`pidfd_alive` is injectable for tests. The production path uses
`pidfd_send_signal(fd, 0, ...)`, which checks liveness without delivering a
signal. The probe returns a proof only when every resource is terminal.
"""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import sys

_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def _load_native_probe():
    name = "sage_lantern_native_probe"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("native_probe.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


N = _load_native_probe()
NativeProbe = N.NativeProbe
ProbeResult = N.ProbeResult


def _load_lifecycle():
    name = "sage_lantern_lifecycle_settlement"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).with_name("lifecycle_settlement.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


L = _load_lifecycle()


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


def _read(root, path):
    try:
        return (Path(root) / path).read_text(
            encoding="utf-8", errors="replace")
    except OSError:
        return None


def _pidfd_alive(fd):
    """Return True, False or None for a pidfd without sending a signal."""
    if sys.platform != "linux":
        return None
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.syscall(
            ctypes.c_long(424),  # pidfd_send_signal on the tested kernels
            ctypes.c_int(fd), ctypes.c_int(0), ctypes.c_void_p(None),
            ctypes.c_ulong(0))
    except (OSError, AttributeError):
        return None
    if result == 0:
        return True
    return False if ctypes.get_errno() == 3 else None  # ESRCH


class LinuxProbe(NativeProbe):
    def __init__(self, root="/", owner="linux-probe", pidfd_alive=None):
        self.root = Path(root)
        self.owner = owner
        self.pidfd_alive = pidfd_alive or _pidfd_alive

    def _identity(self):
        machine = (_read(self.root, "etc/machine-id") or "").strip()
        boot = (_read(
            self.root, "proc/sys/kernel/random/boot_id") or "").strip()
        if not _MACHINE.fullmatch(machine) or not _UUID.fullmatch(boot):
            return None
        return machine, boot

    def _cgroup(self, path):
        raw = _read(self.root, path.lstrip("/") + "/cgroup.events")
        if raw is None:
            return "absent", "cgroup.events missing"
        match = re.search(r"^populated\s+([01])\s*$", raw, re.MULTILINE)
        if not match:
            return None, "cgroup.events malformed"
        return ("empty" if match.group(1) == "0" else "populated"), raw

    def _socket(self, path):
        path = path.removeprefix("unix:")
        raw = _read(self.root, "proc/net/unix")
        if raw is None:
            return None, "unix table missing"
        found = any(line.rstrip().endswith(" " + path) or
                    line.rstrip().endswith("\t" + path)
                    for line in raw.splitlines())
        return ("present" if found else "absent"), path

    def _mount(self, path):
        raw = _read(self.root, "proc/self/mountinfo")
        if raw is None:
            return None, "mountinfo missing"
        found = False
        for line in raw.splitlines():
            fields = line.split()
            if len(fields) > 4 and fields[4] == path:
                found = True
                break
        return ("present" if found else "unmounted"), path

    def _fd(self, fd):
        try:
            info = os.stat(self.root / f"proc/self/fd/{fd}")
        except OSError:
            return "closed", f"fd:{fd}"
        return "open", f"{info.st_dev}:{info.st_ino}"

    def _child(self, identity):
        match = re.fullmatch(r"child:([1-9][0-9]*):([0-9]+)", identity)
        if not match:
            return None, "child identity malformed"
        pid, start = match.group(1), int(match.group(2))
        raw = _read(self.root, f"proc/{pid}/stat")
        if raw is None:
            return "absent", identity
        try:
            fields = raw.rsplit(")", 1)[1].split()
            state, observed_start = fields[0], int(fields[19])
        except (IndexError, ValueError):
            return None, "child stat malformed"
        if observed_start != start:
            return "absent", identity  # The PID was reused by another process.
        if state == "Z":
            return "zombie", identity
        return "running", identity

    def _observe(self, resource):
        kind, identity = resource.kind, resource.identity
        if kind == "pidfd":
            match = re.fullmatch(r"pidfd:(\d+)", identity)
            if not match:
                return None, "pidfd identity malformed"
            alive = self.pidfd_alive(int(match.group(1)))
            if alive is None:
                return None, "pidfd state unknown"
            return ("alive" if alive else "dead"), identity
        if kind == "cgroup":
            return self._cgroup(identity)
        if kind == "socket":
            return self._socket(identity)
        if kind == "mount":
            return self._mount(identity)
        if kind == "fd":
            match = re.fullmatch(r"fd:(\d+)", identity)
            if not match:
                return None, "fd identity malformed"
            return self._fd(int(match.group(1)))
        if kind == "child":
            return self._child(identity)
        return None, f"{kind} probe unavailable"

    def observe(self, obligation):
        identity = self._identity()
        if identity is None:
            return ProbeResult(None, "platform_identity_unavailable")
        machine, boot = identity
        if machine != obligation.binding.machine_id:
            return ProbeResult(None, "platform_machine_mismatch")
        observations = []
        for resource in obligation.resources:
            state, evidence = self._observe(resource)
            if state is None:
                return ProbeResult(None, evidence or "resource_indeterminate")
            terminal = {
                "pidfd": {"dead"},
                "child": {"reaped", "absent"},
                "cgroup": {"absent", "empty"},
                "fd": {"closed"},
                "socket": {"absent", "closed"},
                "mount": {"absent", "unmounted"},
            }.get(resource.kind, set())
            if state not in terminal:
                return ProbeResult(None, f"{resource.kind}_not_terminal")
            observations.append(L.ResourceObservation(
                resource, state, _sha(evidence)))
        digest = hashlib.sha256(L.canonical({
            "binding": {
                "machine_id": obligation.binding.machine_id,
                "boot_id": obligation.binding.boot_id,
                "capture_id": obligation.binding.capture_id,
                "generation": obligation.binding.generation,
                "policy_sha256": obligation.binding.policy_sha256,
                "launch_id": obligation.binding.launch_id,
            },
            "resources": [
                {"kind": item.kind, "identity": item.identity}
                for item in obligation.resources
            ],
        })).hexdigest()
        unsigned = L.SettlementProof(
            digest, machine, boot, self.owner, tuple(observations), "0" * 64)
        proof = L.SettlementProof(
            digest, machine, boot, self.owner, tuple(observations),
            unsigned.expected_sha256())
        return ProbeResult(proof)
