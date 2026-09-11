from __future__ import annotations

import base64
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .crypto import b64decode, canonical_json, fingerprint

OBSERVATION_SCHEMA = "sage.lantern.management.observation.v1"
FIELDS = {"schema", "device_id", "sequence", "observed_at", "freshness_expires_at", "clock_confidence", "source_fingerprint", "state", "state_hash"}
MAX_SAFE_INTEGER = 9_007_199_254_740_991


def _time(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not (value.endswith("Z") or len(value) >= 6 and value[-6] in "+-" and value[-3] == ":"):
        raise ValueError(f"{name} must be a timezone-aware timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{name} must be a valid timestamp") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware timestamp")
    return parsed.astimezone(UTC)


def _protocol(value: Any, path: str = "value") -> None:
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
            _protocol(item, f"{path}[{index}]")
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for key, item in value.items():
            _protocol(item, f"{path}.{key}")
        return
    raise ValueError(f"{path} contains an unsupported value")


def verify_observation(raw: Any, identity: dict[str, Any], now: datetime) -> dict[str, Any]:
    if not isinstance(raw, dict) or set(raw) != {"observation", "signature"}:
        raise ValueError("signed observation fields are invalid")
    value, signature = raw["observation"], raw["signature"]
    if not isinstance(value, dict) or set(value) != FIELDS:
        raise ValueError("observation fields do not match the v1 schema")
    if value["schema"] != OBSERVATION_SCHEMA:
        raise ValueError("unsupported observation schema")
    if value["device_id"] != identity["device_id"]:
        raise ValueError("observation targets a different device record")
    if value["source_fingerprint"] != identity["management_fingerprint"]:
        raise ValueError("observation identity is not registered")
    sequence = value["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= MAX_SAFE_INTEGER:
        raise ValueError("invalid observation sequence")
    if not isinstance(value["clock_confidence"], str) or not value["clock_confidence"]:
        raise ValueError("clock confidence is required")
    if not isinstance(value["state"], dict):
        raise ValueError("observation state must be an object")
    _protocol(value["state"], "observation state")
    state_json = canonical_json(value["state"])
    if len(state_json) > 65_536:
        raise ValueError("observation state exceeds 65536 bytes")
    observed, expires = _time(value["observed_at"], "observed_at"), _time(value["freshness_expires_at"], "freshness_expires_at")
    if expires <= observed or expires - observed > timedelta(hours=24):
        raise ValueError("observation freshness window is invalid")
    now = now.astimezone(UTC)
    if observed > now + timedelta(minutes=5):
        raise ValueError("observation time is too far in the future")
    if expires <= now:
        raise ValueError("observation is stale")
    state_hash = hashlib.sha256(state_json).hexdigest()
    if value["state_hash"] != state_hash:
        raise ValueError("observation state hash is invalid")
    public_raw = b64decode(identity["management_public_key"], 32, "management public key")
    if fingerprint(public_raw) != identity["management_fingerprint"]:
        raise ValueError("registered fingerprint does not match public key")
    signature_raw = b64decode(signature, 64, "observation signature") if isinstance(signature, str) else b""
    try:
        Ed25519PublicKey.from_public_bytes(public_raw).verify(signature_raw, canonical_json(value))
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("observation signature is invalid") from exc
    return {**value, "state_json": state_json.decode(), "signature": signature}

