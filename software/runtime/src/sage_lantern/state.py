from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RuntimeState(StrEnum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    MESH_SENDING = "mesh_sending"
    MESH_RECEIVING = "mesh_receiving"
    SYNC_QUEUED = "sync_queued"
    DEGRADED = "degraded"
    ERROR = "error"


ALLOWED_TRANSITIONS: dict[RuntimeState, set[RuntimeState]] = {
    RuntimeState.IDLE: {
        RuntimeState.THINKING,
        RuntimeState.LISTENING,
        RuntimeState.MESH_SENDING,
        RuntimeState.MESH_RECEIVING,
        RuntimeState.SYNC_QUEUED,
        RuntimeState.DEGRADED,
        RuntimeState.ERROR,
    },
    RuntimeState.LISTENING: {RuntimeState.TRANSCRIBING, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.TRANSCRIBING: {RuntimeState.THINKING, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.THINKING: {RuntimeState.SPEAKING, RuntimeState.IDLE, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.SPEAKING: {RuntimeState.IDLE, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.MESH_SENDING: {RuntimeState.IDLE, RuntimeState.SYNC_QUEUED, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.MESH_RECEIVING: {RuntimeState.IDLE, RuntimeState.ERROR, RuntimeState.DEGRADED},
    RuntimeState.SYNC_QUEUED: {RuntimeState.MESH_SENDING, RuntimeState.IDLE, RuntimeState.DEGRADED, RuntimeState.ERROR},
    RuntimeState.DEGRADED: {RuntimeState.IDLE, RuntimeState.ERROR},
    RuntimeState.ERROR: {RuntimeState.IDLE, RuntimeState.DEGRADED},
}


@dataclass(frozen=True)
class StateEvent:
    session_id: str
    previous: RuntimeState
    current: RuntimeState
    monotonic_ns: int
    detail: str = ""


class JsonlEventLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, event: StateEvent) -> None:
        record = {
            "session_id": event.session_id,
            "previous": event.previous.value,
            "state": event.current.value,
            "monotonic_ns": event.monotonic_ns,
            "detail": event.detail,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")


class StateMachine:
    def __init__(self, event_log: JsonlEventLog, initial: RuntimeState = RuntimeState.IDLE) -> None:
        self.state = initial
        self.event_log = event_log

    def transition(self, target: RuntimeState, session_id: str, detail: str = "") -> StateEvent:
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise ValueError(f"invalid state transition: {self.state.value} -> {target.value}")
        event = StateEvent(
            session_id=session_id,
            previous=self.state,
            current=target,
            monotonic_ns=time.monotonic_ns(),
            detail=detail,
        )
        self.state = target
        self.event_log.write(event)
        return event
