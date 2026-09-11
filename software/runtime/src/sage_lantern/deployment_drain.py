"""Single-process deployment admission, not a portable or persistent receipt.

Trusted root composition must supply artifact and live broker verifiers. Checking
drain-response JSON alone is NOT a live broker verifier. No positive defaults,
implicit fence clearing, service restart, vault shutdown or recovery inference exist here.
Explicit completion is separate from fence-retaining run().
"""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from .mesh_voice_ipc import managed, root_control
from .suspend_io import publish, read_json
from .suspend_journal import SuspendJournal
from .suspend_system import DEPLOYMENT_FENCE, IDLE_SUSPEND, LLM, STOP_SERVICES, SystemEvidence
from .suspend_transition import Snapshot


def verify_broker_request(supervisor, retained_proof, request, *, peer_uid):
    """Root demux hook; peer_uid MUST come from kernel SO_PEERCRED.

    Uses the supervisor's actual retained object, owner lock, live SDK storage
    epoch and joined resource state. Does not admit new work or renew a lease.
    """
    from .storage_supervisor import DrainProof, RootStorageSupervisor
    require(peer_uid == 0 and type(peer_uid) is int and type(supervisor) is RootStorageSupervisor)
    require(type(request) is dict and set(request) == {"op", "proof", "nonce"}
        and request["op"] in {"verify-deployment-drain", "verify-deployment-completion"}
        and type(request["nonce"]) is str and str(uuid.UUID(request["nonce"])) == request["nonce"])
    require(type(retained_proof) is DrainProof and request["proof"] == asdict(retained_proof))
    require(supervisor._control.acquire(blocking=False))
    try:
        require(supervisor._mutex.acquire(blocking=False))
        try:
            supervisor._live()
            require(retained_proof is supervisor._proof and supervisor.phase == "drained"
                and not supervisor._shutdown_requested and not supervisor._leases
                and supervisor._resource is not None and supervisor._resource_joined is True)
            require(retained_proof.vault_epoch == supervisor._baseline
                and retained_proof.broker_id == supervisor.broker_id)
            _confirmed(supervisor._backend.system.deployment_inactive_verified("sage-lantern-voice-loop.service"))
            supervisor._live()
            return {"result": {"nonce": request["nonce"], "proof": asdict(retained_proof)}}
        finally:
            supervisor._mutex.release()
    finally:
        supervisor._control.release()


def verify_root_broker(proof, *, completion=False):
    """Concrete fixed root-IPC verifier; old daemons reject instead of granting."""
    from .mesh_voice_ipc import connect, receive, send
    require(os.geteuid() == 0)
    nonce = str(uuid.uuid4())
    connection = connect()
    try:
        connection.settimeout(5)
        send(connection, {"op": "verify-deployment-completion" if completion else "verify-deployment-drain",
                          "proof": proof, "nonce": nonce})
        response = receive(connection, 1024)
        require(response == {"result": {"nonce": nonce, "proof": proof}})
        return True
    finally:
        connection.close()


def verify_broker_active_request(supervisor, request, *, peer_uid):
    """Read-only root proof of a live producer and real, unchanged storage epoch."""
    from .storage_supervisor import RootStorageSupervisor
    require(type(peer_uid) is int and peer_uid == 0 and type(supervisor) is RootStorageSupervisor)
    require(type(request) is dict and set(request) == {"op", "nonce"}
        and request["op"] == "verify-deployment-active"
        and type(request["nonce"]) is str and str(uuid.UUID(request["nonce"])) == request["nonce"])
    require(supervisor._control.acquire(blocking=False))
    try:
        require(supervisor._mutex.acquire(blocking=False))
        try:
            supervisor._live()
            require(supervisor.phase == "active" and not supervisor._shutdown_requested
                and supervisor._proof is None and supervisor._resource is not None
                and not supervisor._resource_joined
                and supervisor._backend.producer() == supervisor._producer)
            result = dict(nonce=request["nonce"], broker_id=supervisor.broker_id,
                vault_epoch=supervisor._baseline, broker_pid=os.getpid(),
                voice_pid=supervisor._producer.pid)
            supervisor._live()
            return {"result": result}
        finally:
            supervisor._mutex.release()
    finally:
        supervisor._control.release()


def verify_root_broker_active():
    """No legacy fallback, storage-status cache, activation grant or user token."""
    from .mesh_voice_ipc import connect, receive, send
    require(os.geteuid() == 0)
    nonce = str(uuid.uuid4())
    connection = connect()
    try:
        connection.settimeout(5)
        send(connection, {"op": "verify-deployment-active", "nonce": nonce})
        response = receive(connection, 1024)
        require(type(response) is dict and set(response) == {"result"})
        value = response["result"]
        require(type(value) is dict and set(value) == {
            "nonce", "broker_id", "vault_epoch", "broker_pid", "voice_pid"})
        require(value["nonce"] == nonce and type(value["broker_id"]) is str
            and re.fullmatch(r"[0-9a-f]{32}", value["broker_id"]) is not None)
        require(type(value["vault_epoch"]) is str
            and re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]{0,19}", value["vault_epoch"]) is not None
            and int(value["vault_epoch"].split(":")[1]) <= 2**64 - 1)
        require(all(type(value[key]) is int and value[key] > 0 for key in ("broker_pid", "voice_pid")))
        return {key: member for key, member in value.items() if key != "nonce"}
    finally:
        connection.close()


def resume_root_broker(proof):
    """Completion substep, not authority to clear appliance/deployment fences.

    The retained broker enforces locked-auth and sleep-idle prerequisites before
    its own voice start. An ambiguous reply is never retried or called success.
    """
    require(verify_root_broker(proof))
    require(root_control("resume", proof["transition_id"]))
    result = verify_root_broker_active()
    require(result["vault_epoch"] == proof["vault_epoch"]
        and result["broker_id"] != proof["broker_id"])
    return result


class DeploymentDrainError(RuntimeError):
    def __init__(self):
        super().__init__("deployment_drain_unavailable")


def require(value):
    if value is not True:
        raise DeploymentDrainError()


def _confirmed(value):
    require(type(value) is SystemEvidence and value.confirmed is True)


def _digest(value):
    require(type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None)
    return value


def _boot():
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


@dataclass(frozen=True)
class DeploymentEvidence:
    transition_id: str
    candidate_sha256: str
    rollback_sha256: str
    boot_id: str
    broker_id: str
    vault_epoch: str


class DeploymentDrain:
    @classmethod
    def acquire(cls, runner, *, candidate_sha256, rollback_sha256, verify_binding,
                verify_broker, lifetime_seconds=600):
        """Stop/join idle suspend BEFORE acquiring its journal and Appliance.

        runner is trusted loaded installed idle-suspend code, not request input.
        verify_binding(candidate_sha256, rollback_sha256) revalidates both sealed
        inventories. verify_broker(drain_response) checks the retained live broker
        and vault epoch. Callbacks must return literal True; unavailable denies.
        Existing deployment markers are never adopted or overwritten.
        """
        require(os.geteuid() == 0)
        _digest(candidate_sha256)
        _digest(rollback_sha256)
        require(callable(verify_binding) and callable(verify_broker))
        require(type(lifetime_seconds) is int and 1 <= lifetime_seconds <= 1800)
        require(verify_binding(candidate_sha256, rollback_sha256))
        require(managed())
        marker = Path(DEPLOYMENT_FENCE)
        transition = str(uuid.uuid4())
        binding = dict(schema="sage.lantern.deployment-drain.v1", transition_id=transition,
            candidate_sha256=candidate_sha256, rollback_sha256=rollback_sha256,
            boot_id=_boot(), pid=os.getpid())
        descriptor = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            os.fchmod(stream.fileno(), 0o600)
            json.dump(binding, stream, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        parent = os.open(marker.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
        system = runner.SuspendSystem()
        idle_pid, idle_invocation = runner.service_identity(IDLE_SUSPEND)
        _confirmed(system.deployment_stop_verified(IDLE_SUSPEND,
            expected_pid=idle_pid, expected_invocation=idle_invocation))
        journal = SuspendJournal()
        kiosk = None
        try:
            state = journal.load()
            require(not state.active and not state.needs_recovery)
            require(runner.model_baseline_ready())
            model_identity = runner.service_identity(LLM)
            kiosk = runner.kiosk_fence()
            appliance = runner.Appliance(system, runner.power_policy(), transition, kiosk)
            return cls(journal, appliance, transition, candidate_sha256=candidate_sha256,
                rollback_sha256=rollback_sha256, verify_binding=verify_binding,
                verify_broker=verify_broker, marker=marker, binding=binding,
                model_identity=model_identity, lifetime_seconds=lifetime_seconds)
        except BaseException:
            if kiosk is not None:
                kiosk.close()
            journal.close()
            raise

    def __init__(self, journal, appliance, transition_id, *, candidate_sha256,
                 rollback_sha256, verify_binding, verify_broker, marker, binding,
                 model_identity, lifetime_seconds=600, owner_uid=0, boot=_boot,
                 clock=time.monotonic, control=root_control):
        require(type(owner_uid) is int and os.geteuid() == owner_uid and type(journal) is SuspendJournal)
        require(type(transition_id) is str and str(uuid.UUID(transition_id)) == transition_id
            and uuid.UUID(transition_id).int != 0 and appliance.transition_id == transition_id)
        require(callable(verify_binding) and callable(verify_broker) and callable(control))
        require(type(lifetime_seconds) is int and 1 <= lifetime_seconds <= 1800)
        self._candidate, self._rollback = _digest(candidate_sha256), _digest(rollback_sha256)
        self.journal, self.appliance = journal, appliance
        self._snapshot = Snapshot(transition_id)
        self._mutex = threading.Lock()
        self._failed = self._prepared = self._spent = self._running = False
        self._pid, self._thread, self._uid = os.getpid(), threading.get_ident(), owner_uid
        self._boot, self._clock, self._boot_id = boot, clock, boot()
        require(type(self._boot_id) is str and str(uuid.UUID(self._boot_id)) == self._boot_id)
        self._deadline = clock() + lifetime_seconds
        self._binding = dict(binding)
        require(self._binding == dict(schema="sage.lantern.deployment-drain.v1", transition_id=transition_id,
            candidate_sha256=self._candidate, rollback_sha256=self._rollback,
            boot_id=self._boot_id, pid=self._pid))
        self._marker = Path(marker)
        self._verify_binding, self._verify_broker = verify_binding, verify_broker
        self._model_identity, self._control = model_identity, control
        self._proof = None

    def _identity(self):
        require(not self._failed and not self._spent and os.getpid() == self._pid
            and threading.get_ident() == self._thread and os.geteuid() == self._uid
            and self._boot() == self._boot_id and self._clock() < self._deadline)
        require(read_json(self._marker, owner_uid=self._uid) == self._binding)
        require(self._verify_binding(self._candidate, self._rollback))
        _confirmed(self.appliance.system.deployment_inactive_verified(IDLE_SUSPEND))

    def _system(self):
        for method in (self.appliance.system.verify_stop_guards, self.appliance.system.verify_sleep_idle):
            _confirmed(method())

    def prepare(self):
        require(self._mutex.acquire(blocking=False))
        try:
            self._identity()
            require(not self._prepared)
            state = self.journal.load()
            require(not state.needs_recovery and not state.active and self.appliance.fenced() is False)
            self._system()
            self.journal.retain_fence(self._snapshot.transition_id)
            require(self.appliance.retain_fence())
            self._snapshot = Snapshot(self._snapshot.transition_id, revision=1)
            self.journal.checkpoint(self._snapshot)
            require(self.appliance.stop_auth() is True and self.appliance.auth_inactive() is True)
            require(self.appliance.auth_drained())
            proof = self._control("drain", self._snapshot.transition_id)
            require(type(proof) is dict and set(proof) == {"broker_id", "transition_id", "vault_epoch"})
            require(proof["transition_id"] == self._snapshot.transition_id
                and type(proof["broker_id"]) is str and re.fullmatch(r"[0-9a-f]{32}", proof["broker_id"]) is not None
                and type(proof["vault_epoch"]) is str and re.fullmatch(r"[0-9a-f]{64}:[1-9][0-9]{0,19}", proof["vault_epoch"]) is not None
                and int(proof["vault_epoch"].split(":")[1]) <= 2**64 - 1)
            self._proof = dict(proof)
            require(self._verify_broker(dict(self._proof)))
            self.appliance.stopped.add("sage-lantern-voice-loop.service")
            require(self.appliance.quiesce())
            self._system()
            self._identity()
            model_pid, model_invocation = self._model_identity
            _confirmed(self.appliance.system.deployment_stop_verified(LLM,
                expected_pid=model_pid, expected_invocation=model_invocation))
            self._snapshot = Snapshot(self._snapshot.transition_id, revision=2, prepared=True)
            self.journal.checkpoint(self._snapshot)
            self._prepared = True
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            return DeploymentEvidence(self._snapshot.transition_id, self._candidate, self._rollback,
                self._boot_id, self._proof["broker_id"], self._proof["vault_epoch"])
        except BaseException:
            self._failed = True
            raise
        finally:
            self._mutex.release()

    def assert_current(self, *, candidate_sha256, rollback_sha256):
        """Raises on uncertainty. Call immediately before each bounded apply stage."""
        try:
            require(candidate_sha256 == self._candidate and rollback_sha256 == self._rollback)
            self._identity()
            require(self._prepared)
            state = self.journal.load()
            require(state.active and state.snapshot == self._snapshot
                and state.transition_id == self._snapshot.transition_id)
            self._system()
            require(self.appliance.fenced() is True and self.appliance.retain_fence() is True)
            require(self.appliance.auth_drained() is True and self.appliance.voice_drained() is True)
            for service in STOP_SERVICES | {LLM}:
                _confirmed(self.appliance.system.deployment_inactive_verified(service))
            require(self.appliance.kiosk.quiesce())
            require(self._verify_broker(dict(self._proof)))
            self._identity()
            return True
        except BaseException:
            self._failed = True
            raise

    def check(self):
        try:
            return self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
        except Exception:
            return False

    def mutate(self, stage, action):
        """One bounded mutation with durable intent/done and live checks on both sides."""
        require(self._running and type(stage) is str
            and re.fullmatch(r"[a-z][a-z0-9_-]{0,47}", stage) is not None and callable(action))
        try:
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            path = self.journal.directory / ("deployment-" + self._snapshot.transition_id + "-" + stage + ".json")
            record = dict(schema="sage.lantern.deployment-mutation.v1", binding=self._binding,
                stage=stage, phase="intent")
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(descriptor, "w") as stream:
                os.fchmod(stream.fileno(), 0o600)
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            result = action()
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            require(read_json(path, owner_uid=self._uid) == record)
            publish(path, {**record, "phase": "done"}, owner_uid=self._uid)
            return result
        except BaseException:
            self._failed = True
            raise

    def run(self, mutation):
        require(callable(mutation) and self._mutex.acquire(blocking=False))
        try:
            require(not self._running)
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            self._running = True
            result = mutation(self)
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            return result
        except BaseException:
            self._failed = True
            raise
        finally:
            self._spent = True
            self._mutex.release()

    def close(self):
        """Abandon admission, retaining every recovery fence; no implicit resume."""
        require(not self._running or self._spent)
        self._spent = True
        self.appliance.kiosk.close()
        self.journal.close()

    def apply_and_complete(self, mutation, *, completion):
        """Single-use explicit completion; ordinary run() remains fence-retaining."""
        from .deployment_completion import DeploymentCompletion
        require(type(completion) is DeploymentCompletion and callable(mutation))
        require(self._mutex.acquire(blocking=False))
        try:
            require(not self._running)
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            require(verify_root_broker(self._proof, completion=True))
            self._running = True
            mutation(self)
            self.assert_current(candidate_sha256=self._candidate, rollback_sha256=self._rollback)
            return completion.finish(self)
        except BaseException:
            self._failed = True
            raise
        finally:
            self._spent = True
            self._mutex.release()
