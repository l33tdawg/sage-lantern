"""Exact-process native adapter. Root enrolls one already concealed compositor.

Enrollment is an operator/supervisor action, never a user claim. The adapter
does not launch a session or prove the launch chain. All authority is in RAM;
the peer receives booleans, never the broker's guard or grant tickets.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import select
import socket
import stat
import struct
import subprocess
import threading

from .desktop_control import MAX_BODY, unique_object


class NativeError(RuntimeError):
    def __init__(self, code='unavailable'):
        self.code = code
        super().__init__(code)


def trusted_file(path, digest=None):
    """No symlink, writable ancestor or writable/root-unowned payload."""
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise NativeError()
    for ancestor in path.parents:
        info = ancestor.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise NativeError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise NativeError()
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            body = stream.read(64 * 1024 * 1024 + 1)
        if len(body) > 64 * 1024 * 1024:
            raise NativeError()
        if digest is not None and hashlib.sha256(body).hexdigest() != digest:
            raise NativeError()
        return body
    finally:
        os.close(fd)


def process_snapshot(pid, proc=Path('/proc')):
    """Read only process identity/selected launch flags; never emit environment."""
    base = proc / str(pid)
    before = (base / 'stat').read_text()
    fields = before.rsplit(')', 1)[1].split()
    start = int(fields[19])  # /proc stat field 22; comm may contain spaces/parentheses
    status = (base / 'status').read_text()
    uid = re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)$', status, re.M)
    if uid is None or len(set(uid.groups())) != 1:
        raise NativeError()
    executable = str((base / 'exe').resolve(strict=True))
    argv = (base / 'cmdline').read_bytes().rstrip(b'\0').split(b'\0')
    cgroup = (base / 'cgroup').read_text().strip()
    # Do not retain secrets or unrelated variables from the process environment.
    selected = {}
    allowed = {'G_RESOURCE_OVERLAYS', 'LD_PRELOAD', 'LD_LIBRARY_PATH', 'GJS_PATH',
               'GI_TYPELIB_PATH', 'SHELL_DEBUG', 'GNOME_SHELL_SESSION_MODE',
               'GIO_MODULE_DIR', 'GIO_EXTRA_MODULES', 'NOTIFY_SOCKET'}
    for item in (base / 'environ').read_bytes().split(b'\0'):
        key, _, value = item.partition(b'=')
        if key.decode('ascii', errors='ignore') in allowed:
            name = key.decode('ascii')
            if name in selected:
                raise NativeError()
            selected[name] = value.decode('utf-8')
    if (base / 'stat').read_text() != before:
        # Most stat counters can change during a read. Compare start/UID again.
        after = (base / 'stat').read_text().rsplit(')', 1)[1].split()
        if int(after[19]) != start:
            raise NativeError()
    return dict(pid=pid, parent=int(fields[1]), uid=int(uid[1]), start=start, executable=executable,
                cgroup=cgroup, environment=selected, argv=argv)


def session_snapshot(session):
    if type(session) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,32}', session) is None:
        raise NativeError()
    properties = ('Id', 'User', 'Type', 'Class', 'Active', 'Remote', 'Seat', 'Leader')
    result = subprocess.run(['/usr/bin/loginctl', 'show-session', session,
        *(argument for prop in properties for argument in ('--property', prop))],
        check=True, capture_output=True, timeout=2)
    return dict(line.split('=', 1) for line in result.stdout.decode().splitlines())


@dataclass(frozen=True)
class PeerAnchor:
    pid: int
    uid: int
    session: str
    boot: str
    start: int
    cgroup: str
    leader: str
    overlay: str
    executable_digest: str
    parent: int | None = None
    notify: str | None = None

    @classmethod
    def enroll(cls, pid, uid, session, overlay, executable_digest, *,
               snapshot=process_snapshot, session_read=session_snapshot,
               boot_read=lambda: Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
               parent=None, notify=None):
        process = snapshot(pid)
        logind = session_read(session)
        boot = boot_read()
        expected_cgroup = (f'0::/user.slice/user-{uid}.slice/user@{uid}.service/'
                           'session.slice/org.gnome.Shell@ubuntu.service')
        expected_environment = {'G_RESOURCE_OVERLAYS': overlay}
        if parent is not None:
            if type(parent) is not int or parent <= 1 or notify != '/run/sage-lantern-desktop/ready.sock':
                raise NativeError('peer_refused')
            expected_cgroup = '0::/system.slice/sage-lantern-guarded-session.service'
            expected_environment['NOTIFY_SOCKET'] = notify
        if (type(pid) is not int or pid <= 1 or type(uid) is not int or uid <= 0
                or process['uid'] != uid or process['executable'] != '/usr/bin/gnome-shell'
                or process['cgroup'] != expected_cgroup
                or process['argv'] != [b'/usr/bin/gnome-shell', b'--mode=ubuntu']
                or process['environment'] != expected_environment
                or (parent is not None and process.get('parent') != parent)
                or logind.get('Id') != session or logind.get('User') != str(uid)
                or logind.get('Type') != 'wayland' or logind.get('Class') != 'user'
                or logind.get('Active') != 'yes' or logind.get('Remote') != 'no'
                or logind.get('Seat') != 'seat0'
                or not re.fullmatch(r'[1-9][0-9]*', logind.get('Leader', ''))
                or re.fullmatch(r'[0-9a-f-]{36}', boot) is None):
            raise NativeError('peer_refused')
        return cls(pid, uid, session, boot, process['start'], process['cgroup'],
                   logind['Leader'], overlay, executable_digest, parent, notify)

    @property
    def binding(self):
        return f'{self.boot}:uid{self.uid}:pid{self.pid}:start{self.start}:session{self.session}'

    def matches(self, pid, uid, *, snapshot=process_snapshot,
                session_read=session_snapshot,
                boot_read=lambda: Path('/proc/sys/kernel/random/boot_id').read_text().strip()):
        try:
            if (pid, uid) != (self.pid, self.uid):
                return False
            other = self.enroll(pid, uid, self.session, self.overlay,
                                self.executable_digest, snapshot=snapshot,
                                session_read=session_read, boot_read=boot_read,
                                parent=self.parent, notify=self.notify)
            return other == self
        except Exception:
            return False


def kernel_peer(connection):
    pid, uid, _ = struct.unpack('3i', connection.getsockopt(
        socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize('3i')))
    return pid, uid


class BrokerClient:
    def __init__(self, path, uid):
        self.path, self.uid = str(path), uid

    def __call__(self, request):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(15)
            connection.connect(self.path)
            if kernel_peer(connection)[1] != self.uid:
                raise NativeError()
            connection.sendall(json.dumps(request, separators=(',', ':')).encode() + b'\n')
            with connection.makefile('rb') as stream:
                body = stream.readline(MAX_BODY + 2)
            reply = decode(body)
            if reply.get('ok') is not True or type(reply.get('result')) is not dict:
                code = reply.get('error')
                raise NativeError(code if code in {'invalid_pin', 'verification_failed',
                                  'rate_limited', 'verification_busy'} else 'unavailable')
            return reply['result']


def decode(raw):
    if type(raw) is not bytes or not raw.endswith(b'\n') or len(raw) > MAX_BODY + 1:
        raise NativeError('invalid_request')
    try:
        value = json.loads(raw, object_pairs_hook=unique_object,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if type(value) is not dict:
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise NativeError('invalid_request') from None


class NativeSession:
    """One persistent kernel-bound connection; tokens never leave this object."""
    def __init__(self, binding, broker, valid):
        self.binding, self.broker, self.valid = binding, broker, valid
        self.guard = self.token = self.mode = None
        self.closed = False
        self.generation = 0
        self.gate = threading.RLock()

    def close(self):
        with self.gate:
            self.closed = True
            self.generation += 1
            guard, self.guard = self.guard, None
            self.token = self.mode = None
        if guard is not None:
            try:
                self.broker(dict(action='lock', guard=guard))
            except Exception:
                pass  # Client is concealed; broker presence expires without renewal.

    def _admit(self):
        if self.closed or self.valid() is not True:
            self.close()
            raise NativeError('peer_refused')

    def request(self, payload):
        with self.gate:
            self._admit()
            action = payload.get('action')
            if action == 'open':
                if set(payload) != {'action', 'mode'} or payload['mode'] not in ('kiosk', 'desktop'):
                    raise NativeError('invalid_request')
                # Even requesting the same mode replaces/fences every prior ticket.
                self.token = None
                self.generation += 1
                result = self.broker(dict(action='open', mode=payload['mode'], binding=self.binding))
                self._admit()
                guard = result.get('guard')
                if type(guard) is not str or re.fullmatch(r'[A-Za-z0-9_-]{43}', guard) is None:
                    raise NativeError()
                self.guard, self.mode = guard, payload['mode']
                return {'authorized': False}
            if action == 'unlock':
                if (set(payload) != {'action', 'pin'} or type(payload['pin']) is not str
                        or re.fullmatch(r'[0-9]{8}', payload['pin']) is None):
                    raise NativeError('invalid_request')
            elif action not in ('lock', 'presence', 'activity', 'check') or set(payload) != {'action'}:
                raise NativeError('invalid_request')
            if self.guard is None:
                raise NativeError()
            guard, generation = self.guard, self.generation
            if action == 'lock':
                self.generation += 1
                self.token = None
                self.broker(dict(action='lock', guard=guard))
                return {'authorized': False}
            if action != 'unlock':
                if self.token is None:
                    return {'authorized': False}
                reply = self.broker(dict(action=action, guard=guard, token=self.token))
                self._admit()
                if reply.get('authorized') is not True:
                    self.token = None
                return {'authorized': self.token is not None}
        # Do not hold the cancellation gate through PIN verification.
        result = self.broker(dict(action='unlock', guard=guard, pin=payload['pin']))
        with self.gate:
            self._admit()
            if generation != self.generation or guard != self.guard:
                raise NativeError('unlock_cancelled')
            token = result.get('token')
            if type(token) is not str or re.fullmatch(r'[A-Za-z0-9_-]{43}', token) is None:
                raise NativeError()
            if self.broker(dict(action='activate', guard=guard, token=token)).get('authorized') is not True:
                raise NativeError()
            self._admit()
            self.token = token
            return {'authorized': True}


def pidfd_alive(fd):
    return not select.select([fd], [], [], 0)[0]
