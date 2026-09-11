#!/usr/bin/env python3
"""One-shot resident-vault native transition with a pinned Linux backend.

Native.observe performs fresh guard, cgroup, workflow, artifact, governance,
process and listener checks, not receipt echoing. Containment is checked against
the matched recover-stale-audio-epoch helper and independently scanned again.
Default inspection never mutates; --apply stops guarded model/auth, swaps the
binary, and requests SAGE's own signed restart. Services remain inhibited.

Snapshot contract is defined by validate_snapshot below. Pins include the exact
boot/PID/start/invocation, native version/image/manifest/source inventory, current
auth invocation, retained historical receipt hashes and containment/scan hashes.
All historical auth outcomes remain unknown. Only the current invocation's real
post-stop confirmed receipt admits replacement. Guards stay installed afterwards.

The restart child loads the SHA-pinned reviewed request helper unchanged, sets
its INTENT global to this transition's exclusive path, and calls its main with
public pinned arguments. No credential is passed to or returned from the child.
An exception after any intent retains all evidence; no retry/resume/rollback is
provided. This module alone is not deployment or physical acceptance evidence.
"""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import resource
import signal
import stat
import subprocess
import sys
import time
import types
import uuid


AUTH = 'sage-lantern-device-auth.service'
PYTHON = '/opt/sage-lantern/venv/bin/python'
NATIVE = Path('/opt/sage-lantern/bin/sage-gui')
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}
PHASES = ('preflight', 'auth_stopped', 'swapped', 'requested', 'complete')


def require(value):
    if value is not True:
        raise RuntimeError('resident_native_transition_refused')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def hex64(value):
    return type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None


def file_identity(info):
    return tuple(getattr(info, field) for field in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def marker_metadata(parent, leaf, uid, gid):
    require(stat.S_ISDIR(parent.st_mode) and parent.st_uid == uid and parent.st_gid == gid
            and stat.S_IMODE(parent.st_mode) == 0o700 and uid > 0 and gid > 0)
    require(stat.S_ISREG(leaf.st_mode) and leaf.st_uid == 0 and leaf.st_gid == 0
            and stat.S_IMODE(leaf.st_mode) == 0o644 and leaf.st_nlink == 1)


def trusted(path, limit=536870912, *, allow_empty=False):
    require(type(allow_empty) is bool)
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and (0 if allow_empty else 1) <= before.st_size <= limit)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(limit + 1)
        after = os.fstat(descriptor)
        require(file_identity(before) == file_identity(after) == file_identity(path.lstat()) and len(raw) == before.st_size)
        return raw
    finally:
        os.close(descriptor)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, raw, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


class Journal:
    def __init__(self, state, pins):
        self.state = Path(state)
        self.state.mkdir(mode=0o700)
        os.chmod(self.state, 0o700)
        sync_directory(self.state.parent)
        self.path = self.state / 'transition.jsonl'
        write_new(self.path, b'')
        self.append('intent', pins)

    def append(self, phase, details=None):
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        with os.fdopen(descriptor, 'ab') as stream:
            stream.write((json.dumps({'phase': phase, 'details': details}, sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())


def validate_pins(pins):
    require(type(pins) is dict and set(pins) == {'schema', 'transition_id', 'boot_id', 'root_id',
        'initialized_sha256', 'native', 'before', 'after', 'auth_invocation', 'auth_process', 'auth_before_sha256', 'historical_reports',
        'containment_sha256', 'scan_sha256', 'restart_helper', 'restart_helper_sha256'})
    require(pins['schema'] == 'sage.lantern.resident-native-transition.v1')
    for name in ('transition_id', 'boot_id'):
        require(str(uuid.UUID(pins[name])) == pins[name] and uuid.UUID(pins[name]).int != 0)
    for name in ('root_id', 'initialized_sha256', 'containment_sha256', 'scan_sha256', 'restart_helper_sha256', 'auth_before_sha256'):
        require(hex64(pins[name]))
    native = pins['native']
    require(type(native) is dict and set(native) == {'pid', 'start_ticks', 'invocation', 'dashboard_boot'})
    require(type(native['pid']) is int and native['pid'] > 0
            and type(native['start_ticks']) is int and native['start_ticks'] > 0)
    require(str(uuid.UUID(native['dashboard_boot'])) == native['dashboard_boot'])
    auth = pins['auth_process']
    require(type(auth) is dict and set(auth) == {'pid', 'start_ticks', 'invocation', 'image_sha256'})
    require(type(auth['pid']) is int and auth['pid'] > 0
            and type(auth['start_ticks']) is int and auth['start_ticks'] > 0
            and auth['invocation'] == pins['auth_invocation'] and hex64(auth['image_sha256']))
    for invocation in (native['invocation'], pins['auth_invocation']):
        require(type(invocation) is str and re.fullmatch('[0-9a-f]{32}', invocation) is not None
                and invocation != '0' * 32)
    for name in ('before', 'after'):
        artifact = pins[name]
        require(type(artifact) is dict and set(artifact) == {'binary', 'manifest', 'binary_sha256',
            'manifest_sha256', 'inventory_sha256', 'version'})
        require(all(hex64(artifact[field]) for field in ('binary_sha256', 'manifest_sha256', 'inventory_sha256')))
        require(all(type(artifact[field]) is str and Path(artifact[field]).is_absolute()
                    and '..' not in Path(artifact[field]).parts for field in ('binary', 'manifest')))
        require(type(artifact['version']) is str and 0 < len(artifact['version']) <= 200)
    require(pins['before']['binary'] == str(NATIVE)
            and pins['after']['binary'] != str(NATIVE)
            and pins['before']['binary_sha256'] != pins['after']['binary_sha256']
            and pins['before']['version'] != pins['after']['version'])
    reports = pins['historical_reports']
    require(type(reports) is dict and all(type(name) is str
            and re.fullmatch('[0-9a-f]{32}\\.json', name) is not None and hex64(checksum)
            for name, checksum in reports.items()))
    require(pins['auth_invocation'] + '.json' not in reports)
    require(type(pins['restart_helper']) is str and Path(pins['restart_helper']).is_absolute()
            and '..' not in Path(pins['restart_helper']).parts)


def validate_snapshot(snapshot, pins, phase):
    require(phase in PHASES and type(snapshot) is dict)
    require(set(snapshot) == {'boot_id', 'native', 'image', 'installed_sha256', 'dashboard_boot',
        'listener_verified', 'encrypted', 'vault_locked', 'guards_verified', 'cgroups_verified',
        'work_verified', 'auth', 'historical_reports', 'app_version', 'pending_plan', 'active_proposal'})
    require(snapshot['boot_id'] == pins['boot_id'])
    require(snapshot['native'] == {key: pins['native'][key] for key in ('pid', 'start_ticks', 'invocation')})
    require(all(snapshot[key] is True for key in ('listener_verified', 'guards_verified',
        'cgroups_verified', 'work_verified', 'encrypted')))
    require(type(snapshot['app_version']) is int and snapshot['app_version'] == 27
            and snapshot['pending_plan'] is None and snapshot['active_proposal'] is None)
    require(snapshot['historical_reports'] == pins['historical_reports'])
    complete = phase == 'complete'
    require(snapshot['image'] == pins['after' if complete else 'before'])
    require(snapshot['installed_sha256'] == pins['before' if phase in ('preflight', 'auth_stopped') else 'after']['binary_sha256'])
    require(type(snapshot['vault_locked']) is bool)
    if complete:
        require(snapshot['vault_locked'] is True
                and str(uuid.UUID(snapshot['dashboard_boot'])) == snapshot['dashboard_boot']
                and snapshot['dashboard_boot'] != pins['native']['dashboard_boot'])
    else:
        require(snapshot['dashboard_boot'] == pins['native']['dashboard_boot'])
    auth = snapshot['auth']
    require(type(auth) is dict and set(auth) == {'invocation', 'active', 'receipt', 'process'})
    require(auth['invocation'] == pins['auth_invocation'])
    if phase == 'preflight':
        require(auth['active'] is True and auth['receipt'] is None and auth['process'] == pins['auth_process'])
    else:
        require(auth['active'] is False and auth['process'] is None and auth['receipt'] == {
            'invocation_id': pins['auth_invocation'], 'closing': True, 'drained': True,
            'local_revoked': True, 'upstream_cleanup': 'confirmed'})


def checked_artifact(artifact):
    raw = trusted(artifact['binary'])
    manifest_raw = trusted(artifact['manifest'], 2097152)
    require(digest(raw) == artifact['binary_sha256'] and digest(manifest_raw) == artifact['manifest_sha256'])
    manifest = json.loads(manifest_raw)
    require(manifest.get('schema') == 'sage.lantern.binary-staging.v1'
            and all(manifest.get(key) == artifact[key] for key in ('binary_sha256', 'inventory_sha256', 'version')))
    result = subprocess.run([artifact['binary'], 'version'], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=5)
    version = result.stdout.decode('ascii').strip()
    require(result.returncode == 0 and version.startswith('sage-gui ' + artifact['version'] + ' (commit ')
            and version.endswith(', max-app-v27)'))
    return raw, manifest_raw


def backup_and_swap(state, pins, reverify):
    old, manifest = checked_artifact(pins['before'])
    new, _ = checked_artifact(pins['after'])
    write_new(state / 'native-before', old, 0o700)
    write_new(state / 'manifest-before.json', manifest)
    require(digest(trusted(state / 'native-before')) == pins['before']['binary_sha256']
            and digest(trusted(state / 'manifest-before.json')) == pins['before']['manifest_sha256'])
    temporary = NATIVE.with_name('.sage-gui-resident-' + pins['transition_id'])
    write_new(temporary, new, 0o755)
    reverify()
    require(digest(trusted(NATIVE)) == pins['before']['binary_sha256']
            and digest(trusted(temporary)) == pins['after']['binary_sha256'])
    os.replace(temporary, NATIVE)
    sync_directory(NATIVE.parent)


def stop_auth_only(service, reverify):
    require(service == AUTH)
    reverify()
    subprocess.run(['/usr/bin/systemctl', '--no-pager', 'stop', AUTH],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=ENV, timeout=45, check=True)


def restart_child(pins, state):
    raw = trusted(pins['restart_helper'], 1048576)
    require(digest(raw) == pins['restart_helper_sha256'])
    intent = state / 'signed-request.jsonl'
    require(not os.path.lexists(intent))
    child = (
        'import hashlib, pathlib, sys; '
        'raw=pathlib.Path(sys.argv[1]).read_bytes(); '
        'assert hashlib.sha256(raw).hexdigest()==sys.argv[2]; '
        'scope={"__name__":"resident_restart_child","__file__":sys.argv[1]}; '
        'exec(compile(raw,sys.argv[1],"exec"),scope); '
        'scope["INTENT"]=pathlib.Path(sys.argv[3]); scope["main"](sys.argv[4:])'
    )
    result = subprocess.run([PYTHON, '-I', '-B', '-c', child, pins['restart_helper'],
        pins['restart_helper_sha256'], str(intent), '--unit-id', 'unit-a', '--boot-id', pins['boot_id'],
        '--root-id', pins['root_id'], '--initialized-sha256', pins['initialized_sha256'], '--request-restart'],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=35)
    require(result.returncode == 0 and len(result.stdout) <= 4096
            and json.loads(result.stdout) == {'accepted': True, 'completed': False})


def transition(pins, backend, state):
    """Execute once, retaining every intent and all partial outcomes."""
    validate_pins(pins)
    state = Path(state)
    require(state == Path('/var/lib') / ('sage-lantern-resident-native-' + pins['transition_id']))
    require(backend.verify_containment(pins['containment_sha256'], pins['scan_sha256']) is True)
    journal = Journal(state, pins)
    if isinstance(backend, Native):
        backend.prepare(journal)
    validate_snapshot(backend.observe('preflight'), pins, 'preflight')
    for name, before, after, action in (
        ('stop_auth', 'preflight', 'auth_stopped', lambda: backend.stop_pinned_auth(AUTH)),
        ('backup_and_swap', 'auth_stopped', 'swapped', lambda: backend.backup_and_swap(state, pins)),
    ):
        validate_snapshot(backend.observe(before), pins, before)
        journal.append(name + '_intent')
        action()
        validate_snapshot(backend.observe(after), pins, after)
        journal.append(name + '_done')
    validate_snapshot(backend.observe('swapped'), pins, 'swapped')
    journal.append('request_restart_intent')
    restart_child(pins, state)
    journal.append('accepted_not_completed')
    result = backend.wait_for_native_exec()
    validate_snapshot(result, pins, 'complete')
    confirmed = backend.observe('complete')
    validate_snapshot(confirmed, pins, 'complete')
    require(confirmed == result)
    journal.append('native_verified_services_inhibited', result)
    return {'native_updated': True, 'appliance_ready': False, 'user_unlock_required': True}


def pinned_module(pin, name):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'} and hex64(pin['sha256']))
    raw = trusted(pin['path'], 2097152)
    require(digest(raw) == pin['sha256'])
    module = types.ModuleType(name)
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


class Native:
    """Concrete Linux backend. Envelope contains pins, inspection {path,sha256},
    helpers {inspector,recovery,preflight,update}, containment {path,sha256}, scan
    {path,sha256}, supersession {path,sha256}, and extra_files {path:sha256}.
    Helpers are source-hash pinned and loaded only as utilities, never their main.
    extra_files must pin auth helper, initialized-public and unit.env at minimum.
    """

    def __init__(self, envelope):
        require(type(envelope) is dict and set(envelope) == {'pins', 'inspection', 'helpers',
            'containment', 'scan', 'supersession', 'extra_files'})
        self.envelope = envelope
        self.pins = envelope['pins']
        validate_pins(self.pins)
        require(set(envelope['helpers']) == {'inspector', 'recovery', 'preflight', 'update'})
        self.inspector = pinned_module(envelope['helpers']['inspector'], 'resident_inspector')
        self.recovery = pinned_module(envelope['helpers']['recovery'], 'resident_recovery')
        self.preflight = pinned_module(envelope['helpers']['preflight'], 'resident_preflight')
        self.update = pinned_module(envelope['helpers']['update'], 'resident_update_utilities')
        raw = self.pinned_bytes(envelope['inspection'])
        self.expected = self.inspector.decode(raw)
        self.inspector.validate(self.expected)
        require(self.expected['unit_id'] == 'unit-a' and self.expected['boot_id'] == self.pins['boot_id'])
        self.backend = self.recovery.Backend(self.inspector, self.expected, raw,
            envelope['helpers']['inspector']['sha256'])
        self.backend.deadline = self.backend.native.deadline = time.monotonic() + 300
        self.bind_recovery_observer()
        self.observer = self.preflight.Observation()
        self.observer.deadline = self.backend.deadline
        self.state = Path('/var/lib/sage-lantern-resident-native-' + self.pins['transition_id'])
        self.guarded = self.model_stopped = self.auth_stopped = self.swapped = self.completed = False
        self.last_scan = None
        self.model = 'sage-lantern-llm.service'
        self.sage = self.recovery.SAGE
        self.guard_paths = {service: Path('/etc/systemd/system') / (service + '.d') /
            ('zzzz-resident-' + self.pins['transition_id'] + '.conf') for service in (AUTH, self.model)}
        self.guard = ('[Unit]\nConditionPathExists=!' + str(self.state) + '\n[Service]\nRestart=no\n').encode()
        native = self.expected['services'][self.sage]
        require(native['pid'] == self.pins['native']['pid']
                and int(native['start_ticks']) == self.pins['native']['start_ticks']
                and native['invocation'] == self.pins['native']['invocation']
                and native['image_sha256'] == self.pins['before']['binary_sha256'])
        auth = self.expected['services'][AUTH]
        require(self.pins['auth_process'] == dict(pid=auth['pid'], start_ticks=int(auth['start_ticks']),
            invocation=auth['invocation'], image_sha256=auth['image_sha256']))
        required = {'/usr/local/libexec/sage-lantern-device-auth',
            '/etc/sage-lantern/initialized-public.json', '/etc/sage-lantern/unit.env'}
        require(type(envelope['extra_files']) is dict and required <= set(envelope['extra_files'])
                and all(hex64(checksum) for checksum in envelope['extra_files'].values()))
        require(envelope['extra_files']['/etc/sage-lantern/initialized-public.json'] == self.pins['initialized_sha256'])

    @staticmethod
    def pinned_bytes(pin):
        require(type(pin) is dict and set(pin) == {'path', 'sha256'} and hex64(pin['sha256']))
        raw = trusted(pin['path'], 2097152)
        require(digest(raw) == pin['sha256'])
        return raw

    def bind_recovery_observer(self):
        self.backend.contained = self.contained
        self.backend.unchanged = self.contained

    def archived_bytes(self, pin):
        require(type(pin) is dict and set(pin) == {'sha256', 'archive_path'} and hex64(pin['sha256']))
        path = Path(pin['archive_path'])
        require(path.parent == self.recovery.STATE / 'archive'
                and re.fullmatch('[0-9]{3}\\.bin', path.name) is not None)
        raw = trusted(path, 2097152, allow_empty=pin['sha256'] == digest(b''))
        require(stat.S_IMODE(path.lstat().st_mode) == 0o600 and digest(raw) == pin['sha256'])
        return raw

    def files(self):
        self.backend.native.files(self.expected)
        for filename, checksum in self.envelope['extra_files'].items():
            require(digest(trusted(filename)) == checksum)
        for pin in self.envelope['helpers'].values():
            self.pinned_bytes(pin)
        self.pinned_bytes(self.envelope['inspection'])
        require(digest(trusted(self.pins['restart_helper'], 1048576)) == self.pins['restart_helper_sha256'])
        for name in ('before', 'after'):
            artifact = self.pins[name]
            raw = trusted(artifact['manifest'], 2097152)
            require(digest(raw) == artifact['manifest_sha256'])
            value = self.inspector.decode(raw)
            require(value.get('schema') == 'sage.lantern.binary-staging.v1'
                    and all(value.get(key) == artifact[key] for key in ('version', 'binary_sha256', 'inventory_sha256')))
        require(digest(trusted(self.pins['after']['binary'])) == self.pins['after']['binary_sha256'])

    def contained(self):
        self.files()
        self.backend.guards()
        self.observer.system._guard(self.sage, self.observer.deadline, '/var/lib/sage-lantern-cold-native-update')
        require(self.backend.properties(self.sage, ('Restart',)) == {'Restart': 'no'})
        result = {service: self.backend.empty(service) for service in self.recovery.PRODUCERS}
        for service in (AUTH, self.model, self.sage):
            stopped = self.auth_stopped if service == AUTH else self.model_stopped if service == self.model else False
            if stopped:
                self.backend.empty(service)
            else:
                pin = copy.deepcopy(self.expected['services'][service])
                if self.guarded and service in self.guard_paths:
                    pin['dropins'].append(str(self.guard_paths[service]))
                if self.completed and service == self.sage:
                    pin['image_sha256'] = self.pins['after']['binary_sha256']
                self.backend.native.service(service, pin)
        if self.guarded:
            for service, path in self.guard_paths.items():
                require(trusted(path) == self.guard)
                self.observer.system._guard(service, self.observer.deadline, str(self.state))
                fields = self.backend.properties(service, ('DropInPaths', 'Restart', 'NeedDaemonReload', 'FragmentPath'))
                require(set(fields['DropInPaths'].split()) == set(self.expected['services'][service]['dropins']) | {str(path)}
                        and fields['Restart'] == 'no' and fields['NeedDaemonReload'] == 'no'
                        and fields['FragmentPath'] == '/etc/systemd/system/' + service)
        self.observer.system._sleep_idle(self.observer.deadline)
        return result

    def verify_containment(self, containment, scan):
        require(containment == self.envelope['containment']['sha256'] == self.pins['containment_sha256']
                and scan == self.envelope['scan']['sha256'] == self.pins['scan_sha256'])
        receipt = self.inspector.decode(self.pinned_bytes(self.envelope['containment']))
        scanned = self.inspector.decode(self.pinned_bytes(self.envelope['scan']))
        require(receipt.get('schema') == 'sage.lantern.stale-audio-recovery.v1'
                and receipt.get('boot_id') == self.expected['boot_id']
                and receipt.get('expected_sha256') == self.envelope['inspection']['sha256']
                and receipt.get('inspector_sha256') == self.envelope['helpers']['inspector']['sha256']
                and receipt.get('native') == self.expected['services'][self.sage]
                and receipt.get('scan_sha256') == scan
                and receipt.get('quiescence_verified') is True
                and receipt.get('durable_reconciliation_verified') is True
                and receipt.get('historical_work_disposition') == 'unknown'
                and receipt.get('drain_verified') is False
                and scanned.get('expected_sha256') == self.envelope['inspection']['sha256']
                and scanned.get('storage_epoch') == receipt.get('storage_epoch'))
        self.receipt, self.scanned = receipt, scanned
        require(self.contained() == receipt['producers'])
        require(self.backend.native.snapshot(self.expected) == receipt['storage_epoch'])
        for filename, checksum in receipt['commissioning_sha256'].items():
            require(digest(self.commissioning_bytes(filename)) == checksum)
        for filename, pin in receipt['preserved_records'].items():
            owner = self.expected['services'][self.recovery.VOICE]['uid'] if filename.startswith('/run/sage-lantern-voice/') else 0
            require(digest(self.backend.capture_file(filename, owner)) == pin['sha256']
                    and digest(self.archived_bytes(pin)) == pin['sha256'])
        self.verify_supersession()
        checked_artifact(self.pins['before'])
        checked_artifact(self.pins['after'])
        self.auth()
        self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), self.expected['chain_id'], self.observer.get(26657, '/abci_info'))
        native = self.pins['native']
        self.update.listener_owner(native['pid'], native['start_ticks'], self.pins['boot_id'])
        health = self.observer.get(8080, '/v1/dashboard/health')
        require(health.get('boot_id') == native['dashboard_boot']
                and health.get('version') == self.pins['before']['version'] and health.get('encrypted') is True)
        require(self.observer.get(3196, '/state') == {'locked': True})
        return True

    def commissioning_bytes(self, filename):
        require(filename in self.recovery.COMMISSION)
        if filename != '/var/lib/sage-lantern/LOCAL_VOICE_READY':
            return trusted(filename)
        path = Path(filename)
        account = pwd.getpwnam('sage-lantern')
        require(account.pw_uid == self.expected['services'][self.recovery.VOICE]['uid'])
        parent, leaf = path.parent.lstat(), path.lstat()
        marker_metadata(parent, leaf, account.pw_uid, account.pw_gid)
        raw = self.backend.capture_file(path, account.pw_uid)
        require(file_identity(parent) == file_identity(path.parent.lstat())
                and file_identity(leaf) == file_identity(path.lstat()))
        return raw

    def verify_supersession(self):
        value = self.inspector.decode(self.pinned_bytes(self.envelope['supersession']))
        require(value.get('schema') == 'sage.lantern.auth-capability-supersession.v1'
                and value.get('boot_id') == self.pins['boot_id'] and value.get('root_id') == self.pins['root_id']
                and value.get('chain_id') == self.expected['chain_id']
                and value.get('initialized_sha256') == self.pins['initialized_sha256']
                and value.get('capability_invalidated') is True
                and value.get('historical_request_completion') == 'unknown')
        after = value['sage_after']
        require(all(after[key] == self.pins['native'][key] for key in ('pid', 'start_ticks', 'invocation'))
                and after['application_boot_id'] == self.pins['native']['dashboard_boot']
                and after['application_boot_id'] != value['sage_before']['application_boot_id']
                and value['native_after'] == {key: self.pins['before'][key]
                    for key in ('binary_sha256', 'manifest_sha256', 'inventory_sha256', 'version')})
        require(all(self.pins['historical_reports'].get(instance + '.json') == checksum
            for instance, checksum in value['unknown_receipts'].items()))
        self.superseded = value['unknown_receipts']

    def prepare(self, journal):
        self.contained()
        journal.append('backend_pins', self.envelope)
        auth_before = self.preflight.trusted(self.preflight.REPORTS /
            (self.pins['auth_invocation'] + '.json'), self.observer.uid, 512)
        require(digest(auth_before) == self.pins['auth_before_sha256'])
        write_new(self.state / 'auth-before.json', auth_before)
        journal.append('inhibit_auth_model_intent')
        for path in self.guard_paths.values():
            if not path.parent.exists():
                path.parent.mkdir(mode=0o755)
                sync_directory(path.parent.parent)
            for parent in path.parents:
                info = parent.lstat()
                require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
            write_new(path, self.guard, 0o644)
        subprocess.run(['/usr/bin/systemctl', '--no-pager', 'daemon-reload'], check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV, timeout=5)
        self.guarded = True
        self.contained()
        journal.append('inhibit_auth_model_done')
        journal.append('stop_model_intent')
        self.contained()
        subprocess.run(['/usr/bin/systemctl', '--no-pager', 'stop', self.model], check=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=ENV, timeout=45)
        self.model_stopped = True
        self.contained()
        journal.append('stop_model_done')

    def auth(self):
        directory = self.preflight.REPORTS
        current = self.pins['auth_invocation'] + '.json'
        require({path.name for path in directory.iterdir()} == set(self.pins['historical_reports']) | {current})
        for name, checksum in self.pins['historical_reports'].items():
            raw = self.preflight.trusted(directory / name, self.observer.uid, 512)
            require(digest(raw) == checksum)
            value = self.inspector.decode(raw)
            if name[:-5] in self.superseded:
                require(value['instance_id'] == name[:-5] and value['upstream_cleanup'] == 'unknown')
            else:
                self.preflight.report(value, name[:-5], current=False)
        raw = self.preflight.trusted(directory / current, self.observer.uid, 512)
        if not self.auth_stopped:
            require(digest(raw) == self.pins['auth_before_sha256'])
        value = self.inspector.decode(raw)
        self.preflight.report(value, self.pins['auth_invocation'], current=not self.auth_stopped)
        return dict(invocation=self.pins['auth_invocation'], active=not self.auth_stopped,
            process=None if self.auth_stopped else self.pins['auth_process'],
            receipt=None if not self.auth_stopped else dict(invocation_id=value['instance_id'],
                closing=value['closing'], drained=value['drained'], local_revoked=value['local_revoked'],
                upstream_cleanup=value['upstream_cleanup']))

    def observe(self, phase):
        self.contained()
        if not self.completed:
            epoch, scan = self.backend.reconcile()
            require(epoch == self.receipt['storage_epoch'] and scan == self.receipt['reconciliation'])
            self.last_scan = scan
        else:
            require(self.last_scan == self.receipt['reconciliation'])
        self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), self.expected['chain_id'], self.observer.get(26657, '/abci_info'))
        native = self.pins['native']
        listener = self.update.listener_owner(native['pid'], native['start_ticks'], self.pins['boot_id'])
        health = self.observer.get(8080, '/v1/dashboard/health')
        image = self.pins['after' if self.completed else 'before']
        require(health.get('sage') == 'running' and health.get('version') == image['version'])
        require(self.update.listener_owner(native['pid'], native['start_ticks'], self.pins['boot_id']) == listener)
        auth = self.auth()
        if not self.auth_stopped:
            require(self.observer.get(3196, '/state') == {'locked': True})
        self.contained()
        return dict(boot_id=self.pins['boot_id'], native={key: native[key] for key in ('pid', 'start_ticks', 'invocation')},
            image=image, installed_sha256=digest(trusted(NATIVE)), dashboard_boot=health['boot_id'],
            listener_verified=True, encrypted=health.get('encrypted'), vault_locked=health.get('vault_locked'),
            guards_verified=self.guarded, cgroups_verified=True, work_verified=self.model_stopped,
            auth=auth, historical_reports=self.pins['historical_reports'], app_version=27,
            pending_plan=None, active_proposal=None)

    def stop_pinned_auth(self, service):
        stop_auth_only(service, lambda: validate_snapshot(self.observe('preflight'), self.pins, 'preflight'))
        self.auth_stopped = True
        self.contained()
        self.auth()

    def backup_and_swap(self, state, pins):
        backup_and_swap(state, pins, lambda: validate_snapshot(self.observe('auth_stopped'), pins, 'auth_stopped'))
        self.swapped = True

    def wait_for_native_exec(self):
        require(self.swapped and self.auth_stopped and self.model_stopped)
        while self.backend.remaining() > 0:
            process = Path('/proc') / str(self.pins['native']['pid']) / 'exe'
            info, installed = process.stat(), NATIVE.stat()
            if (info.st_dev, info.st_ino) == (installed.st_dev, installed.st_ino):
                self.completed = True
                try:
                    return self.observe('complete')
                except (ConnectionRefusedError, self.update.StartupUnavailable):
                    pass
            time.sleep(0.2)
        raise TimeoutError('native completion unknown')


def main(argv=None):
    parser = argparse.ArgumentParser(description=Native.__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    trusted(__file__, 2097152)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    def timeout(signum, frame):
        raise TimeoutError('native transition deadline or signal')
    for number in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(number, timeout)
    signal.setitimer(signal.ITIMER_REAL, 300)
    raw = Native.pinned_bytes({'path': args.expected, 'sha256': args.expected_sha256})
    envelope = json.loads(raw)
    backend = Native(envelope)
    if args.apply:
        result = transition(backend.pins, backend, backend.state)
    else:
        require(backend.verify_containment(backend.pins['containment_sha256'], backend.pins['scan_sha256']) is True)
        checked_artifact(backend.pins['before'])
        checked_artifact(backend.pins['after'])
        backend.auth()
        result = {'containment_verified': True, 'applied': False, 'auth_drain_verified': False,
            'model_idle_verified': False, 'appliance_ready': False}
    print(json.dumps(result, sort_keys=True))
    signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Native transition refused or outcome unknown; retain all evidence; no automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
