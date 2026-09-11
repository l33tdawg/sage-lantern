from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from .management import DeviceManagementIdentity, Observation, SignedObservation, canonical_json
from .management_transport import IngestAcknowledgement, MTLSObservationClient


_ALLOWED_STATUS_KEYS = frozenset(
    {"status", "services", "software", "hardware", "resources", "mesh", "personal_sage", "commons", "queues", "alerts"}
)
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "private", "recovery", "mnemonic", "memory_content")
MAX_STATUS_BYTES = 65_536


class StatusCollector(Protocol):
    def collect(self) -> Mapping[str, Any]: ...


class SanitizedJSONStatusCollector:
    """Reads an allow-listed, already-sanitized local status document.

    It deliberately cannot launch commands or read SAGE databases. Producers own
    the local status file and must omit personal content and credentials.
    """

    def __init__(self, path: Path) -> None:
        self.path = path

    def collect(self) -> Mapping[str, Any]:
        with self.path.open("rb") as source:
            raw = source.read(MAX_STATUS_BYTES + 1)
        if len(raw) > MAX_STATUS_BYTES:
            raise ValueError("sanitized status exceeds 65536 bytes")
        try:
            value = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("sanitized status is not valid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError("sanitized status must be a JSON object")
        unknown = set(value) - _ALLOWED_STATUS_KEYS
        if unknown:
            raise ValueError(f"sanitized status contains unsupported fields: {', '.join(sorted(unknown))}")
        self._reject_sensitive_keys(value)
        return value

    @classmethod
    def _reject_sensitive_keys(cls, value: object, path: str = "state") -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                normalized = str(key).lower().replace("-", "_")
                if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                    raise ValueError(f"sanitized status contains prohibited field at {path}.{key}")
                cls._reject_sensitive_keys(item, f"{path}.{key}")
        elif isinstance(value, list):
            for index, item in enumerate(value):
                cls._reject_sensitive_keys(item, f"{path}[{index}]")


@dataclass(frozen=True)
class PendingObservation:
    sequence: int
    signed: SignedObservation
    attempts: int
    next_attempt_at: datetime


class ObservationOutbox:
    """Transactional monotonic sequence allocator and single-item durable outbox."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)
        self.connection = sqlite3.connect(path)
        os.chmod(path, 0o600)
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS sequence_state (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                last_sequence INTEGER NOT NULL CHECK (last_sequence >= 0)
            );
            INSERT OR IGNORE INTO sequence_state VALUES (1, 0);
            CREATE TABLE IF NOT EXISTS observation_outbox (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                sequence INTEGER NOT NULL UNIQUE,
                signed_json TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
                next_attempt_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def create(
        self,
        *,
        device_id: str,
        identity: DeviceManagementIdentity,
        state: Mapping[str, Any],
        observed_at: datetime,
        freshness_ttl: timedelta,
        clock_confidence: str,
    ) -> PendingObservation:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            existing = self.connection.execute("SELECT 1 FROM observation_outbox WHERE singleton = 1").fetchone()
            if existing:
                raise ValueError("an observation is already pending acknowledgement")
            last_sequence = self.connection.execute(
                "SELECT last_sequence FROM sequence_state WHERE singleton = 1"
            ).fetchone()[0]
            sequence = last_sequence + 1
            observation = Observation.create(
                device_id=device_id,
                sequence=sequence,
                observed_at=observed_at,
                freshness_ttl=freshness_ttl,
                clock_confidence=clock_confidence,
                source_fingerprint=identity.fingerprint,
                state=state,
            )
            signed = SignedObservation.create(observation, identity)
            now = observed_at.astimezone(UTC)
            self.connection.execute("UPDATE sequence_state SET last_sequence = ? WHERE singleton = 1", (sequence,))
            self.connection.execute(
                "INSERT INTO observation_outbox VALUES (1, ?, ?, 0, ?)",
                (sequence, signed.to_json(), now.isoformat()),
            )
            self.connection.commit()
            return PendingObservation(sequence, signed, 0, now)
        except Exception:
            self.connection.rollback()
            raise

    def pending(self) -> PendingObservation | None:
        row = self.connection.execute(
            "SELECT sequence, signed_json, attempts, next_attempt_at FROM observation_outbox WHERE singleton = 1"
        ).fetchone()
        if row is None:
            return None
        retry_at = datetime.fromisoformat(row[3])
        if retry_at.tzinfo is None:
            raise ValueError("stored retry timestamp has no timezone")
        return PendingObservation(row[0], SignedObservation.from_json(row[1]), row[2], retry_at.astimezone(UTC))

    def fail(self, sequence: int, *, now: datetime, base_backoff: float, max_backoff: float) -> float:
        if base_backoff <= 0 or max_backoff < base_backoff:
            raise ValueError("invalid retry backoff bounds")
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT attempts FROM observation_outbox WHERE singleton = 1 AND sequence = ?", (sequence,)
            ).fetchone()
            if row is None:
                raise ValueError("pending observation sequence does not match")
            attempts = row[0] + 1
            delay = min(base_backoff * (2 ** min(attempts - 1, 30)), max_backoff)
            retry_at = now.astimezone(UTC) + timedelta(seconds=delay)
            self.connection.execute(
                "UPDATE observation_outbox SET attempts = ?, next_attempt_at = ? WHERE singleton = 1",
                (attempts, retry_at.isoformat()),
            )
            self.connection.commit()
            return delay
        except Exception:
            self.connection.rollback()
            raise

    def acknowledge(self, acknowledgement: IngestAcknowledgement) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT signed_json FROM observation_outbox WHERE singleton = 1 AND sequence = ?",
                (acknowledgement.sequence,),
            ).fetchone()
            if row is None:
                raise ValueError("acknowledgement sequence is not pending")
            observation = SignedObservation.from_json(row[0]).observation
            if (
                acknowledgement.device_id != observation.device_id
                or acknowledgement.state_hash != observation.state_hash
                or not acknowledgement.accepted
            ):
                raise ValueError("acknowledgement does not match pending observation")
            self.connection.execute("DELETE FROM observation_outbox WHERE singleton = 1")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def discard_expired(self, sequence: int, *, now: datetime) -> None:
        """Burn an expired sequence without ever assigning it to new content."""
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute(
                "SELECT signed_json FROM observation_outbox WHERE singleton = 1 AND sequence = ?", (sequence,)
            ).fetchone()
            if row is None:
                raise ValueError("pending observation sequence does not match")
            observation = SignedObservation.from_json(row[0]).observation
            expires_at = datetime.fromisoformat(observation.freshness_expires_at.replace("Z", "+00:00"))
            if expires_at.tzinfo is None or now.astimezone(UTC) < expires_at.astimezone(UTC):
                raise ValueError("pending observation is not expired")
            self.connection.execute("DELETE FROM observation_outbox WHERE singleton = 1")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    @property
    def last_sequence(self) -> int:
        return self.connection.execute("SELECT last_sequence FROM sequence_state WHERE singleton = 1").fetchone()[0]

    def close(self) -> None:
        self.connection.close()


class DeviceObservationAgent:
    def __init__(
        self,
        *,
        device_id: str,
        identity: DeviceManagementIdentity,
        collector: StatusCollector,
        outbox: ObservationOutbox,
        client: MTLSObservationClient,
        freshness_ttl: timedelta = timedelta(minutes=5),
        clock_confidence: str = "unknown",
        base_backoff_seconds: float = 5,
        max_backoff_seconds: float = 300,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.device_id = device_id
        self.identity = identity
        self.collector = collector
        self.outbox = outbox
        self.client = client
        self.freshness_ttl = freshness_ttl
        self.clock_confidence = clock_confidence
        self.base_backoff_seconds = base_backoff_seconds
        self.max_backoff_seconds = max_backoff_seconds
        self.now = now

    def run_once(self) -> bool:
        current = self.now().astimezone(UTC)
        pending = self.outbox.pending()
        if pending is not None:
            expires_at = datetime.fromisoformat(
                pending.signed.observation.freshness_expires_at.replace("Z", "+00:00")
            ).astimezone(UTC)
            if current >= expires_at:
                self.outbox.discard_expired(pending.sequence, now=current)
                pending = None
        if pending is None:
            pending = self.outbox.create(
                device_id=self.device_id,
                identity=self.identity,
                state=self.collector.collect(),
                observed_at=current,
                freshness_ttl=self.freshness_ttl,
                clock_confidence=self.clock_confidence,
            )
        if current < pending.next_attempt_at:
            return False
        try:
            acknowledgement = self.client.send(pending.signed)
            self.outbox.acknowledge(acknowledgement)
            return True
        except Exception:
            self.outbox.fail(
                pending.sequence,
                now=current,
                base_backoff=self.base_backoff_seconds,
                max_backoff=self.max_backoff_seconds,
            )
            raise

    def run_forever(self, interval_seconds: float = 60) -> None:
        if interval_seconds <= 0:
            raise ValueError("observation interval must be positive")
        while True:
            try:
                sent = self.run_once()
            except Exception:
                sent = False
            pending = self.outbox.pending()
            if pending is not None:
                delay = max(0.1, (pending.next_attempt_at - self.now().astimezone(UTC)).total_seconds())
            else:
                delay = interval_seconds if sent else min(interval_seconds, self.base_backoff_seconds)
            time.sleep(delay)
