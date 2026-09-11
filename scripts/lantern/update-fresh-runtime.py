#!/usr/bin/env python3
"""Cold Unit A runtime-only replacement after verified native capability recovery.

Default is read-only preflight. --apply creates retained root journal/guards,
stops camera/display/status consumers, installs only the pinned runtime wheel,
and leaves every consumer stopped. No UI/helper/native install or activation.
Failure retains old wheel and containment for manual recovery, never retry.
See docs/lantern/COLD_RUNTIME_UPDATE.md for the expected public input schema.
"""

import argparse
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import types
import zipfile


VENV = Path('/opt/sage-lantern/venv')
SITE = VENV / 'lib/python3.11/site-packages'
STATE = Path('/var/lib/sage-lantern-cold-runtime-update')
CONSUMERS = ('sage-lantern-camera.service', 'sage-lantern-display-power.service', 'sage-lantern-status.service')
GUARD_NAME = 'zzzz-fresh-runtime-update.conf'
GUARD = ('[Unit]\nConditionPathExists=!' + str(STATE) + '\n[Service]\nRestart=no\n').encode()
WHEEL_SHA = '163a219664f8b60f72d4a1905cac965d67adf11a0b657a4ece40cbbb6f5a3d78'
DIST = 'sage_lantern_runtime-0.2.0.dist-info/'
BACKUP = 'sage_lantern_runtime-0.2.0-py3-none-any.whl'
HELPERS = {'voice_loop': 'unit-a-voice-loop.py', 'status': 'unit-a-status-server.py'}
HELPER_SHA = {'voice_loop': 'd8114fa3165f9e65f466f0d4b0d3961039483bc2d5bdab67944d711eaff4ed06',
              'status': '5682338aa01ddadb032f4ec6c1ad3e29daeaed179be577f4cf44152367578c59'}


def require(value):
    if not value:
        raise ValueError('cold_runtime_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path, limit=128 * 1024 * 1024):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= limit)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(limit + 1)
        def signature(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
                and signature(before) == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def pinned(item):
    require(type(item) is dict and set(item) == {'path', 'sha256'}
            and re.fullmatch('[0-9a-f]{64}', item['sha256']))
    raw = read(item['path'])
    require(sha(raw) == item['sha256'])
    return raw


def module(item):
    raw = pinned(item)
    value = types.ModuleType('pinned_runtime_preflight')
    value.__file__ = item['path']
    exec(compile(raw, item['path'], 'exec'), value.__dict__)
    return value


def wheel_files(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        require(1 <= len(entries) <= 512 and len({entry.filename for entry in entries}) == len(entries)
                and sum(entry.file_size for entry in entries) <= 32 * 1024 * 1024)
        result = {}
        for entry in entries:
            name = entry.filename
            require(not entry.is_dir() and not stat.S_ISLNK(entry.external_attr >> 16)
                and re.fullmatch(r'(sage_lantern/[A-Za-z0-9_-]+\.(py|json)|sage_lantern_runtime-0\.2\.0\.dist-info/[A-Za-z_.]+)', name))
            result[name] = archive.read(name)
        require({'sage_lantern/__init__.py', 'sage_lantern/release-manifest.json', DIST + 'METADATA',
                 DIST + 'WHEEL', DIST + 'RECORD', DIST + 'entry_points.txt'} <= result.keys())
        require(b'Name: sage-lantern-runtime\n' in result[DIST + 'METADATA']
                and b'Version: 0.2.0\n' in result[DIST + 'METADATA'])
        return result


def verify_installed(raw, *, site=SITE, reader=read):
    files = wheel_files(raw)
    for name, data in files.items():
        if name != DIST + 'RECORD':
            require(reader(site / name) == data)
    package = site / 'sage_lantern'
    expected = {Path(name).name for name in files if name.startswith('sage_lantern/')}
    actual = set()
    for entry in package.iterdir():
        if entry.name == '__pycache__':
            require(stat.S_ISDIR(entry.lstat().st_mode))
            continue
        require(stat.S_ISREG(entry.lstat().st_mode))
        actual.add(entry.name)
    require(actual == expected)
    return len(expected)


def durable(path, raw, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def transition(backend, record):
    for phase, action in (('inhibit_consumers', backend.inhibit), ('stop_consumers', backend.stop),
                          ('install_runtime', backend.install), ('verify_runtime', backend.verify)):
        backend.current()
        record(phase + '_intent')
        action()
        backend.current()
        record(phase + '_done')
    return dict(runtime_updated=True, services_inhibited=True, activation_authorized=False,
                native_changed=False, helpers_installed=False, recovery='manual-only')


class Update:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'unit_id', 'preflight', 'supersession', 'old_wheel',
                                  'new_wheel', 'consumers', 'files', 'later_helpers'})
        require(expected['schema'] == 'sage.lantern.cold-runtime-update.v1' and expected['unit_id'] == 'unit-a'
                and set(expected['consumers']) == set(CONSUMERS) and set(expected['later_helpers']) == set(HELPERS)
                and expected['new_wheel']['sha256'] == WHEEL_SHA)
        self.expected = expected
        self.preflight = module(expected['preflight'])
        self.observer = self.preflight.Observation()
        self.observer.deadline = time.monotonic() + 180
        self.guarded = False
        self.stopped = set()
        self.old = pinned(expected['old_wheel'])
        self.new = pinned(expected['new_wheel'])
        require(sha(self.old) != sha(self.new))
        self.before_modules = verify_installed(self.old)
        wheel_files(self.new)
        for name, filename in HELPERS.items():
            item = expected['later_helpers'][name]
            require(Path(item['path']).name == filename and item['sha256'] == HELPER_SHA[name])
            pinned(item)
        require(importlib.metadata.version('PyYAML') == '6.0.2')
        self.services = {}
        self.current()

    def native(self):
        item = self.expected['supersession']
        require(set(item) == {'evidence', 'binding', 'helper_sha256', 'manifest_path'})
        binding = self.preflight.decode(pinned(item['binding']))
        require(self.observer.read_supersession(item['evidence']['path'], item['evidence']['sha256'],
            helper_sha256=item['helper_sha256'], binding=binding, manifest_path=item['manifest_path'])
            == dict(capability_invalidated=True, historical_request_completion='unknown',
                    mutation_authorized=False, auth_drain_receipts_rewritten=False))
        self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), binding['chain_id'], self.observer.get(26657, '/abci_info'))

    def current(self):
        if self.guarded:
            info = STATE.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        self.native()
        for path, digest in self.expected['files'].items():
            require(sha(read(path)) == digest)
        for name in CONSUMERS:
            value = self.observer.system._show(name, self.observer.deadline, self.preflight.FIELDS + ('Restart',))
            require(value['LoadState'] == 'loaded' and value['NeedDaemonReload'] == 'no'
                and value['Job'] in ('', '0') and value['Slice'] == 'system.slice'
                and value['KillMode'] == 'control-group' and value['FragmentPath'] in self.expected['files'])
            dropins = set(value['DropInPaths'].split())
            if self.guarded:
                guard = self.guard_path(name)
                require(str(guard) in dropins and read(guard) == GUARD and value['Restart'] == 'no')
                dropins.remove(str(guard))
                self.observer.system._guard(name, self.observer.deadline, str(STATE))
            require(dropins <= self.expected['files'].keys())
            pinned_process = self.expected['consumers'][name]
            canonical = '/system.slice/' + name
            if name in self.stopped or pinned_process is None:
                require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                    and value['ControlGroup'] in ('', canonical))
                self.observer.system._empty_tree(self.observer.system._cgroup(canonical), self.observer.deadline)
            else:
                require(set(pinned_process) == {'pid', 'start_ticks', 'invocation', 'uid', 'executable', 'argv'})
                require(all(type(pinned_process[field]) is int and pinned_process[field] > 0
                    for field in ('pid', 'start_ticks')) and type(pinned_process['uid']) is int and pinned_process['uid'] >= 0
                    and re.fullmatch('[0-9a-f]{32}', pinned_process['invocation'])
                    and pinned_process['executable'] in self.expected['files'])
                require(value['ActiveState'] == 'active' and value['MainPID'] == str(pinned_process['pid'])
                    and value['InvocationID'] == pinned_process['invocation'] and value['ControlGroup'] == canonical)
                process = self.preflight.LinuxProcesses().read(pinned_process['pid'])
                require(process.uid == pinned_process['uid'] and process.start_ticks == pinned_process['start_ticks']
                    and process.executable == pinned_process['executable'] and list(process.argv) == pinned_process['argv']
                    and process.state not in ('Z', 'T', 't', 'X'))
            require(self.observer.system._show(name, self.observer.deadline, tuple(value)) == value)
        self.observer.remaining()

    @staticmethod
    def guard_path(name):
        return Path('/etc/systemd/system', name + '.d', GUARD_NAME)

    def inhibit(self):
        for name in CONSUMERS:
            path = self.guard_path(name)
            if not path.parent.exists():
                path.parent.mkdir(mode=0o755)
                sync(path.parent.parent)
            info = path.parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
            durable(path, GUARD, 0o644)
        self.observer.system._command(['daemon-reload'], self.observer.deadline)
        self.guarded = True

    def stop(self):
        require(self.guarded)
        for name in CONSUMERS:
            self.observer.system._command(['stop', name], self.observer.deadline)
            self.stopped.add(name)
            self.current()

    def install(self):
        require(self.guarded and self.stopped == set(CONSUMERS))
        require(pinned(self.expected['new_wheel']) == self.new and read(STATE / BACKUP) == self.old)
        result = subprocess.run([str(VENV / 'bin/python'), '-I', '-B', '-m', 'pip', 'install',
            '--no-index', '--no-deps', '--no-cache-dir', '--no-compile', '--force-reinstall',
            self.expected['new_wheel']['path']], timeout=self.observer.remaining(),
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C',
                 'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1', 'PYTHONDONTWRITEBYTECODE': '1'},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        self.observer.remaining()
        require(result.returncode == 0)

    def verify(self):
        require(verify_installed(self.new) > 0)
        cache = SITE / 'sage_lantern/__pycache__'
        if cache.exists():
            require(stat.S_ISDIR(cache.lstat().st_mode) and cache.lstat().st_uid == 0)
            entries = list(cache.iterdir())
            require(len(entries) <= 1024)
            for entry in entries:
                require(entry.suffix == '.pyc' and stat.S_ISREG(entry.lstat().st_mode) and entry.lstat().st_uid == 0)
                entry.unlink()
            cache.rmdir()
        require(verify_installed(self.new) > 0)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and Path(sys.prefix) == VENV)
    def expired(signum, frame):
        raise TimeoutError('runtime_update_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        require(not os.path.lexists(STATE))
        raw = read(args.expected, 65536)
        require(sha(raw) == args.expected_sha256)
        backend = Update(json.loads(raw, object_pairs_hook=lambda items: unique(items)))
        if not args.apply:
            print(json.dumps(dict(preflight_verified=True, mutation_authorized=False)))
            return
        STATE.mkdir(mode=0o700)
        sync(STATE.parent)
        durable(STATE / 'expected.json', raw)
        durable(STATE / BACKUP, backend.old)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(phase=phase,
                expected_sha256=args.expected_sha256, old_wheel_sha256=sha(backend.old),
                new_wheel_sha256=sha(backend.new))).encode())
        print(json.dumps(transition(backend, record), sort_keys=True))
    finally:
        signal.alarm(0)


def unique(items):
    value = {}
    for key, item in items:
        require(key not in value)
        value[key] = item
    return value


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Runtime update refused or incomplete; retain guards, journal and old wheel. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
