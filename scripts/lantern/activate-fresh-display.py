#!/usr/bin/env python3
"""Pinned fresh A/B display activation; boot enablement is a separate operation."""

import argparse
import fcntl
from dataclasses import asdict
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time
import types
import uuid
import subprocess


UNIT = 'sage-lantern-display-power.service'
AUTH = 'sage-lantern-device-auth.service'
UNIT_PATH = Path('/etc/systemd/system') / UNIT
SUPERVISOR = Path('/usr/local/libexec/sage-lantern-display-session')
POWER = Path('/usr/local/libexec/sage-lantern-display-power')
POLICY = Path('/etc/sage-lantern-display-power.enabled')
LINK = Path('/etc/systemd/system/graphical.target.wants') / UNIT
STATE = Path('/var/lib/sage-lantern-display-activation')
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')


def require(value):
    if value is not True:
        raise ValueError('fresh_display_refused')


def decode(raw):
    def pairs(items):
        value = {}
        for key, member in items:
            require(key not in value)
            value[key] = member
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def parents(path):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def identity(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def read(path):
    path = Path(path)
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 16777216)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(16777217)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
            == identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw, mode=0o600):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def pin(document):
    require(type(document) is dict and set(document) == {'path', 'sha256'})
    raw = read(document['path'])
    require(sha(raw) == document['sha256'])
    return raw


def session_public(session):
    value = decode(encoded(asdict(session)))
    value['launcher'].pop('state')
    return value


def listener_owner(process_pin, boot_id, proc=Path('/proc')):
    process = proc / str(process_pin['pid'])
    def current():
        require((process / 'stat').read_text().rsplit(')', 1)[1].split()[19]
            == str(process_pin['start_ticks'])
            and (proc / 'sys/kernel/random/boot_id').read_text().strip() == boot_id)
    def listening():
        matches = []
        for name in ('tcp', 'tcp6'):
            with (proc / 'net' / name).open('rb') as stream:
                raw = stream.read(1048577)
            require(len(raw) <= 1048576)
            for row in raw.decode('ascii').splitlines()[1:]:
                fields = row.split()
                require(len(fields) >= 10)
                if fields[3] == '0A' and fields[1].endswith(':0C7C'):
                    matches.append((name, fields[1], fields[9]))
        require(len(matches) == 1 and matches[0][:2] == ('tcp', '0100007F:0C7C')
            and matches[0][2].isdecimal() and int(matches[0][2]) > 0)
        return matches[0][2]
    current()
    inode = listening()
    owned = None
    for index, entry in enumerate((process / 'fd').iterdir()):
        require(index < 4096)
        try:
            if os.readlink(entry) == 'socket:[' + inode + ']':
                owned = entry
        except FileNotFoundError:
            continue
    require(owned is not None and os.readlink(owned) == 'socket:[' + inode + ']'
        and listening() == inode)
    current()
    return inode


def in_unit(process, unit, proc=Path('/proc')):
    with (proc / str(process.pid) / 'cgroup').open('rb') as stream:
        raw = stream.read(16385)
    require(len(raw) <= 16384 and raw.decode().splitlines() == ['0::/system.slice/' + unit])


class Native:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'attempt_id', 'unit_id', 'boot_id', 'files',
            'session', 'policy', 'auth_process', 'auth_dropins'})
        require(expected['schema'] == 'sage.lantern.fresh-display.v1'
            and expected['unit_id'] in ('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'))
        require(str(uuid.UUID(expected['attempt_id'])) == expected['attempt_id']
            and uuid.UUID(expected['attempt_id']).int > 0)
        require(str(uuid.UUID(expected['boot_id'])) == expected['boot_id']
            and uuid.UUID(expected['boot_id']).int > 0)
        self.expected = expected
        self.deadline = time.monotonic() + 90
        self.created_policy = False
        self.started = False
        self.enable_attempted = False
        self.display_process = None
        self.acceptance = None
        self.directory = STATE / expected['attempt_id']
        self.files()
        self.session = types.ModuleType('pinned_fresh_display_session')
        self.session.__file__ = str(SUPERVISOR)
        sys.modules[self.session.__name__] = self.session
        exec(compile(read(SUPERVISOR), str(SUPERVISOR), 'exec'), self.session.__dict__)
        self.native = self.session.Native()

    def files(self):
        files = self.expected['files']
        dropins = self.expected['auth_dropins']
        require(type(dropins) is dict and len(dropins) <= 32)
        for path, digest in dropins.items():
            location = Path(path)
            require(str(location.parent) in ('/etc/systemd/system/' + AUTH + '.d',
                '/run/systemd/system/' + AUTH + '.d') and location.suffix == '.conf'
                and files.get(path) == digest and sha(read(location)) == digest)
        site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages/sage_lantern'
        required = {str(path) for path in site.glob('*.py')} | {
            str(SUPERVISOR), str(POWER), str(UNIT_PATH), '/etc/systemd/system/' + AUTH,
            '/usr/local/libexec/sage-lantern-device-auth', '/usr/local/bin/sage-lantern-session',
            '/usr/local/bin/sage-lantern-kiosk', '/etc/sage-lantern/unit.env',
            '/etc/sage-lantern/appliance-bindings.json', '/etc/sage-lantern/kiosk-boot.json',
            '/etc/sage-lantern/appliance-installed.json', str(Path(__file__).resolve()),
            str(Path('/usr/bin/python3').resolve()), '/usr/bin/bash', '/usr/bin/systemctl'}
        require(type(files) is dict and required <= set(files) and len(files) <= 200)
        require(all(sha(read(path)) == checksum for path, checksum in files.items()))
        require(read('/etc/sage-lantern/unit.env') ==
            f"UNIT_ID={self.expected['unit_id']}\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n".encode())
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])

    def command(self, *arguments):
        require(time.monotonic() < self.deadline)
        result = subprocess.run(['/usr/bin/systemctl', '--no-pager', *arguments],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=min(10, self.deadline - time.monotonic()), env=ENV, check=True)
        require(len(result.stdout) <= 65536)
        return result.stdout.decode().strip()

    def show(self, unit=UNIT):
        fields = ('LoadState', 'ActiveState', 'MainPID', 'ControlPID', 'InvocationID', 'Job',
            'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Transient', 'UnitFileState',
            'ControlGroup', 'KillMode', 'NRestarts')
        raw = self.command('show', unit, *[f'--property={name}' for name in fields])
        value = {}
        for line in raw.splitlines():
            name, separator, member = line.partition('=')
            require(separator == '=' and name not in value)
            value[name] = member
        require(set(value) == set(fields))
        require(value['LoadState'] == 'loaded' and value['Job'] in ('', '0')
            and value['ControlPID'] == '0' and value['NeedDaemonReload'] == 'no'
            and value['Transient'] == 'no'
            and value['FragmentPath'] == '/etc/systemd/system/' + unit
            and value['KillMode'] == 'control-group')
        dropins = value['DropInPaths'].split()
        expected = self.expected['auth_dropins'] if unit == AUTH else {}
        require(len(dropins) == len(set(dropins)) and set(dropins) == set(expected))
        require(all(sha(read(path)) == digest for path, digest in expected.items()))
        return value

    def process(self, unit):
        before = self.show(unit)
        require(before['ActiveState'] == 'active' and int(before['MainPID']) > 1)
        process = self.native.processes.read(int(before['MainPID']))
        image = Path('/proc') / before['MainPID'] / 'exe'
        with image.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        after = self.native.processes.read(process.pid)
        require(self.show(unit) == before and process.state not in ('Z', 'X')
            and after.state not in ('Z', 'X')
            and (after.pid, after.start_ticks, after.executable, after.argv, after.uid)
            == (process.pid, process.start_ticks, process.executable, process.argv, process.uid))
        return dict(pid=process.pid, start_ticks=process.start_ticks,
            invocation=before['InvocationID'], image_sha256=digest)

    def setup(self):
        require(self.process(AUTH) == self.expected['auth_process'])
        listener = listener_owner(self.expected['auth_process'], self.expected['boot_id'])
        connection = http.client.HTTPConnection('127.0.0.1', 3196, timeout=2)
        try:
            connection.request('POST', '/setup/status', body=b'{}', headers={
                'Origin': 'http://127.0.0.1:3100', 'Content-Type': 'application/json',
                'X-Lantern-Auth': 'control'})
            response = connection.getresponse()
            raw = response.read(4097)
            require(response.status == 200 and len(raw) <= 4096)
            value = decode(raw)
            require(value.get('enabled') is True and value.get('recovery_backup_confirmed') is True)
        finally:
            connection.close()
        require(self.process(AUTH) == self.expected['auth_process'])
        require(listener_owner(self.expected['auth_process'], self.expected['boot_id']) == listener)

    def policy(self):
        expected = self.expected['policy']
        parents(POLICY)
        if expected == {'state': 'absent'} and not self.created_policy:
            require(not os.path.lexists(POLICY))
            return
        raw = read(POLICY)
        require(stat.S_IMODE(POLICY.stat().st_mode) == 0o644
            and raw == f"fresh {self.expected['unit_id']}\n".encode())
        if not self.created_policy:
            require(set(expected) == {'state', 'sha256'} and expected['state'] == 'present'
                and sha(raw) == expected['sha256'])

    def empty(self):
        value = self.show()
        require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0')
        group = Path('/sys/fs/cgroup/system.slice') / UNIT
        if os.path.lexists(group):
            pending = [group]
            count = 0
            while pending:
                directory = pending.pop()
                count += 1
                require(count <= 1024 and stat.S_ISDIR(directory.lstat().st_mode))
                before = directory.lstat()
                path = directory / 'cgroup.procs'
                require(not path.is_symlink() and path.read_text().strip() == '')
                for member in directory.iterdir():
                    info = member.lstat()
                    require(not stat.S_ISLNK(info.st_mode))
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(member)
                require(directory.lstat().st_ino == before.st_ino)
        require(self.show() == value)

    def ready(self):
        deadline = min(self.deadline, time.monotonic() + 15)
        initial = None
        while time.monotonic() < deadline:
            value = self.show()
            require(value['NRestarts'] == '0' and value['ActiveState'] == 'active')
            process = self.native.processes.read(int(value['MainPID']))
            current = (process.pid, process.start_ticks, value['InvocationID'])
            require(process.pid > 1 and process.state not in ('Z', 'X'))
            require(process.uid == 0 and value['ControlGroup'] == '/system.slice/' + UNIT)
            in_unit(process, UNIT)
            if initial is None:
                initial = current
            require(current == initial)
            if process.argv == ('/opt/sage-lantern/venv/bin/python', '-I', '-B', str(SUPERVISOR), '--serve'):
                observed = self.process(UNIT)
                require(observed['image_sha256'] == self.expected['files'][str(Path('/usr/bin/python3').resolve())])
                if self.display_process is not None:
                    require(observed == self.display_process)
                self.display_process = observed
                children = [child for child in self.native.snapshot(deadline, None).values()
                    if child.parent == process.pid and child.uid == self.expected['session']['binding']['uid']
                    and child.argv == ('/usr/bin/python3', str(POWER)) and child.state not in ('Z', 'X')]
                require(len(children) <= 1)
                if children:
                    child = children[0]
                    in_unit(child, UNIT)
                    with (Path('/proc') / str(child.pid) / 'exe').open('rb') as stream:
                        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
                    require(digest == self.expected['files'][str(Path('/usr/bin/python3').resolve())])
                    after = self.native.processes.read(child.pid)
                    require((after.pid, after.start_ticks, after.parent, after.uid, after.argv)
                        == (child.pid, child.start_ticks, child.parent, child.uid, child.argv)
                        and after.state not in ('Z', 'X') and self.process(UNIT) == observed)
                    in_unit(after, UNIT)
                    supervisor = self.native.processes.read(process.pid)
                    require(supervisor.uid == 0 and supervisor.start_ticks == process.start_ticks
                        and supervisor.argv == process.argv and supervisor.state not in ('Z', 'X'))
                    in_unit(supervisor, UNIT)
                    return observed
                time.sleep(0.05)
                continue
            require(process.executable == '/usr/lib/systemd/systemd-executor'
                or process.executable == '/usr/lib/systemd/systemd')
            require(process.executable in self.expected['files']
                and self.process(UNIT)['image_sha256'] == self.expected['files'][process.executable])
            time.sleep(0.05)
        raise TimeoutError('display_exec_not_ready')

    def check(self, active=False, enabled=False):
        if self.acceptance is not None:
            boot_acceptance(pin(self.acceptance), self.expected)
        self.files()
        self.setup()
        self.policy()
        binding = self.session.binding_for('fresh', self.expected['unit_id'])
        observed = self.native.check_x11(binding)
        require(session_public(observed) == self.expected['session'])
        value = self.show()
        require(value['UnitFileState'] == ('enabled' if enabled else 'disabled'))
        if enabled:
            parents(LINK)
            require(LINK.is_symlink() and LINK.lstat().st_uid == 0 and os.readlink(LINK) == str(UNIT_PATH))
        else:
            require(not os.path.lexists(LINK))
        if active:
            self.ready()
        else:
            self.empty()
        self.files()

    def claim(self, raw):
        parents(STATE)
        if not STATE.exists():
            STATE.mkdir(mode=0o700)
            sync(STATE.parent)
        require(not STATE.is_symlink() and STATE.stat().st_uid == 0
            and stat.S_IMODE(STATE.stat().st_mode) == 0o700)
        self.lock_descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(self.lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.directory.mkdir(mode=0o700)
        sync(STATE)
        write(self.directory / 'intent.json', raw)

    def record(self, name, value):
        write(self.directory / (name + '.json'), encoded(value))

    def cleanup(self):
        self.deadline = time.monotonic() + 30
        self.files()
        if self.enable_attempted and os.path.lexists(LINK):
            parents(LINK)
            require(LINK.is_symlink() and LINK.lstat().st_uid == 0 and os.readlink(LINK) == str(UNIT_PATH))
            LINK.unlink()
            sync(LINK.parent)
            self.command('daemon-reload')
        if self.started or self.enable_attempted:
            self.show()
            self.command('stop', UNIT)
            self.empty()
        require(not os.path.lexists(LINK))
        return True


def perform(backend, raw, *, enable=False):
    backend.check(active=enable)
    backend.claim(raw)
    try:
        backend.check(active=enable)
        if enable:
            backend.record('enable-intent', {'physical_acceptance_is_operator_attestation': True,
                'acceptance': backend.acceptance})
            backend.enable_attempted = True
            backend.command('enable', UNIT)
            backend.check(active=True, enabled=True)
        else:
            if backend.expected['policy'] == {'state': 'absent'}:
                backend.record('policy-create-intent', {'unit_id': backend.expected['unit_id']})
                write(POLICY, f"fresh {backend.expected['unit_id']}\n".encode(), 0o644)
                backend.created_policy = True
            backend.check()
            backend.record('start-intent', {'service': UNIT})
            backend.started = True
            backend.command('start', UNIT)
            backend.check(active=True)
        result = dict(unit_id=backend.expected['unit_id'], boot_id=backend.expected['boot_id'],
            expected_sha256=sha(raw), display_active=True, boot_enabled=enable,
            display_process=backend.display_process,
            physical_verified=False, reboot_verified=False)
        backend.record('complete', result)
        return result
    except BaseException as error:
        for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
            signal.signal(number, signal.SIG_IGN)
        contained = False
        try:
            contained = backend.cleanup() is True
        except BaseException:
            pass
        try:
            backend.record('failed', {'error_class': type(error).__name__,
                'display_stopped_boot_disabled': contained, 'retry_authorized': False})
        except BaseException:
            pass
        raise


def boot_acceptance(raw, expected):
    value = decode(raw)
    require(set(value) == {'schema', 'unit_id', 'activation', 'screen_off', 'touch_wake',
        'pin_required', 'operator_approved'})
    require(value['schema'] == 'sage.lantern.display-physical-acceptance.v1'
        and value['unit_id'] == expected['unit_id']
        and all(value[name] is True for name in ('screen_off', 'touch_wake', 'pin_required', 'operator_approved')))
    receipt = decode(pin(value['activation']))
    require(receipt['unit_id'] == expected['unit_id'] and receipt['boot_id'] == expected['boot_id']
        and receipt['display_active'] is True and receipt['boot_enabled'] is False)
    activation = Path(value['activation']['path'])
    require(activation.name == 'complete.json' and activation.parent.parent == STATE)
    previous = read(activation.parent / 'intent.json')
    require(sha(previous) == receipt['expected_sha256'])
    previous = decode(previous)
    require(previous['attempt_id'] != expected['attempt_id'])
    for field in ('unit_id', 'boot_id', 'files', 'session', 'auth_process', 'auth_dropins'):
        require(previous[field] == expected[field])
    require(expected['policy'] == {'state': 'present',
        'sha256': sha(f"fresh {expected['unit_id']}\n".encode())})
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--boot-acceptance')
    parser.add_argument('--boot-acceptance-sha256')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux')
    require(bool(args.boot_acceptance) == bool(args.boot_acceptance_sha256))
    raw = pin({'path': args.expected, 'sha256': args.expected_sha256})
    expected = decode(raw)
    if args.boot_acceptance:
        boot_acceptance(pin({'path': args.boot_acceptance, 'sha256': args.boot_acceptance_sha256}), expected)
    def interrupted(signum, frame):
        raise InterruptedError('fresh_display_cancelled')
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(number, interrupted)
    signal.alarm(90)
    backend = Native(expected)
    if args.boot_acceptance:
        backend.acceptance = {'path': args.boot_acceptance, 'sha256': args.boot_acceptance_sha256}
        accepted = boot_acceptance(pin(backend.acceptance), expected)
        backend.display_process = decode(pin(accepted['activation']))['display_process']
    try:
        if args.apply:
            result = perform(backend, raw, enable=bool(args.boot_acceptance))
        else:
            backend.check(active=bool(args.boot_acceptance))
            result = dict(inspected=True, activated=False, boot_enable_authorized=False)
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
