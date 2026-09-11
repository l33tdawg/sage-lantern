"""Opt-in initial Linux USB open, NOT a DTR-free or hardware-ready guarantee.

Trusted root configuration only; never construct policy from browser claims.
The operator must separately approve possible startup reset/data loss and stop
all existing pollers. Kernel open/termios/close may change modem signals. No
explicit modem-control, flush, flash, reset, sleep/retry or reopen is performed.

startup_fence() must hold actual lifecycle quiescence across the whole attempt
and yield a callable lease check. journals_reconciled() must inspect authoritative
workflow recovery state under that fence, returning literal True only when no
unresolved prior IO remains. Neither hook is replaced by a default True stub.
The owning daemon must continue normal access/reconciliation gates after return.

An opener is single-use, including failed/denied attempts. A fresh object is NOT
permission to reopen uncertain IO; a new operator-approved lifecycle decision
and reconciliation are required. Native DTR, firmware boot timing, stale buffers
and each unit's hardware identity remain acceptance gates, not policy attestations.
"""

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading

from .mesh_serial_owner import RadioBinding, adopt_serial_fd


class USBOpenError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise USBOpenError(code)


@dataclass(frozen=True)
class USBStartupPolicy:
    allow_initial_open: bool = False
    acknowledge_possible_reset: bool = False
    phase: str = 'disabled'


@dataclass(frozen=True)
class _USBNode:
    path: Path
    device: int
    inode: int
    rdev: int


def _inspect(binding):
    stable = Path(binding.stable_path)
    _require(stable.is_symlink(), 'stable_usb_link_required')
    target = stable.resolve(strict=True)
    _require(target.parent == Path('/dev') and re.fullmatch(r'tty(?:ACM|USB)[0-9]+', target.name), 'unsupported_tty')
    information = target.stat()
    _require(stat.S_ISCHR(information.st_mode), 'not_character_device')
    device = Path(f'/sys/dev/char/{os.major(information.st_rdev)}:{os.minor(information.st_rdev)}/device').resolve(strict=True)
    _require(Path('/sys/devices') in device.parents, 'unsupported_sysfs_binding')
    for parent in (device, *device.parents):
        if (parent / 'idVendor').is_file():
            observed = tuple((parent / field).read_text().strip() for field in ('idVendor', 'idProduct', 'serial'))
            _require(observed == (binding.vendor_id, binding.product_id, binding.serial), 'usb_binding_mismatch')
            return _USBNode(target, information.st_dev, information.st_ino, information.st_rdev)
    raise USBOpenError('usb_identity_unavailable')


def _lock(directory, name):
    import fcntl
    descriptor = os.open(directory / name, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        information = os.fstat(descriptor)
        _require(stat.S_ISREG(information.st_mode) and information.st_uid == 0
                 and information.st_nlink == 1 and stat.S_IMODE(information.st_mode) == 0o600, 'unsafe_lock_file')
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


class InitialUSBOpener:
    def __init__(self, *, policy=None, startup_fence=None, journals_reconciled=None):
        self._policy = policy
        self._startup_fence = startup_fence
        self._journals_reconciled = journals_reconciled
        self._mutex = threading.Lock()
        self._attempted = False

    def open(self, *, binding, lock_directory, timeout=3):
        """Return handshaken SerialScheduler; no application payload IO here.

        Only the canonical resolved node is opened, with O_NOFOLLOW, O_NOCTTY,
        O_NONBLOCK and O_CLOEXEC. A transient startup lock serializes cooperating
        openers. The owner lock is checked before opening to avoid resetting an
        already-owned radio, then handed off to adopt_serial_fd. TIOCEXCL and
        flock cannot evict pre-existing handles or prevent privileged bypasses.
        """
        with self._mutex:
            _require(not self._attempted, 'initial_open_already_attempted')
            self._attempted = True
        policy = self._policy
        _require(type(policy) is USBStartupPolicy and policy.allow_initial_open is True
                 and policy.acknowledge_possible_reset is True
                 and policy.phase in ('quiescent_startup', 'provisioning'), 'initial_open_not_approved')
        _require(sys.platform == 'linux' and os.geteuid() == 0, 'unsupported_platform_or_operator')
        _require(type(binding) is RadioBinding and callable(self._startup_fence)
                 and callable(self._journals_reconciled), 'missing_trusted_startup_binding')
        _require(type(timeout) in (float, int) and math.isfinite(timeout) and 0 < timeout <= 30, 'invalid_timeout')
        directory = Path(lock_directory)
        _require(directory.is_absolute() and all(not item.is_symlink() for item in (directory, *directory.parents)), 'invalid_lock_directory')
        information = directory.stat()
        _require(stat.S_ISDIR(information.st_mode) and information.st_uid == 0
                 and stat.S_IMODE(information.st_mode) == 0o700, 'unsafe_lock_directory')
        descriptor = startup_lock = owner_lock = None
        scheduler = None
        try:
            with self._startup_fence() as lease:
                def check():
                    _require(callable(lease) and lease() is True
                             and self._journals_reconciled() is True, 'startup_not_quiescent_or_reconciled')
                check()
                before = _inspect(binding)
                startup_lock = _lock(directory, f'mesh-{before.rdev}.startup.lock')
                owner_lock = _lock(directory, f'mesh-{before.rdev}.lock')
                _require(_inspect(binding) == before, 'usb_binding_changed')
                check()
                descriptor = os.open(before.path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_NOFOLLOW | os.O_CLOEXEC)
                opened = os.fstat(descriptor)
                _require(stat.S_ISCHR(opened.st_mode) and
                         (opened.st_dev, opened.st_ino, opened.st_rdev) == (before.device, before.inode, before.rdev)
                         and _inspect(binding) == before, 'usb_binding_changed')
                check()
                released, owner_lock = owner_lock, None
                os.close(released)
                transferred, descriptor = descriptor, None
                scheduler = adopt_serial_fd(transferred, binding=binding, lock_directory=directory, timeout=timeout)
                check()
            released, startup_lock = startup_lock, None
            os.close(released)
            return scheduler
        except BaseException:
            if scheduler is not None:
                scheduler.close(timeout=1)
            raise
        finally:
            try:
                if descriptor is not None:
                    os.close(descriptor)
            finally:
                try:
                    if owner_lock is not None:
                        os.close(owner_lock)
                finally:
                    if startup_lock is not None:
                        os.close(startup_lock)
