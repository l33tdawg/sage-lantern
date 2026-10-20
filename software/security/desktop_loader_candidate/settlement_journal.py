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
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat

import fcntl

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
    return hashlib.sha256(canonical(obligation_payload(obligation))).hexdigest()


def obligation_payload(obligation):
    """Canonical recoverable form of a launch obligation."""
    binding = obligation.binding
    return {
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
    }


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
    obligation: dict | None = None


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

    def pending(self):
        raise JournalBackendUnavailable("no_qualified_journal_backend")

    def create(self, record):
        raise JournalBackendUnavailable("no_qualified_journal_backend")

    def compare_exchange(self, before, after):
        raise JournalBackendUnavailable("no_qualified_journal_backend")


def _record_dict(record):
    return {
        "obligation_sha256": record.obligation_sha256,
        "state": record.state,
        "revision": record.revision,
        "proof_sha256": record.proof_sha256,
        "receipt_sha256": record.receipt_sha256,
        "obligation": record.obligation,
    }


def _record_from_dict(data):
    required = {"obligation_sha256", "state", "revision",
                "proof_sha256", "receipt_sha256"}
    if (not isinstance(data, dict)
            or set(data) not in (required, required | {"obligation"})):
        raise JournalRefused("journal_record_schema_invalid")
    return _record(SettlementRecord(
        data["obligation_sha256"], data["state"], data["revision"],
        data["proof_sha256"], data["receipt_sha256"],
        data.get("obligation")))


class FileJournalBackend:
    """Single-host durable CAS store for settlement records.

    The directory must be private (mode 0700). Each record is one JSON file,
    written through a private temporary file, fsynced and renamed into place.
    A lock file serializes create and compare-exchange operations. This is a
    source component, not an installed service and not a multi-host store.
    """
    MAX_BYTES = 4096

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if stat.S_IMODE(self.root.stat().st_mode) & 0o077:
            raise JournalRefused("journal_directory_not_private")
        self.lock_path = self.root / ".lock"

    @contextmanager
    def _locked(self):
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _path(self, digest):
        if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
            raise JournalRefused("journal_digest_invalid")
        path = self.root / (digest + ".json")
        if path.is_symlink():
            raise JournalRefused("journal_record_symlink")
        return path

    def _read(self, digest):
        path = self._path(digest)
        try:
            raw = path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > self.MAX_BYTES:
            raise JournalRefused("journal_record_too_large")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as error:
            raise JournalRefused("journal_record_decode") from error
        record = _record_from_dict(data)
        if record.obligation_sha256 != digest:
            raise JournalRefused("journal_record_digest_mismatch")
        return record

    def _write(self, record):
        path = self._path(record.obligation_sha256)
        payload = canonical(_record_dict(record)) + b"\n"
        if len(payload) > self.MAX_BYTES:
            raise JournalRefused("journal_record_too_large")
        temp = self.root / (
            f".{record.obligation_sha256}.{os.getpid()}."
            f"{secrets.token_hex(6)}.tmp")
        descriptor = None
        try:
            descriptor = os.open(
                temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temp, path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            except OSError:
                # Some filesystems do not support directory fsync.
                pass
            finally:
                os.close(directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temp.unlink()
            except FileNotFoundError:
                pass
        return record

    def read(self, obligation_sha256):
        with self._locked():
            return self._read(obligation_sha256)

    def pending(self):
        with self._locked():
            records = []
            for path in sorted(self.root.glob("*.json")):
                digest = path.stem
                record = self._read(digest)
                if record is not None and record.state == "attempted":
                    records.append(record)
            return tuple(records)

    def create(self, record):
        _record(record)
        with self._locked():
            path = self._path(record.obligation_sha256)
            if path.exists():
                return None
            return self._write(record)

    def compare_exchange(self, before, after):
        _record(before)
        _record(after)
        if before.obligation_sha256 != after.obligation_sha256:
            raise JournalRefused("journal_cas_binding_mismatch")
        with self._locked():
            current = self._read(before.obligation_sha256)
            if current != before:
                return None
            return self._write(after)


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
    if value.obligation is not None:
        if type(value.obligation) is not dict:
            raise JournalRefused("journal_obligation_invalid")
        try:
            calculated = hashlib.sha256(
                canonical(value.obligation)).hexdigest()
        except (TypeError, ValueError):
            raise JournalRefused("journal_obligation_invalid") from None
        if calculated != value.obligation_sha256:
            raise JournalRefused("journal_obligation_digest_mismatch")
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
            record = SettlementRecord(
                digest, "attempted", 0,
                obligation=obligation_payload(obligation))
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
            if record.obligation != obligation_payload(obligation):
                return JournalOutcome("uncertain", "journal_obligation_mismatch")
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

    def pending(self):
        """Return durable attempts for a restarted trusted owner to probe."""
        records = self.backend.pending()
        return tuple(_record(value) for value in records
                     if value.state == "attempted")
