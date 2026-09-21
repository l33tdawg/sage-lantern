#!/usr/bin/env python3
"""Explicit cross-boot audio recovery, not automatic enablement or drain proof.

Expected input pins current boot, supervisor tree, installed
runtime sources, configuration, commissioned receipt and signed workflow scan.
Old boot attribution is explicitly unverified. Default is read-only.
The guarded scan admits validated owner ledgers as retained historical evidence,
not settled operations. Only verified terminal public/outbox schemas are added.
Pending conversations/media/public/RF work and unsupported schema variants
require separate reviewed recovery. The unguarded inventory API still refuses
all additional kinds.
Retained interrupted-receive audits (sage.lantern.mesh-receive-audit.v1) are
admitted as retained_unknown, but only after proving each row is exactly the
record reviewed receive recovery writes for this slot.
No records are rewritten, replayed, deleted or declared historically drained.
An attempt has an exclusive retained journal. There is no retry or rollback.
Use the installed venv Python -I -B. Never run this from a boot restart loop.
"""

import argparse
import fcntl
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import uuid


SUPERVISOR = Path('/var/lib/sage-lantern-mesh-supervisor')
ATTEMPTS = Path('/var/lib/sage-lantern-cross-boot-audio')
BROKER = 'sage-lantern-mesh-runtime.service'
VOICE = 'sage-lantern-voice-loop.service'
MODEL = 'sage-lantern-llm.service'
SYSTEMD = Path('/etc/systemd/system')
# Retained interrupted-receive audits written by sage_lantern.mesh_receive_recovery.
# They are not settled work; the scan classifies them as retained_unknown only
# after proving they are byte-for-byte the record that reviewed recovery writes.
RECEIVE_AUDIT_SCHEMA = 'sage.lantern.mesh-receive-audit.v1'
RECEIVE_AUDIT_OUTCOME = 'interrupted_receive_abandoned'
RECEIVE_AUDIT_NAMESPACE = uuid.UUID('f6c1d3d2-0b26-4c58-9de1-2a7b0f4c5d10')
RECEIVE_AUDIT_FIELDS = {'schema', 'slot_id', 'operation_id', 'original_revision',
                        'original_sha256', 'recovered_at', 'reason', 'outcome',
                        'replayed', 'delivery_claim'}
RECEIVE_AUDIT_REASON_MAX = 512
# Outbound rows that never reached a confirmed peer outcome. They are retained
# evidence, not settled sends: the scan admits them as retained_unknown and never
# claims delivery. `cancelled` (owner decision 2026-09-21) belongs here because
# it is terminal for this unit's lifecycle and says nothing about the peer -
# the row may already have been delivered and a later receipt still promotes it.
# Rows with peer evidence (delivered/read) still go through full verification.
PENDING_OUTBOX_STATES = {'queued', 'dispatching', 'awaiting_receipt', 'cancelled'}
CLEANUP_SECONDS = 180
ABSENT = tuple(Path(name) for name in ('/run/sage-lantern-voice',
    '/run/sage-lantern-voice-bootstrap.json', '/run/sage-lantern-mesh-control/broker.sock',
    '/run/sage-lantern-mesh-control/voice-start-authorized.json'))


def require(value):
    if not value:
        raise ValueError('cross_boot_audio_refused')


def sleep_idle_batch(system, deadline):
    from sage_lantern.suspend_system import SLEEP_UNITS
    fields = ('Id', 'LoadState', 'ActiveState', 'Job')
    arguments = ['show', '--no-pager', '--all']
    for field in fields:
        arguments.extend(['-p', field])
    raw = system._command([*arguments, *SLEEP_UNITS], deadline)
    observed = {}
    for block in raw.strip().split('\n\n'):
        value = {}
        for line in block.splitlines():
            key, separator, member = line.partition('=')
            require(separator and key in fields and key not in value)
            value[key] = member
        require(set(value) == set(fields) and value['Id'] in SLEEP_UNITS
                and value['Id'] not in observed)
        observed[value['Id']] = value
    require(set(observed) == set(SLEEP_UNITS))
    for unit, value in observed.items():
        required = unit in ('sleep.target', 'suspend.target', 'systemd-suspend.service')
        require(value['LoadState'] in (('loaded',) if required else ('loaded', 'not-found'))
                and value['ActiveState'] == 'inactive' and system._no_job(value['Job']))


def failure_frames(error):
    result = []
    seen = set()
    while error is not None and id(error) not in seen and len(result) < 8:
        seen.add(id(error))
        frames = []
        trace = error.__traceback__
        while trace is not None:
            code = trace.tb_frame.f_code
            frames.append(dict(file=Path(code.co_filename).name, line=trace.tb_lineno,
                               function=code.co_name))
            trace = trace.tb_next
        result.append(dict(error_type=type(error).__name__, frames=frames[-16:]))
        error = error.__cause__ if error.__cause__ is not None else error.__context__
    return result


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(items):
    value = {}
    for key, member in items:
        require(key not in value)
        value[key] = member
    return value


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def parents(path):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def metadata(info):
    return {name: getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode',
        'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}


def public(path):
    path = Path(path)
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        require(len(raw) == before.st_size and metadata(before) == metadata(os.fstat(descriptor))
                == metadata(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'}
            and re.fullmatch('[0-9a-f]{64}', pin['sha256']) is not None)
    raw = public(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def recorder_binding():
    requested = Path('/usr/bin/arecord')
    parents(requested)
    named = requested.lstat()
    target = requested
    link = None
    if stat.S_ISLNK(named.st_mode):
        link = os.readlink(requested)
        require(named.st_uid == 0 and named.st_nlink == 1 and link in ('aplay', '/usr/bin/aplay'))
        target = Path('/usr/bin/aplay')
    parents(target)
    info = target.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink in (1, 2)
            and not info.st_mode & 0o022 and info.st_size <= 2 * 1024 * 1024)
    return dict(requested=metadata(named), link=link, target_path=str(target), target=metadata(info))


def file_hash(path):
    path = Path(path)
    recorder = recorder_binding() if path == Path('/usr/bin/arecord') else None
    if recorder is not None:
        path = Path(recorder['target_path'])
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink in ((1, 2) if recorder else (1,))
                and not before.st_mode & 0o022 and before.st_size <= 8 * 1024**3)
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, 1048576)
            if not chunk:
                break
            total += len(chunk)
            require(total <= before.st_size)
            digest.update(chunk)
        require(total == before.st_size and metadata(before) == metadata(os.fstat(descriptor)) == metadata(path.lstat()))
        require(recorder is None or recorder_binding() == recorder)
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def tree(root):
    parents(root)
    initial = root.lstat()
    require(stat.S_ISDIR(initial.st_mode) and initial.st_uid == 0 and stat.S_IMODE(initial.st_mode) == 0o700)
    result = {}
    pending = [root]
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(len(result) < 64 and info.st_dev == initial.st_dev and info.st_uid == 0
                and not path.is_mount())
        relative = str(path.relative_to(root))
        if stat.S_ISDIR(info.st_mode):
            require(relative in {'.', 'serial'} and stat.S_IMODE(info.st_mode) == 0o700)
            result[relative] = dict(metadata=metadata(info))
            pending.extend(path.iterdir())
        else:
            require(relative in {'owner.lock', 'fence.json'} or re.fullmatch(r'serial/mesh-[0-9]+(?:\.startup)?\.lock', relative))
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600)
            raw = public(path)
            require(relative == 'fence.json' or raw == b'')
            result[relative] = dict(metadata=metadata(info), sha256=sha(raw))
    require({'.', 'owner.lock', 'fence.json', 'serial'} <= set(result))
    return result


def settled_inventory(rows, actor, records, receiver_slot, validate_receiver, *,
                      owner_ledgers=None, validate_outbox=None):
    def validate_receive_audit(payload, record_id):
        """Prove the row is exactly what reviewed receive recovery writes."""
        require(type(payload) is dict and set(payload) == RECEIVE_AUDIT_FIELDS)
        require(payload['schema'] == RECEIVE_AUDIT_SCHEMA and payload['slot_id'] == receiver_slot)
        operation = payload['operation_id']
        require(type(operation) is str and len(operation) == 36
                and str(uuid.UUID(operation)) == operation and uuid.UUID(operation).int != 0)
        require(record_id == str(uuid.uuid5(RECEIVE_AUDIT_NAMESPACE,
            'receive-recovery:' + receiver_slot + ':' + operation)))
        require(payload['outcome'] == RECEIVE_AUDIT_OUTCOME
                and payload['replayed'] is False and payload['delivery_claim'] is False)
        require(type(payload['original_revision']) is int and payload['original_revision'] >= 1)
        require(type(payload['original_sha256']) is str
                and re.fullmatch('[0-9a-f]{64}', payload['original_sha256']) is not None)
        require(type(payload['recovered_at']) is int and payload['recovered_at'] >= 0)
        require(type(payload['reason']) is str and payload['reason'].strip()
                and '\x00' not in payload['reason']
                and len(payload['reason'].encode('utf-8')) <= RECEIVE_AUDIT_REASON_MAX)
        return 'retained_unknown'

    require(type(rows) is list and 0 < len(rows) <= 4096)
    controls = 0
    seen = set()
    for row in rows:
        require(type(row) is dict and set(row) == {
            'schema', 'agent_id', 'record_id', 'revision', 'kind', 'payload', 'trust'})
        require(row['schema'] == 'sage.workflow-journal.v1' and row['agent_id'] == actor['agent_id']
                and row['trust'] == 'untrusted_auxiliary' and type(row['revision']) is int
                and 1 <= row['revision'] <= 2**53 - 1 and row['record_id'] not in seen)
        seen.add(row['record_id'])
        if row['kind'] == 'conversation_control':
            records.validate_control(row['payload'], actor)
            require(row['record_id'] == records.record_id(actor))
            controls += 1
        elif row['kind'] == 'mesh_inbound' and row['payload'].get('schema') == RECEIVE_AUDIT_SCHEMA:
            require(validate_receive_audit(row['payload'], row['record_id']) == 'retained_unknown')
        elif (row['kind'] == 'mesh_outbound' and validate_outbox is not None
                and type(row['payload'].get('body')) is dict
                and row['payload']['body'].get('state') in PENDING_OUTBOX_STATES):
            require(validate_outbox(row) == 'retained_unknown')
        elif row['kind'] == 'mesh_inbound' and (owner_ledgers is None
                or row['payload'].get('schema') == 'sage.lantern.mesh-receiver.v1'):
            require(row['record_id'] == receiver_slot)
            validate_receiver(row['payload'])
            require(row['payload']['state'] in {'idle', 'interrupted'})
        elif row['kind'] in {'correction_attempt', 'contact_issuance', 'public_proposal', 'mesh_outbound', 'mesh_inbound'} and owner_ledgers is not None:
            require(owner_ledgers.inspect(row) in {
                'retained_unknown', 'retained_abandoned', 'retained_issued', 'retained_terminal'})
        else:
            require(row['kind'] == 'conversation_session')
            value = records.validate_session(row['payload'], actor)
            require(row['record_id'] == records.record_id(actor, value['session_id']))
            require(value['media'] is None or value['media']['state'] == 'linked')
            require(all(operation['state'] in {'accepted', 'recall_ready', 'cancelled'}
                and operation['deprecation_status'] in (None, 'deprecated')
                for operation in value['operations'].values()))
        require(controls <= 1)
    require(controls == 1)
    return dict(sha256=sha(canonical(rows)), records=len(rows), historical_outcomes='unknown')


def inspect_retained_inventory(rows, actor, store, identity, contained, config):
    from sage_lantern import conversation_records
    from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection
    from sage_lantern.mesh_daemon import load_identity
    from sage_lantern.mesh_receiver import _validate as validate_receiver
    descriptors, identities = [], {}
    if any(role in config for role in ('public_mesh', 'public_gateway')):
        require(contained() is True)
    try:
        for role in ('public_mesh', 'public_gateway'):
            if role not in config:
                continue
            settings = config[role]
            key_config = {key: value for key, value in config.items() if key != role}
            key_config.update(identity_file=settings['identity_file'],
                agent_id=settings['author'] if role == 'public_mesh' else settings['role']['gateway_key'])
            signer, descriptor = load_identity(key_config)
            descriptors.append(descriptor)
            identities[role] = signer
        inspector = OwnerLedgerInspection(rows, actor, store, identity, containment=contained,
            producer_config=config, producer_identities=identities)

        def validate_outbox(row):
            """Prove a non-terminal outbound row is the reviewed outbox record."""
            from sage_lantern.mesh_envelope import Peer
            from sage_lantern.mesh_outbox import MeshOutbox
            outbox = MeshOutbox(store, identity,
                sender=Peer(actor['chain_id'], bytes.fromhex(actor['agent_id'])),
                guard=lambda action: False, verify_contact=lambda action: False)
            body = outbox._verified(row)
            require(body['state'] in PENDING_OUTBOX_STATES and body['receipts'] == [])
            return 'retained_unknown'

        result = settled_inventory(rows, actor, conversation_records, config['receiver_slot'],
            validate_receiver, owner_ledgers=inspector, validate_outbox=validate_outbox)
        require(inspector.finish())
        return result
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


class Recovery:
    def __init__(self, expected):
        require(set(expected) == {'schema', 'boot_id', 'tree', 'inventory',
            'runtime_sources', 'files', 'commission_sha256', 'storage_epoch', 'workflow_sha256', 'actor',
            'historical_outcomes', 'old_boot_attribution', 'model_argv'})
        require(expected['schema'] == 'sage.lantern.cross-boot-audio.v1')
        require(expected['historical_outcomes'] == 'unknown' and expected['old_boot_attribution'] == 'unverified')
        self.expected = expected
        self.boot = expected['boot_id']
        require(str(uuid.UUID(self.boot)) == self.boot)
        self.state = ATTEMPTS / self.boot
        self.archived = False
        self.guards = set()
        self.model_started = False
        self.model_identity = None
        self.locks = []
        self.verify_sources()
        self.file_pins = {}
        self.recorder_pin = None
        digests = {}
        for collection in (expected['runtime_sources'], expected['files'], expected['inventory']):
            require(type(collection) is dict)
            for path, digest in collection.items():
                require(path not in digests or digests[path] == digest)
                digests[path] = digest
        for path, digest in digests.items():
            require(file_hash(path) == digest)
            self.file_pins[path] = metadata(Path(path).lstat())
        from sage_lantern import audio_readiness
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        self.audio = audio_readiness
        receipt = self.audio.commissioned_receipt()
        require(sha(canonical(receipt)) == expected['commission_sha256'])
        for path, digest in receipt['binding']['inputs'].items():
            require(file_hash(path) == digest)
            observed = metadata(Path(path).lstat())
            require(path not in self.file_pins or self.file_pins[path] == observed)
            self.file_pins[path] = observed
            if path == '/usr/bin/arecord':
                self.recorder_pin = recorder_binding()
        self.owner = ColdBrokerBootstrap(expected['inventory'])
        self.system = self.owner.system
        self.system._sleep_idle = lambda deadline: sleep_idle_batch(self.system, deadline)
        require(type(expected['model_argv']) is list and expected['model_argv']
                and all(type(arg) is str for arg in expected['model_argv'])
                and expected['model_argv'][0] == '/opt/sage-lantern/llama.cpp/llama-server'
                and expected['model_argv'][0] in expected['files'])
        require(not os.path.lexists(self.state))
        self.current()
        require(self.audio.admission_verified(commissioning=True) is True)

    def verify_sources(self):
        site = Path(sys.prefix) / 'lib' / ('python' + str(sys.version_info.major) + '.' + str(sys.version_info.minor)) / 'site-packages'
        sources = {str(path) for package in ('sage_lantern', 'sage_sdk') for path in (site / package).rglob('*.py')}
        require(sources and sources == set(self.expected['runtime_sources']))
        for path, digest in self.expected['runtime_sources'].items():
            require(file_hash(path) == digest)

    def location(self):
        return self.state / 'supervisor' if self.archived else SUPERVISOR

    def guard_path(self, service):
        return SYSTEMD / (service + '.d') / ('zzzz-cross-boot-' + self.boot + '.conf')

    def guard(self):
        return ('[Unit]\nConditionPathExists=!' + str(self.state) + '\n[Service]\nRestart=no\n').encode()

    def command(self, arguments):
        self.system._command(arguments, self.system.clock() + 10)

    def current(self):
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.boot)
        for path, observed in self.file_pins.items():
            parents(Path(path))
            require(metadata(Path(path).lstat()) == observed)
        if getattr(self, 'recorder_pin', None) is not None:
            require(recorder_binding() == self.recorder_pin)
        require(sha(canonical(self.audio.commissioned_receipt())) == self.expected['commission_sha256'])
        require(all(not os.path.lexists(path) for path in ABSENT))
        require(self.system.verify_sleep_idle().confirmed is True)
        from sage_lantern.suspend_io import FENCE
        from sage_lantern.suspend_system import DEPLOYMENT_FENCE
        require(not os.path.lexists(FENCE) and not os.path.lexists(DEPLOYMENT_FENCE))
        for service in (BROKER, VOICE):
            self.owner.inactive(service)
            value = self.system._show(service, self.system.clock() + 5,
                ('LoadState', 'FragmentPath', 'UnitFileState', 'DropInPaths', 'NeedDaemonReload', 'Restart',
                 'ExecMainStartTimestampMonotonic', 'NRestarts'))
            expected = {path for path in self.expected['inventory'] if path.startswith(str(SYSTEMD / (service + '.d')) + '/')}
            if service in self.guards:
                expected.add(str(self.guard_path(service)))
                require(public(self.guard_path(service)) == self.guard())
                self.system._guard(service, self.system.clock() + 5, str(self.state))
            require(value['LoadState'] == 'loaded' and value['NeedDaemonReload'] == 'no'
                    and value['FragmentPath'] == str(SYSTEMD / service) and value['UnitFileState'] == 'disabled'
                    and value['Restart'] == 'no' and value['ExecMainStartTimestampMonotonic'] == '0'
                    and value['NRestarts'] == '0' and set(value['DropInPaths'].split()) == expected)
        if not self.model_started:
            self.owner.inactive(MODEL)
        else:
            require(self.model_ready() == self.model_identity)
        model = self.system._show(MODEL, self.system.clock() + 5,
            ('FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'LoadState'))
        require(model['LoadState'] == 'loaded' and model['NeedDaemonReload'] == 'no'
                and model['FragmentPath'] == str(SYSTEMD / MODEL) and model['FragmentPath'] in self.expected['files']
                and set(model['DropInPaths'].split()) == {path for path in self.expected['files']
                    if path.startswith(str(SYSTEMD / (MODEL + '.d')) + '/')})
        observed = tree(self.location())
        expected_tree = self.expected['tree']
        if self.archived:
            require(not os.path.lexists(SUPERVISOR))
            observed = {key: dict(value, metadata={field: member for field, member in value['metadata'].items()
                if key != '.' or field != 'st_ctime_ns'}) for key, value in observed.items()}
            expected_tree = {key: dict(value, metadata={field: member for field, member in value['metadata'].items()
                if key != '.' or field != 'st_ctime_ns'}) for key, value in expected_tree.items()}
        require(observed == expected_tree)
        fence = decode(public(self.location() / 'fence.json'))
        require(type(fence) is dict and set(fence) == {'schema', 'broker_id', 'phase'}
                and fence['schema'] == 'sage.lantern.storage-supervisor.v1'
                and re.fullmatch('[0-9a-f]{32}', fence['broker_id']) is not None
                and fence['phase'] in {'active', 'recovery_required', 'drained', 'vault_closed', 'stopped_verified', 'stopping'})

    def lock(self):
        self.current()
        for relative in sorted(self.expected['tree']):
            if relative.endswith('.lock'):
                descriptor = os.open(self.location() / relative, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                self.locks.append(descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                require(metadata(os.fstat(descriptor)) == self.expected['tree'][relative]['metadata'])
        self.current()

    def reconcile(self):
        from sage_lantern import conversation_records
        from sage_lantern.mesh_receiver import _validate as validate_receiver
        from sage_lantern.mesh_daemon import load_config, load_identity
        from sage_lantern.device_access import access_epoch
        from sage_lantern.message_contacts import RecipientID
        from sage_lantern.message_storage import MessageStorageProbe
        from sage_lantern.workflow_store import WorkflowStore
        from sage_sdk import SageClient
        self.current()
        epoch = access_epoch()
        require(epoch is not None)
        config = load_config()
        actor = self.expected['actor']
        require(actor == dict(chain_id=config['chain_id'], agent_id=config['agent_id']))
        identity, descriptor = load_identity(config)
        try:
            with SageClient(config['endpoint'], identity, timeout=5, trust_env=False) as client:
                probe = MessageStorageProbe(client, actor=RecipientID(**actor), endpoint=config['endpoint'])
                store = WorkflowStore(client, storage=probe)
                def guard():
                    self.current()
                    require(access_epoch() == epoch)
                    return True
                require(probe.snapshot().epoch == self.expected['storage_epoch'])
                rows = store.scan(limit=50, max_records=4096, access_check=guard)
                result = self.inspect_inventory(rows, store, identity, guard, config)
                require(result['sha256'] == self.expected['workflow_sha256'])
                require(store.scan(limit=50, max_records=4096, access_check=guard) == rows
                        and probe.snapshot().epoch == self.expected['storage_epoch'] and guard())
                return result
        finally:
            os.close(descriptor)

    def inspect_inventory(self, rows, store, identity, guard, config):
        from sage_lantern import conversation_records
        from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection
        from sage_lantern.mesh_receiver import _validate as validate_receiver
        def contained():
            require(self.locks and len(self.locks) == sum(
                path.endswith('.lock') for path in self.expected['tree']))
            names = sorted(path for path in self.expected['tree'] if path.endswith('.lock'))
            for descriptor, name in zip(self.locks, names):
                require(metadata(os.fstat(descriptor)) == self.expected['tree'][name]['metadata'])
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(guard() is True)
            return True
        return inspect_retained_inventory(rows, self.expected['actor'], store, identity, contained, config)

    def inhibit(self):
        for service in (BROKER, VOICE):
            write(self.guard_path(service), self.guard())
            self.guards.add(service)
        self.command(['daemon-reload'])

    def archive(self):
        require(self.guards == {BROKER, VOICE} and self.locks)
        self.current()
        require(SUPERVISOR.stat().st_dev == self.state.stat().st_dev)
        for relative in self.expected['tree']:
            path = SUPERVISOR / relative
            if path.is_dir():
                sync(path)
            else:
                descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        destination = self.state / 'supervisor'
        require(not os.path.lexists(destination))
        os.rename(SUPERVISOR, destination)
        self.archived = True
        sync(SUPERVISOR.parent)
        sync(self.state)

    def model(self):
        self.current()
        self.command(['start', '--no-block', MODEL])
        deadline = time.monotonic() + 90
        invocation = None
        pid = None
        while time.monotonic() < deadline:
            value = self.system._show(MODEL, self.system.clock() + 5, ('ActiveState', 'MainPID', 'InvocationID', 'Job'))
            if value['InvocationID']:
                require(invocation is None or invocation == value['InvocationID'])
                invocation = value['InvocationID']
            if value['MainPID'] != '0':
                require(pid is None or pid == value['MainPID'])
                pid = value['MainPID']
            require(value['ActiveState'] in {'activating', 'active', 'inactive'})
            if value['ActiveState'] == 'active' and value['MainPID'] != '0' and value['Job'] in ('', '0'):
                try:
                    identity = self.model_ready()
                except ConnectionRefusedError:
                    time.sleep(0.2)
                    continue
                if identity is not None:
                    require(identity['invocation'] == invocation)
                    self.model_identity = identity
                    self.model_started = True
                    return
            time.sleep(0.2)
        require(False)

    def model_ready(self):
        from sage_lantern.suspend_io import model_idle
        before = self.system._show(MODEL, self.system.clock() + 5, ('ActiveState', 'MainPID', 'InvocationID', 'Job'))
        require(before['ActiveState'] == 'active' and re.fullmatch('[1-9][0-9]*', before['MainPID'])
                and before['Job'] in ('', '0') and re.fullmatch('[0-9a-f]{32}', before['InvocationID']))
        process = Path('/proc') / before['MainPID']
        ticks = (process / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        executable = Path(self.expected['model_argv'][0]).stat()
        running = (process / 'exe').stat()
        if (executable.st_dev, executable.st_ino) != (running.st_dev, running.st_ino):
            return None
        require((process / 'cmdline').read_bytes().split(b'\0')[:-1] == [arg.encode() for arg in self.expected['model_argv']])
        def listener():
            rows = [line.split() for line in Path('/proc/net/tcp').read_text().splitlines()[1:]]
            matches = [row for row in rows if row[3] == '0A' and row[1].endswith(':1F91')]
            if not matches:
                raise ConnectionRefusedError()
            require(len(matches) == 1 and matches[0][1] == '0100007F:1F91')
            inode = matches[0][9]
            descriptors = list((process / 'fd').iterdir())
            require(len(descriptors) <= 4096)
            owned = False
            for path in descriptors:
                try:
                    owned |= os.readlink(path) == 'socket:[' + inode + ']'
                except FileNotFoundError:
                    pass
            require(owned)
            return inode
        inode = listener()
        def get(route):
            connection = http.client.HTTPConnection('127.0.0.1', 8081, timeout=2)
            try:
                connection.request('GET', route)
                response = connection.getresponse()
                raw = response.read(65537)
                if response.status == 503 and route == '/health':
                    return None
                require(response.status == 200 and len(raw) <= 65536)
                return decode(raw)
            finally:
                connection.close()
        health = get('/health')
        if health is None:
            return None
        require(type(health) is dict and health.get('status') == 'ok' and model_idle(get('/slots')))
        require(listener() == inode and (process / 'stat').read_text().rsplit(')', 1)[1].split()[19] == ticks
                and self.system._show(MODEL, self.system.clock() + 5, tuple(before)) == before)
        return dict(pid=int(before['MainPID']), start_ticks=ticks, invocation=before['InvocationID'], listener=inode)

    def release(self):
        require(self.guards == {BROKER, VOICE})
        for service in (BROKER, VOICE):
            self.current()
            path = self.guard_path(service)
            require(public(path) == self.guard())
            path.unlink()
            sync(path.parent)
            self.guards.remove(service)
            self.command(['daemon-reload'])
        require(self.audio.admission_verified() is True)
        self.owner.fresh()

    def retain(self):
        info = self.state.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        for service in (BROKER, VOICE):
            path = self.guard_path(service)
            if not os.path.lexists(path):
                write(path, self.guard())
            require(public(path) == self.guard())
            self.guards.add(service)
        self.command(['daemon-reload'])
        for service in (BROKER, VOICE):
            deadline = self.system.clock() + 5
            fields = ('LoadState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Restart')
            value = self.system._show(service, deadline, fields)
            paths = {path for path in self.expected['inventory']
                     if path.startswith(str(SYSTEMD / (service + '.d')) + '/')}
            paths.add(str(self.guard_path(service)))
            require(value['LoadState'] == 'loaded' and value['FragmentPath'] == str(SYSTEMD / service)
                    and value['NeedDaemonReload'] == 'no' and value['Restart'] == 'no'
                    and set(value['DropInPaths'].split()) == paths)
            self.system._guard(service, deadline, str(self.state))
            require(public(self.guard_path(service)) == self.guard()
                    and self.system._show(service, deadline, fields) == value)

    def completed(self):
        evidence = self.owner.active()
        document = decode(public(ABSENT[1]))
        require(document['phase'] == 'complete' and document['boot_id'] == self.boot
                and document['evidence'] == evidence and document['inventory'] == self.expected['inventory'])
        write(self.state / 'new-lifetime.json', canonical(document))
        require(decode(public(self.state / 'new-lifetime.json')) == document and self.owner.active() == evidence)
        return dict(boot_id=self.boot, broker=evidence, lifetime_sha256=sha(canonical(document)))

    def close(self):
        for descriptor in self.locks:
            os.close(descriptor)
        self.locks.clear()


def execute(backend, record):
    for name in ('inhibit', 'archive', 'model'):
        backend.reconcile()
        record(name + '_intent')
        getattr(backend, name)()
        backend.current()
        record(name + '_done')
    backend.reconcile()
    record('release_intent')
    backend.release()
    record('release_done')
    backend.reconcile()
    backend.owner.fresh()
    record('bootstrap_intent')
    backend.bootstrap_attempted = True
    result = backend.owner.start()
    record('bootstrap_done', dict(result, lifetime=backend.completed()))
    return dict(result, historical_outcomes='unknown', old_drain_verified=False, automatic_boot_enabled=False)


def transition(backend, record):
    try:
        return execute(backend, record)
    except BaseException as failure:
        signal.alarm(0)
        handlers = {signum: signal.signal(signum, signal.SIG_IGN) for signum in (signal.SIGINT, signal.SIGTERM)}
        def cleanup_expired(signum, frame):
            raise TimeoutError('cross_boot_cleanup_deadline')
        handlers[signal.SIGALRM] = signal.signal(signal.SIGALRM, cleanup_expired)
        deadline = time.monotonic() + CLEANUP_SECONDS
        signal.alarm(CLEANUP_SECONDS)
        outcome = dict(guards_retained=False, containment_verified=False,
                       producer_outcome='unverified', historical_outcomes='unknown')
        try:
            try:
                outcome['failure_frames'] = failure_frames(failure)
            except BaseException:
                pass
            try:
                if time.monotonic() < deadline:
                    record('retain_failure_guards_intent')
            except BaseException:
                pass
            try:
                if time.monotonic() < deadline:
                    backend.retain()
                    outcome['guards_retained'] = True
            except BaseException:
                pass
            if getattr(backend, 'bootstrap_attempted', False) is True:
                try:
                    if time.monotonic() < deadline:
                        outcome['containment_verified'] = backend.owner.contain() is True
                except BaseException:
                    pass
                outcome['producer_outcome'] = 'contained' if outcome['containment_verified'] else 'active_or_unknown'
            try:
                if time.monotonic() < deadline:
                    record('retain_failure_guards_done', outcome)
            except BaseException:
                pass
            print(json.dumps(dict(recovery_required=True, **outcome)), file=sys.stderr)
        finally:
            signal.alarm(0)
            for signum, handler in handlers.items():
                signal.signal(signum, handler)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and Path(sys.prefix) == Path('/opt/sage-lantern/venv'))
    def expired(signum, frame):
        raise TimeoutError('cross_boot_deadline')
    signal.signal(signal.SIGALRM, expired)
    def interrupted(signum, frame):
        raise InterruptedError('cross_boot_interrupted')
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    signal.alarm(300)
    raw = pinned(dict(path=args.expected, sha256=args.expected_sha256))
    backend = Recovery(decode(raw))
    try:
        backend.lock()
        summary = backend.reconcile()
        if not args.apply:
            print(json.dumps(dict(preflight_verified=True, mutation_authorized=False, reconciliation=summary)))
            return
        parents(ATTEMPTS)
        if not ATTEMPTS.exists():
            ATTEMPTS.mkdir(mode=0o700)
            sync(ATTEMPTS.parent)
        require(stat.S_ISDIR(ATTEMPTS.lstat().st_mode) and ATTEMPTS.lstat().st_uid == 0
                and stat.S_IMODE(ATTEMPTS.lstat().st_mode) == 0o700)
        backend.state.mkdir(mode=0o700)
        sync(ATTEMPTS)
        write(backend.state / 'expected.json', raw)
        sequence = 0
        def record(phase, details=None):
            nonlocal sequence
            sequence += 1
            write(backend.state / f'{sequence:02d}-{phase}.json', canonical(dict(phase=phase,
                expected_sha256=args.expected_sha256, historical_outcomes='unknown', details=details)))
        print(json.dumps(transition(backend, record)))
    finally:
        backend.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, recovery_required=True, automatic_retry=False)))
        raise SystemExit(2) from None
