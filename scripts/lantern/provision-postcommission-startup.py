#!/usr/bin/env python3
"""Explicit fresh post-commissioning handoff/install phases; no same-boot start."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import types
import uuid


STATE = Path('/var/lib/sage-lantern-postcommission-startup')
INSTALLER_SHA = 'd3fd22d0f3d00d2032078fbc832882197c697d495ac4f8107e3f000ad86c488f'


def startup_inactive(system, installed=True):
    deadline = time.monotonic() + 10
    service = 'sage-lantern-startup.service'
    fields = ('LoadState', 'ActiveState', 'MainPID', 'Job', 'ControlGroup', 'Slice', 'NeedDaemonReload',
        'FragmentPath', 'DropInPaths', 'Transient')
    before = system._show(service, deadline, fields)
    require(before['LoadState'] == ('loaded' if installed else 'not-found') and before['NeedDaemonReload'] == 'no'
        and before['ActiveState'] == 'inactive' and before['MainPID'] == '0'
        and system._no_job(before['Job']) and before['Slice'] in (('system.slice',) if installed else ('', 'system.slice'))
        and before['DropInPaths'] == '' and before['Transient'] == 'no'
        and before['FragmentPath'] == ('/etc/systemd/system/' + service if installed else ''))
    group = '/system.slice/' + service
    require(before['ControlGroup'] in ('', group))
    system._empty_tree(system._cgroup(group), deadline)
    require(system._show(service, deadline, fields) == before)


def require(value):
    if value is not True:
        raise ValueError('postcommission_refused')


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 2097152)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
            for after in (os.fstat(descriptor), path.lstat()) for key in fields))
        return raw
    finally:
        os.close(descriptor)


def load(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = trusted(pin['path'])
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'])
    module = types.ModuleType('postcommission_' + pin['sha256'])
    module.__file__ = pin['path']
    sys.modules[module.__name__] = module
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


class Phase:
    def __init__(self, handoff_pin, dependencies_raw):
        self.handoff = load(handoff_pin)
        self.dependencies_raw = dependencies_raw
        self.dependencies = self.handoff.decode(dependencies_raw)
        deps = self.dependencies
        require(set(deps) == {'schema', 'unit_id', 'handoff', 'tools', 'installer', 'service'}
            and deps['schema'] == 'sage.lantern.postcommission-dependencies.v1'
            and deps['unit_id'] in ('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f') and deps['handoff'] == handoff_pin)
        require(deps['installer']['sha256'] == INSTALLER_SHA)
        self.installer = load(deps['installer'])
        self.tools_raw = self.handoff.pinned(deps['tools'])
        tools = self.handoff.decode(self.tools_raw)
        require(tools['unit_id'] == deps['unit_id'])
        self.backend = self.handoff.Native(tools, self.tools_raw)
        require(tools['coordinator']['path'] == str(self.installer.COORDINATOR))
        self.installer.startup = self.installer.load_coordinator(tools['coordinator']['sha256'])
        self.service = self.handoff.pinned(deps['service'])

    def integrity(self):
        for name in ('handoff', 'tools', 'installer', 'service'):
            self.handoff.pinned(self.dependencies[name])

    def absent_service(self):
        from sage_lantern.suspend_system import SuspendSystem
        startup_inactive(SuspendSystem(), installed=False)

    def installed_handoff(self, snapshot, record):
        module = self.handoff
        directory = module.HANDOFF / snapshot['boot_id']
        original = module.decode(module.trusted(directory / 'intent.json'))
        require(original['schema'] == 'sage.lantern.fresh-audio-handoff.v1')
        require({key: value for key, value in original.items() if key != 'access_epoch'} ==
            {key: value for key, value in snapshot.items() if key != 'access_epoch'})
        documents = module.documents(original, self.backend.tools, record)
        complete = module.trusted(directory / 'complete.json')
        require(module.decode(complete) == dict(schema='sage.lantern.fresh-audio-handoff-result.v1',
            unit_id=snapshot['unit_id'], boot_id=snapshot['boot_id'],
            files={str(path): module.sha(raw) for path, raw in documents.items()},
            services_started=False, services_enabled=False, reboot_accepted=False))
        require(all(module.trusted(path) == raw for path, raw in documents.items()))
        return documents, module.sha(complete)

    def inspect(self, phase):
        require(phase in ('handoff', 'install-startup'))
        self.integrity()
        self.absent_service()
        snapshot, record = self.backend.snapshot()
        require(snapshot['unit_id'] == self.dependencies['unit_id'])
        documents = self.handoff.documents(snapshot, self.backend.tools, record)
        config_path = self.handoff.HANDOFF / snapshot['boot_id'] / 'startup.json'
        require(not os.path.lexists(self.installer.SERVICE)
            and not os.path.lexists(self.installer.startup.CONFIG)
            and not os.path.lexists('/etc/systemd/system/multi-user.target.wants/sage-lantern-startup.service'))
        receipt = None
        if phase == 'handoff':
            require(not os.path.lexists(self.handoff.HANDOFF / snapshot['boot_id'])
                and not os.path.lexists(self.handoff.LIFETIMES / snapshot['boot_id']))
            self.backend.coordinator.validate_full_config(self.handoff.decode(documents[config_path]))
        else:
            documents, receipt = self.installed_handoff(snapshot, record)
            self.installer.preflight(documents[config_path], self.service)
        self.integrity()
        self.absent_service()
        require(self.backend.snapshot() == (snapshot, record))
        return dict(schema='sage.lantern.postcommission-phase.v1', phase=phase,
            dependencies_sha256=self.handoff.sha(self.dependencies_raw), snapshot=snapshot,
            config_sha256=self.handoff.sha(documents[config_path]), handoff_receipt_sha256=receipt)

    def claim(self, expected, attempt_id):
        require(str(uuid.UUID(attempt_id)) == attempt_id and uuid.UUID(attempt_id).int > 0)
        helper = self.backend.helper
        helper.parents(STATE)
        if not os.path.lexists(STATE):
            STATE.mkdir(mode=0o700)
            helper.sync(STATE.parent)
        info = STATE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        self.lock = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.directory = STATE / attempt_id
        self.directory.mkdir(mode=0o700)
        helper.sync(STATE)
        self.record('intent', expected)

    def record(self, name, value):
        self.backend.helper.write(self.directory / (name + '.json'), self.handoff.canonical(value))

    def install(self, expected):
        path = self.handoff.HANDOFF / expected['snapshot']['boot_id'] / 'startup.json'
        args = ['/opt/sage-lantern/venv/bin/python', '-I', '-B', self.dependencies['installer']['path'],
            '--config', str(path), '--config-sha256', expected['config_sha256'],
            '--service', self.dependencies['service']['path'], '--service-sha256', self.dependencies['service']['sha256'],
            '--coordinator-sha256', self.backend.tools['coordinator']['sha256'], '--apply']
        subprocess.run(args, check=True, timeout=90, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=self.backend.coordinator.ENV)
        require(self.handoff.trusted(self.installer.startup.CONFIG) == self.handoff.trusted(path)
            and self.handoff.trusted(self.installer.SERVICE) == self.service)
        link = Path('/etc/systemd/system/multi-user.target.wants/sage-lantern-startup.service')
        self.backend.helper.parents(link)
        require(link.is_symlink() and link.lstat().st_uid == 0 and link.resolve() == self.installer.SERVICE)
        from sage_lantern.suspend_system import SuspendSystem
        system = SuspendSystem()
        startup_inactive(system)


def apply(backend, expected, attempt_id):
    require(backend.inspect(expected['phase']) == expected)
    backend.claim(expected, attempt_id)
    try:
        require(backend.inspect(expected['phase']) == expected)
        if expected['phase'] == 'handoff':
            result = backend.handoff.apply(backend.backend, expected['snapshot'])
        else:
            backend.install(expected)
            backend.integrity()
            require(backend.backend.snapshot()[0] == expected['snapshot'])
            result = dict(installed=True, enabled=True, services_started=False, reboot_accepted=False)
        backend.record('complete', result)
        return result
    except BaseException as error:
        try:
            backend.record('failed', dict(error_class=type(error).__name__, phase=expected['phase'],
                partial_state_must_be_inspected=True, retry_authorized=False))
        except BaseException:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--handoff', required=True)
    parser.add_argument('--handoff-sha256', required=True)
    parser.add_argument('--dependencies', required=True)
    parser.add_argument('--dependencies-sha256', required=True)
    parser.add_argument('--phase', required=True, choices=('handoff', 'install-startup'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--attempt-id')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    require(bool(args.expected) == bool(args.expected_sha256) == bool(args.attempt_id) == args.apply)
    def cancelled(signum, frame):
        raise InterruptedError('postcommission_cancelled')
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(number, cancelled)
    signal.alarm(400)
    raw = trusted(args.dependencies)
    require(hashlib.sha256(raw).hexdigest() == args.dependencies_sha256)
    backend = Phase({'path': args.handoff, 'sha256': args.handoff_sha256}, raw)
    if args.apply:
        expected = backend.handoff.decode(backend.handoff.pinned({'path': args.expected, 'sha256': args.expected_sha256}))
        require(expected['phase'] == args.phase)
        result = apply(backend, expected, args.attempt_id)
    else:
        result = backend.inspect(args.phase)
    print(backend.handoff.canonical(result).decode())


if __name__ == '__main__':
    main()
