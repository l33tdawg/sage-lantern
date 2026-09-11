#!/usr/bin/env python3
"""Install exactly two prepared fresh mesh files, never activate hardware.

Default is read-only. --apply retains an exclusive root journal and refuses
existing destinations. Reads signed private storage through the pinned original
preparer; never performs radio handshake, microphone capture or service start.
See docs/lantern/INSTALL_PREPARED_FRESH_MESH.md for the public input contract.
"""

import argparse
import hashlib
import json
import os
import pwd
from pathlib import Path
import re
import signal
import stat
import sys
import time
import uuid
from types import ModuleType, SimpleNamespace


STATE = Path('/var/lib/sage-lantern-fresh-mesh-install')
NATIVE_GUARD = Path('/var/lib/sage-lantern-cold-native-update')
B_GUARD = Path('/var/lib/sage-lantern-b-mesh-containment')
B_SERVICES = ('sage-lantern-fleet-admin.service',)
B_ABSENT = ('/var/lib/sage-lantern-mesh-supervisor', '/run/sage-lantern-mesh-control',
    '/run/sage-lantern-voice-bootstrap.json', '/run/sage-lantern-voice',
    '/var/lib/sage-lantern/LOCAL_VOICE_READY', '/etc/sage-lantern/audio-commissioned.json',
    '/var/lib/sage-lantern-cross-boot-audio')
DESTINATIONS = {'mesh-runtime.json': (Path('/etc/sage-lantern/mesh-runtime.json'), 0o600),
    '20-radio.conf': (Path('/etc/systemd/system/sage-lantern-mesh-runtime.service.d/20-radio.conf'), 0o644)}
SERVICES = tuple('sage-lantern-' + name + '.service' for name in ('mesh-runtime', 'voice-loop', 'llm'))
SOURCES = {'prepare-fresh-mesh-runtime.py', 'prepare-mesh-runtime.py', 'provision-fresh-appliance.py'}
ARGUMENTS = {'manifest', 'radio_inventory', 'current_sage_policy', 'manifest_sha256', 'radio_inventory_sha256',
    'commission_sha256', 'app_sha256', 'bindings_sha256', 'current_sage_sha256', 'current_sage_policy_sha256',
    'sage_binary_sha256', 'radio_fingerprint'}


def require(value):
    if not value:
        raise ValueError('fresh_mesh_install_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def fresh_b(expected):
    mode = expected.get('containment')
    require(mode is None or (mode == 'fresh-b-v1' and expected.get('unit_id') == 'unit-b'))
    return mode == 'fresh-b-v1'


def b_guard_path(service):
    require(service in SERVICES + B_SERVICES)
    return Path('/etc/systemd/system') / (service + '.d') / 'zzzz-b-first-mesh.conf'


def b_guard_bytes():
    return ('[Unit]\nConditionPathExists=!' + str(B_GUARD) + '\n[Service]\nRestart=no\n').encode()


def b_no_prior_lifetime():
    require(all(not os.path.lexists(path) for path in B_ABSENT))
    require(not os.path.lexists(NATIVE_GUARD))
    account = pwd.getpwnam('sage-lantern')
    b_fresh_history(Path('/var/lib/sage-lantern/history'), account.pw_uid, account.pw_gid)
    for name in ('conversations-v1', 'public-outbox', 'outbox', 'photos'):
        path = Path('/var/lib/sage-lantern') / name
        require(not os.path.lexists(path))


def b_fresh_history(root, uid, gid):
    require(uid > 0 and gid > 0)
    def signature(info):
        return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
    def metadata(path, mode, regular=False):
        info = path.lstat()
        require((stat.S_ISREG(info.st_mode) if regular else stat.S_ISDIR(info.st_mode))
                and info.st_uid == uid and info.st_gid == gid and stat.S_IMODE(info.st_mode) == mode)
        require(not set(os.listxattr(path)) & {'system.posix_acl_access', 'system.posix_acl_default'})
        if regular:
            require(info.st_nlink == 1)
        return signature(info)
    for parent in root.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, uid)
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX))
        require(not set(os.listxattr(parent)) & {'system.posix_acl_access', 'system.posix_acl_default'})
    root_pin = metadata(root, 0o700)
    require({path.name for path in root.iterdir()} == {'history.json', 'conversations-v1'})
    conversation = root / 'conversations-v1'
    conversation_pin = metadata(conversation, 0o700)
    children = {path.name for path in conversation.iterdir()}
    require(children in (set(), {'owner.lock'}))
    lock_pin = None
    if children:
        lock_pin = metadata(conversation / 'owner.lock', 0o600, True)
        require((conversation / 'owner.lock').lstat().st_size == 0)
    path = root / 'history.json'
    before = metadata(path, 0o600, True)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        require(signature(os.fstat(stream.fileno())) == before and before[6] <= 4096)
        raw = stream.read(4097)
        require(len(raw) == before[6] and signature(os.fstat(stream.fileno())) == before)
    preferences = decode(raw)
    require(type(preferences) is dict and set(preferences) == {'version', 'enabled', 'photos_enabled', 'generation', 'items'}
            and type(preferences['version']) is int and preferences['version'] == 2
            and type(preferences['enabled']) is bool and type(preferences['photos_enabled']) is bool
            and type(preferences['generation']) is str and preferences['items'] == [])
    generation = uuid.UUID(preferences['generation'])
    require(generation.int != 0 and str(generation) == preferences['generation'])
    require(metadata(path, 0o600, True) == before and metadata(root, 0o700) == root_pin
            and metadata(conversation, 0o700) == conversation_pin)
    if lock_pin is not None:
        require(metadata(conversation / 'owner.lock', 0o600, True) == lock_pin)


def b_other_producers():
    uid = pwd.getpwnam('sage-lantern').pw_uid
    require(uid > 0)
    allowed = {'0::/system.slice/' + name + '.service' for name in
               ('sage-lantern-personal-sage', 'sage-lantern-device-auth', 'sage-lantern-status')}
    for process in Path('/proc').iterdir():
        if not process.name.isdigit():
            continue
        try:
            status = (process / 'status').read_text()
            ids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
            if str(uid) in ids:
                require((process / 'cgroup').read_text().strip() in allowed)
        except (FileNotFoundError, ProcessLookupError):
            continue


def b_empty_workflows(config, storage_epoch, guard):
    from sage_lantern.mesh_daemon import load_identity
    from sage_lantern.device_access import access_epoch
    from sage_lantern.message_contacts import RecipientID
    from sage_lantern.message_storage import MessageStorageProbe
    from sage_lantern.workflow_store import WorkflowStore
    from sage_sdk import SageClient
    epoch = access_epoch()
    require(epoch is not None)
    def access():
        guard()
        require(access_epoch() == epoch)
        return True
    identity, descriptor = load_identity(config)
    try:
        with SageClient(config['endpoint'], identity, timeout=5, trust_env=False) as client:
            probe = MessageStorageProbe(client, actor=RecipientID(config['chain_id'], config['agent_id']),
                                        endpoint=config['endpoint'])
            store = WorkflowStore(client, storage=probe)
            require(probe.snapshot().epoch == storage_epoch)
            require(store.scan(limit=50, max_records=4096, access_check=access) == [])
            require(store.scan(limit=50, max_records=4096, access_check=access) == [])
            require(probe.snapshot().epoch == storage_epoch and access())
    finally:
        os.close(descriptor)


def b_services(system, expected, deadline, installed_guards=(), *, loaded=False, mesh_installed=False, releasing=False):
    require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected['boot_id'])
    b_no_prior_lifetime()
    b_other_producers()
    require(type(expected['service_files']) is dict and expected['service_files'])
    for path, digest in expected['service_files'].items():
        require(sha(read(path)) == digest)
    seen = set()
    for service in SERVICES + B_SERVICES:
        fields = ('LoadState', 'ActiveState', 'MainPID', 'Job', 'ControlGroup', 'Slice', 'KillMode',
                  'Restart', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
        value = system._show(service, deadline, fields)
        fragment = '/etc/systemd/system/' + service
        originals = {path for path in expected['service_files'] if path.startswith(fragment + '.d/')}
        require(fragment in expected['service_files'])
        seen.update(originals | {fragment})
        added = {str(b_guard_path(service))} if service in installed_guards else set()
        radio = {str(DESTINATIONS['20-radio.conf'][0])} if mesh_installed and service == SERVICES[0] else set()
        dropins = value['DropInPaths'].split()
        require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'inactive'
                and value['MainPID'] == '0' and value['Job'] in ('', '0')
                and value['ControlGroup'] in ('', '/system.slice/' + service)
                and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group'
                and value['FragmentPath'] == fragment and len(dropins) == len(set(dropins)))
        if loaded:
            require(originals | added <= set(dropins) <= originals | added | radio
                    and (value['NeedDaemonReload'] == 'no' or bool(radio)) and value['Restart'] == 'no')
            system._guard(service, deadline, str(B_GUARD))
        else:
            removed = {str(b_guard_path(service))} if releasing else set()
            require(originals <= set(dropins) <= originals | added | removed | radio)
            require(value['NeedDaemonReload'] == 'no' or bool(added) or releasing)
        if added:
            require(read(b_guard_path(service), 0o644) == b_guard_bytes())
            if releasing:
                system._guard(service, deadline, str(B_GUARD))
        else:
            require(not os.path.lexists(b_guard_path(service)))
        system._empty_tree(system._cgroup('/system.slice/' + service), deadline)
        require(system._show(service, deadline, fields) == value)
    require(seen == set(expected['service_files']))
    system._sleep_idle(deadline)


def establish_b(expected, *, apply=False):
    require(fresh_b(expected) and not os.path.lexists(B_GUARD) and not os.path.lexists(STATE))
    preparer = load_preparer(expected)
    arguments = dict(expected['preparation_args'])
    for name in ('manifest', 'radio_inventory', 'current_sage_policy'):
        arguments[name] = Path(arguments[name])
    args = SimpleNamespace(**arguments)
    raw, config, binding = preparer.inputs(args)
    require(config['unit_id'] == 'unit-b')
    from sage_lantern.suspend_system import SuspendSystem
    system = SuspendSystem()
    deadline = time.monotonic() + 120
    installed = set()
    def guard():
        system._remaining(deadline)
        b_services(system, expected, deadline, installed)
        for path, _ in DESTINATIONS.values():
            require(not os.path.lexists(path))
        require(preparer.inputs(args) == (raw, config, binding))
    guard()
    observation = preparer.observe(config, binding)
    require(observation['boot_id'] == expected['boot_id'])
    b_empty_workflows(config, observation['storage_epoch'], guard)
    if not apply:
        return dict(containment_checked=True, installed=False, workflow_empty_verified=True)
    directory(B_GUARD.parent)
    B_GUARD.mkdir(mode=0o700)
    sync(B_GUARD.parent)
    write_new(B_GUARD / 'expected.json', json.dumps(expected, sort_keys=True).encode(), 0o600)
    for service in SERVICES + B_SERVICES:
        guard()
        path = b_guard_path(service)
        if not path.parent.exists():
            directory(path.parent.parent)
            path.parent.mkdir(mode=0o755)
            sync(path.parent.parent)
        write_new(path, b_guard_bytes(), 0o644)
        installed.add(service)
        guard()
    system._command(['daemon-reload'], deadline)
    def fenced():
        b_services(system, expected, deadline, installed, loaded=True)
        require(preparer.inputs(args) == (raw, config, binding))
    fenced()
    b_empty_workflows(config, observation['storage_epoch'], fenced)
    require(preparer.observe(config, binding) == observation)
    write_new(B_GUARD / 'complete.json', json.dumps(dict(schema='sage.lantern.b-first-mesh-containment.v1',
        expected_sha256=sha(json.dumps(expected, sort_keys=True).encode()), boot_id=expected['boot_id'],
        storage_epoch=observation['storage_epoch']), sort_keys=True).encode(), 0o600)
    return dict(contained=True, services_started=False, guards_retained=True, workflow_empty_verified=True)


def release_b(expected, *, apply=False):
    require(fresh_b(expected))
    require(not os.path.lexists(B_GUARD / 'release-intent.json'))
    receipt = decode(read(STATE / 'complete.json', 0o600))
    require(receipt == dict(schema='sage.lantern.b-mesh-installed.v1',
                            expected_sha256=sha(json.dumps(expected, sort_keys=True).encode())))
    installer = Installer(expected, already_installed=True)
    installer.current()
    if not apply:
        return dict(release_checked=True, released=False)
    write_new(B_GUARD / 'release-intent.json', json.dumps(receipt, sort_keys=True).encode(), 0o600)
    retained = set(SERVICES + B_SERVICES)
    def guard():
        b_services(installer.system, expected, installer.deadline, retained,
                   mesh_installed=True, releasing=True)
        for name, (path, mode) in DESTINATIONS.items():
            require(read(path, mode) == installer.artifacts[name])
        require(decode(read(STATE / 'complete.json', 0o600)) == receipt)
        validate_prepared(installer.preparer, installer.args, decode(installer.receipt_raw),
                          installer.artifacts, 'unit-b', expected['boot_id'])
    for service in SERVICES + B_SERVICES:
        guard()
        path = b_guard_path(service)
        require(read(path, 0o644) == b_guard_bytes())
        write_new(B_GUARD / (service + '.released-guard'), b_guard_bytes(), 0o600)
        path.unlink()
        sync(path.parent)
        retained.remove(service)
        guard()
    installer.system._command(['daemon-reload'], installer.deadline)
    guard()
    for service in SERVICES + B_SERVICES:
        value = installer.system._show(service, installer.deadline, ('DropInPaths', 'NeedDaemonReload'))
        require(str(b_guard_path(service)) not in value['DropInPaths'].split() and value['NeedDaemonReload'] == 'no')
    _, config, binding = installer.preparer.inputs(installer.args)
    observation = installer.preparer.observe(config, binding)
    b_empty_workflows(config, observation['storage_epoch'], guard)
    write_new(B_GUARD / 'released.json', json.dumps(dict(schema='sage.lantern.b-mesh-released.v1',
        boot_id=expected['boot_id'], services_started=False), sort_keys=True).encode(), 0o600)
    return dict(released=True, evidence_retained=True, services_started=False, audio_commissioning_required=True)


def decode(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            require(key not in value)
            value[key] = item
        return value
    return json.loads(raw, object_pairs_hook=pairs)


def directory(path, mode=None):
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)))
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    if mode is not None:
        require(stat.S_IMODE(path.lstat().st_mode) == mode)


def read(path, mode=None):
    path = Path(path)
    directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 128 * 1024)
        require(mode is None or stat.S_IMODE(before.st_mode) == mode)
        raw = os.read(descriptor, before.st_size + 1)
        def signature(info):
            return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
                and signature(before) == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, raw, mode):
    directory(path.parent)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def load_preparer(expected):
    root = Path(expected['preparer_dir'])
    require(set(expected['preparer_sha256']) == SOURCES)
    raw = {name: read(root / name) for name in SOURCES}
    require(all(sha(data) == expected['preparer_sha256'][name] for name, data in raw.items()))
    module = ModuleType('pinned_fresh_mesh_preparer')
    module.__file__ = str(root / 'prepare-fresh-mesh-runtime.py')
    exec(compile(raw['prepare-fresh-mesh-runtime.py'], module.__file__, 'exec'), module.__dict__)
    require(all(read(root / name) == data for name, data in raw.items()))
    return module


def validate_prepared(preparer, args, receipt, artifacts, unit_id, boot_id):
    from sage_lantern.mesh_daemon import device_policy
    raw, config, binding = preparer.inputs(args)
    observation = preparer.observe(config, binding)
    require(config['unit_id'] == unit_id and observation['boot_id'] == boot_id)
    expected_artifacts = {'mesh-runtime.json': preparer.encoded(config), '20-radio.conf': device_policy(config).encode()}
    require(artifacts == expected_artifacts)
    expected = dict(schema='sage.lantern.fresh-mesh-preparation.v1', unit_id=unit_id,
        inputs={name: sha(data) for name, data in raw.items()},
        artifacts={name: sha(data) for name, data in artifacts.items()}, observation=observation,
        private_identity_verified=True, storage_verified=True, usb_binding_verified=True,
        sage_binary_sha256=args.sage_binary_sha256, current_config_validated=True,
        commissioning_diff_explained=False, radio_handshake_verified=False,
        capture_verified=False, installed=False, activated=False, ready=False)
    require(preparer.encoded(receipt) == preparer.encoded(expected))
    require(preparer.inputs(args) == (raw, config, binding) and preparer.observe(config, binding) == observation)


class Installer:
    def __init__(self, expected, *, already_installed=False):
        self.fresh_b = fresh_b(expected)
        require(set(expected) == {'schema', 'unit_id', 'boot_id', 'prepared_dir', 'prepared_sha256',
            'artifact_sha256', 'preparer_dir', 'preparer_sha256', 'preparation_args', 'service_files'}
            | ({'containment'} if self.fresh_b else set()))
        require(expected['schema'] == 'sage.lantern.install-prepared-mesh.v1'
            and expected['unit_id'] in ('unit-a', 'unit-b') and set(expected['artifact_sha256']) == set(DESTINATIONS)
            and set(expected['preparation_args']) == ARGUMENTS)
        self.expected = expected
        self.preparer = load_preparer(expected)
        args = dict(expected['preparation_args'])
        for name in ('manifest', 'radio_inventory', 'current_sage_policy'):
            args[name] = Path(args[name])
        self.args = SimpleNamespace(**args)
        from sage_lantern.suspend_system import SuspendSystem
        self.system = SuspendSystem()
        self.deadline = time.monotonic() + 120
        require(not already_installed or self.fresh_b)
        self.installed = set(DESTINATIONS) if already_installed else set()
        root = Path(expected['prepared_dir'])
        directory(root, 0o700)
        require({entry.name for entry in root.iterdir()} == {'PREPARED.json', *DESTINATIONS})
        self.receipt_raw = read(root / 'PREPARED.json', 0o600)
        require(sha(self.receipt_raw) == expected['prepared_sha256'])
        self.artifacts = {name: read(root / name, mode) for name, (_, mode) in DESTINATIONS.items()}
        require(all(sha(data) == expected['artifact_sha256'][name] for name, data in self.artifacts.items()))
        self.current()

    def current(self):
        self.system._remaining(self.deadline)
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        if self.fresh_b:
            directory(B_GUARD, 0o700)
            expected_raw = json.dumps(self.expected, sort_keys=True).encode()
            require(read(B_GUARD / 'expected.json', 0o600) == expected_raw)
            complete = decode(read(B_GUARD / 'complete.json', 0o600))
            require(set(complete) == {'schema', 'expected_sha256', 'boot_id', 'storage_epoch'}
                    and complete['schema'] == 'sage.lantern.b-first-mesh-containment.v1'
                    and complete['expected_sha256'] == sha(expected_raw)
                    and complete['boot_id'] == self.expected['boot_id'])
            def guarded():
                self.system._remaining(self.deadline)
                b_services(self.system, self.expected, self.deadline, SERVICES + B_SERVICES,
                           loaded=True, mesh_installed='20-radio.conf' in self.installed)
            guarded()
            _, config, binding = self.preparer.inputs(self.args)
            observation = self.preparer.observe(config, binding)
            require(observation['storage_epoch'] == complete['storage_epoch'])
            b_empty_workflows(config, complete['storage_epoch'], guarded)
        else:
            directory(NATIVE_GUARD, 0o700)
        for path, digest in self.expected['service_files'].items():
            require(sha(read(path)) == digest)
        for service in (() if self.fresh_b else SERVICES):
            fields = ('LoadState', 'ActiveState', 'MainPID', 'Job', 'ControlGroup', 'Slice',
                      'KillMode', 'Restart', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')
            value = self.system._show(service, self.deadline, fields)
            canonical = '/system.slice/' + service
            allowed = set(self.expected['service_files'])
            if service == SERVICES[0] and '20-radio.conf' in self.installed:
                allowed.add(str(DESTINATIONS['20-radio.conf'][0]))
            require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'inactive'
                and value['MainPID'] == '0' and value['Job'] in ('', '0') and value['ControlGroup'] in ('', canonical)
                and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group' and value['Restart'] == 'no'
                and value['FragmentPath'] in self.expected['service_files']
                and set(value['DropInPaths'].split()) <= allowed)
            require(value['NeedDaemonReload'] == 'no' or (service == SERVICES[0]
                    and '20-radio.conf' in self.installed and value['NeedDaemonReload'] == 'yes'))
            self.system._guard(service, self.deadline, str(NATIVE_GUARD))
            self.system._empty_tree(self.system._cgroup(canonical), self.deadline)
            require(self.system._show(service, self.deadline, fields) == value)
        self.system._sleep_idle(self.deadline)
        for name, (path, mode) in DESTINATIONS.items():
            directory(path.parent)
            if name in self.installed:
                require(read(path, mode) == self.artifacts[name])
            else:
                require(not os.path.lexists(path))
        root = Path(self.expected['prepared_dir'])
        require(read(root / 'PREPARED.json', 0o600) == self.receipt_raw)
        require(all(read(root / name, mode) == self.artifacts[name] for name, (_, mode) in DESTINATIONS.items()))
        validate_prepared(self.preparer, self.args, decode(self.receipt_raw), self.artifacts,
                          self.expected['unit_id'], self.expected['boot_id'])
        self.system._remaining(self.deadline)

    def apply(self, record):
        for name, (path, mode) in DESTINATIONS.items():
            self.current()
            record(name + '_intent')
            write_new(path, self.artifacts[name], mode)
            self.installed.add(name)
            self.current()
            record(name + '_verified')
        return dict(installed=True, activated=False, ready=False, services_started=False,
                    radio_handshake_verified=False, capture_verified=False, daemon_reload_required=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--establish-fresh-b-containment', action='store_true')
    parser.add_argument('--release-fresh-b-containment', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    def expired(signum, frame):
        raise TimeoutError('mesh_install_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(150)
    try:
        require(not (args.establish_fresh_b_containment and args.release_fresh_b_containment))
        require(args.release_fresh_b_containment or not os.path.lexists(STATE))
        raw = read(args.expected)
        require(sha(raw) == args.expected_sha256)
        if args.establish_fresh_b_containment:
            try:
                result = establish_b(decode(raw), apply=args.apply)
            except BaseException:
                if args.apply and os.path.lexists(B_GUARD):
                    from sage_lantern.suspend_system import SuspendSystem
                    try:
                        system = SuspendSystem()
                        system._command(['daemon-reload'], time.monotonic() + 10)
                    except BaseException:
                        pass
                    print('Fresh B containment incomplete/unverified; retain all guards and evidence. No install/start authorized.', file=sys.stderr)
                raise
            print(json.dumps(result, sort_keys=True))
            return
        if args.release_fresh_b_containment:
            try:
                result = release_b(decode(raw), apply=args.apply)
            except BaseException:
                if args.apply:
                    print('Fresh B release incomplete; guard coverage unverified. Preserve release intent and require operator reconciliation; no starts authorized.', file=sys.stderr)
                raise
            print(json.dumps(result, sort_keys=True))
            return
        installer = Installer(decode(raw))
        if not args.apply:
            print(json.dumps(dict(preflight_verified=True, installed=False, activated=False)))
            return
        directory(STATE.parent)
        STATE.mkdir(mode=0o700)
        sync(STATE.parent)
        write_new(STATE / 'expected.json', raw, 0o600)
        write_new(STATE / 'PREPARED.json', installer.receipt_raw, 0o600)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            write_new(STATE / f'{counter:02d}.json', json.dumps(dict(phase=phase,
                expected_sha256=args.expected_sha256, prepared_sha256=installer.expected['prepared_sha256'],
                artifacts=installer.expected['artifact_sha256'])).encode(), 0o600)
        result = installer.apply(record)
        if installer.fresh_b:
            write_new(STATE / 'complete.json', json.dumps(dict(schema='sage.lantern.b-mesh-installed.v1',
                expected_sha256=sha(json.dumps(installer.expected, sort_keys=True).encode())), sort_keys=True).encode(), 0o600)
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Mesh installation refused or incomplete; retain journal/guards/partial files, no automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
