"""Pure staged suspend coordinator; all effects belong to a trusted adapter.

No commands, files, timers or systemd assumptions are implemented here. The
adapter must serialize all OS sleep/auth-management paths, enforce deadlines on
every action, and keep a root-owned fence across process death. ACKs are local
typed evidence, not authentication of arbitrary external JSON.

CHECKPOINT confirms durable storage of the exact snapshot. SUBMIT_SLEEP only
confirms submission, never resume. VERIFY_RESUMED proves the correlated sleep
cycle ended with no pending sleep job. VERIFY_ABORTED proves no sleep remains
queued/running and none can start behind the coordinator's back. Both proofs
must cover other suspend entry paths, not just the last command's exit status.

QUIESCE confirms private endpoints deny, sensitive output/capture has stopped,
UI secrets are cleared, and upstream credential cleanup has completed. A delay
or unavailable auth port is insufficient. STOP_AUTH and AUTH_INACTIVE cover all
workers and competing starts. RETAIN_FENCE blocks starts/admission until release.
START_AUTH_LOCKED starts the broker with its unchanged persistent rate database
and confirms fresh locked readiness; it never supplies a PIN or restores tokens.

Adapter exceptions/negative/unknown ACKs enter recovery. Best-effort recovery
actions cannot guarantee safety when the adapter itself fails: the root adapter
must independently default to fenced/unavailable on death. Never use an
unconditional finally/ExecStopPost handler to release its fence. A restored
snapshot always requires a fresh terminal proof, even if it records completion.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import Enum


class Phase(Enum):
    PREPARING = "preparing"
    SLEEP_REQUESTED = "sleep_requested"
    RESUMED = "resumed"
    ABORTED_VERIFIED = "aborted_verified"
    RECOVERY_REQUIRED = "recovery_required"


class Gate(Enum):
    RETAIN_FENCE = "retain_fence"
    CHECKPOINT = "checkpoint"
    STOP_AUTH = "stop_auth"
    AUTH_INACTIVE = "auth_inactive"
    QUIESCE = "quiesce"
    IDLE_ELIGIBLE = "idle_eligible"
    SUBMIT_SLEEP = "submit_sleep"
    VERIFY_RESUMED = "verify_resumed"
    VERIFY_ABORTED = "verify_aborted"
    RELEASE_FENCE = "release_fence"
    START_AUTH_LOCKED = "start_auth_locked"


class Outcome(Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Snapshot:
    transition_id: str
    phase: Phase = Phase.PREPARING
    revision: int = 0
    prepared: bool = False
    completed: bool = False


@dataclass(frozen=True)
class Request:
    gate: Gate
    snapshot: Snapshot


@dataclass(frozen=True)
class Acknowledgement:
    request: Request
    outcome: Outcome


class SuspendTransitionError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


Perform = Callable[[Request], Acknowledgement]


class SuspendTransition:
    """One owner per transition; concurrent/reentrant calls reject as busy.

    prepare/request_sleep/resume/abort return immutable snapshots. request_sleep
    can run once only; uncertainty requires resume/abort verification, not retry.
    snapshot is observational: it is not a substitute for the external fence.
    """

    def __init__(self, transition_id: str, perform: Perform):
        if (type(transition_id) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", transition_id) is None
                or not callable(perform)):
            raise SuspendTransitionError("invalid_configuration")
        self._perform = perform
        self._snapshot = Snapshot(transition_id)
        self._mutex = threading.Lock()

    @property
    def snapshot(self) -> Snapshot:
        return self._snapshot

    @classmethod
    def restore(cls, snapshot: Snapshot, perform: Perform) -> SuspendTransition:
        if (type(snapshot) is not Snapshot or type(snapshot.phase) is not Phase
                or type(snapshot.revision) is not int or snapshot.revision < 0
                or type(snapshot.prepared) is not bool or type(snapshot.completed) is not bool
                or (snapshot.prepared and snapshot.phase is not Phase.PREPARING)
                or (snapshot.completed and snapshot.phase not in (Phase.RESUMED, Phase.ABORTED_VERIFIED))):
            raise SuspendTransitionError("invalid_snapshot")
        coordinator = cls(snapshot.transition_id, perform)
        coordinator._snapshot = replace(snapshot, phase=Phase.RECOVERY_REQUIRED,
                                        revision=snapshot.revision + 1, prepared=False, completed=False)
        return coordinator

    def _ack(self, gate: Gate) -> None:
        request = Request(gate, self._snapshot)
        acknowledgement = self._perform(request)
        if (type(acknowledgement) is not Acknowledgement
                or acknowledgement.request is not request
                or acknowledgement.outcome is not Outcome.CONFIRMED):
            raise SuspendTransitionError("gate_unconfirmed:" + gate.value)

    def _checkpoint(self, phase: Phase, *, prepared: bool = False, completed: bool = False) -> None:
        self._snapshot = replace(self._snapshot, phase=phase,
                                 revision=self._snapshot.revision + 1,
                                 prepared=prepared, completed=completed)
        self._ack(Gate.CHECKPOINT)

    def _recover_failure(self) -> None:
        self._snapshot = replace(self._snapshot, phase=Phase.RECOVERY_REQUIRED,
                                 revision=self._snapshot.revision + 1, prepared=False, completed=False)
        for gate in (Gate.RETAIN_FENCE, Gate.STOP_AUTH, Gate.AUTH_INACTIVE, Gate.CHECKPOINT):
            try:
                self._ack(gate)
            except Exception:
                pass

    def _run(self, operation: Callable[[], None], allowed: Callable[[], bool]) -> Snapshot:
        if not self._mutex.acquire(blocking=False):
            raise SuspendTransitionError("busy")
        try:
            if not allowed() or self._snapshot.completed:
                raise SuspendTransitionError("invalid_transition")
            try:
                operation()
            except Exception:
                self._recover_failure()
                raise SuspendTransitionError("recovery_required") from None
            return self._snapshot
        finally:
            self._mutex.release()

    def prepare(self) -> Snapshot:
        def operation():
            self._ack(Gate.RETAIN_FENCE)
            self._checkpoint(Phase.PREPARING)
            self._ack(Gate.STOP_AUTH)
            self._ack(Gate.AUTH_INACTIVE)
            self._ack(Gate.QUIESCE)
            self._ack(Gate.IDLE_ELIGIBLE)
            self._checkpoint(Phase.PREPARING, prepared=True)

        return self._run(operation, lambda: self._snapshot.phase is Phase.PREPARING and not self._snapshot.prepared)

    def request_sleep(self) -> Snapshot:
        def operation():
            self._ack(Gate.RETAIN_FENCE)
            self._ack(Gate.AUTH_INACTIVE)
            self._ack(Gate.QUIESCE)
            self._ack(Gate.IDLE_ELIGIBLE)
            self._checkpoint(Phase.SLEEP_REQUESTED)
            self._ack(Gate.SUBMIT_SLEEP)

        return self._run(operation, lambda: self._snapshot.phase is Phase.PREPARING and self._snapshot.prepared)

    def _finish(self, phase: Phase, proof: Gate) -> None:
        self._ack(Gate.RETAIN_FENCE)
        self._ack(Gate.STOP_AUTH)
        self._ack(Gate.AUTH_INACTIVE)
        self._ack(Gate.QUIESCE)
        self._ack(proof)
        self._checkpoint(phase, completed=True)
        self._ack(Gate.RELEASE_FENCE)
        self._ack(Gate.START_AUTH_LOCKED)

    def resume(self) -> Snapshot:
        return self._run(lambda: self._finish(Phase.RESUMED, Gate.VERIFY_RESUMED),
                         lambda: self._snapshot.phase in (Phase.SLEEP_REQUESTED, Phase.RECOVERY_REQUIRED))

    def abort(self) -> Snapshot:
        return self._run(lambda: self._finish(Phase.ABORTED_VERIFIED, Gate.VERIFY_ABORTED),
                         lambda: self._snapshot.phase in (Phase.PREPARING, Phase.SLEEP_REQUESTED,
                                                          Phase.RECOVERY_REQUIRED))
