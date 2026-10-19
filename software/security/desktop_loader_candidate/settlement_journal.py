"""Disabled durable-journal contract for lifecycle settlement.

This layer records an attempted release and a later settlement in one exact
CAS record. The default backend refuses every operation. Injected backends are
test ports, not a durable store, an authenticated owner or a native probe.

The journal does not decide whether a child is gone. It accepts settlement only
after ``lifecycle_settlement.SettlementAuthority`` has accepted a proof, then
persists the record idempotently and verifies the write by reading it back.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import re

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


class JournalRefused(ValueError):
    """Known invalid journal state. Never permission to settle."""


class JournalBackendUnavailable(RuntimeError):
    pass


class JournalUnknown(RuntimeError):
    """A write may have happened; recovery must read fresh state."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def obligation_digest(obligation):
    """Stable digest of the launch binding and exact owned resources."""
    binding = obligation.binding
    return hashlib.sha256(canonical({
        "binding": {
            "machine_id": binding.machine_id,
            "boot_id": binding.boot_id,
            "capture_id": binding.capture_id,
            "generation": binding.generation,
            "policy_sha256": binding.policy_sha256,
            "launch_id": binding.launch_id,
        },
        "resources": [
            {"kind": item.kind, "identity": item.identity}
            for item in obligation.resources
        ],
    })).hexdigest()


def settlement_digest(obligation_sha256, proof_sha256):
    return hashlib.sha256(canonical({
        "obligation_sha256": obligation_sha256,
        "proof_sha256": proof_sha256,
    })).hexdigest()


@dataclass(frozen=True)
class SettlementRecord:
    obligation_sha256: str
    state: str
    revision: int
    proof_sha256: str | None = None
    receipt_sha256: str | None = None


@dataclass(frozen=True)
class JournalOutcome:
    status: str
    reason: str = ""
    record: SettlementRecord | None = None
    runtime_authorized: bool = False


class UnavailableJournalBackend:
    """Default: no durable settlement store exists."""
    def read(self, obligation_sha256):
        raise JournalBackendUnavailable("no_qualified_journal_backend")

    def create(self, record):
        raise JournalBackendUnavailable("no_qualified_journal_backend")

    def compare_exchange(self, before, after):
        raise JournalBackendUnavailable("no_qualified_journal_backend")


def _record(value, digest=None):
    if (type(value) is not SettlementRecord
            or not isinstance(value.obligation_sha256, str)
            or not _DIGEST.fullmatch(value.obligation_sha256)
            or value.state not in ("attempted", "settled")
            or type(value.revision) is not int or value.revision < 0
            or value.revision > 2**63 - 1):
        raise JournalRefused("settlement_record_invalid")
    if digest is not None and value.obligation_sha256 != digest:
        raise JournalRefused("settlement_record_binding_mismatch")
    if value.state == "attempted":
        if value.proof_sha256 is not None or value.receipt_sha256 is not None:
            raise JournalRefused("attempted_record_shape_invalid")
    else:
        if (not isinstance(value.proof_sha256, str)
                or not _DIGEST.fullmatch(value.proof_sha256)
                or not isinstance(value.receipt_sha256, str)
                or not _DIGEST.fullmatch(value.receipt_sha256)):
            raise JournalRefused("settled_record_shape_invalid")
        if value.receipt_sha256 != settlement_digest(
                value.obligation_sha256, value.proof_sha256):
            raise JournalRefused("settled_record_receipt_mismatch")
    return value


class SettlementJournal:
    """Idempotent attempt and settlement records over one CAS backend."""
    def __init__(self, backend=None, authority=None):
        self.backend = (UnavailableJournalBackend()
                        if backend is None else backend)
        self.authority = authority

    def _read(self, digest):
        value = self.backend.read(digest)
        if value is None:
            return None
        return _record(value, digest)

    def _state(self, digest, *, fallback_reason):
        try:
            record = self._read(digest)
        except (JournalRefused, JournalBackendUnavailable, OSError):
            return JournalOutcome("uncertain", fallback_reason)
        if record is None:
            return JournalOutcome("uncertain", "attempt_not_recorded")
        return JournalOutcome(record.state, record=record)

    def record_attempt(self, obligation):
        """Create the attempt once; an existing record is returned unchanged."""
        try:
            digest = obligation_digest(obligation)
            existing = self._read(digest)
            if existing is not None:
                return JournalOutcome(existing.state, record=existing)
            record = SettlementRecord(digest, "attempted", 0)
            created = self.backend.create(record)
            if created is None:
                return self._state(digest,
                                   fallback_reason="attempt_create_conflict")
            _record(created, digest)
            if created != record:
                return JournalOutcome("uncertain", "attempt_receipt_mismatch")
            persisted = self._read(digest)
            if persisted != record:
                return JournalOutcome("uncertain", "attempt_readback_mismatch")
            return JournalOutcome("attempted", record=record)
        except (JournalRefused, JournalBackendUnavailable, JournalUnknown,
                OSError) as error:
            return JournalOutcome("uncertain", str(error))

    def settle(self, obligation, proof):
        """Validate a proof, then CAS the attempt to settled exactly once."""
        if self.authority is None:
            return JournalOutcome("uncertain", "settlement_authority_missing")
        try:
            digest = obligation_digest(obligation)
            existing = self._read(digest)
        except (JournalRefused, JournalBackendUnavailable, JournalUnknown,
                OSError) as error:
            return JournalOutcome("uncertain", str(error))
        if existing is not None and existing.state == "settled":
            if getattr(proof, "proof_sha256", None) == existing.proof_sha256:
                return JournalOutcome("settled", record=existing)
            return JournalOutcome("uncertain",
                                  "already_settled_with_different_proof")
        try:
            accepted = self.authority.settle(obligation, proof)
        except (ValueError, RuntimeError, OSError) as error:
            return JournalOutcome("uncertain", str(error))
        if accepted.status != "settled" or accepted.receipt is None:
            return JournalOutcome("uncertain", accepted.reason or accepted.status)
        receipt = accepted.receipt
        try:
            if receipt.binding != obligation.binding:
                return JournalOutcome("uncertain", "settlement_binding_mismatch")
            record = existing
            if record is None:
                return JournalOutcome("uncertain", "attempt_not_recorded")
            if record.state != "attempted":
                return JournalOutcome("uncertain", "record_not_attempted")
            after = replace(
                record, state="settled", revision=record.revision + 1,
                proof_sha256=receipt.proof_sha256,
                receipt_sha256=settlement_digest(digest, receipt.proof_sha256))
            _record(after, digest)
            written = self.backend.compare_exchange(record, after)
            if written is None:
                current = self._read(digest)
                if (current is not None and current.state == "settled"
                        and current.proof_sha256 == receipt.proof_sha256):
                    return JournalOutcome("settled", record=current)
                return JournalOutcome("uncertain", "settlement_cas_conflict")
            _record(written, digest)
            if written != after:
                return JournalOutcome("uncertain", "settlement_receipt_mismatch")
            persisted = self._read(digest)
            if persisted != after:
                return JournalOutcome("uncertain", "settlement_readback_mismatch")
            return JournalOutcome("settled", record=after)
        except (JournalRefused, JournalBackendUnavailable, JournalUnknown,
                OSError) as error:
            return JournalOutcome("uncertain", str(error))
