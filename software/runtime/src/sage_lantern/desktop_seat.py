"""Read-only seat binding for a root-created compositor; never acquire a seat.

This supplements the pidfd/process anchor. It does not qualify physical input,
prevent another compositor launch, or preserve clients after compositor death.
The saved-session format is source-bound to the reviewed systemd 259 lane.
"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import subprocess

from .desktop_control import unique_object
from .desktop_native import NativeError, trusted_file


def saved_session(session, *, read=trusted_file):
    if type(session) is not str or re.fullmatch(r'[A-Za-z0-9_-]{1,32}', session) is None:
        raise NativeError('seat_refused')
    fields = {}
    for line in read('/run/systemd/sessions/' + session).decode().splitlines():
        key, separator, value = line.partition('=')
        if not separator or key not in {'UID', 'ACTIVE', 'TYPE', 'CLASS', 'SEAT',
                                        'LEADER', 'CONTROLLER', 'DEVICES'}:
            continue
        if key in fields:
            raise NativeError('seat_refused')
        fields[key] = value
    return fields


def controller_pid(name, *, run=subprocess.run):
    if type(name) is not str or re.fullmatch(r':[0-9]+\.[0-9]+', name) is None:
        raise NativeError('seat_refused')
    result = run(['/usr/bin/busctl', '--system', '--json=short', 'call',
        'org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
        'GetConnectionUnixProcessID', 's', name],
        check=True, capture_output=True, timeout=2)
    value = json.loads(result.stdout, object_pairs_hook=unique_object)
    if (type(value) is not dict or set(value) != {'type', 'data'} or value['type'] != 'u'
            or type(value['data']) is not list or len(value['data']) != 1
            or type(value['data'][0]) is not int or value['data'][0] <= 1):
        raise NativeError('seat_refused')
    return value['data'][0]


def device_fds(pid, *, proc=Path('/proc')):
    """Inspect only DRM card/evdev character descriptors, never open a device."""
    if type(pid) is not int or pid <= 1:
        raise NativeError('seat_refused')
    devices = set()
    for fd in (proc / str(pid) / 'fd').iterdir():
        try:
            name = os.readlink(fd)
            if re.fullmatch(r'/dev/(dri/card[0-9]+|input/event[0-9]+)', name) is None:
                continue
            info = fd.stat()
            if not stat.S_ISCHR(info.st_mode):
                raise NativeError('seat_refused')
            devices.add((os.major(info.st_rdev), os.minor(info.st_rdev)))
        except FileNotFoundError:
            continue  # A descriptor may close during enumeration.
    return devices


@dataclass(frozen=True)
class SeatAnchor:
    pid: int
    uid: int
    session: str
    leader: str
    controller: str

    @classmethod
    def enroll(cls, pid, uid, session, leader, *, read=saved_session,
               resolve=controller_pid, devices=device_fds):
        before = read(session)
        if (type(pid) is not int or pid <= 1 or type(uid) is not int or uid <= 0
                or type(leader) is not str or re.fullmatch(r'[1-9][0-9]*', leader) is None
                or any(before.get(key) != value for key, value in dict(
                    UID=str(uid), ACTIVE='1', TYPE='wayland', CLASS='user',
                    SEAT='seat0', LEADER=leader).items())):
            raise NativeError('seat_refused')
        controller = before.get('CONTROLLER')
        if resolve(controller) != pid:
            raise NativeError('seat_refused')
        registered = set()
        for entry in before.get('DEVICES', '').split():
            if re.fullmatch(r'[0-9]+:[0-9]+', entry) is None:
                raise NativeError('seat_refused')
            registered.add(tuple(map(int, entry.split(':'))))
        opened = devices(pid)
        # Descriptor numbers need not be fixed; their kernel device IDs must
        # belong to the logind controller's registered DRM and input set.
        if (not opened or not opened <= registered
                or not any(major == 226 for major, _ in opened)
                or not any(major == 13 for major, _ in opened)
                or read(session) != before or resolve(controller) != pid):
            raise NativeError('seat_refused')
        return cls(pid, uid, session, leader, controller)

    def matches(self, **dependencies):
        try:
            return self.enroll(self.pid, self.uid, self.session, self.leader,
                               **dependencies) == self
        except Exception:
            return False
