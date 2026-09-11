#!/usr/bin/env python3
"""Explicit stale-lifetime containment, never native restart or rebootstrap.

Requires the original root-pinned inspect-stale-audio-epoch expected input and
helper SHA. Default runs that read-only inspector. --apply creates an exclusive
retained root0700 journal, adds two inhibition/preservation drop-ins, reloads,
stops only broker/voice, and performs signed same-actor GET scans. Reconciliation
accepts exactly one validated conversation_control row and nothing else.
Histories and audio commissioning are never moved or edited. Stale lifetime
records are copied to a private archive after reconciliation; originals remain.
No retry/resume, baseline reset, reset-failed, native stop, drain fabrication,
workflow PUT, radio opening, or removal of evidence. Failed partial mutations
retain evidence and require operator review. SIGKILL is not a durable lease.

Complete receipt: sage.lantern.stale-audio-recovery.v1, in STATE/complete.json.
Consumer MUST independently recheck containment, storage epoch and native pins,
and separately establish current-auth drain and native signer/snapshot gates.
The receipt never certifies historical completion or outstanding LLM inference.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import shlex
import stat
import subprocess
import sys
import time


STATE = Path('/var/lib/sage-lantern-stale-audio-recovery')
BROKER = 'sage-lantern-mesh-runtime.service'
VOICE = 'sage-lantern-voice-loop.service'
SAGE = 'sage-lantern-personal-sage.service'
PRODUCERS = (BROKER, VOICE)
LIFETIMES = ('/var/lib/sage-lantern-mesh-supervisor', '/run/sage-lantern-voice')
COMMISSION = ('/etc/sage-lantern/audio-capture.json', '/etc/sage-lantern/audio-commissioned.json',
              '/var/lib/sage-lantern/LOCAL_VOICE_READY')
GUARDS = {service: Path('/etc/systemd/system') / (service + '.d') / 'zzzz-stale-epoch-containment.conf'
          for service in PRODUCERS}


def require(value):
    if not value:
        raise ValueError('stale_audio_recovery_refused')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def guard_bytes(service):
    require(service in PRODUCERS)
    timeout = 75 if service == BROKER else 30
    return ('[Unit]\nConditionPathExists=!' + str(STATE) + '\n[Service]\nRestart=no\n'
            'RuntimeDirectoryPreserve=yes\nTimeoutStopSec=' + str(timeout) + '\n').encode()


def write_new(path, raw, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def control_inventory(rows, chain_id, agent_id):
    from sage_lantern import conversation_records as records
    require(type(rows) is list and len(rows) == 1)
    row = rows[0]
    actor = dict(chain_id=chain_id, agent_id=agent_id)
    require(type(row) is dict and set(row) == {'schema', 'agent_id', 'record_id', 'revision', 'kind', 'payload', 'trust'}
            and row['schema'] == 'sage.workflow-journal.v1' and row['agent_id'] == agent_id
            and row['kind'] == 'conversation_control' and row['trust'] == 'untrusted_auxiliary'
            and type(row['revision']) is int and 1 <= row['revision'] <= 2**53 - 1
            and row['record_id'] == records.record_id(actor))
    records.validate_control(row['payload'], actor)
    return dict(schema='sage.lantern.control-only-reconciliation.v1', complete=True,
        inventory_sha256=sha(canonical(rows)), record_id=row['record_id'], revision=row['revision'],
        counts=dict(conversation_control=1, conversation_session=0, mesh_outbound=0,
                    mesh_inbound=0, public_proposal=0), unsupported_count=0,
        workflow_referenced_pending_media=0, private_media_inventory_verified=False)


class Backend:
    def __init__(self, helper, expected, expected_raw, helper_sha256):
        self.helper, self.expected = helper, expected
        self.expected_raw, self.helper_sha256 = expected_raw, helper_sha256
        self.native = helper.Native(300)
        self.deadline = self.native.deadline
        self.sequence = 0
        self.process_fences = {}

    def remaining(self):
        require(time.monotonic() < self.deadline)
        return self.deadline - time.monotonic()

    def journal(self, phase, value):
        self.sequence += 1
        write_new(STATE / (f'{self.sequence:02d}-' + phase + '.json'), canonical(value))

    def command(self, arguments):
        allowed = arguments == ['daemon-reload'] or arguments in [['stop', '--no-block', service] for service in PRODUCERS]
        require(allowed)
        subprocess.run(['/usr/bin/systemctl', *arguments], stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
            timeout=min(5, self.remaining()), env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})

    def properties(self, service, fields):
        command = ['/usr/bin/systemctl', 'show', '--no-pager']
        for field in fields:
            command += ['-p', field]
        result = subprocess.run(command + [service], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True,
            timeout=min(5, self.remaining()), env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        require(len(result.stdout) <= 16384)
        rows = result.stdout.decode().splitlines()
        require(all('=' in row for row in rows))
        values = dict(row.split('=', 1) for row in rows)
        require(len(rows) == len(values) and set(values) == set(fields))
        return values

    def unchanged(self):
        self.native.files(self.expected)
        for service in self.helper.SERVICES:
            if service not in PRODUCERS:
                self.native.service(service, self.expected['services'][service])
                process = Path('/proc') / str(self.expected['services'][service]['pid'])
                fence = self.helper.file_identity((process / 'exe').stat())
                require(service not in self.process_fences or self.process_fences[service] == fence)
                self.process_fences[service] = fence

    def live_pins(self):
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        for service in self.helper.SERVICES:
            if service in PRODUCERS:
                continue
            pin = self.expected['services'][service]
            fields = ('LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'Job', 'ControlGroup')
            value = self.properties(service, fields)
            group = '/system.slice/' + service
            require(value == dict(LoadState='loaded', ActiveState='active', MainPID=str(pin['pid']),
                InvocationID=pin['invocation'], Job=value['Job'], ControlGroup=group) and value['Job'] in ('', '0'))
            process = Path('/proc') / str(pin['pid'])
            require(process.stat().st_uid == pin['uid']
                    and (process / 'stat').read_text().rsplit(')', 1)[1].split()[19] == pin['start_ticks']
                    and (process / 'cmdline').read_bytes() == b'\x00'.join(part.encode() for part in pin['argv']) + b'\x00'
                    and (process / 'cgroup').read_text().splitlines() == ['0::' + group]
                    and self.helper.file_identity((process / 'exe').stat()) == self.process_fences[service])

    def condition(self, service, *, inhibited):
        encoded = ''.join(character if character.isascii() and character.isalnum()
                          else f'_{ord(character):02x}' for character in service)
        command = ['/usr/bin/busctl', '--system', '--no-pager', 'get-property', 'org.freedesktop.systemd1',
            '/org/freedesktop/systemd1/unit/' + encoded, 'org.freedesktop.systemd1.Unit', 'Conditions']
        result = subprocess.run(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, check=True, timeout=min(5, self.remaining()),
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        require(len(result.stdout) <= 16384)
        values = shlex.split(result.stdout.decode())
        require(len(values) >= 2 and values[0] == 'a(sbbsi)' and values[1].isdigit()
                and int(values[1]) <= 128 and len(values) == 2 + int(values[1]) * 5)
        found = False
        for offset in range(2, len(values), 5):
            kind, trigger, negate, parameter, outcome = values[offset:offset + 5]
            require(trigger in ('true', 'false') and negate in ('true', 'false')
                    and outcome.lstrip('-').isdigit() and -(2**31) <= int(outcome) < 2**31)
            if parameter == str(STATE):
                require(kind == 'ConditionPathExists' and trigger == 'false' and negate == 'true')
                found = True
        require(found is inhibited)

    def guards(self):
        for service, path in GUARDS.items():
            require(self.helper.public_bytes(str(path)) == guard_bytes(service))
            fields = ('DropInPaths', 'NeedDaemonReload', 'RuntimeDirectoryPreserve', 'Restart', 'KillMode', 'FragmentPath')
            value = self.properties(service, fields)
            require(set(value['DropInPaths'].split()) == set(self.expected['services'][service]['dropins']) | {str(path)}
                    and value['NeedDaemonReload'] == 'no' and value['RuntimeDirectoryPreserve'] == 'yes'
                    and value['Restart'] == 'no' and value['KillMode'] == 'control-group'
                    and value['FragmentPath'] == self.helper.SYSTEMD + service)
            self.condition(service, inhibited=True)

    def empty(self, service):
        fields = ('LoadState', 'ActiveState', 'MainPID', 'Job', 'ControlGroup', 'Slice', 'KillMode')
        before = self.properties(service, fields)
        group = '/system.slice/' + service
        require(before['LoadState'] == 'loaded' and before['ActiveState'] in ('inactive', 'failed')
                and before['MainPID'] == '0' and before['Job'] in ('', '0')
                and before['ControlGroup'] in ('', group) and before['Slice'] == 'system.slice'
                and before['KillMode'] == 'control-group')
        from sage_lantern.suspend_system import SuspendSystem
        system = SuspendSystem()
        system._empty_tree(system._cgroup(group), self.deadline)
        require(not Path('/proc', str(self.expected['services'][service]['pid'])).exists())
        require(self.properties(service, fields) == before)
        return dict(active_state=before['ActiveState'], main_pid=0, job_absent=True,
                    canonical_cgroup=group, recursive_cgroup_empty=True, prior_process_absent=True)

    def contained(self):
        self.live_pins()
        self.guards()
        result = {service: self.empty(service) for service in PRODUCERS}
        self.remaining()
        return result

    def capture_file(self, path, owner):
        path = Path(path)
        for parent in path.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner) and not info.st_mode & 0o022)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_uid in (0, owner) and before.st_nlink == 1
                    and not before.st_mode & 0o022 and before.st_size <= 2097152)
            raw = os.read(descriptor, 2097153)
            require(len(raw) == before.st_size and self.helper.file_identity(before) == self.helper.file_identity(os.fstat(descriptor))
                    and self.helper.file_identity(before) == self.helper.file_identity(path.lstat()))
            return raw
        finally:
            os.close(descriptor)

    def commission_bytes(self, name):
        require(name in COMMISSION)
        if name != '/var/lib/sage-lantern/LOCAL_VOICE_READY':
            return self.helper.public_bytes(name)
        path = Path(name)
        owner = self.expected['services'][VOICE]['uid']
        require(type(owner) is int and owner > 0)
        parents = {}
        for parent in path.parents:
            info = parent.lstat()
            allowed = (0, owner) if str(parent) == '/var/lib/sage-lantern' else (0,)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in allowed and not info.st_mode & 0o022)
            parents[parent] = self.helper.file_identity(info)
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                    and not before.st_mode & 0o022 and 0 < before.st_size <= 16384)
            raw = os.read(descriptor, 16385)
            require(len(raw) == before.st_size and self.helper.file_identity(before) == self.helper.file_identity(os.fstat(descriptor))
                    and self.helper.file_identity(before) == self.helper.file_identity(path.lstat()))
            require(all(self.helper.file_identity(parent.lstat()) == identity for parent, identity in parents.items()))
            return raw
        finally:
            os.close(descriptor)

    def archive(self):
        paths = [Path(self.expected['bootstrap_path']), Path('/run/sage-lantern-voice-bootstrap.json')]
        for directory in LIFETIMES:
            root = Path(directory)
            if not root.exists():
                continue
            pending = [root]
            count = 0
            while pending:
                parent = pending.pop()
                require(stat.S_ISDIR(parent.lstat().st_mode))
                for child in parent.iterdir():
                    count += 1
                    require(count <= 256 and not child.is_symlink())
                    if child.is_dir():
                        pending.append(child)
                    else:
                        paths.append(child)
        destination = STATE / 'archive'
        destination.mkdir(mode=0o700)
        archived = {}
        for index, path in enumerate(sorted(set(paths))):
            if not os.path.lexists(path):
                continue
            owner = self.expected['services'][VOICE]['uid'] if str(path).startswith('/run/sage-lantern-voice/') else 0
            raw = self.capture_file(path, owner)
            name = f'{index:03d}.bin'
            write_new(destination / name, raw)
            require(self.capture_file(path, owner) == raw)
            archived[str(path)] = dict(sha256=sha(raw), archive_path=str(destination / name))
        return archived

    def reconcile(self):
        from sage_lantern.mesh_daemon import load_config, load_identity
        from sage_lantern.message_contacts import RecipientID
        from sage_lantern.message_storage import MessageStorageProbe
        from sage_lantern.workflow_store import WorkflowStore
        from sage_sdk import SageClient
        self.unchanged()
        self.contained()
        config = load_config()
        require(config['unit_id'] == self.expected['unit_id'] and config['chain_id'] == self.expected['chain_id']
                and config['agent_id'] == self.expected['agent_id'])
        identity, descriptor = load_identity(config)
        try:
            with SageClient(config['endpoint'], identity, timeout=min(5, self.remaining()), trust_env=False) as client:
                probe = MessageStorageProbe(client, actor=RecipientID(config['chain_id'], identity.agent_id), endpoint=config['endpoint'])
                store = WorkflowStore(client, storage=probe)
                def check():
                    self.contained()
                    return True
                before = probe.snapshot().epoch
                rows = store.scan(limit=50, max_records=4096, access_check=check)
                summary = control_inventory(rows, config['chain_id'], identity.agent_id)
                second = store.scan(limit=50, max_records=4096, access_check=check)
                require(rows == second and probe.snapshot().epoch == before)
                self.contained()
                self.unchanged()
                return before, dict(summary, stable_epoch=True, repeated_inventory_equal=True)
        finally:
            os.close(descriptor)


def preflight(backend):
    expected = backend.expected
    require(not os.path.lexists(STATE) and all(not os.path.lexists(path) for path in GUARDS.values()))
    observation = backend.helper.inspect(expected, backend.native)
    commissioning = {name: sha(backend.commission_bytes(name)) for name in COMMISSION}
    for path in (*GUARDS.values(), STATE):
        for parent in path.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    backend.unchanged()
    for service in PRODUCERS:
        backend.condition(service, inhibited=False)
    return observation, commissioning


def apply(backend):
    expected = backend.expected
    observation, commissioning = preflight(backend)
    require(backend.remaining() >= 200)
    STATE.mkdir(mode=0o700)
    try:
        backend.journal('intent', dict(expected=expected, expected_sha256=sha(backend.expected_raw),
            inspector_sha256=backend.helper_sha256, observation=observation, commissioning_sha256=commissioning))
        for service, path in GUARDS.items():
            backend.journal('guard-intent', dict(path=str(path), sha256=sha(guard_bytes(service))))
            write_new(path, guard_bytes(service), 0o644)
        backend.command(['daemon-reload'])
        backend.guards()
        backend.unchanged()
        for service in PRODUCERS:
            pin = dict(expected['services'][service])
            pin['dropins'] = pin['dropins'] + [str(GUARDS[service])]
            backend.native.service(service, pin)
        for service in PRODUCERS:
            backend.journal('stop-intent', dict(service=service))
            backend.command(['stop', '--no-block', service])
        stop_deadline = min(backend.deadline, time.monotonic() + 100)
        while True:
            backend.remaining()
            require(time.monotonic() < stop_deadline)
            try:
                quiescence = backend.contained()
                break
            except (ValueError, subprocess.CalledProcessError):
                time.sleep(min(0.2, backend.remaining()))
        backend.journal('quiescence', quiescence)
        storage_epoch, reconciliation = backend.reconcile()
        scan = dict(expected_sha256=sha(backend.expected_raw), storage_epoch=storage_epoch, **reconciliation)
        write_new(STATE / 'reconciliation.json', canonical(scan))
        archived = backend.archive()
        require(backend.contained() == quiescence)
        backend.unchanged()
        require(backend.native.snapshot(expected) == storage_epoch)
        require({name: sha(backend.commission_bytes(name)) for name in COMMISSION} == commissioning)
        receipt = dict(schema='sage.lantern.stale-audio-recovery.v1', unit_id=expected['unit_id'],
            boot_id=expected['boot_id'], chain_id=expected['chain_id'], agent_id=expected['agent_id'],
            expected_sha256=sha(backend.expected_raw), inspector_sha256=backend.helper_sha256,
            native=expected['services'][SAGE], producers=quiescence,
            guards={str(path): sha(guard_bytes(service)) for service, path in GUARDS.items()},
            preserved_records=archived, commissioning_sha256=commissioning, storage_epoch=storage_epoch,
            scan_sha256=sha(canonical(scan)),
            reconciliation=reconciliation, quiescence_verified=True, durable_reconciliation_verified=True,
            historical_work_disposition='unknown', drain_verified=False, native_update_authorized=False,
            rebootstrap_authorized=False, original_lifetime_records_retained=True,
            llm_inference_idle_verified=False, observed_monotonic_ns=time.monotonic_ns())
        write_new(STATE / 'complete.json', canonical(receipt))
        return receipt
    except BaseException as error:
        try:
            backend.journal('failed', dict(error_type=type(error).__name__, recovery_required=True,
                historical_work_disposition='unknown', native_update_authorized=False, rebootstrap_authorized=False))
        except Exception:
            pass
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspector', required=True)
    parser.add_argument('--inspector-sha256', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    from sage_lantern.deployment_bringup import read_public
    raw_helper = read_public(args.inspector)
    require(sha(raw_helper) == args.inspector_sha256)
    helper = importlib.util.module_from_spec(importlib.util.spec_from_loader('pinned_stale_inspector', loader=None))
    exec(compile(raw_helper, args.inspector, 'exec'), helper.__dict__)
    expected_raw = helper.public_bytes(args.expected)
    require(sha(expected_raw) == args.expected_sha256)
    expected = helper.decode(expected_raw)
    helper.validate(expected)
    backend = Backend(helper, expected, expected_raw, args.inspector_sha256)
    def cancelled(signum, frame):
        raise TimeoutError('stale_recovery_cancelled')
    handlers = {number: signal.signal(number, cancelled) for number in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM)}
    signal.setitimer(signal.ITIMER_REAL, 300)
    try:
        if args.apply:
            result = apply(backend)
        else:
            observation, commissioning = preflight(backend)
            result = dict(observation=observation, commissioning_sha256=commissioning,
                          recovery_preflight_verified=True, mutations_performed=False)
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        for number, handler in handlers.items():
            signal.signal(number, handler)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(verified=False, error_type=type(error).__name__, recovery_required=True)))
        raise SystemExit(1)
