from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


PUBLIC_SCHEMA = "sage.commons.public-derivative.v1"
INITIAL_STATE = "QUEUED_LOCAL"
STATES = {
    "QUEUED_LOCAL",
    "SENT",
    "RECEIVED",
    "QUARANTINED",
    "NEEDS_ACTION",
    "APPROVED",
    "SUBMITTED",
    "COMMITTED",
    "REJECTED",
    "EXPIRED",
    "SUPERSEDED",
}
TRANSITIONS = {
    "QUEUED_LOCAL": {"SENT", "REJECTED", "EXPIRED", "SUPERSEDED"},
    "SENT": {"SENT", "RECEIVED", "REJECTED", "EXPIRED"},
    "RECEIVED": {"QUARANTINED", "NEEDS_ACTION", "APPROVED", "REJECTED", "EXPIRED"},
    "QUARANTINED": {"NEEDS_ACTION", "APPROVED", "REJECTED", "EXPIRED"},
    "NEEDS_ACTION": {"QUARANTINED", "APPROVED", "REJECTED", "EXPIRED"},
    "APPROVED": {"SUBMITTED", "REJECTED", "EXPIRED"},
    "SUBMITTED": {"COMMITTED", "REJECTED", "EXPIRED"},
    "COMMITTED": {"SUPERSEDED"},
    "REJECTED": set(),
    "EXPIRED": set(),
    "SUPERSEDED": set(),
}


@dataclass(frozen=True)
class ProposalRecord:
    envelope_id: str
    content_sha256: str
    state: str
    reason_code: str | None
    updated_at: str

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


class ProposalOutbox:
    """Durable idempotent storage for already signed public derivative envelopes."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS proposal_outbox (
                envelope_id TEXT PRIMARY KEY,
                content_sha256 TEXT NOT NULL,
                canonical_json TEXT NOT NULL,
                state TEXT NOT NULL,
                reason_code TEXT,
                receipt_json TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def enqueue(self, raw: str) -> tuple[ProposalRecord, bool]:
        data = self._validate_envelope(raw)
        canonical = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        content_sha256 = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        envelope_id = data["envelope_id"]
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        try:
            self.connection.execute(
                """
                INSERT INTO proposal_outbox
                    (envelope_id, content_sha256, canonical_json, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (envelope_id, content_sha256, canonical, INITIAL_STATE, now, now),
            )
            self.connection.commit()
            return self.get(envelope_id), True
        except sqlite3.IntegrityError:
            record = self.get(envelope_id)
            if record.content_sha256 != content_sha256:
                raise ValueError("envelope_id is already bound to different canonical bytes") from None
            return record, False

    def update_state(
        self,
        envelope_id: str,
        state: str,
        *,
        reason_code: str | None = None,
        receipt: dict[str, object] | None = None,
    ) -> ProposalRecord:
        if state not in STATES:
            raise ValueError("unknown proposal state")
        current = self.get(envelope_id)
        if state not in TRANSITIONS[current.state]:
            raise ValueError(f"invalid proposal transition: {current.state} -> {state}")
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        receipt_json = None if receipt is None else json.dumps(receipt, separators=(",", ":"), sort_keys=True)
        self.connection.execute(
            """
            UPDATE proposal_outbox
               SET state = ?, reason_code = ?, receipt_json = ?, updated_at = ?
             WHERE envelope_id = ?
            """,
            (state, reason_code, receipt_json, now, envelope_id),
        )
        self.connection.commit()
        return self.get(envelope_id)

    def get(self, envelope_id: str) -> ProposalRecord:
        row = self.connection.execute(
            """
            SELECT envelope_id, content_sha256, state, reason_code, updated_at
              FROM proposal_outbox
             WHERE envelope_id = ?
            """,
            (envelope_id,),
        ).fetchone()
        if row is None:
            raise KeyError(envelope_id)
        return ProposalRecord(*row)

    @staticmethod
    def _validate_envelope(raw: str) -> dict[str, object]:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("malformed public derivative JSON") from exc
        if not isinstance(data, dict):
            raise ValueError("public derivative must be a JSON object")
        if data.get("schema") != PUBLIC_SCHEMA:
            raise ValueError("unsupported public derivative schema")
        envelope_id = data.get("envelope_id")
        if not isinstance(envelope_id, str) or not 8 <= len(envelope_id) <= 128:
            raise ValueError("invalid envelope_id")
        privacy = data.get("privacy")
        if not isinstance(privacy, dict) or privacy.get("classification") != "PUBLIC":
            raise ValueError("public derivative classification must be PUBLIC")
        signature = data.get("signature")
        if (
            not isinstance(signature, dict)
            or signature.get("algorithm") != "Ed25519"
            or not isinstance(signature.get("value"), str)
            or not signature["value"]
        ):
            raise ValueError("a detached Ed25519 signature is required")
        return data
