from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime


SCHEMA = "sage.lantern.message.v1"
_AGENT_ID = re.compile(r"^[0-9a-f]{64}$")
_PAYLOAD_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")


@dataclass(frozen=True)
class LanternEnvelope:
    schema: str
    message_id: str
    source_agent_id: str
    destination: str
    sequence: int
    created_at: str
    payload_type: str
    body: str
    ttl_seconds: int
    integrity: str

    def canonical_json(self) -> str:
        self.validate()
        return json.dumps(asdict(self), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def encoded_size(self) -> int:
        return len(self.canonical_json().encode("utf-8"))

    def validate(self, *, max_body_bytes: int = 1024, require_integrity: bool = False) -> None:
        if self.schema != SCHEMA:
            raise ValueError("unsupported envelope schema")
        if not self.message_id or len(self.message_id) > 128:
            raise ValueError("invalid message_id")
        if not _AGENT_ID.fullmatch(self.source_agent_id):
            raise ValueError("source_agent_id must be a lowercase 32-byte Ed25519 public key")
        if not self.destination or len(self.destination) > 256:
            raise ValueError("invalid destination")
        if self.sequence < 0:
            raise ValueError("sequence must be non-negative")
        if not _PAYLOAD_TYPE.fullmatch(self.payload_type):
            raise ValueError("invalid payload_type")
        if len(self.body.encode("utf-8")) > max_body_bytes:
            raise ValueError("body exceeds configured byte limit")
        if not 1 <= self.ttl_seconds <= 86_400:
            raise ValueError("ttl_seconds must be between 1 and 86400")
        if require_integrity and not self.integrity:
            raise ValueError("integrity metadata is required")
        self.created_datetime()

    def created_datetime(self) -> datetime:
        parsed = datetime.fromisoformat(self.created_at.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        return parsed.astimezone(UTC)

    def is_expired(self, now: datetime | None = None) -> bool:
        current = (now or datetime.now(UTC)).astimezone(UTC)
        return (current - self.created_datetime()).total_seconds() > self.ttl_seconds

    @classmethod
    def from_json(cls, raw: str, *, max_body_bytes: int = 1024, require_integrity: bool = True) -> "LanternEnvelope":
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed envelope JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("envelope must be a JSON object")
        expected = set(cls.__dataclass_fields__)
        if set(data) != expected:
            raise ValueError("envelope fields do not match the v1 schema")
        envelope = cls(**data)
        envelope.validate(max_body_bytes=max_body_bytes, require_integrity=require_integrity)
        if envelope.is_expired():
            raise ValueError("expired envelope")
        return envelope

