"""Standalone captured-JPEG storage, not consent, history integration or an API.

Provision an absolute, owner-only 0700 directory with trusted ancestors first.
POSIX ownership/mode checks do not validate ACLs: deployment must exclude ACL
grants and untrusted writers. Same-node same-UID processes and root are trusted;
this is neither encryption nor a filesystem sandbox against those actors.
All cooperating instances lock the directory on a local POSIX filesystem with
flock, atomic rename and fsync support. External disk consumers can still race
free-space checks. No eviction, gallery or background capture runs. Recovery is
explicit through prune; constructing the store does not scan or remove records.

Each UUID-named record holds a version marker, SHA-256 and unchanged JPEG bytes.
The digest detects corruption, not malicious replacement by a trusted writer.
JPEG checks adapt the existing LlamaCppHTTP header parser to capture bounds;
they do not decode pixels or prove a complete, decodable JPEG entropy stream.
Original metadata is preserved too; publication must separately authorize/redact it.
A crash before rename can leave a private temporary record; after rename/fsync
failure the outcome is uncertain: get/retry the same ID rather than overwriting.
Deletion unlinks a record, not secure flash erasure or deletion of other copies.
"""

from __future__ import annotations

import fcntl
import hashlib
import hmac
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator
from uuid import UUID, uuid4


MAX_JPEG_BYTES = 2 * 1024 * 1024
DEFAULT_FREE_BYTES = 512 * 1024 * 1024
_MAGIC = b"LJPEG01\0"
_HEADER_BYTES = len(_MAGIC) + 32


class HistoryMediaError(RuntimeError):
    """Bounded error code; never includes image contents or filesystem paths."""


def _validate_jpeg(image: bytes, dimensions: tuple[int, int]) -> None:
    if type(image) is not bytes or not 4 <= len(image) <= MAX_JPEG_BYTES:
        raise HistoryMediaError("invalid_jpeg")
    if not image.startswith(b"\xff\xd8") or not image.endswith(b"\xff\xd9"):
        raise HistoryMediaError("invalid_jpeg")
    offset = 2
    components = None
    while offset < len(image) - 2:
        if image[offset] != 255:
            break
        while offset < len(image) and image[offset] == 255:
            offset += 1
        if offset >= len(image):
            break
        marker = image[offset]
        offset += 1
        if marker in (0, 1, 216, 217) or 208 <= marker <= 215 or offset + 2 > len(image) - 2:
            break
        length = int.from_bytes(image[offset:offset + 2], "big")
        if length < 2 or offset + length > len(image) - 2:
            break
        segment = image[offset + 2:offset + length]
        offset += length
        if marker in (192, 194):
            if components is not None or len(segment) < 6:
                break
            height = int.from_bytes(segment[1:3], "big")
            width = int.from_bytes(segment[3:5], "big")
            components = segment[5]
            if segment[0] != 8 or components not in (1, 3) or len(segment) != 6 + 3 * components:
                break
            if not (0 < width <= dimensions[0] and 0 < height <= dimensions[1]):
                break
        elif 192 <= marker <= 207 and marker not in (196, 200, 204):
            break
        if marker == 218:
            if (components is not None and segment and 1 <= segment[0] <= components
                    and len(segment) == 4 + 2 * segment[0] and offset < len(image) - 2):
                return
            break
    raise HistoryMediaError("invalid_jpeg")


class HistoryMediaStore:
    """put(UUID string, bytes) -> hex digest; get -> bytes; delete -> bool.

    IDs must be canonical lowercase hyphenated UUID strings. Missing get raises
    media_not_found; missing delete returns False. Equal put is idempotent;
    conflicting or corrupt existing records are never replaced. Instances open
    independent directory descriptors per operation, permitting thread/process
    serialization without a shared mutable connection or close lifecycle.
    """

    def __init__(self, *, directory: Path, dimensions: tuple[int, int] = (1920, 1080),
                 reserve_free_bytes: int = DEFAULT_FREE_BYTES):
        if (type(dimensions) is not tuple or len(dimensions) != 2
                or any(type(value) is not int for value in dimensions)
                or dimensions not in ((1920, 1080), (1280, 720))
                or type(reserve_free_bytes) is not int or reserve_free_bytes < 0):
            raise HistoryMediaError("invalid_configuration")
        if not isinstance(directory, Path) or not directory.is_absolute():
            raise HistoryMediaError("unsafe_storage")
        self._directory = directory
        self._dimensions = dimensions
        self._reserve = reserve_free_bytes
        self._identity = None
        with self._locked() as descriptor:
            metadata = os.fstat(descriptor)
            self._identity = (metadata.st_dev, metadata.st_ino)

    @staticmethod
    def _name(item_id: str) -> str:
        try:
            if type(item_id) is not str or str(UUID(item_id)) != item_id:
                raise ValueError
        except (ValueError, TypeError, AttributeError):
            raise HistoryMediaError("invalid_item_id") from None
        return item_id + ".media"

    @staticmethod
    def _check_file(metadata: os.stat_result) -> None:
        if (not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.geteuid()
                or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_nlink != 1
                or metadata.st_size > MAX_JPEG_BYTES + _HEADER_BYTES):
            raise HistoryMediaError("unsafe_storage")

    @contextmanager
    def _locked(self) -> Iterator[int]:
        descriptor = None
        try:
            try:
                resolved = self._directory.resolve(strict=True)
            except RuntimeError:
                raise HistoryMediaError("unsafe_storage") from None
            if resolved != self._directory:
                raise HistoryMediaError("unsafe_storage")
            descriptor = os.open(self._directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            deadline = time.monotonic() + 1.0
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise HistoryMediaError("media_busy") from None
                    time.sleep(min(0.01, remaining))
            metadata = os.fstat(descriptor)
            current = self._directory.lstat()
            identity = (metadata.st_dev, metadata.st_ino)
            if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o700
                    or identity != (current.st_dev, current.st_ino)
                    or (self._identity is not None and identity != self._identity)):
                raise HistoryMediaError("unsafe_storage")
            yield descriptor
        except OSError:
            raise HistoryMediaError("media_unavailable") from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def _read(self, directory: int, name: str) -> bytes | None:
        try:
            metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            return None
        self._check_file(metadata)
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            self._check_file(opened)
            if (metadata.st_dev, metadata.st_ino) != (opened.st_dev, opened.st_ino):
                raise HistoryMediaError("unsafe_storage")
            record = stream.read(MAX_JPEG_BYTES + _HEADER_BYTES + 1)
        image = record[_HEADER_BYTES:]
        if (len(record) > MAX_JPEG_BYTES + _HEADER_BYTES or not record.startswith(_MAGIC)
                or not hmac.compare_digest(record[len(_MAGIC):_HEADER_BYTES], hashlib.sha256(image).digest())):
            raise HistoryMediaError("corrupt_media")
        _validate_jpeg(image, self._dimensions)
        return image

    def _space(self, directory: int, required: int = 0) -> None:
        available = os.fstatvfs(directory)
        block = available.f_frsize or available.f_bsize
        allocation = ((required + block - 1) // block + 1) * block if required else 0
        if available.f_bavail * block < self._reserve + allocation:
            raise HistoryMediaError("insufficient_space")

    def put(self, item_id: str, image: bytes) -> str:
        name = self._name(item_id)
        _validate_jpeg(image, self._dimensions)
        digest = hashlib.sha256(image).digest()
        with self._locked() as directory:
            existing = self._read(directory, name)
            if existing is not None:
                if existing != image:
                    raise HistoryMediaError("item_conflict")
                os.fsync(directory)
                return digest.hex()
            record = _MAGIC + digest + image
            self._space(directory, len(record))
            temporary = ".pending-" + str(uuid4())
            descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                 0o600, dir_fd=directory)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(record)
                    stream.flush()
                    os.fsync(stream.fileno())
                self._space(directory)
                os.rename(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
                os.fsync(directory)
            finally:
                try:
                    os.unlink(temporary, dir_fd=directory)
                except FileNotFoundError:
                    pass
        return digest.hex()

    def get(self, item_id: str) -> bytes:
        name = self._name(item_id)
        with self._locked() as directory:
            image = self._read(directory, name)
            if image is None:
                raise HistoryMediaError("media_not_found")
            return image

    def delete(self, item_id: str) -> bool:
        name = self._name(item_id)
        with self._locked() as directory:
            try:
                metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                os.fsync(directory)
                return False
            self._check_file(metadata)
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
            return True

    def prune(self, retained_ids: list[str] | tuple[str, ...] | set[str] | frozenset[str]) -> int:
        """Remove orphan records and abandoned pending files; return unlink count.

        Call at startup/maintenance while holding the history metadata transaction
        lock, using its complete retained-media UUID set. All metadata writers
        must take that lock before put/delete/prune and hold it through metadata
        commit: the directory lock alone cannot protect a put awaiting its link
        into history. Missing retained records are not fabricated or repaired.

        Unknown filenames are untouched. Unsafe recognized orphan files fail
        closed. Partial deletion on I/O failure is retryable, not transactional;
        every validated attempt fsyncs the directory, including no-op retries.
        """
        with self._locked() as directory:
            if type(retained_ids) not in (list, tuple, set, frozenset):
                raise HistoryMediaError("invalid_item_id")
            retained = {self._name(item_id) for item_id in retained_ids}
            deleted = 0
            try:
                candidates = []
                with os.scandir(directory) as entries:
                    for entry in entries:
                        name = entry.name
                        if name in retained:
                            continue
                        if name.endswith(".media"):
                            item_id = name[:-6]
                        elif name.startswith(".pending-"):
                            item_id = name[9:]
                        else:
                            continue
                        try:
                            self._name(item_id)
                        except HistoryMediaError:
                            continue
                        metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
                        self._check_file(metadata)
                        candidates.append((name, metadata.st_dev, metadata.st_ino))
                for name, device, inode in candidates:
                    metadata = os.stat(name, dir_fd=directory, follow_symlinks=False)
                    self._check_file(metadata)
                    if (metadata.st_dev, metadata.st_ino) != (device, inode):
                        raise HistoryMediaError("unsafe_storage")
                    os.unlink(name, dir_fd=directory)
                    deleted += 1
                return deleted
            finally:
                os.fsync(directory)
