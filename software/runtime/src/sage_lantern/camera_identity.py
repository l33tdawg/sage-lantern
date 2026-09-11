"""Fresh camera service identity verification without NSS or capture.

An observation binds one live systemd DynamicUser instance, not a persistent
UID or a provisioning receipt. Health is checked between matching observations.
"""

import argparse
from dataclasses import dataclass
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

from .suspend_kiosk import LinuxProcesses
from .suspend_system import SuspendSystem


SERVICE = 'sage-lantern-camera.service'
UNIT = Path('/etc/systemd/system') / SERVICE
HELPER = Path('/usr/local/libexec/sage-lantern-camera-server')
PYTHON = Path('/opt/sage-lantern/venv/bin/python')
FIELDS = ('LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'User', 'DynamicUser',
          'ControlGroup', 'Slice', 'KillMode', 'Job', 'NeedDaemonReload', 'FragmentPath',
          'DropInPaths', 'ExecStart')


def require(value):
    if not value:
        raise ValueError('camera_identity_unverified')


def trusted(path, digest=None):
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022)
        signature = lambda value: (value.st_dev, value.st_ino, value.st_mode, value.st_uid,
            value.st_gid, value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        if digest is not None:
            require(0 < info.st_size <= 262144)
            raw = os.read(descriptor, 262145)
            require(len(raw) == info.st_size and hashlib.sha256(raw).hexdigest() == digest)
        require(signature(info) == signature(os.fstat(descriptor)) == signature(path.lstat()))
        return signature(info)
    finally:
        os.close(descriptor)


def show(system, deadline, device):
    arguments = ['show', '--no-pager', '--all']
    for field in FIELDS:
        arguments.extend(['-p', field])
    text = system._command(arguments + [SERVICE], deadline)
    value = {}
    for line in text.splitlines():
        key, separator, content = line.partition('=')
        require(separator and key in FIELDS and key not in value)
        value[key] = content
    require(set(value) == set(FIELDS))
    prefix = '{ path=' + str(PYTHON) + ' ; argv[]=' + str(PYTHON) + ' ' + str(HELPER) + ' --device=' + device
    require(re.fullmatch(re.escape(prefix) + r' ; ignore_errors=no ; start_time=[^{};\n]+ ; '
        r'stop_time=[^{};\n]+ ; pid=[0-9]+ ; code=[A-Za-z_()]+ ; status=[A-Za-z0-9_/-]+ \}', value['ExecStart']))
    value['ExecStart'] = prefix
    require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'active'
            and value['DynamicUser'] == 'yes' and value['User'] == 'sage-lantern-camera'
            and re.fullmatch('[1-9][0-9]*', value['MainPID'])
            and re.fullmatch('[0-9a-f]{32}', value['InvocationID']) and value['InvocationID'] != '0' * 32
            and value['ControlGroup'] == '/system.slice/' + SERVICE
            and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group'
            and value['Job'] in ('', '0') and value['NeedDaemonReload'] == 'no'
            and value['FragmentPath'] == str(UNIT) and value['DropInPaths'] == '')
    return value


def listener(pid, *, proc=Path('/proc')):
    with (proc / 'net/tcp').open('rb') as stream:
        raw = stream.read(1048577)
    require(len(raw) <= 1048576)
    rows = [row.split() for row in raw.decode('ascii').splitlines()[1:]]
    require(all(len(row) >= 10 for row in rows))
    matches = [row for row in rows if row[3] == '0A' and row[1].endswith(':0C7E')]
    require(len(matches) == 1 and matches[0][1] == '0100007F:0C7E'
            and re.fullmatch('[1-9][0-9]*', matches[0][9]))
    inode = matches[0][9]
    owned = None
    for index, entry in enumerate((proc / str(pid) / 'fd').iterdir()):
        require(index < 4096)
        try:
            if os.readlink(entry) == 'socket:[' + inode + ']':
                owned = entry
        except FileNotFoundError:
            continue
    require(owned is not None and os.readlink(owned) == 'socket:[' + inode + ']')
    return inode


def health():
    connection = http.client.HTTPConnection('127.0.0.1', 3198, timeout=2)
    try:
        connection.request('GET', '/health')
        response = connection.getresponse()
        raw = response.read(4097)
        require(response.status == 200 and len(raw) <= 4096)
        value = json.loads(raw)
        require(type(value) is dict and value.get('camera_present') is True and value.get('vision_ready') is False)
        return True
    finally:
        connection.close()


@dataclass(frozen=True)
class CameraIdentity:
    boot_id: str
    invocation: str
    pid: int
    start_ticks: int
    uid: int
    listener_inode: str


def verify_camera_identity(device, unit_sha256, helper_sha256, *, system=None, processes=None, health_check=None):
    require(os.geteuid() == 0 and sys.platform == 'linux')
    require(type(device) is str and re.fullmatch(r'/dev/v4l/by-id/[A-Za-z0-9_.:-]+-video-index[0-9]+', device))
    require(all(type(digest) is str and re.fullmatch('[0-9a-f]{64}', digest) for digest in (unit_sha256, helper_sha256)))
    system = SuspendSystem() if system is None else system
    processes = LinuxProcesses() if processes is None else processes
    health_check = health if health_check is None else health_check
    deadline = time.monotonic() + 10
    executable = PYTHON.resolve(strict=True)
    def observe():
        unit = trusted(UNIT, unit_sha256)
        helper = trusted(HELPER, helper_sha256)
        interpreter = trusted(executable)
        value = show(system, deadline, device)
        process = processes.read(int(value['MainPID']))
        require(process.pid == int(value['MainPID']) and process.uid > 0 and process.start_ticks > 0
                and process.executable == str(executable)
                and process.argv == (str(PYTHON), str(HELPER), '--device=' + device)
                and process.state not in ('Z', 'T', 't', 'X'))
        root = Path('/proc', str(process.pid))
        require((root / 'cgroup').read_text() == '0::/system.slice/' + SERVICE + '\n')
        running = (root / 'exe').stat()
        require((running.st_dev, running.st_ino) == interpreter[:2])
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        require(re.fullmatch('[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot))
        identity = CameraIdentity(boot, value['InvocationID'], process.pid, process.start_ticks, process.uid, listener(process.pid))
        require(show(system, deadline, device) == value and time.monotonic() < deadline)
        return identity, unit, helper, interpreter
    before = observe()
    require(health_check() is True)
    require(observe() == before)
    return before[0]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--device', required=True)
    parser.add_argument('--unit-sha256', required=True)
    parser.add_argument('--helper-sha256', required=True)
    args = parser.parse_args(argv)
    verify_camera_identity(args.device, args.unit_sha256, args.helper_sha256)
    print(json.dumps(dict(service_identity_verified=True, health_verified=True, capture_verified=False)))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Camera service identity/health not verified; no capture attempted.', file=sys.stderr)
        raise SystemExit(2) from None
