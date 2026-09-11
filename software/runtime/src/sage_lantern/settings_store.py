"""DRAFT ONLY persistence; no accepted/applied desired state or authorization.

The constructor directory must come from trusted operator configuration, never a
request. Provision it beforehand with mode 0700, owned by the process operator,
on a local filesystem with SQLite locking/fsync support and trusted ancestors.
The fixed database stores only a draft revision and three optional booleans.
History, messaging and recovery are intentionally excluded from this slice.

POSIX mode/owner/link checks are not ACL validation, encryption, human identity
verification, or protection against a malicious same-UID process/root. Deployment
must exclude inherited ACL grants and untrusted writers. No default/live path,
network/UI API, schema migration, consent, collector or apply handler is provided.
Path checks assume trusted parents cannot be renamed/replaced between validation
and SQLite's path-based opens; they are not a race-proof filesystem sandbox.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Literal

from .settings import REGISTRY, Implementation, SettingId, SettingKind


DATABASE_NAME = "settings-drafts.sqlite3"
SCHEMA_VERSION = 1
MAX_REVISION = (1 << 63) - 1
MAX_FILE_BYTES = 1024 * 1024
DRAFTABLE_SETTINGS = (SettingId.VOICE, SettingId.PHOTO, SettingId.TTS)

_SCHEMA = """CREATE TABLE draft_state (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    revision INTEGER NOT NULL CHECK (typeof(revision) = 'integer' AND revision >= 0),
    voice_capture INTEGER CHECK (voice_capture IS NULL OR
        (typeof(voice_capture) = 'integer' AND voice_capture IN (0, 1))),
    photo_answers INTEGER CHECK (photo_answers IS NULL OR
        (typeof(photo_answers) = 'integer' AND photo_answers IN (0, 1))),
    spoken_responses INTEGER CHECK (spoken_responses IS NULL OR
        (typeof(spoken_responses) = 'integer' AND spoken_responses IN (0, 1)))
)"""


class DraftErrorCode(StrEnum):
    INVALID_DRAFT = "invalid_draft"
    INVALID_REVISION = "invalid_revision"
    REVISION_CONFLICT = "draft_revision_conflict"
    REVISION_EXHAUSTED = "draft_revision_exhausted"
    UNSAFE_STORAGE = "unsafe_draft_storage"
    UNSUPPORTED_SCHEMA = "unsupported_draft_schema"
    INVALID_STATE = "invalid_draft_state"
    UNAVAILABLE = "draft_store_unavailable"


class DraftStoreError(RuntimeError):
    def __init__(self, code: DraftErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    revision: int
    intent: Mapping[SettingId, bool]
    state: Literal["draft_only"] = field(default="draft_only", init=False)


class DraftSettingsStore:
    """One connection per instance/thread; CAS serializes independent writers.

    replace() replaces the entire draft, including clearing with an empty mapping.
    Every successful replacement advances the draft revision, even a no-op. After
    an uncertain commit/response, read the draft instead of blindly retrying.
    A snapshot's intent may feed project_settings(desired=...), but is neither an
    accepted desired revision nor an observation of effective state.
    """

    def __init__(self, *, directory: Path):
        self._connection: sqlite3.Connection | None = None
        self._directory_fd: int | None = None
        try:
            if not isinstance(directory, Path) or not directory.is_absolute():
                raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)
            self._directory = directory
            if directory.resolve(strict=True) != directory:
                raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)
            self._directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._directory_identity = os.fstat(self._directory_fd)
            self._check_directory()
            created = False
            try:
                descriptor = os.open(
                    DATABASE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600, dir_fd=self._directory_fd,
                )
            except FileExistsError:
                pass
            else:
                created = True
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                os.fsync(self._directory_fd)
            self._file_identity = self._check_file(DATABASE_NAME)
            self._check_files()
            self._connection = sqlite3.connect(
                (directory / DATABASE_NAME).as_uri() + "?mode=rw",
                uri=True, timeout=1.0, isolation_level=None,
            )
            self._connection.execute("PRAGMA trusted_schema = OFF")
            self._connection.execute("PRAGMA synchronous = FULL")
            with self._transaction(write=True):
                version = self._connection.execute("PRAGMA user_version").fetchone()[0]
                if created and version == 0:
                    if self._connection.execute("SELECT name FROM sqlite_master LIMIT 1").fetchone():
                        raise DraftStoreError(DraftErrorCode.UNSUPPORTED_SCHEMA)
                    self._connection.execute(_SCHEMA)
                    self._connection.execute("INSERT INTO draft_state VALUES (1, 0, NULL, NULL, NULL)")
                    self._connection.execute("PRAGMA user_version = 1")
                self._check_schema()
                self._snapshot()
        except Exception as error:
            self.close()
            if isinstance(error, DraftStoreError):
                raise
            raise DraftStoreError(DraftErrorCode.UNAVAILABLE) from None

    def _check_directory(self) -> None:
        metadata = self._directory.lstat()
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o700
            or (metadata.st_dev, metadata.st_ino)
            != (self._directory_identity.st_dev, self._directory_identity.st_ino)
            or self._directory.resolve(strict=True) != self._directory
        ):
            raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)

    def _check_file(self, name: str) -> os.stat_result:
        metadata = os.stat(name, dir_fd=self._directory_fd, follow_symlinks=False)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_nlink != 1
            or metadata.st_size > MAX_FILE_BYTES
        ):
            raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)
        return metadata

    def _check_files(self) -> None:
        self._check_directory()
        metadata = self._check_file(DATABASE_NAME)
        if (metadata.st_dev, metadata.st_ino) != (self._file_identity.st_dev, self._file_identity.st_ino):
            raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)
        for suffix in ("-journal", "-wal", "-shm"):
            try:
                self._check_file(DATABASE_NAME + suffix)
            except FileNotFoundError:
                continue
            if suffix != "-journal":
                raise DraftStoreError(DraftErrorCode.UNSAFE_STORAGE)

    def _check_schema(self) -> None:
        if self._connection.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
            raise DraftStoreError(DraftErrorCode.UNSUPPORTED_SCHEMA)
        if self._connection.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise DraftStoreError(DraftErrorCode.UNSUPPORTED_SCHEMA)
        objects = self._connection.execute("SELECT type, name, sql FROM sqlite_master LIMIT 2").fetchall()
        if objects != [("table", "draft_state", _SCHEMA)]:
            raise DraftStoreError(DraftErrorCode.UNSUPPORTED_SCHEMA)

    @contextmanager
    def _transaction(self, *, write: bool) -> Iterator[None]:
        try:
            if self._connection is None:
                raise DraftStoreError(DraftErrorCode.UNAVAILABLE)
            self._check_files()
            self._connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield
            self._check_files()
            self._connection.commit()
        except BaseException as error:
            if self._connection is not None:
                try:
                    self._connection.rollback()
                except sqlite3.Error:
                    self.close()
            if isinstance(error, DraftStoreError) or not isinstance(error, Exception):
                raise
            raise DraftStoreError(DraftErrorCode.UNAVAILABLE) from None

    def _snapshot(self) -> DraftSnapshot:
        rows = self._connection.execute(
            "SELECT singleton, revision, voice_capture, photo_answers, spoken_responses FROM draft_state LIMIT 2"
        ).fetchall()
        if len(rows) != 1:
            raise DraftStoreError(DraftErrorCode.INVALID_STATE)
        singleton, revision, *values = rows[0]
        if singleton != 1 or type(revision) is not int or not 0 <= revision <= MAX_REVISION:
            raise DraftStoreError(DraftErrorCode.INVALID_STATE)
        if any(value is not None and (type(value) is not int or value not in (0, 1)) for value in values):
            raise DraftStoreError(DraftErrorCode.INVALID_STATE)
        return DraftSnapshot(revision, MappingProxyType({
            setting_id: bool(value)
            for setting_id, value in zip(DRAFTABLE_SETTINGS, values)
            if value is not None
        }))

    def read(self) -> DraftSnapshot:
        with self._transaction(write=False):
            self._check_schema()
            snapshot = self._snapshot()
        return snapshot

    def replace(self, *, expected_revision: int, intent: Mapping[SettingId, bool]) -> DraftSnapshot:
        if type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION:
            raise DraftStoreError(DraftErrorCode.INVALID_REVISION)
        try:
            if not isinstance(intent, Mapping) or len(intent) > len(DRAFTABLE_SETTINGS):
                raise DraftStoreError(DraftErrorCode.INVALID_DRAFT)
            values = dict(intent)
        except Exception:
            raise DraftStoreError(DraftErrorCode.INVALID_DRAFT) from None
        for setting_id, value in values.items():
            if (
                not isinstance(setting_id, SettingId)
                or setting_id not in DRAFTABLE_SETTINGS
                or REGISTRY[setting_id].implementation is not Implementation.EXISTING
                or REGISTRY[setting_id].kind is SettingKind.ACTION
                or type(value) is not bool
            ):
                raise DraftStoreError(DraftErrorCode.INVALID_DRAFT)
        with self._transaction(write=True):
            self._check_schema()
            previous = self._snapshot()
            if previous.revision != expected_revision:
                raise DraftStoreError(DraftErrorCode.REVISION_CONFLICT)
            if previous.revision == MAX_REVISION:
                raise DraftStoreError(DraftErrorCode.REVISION_EXHAUSTED)
            self._connection.execute(
                "UPDATE draft_state SET revision = ?, voice_capture = ?, photo_answers = ?, spoken_responses = ? "
                "WHERE singleton = 1 AND revision = ?",
                (expected_revision + 1, *(int(values[setting_id]) if setting_id in values else None
                                         for setting_id in DRAFTABLE_SETTINGS), expected_revision),
            )
            snapshot = self._snapshot()
        return snapshot

    def close(self) -> None:
        try:
            if self._connection is not None:
                self._connection.close()
        finally:
            self._connection = None
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None

    def __enter__(self) -> DraftSettingsStore:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()
