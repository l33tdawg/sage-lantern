"""Root-owned cooperating voice/vault lifecycle, inactive until explicitly wired.

The root dispatcher must construct this before starting voice, keep it alive
through auth stop and voice drain, and route every cooperating vault stop here.
It must never adopt an already running producer or automatically recover an old
journal. Independent root systemctl/kill operations are outside this contract.

Capture leases are process-, broker- and live vault-generation-bound. They are
NOT user authorization. Consumers must obtain one before destructive dequeue,
and use it only for already admitted capture persistence. A separate drain lease
exists only while root is positively stopping the producer. Neither permits RF,
disclosure, receipt creation, or user reads. User lock deliberately does not
revoke admitted storage; vault stop is forbidden until the producer has exited
with its bound positive drain receipt. Expiry only denies, never proves drain.

RootVoiceBackend uses the existing voice request/receipt and SuspendSystem
process-tree verification. A successful drain is NOT a sleep-ready receipt:
auth, camera, kiosk, model, journal and suspend/resume gates still apply.
"""

from dataclasses import dataclass
import fcntl
import math
import os
from pathlib import Path
import re
import secrets
import stat
import threading
import time
import uuid

from .message_storage import MessageStorageSnapshot
from .suspend_io import FENCE, publish, read_json, public_json
from .suspend_system import SuspendSystem


VOICE = "sage-lantern-voice-loop.service"
VAULT = "sage-lantern-personal-sage.service"
AUTH = "sage-lantern-device-auth.service"
CHECKS = {"http_drained", "http_outcome_known", "session_finalized", "child_groups_absent",
          "memory_drained", "community_drained", "history_outcome_known", "deadline_met"}

# How long one verified epoch/vault identity pair is reused by the *background*
# lifecycle check. Every guarded call re-derives the pair: `_epoch()` is a signed
# HTTP snapshot of the vault service and `vault_identity()` is a `systemctl show`
# subprocess. A dispatch wake makes dozens of guarded calls per fragment - on
# unit B 2026-09-19 the wake paid ~165 verifications (approximately 5 s of a 10 s
# radio budget) inside one fragment and timed out before the wire. One window
# keeps a change observable within a second; the admission paths (`check`,
# `_live`, `voice_peer`) keep their own immediate verification.
LIVE_CHECK_SECONDS = 1.0


class StorageSupervisorError(RuntimeError):
    def __init__(self):
        super().__init__("storage_supervisor_unavailable")


@dataclass(frozen=True)
class Producer:
    pid: int
    uid: int
    identity: dict


@dataclass(frozen=True)
class DrainProof:
    broker_id: str
    transition_id: str
    vault_epoch: str


class RootVoiceBackend:
    """Fixed local service operations; no caller-supplied commands or unit names."""

    def __init__(self, *, voice_uid, system=None, request_path=Path("/run/sage-lantern-voice-drain-request.json")):
        if type(voice_uid) is not int or voice_uid <= 0:
            raise StorageSupervisorError()
        self.uid = voice_uid
        self.request_path = Path(request_path)
        self.system = system if system is not None else SuspendSystem()

    def _deadline(self):
        if os.geteuid() != 0:
            raise StorageSupervisorError()
        return self.system.clock() + self.system.timeout

    def vault_identity(self):
        value = self.system._show(VAULT, self._deadline(), ("ActiveState", "Job", "MainPID", "InvocationID"))
        if (value["ActiveState"] != "active" or not self.system._no_job(value["Job"])
                or re.fullmatch(r"[1-9][0-9]{0,9}", value["MainPID"]) is None
                or re.fullmatch(r"[0-9a-f]{32}", value["InvocationID"]) is None):
            raise StorageSupervisorError()
        return value["MainPID"], value["InvocationID"]

    def producer(self, *, stopping=False):
        deadline = self._deadline()
        value = self.system._show(VOICE, deadline, ("ActiveState", "Job", "MainPID", "InvocationID"))
        identity = read_json(Path("/run/sage-lantern-voice/instance.json"), owner_uid=self.uid)
        if (type(identity) is not dict
                or set(identity) != {"instance_id", "pid", "start_ticks", "boot_id", "invocation_id"}
                or value["ActiveState"] not in ({"active", "deactivating"} if stopping else {"active"})
                or (not stopping and not self.system._no_job(value["Job"]))
                or type(identity["pid"]) is not int or identity["pid"] <= 0
                or str(identity["pid"]) != value["MainPID"]
                or identity["invocation_id"] != value["InvocationID"]
                or re.fullmatch(r"[0-9a-f]{32}", value["InvocationID"]) is None
                or type(identity["instance_id"]) is not str
                or str(uuid.UUID(identity["instance_id"])) != identity["instance_id"]
                or identity["boot_id"] != Path("/proc/sys/kernel/random/boot_id").read_text().strip()
                or identity["start_ticks"] != Path(f'/proc/{identity["pid"]}/stat').read_text().rsplit(")", 1)[1].split()[19]
                or Path(f'/proc/{identity["pid"]}').stat().st_uid != self.uid):
            raise StorageSupervisorError()
        return Producer(identity["pid"], self.uid, identity)

    def start_voice(self):
        if self.system.inactive_verified(VOICE).confirmed is not True:
            raise StorageSupervisorError()
        self.system._command(["start", VOICE], self._deadline())
        deadline = self._deadline()
        while self.system.clock() < deadline:
            try:
                return self.producer()
            except Exception:
                self.system.sleep(0.05)
        raise StorageSupervisorError()

    def stop_voice(self, producer, transition, *, timeout):
        if self.producer() != producer:
            return False
        publish(self.request_path,
                {**producer.identity, "schema": "sage.lantern.voice-drain-request.v1",
                 "transition_id": transition}, mode=0o644)
        original_timeout = self.system.timeout
        try:
            self.system.timeout = timeout
            if self.system.stop_verified(VOICE).confirmed is not True:
                return False
        finally:
            self.system.timeout = original_timeout
        value = read_json(Path("/run/sage-lantern-voice/drain-receipt.json"), owner_uid=self.uid)
        return (type(value) is dict and value.get("schema") == "sage.lantern.voice-drain.v1"
                and value.get("transition_id") == transition
                and all(type(value.get(key)) is type(member) and value[key] == member
                        for key, member in producer.identity.items())
                and value.get("drained") is True and value.get("reconcile_required") is False
                and type(value.get("checks")) is dict and set(value["checks"]) == CHECKS
                and all(value["checks"][key] is True for key in CHECKS)
                and self.voice_inactive() is True)

    def voice_inactive(self):
        return self.system.inactive_verified(VOICE).confirmed is True

    def drain_prerequisites(self, transition):
        if (read_json(FENCE, owner_uid=0) != {"transition_id": transition}
                or self.system.verify_stop_guards().confirmed is not True
                or self.system.inactive_verified(AUTH).confirmed is not True):
            return False
        directory = Path("/var/lib/sage-lantern/device-lock/drain")
        info = directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.uid
                or stat.S_IMODE(info.st_mode) != 0o700 or directory.resolve() != directory):
            return False
        count = 0
        for path in directory.iterdir():
            count += 1
            if count > 1024 or re.fullmatch(r"[0-9a-f]{32}\.json", path.name) is None:
                return False
            value = read_json(path, owner_uid=self.uid, limit=512)
            if (type(value) is not dict
                    or set(value) != {"version", "instance_id", "closing", "drained", "local_revoked", "upstream_cleanup"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["instance_id"] != path.stem
                    or any(value[key] is not True for key in ("closing", "drained", "local_revoked"))
                    or value["upstream_cleanup"] != "confirmed"):
                return False
        return count > 0

    def stop_vault(self, expected):
        if self.vault_identity() != expected or self.voice_inactive() is not True:
            return False
        deadline = self._deadline()
        fields = ("ActiveState", "Job", "MainPID", "ControlGroup", "KillMode")
        before = self.system._show(VAULT, deadline, fields)
        if before["KillMode"] != "control-group" or not before["ControlGroup"]:
            return False
        group = self.system._cgroup(before["ControlGroup"])
        self.system._command(["stop", VAULT], deadline)
        after = self.system._show(VAULT, deadline, fields)
        if (after["ActiveState"] != "inactive" or not self.system._no_job(after["Job"])
                or after["MainPID"] != "0"):
            return False
        self.system._empty_tree(group, deadline)
        if after["ControlGroup"]:
            self.system._empty_tree(self.system._cgroup(after["ControlGroup"]), deadline)
        return self.system._show(VAULT, deadline, fields) == after

    def resume_prerequisites(self):
        state = public_json(3196, "/state", limit=256)
        return (not os.path.lexists(FENCE) and self.voice_inactive() is True
                and self.system.verify_sleep_idle().confirmed is True
                and type(state) is dict and set(state) == {"locked"} and state["locked"] is True)


class RootStorageSupervisor:
    """Own one voice lifetime. Existing state requires operator recovery, not retry.

    directory must be a preprovisioned root-private real directory. snapshot is
    the real MessageStorageProbe.snapshot, access_epoch is device_access.access_epoch.
    Injection supports synthetic tests; these are trusted root dependencies,
    never request-supplied callbacks. All cooperating service starts must use
    start(); admission fails when this broker is absent or its instance changes.
    """

    def __init__(self, directory, *, backend, snapshot, access_epoch, clock=time.monotonic, owner_uid=0):
        self._mutex = threading.RLock()
        self._control = threading.Lock()
        self._fd = None
        self._closed = False
        self._record = None
        self._pid = os.getpid()
        self.phase = "initial"
        self.broker_id = secrets.token_hex(16)
        self._leases = {}
        self._producer = None
        self._proof = None
        self._resource = None
        self._resource_joined = False
        self._shutdown_requested = False
        self._backend, self._snapshot, self._access, self._clock = backend, snapshot, access_epoch, clock
        self._live_cache = None
        self._epoch_observers = set()
        self._epoch_refused = False
        self._epoch_dependencies = (backend, snapshot, clock, backend.vault_identity,
            self._mutex, self._epoch_observers, type(self)._held, type(self)._epoch)
        self.directory = Path(directory)
        self._owner = owner_uid
        try:
            self._path_identity = self._path()
            self._fd = os.open(self.directory / "owner.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(self._fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != owner_uid
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):
                raise StorageSupervisorError()
            self._inode = info.st_dev, info.st_ino
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._state_path = self.directory / "fence.json"
            if os.path.lexists(self._state_path):
                raise StorageSupervisorError()
            self._persist("initial")
        except Exception:
            self.close()
            raise StorageSupervisorError() from None

    def _path(self):
        if (os.geteuid() != self._owner or not self.directory.is_absolute()
                or ".." in self.directory.parts or self.directory.resolve() != self.directory):
            raise StorageSupervisorError()
        result = []
        for path in (*reversed(self.directory.parents), self.directory):
            info = path.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, self._owner}
                    or (info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX))):
                raise StorageSupervisorError()
            if path == self.directory and (info.st_uid != self._owner or stat.S_IMODE(info.st_mode) != 0o700):
                raise StorageSupervisorError()
            result.append((info.st_dev, info.st_ino))
        return result

    def _held(self):
        if self._closed or self._pid != os.getpid() or self._path() != self._path_identity:
            raise StorageSupervisorError()
        if self._record is not None and read_json(self._state_path, owner_uid=self._owner) != self._record:
            raise StorageSupervisorError()
        info = (self.directory / "owner.lock").lstat()
        if ((info.st_dev, info.st_ino) != self._inode or info.st_nlink != 1
                or info.st_uid != self._owner or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size != 0):
            raise StorageSupervisorError()

    def _persist(self, phase):
        self._held()
        record = {"schema": "sage.lantern.storage-supervisor.v1",
                  "broker_id": self.broker_id, "phase": phase}
        publish(self._state_path, record, owner_uid=self._owner)
        self._record = record
        self.phase = phase

    def _epoch(self):
        value = self._snapshot()
        if (type(value) is not MessageStorageSnapshot
                or re.fullmatch(r"[0-9a-f]{64}", value.instance_id) is None
                or re.fullmatch(r"[1-9][0-9]{0,19}", value.vault_generation) is None
                or int(value.vault_generation) > 2**64 - 1):
            raise StorageSupervisorError()
        return value.epoch

    def _live_identity(self, key):
        """The verified (epoch, vault identity) pair, reused for LIVE_CHECK_SECONDS.

        The pair is re-derived on every guarded call, which is what a dispatch
        wake cannot afford (see LIVE_CHECK_SECONDS). Any phase, ownership or
        broker change invalidates the reuse immediately because `key` carries
        them, and a change in the identity itself is observed within one window.
        """
        now = self._clock()
        cached = self._live_cache
        if cached is not None and cached[0] == key and now - cached[1] < LIVE_CHECK_SECONDS:
            return cached[2], cached[3]
        epoch = self._epoch()
        vault = self._backend.vault_identity()
        self._live_cache = (key, now, epoch, vault)
        return epoch, vault

    def _live(self):
        self._held()
        if self._epoch() != self._baseline or self._backend.vault_identity() != self._vault:
            raise StorageSupervisorError()

    def _fail(self):
        self.phase = "recovery_required"
        self._leases.clear()
        try:
            self._persist("recovery_required")
        except Exception:
            pass

    def start(self):
        with self._control, self._mutex:
            try:
                if self.phase != "initial":
                    raise StorageSupervisorError()
                self._baseline, self._vault = self._epoch(), self._backend.vault_identity()
                self._persist("starting")
                self._producer = self._backend.start_voice()
                if type(self._producer) is not Producer:
                    raise StorageSupervisorError()
                self._live()
                self._persist("active")
            except Exception:
                self._fail()
                raise StorageSupervisorError() from None

    def acquire(self, *, pid, uid, epoch):
        with self._mutex:
            try:
                self._live()
                if (self.phase != "active" or len(self._leases) >= 32
                        or type(epoch) is not str or re.fullmatch(r"[0-9a-f]{32}", epoch) is None
                        or self._access() != epoch or self._backend.producer() != self._producer
                        or (pid, uid) != (self._producer.pid, self._producer.uid)):
                    raise StorageSupervisorError()
                token = secrets.token_hex(32)
                self._leases[token] = self._clock() + 30
                self._live()
                if self._access() != epoch:
                    del self._leases[token]
                    raise StorageSupervisorError()
                return token
            except Exception:
                raise StorageSupervisorError() from None

    def voice_peer(self, *, pid, uid, broker_id=None, draining=False):
        try:
            with self._mutex:
                self._held()
                before = (self.broker_id, self.phase, self._baseline, self._vault, self._producer, self._shutdown_requested)
                if not (self.phase in ({"active", "draining", "stopping"} if draining else {"active"})
                        and (draining or not self._shutdown_requested)
                        and (broker_id is None or broker_id == self.broker_id)
                        and type(pid) is int and type(uid) is int
                        and (pid, uid) == (self._producer.pid, self._producer.uid)):
                    return False
            if (self._epoch() != before[2] or self._backend.vault_identity() != before[3]
                    or self._backend.producer(stopping=draining) != before[4]):
                return False
            with self._mutex:
                self._held()
                return before == (self.broker_id, self.phase, self._baseline, self._vault, self._producer, self._shutdown_requested)
        except Exception:
            return False

    def own_resource(self, resource):
        with self._mutex:
            self._live()
            if self.phase != "active" or self._resource is not None or not callable(resource.close):
                raise StorageSupervisorError()
            self._resource = resource

    def resource_lifecycle_check(self):
        try:
            with self._mutex:
                self._held()
                before = (self.broker_id, self.phase, self._baseline, self._vault,
                    self._resource, self._resource_joined, getattr(self, '_drain_deadline', None))
                if self._resource is None or self._resource_joined:
                    return False
            epoch, vault = self._live_identity((self.broker_id, self.phase, self._resource is not None,
                                                self._resource_joined, self._vault))
            if epoch != before[2] or vault != before[3]:
                return False
            with self._mutex:
                self._held()
                return (before == (self.broker_id, self.phase, self._baseline, self._vault,
                        self._resource, self._resource_joined, getattr(self, '_drain_deadline', None))
                    and (self.phase == "active" or self.phase in {"draining", "stopping"}
                        and self._clock() < self._drain_deadline))
        except Exception:
            return False

    def verified_epoch(self):
        """Fresh storage identity, never a cached permission or drain receipt.

        Already-owned resources may observe it during the original bounded
        drain, while private action callers retain their own active-only gate.
        Callback IO releases the state lock; no lease or deadline is renewed.
        """
        thread = threading.get_ident()
        entered = False
        resource = None
        dependencies = self._epoch_dependencies
        backend, snapshot, clock, vault_reader, mutex, observers, held, epoch_reader = dependencies

        def state():
            current_vault = backend.vault_identity
            same_vault_reader = (getattr(current_vault, '__self__', None) is getattr(vault_reader, '__self__', None)
                and getattr(current_vault, '__func__', current_vault) is getattr(vault_reader, '__func__', vault_reader))
            if (self._epoch_dependencies is not dependencies or self._epoch_refused
                    or self._backend is not backend or self._snapshot is not snapshot or self._clock is not clock
                    or self._mutex is not mutex or self._epoch_observers is not observers
                    or type(self)._held is not held or type(self)._epoch is not epoch_reader
                    or '_held' in vars(self) or '_epoch' in vars(self) or not same_vault_reader):
                raise StorageSupervisorError()
            held(self)
            info = os.fstat(self._fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != self._owner
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0
                    or (info.st_dev, info.st_ino) != self._inode
                    or resource is None or self._resource is not resource or self._resource_joined
                    or self.phase not in {'active', 'draining', 'stopping'}):
                raise StorageSupervisorError()
            deadline = getattr(self, '_drain_deadline', None)
            if (self._epoch_refused or self._epoch_dependencies is not dependencies
                    or self._backend is not backend or self._snapshot is not snapshot
                    or self._clock is not clock or self._mutex is not mutex or self._epoch_observers is not observers):
                raise StorageSupervisorError()
            return (self.broker_id, self.phase, self._baseline, self._vault,
                self._resource_joined, deadline, self._shutdown_requested,
                self._fd, self._inode, self._pid, self._owner, self.directory, self._state_path,
                tuple(self._path_identity), tuple(sorted(self._record.items())))

        def checked_state():
            before = state()
            if before[1] != 'active':
                now = clock()
                if (state() != before or type(before[5]) not in (int, float) or not math.isfinite(before[5])
                        or type(now) not in (int, float) or not math.isfinite(now) or now >= before[5]):
                    raise StorageSupervisorError()
            return before

        try:
            with mutex:
                if thread in observers:
                    self._epoch_refused = True
                    raise StorageSupervisorError()
                observers.add(thread)
                entered = True
                resource = self._resource
                before = checked_state()
            epoch = epoch_reader(self)
            with mutex:
                if checked_state() != before:
                    raise StorageSupervisorError()
            vault = vault_reader()
            with mutex:
                if checked_state() != before or epoch != before[2] or vault != before[3]:
                    raise StorageSupervisorError()
                return epoch
        except BaseException as error:
            if entered:
                with mutex:
                    self._epoch_refused = True
            if not isinstance(error, Exception):
                raise
            raise StorageSupervisorError() from None
        finally:
            if entered:
                with mutex:
                    observers.discard(thread)

    def resource_drain(self, timeout):
        with self._mutex:
            self._live()
            if self.phase not in {"draining", "stopping"}:
                return False
            if self._resource_joined or self._resource is None:
                return True
            resource = self._resource
        joined = resource.close(timeout=timeout)
        with self._mutex:
            self._live()
            if joined is not True or self._clock() >= self._drain_deadline:
                return False
            self._resource_joined = True
            return True

    def check(self, token, *, pid, uid):
        try:
            with self._mutex:
                self._held()
                if type(token) is not str or self.phase not in {"active", "draining"}:
                    return False
                before = (self.broker_id, self.phase, self._baseline, self._vault,
                    self._producer, self._leases.get(token, -math.inf))
                if (pid, uid) != (self._producer.pid, self._producer.uid) or self._clock() >= before[5]:
                    return False
            if (self._epoch() != before[2] or self._backend.vault_identity() != before[3]
                    or self._backend.producer(stopping=before[1] == "draining") != before[4]):
                return False
            with self._mutex:
                self._held()
                return (before == (self.broker_id, self.phase, self._baseline, self._vault,
                        self._producer, self._leases.get(token, -math.inf))
                    and self._clock() < before[5])
        except Exception:
            return False

    def finish_capture(self, token, *, pid, uid):
        with self._mutex:
            if self._producer is not None and (pid, uid) == (self._producer.pid, self._producer.uid):
                self._leases.pop(token, None)

    def drain_check(self, *, pid, uid):
        with self._mutex:
            try:
                self._live()
                return (self.phase == "draining" and self._clock() < self._drain_deadline
                        and (pid, uid) == (self._producer.pid, self._producer.uid)
                        and self._backend.producer(stopping=True) == self._producer)
            except Exception:
                return False

    def drain(self, transition_id, *, timeout=30):
        if (type(transition_id) is not str or str(uuid.UUID(transition_id)) != transition_id
                or type(timeout) not in (int, float) or not math.isfinite(timeout) or not 1 <= timeout <= 60):
            raise StorageSupervisorError()
        if not self._control.acquire(blocking=False):
            raise StorageSupervisorError()
        try:
            with self._mutex:
                if self.phase != "active":
                    raise StorageSupervisorError()
                self._live()
                self._drain_deadline = self._clock() + timeout
                self._persist("draining")
                if self._backend.drain_prerequisites(transition_id) is not True:
                    raise StorageSupervisorError()
            stopped = self._backend.stop_voice(self._producer, transition_id, timeout=timeout)
            remaining = self._drain_deadline - self._clock()
            resource_joined = remaining > 0 and self.resource_drain(remaining) is True
            with self._mutex:
                self._live()
                if (stopped is not True or resource_joined is not True or self._clock() >= self._drain_deadline
                        or self._backend.voice_inactive() is not True):
                    raise StorageSupervisorError()
                self._leases.clear()
                self._persist("drained")
                self._proof = DrainProof(self.broker_id, transition_id, self._baseline)
                return self._proof
        except Exception:
            with self._mutex:
                self._fail()
            raise StorageSupervisorError() from None
        finally:
            self._control.release()

    def close_vault(self, proof):
        with self._control, self._mutex:
            try:
                self._live()
                if (type(proof) is not DrainProof or proof is not self._proof or self.phase != "drained"
                        or self._leases or self._backend.voice_inactive() is not True
                        or self._backend.drain_prerequisites(proof.transition_id) is not True):
                    raise StorageSupervisorError()
                self._persist("vault_closing")
                if self._backend.stop_vault(self._vault) is not True:
                    raise StorageSupervisorError()
                self._persist("vault_closed")
            except Exception:
                self._fail()
                raise StorageSupervisorError() from None

    def resume(self, proof):
        with self._control, self._mutex:
            try:
                self._live()
                if (self._shutdown_requested or type(proof) is not DrainProof or proof is not self._proof or self.phase != "drained"
                        or self._backend.resume_prerequisites() is not True):
                    raise StorageSupervisorError()
                self._persist("restarting")
                self._producer = self._backend.start_voice()
                if type(self._producer) is not Producer:
                    raise StorageSupervisorError()
                self._resource = None
                self._resource_joined = False
                self._proof = None
                self.broker_id = secrets.token_hex(16)
                self._live()
                self._persist("active")
            except Exception:
                self._fail()
                raise StorageSupervisorError() from None

    def begin_shutdown(self, timeout):
        with self._mutex:
            self._shutdown_requested = True
            try:
                self._held()
                if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
                    return False
                deadline = self._clock() + timeout
                if self.phase in {"draining", "stopping"}:
                    self._drain_deadline = min(self._drain_deadline, deadline)
                elif self.phase in {"initial", "starting", "active"}:
                    self._drain_deadline = deadline
                    self._persist("stopping")
                return self.phase in {"stopping", "draining", "drained", "vault_closed", "stopped_verified"}
            except Exception:
                return False

    def finish_shutdown(self, timeout):
        with self._mutex:
            if not self._shutdown_requested or self.phase not in {"stopping", "draining", "drained", "vault_closed", "stopped_verified"}:
                return False
            resource = None if self._resource_joined else self._resource
        try:
            if resource is not None and resource.close(timeout=timeout) is not True:
                return False
            with self._mutex:
                self._held()
                if self._leases or self.phase == "recovery_required":
                    return False
                self._resource_joined = True
                self._persist("stopped_verified")
                return True
        except Exception:
            return False

    def close(self):
        with self._mutex:
            self._closed = True
            self._leases.clear()
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None
