"""Independent monotonic seal for the settlement journal.

This source component does not claim hardware rollback resistance. The caller
must place the anchor on a separately protected monotonic store, or bind it to
a TPM/RPMB-equivalent owner, before treating it as a production trust anchor.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
import os
from pathlib import Path
import secrets
import stat

import fcntl


SCHEMA = "sage.lantern.settlement-anchor.v1"
_DIGEST = 64


class AnchorRefused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def journal_digest(records):
    """Digest the complete recoverable record set, not only record names."""
    rows = []
    for record in sorted(records, key=lambda value: value.obligation_sha256):
        rows.append({
            "obligation_sha256": record.obligation_sha256,
            "state": record.state,
            "revision": record.revision,
            "proof_sha256": record.proof_sha256,
            "receipt_sha256": record.receipt_sha256,
            "obligation": record.obligation,
        })
    return hashlib.sha256(canonical(rows)).hexdigest()


@dataclass(frozen=True)
class AnchorValue:
    generation: int
    journal_sha256: str
    record_count: int
    release_sha256: str | None = None


def _anchor(value):
    if (type(value) is not AnchorValue
            or type(value.generation) is not int or value.generation < 1
            or type(value.journal_sha256) is not str
            or len(value.journal_sha256) != _DIGEST
            or type(value.record_count) is not int or value.record_count < 0
            or (value.release_sha256 is not None
                and (type(value.release_sha256) is not str
                     or len(value.release_sha256) != _DIGEST))):
        raise AnchorRefused("anchor_value_invalid")
    return value


class FileAnchorStore:
    """Private root-owned CAS store for one independent anchor."""
    MAX_BYTES = 4096

    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise AnchorRefused("anchor_directory_not_private")
        self.path = self.root / "anchor.json"
        self.lock_path = self.root / ".lock"

    def _locked(self):
        return _Lock(self.lock_path)

    def read(self):
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError:
            return None
        if len(raw) > self.MAX_BYTES:
            raise AnchorRefused("anchor_too_large")
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise AnchorRefused("anchor_decode") from None
        if type(value) is not dict or set(value) != {
                "schema", "generation", "journal_sha256", "record_count",
                "release_sha256"} or value["schema"] != SCHEMA:
            raise AnchorRefused("anchor_schema_invalid")
        return _anchor(AnchorValue(
            value["generation"], value["journal_sha256"],
            value["record_count"], value["release_sha256"]))

    def create(self, value):
        _anchor(value)
        with self._locked():
            if self.path.exists():
                return None
            self._write(value)
            return value

    def compare_exchange(self, before, after):
        _anchor(before)
        _anchor(after)
        if before.generation != after.generation or after.generation != before.generation + 1:
            raise AnchorRefused("anchor_generation_mismatch")
        with self._locked():
            if self.read() != before:
                return None
            self._write(after)
            return after

    def _write(self, value):
        payload = canonical({
            "schema": SCHEMA,
            "generation": value.generation,
            "journal_sha256": value.journal_sha256,
            "record_count": value.record_count,
            "release_sha256": value.release_sha256,
        }) + b"\n"
        if len(payload) > self.MAX_BYTES:
            raise AnchorRefused("anchor_too_large")
        temporary = self.root / (
            f".anchor.{os.getpid()}.{secrets.token_hex(6)}.tmp")
        descriptor = None
        try:
            descriptor = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(descriptor, payload)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.path)
            directory = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass


class _Lock:
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self.fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *_):
        fcntl.flock(self.fd, fcntl.LOCK_UN)
        os.close(self.fd)
        self.fd = None


def advance(store, records, *, release_sha256=None):
    """Compare the protected seal with the current recoverable record set."""
    digest = journal_digest(records)
    before = store.read()
    if before is None:
        value = AnchorValue(1, digest, len(records), release_sha256)
        return store.create(value)
    if (before.journal_sha256 != digest or before.record_count != len(records)
            or before.release_sha256 != release_sha256):
        raise AnchorRefused("anchor_journal_mismatch")
    return before
