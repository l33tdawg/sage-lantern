"""Trusted local suspend fence/checkpoint storage; no OS or auth operations.

Production uses root and /run/sage-lantern-suspend. /run survives process death,
not reboot: boot admission must independently start locked. Tests may inject an
owner UID and absolute private directory. Ancestors must be trusted real
directories without ACL grants/untrusted replacement. Mode checks do not defend
against root or malicious same-UID writers. Use a local flock/fsync filesystem.

Hold this journal for the complete transition and recovery (exclusive lifetime
directory flock). Any exception means fenced/unavailable, never permission to
start auth or sleep. Successful checkpoint first durably establishes `active`,
then atomically replaces `checkpoint.json`. No close/exit path clears anything.
clear requires the exact persisted terminal snapshot and a new trusted caller
verification, not a stored flag, HTTP assertion or mere process exit status.

Each checkpoint re-arms the fence, including completed=True. The coordinator
persists its terminal checkpoint before releasing the fence and starting auth.
Restoring any snapshot still requires fresh terminal verification. This module
does not turn checkpoint persistence into an OS/action acknowledgement.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import re
import stat
import threading
from dataclasses import dataclass
from uuid import uuid4

from .suspend_transition import Phase, Snapshot


DIRECTORY = Path("/run/sage-lantern-suspend")
MAX_BYTES = 4096
TERMINAL = {Phase.RESUMED, Phase.ABORTED_VERIFIED}


class SuspendJournalError(RuntimeError):
    """Content/path-free error; caller must preserve admission fencing."""


@dataclass(frozen=True)
class JournalState:
    active: bool
    needs_recovery: bool
    snapshot: Snapshot | None
    transition_id: str | None


def _id(value):
    if type(value) is not str or re.fullmatch(r"[A-Za-z0-9_-]{8,128}", value) is None:
        raise SuspendJournalError("invalid_snapshot")
    return value


def _snapshot(value):
    if (type(value) is not Snapshot or type(value.phase) is not Phase
            or type(value.revision) is not int or not 0 <= value.revision <= 9007199254740991
            or type(value.prepared) is not bool or type(value.completed) is not bool
            or (value.prepared and value.phase is not Phase.PREPARING)
            or (value.completed and value.phase not in TERMINAL)):
        raise SuspendJournalError("invalid_snapshot")
    _id(value.transition_id)
    return value


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError
        value[key] = item
    return value


class SuspendJournal:
    def __init__(self, *, directory: Path = DIRECTORY, owner_uid: int = 0):
        self._fd = None
        self._guard = threading.RLock()
        self.needs_recovery = True
        self._uncertain = False
        self.directory = directory
        self.owner_uid = owner_uid
        try:
            if (type(owner_uid) is not int or owner_uid < 0 or os.geteuid() != owner_uid
                    or not isinstance(directory, Path) or not directory.is_absolute()):
                raise SuspendJournalError("unsafe_configuration")
            for parent in directory.parents:
                if not stat.S_ISDIR(parent.lstat().st_mode):
                    raise SuspendJournalError("unsafe_storage")
            try:
                directory.mkdir(mode=0o700)
            except FileExistsError:
                pass
            self._fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            info = os.fstat(self._fd)
            self._identity = (info.st_dev, info.st_ino)
            self._check_directory()
            try:
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SuspendJournalError("journal_busy") from None
            parent_fd = os.open(directory.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(parent_fd)
            finally:
                os.close(parent_fd)
            self.load()
        except (OSError, ValueError, SuspendJournalError):
            self.close()
            raise SuspendJournalError("journal_unavailable") from None

    def _check_directory(self):
        if self._fd is None:
            raise SuspendJournalError("journal_closed")
        info = self.directory.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != self.owner_uid
                or stat.S_IMODE(info.st_mode) != 0o700
                or (info.st_dev, info.st_ino) != self._identity
                or self.directory.resolve(strict=True) != self.directory):
            raise SuspendJournalError("unsafe_storage")

    def _file(self, info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner_uid
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > MAX_BYTES):
            raise SuspendJournalError("unsafe_storage")

    def _read(self, name):
        try:
            before = os.stat(name, dir_fd=self._fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        self._file(before)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self._fd)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            self._file(opened)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise SuspendJournalError("unsafe_storage")
            raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SuspendJournalError("journal_corrupt")
        try:
            value = json.loads(raw, object_pairs_hook=_unique,
                               parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise SuspendJournalError("journal_corrupt") from None
        if type(value) is not dict or type(value.get("version")) is not int or value["version"] != 1:
            raise SuspendJournalError("journal_corrupt")
        return value

    def _load(self):
        self._check_directory()
        active = self._read("active")
        if active is not None:
            if set(active) != {"version", "transition_id"}:
                raise SuspendJournalError("journal_corrupt")
            _id(active["transition_id"])
        record = self._read("checkpoint.json")
        snapshot = None
        if record is not None:
            if set(record) != {"version", "transition_id", "phase", "revision", "prepared", "completed"}:
                raise SuspendJournalError("journal_corrupt")
            try:
                snapshot = _snapshot(Snapshot(record["transition_id"], Phase(record["phase"]),
                                              record["revision"], record["prepared"], record["completed"]))
            except (ValueError, TypeError):
                raise SuspendJournalError("journal_corrupt") from None
        if active is None and snapshot is not None and snapshot.phase not in TERMINAL:
            raise SuspendJournalError("missing_active_fence")
        if active is not None and snapshot is not None and active["transition_id"] != snapshot.transition_id:
            if snapshot.phase not in TERMINAL:
                raise SuspendJournalError("transition_conflict")
            snapshot = None
        transition_id = active["transition_id"] if active is not None else snapshot.transition_id if snapshot else None
        return JournalState(active is not None, active is not None or self._uncertain, snapshot, transition_id)

    def load(self):
        with self._guard:
            self.needs_recovery = True
            try:
                result = self._load()
                self.needs_recovery = result.needs_recovery
                return result
            except OSError:
                self._uncertain = True
                raise SuspendJournalError("journal_unavailable") from None
            except SuspendJournalError:
                self._uncertain = True
                raise

    def _write(self, name, value):
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
        if len(encoded) > MAX_BYTES:
            raise SuspendJournalError("journal_corrupt")
        self._read(name)
        temporary = ".pending-" + str(uuid4())
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self._fd)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=self._fd, dst_dir_fd=self._fd)
            os.fsync(self._fd)
        finally:
            try:
                os.unlink(temporary, dir_fd=self._fd)
            except FileNotFoundError:
                pass

    def retain_fence(self, transition_id):
        _id(transition_id)
        with self._guard:
            self.needs_recovery = True
            try:
                state = self._load()
                active = self._read("active")
                if active is not None and active["transition_id"] != transition_id:
                    raise SuspendJournalError("transition_conflict")
                if state.snapshot is not None and state.snapshot.transition_id != transition_id and (state.active or state.snapshot.phase not in TERMINAL or state.needs_recovery):
                    raise SuspendJournalError("transition_conflict")
                self._write("active", dict(version=1, transition_id=transition_id))
            except OSError:
                self._uncertain = True
                raise SuspendJournalError("journal_unavailable") from None

    def checkpoint(self, snapshot: Snapshot):
        _snapshot(snapshot)
        with self._guard:
            self.needs_recovery = True
            try:
                state = self._load()
                current = state.snapshot
                if current is not None:
                    if current.transition_id != snapshot.transition_id:
                        if state.active or current.phase not in TERMINAL or state.needs_recovery:
                            raise SuspendJournalError("checkpoint_conflict")
                    elif (snapshot.revision < current.revision
                          or (snapshot.revision == current.revision and snapshot != current)):
                        raise SuspendJournalError("checkpoint_conflict")
                self.retain_fence(snapshot.transition_id)
                self._write("checkpoint.json", dict(version=1, transition_id=snapshot.transition_id,
                    phase=snapshot.phase.value, revision=snapshot.revision,
                    prepared=snapshot.prepared, completed=snapshot.completed))
            except OSError:
                self._uncertain = True
                raise SuspendJournalError("journal_unavailable") from None

    def clear(self, snapshot: Snapshot, *, verified: bool = False):
        _snapshot(snapshot)
        if verified is not True or snapshot.phase not in TERMINAL:
            raise SuspendJournalError("terminal_verification_required")
        with self._guard:
            self.needs_recovery = True
            try:
                if self._load().snapshot != snapshot:
                    raise SuspendJournalError("checkpoint_conflict")
                try:
                    os.unlink("active", dir_fd=self._fd)
                except FileNotFoundError:
                    pass
                os.fsync(self._fd)
                self._uncertain = False
                self.needs_recovery = False
            except OSError:
                self._uncertain = True
                raise SuspendJournalError("journal_unavailable") from None

    def close(self):
        with self._guard:
            if self._fd is not None:
                os.close(self._fd)
                self._fd = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
