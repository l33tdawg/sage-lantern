"""Root-created compositor identity and readiness; no caller launch claims."""
from __future__ import annotations

import os
from pathlib import Path
import pwd
import re
import socket
import struct
import subprocess
import uuid

from .desktop_native import NativeError, process_snapshot, session_snapshot

ROOT_CGROUP = '0::/system.slice/sage-lantern-guarded-session.service'
CONTROL = '/run/sage-lantern-desktop/start.sock'
NOTIFY = '/run/sage-lantern-desktop/ready.sock'
BRIDGE = '/usr/local/libexec/sage-lantern-guarded-session-bridge'


def active_session(uid, *, run=subprocess.run, read=session_snapshot):
    """Derive the one local active session from logind, never request JSON."""
    result = run(['/usr/bin/loginctl', 'list-sessions', '--no-legend', '--no-pager'],
                 capture_output=True, check=True, timeout=2)
    sessions = []
    for line in result.stdout.decode().splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[1] != str(uid):
            continue
        value = read(fields[0])
        if all(value.get(k) == v for k, v in dict(User=str(uid), Type='wayland',
                Class='user', Active='yes', Remote='no', Seat='seat0').items()):
            if value.get('Id') != fields[0] or not re.fullmatch(r'[1-9][0-9]*', value.get('Leader', '')):
                raise NativeError()
            sessions.append(value)
    if len(sessions) != 1:
        raise NativeError()
    return sessions[0]


def bridge_peer(pid, uid, expected_uid, *, snapshot=process_snapshot):
    if uid != expected_uid:
        return False
    try:
        value = snapshot(pid)
        return (value['uid'] == expected_uid and value['cgroup'] ==
            f'0::/user.slice/user-{uid}.slice/user@{uid}.service/session.slice/org.gnome.Shell@ubuntu.service'
            and value['argv'] == [b'/usr/bin/python3', b'-I', BRIDGE.encode()])
    except Exception:
        return False


def shell_environment(policy, session):
    if policy['uid'] != 1000 or policy['account'] != 'sage' or policy['home'] != '/home/sage':
        raise NativeError()
    return dict(HOME=policy['home'], USER=policy['account'], LOGNAME=policy['account'],
        PATH='/usr/bin:/bin', LANG='C.UTF-8', XDG_RUNTIME_DIR='/run/user/1000',
        DBUS_SESSION_BUS_ADDRESS='unix:path=/run/user/1000/bus', XDG_SESSION_ID=session['Id'],
        XDG_SESSION_TYPE='wayland', XDG_SEAT='seat0', XDG_CURRENT_DESKTOP='ubuntu:GNOME',
        XDG_SESSION_DESKTOP='ubuntu', DESKTOP_SESSION='ubuntu',
        G_RESOURCE_OVERLAYS=policy['overlay'], NOTIFY_SOCKET=NOTIFY)


def existing_compositor(uid, *, proc=Path('/proc'), runtime=None, snapshot=process_snapshot):
    """Refuse replacement/adoption: preserve any existing desktop and apps."""
    runtime = Path(f'/run/user/{uid}') if runtime is None else runtime
    if any(runtime.glob('wayland-*')):
        return True
    for path in proc.iterdir():
        if not path.name.isdigit():
            continue
        try:
            value = snapshot(int(path.name))
        except (OSError, ValueError, NativeError):
            continue  # Other UIDs/proc entries may be intentionally unreadable.
        if value['uid'] == uid and value['executable'] == '/usr/bin/gnome-shell':
            return True
    return False


def spawn_shell(policy, session, *, popen=subprocess.Popen):
    # Parent verifies the system unit's inherited NoNewPrivileges/LimitCORE.
    # Popen's native UID/GID operations avoid preexec_fn in a threaded daemon.
    account = pwd.getpwnam(policy['account'])
    if (account.pw_uid, account.pw_gid, account.pw_dir) != (policy['uid'], policy['gid'], policy['home']):
        raise NativeError()
    return popen(['/usr/bin/gnome-shell', '--mode=ubuntu'],
        env=shell_environment(policy, session), cwd='/', stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
        user=account.pw_uid, group=account.pw_gid,
        extra_groups=os.getgrouplist(account.pw_name, account.pw_gid),
        umask=0o077)


def ready_datagram(body, ancillary, flags, pid, uid):
    """Linux credentials must name the exact child, not a notify JSON claim."""
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or len(body) > 1024:
        return False
    credentials = [data for level, kind, data in ancillary
        if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS]
    if len(credentials) != 1 or len(credentials[0]) != struct.calcsize('3i'):
        return False
    observed_pid, observed_uid, _ = struct.unpack('3i', credentials[0])
    return (observed_pid, observed_uid) == (pid, uid) and body.rstrip(b'\n') == b'READY=1'


class LaunchOnce:
    """A daemon lifetime permits one launch attempt. Failure never restarts apps."""
    def __init__(self, prepare, spawn=spawn_shell, pidfd_open=None):
        self.prepare, self.spawn = prepare, spawn
        self.pidfd_open = pidfd_open or getattr(os, 'pidfd_open', None)
        self.attempted = False
        self.child = None
        self.fd = None

    def start(self):
        if self.attempted:
            raise NativeError('held')
        policy, session = self.prepare()  # Failed preflight may be retried; never spawn.
        self.attempted = True
        self.child = self.spawn(policy, session)
        if not callable(self.pidfd_open):
            raise NativeError('pidfd_unavailable')
        self.fd = self.pidfd_open(self.child.pid)
        return policy, session, self.child


class BrokerLaunchOnce:
    """Stage a stopped child through the broker before the one release call."""
    def __init__(self, prepare, broker_factory, record, selection):
        self.prepare = prepare
        self.broker_factory = broker_factory
        self.record = record
        self.selection = selection
        self.attempted = False
        self.child = None
        self.fd = None
        self.broker = None

    def start(self):
        if self.attempted:
            raise NativeError('held')
        policy, session = self.prepare()
        selection = self.selection()
        if selection is None:
            raise NativeError('capture_selection_required')
        self.attempted = True
        self.broker = self.broker_factory()
        try:
            self.child = self.broker.stage_until_recorded(
                lambda child, request: self.record(child, request, selection),
                launch_id=str(uuid.uuid4()),
                capture_id=selection.capture_id,
                generation=selection.generation,
                policy_sha256=selection.policy_sha256)
        except BaseException:
            if self.broker.staged is not None:
                self.child = self.broker.staged
                self.fd = self.child.pidfd
            raise
        finally:
            self.broker.close()
            self.broker = None
        self.fd = self.child.pidfd
        return policy, session, self.child
