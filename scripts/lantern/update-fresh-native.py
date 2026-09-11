#!/usr/bin/env python3
"""One-shot Unit A native replacement through SAGE's coordinated restart only.

Runtime/UI are untouched. Success leaves auth/producers/start guards inhibited;
it is not appliance activation. Every failure retains journal, guards and backup
for manual reconciliation. Never stop/restart SAGE through systemd or retry the
signed request. The preflight source and its expected document require pins.

Resume is separate: --inspect-resume or --resume-request require capability
recovery and a new --resume-checkpoint/--resume-checkpoint-sha256 pair. The exact
checkpoint schema sage.lantern.cold-native-resume.v1 contains journal_sha256,
the original expected_sha256/preflight_sha256/recovery_binding_sha256/
supersession_helper_sha256, replacement_helper {path, sha256}, services (all
preflight FIELDS plus Restart), and receipts (filename to current SHA256).
Inspection performs only observations/version checks. It neither recreates
the original journal nor invokes the signer. Resume uses a separate exclusive
resume-request.jsonl and never repeats the swap, auth stop or guard installation.
"""

import argparse
import fcntl
import hashlib
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
import uuid


STATE = Path('/var/lib/sage-lantern-cold-native-update')
GUARD_NAME = 'zzzz-fresh-native-update.conf'
GUARD = ('[Unit]\nConditionPathExists=!' + str(STATE) + '\n[Service]\nRestart=no\n').encode()
PYTHON = '/opt/sage-lantern/venv/bin/python'


class StartupUnavailable(RuntimeError):
    """The pinned process has not yet exposed its fixed listener."""


def require(value):
    if value is not True:
        raise RuntimeError('cold_native_update_refused')


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, raw, mode):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def listener_owner(pid, start_ticks, boot_id, *, proc=Path('/proc')):
    process = proc / str(pid)
    def identity():
        return ((process / 'stat').read_text().rsplit(')', 1)[1].split()[19],
                (proc / 'sys/kernel/random/boot_id').read_text().strip())
    require(identity() == (str(start_ticks), boot_id))
    def listening():
        with (proc / 'net/tcp').open('rb') as stream:
            raw = stream.read(1048577)
        require(len(raw) <= 1048576)
        rows = [row.split() for row in raw.decode('ascii').splitlines()[1:]]
        require(all(len(row) >= 10 and re.fullmatch('[0-9A-F]{8}:[0-9A-F]{4}', row[1]) is not None for row in rows))
        matches = [row for row in rows if row[3] == '0A'
                   and row[1].endswith(':1F90')]
        if not matches:
            require(identity() == (str(start_ticks), boot_id))
            raise StartupUnavailable('native_listener_not_bound')
        require(len(matches) == 1 and matches[0][1] == '0100007F:1F90'
                and re.fullmatch('[1-9][0-9]*', matches[0][9]) is not None)
        return matches[0][9]
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
            and listening() == inode and identity() == (str(start_ticks), boot_id))
    return inode


class Journal:
    def __init__(self, binding):
        STATE.mkdir(mode=0o700)
        os.chmod(STATE, 0o700)
        sync_directory(STATE.parent)
        self.descriptor = os.open(STATE / 'journal.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.append('intent', binding)
        sync_directory(STATE)

    def append(self, phase, details=None):
        raw = (json.dumps(dict(phase=phase, details=details), sort_keys=True) + '\n').encode()
        with os.fdopen(os.dup(self.descriptor), 'ab') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def close(self):
        os.close(self.descriptor)


def transition(backend, journal):
    for phase, action in (('inhibit_starts', backend.inhibit), ('drain_auth', backend.drain_auth),
                          ('swap_native', backend.swap), ('request_restart', backend.restart)):
        backend.current()
        journal.append(phase + '_intent')
        action()
        backend.current()
        journal.append(phase + '_done')
    result = backend.completed()
    backend.current()
    journal.append('native_verified_services_inhibited', result)
    return result


class NativeUpdate:
    def __init__(self, preflight, expected, observed):
        self.preflight, self.expected = preflight, expected
        self.services, self.reports = observed
        self.observer = preflight.Observation()
        self.observer.deadline = time.monotonic() + 180
        self.guarded = False
        self.auth_stopped = False
        self.swapped = False
        self.requested = False
        self.running_inode = self.inode(Path('/proc', str(expected['services'][preflight.SAGE]['pid']), 'exe'))
        prior = preflight.decode(preflight.trusted(expected['native_before']['manifest']))
        self.old_version = prior['version']
        pinned = expected['services'][preflight.SAGE]
        self.old_listener = listener_owner(pinned['pid'], pinned['start_ticks'], expected['boot_id'])
        self.old_dashboard_boot = self.dashboard_boot(self.old_version)
        require(listener_owner(pinned['pid'], pinned['start_ticks'], expected['boot_id']) == self.old_listener)

    @staticmethod
    def inode(path):
        value = path.stat()
        return value.st_dev, value.st_ino

    def command(self, arguments):
        allowed = (['daemon-reload'], ['stop', self.preflight.AUTH])
        require(arguments in allowed)
        return self.observer.system._command(arguments, self.observer.deadline)

    def guard_path(self, service):
        return Path('/etc/systemd/system', service + '.d', GUARD_NAME)

    def current(self):
        preflight, expected = self.preflight, self.expected
        state = STATE.lstat()
        require(stat.S_ISDIR(state.st_mode) and state.st_uid == 0 and stat.S_IMODE(state.st_mode) == 0o700)
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected['boot_id'])
        for name, digest in expected['files'].items():
            if name == preflight.BINARY and self.swapped:
                digest = expected['files'][expected['native_after']['binary']]
            require(preflight.sha(preflight.trusted(name, self.observer.uid if name == preflight.YAML else 0)) == digest)
        for path in preflight.ABSENT:
            if path == '/run/sage-lantern-native-restart-request.jsonl' and self.requested:
                continue
            require(not os.path.lexists(path))
        for name, original in self.services.items():
            if original['LoadState'] == 'not-found':
                require(name == 'sage-lantern-idle-suspend.service')
                require(self.observer.service(name, None, {}) == original)
                continue
            value = self.observer.system._show(name, self.observer.deadline, preflight.FIELDS + ('Restart',))
            require(value['LoadState'] == 'loaded' and value['NeedDaemonReload'] == 'no'
                    and value['Job'] in ('', '0') and value['FragmentPath'] == original['FragmentPath']
                    and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group')
            dropins = set(original['DropInPaths'].split())
            if self.guarded:
                dropins.add(str(self.guard_path(name)))
                require(preflight.trusted(self.guard_path(name)) == GUARD and value['Restart'] == 'no')
                self.observer.system._guard(name, self.observer.deadline, str(STATE))
            require(set(value['DropInPaths'].split()) == dropins)
            if name in preflight.COLD or (name == preflight.AUTH and self.auth_stopped):
                canonical = '/system.slice/' + name
                require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                        and value['ControlGroup'] in ('', canonical))
                self.observer.system._empty_tree(self.observer.system._cgroup(canonical), self.observer.deadline)
            else:
                pinned = expected['services'][name]
                require(value['ActiveState'] == 'active' and value['MainPID'] == str(pinned['pid'])
                        and value['InvocationID'] == pinned['invocation'] and value['User'] == 'sage-lantern'
                        and value['ControlGroup'] == '/system.slice/' + name)
                process = preflight.LinuxProcesses().read(pinned['pid'])
                require(process.uid == self.observer.uid and process.start_ticks == pinned['start_ticks']
                        and list(process.argv) == pinned['argv'] and process.state not in ('Z', 'T', 't', 'X'))
                inode = self.inode(Path('/proc', str(pinned['pid']), 'exe'))
                if name == preflight.SAGE:
                    allowed = {self.running_inode}
                    if self.requested:
                        allowed.add(self.inode(Path(preflight.BINARY)))
                    require(inode in allowed)
                else:
                    require(inode == self.inode(Path(pinned['executable'])))
            require(self.observer.system._show(name, self.observer.deadline, tuple(value)) == value)
        self.observer.system._sleep_idle(self.observer.deadline)
        self.observer.remaining()

    def inhibit(self):
        for name, original in self.services.items():
            if original['LoadState'] == 'not-found':
                require(name == 'sage-lantern-idle-suspend.service')
                continue
            path = self.guard_path(name)
            if not path.parent.exists():
                path.parent.mkdir(mode=0o755)
                sync_directory(path.parent.parent)
            info = path.parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
            write_new(path, GUARD, 0o644)
        self.command(['daemon-reload'])
        self.guarded = True

    def drained_reports(self):
        preflight = self.preflight
        names = {entry.name for entry in preflight.REPORTS.iterdir()}
        require(names == set(self.reports))
        current = self.expected['services'][preflight.AUTH]['invocation'] + '.json'
        for name in names:
            path = preflight.REPORTS / name
            raw = preflight.trusted(path, self.observer.uid, 512)
            require(stat.S_IMODE(path.lstat().st_mode) == 0o600)
            preflight.report(preflight.decode(raw), name.removesuffix('.json'), current=False)
            if name != current:
                require(preflight.sha(raw) == self.reports[name])

    def drain_auth(self):
        require(self.guarded and not self.auth_stopped)
        self.command(['stop', self.preflight.AUTH])
        self.auth_stopped = True
        self.current()
        self.drained_reports()

    def governance(self):
        self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), self.expected['chain_id'], self.observer.get(26657, '/abci_info'))

    def dashboard_boot(self, version, *, locked=False):
        value = self.observer.get(8080, '/v1/dashboard/health')
        require(type(value) is dict and value.get('sage') == 'running' and value.get('version') == version)
        if locked:
            require(value.get('encrypted') is True and value.get('vault_locked') is True)
        boot = value.get('boot_id')
        require(type(boot) is str and str(uuid.UUID(boot)) == boot and uuid.UUID(boot).int != 0)
        return boot

    def candidate_version(self):
        expected, preflight = self.expected, self.preflight
        manifest = preflight.decode(preflight.trusted(expected['native_after']['manifest']))
        prior = preflight.decode(preflight.trusted(expected['native_before']['manifest']))
        require(manifest.get('schema') == prior.get('schema') == 'sage.lantern.binary-staging.v1'
                and type(manifest.get('version')) is str and manifest['version'] != prior.get('version'))
        result = subprocess.run([expected['native_after']['binary'], 'version'], capture_output=True, timeout=5,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'})
        version = result.stdout.decode('ascii').strip()
        require(result.returncode == 0 and version.startswith('sage-gui ' + manifest['version'] + ' (commit ')
                and version.endswith(', max-app-v27)'))
        return manifest['version']

    def swap(self):
        require(self.guarded and self.auth_stopped)
        self.drained_reports()
        self.governance()
        self.candidate_version()
        preflight, expected = self.preflight, self.expected
        candidate = expected['native_after']['binary']
        raw = preflight.trusted(candidate)
        require(preflight.sha(raw) == expected['files'][candidate])
        destination = Path(preflight.BINARY)
        temporary = destination.with_name('.sage-gui-cold-native-update')
        write_new(temporary, raw, 0o755)
        self.current()
        require(preflight.sha(preflight.trusted(temporary)) == expected['files'][candidate])
        os.replace(temporary, destination)
        sync_directory(destination.parent)
        self.swapped = True

    def restart(self):
        require(self.swapped and not self.requested)
        self.drained_reports()
        self.governance()
        require(self.dashboard_boot(self.old_version) == self.old_dashboard_boot)
        pinned = self.expected['services'][self.preflight.SAGE]
        require(listener_owner(pinned['pid'], pinned['start_ticks'], self.expected['boot_id']) == self.old_listener)
        self.requested = True
        expected = self.expected
        result = subprocess.run([PYTHON, '-I', '-B', self.restart_helper(), '--unit-id', 'unit-a',
            '--boot-id', expected['boot_id'], '--root-id', expected['root_id'], '--initialized-sha256',
            expected['files'][self.preflight.CONFIG + 'initialized-public.json'], '--request-restart'],
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'},
            capture_output=True, timeout=min(35, self.observer.remaining()))
        require(result.returncode == 0 and self.preflight.decode(result.stdout) == {'accepted': True, 'completed': False})

    def restart_helper(self):
        return self.expected['restart_helper']

    def completed(self):
        require(self.requested and self.swapped)
        preflight, expected = self.preflight, self.expected
        pid = expected['services'][preflight.SAGE]['pid']
        while self.observer.remaining() > 0:
            self.current()
            installed_inode = self.inode(Path(preflight.BINARY))
            require(installed_inode != self.running_inode)
            if self.inode(Path('/proc', str(pid), 'exe')) == installed_inode:
                try:
                    self.governance()
                except ConnectionRefusedError:
                    time.sleep(0.2)
                    continue
                version = self.candidate_version()
                pinned = expected['services'][preflight.SAGE]
                try:
                    listener = listener_owner(pid, pinned['start_ticks'], expected['boot_id'])
                    dashboard_boot = self.dashboard_boot(version, locked=True)
                except (StartupUnavailable, ConnectionRefusedError):
                    time.sleep(0.2)
                    continue
                require(dashboard_boot != self.old_dashboard_boot)
                self.current()
                try:
                    confirmed_boot = self.dashboard_boot(version, locked=True)
                    confirmed_listener = listener_owner(pid, pinned['start_ticks'], expected['boot_id'])
                except (StartupUnavailable, ConnectionRefusedError):
                    time.sleep(0.2)
                    continue
                require(confirmed_boot == dashboard_boot and confirmed_listener == listener
                        and self.inode(Path('/proc', str(pid), 'exe')) == installed_inode)
                return dict(native_updated=True, binary_sha256=expected['files'][expected['native_after']['binary']],
                    version=version, pid=pid, invocation=expected['services'][preflight.SAGE]['invocation'],
                    dashboard_boot_before=self.old_dashboard_boot, dashboard_boot_after=dashboard_boot,
                    listener_inode=listener, encrypted=True, vault_locked=True,
                    services_inhibited=True, appliance_ready=False, recovery='manual-only')
            time.sleep(0.2)
        raise TimeoutError('native completion unknown')


class RecoveryNativeUpdate(NativeUpdate):
    """Explicit capability supersession; historical request outcomes stay unknown."""

    def bind_recovery(self, validator, binding, unknown_receipts, *, auth_drained=False):
        require(type(auth_drained) is bool)
        validator.exact(binding, validator.BINDING_FIELDS)
        expected, preflight = self.expected, self.preflight
        def image(name):
            artifact = expected[name]
            raw = preflight.trusted(artifact['manifest'])
            manifest = preflight.decode(raw)
            value = dict(binary_sha256=expected['files'][artifact['binary']],
                manifest_sha256=preflight.sha(raw), inventory_sha256=manifest['inventory_sha256'],
                version=manifest['version'])
            validator.image(value)
            return value
        sage = expected['services'][preflight.SAGE]
        auth = expected['services'][preflight.AUTH]
        observed = dict(unit_id='unit-a', boot_id=expected['boot_id'], root_id=expected['root_id'],
            chain_id=expected['chain_id'], initialized_sha256=expected['files'][preflight.CONFIG + 'initialized-public.json'],
            native_before=image('native_before'), native_after=image('native_after'),
            sage_before=dict(pid=sage['pid'], start_ticks=sage['start_ticks'], invocation=sage['invocation'],
                uid=self.observer.uid, application_boot_id=self.old_dashboard_boot),
            current_auth=dict(pid=auth['pid'], start_ticks=auth['start_ticks'], instance_id=auth['invocation'],
                uid=self.observer.uid), unknown_receipts=dict(unknown_receipts))
        validator.identity(binding['sage_before'])
        validator.identity(binding['current_auth'], auth=True)
        require(type(binding['unknown_receipts']) is dict and 1 <= len(binding['unknown_receipts']) <= 1023
                and binding == observed and auth['invocation'] not in unknown_receipts)
        for instance, digest in unknown_receipts.items():
            validator.invocation(instance)
            validator.digest(digest)
        self.validator, self.recovery_binding = validator, preflight.decode(json.dumps(binding))
        self.recovery_reports(drained=auth_drained)

    def recovery_reports(self, *, drained):
        preflight = self.preflight
        info = preflight.REPORTS.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.observer.uid
                and stat.S_IMODE(info.st_mode) == 0o700)
        names = {entry.name for entry in preflight.REPORTS.iterdir()}
        require(names == set(self.reports))
        current = self.recovery_binding['current_auth']['instance_id']
        unknown = self.recovery_binding['unknown_receipts']
        receipts = {}
        for name in names:
            path = preflight.REPORTS / name
            raw = preflight.trusted(path, self.observer.uid, 512)
            require(stat.S_IMODE(path.lstat().st_mode) == 0o600)
            instance = name.removesuffix('.json')
            self.validator.receipt(raw, instance, confirmed=instance not in unknown and (instance != current or drained))
            if instance != current or not drained:
                require(preflight.sha(raw) == self.reports[name])
            if instance in unknown:
                require(preflight.sha(raw) == unknown[instance])
            receipts[instance] = raw
        return receipts

    def current(self):
        super().current()
        self.recovery_reports(drained=self.auth_stopped)

    def drained_reports(self):
        require(self.auth_stopped)
        self.recovery_reports(drained=True)

    def publish_supersession(self, completed, journal):
        require(self.guarded and self.auth_stopped and self.swapped and self.requested)
        require(self.completed() == completed)
        receipts = self.recovery_reports(drained=True)
        binding = self.recovery_binding
        after = dict(binding['sage_before'], application_boot_id=completed['dashboard_boot_after'])
        evidence = dict(binding, schema=self.validator.SCHEMA, sage_after=after,
            current_auth_report_sha256=self.preflight.sha(receipts[binding['current_auth']['instance_id']]),
            capability_invalidated=True, historical_request_completion='unknown')
        self.current()
        live = dict(boot_id=self.expected['boot_id'], sage_after=after, native_after=binding['native_after'],
            auth_inactive=True, auth_cgroup_empty=True, starts_inhibited=True,
            encrypted=completed['encrypted'], vault_locked=completed['vault_locked'])
        verdict = self.validator.validate(evidence, binding=binding, receipts=receipts, live=live)
        require(verdict == dict(capability_invalidated=True, historical_request_completion='unknown',
                                mutation_authorized=False, auth_drain_receipts_rewritten=False))
        raw = (json.dumps(evidence, sort_keys=True) + '\n').encode()
        path = STATE / 'capability-supersession.json'
        journal.append('capability_supersession_intent', dict(path=str(path), sha256=self.preflight.sha(raw)))
        self.current()
        write_new(path, raw, 0o600)
        self.current()
        require(self.recovery_reports(drained=True) == receipts)
        journal.append('capability_supersession_done', dict(path=str(path), sha256=self.preflight.sha(raw)))
        return dict(path=str(path), sha256=self.preflight.sha(raw), **verdict)


def pinned_source(path, digest):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= 65536)
        raw = stream.read(65537)
    require(hashlib.sha256(raw).hexdigest() == digest)
    return raw


def checkpoint_journal(raw, checkpoint, *, decode=json.loads):
    require(hashlib.sha256(raw).hexdigest() == checkpoint['journal_sha256'] and raw.endswith(b'\n'))
    rows = [decode(line) for line in raw.splitlines()]
    phases = ['intent', 'inhibit_starts_intent', 'inhibit_starts_done', 'drain_auth_intent',
              'drain_auth_done', 'swap_native_intent', 'swap_native_done', 'request_restart_intent']
    require(len(rows) == len(phases))
    for row, phase in zip(rows, phases):
        require(type(row) is dict and set(row) == {'phase', 'details'} and row['phase'] == phase)
        if phase != 'intent':
            require(row['details'] is None)
    intent = rows[0]['details']
    require(type(intent) is dict and set(intent) == {'expected_sha256', 'preflight_sha256',
        'dashboard_boot_before', 'listener_inode_before', 'capability_recovery',
        'recovery_binding_sha256', 'supersession_helper_sha256'} and intent['capability_recovery'] is True)
    for name in ('expected_sha256', 'preflight_sha256', 'recovery_binding_sha256', 'supersession_helper_sha256'):
        require(intent[name] == checkpoint[name])
    return intent


class ResumeJournal:
    def __init__(self, checkpoint_sha256):
        self.descriptor = os.open(STATE / 'resume-request.jsonl',
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        os.fchmod(self.descriptor, 0o600)
        fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.append('resume_intent', dict(checkpoint_sha256=checkpoint_sha256))
        sync_directory(STATE)

    append = Journal.append
    close = Journal.close


class CheckpointResume(RecoveryNativeUpdate):
    @classmethod
    def inspect(cls, preflight, expected, checkpoint, intent, validator, binding):
        require(checkpoint['schema'] == 'sage.lantern.cold-native-resume.v1')
        owner = cls.__new__(cls)
        owner.preflight, owner.expected = preflight, expected
        owner.observer = preflight.Observation()
        owner.observer.deadline = time.monotonic() + 180
        owner.checkpoint = checkpoint
        owner.guarded = owner.auth_stopped = owner.swapped = True
        owner.requested = False
        owner.old_dashboard_boot, owner.old_listener = intent['dashboard_boot_before'], intent['listener_inode_before']
        prior = preflight.decode(preflight.trusted(expected['native_before']['manifest']))
        owner.old_version = prior['version']
        replacement = checkpoint['replacement_helper']
        require(type(replacement) is dict and set(replacement) == {'path', 'sha256'}
                and replacement['path'] != expected['restart_helper'] and replacement['path'] not in expected['files']
                and replacement['sha256'] != expected['files'][expected['restart_helper']])
        pinned_source(replacement['path'], replacement['sha256'])
        require(expected['files'][expected['restart_helper']] == preflight.RESTART_SHA)
        require(set(checkpoint['services']) == {preflight.AUTH, preflight.SAGE, *preflight.COLD})
        owner.services = {}
        for name, value in checkpoint['services'].items():
            require(type(value) is dict and set(value) == set(preflight.FIELDS) | {'Restart'})
            if value['LoadState'] == 'not-found':
                require(name == 'sage-lantern-idle-suspend.service')
                owner.services[name] = {field: value[field] for field in preflight.FIELDS}
                continue
            require(value['FragmentPath'] == '/etc/systemd/system/' + name
                    and value['FragmentPath'] in expected['files'])
            paths = value['DropInPaths'].split()
            require(len(paths) == len(set(paths)) and str(owner.guard_path(name)) in paths)
            original = [path for path in paths if path != str(owner.guard_path(name))]
            require(all(path in expected['files'] for path in original))
            owner.services[name] = dict(value, DropInPaths=' '.join(original))
        require(type(checkpoint['receipts']) is dict and 1 <= len(checkpoint['receipts']) <= 1024)
        owner.reports = dict(checkpoint['receipts'])
        for name, digest in owner.reports.items():
            require(re.fullmatch('[0-9a-f]{32}\\.json', name) is not None)
            validator.digest(digest)
            require(preflight.sha(preflight.trusted(preflight.REPORTS / name, owner.observer.uid, 512)) == digest)
        pinned = expected['services'][preflight.SAGE]
        process = Path('/proc', str(pinned['pid']), 'exe')
        owner.running_inode = owner.inode(process)
        descriptor = os.open(process, os.O_RDONLY)
        try:
            info = os.fstat(descriptor)
            require(stat.S_ISREG(info.st_mode) and 0 < info.st_size <= 128 * 1024 * 1024
                    and (info.st_dev, info.st_ino) == owner.running_inode)
            with os.fdopen(os.dup(descriptor), 'rb') as stream:
                raw = stream.read(128 * 1024 * 1024 + 1)
            require(len(raw) == info.st_size and preflight.sha(raw) == expected['files'][preflight.BINARY]
                    and owner.inode(process) == owner.running_inode
                    and owner.running_inode != owner.inode(Path(preflight.BINARY)))
        finally:
            os.close(descriptor)
        owner.bind_recovery(validator, binding, binding['unknown_receipts'], auth_drained=True)
        owner.current()
        for name, value in checkpoint['services'].items():
            require(owner.observer.system._show(name, owner.observer.deadline, tuple(value)) == value)
        require(owner.dashboard_boot(owner.old_version) == owner.old_dashboard_boot
                and listener_owner(pinned['pid'], pinned['start_ticks'], expected['boot_id']) == owner.old_listener)
        owner.governance()
        owner.candidate_version()
        owner.current()
        return owner

    def current(self):
        super().current()
        require(self.preflight.sha(self.preflight.trusted(STATE / 'journal.jsonl', limit=65536))
                == self.checkpoint['journal_sha256'])
        replacement = self.checkpoint['replacement_helper']
        pinned_source(replacement['path'], replacement['sha256'])
        if not self.requested:
            helpers = {replacement['path'], self.expected['restart_helper']}
            for process in self.preflight.LinuxProcesses().snapshot().values():
                require(not any(argument in helpers for argument in process.argv))

    def restart_helper(self):
        return self.checkpoint['replacement_helper']['path']


def resume_checkpoint(args, preflight, expected, validator, binding):
    require(args.capability_recovery and validator is not None)
    raw = pinned_source(args.resume_checkpoint, args.resume_checkpoint_sha256)
    checkpoint = preflight.decode(raw)
    require(type(checkpoint) is dict and set(checkpoint) == {'schema', 'journal_sha256', 'expected_sha256',
        'preflight_sha256', 'recovery_binding_sha256', 'supersession_helper_sha256',
        'replacement_helper', 'services', 'receipts'})
    for name in ('expected_sha256', 'preflight_sha256', 'recovery_binding_sha256', 'supersession_helper_sha256'):
        require(checkpoint[name] == getattr(args, name))
    info = STATE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    require(not os.path.lexists(STATE / 'resume-request.jsonl')
            and not os.path.lexists(STATE / 'capability-supersession.json')
            and not os.path.lexists('/run/sage-lantern-native-restart-request.jsonl'))
    require(preflight.sha(preflight.trusted(STATE / 'capability-binding.json', limit=65536)) == args.recovery_binding_sha256)
    descriptor = os.open(STATE / 'journal.jsonl', os.O_RDONLY | os.O_NOFOLLOW)
    journal = None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_size <= 65536)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            journal_raw = stream.read(65537)
        require(journal_raw == preflight.trusted(STATE / 'journal.jsonl', limit=65536))
        intent = checkpoint_journal(journal_raw, checkpoint, decode=preflight.decode)
        backend = CheckpointResume.inspect(preflight, expected, checkpoint, intent, validator, binding)
        if args.inspect_resume:
            return dict(resume_checkpoint_verified=True, restart_requested=False,
                        checkpoint_sha256=args.resume_checkpoint_sha256, mutation_authorized=False)
        journal = ResumeJournal(args.resume_checkpoint_sha256)
        backend.current()
        journal.append('request_restart_intent')
        backend.restart()
        journal.append('request_restart_accepted_not_completed')
        result = backend.completed()
        backend.current()
        journal.append('native_verified_services_inhibited', result)
        result['supersession'] = backend.publish_supersession(result, journal)
        return result
    finally:
        if journal is not None:
            journal.close()
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('preflight', 'preflight-sha256', 'expected', 'expected-sha256'):
        parser.add_argument('--' + name, required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--inspect-resume', action='store_true')
    mode.add_argument('--resume-request', action='store_true')
    parser.add_argument('--resume-checkpoint')
    parser.add_argument('--resume-checkpoint-sha256')
    parser.add_argument('--capability-recovery', action='store_true')
    parser.add_argument('--recovery-binding')
    parser.add_argument('--recovery-binding-sha256')
    parser.add_argument('--supersession-helper-sha256')
    args = parser.parse_args(argv)
    recovery_options = (args.recovery_binding, args.recovery_binding_sha256, args.supersession_helper_sha256)
    require(all(recovery_options) if args.capability_recovery else not any(recovery_options))
    require((args.capability_recovery and bool(args.resume_checkpoint) and bool(args.resume_checkpoint_sha256))
            if not args.apply else not args.resume_checkpoint and not args.resume_checkpoint_sha256)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    source = pinned_source(args.preflight, args.preflight_sha256)
    preflight = types.ModuleType('pinned_cold_preflight')
    preflight.__file__ = args.preflight
    exec(compile(source, args.preflight, 'exec'), preflight.__dict__)
    raw = preflight.trusted(args.expected, limit=65536)
    require(preflight.sha(raw) == args.expected_sha256)
    expected = preflight.decode(raw)
    validator = binding = None
    if args.capability_recovery:
        helper_path = Path(args.preflight).with_name('auth-capability-supersession.py')
        helper_raw = pinned_source(helper_path, args.supersession_helper_sha256)
        validator = types.ModuleType('pinned_capability_supersession')
        exec(compile(helper_raw, str(helper_path), 'exec'), validator.__dict__)
        binding_raw = pinned_source(args.recovery_binding, args.recovery_binding_sha256)
        binding = preflight.decode(binding_raw)
    def expired(signum, frame):
        raise TimeoutError('cold native transition deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    journal = None
    try:
        if not args.apply:
            print(json.dumps(resume_checkpoint(args, preflight, expected, validator, binding), sort_keys=True))
            return
        observer = preflight.Observation(recovery_entry=args.capability_recovery)
        admission = preflight.verify(expected, observer)
        require((admission.get('recovery_entry_verified') is True and admission.get('preflight_verified') is False)
                if args.capability_recovery else admission.get('preflight_verified') is True)
        observed = observer.observe(expected)
        backend = (RecoveryNativeUpdate if args.capability_recovery else NativeUpdate)(preflight, expected, observed)
        if args.capability_recovery:
            backend.bind_recovery(validator, binding, observer.unknown_receipts)
        journal = Journal(dict(expected_sha256=args.expected_sha256, preflight_sha256=args.preflight_sha256,
                               dashboard_boot_before=backend.old_dashboard_boot, listener_inode_before=backend.old_listener,
                               capability_recovery=args.capability_recovery,
                               recovery_binding_sha256=args.recovery_binding_sha256,
                               supersession_helper_sha256=args.supersession_helper_sha256))
        if args.capability_recovery:
            write_new(STATE / 'capability-binding.json', binding_raw, 0o600)
        result = transition(backend, journal)
        if args.capability_recovery:
            result['supersession'] = backend.publish_supersession(result, journal)
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.alarm(0)
        if journal is not None:
            journal.close()


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Native update incomplete or refused; retain guards/journal/backup. No retry or automatic rollback.', file=sys.stderr)
        raise SystemExit(2) from None
