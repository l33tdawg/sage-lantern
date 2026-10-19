"""Durable candidate reservations; contains hashes/receipts, never note text.

Use a pre-created private directory owned by this process UID. Reservations are
permanent, including uncertain operations. No retry/recovery/reset API exists.
This protects cooperating processes, not root or a compromised same-UID writer.
"""
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import secrets
import stat

from action_session import ActionRefused, VerifiedReceipt, fingerprint


def _digest(value):
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ActionRefused("journal_invalid_digest")
    return value


class DurableActionJournal:
    def __init__(self, directory):
        self.path = Path(directory).absolute()
        self.fd = None
        try:
            if any(parent.is_symlink() for parent in (self.path, *self.path.parents)):
                raise ValueError()
            self.fd = os.open(self.path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            self._check()
        except Exception:
            self.close()
            raise ActionRefused("journal_directory_unavailable") from None

    def _check(self):
        opened, named = os.fstat(self.fd), self.path.lstat()
        if (not stat.S_ISDIR(opened.st_mode) or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) != 0o700
                or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
            raise ActionRefused("journal_directory_changed")

    def _read(self, name):
        self._check()
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.fd)
        try:
            s = os.fstat(fd)
            if (not stat.S_ISREG(s.st_mode) or s.st_uid != os.geteuid()
                    or stat.S_IMODE(s.st_mode) != 0o600 or s.st_nlink != 1 or s.st_size > 4096):
                raise ValueError()
            return os.read(fd, 4097)
        finally:
            os.close(fd)

    def reserve(self, key, fingerprint):
        name, value = _digest(key) + ".reserved", (_digest(fingerprint) + "\n").encode()
        try:
            self._check()
            try:
                fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             0o600, dir_fd=self.fd)
            except FileExistsError:
                # Even a conflicting/truncated reservation cannot be recycled.
                if self._read(name) != value:
                    raise ActionRefused("journal_reservation_conflict_or_uncertain")
                return False
            try:
                if os.write(fd, value) != len(value):
                    raise OSError()
                os.fsync(fd)
            finally:
                os.close(fd)
            os.fsync(self.fd)
            self._check()
            return True
        except ActionRefused:
            raise
        except Exception:
            raise ActionRefused("journal_reservation_uncertain") from None

    def complete(self, key, receipt):
        _digest(key)
        if type(receipt) is not VerifiedReceipt:
            raise ActionRefused("journal_verified_receipt_required")
        temporary = ".receipt-" + secrets.token_hex(16)
        try:
            reserved = self._read(key + ".reserved")
            if len(reserved) != 65 or not re.fullmatch(rb"[0-9a-f]{64}\n", reserved):
                raise ValueError()
            for value in (receipt.operation_id, receipt.request_sha256, receipt.scope_sha256,
                          receipt.calls_sha256, receipt.result_sha256):
                _digest(value)
            expected = fingerprint({"operation_id": receipt.operation_id, "scope": receipt.scope_sha256,
                                    "calls": receipt.calls_sha256, "request": receipt.request_sha256})
            if (reserved != (expected + "\n").encode()
                    or receipt.state not in {"committed", "read_complete"}
                    or receipt.trust_basis not in {"signed_receipt", "pinned_online_canonical_observation"}
                    or type(receipt.evidence_id) is not str
                    or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,192}", receipt.evidence_id)):
                raise ValueError()
            encoded = (json.dumps(asdict(receipt), sort_keys=True, separators=(",", ":")) + "\n").encode()
            if len(encoded) > 4096:
                raise ValueError()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=self.fd)
            try:
                if os.write(fd, encoded) != len(encoded):
                    raise OSError()
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                os.link(temporary, key + ".receipt", src_dir_fd=self.fd, dst_dir_fd=self.fd,
                        follow_symlinks=False)
            except FileExistsError:
                if self._read(key + ".receipt") != encoded:
                    raise ValueError()
            os.unlink(temporary, dir_fd=self.fd)
            os.fsync(self.fd)
            self._check()
        except Exception:
            # Reserved forever. Completion failure never authorizes another send.
            raise ActionRefused("journal_completion_uncertain") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=self.fd)
            except (OSError, TypeError):
                pass

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
