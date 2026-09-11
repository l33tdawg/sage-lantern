from __future__ import annotations

import fcntl
import json
import os
import secrets
import stat
import unicodedata
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from pathlib import Path


MAX_REVISION = (1 << 63) - 1


class ProfileError(ValueError):
    pass


class ProfileRevisionConflict(ProfileError):
    pass


def normalize_display_name(name: str) -> str:
    if type(name) is not str:
        raise ProfileError("Display name must be text")
    if any(unicodedata.category(character).startswith("C") or unicodedata.category(character) in {"Zl", "Zp"} for character in name):
        raise ProfileError("Display name cannot contain control or formatting characters")
    normalized = " ".join(unicodedata.normalize("NFC", name).split())
    if not 1 <= len(normalized) <= 40 or len(normalized.encode("utf-8")) > 160:
        raise ProfileError("Display name must contain 1–40 characters and at most 160 UTF-8 bytes")
    return normalized


@dataclass(frozen=True)
class DeviceProfile:
    version: int = 1
    configured: bool = False
    display_name: str = ""
    public_discoverable: bool = False
    revision: int = 0

    @property
    def setup_required(self) -> bool:
        return not self.configured

    def to_dict(self) -> dict:
        return {**asdict(self), "setup_required": self.setup_required}


class DeviceProfileStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    @staticmethod
    def _private(metadata: os.stat_result, mode: int, *, directory: bool = False) -> None:
        valid_type = stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)
        if not valid_type or stat.S_IMODE(metadata.st_mode) != mode or metadata.st_uid != os.geteuid():
            raise ProfileError("Unsafe profile storage ownership, permissions or type")
        if not directory and metadata.st_nlink != 1:
            raise ProfileError("Profile storage must not be hard linked")

    @contextmanager
    def _locked(self):
        try:
            self.directory.mkdir(mode=0o700)
        except FileExistsError:
            pass
        directory = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            self._private(os.fstat(directory), 0o700, directory=True)
            lock = os.open(".profile.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
            try:
                self._private(os.fstat(lock), 0o600)
                fcntl.flock(lock, fcntl.LOCK_EX)
                yield directory
            finally:
                os.close(lock)
        finally:
            os.close(directory)

    @staticmethod
    def _unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ProfileError("Duplicate profile field")
            result[key] = value
        return result

    def _load(self, directory: int) -> DeviceProfile:
        try:
            descriptor = os.open("profile.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        except FileNotFoundError:
            return DeviceProfile()
        with os.fdopen(descriptor, "rb") as source:
            self._private(os.fstat(source.fileno()), 0o600)
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ProfileError("Profile exceeds storage limit")
        try:
            value = json.loads(raw.decode("utf-8"), object_pairs_hook=self._unique_object)
        except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
            raise ProfileError("Corrupt device profile") from error
        if type(value) is not dict or set(value) != set(DeviceProfile.__dataclass_fields__):
            raise ProfileError("Invalid profile schema")
        if type(value["version"]) is not int or value["version"] != 1:
            raise ProfileError("Unsupported profile version")
        if type(value["configured"]) is not bool or type(value["public_discoverable"]) is not bool:
            raise ProfileError("Invalid profile state")
        if type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION:
            raise ProfileError("Invalid profile revision")
        if value["configured"]:
            if value["revision"] == 0 or normalize_display_name(value["display_name"]) != value["display_name"]:
                raise ProfileError("Invalid configured profile")
        elif value["display_name"] != "" or value["revision"] != 0:
            raise ProfileError("Invalid unconfigured profile")
        return DeviceProfile(**value)

    def load(self) -> DeviceProfile:
        with self._locked() as directory:
            return self._load(directory)

    def _save(self, directory: int, profile: DeviceProfile) -> None:
        temporary = ".profile-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                os.fchmod(target.fileno(), 0o600)
                json.dump(asdict(profile), target, ensure_ascii=False, sort_keys=True)
                target.write("\n")
                target.flush()
                os.fsync(target.fileno())
            os.replace(temporary, "profile.json", src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass

    def _change(self, name: str, expected_revision: int | None, *, setup: bool) -> DeviceProfile:
        normalized = normalize_display_name(name)
        if expected_revision is not None and (type(expected_revision) is not int or not 0 <= expected_revision <= MAX_REVISION):
            raise ProfileError("Invalid expected revision")
        with self._locked() as directory:
            current = self._load(directory)
            if expected_revision is not None and expected_revision != current.revision:
                raise ProfileRevisionConflict("Profile revision changed; reload before retrying")
            if not current.configured and not setup:
                raise ProfileError("Complete setup before renaming")
            if current.configured:
                if normalized == current.display_name:
                    return current
                if setup:
                    raise ProfileError("Profile is already configured; use rename")
            if current.revision == MAX_REVISION:
                raise ProfileError("Profile revision exhausted")
            updated = replace(current, configured=True, display_name=normalized, revision=current.revision + 1)
            self._save(directory, updated)
            return updated

    def complete_setup(self, name: str, expected_revision: int | None = None) -> DeviceProfile:
        return self._change(name, expected_revision, setup=True)

    def rename(self, name: str, expected_revision: int | None = None) -> DeviceProfile:
        return self._change(name, expected_revision, setup=False)
