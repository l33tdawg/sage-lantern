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
import re
import secrets
import stat
import subprocess
import tempfile

import fcntl


SCHEMA = "sage.lantern.settlement-anchor.v1"
OWNER_SCHEMA = "sage.lantern.settlement-owner.v1"
_DIGEST = 64
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_OWNER = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")


class AnchorRefused(ValueError):
    pass


class CounterRefused(RuntimeError):
    pass


@dataclass(frozen=True)
class CounterValue:
    value: int


class UnavailableCounter:
    """Default: no separately protected monotonic counter exists."""
    def read(self):
        raise CounterRefused("monotonic_counter_unavailable")

    def advance(self, expected):
        raise CounterRefused("monotonic_counter_unavailable")


class Tpm2NvCounter:
    """TPM2 NV-counter adapter; provisioning and TPM acceptance are external."""
    def __init__(self, index, *, nvread="/usr/bin/tpm2_nvread",
                 nvincrement="/usr/bin/tpm2_nvincrement", timeout=10,
                 runner=None):
        if (type(index) is not int or index < 0 or index > 0xFFFFFFFF
                or not Path(nvread).is_absolute()
                or not Path(nvincrement).is_absolute()
                or type(timeout) is not int or timeout < 1):
            raise CounterRefused("tpm2_counter_config_invalid")
        self.index = index
        self.nvread = nvread
        self.nvincrement = nvincrement
        self.timeout = timeout
        self.runner = runner or subprocess.run

    @property
    def selector(self):
        return f"0x{self.index:08x}"

    def _run(self, argv):
        result = self.runner(
            argv, check=False, capture_output=True, timeout=self.timeout)
        if type(result.returncode) is not int or result.returncode != 0:
            raise CounterRefused("tpm2_command_failed")

    def read(self):
        descriptor, temporary = tempfile.mkstemp(prefix="sage-tpm-counter-")
        os.close(descriptor)
        try:
            self._run([self.nvread, "-C", "o", "-x", self.selector,
                       "-s", "8", "-o", temporary])
            try:
                raw = Path(temporary).read_bytes()
            except OSError:
                raise CounterRefused("tpm2_counter_read_failed") from None
            if len(raw) != 8:
                raise CounterRefused("tpm2_counter_size_invalid")
            return CounterValue(int.from_bytes(raw, "big"))
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass

    def advance(self, expected):
        if type(expected) is not int or expected < 0:
            raise CounterRefused("tpm2_counter_expected_invalid")
        self._run([self.nvincrement, "-C", "o", "-x", self.selector])
        value = self.read().value
        if value != expected + 1:
            raise CounterRefused("tpm2_counter_not_advanced")
        return CounterValue(value)


@dataclass(frozen=True)
class OwnerIdentity:
    """Identity loaded from one protected owner policy."""
    machine_id: str
    owner_id: str
    executable_sha256: str
    uid: int


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _owner(value):
    if (type(value) is not OwnerIdentity
            or type(value.machine_id) is not str
            or not _MACHINE.fullmatch(value.machine_id)
            or type(value.owner_id) is not str
            or not _OWNER.fullmatch(value.owner_id)
            or type(value.executable_sha256) is not str
            or len(value.executable_sha256) != _DIGEST
            or type(value.uid) is not int or value.uid < 0):
        raise AnchorRefused("owner_identity_invalid")
    return value


def owner_digest(owner):
    value = _owner(owner)
    return hashlib.sha256(canonical({
        "machine_id": value.machine_id,
        "owner_id": value.owner_id,
        "executable_sha256": value.executable_sha256,
        "uid": value.uid,
    })).hexdigest()


def load_owner(path, expected_uid):
    """Load one owner policy from an exact private regular file."""
    path = Path(path)
    if not path.is_absolute() or expected_uid < 0:
        raise AnchorRefused("owner_policy_path_invalid")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise AnchorRefused("owner_policy_unavailable") from None
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != expected_uid
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1 or before.st_size < 1
                or before.st_size > 4096):
            raise AnchorRefused("owner_policy_identity_invalid")
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)):
            raise AnchorRefused("owner_policy_changed")
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError) as error:
        if isinstance(error, AnchorRefused):
            raise
        raise AnchorRefused("owner_policy_decode") from None
    finally:
        os.close(descriptor)
    if type(value) is not dict or set(value) != {
            "schema", "machine_id", "owner_id", "executable_sha256", "uid"}:
        raise AnchorRefused("owner_policy_schema_invalid")
    if value["schema"] != OWNER_SCHEMA:
        raise AnchorRefused("owner_policy_schema_invalid")
    owner = _owner(OwnerIdentity(
        value["machine_id"], value["owner_id"],
        value["executable_sha256"], value["uid"]))
    if owner.uid != expected_uid:
        raise AnchorRefused("owner_policy_uid_mismatch")
    return owner


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


def release_set_digest(records):
    """Digest the full set of outstanding, pre-release obligations."""
    pending = sorted(
        record.obligation_sha256 for record in records
        if record.state == "attempted")
    return hashlib.sha256(canonical(pending)).hexdigest()


@dataclass(frozen=True)
class AnchorValue:
    generation: int
    journal_sha256: str
    record_count: int
    release_sha256: str | None = None
    owner_sha256: str | None = None
    counter_value: int | None = None


def _anchor(value):
    if (type(value) is not AnchorValue
            or type(value.generation) is not int or value.generation < 1
            or type(value.journal_sha256) is not str
            or len(value.journal_sha256) != _DIGEST
            or type(value.record_count) is not int or value.record_count < 0
            or (value.release_sha256 is not None
                and (type(value.release_sha256) is not str
                     or len(value.release_sha256) != _DIGEST))
            or (value.owner_sha256 is not None
                and (type(value.owner_sha256) is not str
                     or len(value.owner_sha256) != _DIGEST))
            or (value.counter_value is not None
                and (type(value.counter_value) is not int
                     or value.counter_value < 0))):
        raise AnchorRefused("anchor_value_invalid")
    return value


class FileAnchorStore:
    """Private root-owned CAS store for one independent anchor."""
    MAX_BYTES = 4096

    def __init__(self, root, owner=None, counter=None):
        self.root = Path(root)
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        info = self.root.stat()
        if stat.S_IMODE(info.st_mode) != 0o700 or info.st_uid != os.geteuid():
            raise AnchorRefused("anchor_directory_not_private")
        self.path = self.root / "anchor.json"
        self.lock_path = self.root / ".lock"
        self.owner_sha256 = None if owner is None else owner_digest(owner)
        self.counter = counter

    def _counter_value(self):
        if self.counter is None:
            return None
        try:
            value = self.counter.read()
        except (CounterRefused, OSError):
            raise AnchorRefused("anchor_counter_unavailable") from None
        if (type(value) is not CounterValue or type(value.value) is not int
                or value.value < 0):
            raise AnchorRefused("anchor_counter_invalid")
        return value

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
        required = {"schema", "generation", "journal_sha256", "record_count",
                    "release_sha256"}
        if (type(value) is not dict
                or not required.issubset(value)
                or set(value) - required - {"owner_sha256", "counter_value"}
                or value["schema"] != SCHEMA):
            raise AnchorRefused("anchor_schema_invalid")
        result = _anchor(AnchorValue(
            value["generation"], value["journal_sha256"],
            value["record_count"], value["release_sha256"],
            value.get("owner_sha256"), value.get("counter_value")))
        if (self.owner_sha256 is not None
                and result.owner_sha256 != self.owner_sha256):
            raise AnchorRefused("anchor_owner_mismatch")
        counter = self._counter_value()
        if counter is not None and result.counter_value != counter.value:
            raise AnchorRefused("anchor_counter_mismatch")
        return result

    def create(self, value):
        _anchor(value)
        with self._locked():
            if self.path.exists():
                return None
            counter = self._counter_value()
            if counter is not None:
                if (value.counter_value is not None
                        and value.counter_value != counter.value):
                    raise AnchorRefused("anchor_counter_mismatch")
                value = replace(value, counter_value=counter.value)
            self._write(value)
            return value

    def compare_exchange(self, before, after):
        _anchor(before)
        _anchor(after)
        if after.generation != before.generation + 1:
            raise AnchorRefused("anchor_generation_mismatch")
        with self._locked():
            if self.read() != before:
                return None
            if self.counter is not None:
                counter = self._counter_value()
                if counter is None or before.counter_value != counter.value:
                    raise AnchorRefused("anchor_counter_mismatch")
                try:
                    advanced = self.counter.advance(counter.value)
                except (CounterRefused, OSError):
                    raise AnchorRefused("anchor_counter_advance_failed") from None
                if (type(advanced) is not CounterValue
                        or type(advanced.value) is not int
                        or advanced.value != counter.value + 1):
                    raise AnchorRefused("anchor_counter_not_advanced")
                if (after.counter_value is not None
                        and after.counter_value != advanced.value):
                    raise AnchorRefused("anchor_counter_mismatch")
                after = replace(after, counter_value=advanced.value)
            self._write(after)
            return after

    def _write(self, value):
        payload = canonical({
            "schema": SCHEMA,
            "generation": value.generation,
            "journal_sha256": value.journal_sha256,
            "record_count": value.record_count,
            "release_sha256": value.release_sha256,
            "owner_sha256": value.owner_sha256,
            "counter_value": value.counter_value,
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


def advance(store, records, *, release_sha256=None, owner_sha256=None):
    """Compare the protected seal with the current recoverable record set."""
    digest = journal_digest(records)
    before = store.read()
    if before is None:
        value = AnchorValue(
            1, digest, len(records), release_sha256, owner_sha256)
        return store.create(value)
    if (before.journal_sha256 != digest or before.record_count != len(records)
            or before.release_sha256 != release_sha256
            or before.owner_sha256 != owner_sha256):
        raise AnchorRefused("anchor_journal_mismatch")
    return before
