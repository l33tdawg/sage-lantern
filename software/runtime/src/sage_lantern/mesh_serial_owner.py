"""Exclusive Linux serial scheduler; no device opening, resets or automatic retries.

The operator must separately validate initial TTY open/close electrical behavior.
adopt_serial_fd consumes the supplied FD even on failure. No duplicated/inherited
FDs or pre-existing pollers may remain: TIOCEXCL cannot evict existing openers or
privileged bypasses. All workflow producers for this radio/actor must submit
their ENTIRE operation here, including persistence, not merely the RF portion.
Other processes must use the owning daemon, never write those workflow rows
directly. A mailbox fence covers only producers honoring this ownership contract.

Callbacks are trusted, bounded and non-reentrant. A hung callback retains serial
ownership; close returns False rather than claiming drain or forcing unknown IO.
Future wait timeouts are not cancellation or permission to retry. No payload logs.
"""

from collections import deque
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
import stat
import sys
import threading
import time

from .meshcore_transport import MeshCoreClient, SerialFDStream


class SerialOwnerError(RuntimeError):
    pass


def _require(condition, code):
    if not condition:
        raise SerialOwnerError(code)


def _timeout(value):
    _require(type(value) in (float, int) and math.isfinite(value) and 0 < value <= 60, 'invalid_timeout')
    return value


@dataclass(frozen=True)
class RadioBinding:
    unit_id: str
    stable_path: str
    serial: str
    vendor_id: str
    product_id: str
    public_key: bytes

    def __post_init__(self):
        for value, pattern in ((self.unit_id, r'unit-[a-z]'),
                               (self.stable_path, r'/dev/serial/by-id/[A-Za-z0-9_.:+-]+'),
                               (self.serial, r'[A-Za-z0-9:_-]{1,128}'),
                               (self.vendor_id, r'[0-9a-f]{4}'), (self.product_id, r'[0-9a-f]{4}')):
            _require(type(value) is str and re.fullmatch(pattern, value), 'invalid_binding')
        _require(type(self.public_key) is bytes and len(self.public_key) == 32, 'invalid_radio_key')

    @classmethod
    def from_inventory(cls, document, *, unit_id, expected_fingerprint):
        """Consume public inventory plus separately verified operator fingerprint.

        This checks consistency, not provenance of caller-supplied documents.
        Never accept these inputs from an unauthenticated/browser request.
        """
        try:
            _require(document['schema'] == 'sage.lantern.meshcore-radio-public.v1'
                     and document['unit_id'] == unit_id
                     and re.fullmatch(r'unit-[a-z]', unit_id), 'invalid_unit_binding')
            usb, identity = document['usb'], document['radio_identity']
            _require(type(usb['stable_path']) is str and re.fullmatch(r'/dev/serial/by-id/[A-Za-z0-9_.:+-]+', usb['stable_path']), 'invalid_stable_path')
            for key, pattern in (('serial', r'[A-Za-z0-9:_-]{1,128}'), ('vendor_id', r'[0-9a-f]{4}'), ('product_id', r'[0-9a-f]{4}')):
                _require(type(usb[key]) is str and re.fullmatch(pattern, usb[key]), 'invalid_usb_binding')
            _require(type(identity['public_key']) is str and re.fullmatch(r'[0-9a-f]{64}', identity['public_key']), 'invalid_radio_key')
            public = bytes.fromhex(identity['public_key'])
            fingerprint = 'sha256:' + hashlib.sha256(public).hexdigest()
            _require(fingerprint == identity['public_key_sha256'] == expected_fingerprint, 'radio_binding_mismatch')
            return cls(unit_id, usb['stable_path'], usb['serial'], usb['vendor_id'], usb['product_id'], public)
        except (KeyError, TypeError, ValueError):
            raise SerialOwnerError('invalid_inventory') from None


class SerialScheduler:
    """Bounded FIFO of whole producer operations, with real drain/fence leases.

    Fence timeout permanently contains admissions without requesting shutdown.
    Ownership remains held after admitted jobs finish until explicit close().
    """

    def __init__(self, client, *, release, capacity=32):
        _require(callable(release) and type(capacity) is int and 1 <= capacity <= 128, 'invalid_scheduler')
        self._client, self._release, self._capacity = client, release, capacity
        self._condition = threading.Condition()
        self._queue = deque()
        self._active = False
        self._closing = False
        self._contained = False
        self._fenced = False
        self._released = False
        self._thread = threading.Thread(target=self._run, name='lantern-mesh-owner', daemon=True)
        self._thread.start()

    def submit(self, operation):
        _require(callable(operation), 'invalid_operation')
        with self._condition:
            _require(threading.current_thread() is not self._thread, 'reentrant_operation')
            _require(not self._closing and not self._fenced and not self._contained, 'producer_admission_fenced')
            _require(len(self._queue) < self._capacity, 'producer_queue_full')
            future = Future()
            self._queue.append((operation, future))
            self._condition.notify_all()
            return future

    def _run(self):
        try:
            while True:
                with self._condition:
                    while not self._queue:
                        if self._closing and not self._fenced:
                            return
                        self._condition.wait()
                    operation, future = self._queue.popleft()
                    self._active = True
                try:
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(operation(self._client))
                        except BaseException as error:
                            future.set_exception(error)
                finally:
                    with self._condition:
                        self._active = False
                        self._condition.notify_all()
        finally:
            try:
                self._release()
            except BaseException:
                return
            with self._condition:
                self._released = True
                self._condition.notify_all()

    @contextmanager
    def producer_fence(self, timeout=5):
        deadline = time.monotonic() + _timeout(timeout)
        owner = threading.get_ident()
        live = False
        with self._condition:
            _require(threading.current_thread() is not self._thread, 'reentrant_fence')
            _require(not self._closing and not self._fenced and not self._contained, 'producer_admission_fenced')
            self._fenced = True
            while self._active or self._queue:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    self._contained = True
                    self._fenced = False
                    self._condition.notify_all()
                    raise SerialOwnerError('producer_drain_timeout')
                self._condition.wait(remaining)
            live = True

        def lease():
            with self._condition:
                return (live and threading.get_ident() == owner and self._fenced
                        and not self._closing and not self._active and not self._queue)
        try:
            yield lease
        finally:
            with self._condition:
                live = False
                self._fenced = False
                self._condition.notify_all()

    def close(self, timeout=5):
        _timeout(timeout)
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        if threading.current_thread() is self._thread:
            return False
        self._thread.join(timeout)
        with self._condition:
            return not self._thread.is_alive() and self._released


def _verify_device(descriptor, binding):
    information = os.fstat(descriptor)
    _require(stat.S_ISCHR(information.st_mode) and os.isatty(descriptor), 'not_serial_tty')
    stable = Path(binding.stable_path)
    _require(stable.is_symlink(), 'stable_usb_link_required')
    target = stable.resolve(strict=True)
    _require(target.parent == Path('/dev') and re.fullmatch(r'tty(?:ACM|USB)[0-9]+', target.name), 'unsupported_tty')
    linked = target.stat()
    _require((linked.st_dev, linked.st_ino, linked.st_rdev) == (information.st_dev, information.st_ino, information.st_rdev), 'descriptor_binding_mismatch')
    device = Path(f'/sys/dev/char/{os.major(information.st_rdev)}:{os.minor(information.st_rdev)}/device').resolve(strict=True)
    for parent in (device, *device.parents):
        if (parent / 'idVendor').is_file():
            observed = tuple((parent / field).read_text().strip() for field in ('idVendor', 'idProduct', 'serial'))
            _require(observed == (binding.vendor_id, binding.product_id, binding.serial), 'usb_binding_mismatch')
            return information.st_rdev
    raise SerialOwnerError('usb_identity_unavailable')


def adopt_serial_fd(descriptor, *, binding, lock_directory, timeout=3):
    """Root-only runtime factory; consumes an operator-opened FD, never opens USB.

    Configure raw 115200 8N1 with no flow control, no flush, and HUPCL cleared.
    No DTR/RTS ioctl, reset, reopen or automatic retry. Electrical effects of
    initial open and driver termios/close behavior still require hardware proof.
    All competing pollers must be stopped before this factory is authorized.
    """
    lock_fd = None
    scheduler = None
    try:
        _require(sys.platform == 'linux' and os.geteuid() == 0, 'unsupported_platform_or_operator')
        import fcntl
        import termios
        _require(type(binding) is RadioBinding, 'invalid_binding')
        _timeout(timeout)
        _require(timeout <= 30, 'invalid_serial_timeout')
        device_number = _verify_device(descriptor, binding)
        directory = Path(lock_directory)
        _require(directory.is_absolute() and all(not item.is_symlink() for item in (directory, *directory.parents)), 'invalid_lock_directory')
        information = directory.stat()
        _require(stat.S_ISDIR(information.st_mode) and information.st_uid == 0 and stat.S_IMODE(information.st_mode) == 0o700, 'unsafe_lock_directory')
        lock_fd = os.open(directory / f'mesh-{device_number}.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        information = os.fstat(lock_fd)
        _require(stat.S_ISREG(information.st_mode) and information.st_nlink == 1 and information.st_uid == 0 and stat.S_IMODE(information.st_mode) == 0o600, 'unsafe_lock_file')
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.ioctl(descriptor, termios.TIOCEXCL)
        os.set_inheritable(descriptor, False)
        os.set_blocking(descriptor, False)
        settings = termios.tcgetattr(descriptor)
        settings[:4] = [0, 0, termios.CS8 | termios.CREAD | termios.CLOCAL, 0]
        settings[4:6] = [termios.B115200, termios.B115200]
        settings[6][termios.VMIN], settings[6][termios.VTIME] = 0, 0
        termios.tcsetattr(descriptor, termios.TCSANOW, settings)
        _require(_verify_device(descriptor, binding) == device_number, 'device_changed')
        client = MeshCoreClient(SerialFDStream(descriptor), expected_public_key=binding.public_key, timeout=timeout)

        def release():
            try:
                os.close(descriptor)
            finally:
                os.close(lock_fd)
        scheduler = SerialScheduler(client, release=release)
        scheduler.submit(lambda device: device.handshake()).result(timeout=timeout + 1)
        return scheduler
    except BaseException:
        if scheduler is not None:
            scheduler.close(timeout=1)
        else:
            try:
                os.close(descriptor)
            finally:
                if lock_fd is not None:
                    os.close(lock_fd)
        raise
