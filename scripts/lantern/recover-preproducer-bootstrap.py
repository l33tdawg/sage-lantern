#!/usr/bin/env python3
"""Explicit failed-before-voice archival; never a drain or bootstrap retry.

Expected schema sage.lantern.preproducer-recovery.v1 has boot_id, broker_id,
services (VOICE/BROKER/MODEL exact FIELDS snapshots), marker_parent {uid,mode}, files (exact FILES paths
to {sha256,size,mode,gid}), and configuration (loaded unit/drop-in SHA256 pins).
Default observes and temporarily acquires the existing owner flock. --apply
creates a retained exclusive journal, archives exact artifacts, then resets
only broker failure state. No starts, capture, unit edits, or invented drains.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import subprocess
import sys


ATTEMPT = Path('/var/lib/sage-lantern-preproducer-recovery')
SUPERVISOR = Path('/var/lib/sage-lantern-mesh-supervisor')
BOOTSTRAP = Path('/run/sage-lantern-voice-bootstrap.json')
MARKER = Path('/var/lib/sage-lantern/LOCAL_VOICE_READY')
RECEIPT = Path('/etc/sage-lantern/audio-capture.json')
COMMISSION = Path('/etc/sage-lantern/audio-commissioned.json')
FILES = (MARKER, RECEIPT, COMMISSION, BOOTSTRAP, SUPERVISOR / 'owner.lock', SUPERVISOR / 'fence.json')
ABSENT = tuple(Path(path) for path in ('/run/sage-lantern-mesh-control/broker.sock',
    '/run/sage-lantern-mesh-control/voice-start-authorized.json', '/run/sage-lantern-voice',
    '/run/sage-lantern-audio-validation.pending'))
VOICE = 'sage-lantern-voice-loop.service'
BROKER = 'sage-lantern-mesh-runtime.service'
MODEL = 'sage-lantern-llm.service'
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'InvocationID', 'Job', 'ControlGroup',
    'Slice', 'KillMode', 'Restart', 'Result', 'ExecMainStartTimestampMonotonic',
    'ExecMainExitTimestampMonotonic', 'ExecMainCode', 'ExecMainStatus', 'NRestarts',
    'FragmentPath', 'DropInPaths', 'NeedDaemonReload')


def require(value):
    if not value:
        raise ValueError('preproducer_recovery_refused')


def decode(raw):
    def unique(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=unique)


def parents(path, *, marker_parent=None):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        if path == MARKER and parent == MARKER.parent and marker_parent is not None:
            require(set(marker_parent) == {'uid', 'mode'} and type(marker_parent['uid']) is int
                    and marker_parent['uid'] > 0 and marker_parent['mode'] == 0o700
                    and stat.S_ISDIR(info.st_mode) and info.st_uid == marker_parent['uid']
                    and stat.S_IMODE(info.st_mode) == marker_parent['mode'])
        else:
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def read(path, limit=262144, *, marker_parent=None):
    path = Path(path)
    parents(path, marker_parent=marker_parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= limit)
        raw = os.read(descriptor, limit + 1)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                    info.st_size, info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) <= limit and identity(before) == identity(os.fstat(descriptor))
                and identity(before) == identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw, mode=0o600, gid=0):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), 0, gid)
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def command(arguments):
    require(arguments == ['reset-failed', BROKER] or arguments[:1] == ['show'])
    result = subprocess.run(['/usr/bin/systemctl', *arguments], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=5, check=False,
        env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    require(result.returncode == 0 and len(result.stdout) <= 65536)
    return result.stdout.decode()


def show(service):
    require(service in (VOICE, BROKER, MODEL))
    arguments = ['show', '--no-pager', '--all']
    for field in FIELDS:
        arguments.extend(['-p', field])
    value = {}
    for line in command(arguments + [service]).splitlines():
        key, separator, content = line.partition('=')
        require(separator and key in FIELDS and key not in value)
        value[key] = content
    require(set(value) == set(FIELDS))
    return value


def empty(service):
    path = Path('/sys/fs/cgroup/system.slice') / service
    parents(path)
    if os.path.lexists(path):
        require(stat.S_ISDIR(path.lstat().st_mode))
        events = read(path / 'cgroup.events', 4096).decode().splitlines()
        require([line for line in events if line.startswith('populated ')] == ['populated 0']
                and not read(path / 'cgroup.procs', 4096).strip())


def services(expected, reset=False):
    require(set(expected) == {VOICE, BROKER, MODEL})
    for service, before in expected.items():
        require(set(before) == set(FIELDS))
        value = show(service)
        wanted = dict(before)
        alternatives = [wanted]
        if service == BROKER and reset:
            wanted.update(ActiveState='inactive', SubState='dead', Result='success')
            alternatives.append(dict(wanted, InvocationID='', ExecMainStartTimestampMonotonic='0',
                ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0'))
        require(value in alternatives and value['LoadState'] == 'loaded' and value['Job'] in ('', '0')
                and value['NeedDaemonReload'] == 'no' and value['Slice'] == 'system.slice'
                and value['KillMode'] == 'control-group')
        if service == MODEL:
            require(value['ActiveState'] == 'active' and re.fullmatch('[1-9][0-9]*', value['MainPID']))
        else:
            require(value['MainPID'] == '0' and value['NRestarts'] == '0' and value['Restart'] == 'no'
                    and value['ControlGroup'] in ('', '/system.slice/' + service))
            if service == VOICE:
                require(value['ActiveState'] == 'inactive'
                        and value['ExecMainStartTimestampMonotonic'] == value['ExecMainExitTimestampMonotonic'] == '0'
                        and value['ExecMainCode'] == value['ExecMainStatus'] == '0')
            else:
                require(before['ActiveState'] == 'failed'
                        and 0 < int(before['ExecMainStartTimestampMonotonic']) < int(before['ExecMainExitTimestampMonotonic']))
            empty(service)
        require(show(service) == value)


class Recovery:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'boot_id', 'broker_id', 'services', 'files', 'configuration', 'marker_parent'}
                and expected['schema'] == 'sage.lantern.preproducer-recovery.v1'
                and re.fullmatch('[0-9a-f]{32}', expected['broker_id'])
                and set(expected['files']) == {str(path) for path in FILES})
        require(expected['marker_parent'] == dict(uid=pwd.getpwnam('sage-lantern').pw_uid, mode=0o700)
                and expected['marker_parent']['uid'] > 0)
        self.expected, self.moved, self.reset = expected, set(), False
        self.owner = None
        for service, value in expected['services'].items():
            paths = [value['FragmentPath'], *value['DropInPaths'].split()]
            require(value['FragmentPath'] == '/etc/systemd/system/' + service)
            require(len(paths) == len(set(paths)) and set(paths) <= expected['configuration'].keys())
        for path in expected['configuration']:
            require(any(path == '/etc/systemd/system/' + service or
                re.fullmatch(re.escape('/etc/systemd/system/' + service + '.d/') + r'[A-Za-z0-9_.-]+\.conf', path)
                for service in (VOICE, BROKER, MODEL)))
        self.current()
        self.owner = os.open(SUPERVISOR / 'owner.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.current()
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.owner is not None:
            os.close(self.owner)
            self.owner = None

    def location(self, path):
        if path in self.moved:
            return ATTEMPT / ('supervisor/' + path.name if path.parent == SUPERVISOR else path.name)
        return path

    def file(self, path, actual=None):
        actual = self.location(path) if actual is None else actual
        raw = read(actual, marker_parent=self.expected['marker_parent'] if actual == MARKER else None)
        info = actual.lstat()
        pin = self.expected['files'][str(path)]
        require(set(pin) == {'sha256', 'size', 'mode', 'gid'}
                and sha(raw) == pin['sha256'] and len(raw) == pin['size'] == info.st_size
                and stat.S_IMODE(info.st_mode) == pin['mode'] and info.st_gid == pin['gid'])
        return raw

    def current(self):
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        for path in ABSENT:
            require(not os.path.lexists(path))
        for path, digest in self.expected['configuration'].items():
            require(sha(read(path)) == digest)
        for path in FILES:
            self.file(path)
            if path in self.moved:
                require(not os.path.lexists(path))
        supervisor = ATTEMPT / 'supervisor' if SUPERVISOR / 'owner.lock' in self.moved else SUPERVISOR
        if supervisor != SUPERVISOR:
            require(not os.path.lexists(SUPERVISOR))
        require({entry.name for entry in supervisor.iterdir()} == {'owner.lock', 'fence.json', 'serial'})
        info = supervisor.lstat()
        serial = supervisor / 'serial'
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        parents(serial / 'child')
        serial_info = serial.lstat()
        require(stat.S_IMODE(serial_info.st_mode) == 0o700 and serial_info.st_dev == info.st_dev and not list(serial.iterdir()))
        require(self.file(SUPERVISOR / 'owner.lock') == b'')
        require(decode(self.file(SUPERVISOR / 'fence.json')) == dict(schema='sage.lantern.storage-supervisor.v1',
            broker_id=self.expected['broker_id'], phase='recovery_required'))
        record = decode(self.file(BOOTSTRAP))
        require(record['schema'] == 'sage.lantern.voice-bootstrap.v1' and record['boot_id'] == self.expected['boot_id']
                and record['phase'] == 'failed' and record['start_attempted'] is True
                and record['containment_verified'] is False and record['recovery_required'] is True)
        if self.owner is not None:
            opened = os.fstat(self.owner)
            current = (supervisor / 'owner.lock').lstat()
            require((opened.st_dev, opened.st_ino) == (current.st_dev, current.st_ino)
                    and stat.S_IMODE(current.st_mode) == 0o600)
            fcntl.flock(self.owner, fcntl.LOCK_EX | fcntl.LOCK_NB)
        services(self.expected['services'], self.reset)

    def archive_file(self, path):
        require(path in (MARKER, RECEIPT, COMMISSION, BOOTSTRAP) and path not in self.moved)
        raw = self.file(path)
        pin = self.expected['files'][str(path)]
        destination = ATTEMPT / path.name
        write(destination, raw, pin['mode'], pin['gid'])
        self.file(path, destination)
        self.current()
        path.unlink()
        sync(path.parent)
        self.moved.add(path)

    def archive_supervisor(self):
        require({MARKER, RECEIPT, COMMISSION, BOOTSTRAP} <= self.moved and self.owner is not None)
        destination = ATTEMPT / 'supervisor'
        require(not os.path.lexists(destination) and SUPERVISOR.stat().st_dev == ATTEMPT.stat().st_dev)
        self.current()
        descriptor = os.open(SUPERVISOR / 'fence.json', os.O_RDONLY | os.O_NOFOLLOW)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.fsync(self.owner)
        sync(SUPERVISOR / 'serial')
        sync(SUPERVISOR)
        os.rename(SUPERVISOR, destination)
        sync(SUPERVISOR.parent)
        sync(ATTEMPT)
        self.moved.update((SUPERVISOR / 'owner.lock', SUPERVISOR / 'fence.json'))

    def reset_broker(self):
        require(self.moved == set(FILES) and not self.reset)
        self.current()
        command(['reset-failed', BROKER])
        self.reset = True


def transition(backend, record):
    actions = [(path.name, lambda path=path: backend.archive_file(path)) for path in (MARKER, RECEIPT, COMMISSION, BOOTSTRAP)]
    actions += [('supervisor', backend.archive_supervisor), ('reset_broker', backend.reset_broker)]
    for name, action in actions:
        backend.current()
        record(name + '_intent')
        action()
        backend.current()
        record(name + '_done')
    return dict(preproducer_attempt_archived=True, current_quiescence_verified=True,
        historical_containment_verified=False, drain_claimed=False, services_started=False,
        audio_recommissioning_required=True, retry_authorized=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(ATTEMPT))
    def expired(signum, frame):
        raise TimeoutError('preproducer_recovery_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(120)
    backend = None
    try:
        raw = read(args.expected)
        require(sha(raw) == args.expected_sha256)
        backend = Recovery(decode(raw))
        if not args.apply:
            print(json.dumps(dict(preproducer_checkpoint_verified=True, mutation_authorized=False, drain_claimed=False)))
            return
        parents(ATTEMPT)
        ATTEMPT.mkdir(mode=0o700)
        sync(ATTEMPT.parent)
        write(ATTEMPT / 'expected.json', raw)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            write(ATTEMPT / f'{counter:02d}-{phase}.json', json.dumps(dict(phase=phase, expected_sha256=args.expected_sha256)).encode())
        print(json.dumps(transition(backend, record)))
    finally:
        if backend is not None:
            backend.close()
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        frames = []
        trace = error.__traceback__
        while trace is not None:
            frames.append(dict(file=trace.tb_frame.f_code.co_filename, line=trace.tb_lineno))
            trace = trace.tb_next
        print(json.dumps(dict(error_type=type(error).__name__, frames=frames, recovery_required=True,
                              automatic_retry=False)))
        raise SystemExit(2) from None
