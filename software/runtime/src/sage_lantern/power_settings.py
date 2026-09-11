"""Private revisioned power preferences; no suspend execution or applied state.

Directory is trusted service configuration under the auth state directory, not
request input. Canonical parents must be trusted against replacement and ACL
grants; POSIX checks do not isolate malicious same-UID/root processes. Atomic
replacement plus flock serializes readers/writers across processes. A failure
after replace may have committed: reconcile by load, never blindly retry.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
import time
import uuid
from contextlib import contextmanager
from pathlib import Path


MAX_REVISION = (1 << 53) - 1
DISPLAY_DELAYS = (30, 60, 120, 300)
SLEEP_DELAYS = (300, 600, 900, 1800)
LOCK_DELAYS = (60, 120, 300, 600, 900)


class PowerSettingsError(ValueError):
    def __init__(self):
        super().__init__("power_settings_unavailable")


class PowerRevisionConflict(PowerSettingsError):
    pass


def validate_policy(value: object) -> dict:
    if (type(value) is not dict
            or set(value) != {"revision", "display_off_seconds", "deep_sleep_seconds", "deep_sleep_available"}
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION
            or type(value["display_off_seconds"]) is not int or value["display_off_seconds"] not in DISPLAY_DELAYS
            or type(value["deep_sleep_seconds"]) is not int or value["deep_sleep_seconds"] not in SLEEP_DELAYS
            or value["deep_sleep_seconds"] <= value["display_off_seconds"]
            or value["deep_sleep_available"] is not False):
        raise PowerSettingsError()
    return dict(value)


def _unique(pairs):
    value = {}
    for name, member in pairs:
        if name in value:
            raise PowerSettingsError()
        value[name] = member
    return value


def validate_lock_policy(value):
    if (type(value) is not dict or set(value) != {"revision", "lock_seconds"}
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION
            or type(value["lock_seconds"]) is not int or value["lock_seconds"] not in LOCK_DELAYS):
        raise PowerSettingsError()
    return dict(value)


class PowerSettingsStore:
    filename = "power.json"
    validate = staticmethod(validate_policy)

    def defaults(self):
        return dict(revision=0, display_off_seconds=30,
                    deep_sleep_seconds=300, deep_sleep_available=False)

    def __init__(self, directory: Path):
        self.directory = directory

    @staticmethod
    def _safe(metadata, mode, *, directory=False):
        valid_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if (not valid_type or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != mode
                or (not directory and metadata.st_nlink != 1)):
            raise PowerSettingsError()

    @contextmanager
    def _locked(self):
        directory = lock = None
        try:
            if (not isinstance(self.directory, Path) or not self.directory.is_absolute()
                    or self.directory.resolve() != self.directory):
                raise PowerSettingsError()
            self.directory.mkdir(mode=0o700, exist_ok=True)
            directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._safe(os.fstat(directory), 0o700, directory=True)
            lock = os.open("power.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK,
                           0o600, dir_fd=directory)
            self._safe(os.fstat(lock), 0o600)
            deadline = time.monotonic() + 0.25
            while True:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise PowerSettingsError()
                    time.sleep(0.005)
            yield directory
        except PowerSettingsError:
            raise
        except Exception:
            raise PowerSettingsError() from None
        finally:
            if lock is not None:
                os.close(lock)
            if directory is not None:
                os.close(directory)

    def _load(self, directory):
        try:
            descriptor = os.open(self.filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return self.validate(self.defaults())
        with os.fdopen(descriptor, "rb") as stream:
            self._safe(os.fstat(stream.fileno()), 0o600)
            raw = stream.read(1025)
        if len(raw) > 1024:
            raise PowerSettingsError()
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique)
        if type(value) is not dict or type(value.get("version")) is not int or value.pop("version") != 1:
            raise PowerSettingsError()
        return self.validate(value)

    def load(self) -> dict:
        with self._locked() as directory:
            return self._load(directory)

    def update(self, *, expected_revision: int, display_off_seconds: int,
               deep_sleep_seconds: int, authorize=None) -> dict:
        candidate = validate_policy(dict(revision=expected_revision, display_off_seconds=display_off_seconds,
                                         deep_sleep_seconds=deep_sleep_seconds, deep_sleep_available=False))
        return self._update(candidate, authorize)

    def _update(self, candidate, authorize):
        expected_revision = candidate["revision"]
        with self._locked() as directory:
            current = self._load(directory)
            if current["revision"] != expected_revision:
                raise PowerRevisionConflict()
            if expected_revision == MAX_REVISION:
                raise PowerSettingsError()
            candidate["revision"] += 1
            temporary = ".power-" + uuid.uuid4().hex
            try:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=directory)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(json.dumps({"version": 1, **candidate}, separators=(",", ":")).encode())
                    stream.flush()
                    os.fsync(stream.fileno())
                if authorize is not None and authorize() is not True:
                    raise PowerSettingsError()
                os.replace(temporary, self.filename, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
            return candidate


class LockSettingsStore(PowerSettingsStore):
    filename = "lock.json"
    validate = staticmethod(validate_lock_policy)

    def defaults(self):
        return dict(revision=0, lock_seconds=300)

    def update(self, *, expected_revision, lock_seconds, authorize=None):
        candidate = self.validate(dict(revision=expected_revision, lock_seconds=lock_seconds))
        return self._update(candidate, authorize)
