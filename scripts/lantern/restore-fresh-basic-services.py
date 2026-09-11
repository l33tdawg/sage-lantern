#!/usr/bin/env python3
"""Unit A post-runtime helper install and basic-service restore; no voice activation.

Default inspects only. Inputs pin the runtime updater, its completed expected
document, all service snapshots/configuration, and the two installed old helpers.
Failure retains the exclusive journal and backups; there is no automatic retry.

Expected schema sage.lantern.basic-restore.v1 has runtime_updater,
runtime_expected, prior_runtime_updater and prior_runtime_expected {path, sha256}
pins for the explicit R7-after-R6 transition; files maps installed paths to
SHA256; services maps AUTH/BASIC/COLD/SAGE to reviewed FIELDS+Restart+ExecStart
systemctl snapshots; start maps AUTH/BASIC to {executable, argv}; old_helpers
maps voice_loop/status to {path, sha256}. Candidate helpers and final wheel
come from the separately pinned completed runtime contract, not an R6 default.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import sys
import time
import types


STATE = Path('/var/lib/sage-lantern-basic-restore')
NATIVE = Path('/var/lib/sage-lantern-cold-native-update')
AUTH = 'sage-lantern-device-auth.service'
BASIC = tuple('sage-lantern-' + name + '.service' for name in ('status', 'camera', 'display-power'))
TARGETS = {'voice_loop': '/usr/local/libexec/sage-lantern-voice-loop',
           'status': '/usr/local/libexec/sage-lantern-status-server'}
NATIVE_GUARD = b'[Unit]\nConditionPathExists=!/var/lib/sage-lantern-cold-native-update\n[Service]\nRestart=no\n'
R7_WHEEL_SHA = '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'
R6_GUARD_SHA = '67b7494721b3bc3ac5303e9b3e79974dcf7584a36a5c5042221038de228b2b63'


def require(value):
    if not value:
        raise ValueError('basic_restore_refused')


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def exec_definition(raw):
    require(type(raw) is str and len(raw) <= 16384)
    match = re.fullmatch(r'\{ path=([^{};\n]+) ; argv\[\]=([^{};\n]+) ; '
        r'ignore_errors=(yes|no) ; start_time=([^{};\n]+) ; stop_time=([^{};\n]+) ; '
        r'pid=([0-9]+) ; code=([A-Za-z_()]+) ; status=([A-Za-z0-9_/-]+) \}', raw)
    require(match is not None)
    path, arguments, ignore = match.group(1, 2, 3)
    words = arguments.split(' ')
    require(path.startswith('/') and '..' not in Path(path).parts and words[0] == path
            and all(re.fullmatch('[A-Za-z0-9_./:=+@%,~-]+', word) for word in words))
    return path, tuple(words), ignore


def snapshot(value, service):
    result = dict(value)
    if result['LoadState'] == 'not-found':
        require(service == 'sage-lantern-idle-suspend.service' and result.get('ExecStart', '') == '')
        result['ExecStart'] = ''
    else:
        result['ExecStart'] = exec_definition(result['ExecStart'])
    return result


def load(item):
    require(set(item) == {'path', 'sha256'})
    path = Path(item['path'])
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= 262144)
        raw = os.read(descriptor, 262145)
        def signature(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                    value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) == info.st_size and signature(info) == signature(os.fstat(descriptor))
                and signature(info) == signature(path.lstat())
                and hashlib.sha256(raw).hexdigest() == item['sha256'])
        return raw
    finally:
        os.close(descriptor)


def module(item):
    value = types.ModuleType('basic_restore_dependency')
    value.__file__ = item['path']
    exec(compile(load(item), item['path'], 'exec'), value.__dict__)
    return value


def completion(runtime, raw, *, installed=True):
    expected = decode(raw)
    info = runtime.STATE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    require(runtime.read(runtime.STATE / 'expected.json') == raw)
    phases = [phase + suffix for phase in ('inhibit_consumers', 'stop_consumers',
              'install_runtime', 'verify_runtime') for suffix in ('_intent', '_done')]
    names = {'expected.json', runtime.BACKUP}
    for index, phase in enumerate(phases, 1):
        name = f'{index:02d}-{phase}.json'
        names.add(name)
        require(decode(runtime.read(runtime.STATE / name)) == dict(phase=phase,
            expected_sha256=runtime.sha(raw), old_wheel_sha256=expected['old_wheel']['sha256'],
            new_wheel_sha256=expected['new_wheel']['sha256']))
    require({entry.name for entry in runtime.STATE.iterdir()} == names)
    require(runtime.sha(runtime.read(runtime.STATE / runtime.BACKUP)) == expected['old_wheel']['sha256'])
    require(expected['new_wheel']['sha256'] == runtime.WHEEL_SHA)
    wheel = runtime.pinned(expected['new_wheel'])
    if installed:
        runtime.verify_installed(wheel)
    return expected


def transition(backend, record):
    for key in TARGETS:
        backend.current()
        record('install_' + key + '_intent')
        backend.install(key)
        backend.current()
        record('install_' + key + '_done')
    for service in (AUTH, *BASIC):
        backend.current()
        record('release_' + service + '_intent')
        backend.release(service, record)
        backend.current()
        record('release_' + service + '_done')
        record('start_' + service + '_intent')
        backend.start(service)
        backend.current()
        record('start_' + service + '_done')
    return dict(basic_services_restored=True, auth_locked=True, voice_activated=False,
                audio_readiness_claimed=False, native_changed=False)


def lineage(prior, final, consumers):
    require(prior['unit_id'] == final['unit_id'] == 'unit-a'
            and final['old_wheel']['sha256'] == prior['new_wheel']['sha256']
            and final['new_wheel']['sha256'] == R7_WHEEL_SHA
            and final['new_wheel']['sha256'] != final['old_wheel']['sha256']
            and final['supersession'] == prior['supersession']
            and final['later_helpers'] == prior['later_helpers']
            and final['consumers'] == {name: None for name in consumers})


class Restore:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'runtime_updater', 'runtime_expected', 'files',
                                 'services', 'start', 'old_helpers',
                                 'prior_runtime_updater', 'prior_runtime_expected'})
        require(expected['schema'] == 'sage.lantern.basic-restore.v1')
        self.expected = expected
        self.runtime = module(expected['runtime_updater'])
        self.prior_runtime = module(expected['prior_runtime_updater'])
        require(self.runtime.STATE == Path('/var/lib/sage-lantern-cold-runtime-update-r7')
                and self.runtime.GUARD_NAME == 'zzzz-fresh-runtime-r7-update.conf'
                and self.prior_runtime.STATE == Path('/var/lib/sage-lantern-cold-runtime-update')
                and self.prior_runtime.GUARD_NAME == 'zzzz-fresh-runtime-update.conf')
        require(hashlib.sha256(self.prior_runtime.GUARD).hexdigest() == R6_GUARD_SHA
                and self.runtime.GUARD == (b'[Unit]\nConditionPathExists=!'
                    + str(self.runtime.STATE).encode() + b'\n[Service]\nRestart=no\n'))
        self.raw = load(expected['runtime_expected'])
        self.prior_raw = load(expected['prior_runtime_expected'])
        self.prior_context = completion(self.prior_runtime, self.prior_raw, installed=False)
        self.context = completion(self.runtime, self.raw)
        lineage(self.prior_context, self.context, self.runtime.CONSUMERS)
        self.preflight = self.runtime.module(self.context['preflight'])
        self.observer = self.preflight.Observation()
        self.observer.deadline = time.monotonic() + 180
        self.sup = self.context['supersession']
        self.binding = decode(self.runtime.pinned(self.sup['binding']))
        self.evidence = decode(self.runtime.pinned(self.sup['evidence']))
        self.validator = module(dict(path=str(Path(self.context['preflight']['path']).with_name(
            'auth-capability-supersession.py')), sha256=self.sup['helper_sha256']))
        require(self.observer.read_supersession(self.sup['evidence']['path'], self.sup['evidence']['sha256'],
            helper_sha256=self.sup['helper_sha256'], binding=self.binding,
            manifest_path=self.sup['manifest_path'])['capability_invalidated'] is True)
        self.installed, self.released, self.running, self.ticks = set(), set(), {}, {}
        self.removed = set()
        self.receipts = {entry: self.preflight.trusted(entry, self.observer.uid, 512)
                         for entry in self.preflight.REPORTS.iterdir()}
        require(set(expected['services']) == {AUTH, *BASIC, *self.preflight.COLD, self.preflight.SAGE}
                and set(expected['start']) == {AUTH, *BASIC}
                and set(expected['old_helpers']) == set(TARGETS))
        for key, target in TARGETS.items():
            require(expected['old_helpers'][key]['path'] == target)
            self.runtime.pinned(expected['old_helpers'][key])
            require(stat.S_IMODE(Path(target).lstat().st_mode) == 0o755)
            item = self.context['later_helpers'][key]
            require(item['sha256'] == self.runtime.HELPER_SHA[key]
                    and Path(item['path']).name == self.runtime.HELPERS[key])
            self.runtime.pinned(item)
        for service, item in expected['start'].items():
            require(set(item) == {'executable', 'argv'} and type(item['argv']) is list
                    and item['argv'] and item['executable'] in expected['files'])
            for argument in item['argv']:
                require(type(argument) is str)
                if argument.startswith('/usr/local/libexec/'):
                    require(argument in expected['files'])
        require('/usr/local/libexec/sage-lantern-device-auth' in expected['files'])
        for path, digest in self.context['files'].items():
            if path not in TARGETS.values():
                require(expected['files'].get(path) == digest)
        self.current()

    def guards(self, service):
        require(service in (AUTH, *BASIC))
        if service == AUTH:
            return ((self.guard(service), NATIVE_GUARD, NATIVE),)
        return tuple((Path('/etc/systemd/system', service + '.d', runtime.GUARD_NAME),
                      runtime.GUARD, runtime.STATE) for runtime in (self.prior_runtime, self.runtime))

    def guard(self, service):
        require(service in (AUTH, *BASIC))
        name = 'zzzz-fresh-native-update.conf' if service == AUTH else self.runtime.GUARD_NAME
        return Path('/etc/systemd/system', service + '.d', name)

    def show(self, service):
        fields = self.preflight.FIELDS + ('Restart', 'ExecStart')
        arguments = ['show', '--no-pager', '--all']
        for field in fields:
            arguments.extend(['-p', field])
        text = self.observer.system._command(arguments + [service], self.observer.deadline)
        value = {}
        for line in text.splitlines():
            key, separator, content = line.partition('=')
            require(separator and key in fields and key not in value)
            value[key] = content
        missing = set(fields) - value.keys()
        require(not missing or missing == {'ExecStart'} and service == 'sage-lantern-idle-suspend.service'
                and value.get('LoadState') == 'not-found')
        snapshot(value, service)
        return value

    def native(self):
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.binding['boot_id'])
        self.runtime.pinned(self.sup['evidence'])
        self.runtime.pinned(self.sup['binding'])
        require(self.runtime.sha(self.runtime.read(self.preflight.CONFIG + 'initialized-public.json'))
                == self.binding['initialized_sha256'])
        require(self.runtime.sha(self.runtime.read(self.preflight.BINARY))
                == self.binding['native_after']['binary_sha256'])
        pinned = self.evidence['sage_after']
        process = self.preflight.LinuxProcesses().read(pinned['pid'])
        require(process.uid == pinned['uid'] and process.start_ticks == pinned['start_ticks']
                and process.executable == self.preflight.BINARY and process.argv == (self.preflight.BINARY, 'serve')
                and process.state not in ('Z', 'T', 't', 'X'))
        running, installed = Path('/proc', str(pinned['pid']), 'exe').stat(), Path(self.preflight.BINARY).stat()
        require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
        listener = self.validator.listener_owner(pinned['pid'], pinned['start_ticks'], self.binding['boot_id'])
        health = self.observer.get(8080, '/v1/dashboard/health')
        require(health['sage'] == 'running' and health['version'] == self.binding['native_after']['version']
                and health['boot_id'] == pinned['application_boot_id']
                and health['encrypted'] is True and health['vault_locked'] is True)
        require(self.validator.listener_owner(pinned['pid'], pinned['start_ticks'], self.binding['boot_id']) == listener)
        self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), self.binding['chain_id'], self.observer.get(26657, '/abci_info'))

    def current(self):
        load(self.expected['runtime_updater'])
        require(load(self.expected['runtime_expected']) == self.raw)
        load(self.expected['prior_runtime_updater'])
        require(load(self.expected['prior_runtime_expected']) == self.prior_raw)
        for path, raw in self.receipts.items():
            require(self.preflight.trusted(path, self.observer.uid, 512) == raw)
        self.native()
        completion(self.runtime, self.raw)
        completion(self.prior_runtime, self.prior_raw, installed=False)
        for path, digest in self.expected['files'].items():
            if path not in TARGETS.values() and Path(path) not in self.removed:
                require(self.runtime.sha(self.runtime.read(path)) == digest)
        for key, target in TARGETS.items():
            item = self.context['later_helpers'][key] if key in self.installed else self.expected['old_helpers'][key]
            require(self.runtime.sha(self.runtime.read(target)) == item['sha256'])
        for service, before in self.expected['services'].items():
            before = snapshot(before, service)
            value = snapshot(self.show(service), service)
            require(set(before) == set(value) and value['NeedDaemonReload'] == 'no' and value['Job'] in ('', '0'))
            if value['LoadState'] == 'not-found':
                require(service == 'sage-lantern-idle-suspend.service' and value == before)
                self.observer.service(service, None, {})
                continue
            require(value['LoadState'] == 'loaded' and value['FragmentPath'] ==
                    '/etc/systemd/system/' + service and value['FragmentPath'] in self.expected['files'])
            dropins = set(before['DropInPaths'].split())
            if service in (AUTH, *BASIC):
                for path, raw, state in self.guards(service):
                    require(str(path) in dropins)
                    if path in self.removed:
                        dropins.remove(str(path))
                        require(not os.path.lexists(path))
                    else:
                        require(self.runtime.read(path) == raw)
                        self.observer.system._guard(service, self.observer.deadline, str(state))
            require(set(value['DropInPaths'].split()) == dropins)
            for path in dropins:
                if service not in (AUTH, *BASIC) or path not in {str(item[0]) for item in self.guards(service)}:
                    require(path in self.expected['files'])
            for field in ('User', 'ExecStart', 'Slice', 'KillMode'):
                require(value[field] == before[field])
            require(value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group')
            if service == self.preflight.SAGE:
                require(value == before and value['MainPID'] == str(self.binding['sage_before']['pid'])
                        and value['InvocationID'] == self.binding['sage_before']['invocation'])
            elif service in self.running:
                require(value == self.running[service])
                require(self.process(service, value).start_ticks == self.ticks[service])
            else:
                require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                        and value['ControlGroup'] in ('', '/system.slice/' + service))
                self.observer.system._empty_tree(self.observer.system._cgroup('/system.slice/' + service),
                                                self.observer.deadline)
            if service not in self.released:
                require(value['Restart'] == 'no')
            require(snapshot(self.show(service), service) == value)
        if AUTH in self.running:
            self.locked()

    def process(self, service, value):
        require(value['ActiveState'] == 'active' and int(value['MainPID']) > 0
                and re.fullmatch('[0-9a-f]{32}', value['InvocationID'])
                and value['ControlGroup'] == '/system.slice/' + service)
        process = self.preflight.LinuxProcesses().read(int(value['MainPID']))
        item = self.expected['start'][service]
        require(process.uid == pwd.getpwnam(value['User']).pw_uid
                and process.executable == item['executable'] and list(process.argv) == item['argv']
                and process.state not in ('Z', 'T', 't', 'X'))
        require(Path('/proc', str(process.pid), 'cgroup').read_text() == '0::/system.slice/' + service + '\n')
        running, installed = Path('/proc', str(process.pid), 'exe').stat(), Path(item['executable']).stat()
        require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
        return process

    def locked(self):
        process = self.process(AUTH, self.show(AUTH))
        rows = Path('/proc/net/tcp').read_text().splitlines()[1:]
        matches = [row.split() for row in rows if len(row.split()) >= 10
                   and row.split()[3] == '0A' and row.split()[1].endswith(':0C7C')]
        if not matches:
            raise ConnectionRefusedError('auth_listener_not_bound')
        require(len(matches) == 1 and matches[0][1] == '0100007F:0C7C')
        inode = matches[0][9]
        entries = list(Path('/proc', str(process.pid), 'fd').iterdir())
        require(len(entries) <= 4096)
        owned = False
        for entry in entries:
            try:
                owned |= os.readlink(entry) == 'socket:[' + inode + ']'
            except FileNotFoundError:
                pass
        require(owned and self.observer.get(3196, '/state').get('locked') is True)
        require([row.split() for row in Path('/proc/net/tcp').read_text().splitlines()[1:]
                 if len(row.split()) >= 10 and row.split()[3] == '0A'
                 and row.split()[1].endswith(':0C7C')] == matches)
        require(self.process(AUTH, self.show(AUTH)).start_ticks == process.start_ticks)

    def install(self, key):
        require(key not in self.installed and not self.released)
        path = Path(TARGETS[key])
        old = self.runtime.pinned(self.expected['old_helpers'][key])
        require(stat.S_IMODE(path.lstat().st_mode) == 0o755)
        self.runtime.durable(STATE / (key + '.before'), old, 0o755)
        raw = self.runtime.pinned(self.context['later_helpers'][key])
        temporary = path.with_name(path.name + '.basic-restore-new')
        self.runtime.durable(temporary, raw, 0o755)
        self.current()
        os.replace(temporary, path)
        self.runtime.sync(path.parent)
        self.installed.add(key)

    def release(self, service, record):
        require(self.installed == set(TARGETS) and service not in self.released)
        require(service == AUTH or AUTH in self.running)
        for path, raw, state in self.guards(service):
            self.current()
            require(path not in self.removed and self.runtime.read(path) == raw)
            phase = 'remove_' + service + '_' + path.name
            record(phase + '_intent')
            path.unlink()
            self.runtime.sync(path.parent)
            self.removed.add(path)
            self.observer.system._command(['daemon-reload'], self.observer.deadline)
            if all(item[0] in self.removed for item in self.guards(service)):
                self.released.add(service)
            self.current()
            record(phase + '_done')
        self.released.add(service)

    def start(self, service):
        require(service in self.released and service not in self.running)
        self.observer.system._command(['start', service], self.observer.deadline)
        value = snapshot(self.show(service), service)
        process = self.process(service, value)
        self.running[service] = value
        self.ticks[service] = process.start_ticks
        if service == AUTH:
            deadline = min(self.observer.deadline, time.monotonic() + 10)
            while True:
                try:
                    self.locked()
                    break
                except ConnectionRefusedError:
                    require(time.monotonic() < deadline and snapshot(self.show(service), service) == value)
                    time.sleep(0.1)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    def expired(signum, frame):
        raise TimeoutError('basic_restore_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        raw = load(dict(path=args.expected, sha256=args.expected_sha256))
        backend = Restore(decode(raw))
        if not args.apply:
            print(json.dumps(dict(preflight_verified=True, mutation_authorized=False)))
            return
        STATE.mkdir(mode=0o700)
        backend.runtime.sync(STATE.parent)
        backend.runtime.durable(STATE / 'expected.json', raw)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            backend.runtime.durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(
                phase=phase, expected_sha256=args.expected_sha256)).encode())
        print(json.dumps(transition(backend, record)))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Basic restore refused/incomplete; preserve journal/backups. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
