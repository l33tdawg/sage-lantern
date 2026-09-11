"""Cooperating single-owner fence shared with legacy ConversationStore.

Supply an already provisioned canonical absolute 0700 directory. As with the
legacy store, symlink ancestors (including macOS /var or /tmp aliases) are not
resolved: use their operator-validated canonical paths. Ancestors must belong
to root/current UID and be non-writable by others; root-owned sticky temporary
ancestors are allowed, protecting owned child names. ACLs must be excluded by
provisioning. This is not protection against root, malicious same-credential
processes, or noncooperating writers. Use a local filesystem supporting flock.

Only empty 0600 owner.lock may be created; no directory, DB or text is created.
The marker is retained on close/failure. With owner.lock present but no legacy
DB, legacy ConversationStore refuses fresh initialization (missing_database).
This deliberately prevents automatic downgrade; never erase the marker or
fallback to legacy storage. An existing legacy DB is not migrated or removed.

Hold this object for the whole service lifetime and require valid() around
admission and storage work. False is terminal for this instance. The check is
not a transactional fence against hostile path replacement after it returns.
"""

import fcntl
import os
from pathlib import Path
import stat
import threading


class ConversationOwnerError(RuntimeError):
    def __init__(self):
        super().__init__("conversation_owner_unavailable")


class ProtectedConversationOwner:
    def __init__(self, directory):
        self._mutex = threading.RLock()
        self._directory_fd = None
        self._lock_fd = None
        self._held = False
        self._failed = False
        self._pid = os.getpid()
        try:
            self.directory = Path(directory)
            if not self.directory.is_absolute() or ".." in self.directory.parts:
                raise ConversationOwnerError()
            self._ancestors = self._inspect_path()
            self._directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if self._identity(os.fstat(self._directory_fd)) != self._ancestors[-1][1]:
                raise ConversationOwnerError()
            flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
            try:
                self._lock_fd = os.open("owner.lock", flags | os.O_CREAT | os.O_EXCL, 0o600,
                                        dir_fd=self._directory_fd)
            except FileExistsError:
                self._lock_fd = os.open("owner.lock", flags, dir_fd=self._directory_fd)
            self._file(os.fstat(self._lock_fd))
            fcntl.flock(self._lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._held = True
            if not self.valid():
                raise ConversationOwnerError()
            os.fsync(self._lock_fd)
            os.fsync(self._directory_fd)
            if not self.valid():
                raise ConversationOwnerError()
        except Exception:
            self.close()
            raise ConversationOwnerError() from None

    @staticmethod
    def _identity(info):
        return info.st_dev, info.st_ino

    def _inspect_path(self):
        result = []
        for path in (*reversed(self.directory.parents), self.directory):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in {0, os.geteuid()}:
                raise ConversationOwnerError()
            if path == self.directory:
                if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                    raise ConversationOwnerError()
            elif info.st_mode & 0o022 and not (info.st_uid == 0 and info.st_mode & stat.S_ISVTX):
                raise ConversationOwnerError()
            result.append((path, self._identity(info)))
        return result

    @staticmethod
    def _file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1 or info.st_size != 0):
            raise ConversationOwnerError()

    def valid(self):
        with self._mutex:
            if self._failed or not self._held or self._pid != os.getpid():
                return False
            try:
                if self._inspect_path() != self._ancestors:
                    raise ConversationOwnerError()
                directory = os.fstat(self._directory_fd)
                if self._identity(directory) != self._ancestors[-1][1]:
                    raise ConversationOwnerError()
                opened = os.fstat(self._lock_fd)
                named = os.stat("owner.lock", dir_fd=self._directory_fd, follow_symlinks=False)
                self._file(opened)
                self._file(named)
                if self._identity(opened) != self._identity(named):
                    raise ConversationOwnerError()
                return True
            except Exception:
                self._failed = True
                return False

    def close(self):
        with self._mutex:
            if self._lock_fd is not None:
                try:
                    if self._held and self._pid == os.getpid():
                        fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
                finally:
                    os.close(self._lock_fd)
                    self._lock_fd = None
                    self._held = False
            if self._directory_fd is not None:
                os.close(self._directory_fd)
                self._directory_fd = None
