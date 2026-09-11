#!/usr/bin/env python3
"""One-shot post-native, post-owner-unlock reactivation; no historical drain claim.

Expected schema sage.lantern.resident-voice-reactivation.v1 pins helper sources
(inspector,recovery,native,preflight,update), inspection, containment and native
completion journal {path,sha256}, current live_services (SAGE,AUTH,MODEL), files,
exited_services (BROKER,VOICE exact EXIT_FIELDS), bootstrap inventory and
post-unlock storage_epoch. Default is read-only.
Auth/model must already be restored. Only the two exact stale-containment guards
are removed; old records remain archived. No capture, consent edits or retries.
"""

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import time
import types
import uuid


STATE = Path('/var/lib/sage-lantern-resident-voice-reactivation')
SUPERVISOR = Path('/var/lib/sage-lantern-mesh-supervisor')
VOICE_RUN = Path('/run/sage-lantern-voice')
BOOTSTRAP = Path('/run/sage-lantern-voice-bootstrap.json')
VOICE = 'sage-lantern-voice-loop.service'
BROKER = 'sage-lantern-mesh-runtime.service'
SAGE = 'sage-lantern-personal-sage.service'
AUTH = 'sage-lantern-device-auth.service'
MODEL = 'sage-lantern-llm.service'
VERSION = 'lantern-local.20260907-fresh-v27-resident-vault'
BINARY_SHA = '11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c'
ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'HOME': '/nonexistent'}
EXIT_FIELDS = ('ActiveState', 'SubState', 'MainPID', 'ExecMainPID', 'InvocationID', 'Result',
               'ExecMainStartTimestampMonotonic', 'ExecMainExitTimestampMonotonic', 'ExecMainCode', 'ExecMainStatus', 'NRestarts')


def require(value):
    if not value:
        raise ValueError('resident_voice_reactivation_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def parents(path):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def trusted(path):
    path = Path(path)
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and info.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == info.st_size and all(getattr(info, field) == getattr(after, field)
                for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(set(pin) == {'path', 'sha256'} and type(pin['sha256']) is str and len(pin['sha256']) == 64)
    raw = trusted(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def load(pin, name):
    raw = pinned(pin)
    module = types.ModuleType(name)
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_EXCL | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def exited(value, before, reset):
    require(set(before) == set(EXIT_FIELDS) and before['ActiveState'] in ('failed', 'inactive')
            and before['MainPID'] == '0' and before['NRestarts'] == '0')
    alternatives = [before]
    if reset:
        retained = dict(before, ActiveState='inactive', SubState='dead', Result='success')
        alternatives = [retained, dict(retained, ExecMainPID='0', InvocationID='',
            ExecMainStartTimestampMonotonic='0', ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0')]
    require(value in alternatives)


def listener(pin, port):
    process = Path('/proc') / str(pin['pid'])
    def identity():
        require((process / 'stat').read_text().rsplit(')', 1)[1].split()[19] == pin['start_ticks']
                and process.stat().st_uid == pin['uid'])
    def socket_inode():
        raw = Path('/proc/net/tcp').read_bytes()
        require(len(raw) <= 1048576)
        rows = [line.split() for line in raw.decode().splitlines()[1:]]
        require(all(len(row) >= 10 for row in rows))
        matches = [row for row in rows if row[3] == '0A' and row[1].endswith(f':{port:04X}')]
        require(len(matches) == 1 and matches[0][1] == f'0100007F:{port:04X}')
        return matches[0][9]
    identity()
    inode = socket_inode()
    owned = False
    for index, descriptor in enumerate((process / 'fd').iterdir()):
        require(index < 4096)
        try:
            owned |= os.readlink(descriptor) == 'socket:[' + inode + ']'
        except FileNotFoundError:
            pass
    require(owned and socket_inode() == inode)
    identity()
    return inode


class Native:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'helpers', 'inspection', 'containment', 'native_completion',
                'live_services', 'exited_services', 'files', 'inventory', 'storage_epoch'}
                and expected['schema'] == 'sage.lantern.resident-voice-reactivation.v1'
                and set(expected['helpers']) == {'inspector', 'recovery', 'native', 'preflight', 'update'}
                and set(expected['live_services']) == {SAGE, AUTH, MODEL}
                and set(expected['exited_services']) == {BROKER, VOICE})
        self.expected = expected
        self.helpers = {name: load(pin, 'reactivation_' + name) for name, pin in expected['helpers'].items()}
        self.inspector, self.recovery = self.helpers['inspector'], self.helpers['recovery']
        self.old_raw = pinned(expected['inspection'])
        self.old = self.inspector.decode(self.old_raw)
        self.inspector.validate(self.old)
        self.receipt = self.inspector.decode(pinned(expected['containment']))
        self.completed = [self.inspector.decode(line) for line in pinned(expected['native_completion']).splitlines()]
        require(self.completed[0]['phase'] == 'intent'
                and self.completed[-1]['phase'] == 'native_verified_services_inhibited')
        self.pins = self.completed[0]['details']
        self.helpers['native'].validate_pins(self.pins)
        self.completion = self.completed[-1]['details']
        self.helpers['native'].validate_snapshot(self.completion, self.pins, 'complete')
        self.transition = self.pins['transition_id']
        require(self.pins['after']['version'] == VERSION and self.pins['after']['binary_sha256'] == BINARY_SHA
                and self.pins['containment_sha256'] == expected['containment']['sha256'])
        require(self.receipt['schema'] == 'sage.lantern.stale-audio-recovery.v1'
                and self.receipt['expected_sha256'] == expected['inspection']['sha256']
                and self.receipt['inspector_sha256'] == expected['helpers']['inspector']['sha256']
                and self.receipt['boot_id'] == self.old['boot_id'] == self.pins['boot_id']
                and self.receipt['chain_id'] == self.old['chain_id']
                and self.receipt['agent_id'] == self.old['agent_id']
                and self.receipt['quiescence_verified'] is True
                and self.receipt['durable_reconciliation_verified'] is True
                and self.receipt['historical_work_disposition'] == 'unknown'
                and self.receipt['drain_verified'] is False)
        self.backend = self.recovery.Backend(self.inspector, self.old, self.old_raw, expected['helpers']['inspector']['sha256'])
        self.observer = self.helpers['preflight'].Observation()
        self.observer.deadline = self.backend.deadline
        self.live = copy.deepcopy(self.old)
        self.live['services'].update(expected['live_services'])
        self.live['files'].update(expected['files'])
        native = expected['live_services'][SAGE]
        require(native['pid'] == self.pins['native']['pid'] and int(native['start_ticks']) == self.pins['native']['start_ticks']
                and native['invocation'] == self.pins['native']['invocation'] and native['image_sha256'] == BINARY_SHA)
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        self.owner = ColdBrokerBootstrap(expected['inventory'])
        self.destinations = {SUPERVISOR: STATE / 'supervisor',
            VOICE_RUN: Path('/run/sage-lantern-voice-resident-' + self.transition),
            BOOTSTRAP: Path('/run/sage-lantern-voice-bootstrap-resident-' + self.transition + '.json')}
        self.moved, self.released, self.reset = set(), set(), set()
        self.lock = None
        self.sequence = 0
        self.initial_trees = None

    def command(self, arguments):
        require(arguments == ['daemon-reload'] or arguments in [['reset-failed', service] for service in (BROKER, VOICE)])
        subprocess.run(['/usr/bin/systemctl', *arguments], check=True, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=min(5, self.backend.remaining()), env=ENV)

    def location(self, original):
        for root, destination in self.destinations.items():
            if root in self.moved and (original == root or root in original.parents):
                return destination / original.relative_to(root)
        return original

    def records(self):
        records = self.receipt['preserved_records']
        roots = (SUPERVISOR, VOICE_RUN)
        actual = {}
        for root in roots:
            directory = self.location(root)
            pending = [directory]
            while pending:
                parent = pending.pop()
                info = parent.lstat()
                require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022
                        and info.st_uid in (0, self.old['services'][VOICE]['uid'])
                        and info.st_dev == directory.lstat().st_dev)
                relative = str(root / parent.relative_to(directory))
                actual[relative] = [info.st_dev, info.st_ino, info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)]
                require(len(actual) <= 256)
                for child in parent.iterdir():
                    require(not child.is_symlink())
                    if child.is_dir():
                        pending.append(child)
                    else:
                        original = str(root / child.relative_to(directory))
                        require(original in records)
                        metadata = child.lstat()
                        require(stat.S_ISREG(metadata.st_mode) and metadata.st_dev == info.st_dev)
                        actual[original] = [metadata.st_dev, metadata.st_ino, metadata.st_uid,
                            metadata.st_gid, stat.S_IMODE(metadata.st_mode), metadata.st_size]
        for original, pin in records.items():
            path = Path(original)
            require(path in (Path(self.old['bootstrap_path']), BOOTSTRAP)
                    or any(root in path.parents for root in roots))
            owner = self.old['services'][VOICE]['uid'] if VOICE_RUN in path.parents else 0
            require(sha(self.backend.capture_file(self.location(path), owner)) == pin['sha256']
                    and sha(trusted(pin['archive_path'])) == pin['sha256'])
        require(str(BOOTSTRAP) in records and str(SUPERVISOR / 'owner.lock') in records)
        if self.initial_trees is None:
            self.initial_trees = actual
        require(actual == self.initial_trees)
        for root in self.moved:
            require(not os.path.lexists(root))
        if self.lock is not None:
            info = os.fstat(self.lock)
            named = self.location(SUPERVISOR / 'owner.lock').lstat()
            require((info.st_dev, info.st_ino) == (named.st_dev, named.st_ino))
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def current(self):
        self.backend.remaining()
        self.backend.native.files(self.live)
        for pin in self.expected['helpers'].values():
            pinned(pin)
        for key in ('inspection', 'containment', 'native_completion'):
            pinned(self.expected[key])
        require(self.receipt['guards'] == {str(path): sha(self.recovery.guard_bytes(service))
                for service, path in self.recovery.GUARDS.items()})
        for service in (BROKER, VOICE):
            self.backend.empty(service)
            exited(self.backend.properties(service, EXIT_FIELDS), self.expected['exited_services'][service], service in self.reset)
            guard = self.recovery.GUARDS[service]
            require(not os.path.lexists(guard) if service in self.released else trusted(guard) == self.recovery.guard_bytes(service))
            fields = ('DropInPaths', 'NeedDaemonReload', 'Restart', 'KillMode', 'FragmentPath')
            value = self.backend.properties(service, fields)
            paths = set(self.old['services'][service]['dropins']) | (set() if service in self.released else {str(guard)})
            require(set(value['DropInPaths'].split()) == paths and value['NeedDaemonReload'] == 'no'
                    and value['Restart'] == 'no' and value['KillMode'] == 'control-group'
                    and value['FragmentPath'] == self.inspector.SYSTEMD + service)
            self.backend.condition(service, inhibited=service not in self.released)
        self.records()
        for service, pin in self.expected['live_services'].items():
            self.backend.native.service(service, pin)
        native = self.expected['live_services'][SAGE]
        installed = Path('/opt/sage-lantern/bin/sage-gui').lstat()
        running = (Path('/proc') / str(native['pid']) / 'exe').stat()
        require(stat.S_ISREG(installed.st_mode) and installed.st_uid == 0 and installed.st_nlink == 1
                and not installed.st_mode & 0o022
                and (installed.st_dev, installed.st_ino) == (running.st_dev, running.st_ino))
        native_listener = self.helpers['update'].listener_owner(native['pid'], int(native['start_ticks']), self.old['boot_id'])
        health = self.observer.get(8080, '/v1/dashboard/health')
        require(health.get('version') == VERSION and health.get('boot_id') == self.completion['dashboard_boot']
                and health.get('encrypted') is True and health.get('vault_locked') is False)
        require(self.helpers['update'].listener_owner(native['pid'], int(native['start_ticks']), self.old['boot_id']) == native_listener)
        auth_listener = listener(self.expected['live_services'][AUTH], 3196)
        require(self.observer.get(3196, '/state') == {'locked': False}
                and listener(self.expected['live_services'][AUTH], 3196) == auth_listener)
        model_listener = listener(self.expected['live_services'][MODEL], 8081)
        require(self.observer.get(8081, '/health').get('status') == 'ok'
                and listener(self.expected['live_services'][MODEL], 8081) == model_listener)
        self.helpers['preflight'].governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            self.observer.get(26657, '/status'), self.old['chain_id'], self.observer.get(26657, '/abci_info'))
        self.observer.system._sleep_idle(self.observer.deadline)
        for filename, digest in self.receipt['commissioning_sha256'].items():
            require(sha(self.backend.commission_bytes(filename)) == digest)
        require(set(self.receipt['commissioning_sha256']) == set(self.recovery.COMMISSION))
        for filename, digest in self.expected['inventory'].items():
            require(sha(trusted(filename)) == digest)
        from sage_lantern.deployment_bringup import SOCKET, START
        require(not os.path.lexists(SOCKET) and not os.path.lexists(START))

    def reconcile(self):
        from sage_lantern.mesh_daemon import load_config, load_identity
        from sage_lantern.message_contacts import RecipientID
        from sage_lantern.message_storage import MessageStorageProbe
        from sage_lantern.workflow_store import WorkflowStore
        from sage_sdk import SageClient
        self.current()
        config = load_config()
        require((config['unit_id'], config['chain_id'], config['agent_id']) ==
                (self.old['unit_id'], self.old['chain_id'], self.old['agent_id']))
        identity, descriptor = load_identity(config)
        try:
            with SageClient(config['endpoint'], identity, timeout=5, trust_env=False) as client:
                probe = MessageStorageProbe(client, actor=RecipientID(config['chain_id'], identity.agent_id), endpoint=config['endpoint'])
                store = WorkflowStore(client, storage=probe)
                def check():
                    self.current()
                    return True
                require(probe.snapshot().epoch == self.expected['storage_epoch'])
                rows = store.scan(limit=50, max_records=4096, access_check=check)
                summary = self.recovery.control_inventory(rows, config['chain_id'], identity.agent_id)
                require(summary == {key: self.receipt['reconciliation'][key] for key in summary})
                require(store.scan(limit=50, max_records=4096, access_check=check) == rows
                        and probe.snapshot().epoch == self.expected['storage_epoch'])
                self.current()
                return summary
        finally:
            os.close(descriptor)

    def relocate(self, root):
        require(root in self.destinations and root not in self.moved)
        self.current()
        destination = self.destinations[root]
        parents(destination)
        require(not os.path.lexists(destination) and root.lstat().st_dev == destination.parent.stat().st_dev)
        for original in self.receipt['preserved_records']:
            path = Path(original)
            if path == root or root in path.parents:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        if root.is_dir():
            for path in sorted(root.rglob('*'), reverse=True):
                if path.is_dir():
                    sync(path)
            sync(root)
        self.current()
        os.rename(root, destination)
        sync(root.parent)
        sync(destination.parent)
        self.moved.add(root)

    def reset_service(self, service):
        require(self.moved == set(self.destinations) and service not in self.reset)
        self.current()
        self.command(['reset-failed', service])
        self.reset.add(service)
        self.owner.inactive(service)

    def release(self, service):
        require(self.reset == {BROKER, VOICE} and service not in self.released)
        self.reconcile()
        path = self.recovery.GUARDS[service]
        raw = trusted(path)
        require(sha(raw) == self.receipt['guards'][str(path)])
        write(STATE / (service + '.guard'), raw)
        self.current()
        path.unlink()
        sync(path.parent)
        self.released.add(service)
        self.command(['daemon-reload'])

    def close(self):
        if self.lock is not None:
            os.close(self.lock)
            self.lock = None


def transition(backend, record):
    for name, action in [*(('relocate_' + root.name, lambda root=root: backend.relocate(root))
            for root in (SUPERVISOR, VOICE_RUN, BOOTSTRAP)),
            *(('reset_' + service, lambda service=service: backend.reset_service(service)) for service in (BROKER, VOICE)),
            *(('release_' + service, lambda service=service: backend.release(service)) for service in (BROKER, VOICE))]:
        backend.current()
        record(name + '_intent')
        action()
        backend.current()
        record(name + '_done')
    backend.reconcile()
    backend.owner.fresh()
    record('new_lifetime_start_intent')
    result = backend.owner.start()
    record('new_lifetime_start_done', result)
    return dict(result, historical_work_disposition='unknown', old_drain_verified=False,
                recapture_performed=False, automatic_retry=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    raw = pinned({'path': args.expected, 'sha256': args.expected_sha256})
    backend = Native(json.loads(raw))
    try:
        backend.current()
        backend.lock = os.open(SUPERVISOR / 'owner.lock', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        fcntl.flock(backend.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backend.reconcile()
        require(all(not os.path.lexists(path) for path in backend.destinations.values()))
        if not args.apply:
            print(json.dumps(dict(checkpoint_verified=True, started=False, old_drain_verified=False)))
            return
        parents(STATE)
        STATE.mkdir(mode=0o700)
        sync(STATE.parent)
        write(STATE / 'expected.json', raw)
        write(STATE / 'containment.json', pinned(backend.expected['containment']))
        def record(phase, details=None):
            backend.sequence += 1
            write(STATE / f'{backend.sequence:02d}-{phase}.json', canonical(dict(phase=phase,
                expected_sha256=args.expected_sha256, details=details)))
        print(json.dumps(transition(backend, record), sort_keys=True))
    finally:
        backend.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, recovery_required=True, automatic_retry=False)))
        raise SystemExit(2) from None
