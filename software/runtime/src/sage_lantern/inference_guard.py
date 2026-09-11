"""Durable uncertainty markers; provisioning and model-baseline ownership are external.

An empty directory is evidence only after all producers are fenced and drained,
and against a root-verified model invocation with no untracked admissions.
Directories must already exist. Orphans are never expired or automatically cleared.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import os
from pathlib import Path
import re
import stat
import uuid


MAX_PENDING = 1024


class InferenceGuardError(RuntimeError):
    def __init__(self):
        super().__init__("inference_guard_unavailable")


@contextmanager
def _directory(directory, owner_uid):
    descriptor = None
    try:
        path = Path(directory)
        if not path.is_absolute() or ".." in path.parts:
            raise InferenceGuardError()
        descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
        for component in path.parts[1:]:
            child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if metadata.st_uid != owner_uid or stat.S_IMODE(metadata.st_mode) != 0o700:
            raise InferenceGuardError()
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield descriptor
    except (OSError, ValueError, TypeError):
        raise InferenceGuardError() from None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _marker(descriptor, name, owner_uid):
    if re.fullmatch(r"[0-9a-f]{32}", name) is None:
        raise InferenceGuardError()
    marker = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
    try:
        metadata = os.fstat(marker)
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != owner_uid
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
                or metadata.st_size != 33 or os.read(marker, 34) != (name + "\n").encode("ascii")):
            raise InferenceGuardError()
    finally:
        os.close(marker)


def _count(descriptor, owner_uid):
    count = 0
    with os.scandir(descriptor) as entries:
        for entry in entries:
            count += 1
            if count > MAX_PENDING:
                raise InferenceGuardError()
            _marker(descriptor, entry.name, owner_uid)
    return count


def pending_count(directory, *, owner_uid):
    """Read-only, strict count; errors never mean empty. Call after producer stop."""
    with _directory(directory, owner_uid) as descriptor:
        return _count(descriptor, owner_uid)


@dataclass(frozen=True)
class PendingInference:
    directory: str
    request_id: str
    owner_uid: int
    device: int
    inode: int

    def complete(self):
        """Return True after durable removal; caller verifies terminal and HTTP closure."""
        with _directory(self.directory, self.owner_uid) as descriptor:
            metadata = os.fstat(descriptor)
            if (metadata.st_dev, metadata.st_ino) != (self.device, self.inode):
                raise InferenceGuardError()
            _marker(descriptor, self.request_id, self.owner_uid)
            os.unlink(self.request_id, dir_fd=descriptor)
            os.fsync(descriptor)
        return True


def begin(directory, *, request_id=None, require_empty=False):
    """Optionally require zero pending work atomically with marker creation."""
    if type(require_empty) is not bool:
        raise InferenceGuardError()
    if request_id is not None and (type(request_id) is not str or re.fullmatch(r"[0-9a-f]{32}", request_id) is None):
        raise InferenceGuardError()
    owner_uid = os.geteuid()
    with _directory(directory, owner_uid) as descriptor:
        count = _count(descriptor, owner_uid)
        if count >= MAX_PENDING or require_empty and count != 0:
            raise InferenceGuardError()
        request_id = uuid.uuid4().hex if request_id is None else request_id
        marker = os.open(request_id, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=descriptor)
        try:
            os.fchmod(marker, 0o600)
            content = (request_id + "\n").encode("ascii")
            if os.write(marker, content) != len(content):
                raise InferenceGuardError()
            os.fsync(marker)
        finally:
            os.close(marker)
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        return PendingInference(str(directory), request_id, owner_uid, metadata.st_dev, metadata.st_ino)
