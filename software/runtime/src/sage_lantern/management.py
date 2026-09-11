from __future__ import annotations

import base64
import hashlib
import json
import os
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey


OBSERVATION_SCHEMA = "sage.lantern.management.observation.v1"
COMMAND_SCHEMA = "sage.lantern.management.command.v1"
RECEIPT_SCHEMA = "sage.lantern.management.receipt.v1"

# Device management is declarative and bounded. This is intentionally not a
# remote shell or a pass-through to personal SAGE authority.
ALLOWED_ACTIONS = frozenset(
    {
        "apply_ai_preferences",
        "apply_device_profile",
        "collect_sanitized_evidence",
        "run_diagnostic",
        "stage_release",
    }
)
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _validate_protocol_value(value: object, *, path: str = "value") -> None:
    if value is None or isinstance(value, (str, bool)):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise ValueError(f"{path} integer exceeds the cross-platform safe range")
        return
    if isinstance(value, float):
        raise ValueError(f"{path} must not contain floating-point values")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_protocol_value(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{path} keys must be strings")
            _validate_protocol_value(item, path=f"{path}.{key}")
        return
    raise ValueError(f"{path} contains an unsupported value")


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False)


def sha256_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO 8601") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: str, *, name: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be base64") from exc


@dataclass(frozen=True)
class DeviceManagementIdentity:
    signing_key: SigningKey

    @classmethod
    def load_or_create(cls, root: Path) -> "DeviceManagementIdentity":
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(root, 0o700)
        private_path = root / "management-ed25519.key"
        public_path = root / "management-ed25519.pub"

        if private_path.exists():
            raw = private_path.read_bytes()
            if len(raw) != 32:  # Ed25519 seed size
                raise ValueError("invalid device-management private key length")
            identity = cls(SigningKey(raw))
        else:
            identity = cls(SigningKey.generate())
            fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                os.write(fd, identity.signing_key.encode())
                os.fsync(fd)
            finally:
                os.close(fd)

        expected_public = identity.signing_key.verify_key.encode()
        if public_path.exists() and public_path.read_bytes() != expected_public:
            raise ValueError("device-management public key does not match private key")
        if not public_path.exists():
            public_path.write_bytes(expected_public)
            os.chmod(public_path, 0o644)
        return identity

    @property
    def public_key(self) -> str:
        return _b64encode(self.signing_key.verify_key.encode())

    @property
    def fingerprint(self) -> str:
        return "sha256:" + hashlib.sha256(self.signing_key.verify_key.encode()).hexdigest()

    def sign(self, value: object) -> str:
        return _b64encode(self.signing_key.sign(canonical_json(value).encode("utf-8")).signature)


@dataclass(frozen=True)
class Observation:
    schema: str
    device_id: str
    sequence: int
    observed_at: str
    freshness_expires_at: str
    clock_confidence: str
    source_fingerprint: str
    state: Mapping[str, Any]
    state_hash: str

    @classmethod
    def create(
        cls,
        *,
        device_id: str,
        sequence: int,
        observed_at: datetime,
        freshness_ttl: timedelta,
        clock_confidence: str,
        source_fingerprint: str,
        state: Mapping[str, Any],
    ) -> "Observation":
        if not device_id:
            raise ValueError("device_id is required")
        if sequence < 0:
            raise ValueError("sequence must be non-negative")
        if freshness_ttl <= timedelta(0) or freshness_ttl > timedelta(hours=24):
            raise ValueError("freshness_ttl must be between 1 second and 24 hours")
        if observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
        observed = observed_at.astimezone(UTC)
        _validate_protocol_value(state, path="observation state")
        state_copy = json.loads(canonical_json(state))
        if len(canonical_json(state_copy).encode("utf-8")) > 65_536:
            raise ValueError("observation state exceeds 65536 bytes")
        return cls(
            schema=OBSERVATION_SCHEMA,
            device_id=device_id,
            sequence=sequence,
            observed_at=observed.isoformat().replace("+00:00", "Z"),
            freshness_expires_at=(observed + freshness_ttl).isoformat().replace("+00:00", "Z"),
            clock_confidence=clock_confidence,
            source_fingerprint=source_fingerprint,
            state=state_copy,
            state_hash=sha256_json(state_copy),
        )


@dataclass(frozen=True)
class SignedObservation:
    observation: Observation
    signature: str

    @classmethod
    def create(cls, observation: Observation, identity: DeviceManagementIdentity) -> "SignedObservation":
        if observation.source_fingerprint != identity.fingerprint:
            raise ValueError("observation source does not match device-management identity")
        return cls(observation=observation, signature=identity.sign(asdict(observation)))

    def to_json(self) -> str:
        return canonical_json({"observation": asdict(self.observation), "signature": self.signature})

    @classmethod
    def from_json(cls, raw: str) -> "SignedObservation":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed signed observation JSON") from exc
        if not isinstance(value, dict) or set(value) != {"observation", "signature"}:
            raise ValueError("signed observation fields do not match the v1 schema")
        observation_value = value["observation"]
        if not isinstance(observation_value, dict) or set(observation_value) != set(Observation.__dataclass_fields__):
            raise ValueError("observation fields do not match the v1 schema")
        if not isinstance(value["signature"], str):
            raise ValueError("observation signature must be a string")
        observation = Observation(**observation_value)
        if observation.schema != OBSERVATION_SCHEMA:
            raise ValueError("unsupported observation schema")
        if sha256_json(observation.state) != observation.state_hash:
            raise ValueError("observation state hash does not match state")
        return cls(observation=observation, signature=value["signature"])


@dataclass(frozen=True)
class ManagementCommand:
    schema: str
    command_id: str
    device_id: str
    sequence: int
    issued_at: str
    expires_at: str
    action: str
    expected_state_hash: str
    desired_revision: str
    parameters: Mapping[str, Any]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ManagementCommand":
        expected = set(cls.__dataclass_fields__)
        if set(value) != expected:
            raise ValueError("command fields do not match the v1 schema")
        command = cls(**value)
        command.validate()
        return command

    def validate(self) -> None:
        if self.schema != COMMAND_SCHEMA:
            raise ValueError("unsupported management command schema")
        if not self.command_id or not self.device_id or not self.desired_revision:
            raise ValueError("command_id, device_id, and desired_revision are required")
        if self.sequence < 0:
            raise ValueError("command sequence must be non-negative")
        if self.action not in ALLOWED_ACTIONS:
            raise ValueError("management action is not allowed")
        if not isinstance(self.parameters, dict):
            raise ValueError("command parameters must be an object")
        _validate_protocol_value(self.parameters, path="command parameters")
        if len(canonical_json(self.parameters).encode("utf-8")) > 16_384:
            raise ValueError("command parameters exceed 16384 bytes")
        issued_at = _utc(self.issued_at)
        expires_at = _utc(self.expires_at)
        if expires_at <= issued_at:
            raise ValueError("command expiry must be after issue time")
        if expires_at - issued_at > timedelta(hours=24):
            raise ValueError("command validity cannot exceed 24 hours")


@dataclass(frozen=True)
class SignedCommand:
    command: ManagementCommand
    signature: str

    @classmethod
    def from_json(cls, raw: str) -> "SignedCommand":
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed signed command JSON") from exc
        if not isinstance(value, dict) or set(value) != {"command", "signature"}:
            raise ValueError("signed command fields do not match the v1 schema")
        if not isinstance(value["command"], dict) or not isinstance(value["signature"], str):
            raise ValueError("invalid signed command")
        return cls(ManagementCommand.from_dict(value["command"]), value["signature"])


class ReplayStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS accepted_commands (
                   authority_fingerprint TEXT NOT NULL,
                   command_id TEXT NOT NULL,
                   sequence INTEGER NOT NULL,
                   accepted_at TEXT NOT NULL,
                   PRIMARY KEY (authority_fingerprint, command_id),
                   UNIQUE (authority_fingerprint, sequence)
               )"""
        )
        self.connection.commit()

    def accept(self, *, authority_fingerprint: str, command_id: str, sequence: int, accepted_at: datetime) -> None:
        try:
            self.connection.execute("BEGIN IMMEDIATE")
            highest = self.connection.execute(
                "SELECT MAX(sequence) FROM accepted_commands WHERE authority_fingerprint = ?",
                (authority_fingerprint,),
            ).fetchone()[0]
            if highest is not None and sequence <= highest:
                raise ValueError("command sequence is stale or replayed")
            try:
                self.connection.execute(
                    "INSERT INTO accepted_commands VALUES (?, ?, ?, ?)",
                    (authority_fingerprint, command_id, sequence, accepted_at.astimezone(UTC).isoformat()),
                )
            except sqlite3.IntegrityError as exc:
                raise ValueError("command was already accepted") from exc
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise

    def close(self) -> None:
        self.connection.close()


@dataclass(frozen=True)
class CommandReceipt:
    schema: str
    command_id: str
    device_id: str
    command_sequence: int
    action: str
    desired_revision: str
    outcome: str
    reason_code: str | None
    prior_state_hash: str
    resulting_state_hash: str
    completed_at: str
    source_fingerprint: str


@dataclass(frozen=True)
class SignedReceipt:
    receipt: CommandReceipt
    signature: str

    def to_json(self) -> str:
        return canonical_json({"receipt": asdict(self.receipt), "signature": self.signature})


class CommandGate:
    def __init__(
        self,
        *,
        device_id: str,
        authority_verify_key: VerifyKey,
        replay_store: ReplayStore,
        identity: DeviceManagementIdentity,
        max_future_skew: timedelta = timedelta(minutes=5),
    ) -> None:
        self.device_id = device_id
        self.authority_verify_key = authority_verify_key
        self.authority_fingerprint = "sha256:" + hashlib.sha256(authority_verify_key.encode()).hexdigest()
        self.replay_store = replay_store
        self.identity = identity
        self.max_future_skew = max_future_skew

    @classmethod
    def from_authority_public_key(
        cls, *, device_id: str, authority_public_key: str, replay_store: ReplayStore, identity: DeviceManagementIdentity
    ) -> "CommandGate":
        raw = _b64decode(authority_public_key, name="authority_public_key")
        try:
            key = VerifyKey(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError("invalid authority public key") from exc
        return cls(device_id=device_id, authority_verify_key=key, replay_store=replay_store, identity=identity)

    def execute(
        self,
        signed: SignedCommand,
        *,
        current_state: Mapping[str, Any],
        handlers: Mapping[str, Callable[[Mapping[str, Any]], Mapping[str, Any]]],
        now: datetime | None = None,
    ) -> SignedReceipt:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        command = signed.command
        command.validate()
        try:
            self.authority_verify_key.verify(
                canonical_json(asdict(command)).encode("utf-8"),
                _b64decode(signed.signature, name="command signature"),
            )
        except BadSignatureError as exc:
            raise ValueError("command signature is invalid") from exc

        if command.device_id != self.device_id:
            raise ValueError("command targets a different device")
        if _utc(command.issued_at) > current + self.max_future_skew:
            raise ValueError("command issue time is too far in the future")
        if current >= _utc(command.expires_at):
            raise ValueError("command is expired")

        prior_hash = sha256_json(current_state)
        if command.expected_state_hash != prior_hash:
            raise ValueError("command expected_state_hash does not match current state")
        handler = handlers.get(command.action)
        if handler is None:
            raise ValueError("no local handler is installed for management action")

        # Acceptance is durable before execution. A crash cannot silently cause
        # the same signed mutation to run again after restart.
        self.replay_store.accept(
            authority_fingerprint=self.authority_fingerprint,
            command_id=command.command_id,
            sequence=command.sequence,
            accepted_at=current,
        )
        try:
            resulting_state = handler(command.parameters)
            outcome = "succeeded"
            reason_code = None
        except Exception as exc:  # the receipt records bounded failure, never exception text
            resulting_state = current_state
            outcome = "failed"
            reason_code = type(exc).__name__

        receipt = CommandReceipt(
            schema=RECEIPT_SCHEMA,
            command_id=command.command_id,
            device_id=self.device_id,
            command_sequence=command.sequence,
            action=command.action,
            desired_revision=command.desired_revision,
            outcome=outcome,
            reason_code=reason_code,
            prior_state_hash=prior_hash,
            resulting_state_hash=sha256_json(resulting_state),
            completed_at=current.isoformat().replace("+00:00", "Z"),
            source_fingerprint=self.identity.fingerprint,
        )
        return SignedReceipt(receipt=receipt, signature=self.identity.sign(asdict(receipt)))
