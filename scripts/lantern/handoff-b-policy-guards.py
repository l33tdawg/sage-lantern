#!/usr/bin/env python3
"""Transfer B policy guards to established mesh containment or verified startup.

No service starts, commissioning, key export, native replacement or retry.
Default inspection is read-only. See B_POLICY_GUARD_HANDOFF.md.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time
import types
import re


ROOT = Path('/var/lib/sage-lantern-b-policy-guard-handoff')
FAILED_MESH_SHA = '4d4af77a6ce3a7ff48864f6f9446d35799750b3ac06ff9c10b04406904734e2b'
PROJECTED_MESH_SHA = '5012e19dd0d0cac6fe8a2e3f7218b20a7c0d632142b4f77c58f242ae43c41533'
PROJECTED_RECEIPT_SHA = '059ad90b5a5f10b77363d36d6cc1e19382a197e0022a23aaed9ba3ac14890cde'
ORIGINAL_HANDOFF_SHA = '3ac5ae9b542620176190511ab733b9dbed4affe9a7e1548b640298732d193930'
FAILED_MESH_INVENTORY_SHA = '024b7e76f824907a3bd562ec92b3624e3d1c08e43538a8050cd1b6bd4618d5d2'
POLICY_SHA = '33a8963dbd7d1fdf9d0bfc11593f66d86034f2e8cf4b2fb854a2320b0280bb9e'
RECOVERY_SHA = 'a66eae8197e76fa4d7d8a06849e65a783c1564a299b406bcda1682b5d2c930ea'
RECOVERY_COORDINATOR_SHA = 'c04ad4651381f295a179e354f40bb1e3f28d663d3c7ebd16ff0b75631fa23567'
SUCCESS_ATTEMPT = 'e2e9dbc6-dcb9-4964-8ffc-3cb063705b64'
SUCCESS_COMPLETE_SHA = 'a088388d91419dad7de68a286985f92b0b5499ec10c41aa38e4e485f12fd15ca'
RECOVERY_POLICY_SHA = '5a894e97af9acce9a10a1c09190ae9acc9f4ff1580db41e95c0feada5a253223'
FAILED_JOURNAL_SHA = '1203b2da6d51bbdb0b6864566d39c73630778eba9c19031135e002c61a2fce97'
NATIVE_COMPLETE_SHA = '7eb3c9b47020c926bf42e5ab9a82d9fd51f56e2afcded7cd8f8dd16a3152007f'
HISTORICAL_AUTH = {
    'fdc7a87838fe42e1842e8bd2ddbfb202.json': '694568351d724a7e115ade92052837b1ae5800d05c54d7b8a5cb368c5a929162',
    '896e47024cfe4c929bee3be6cc6ad35a.json': 'edd4f1f7af0265759563b109b7292457e61923e4a4ac1b959cee17f9fed5d572',
    'fbe0ffe0af62451d9c682896ba32fe53.json': '896c4b0efa3f73e674b68bc144143f249ef51e17d2ec488c5b99b2069c872bbd',
    'b4894d28dabd49409f5462299d737b34.json': '3b44405f22afeb2d2220d81c4e728ec36195c5a2c8b4a2da88bbe3beabca0af0'}
PRIOR_RECOVERY = Path('/var/lib/lantern-b-release-recovery-8c53b540-695e-4661-9599-30272e4b0c94')
PRIOR_FILES = {
    'error.json': 'b24cd313be7e0acbb8061c8b89300e753e8afd4a9088c2de18511c794c4bc02c',
    'expected.json': '5062d3aa02ac2bf94bb310e7e98bc3b8a6256a0c8c8220014e05b0839e3101d7',
    'failed.json': '5c6b5912ec59c3a1151226aebe1e1821226fea1af238c388f70b2405ac174e8a',
    'guard-sage-lantern-device-auth.service': 'd21ce9c4a90c7f9da3a9b82db52b0b878a17b9a8cf2867920c479ac042427635',
    'guard-sage-lantern-fleet-admin.service': 'd21ce9c4a90c7f9da3a9b82db52b0b878a17b9a8cf2867920c479ac042427635',
    'journal.jsonl': '6b8a7435fb510ebd3d0188ac4441f7c5290db34b4a1c915fbfb92d08952650e0'}
DIAGNOSTIC_RECOVERY = Path('/var/lib/lantern-b-release-recovery-e30159af-a38c-47ad-80d2-ef19f9882453')
DIAGNOSTIC_FILES = dict(PRIOR_FILES, **{
    'error.json': 'a0c917f392ca10edd17b17bbdf3fb633f8b44eb07f4d9011e428eac422c09037',
    'expected.json': '247837480fe2dd6ac539c14f0b32788040ffd9d8c0464f5d05f916f6ade2b7aa'})
AUDIO_HANDOFF_SHA = '61d4b353797c3f56fa1405e99374ad84d71a2b6d68a107c281ad9973c670ab59'
STARTUP_SERVICE_SHA = 'ce82967f6738fe074ea984f236c14c59f27a38ddfd058e716d395b1714e65293'
STARTUP = Path('/etc/sage-lantern/startup.json')
STARTUP_SERVICE = '/etc/systemd/system/sage-lantern-startup.service'


def require(value):
    if value is not True:
        raise RuntimeError('b_policy_guard_handoff_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def encoded(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def parents(path):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def read(path):
    path = Path(path)
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 2097152)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
            'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, name) == getattr(after, name)
            for after in (os.fstat(descriptor), path.lstat()) for name in fields))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = read(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def load(pin):
    raw = pinned(pin)
    module = types.ModuleType('reviewed_b_guard_dependency')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw, mode=0o600):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def project_mesh(expected, guards):
    require(expected['unit_id'] == 'unit-b' and expected['containment'] == 'fresh-b-v1')
    result = decode(encoded(expected))
    for path, raw in guards.items():
        require(result['service_files'].pop(str(path), None) == sha(raw))
    require(set(expected['service_files']) - set(result['service_files']) == set(map(str, guards)))
    return result


def mesh_receipt(raw, expected):
    receipt = decode(raw)
    require(type(receipt) is dict and type(receipt.get('storage_epoch')) is str
        and 0 < len(receipt['storage_epoch']) <= 256)
    require(receipt == dict(schema='sage.lantern.b-first-mesh-containment.v1',
        expected_sha256=sha(encoded(expected)), boot_id=expected['boot_id'],
        storage_epoch=receipt['storage_epoch']))
    return receipt


def prior_recovery_failure():
    for directory, files in ((PRIOR_RECOVERY, PRIOR_FILES), (DIAGNOSTIC_RECOVERY, DIAGNOSTIC_FILES)):
        info = directory.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        require({path.name for path in directory.iterdir()} == set(files))
        for name, checksum in files.items():
            require(sha(read(directory / name)) == checksum)
        require({path.name for path in directory.iterdir()} == set(files))


def recovered_release(evidence, policy, module, policy_pin, *, current_auth=False):
    require(type(evidence) is dict and set(evidence) == {'schema', 'recovery', 'expected', 'complete'}
        and evidence['schema'] == 'sage.lantern.b-recovered-release-evidence.v1'
        and evidence['recovery']['sha256'] == RECOVERY_SHA
        and evidence['complete']['sha256'] == SUCCESS_COMPLETE_SHA)
    pinned(evidence['recovery'])
    prior_recovery_failure()
    raw = pinned(evidence['expected'])
    expected = decode(raw)
    require(type(expected) is dict and set(expected) == {'schema', 'attempt_id', 'self_sha256',
        'coordinator', 'policy_expected', 'journal_sha256', 'complete_sha256', 'auth_reports'}
        and expected['schema'] == 'sage.lantern.b-release-recovery.v1'
        and module.valid_boot(expected['attempt_id']) and expected['attempt_id'] == SUCCESS_ATTEMPT
        and expected['self_sha256'] == RECOVERY_SHA
        and expected['coordinator']['sha256'] == RECOVERY_COORDINATOR_SHA
        and expected['policy_expected'] == policy_pin and policy_pin['sha256'] == RECOVERY_POLICY_SHA
        and expected['journal_sha256'] == FAILED_JOURNAL_SHA
        and expected['complete_sha256'] == NATIVE_COMPLETE_SHA
        and expected['auth_reports'] == HISTORICAL_AUTH)
    pinned(expected['coordinator'])
    root = Path('/var/lib/lantern-b-release-recovery-' + expected['attempt_id'])
    require(root != PRIOR_RECOVERY and evidence['expected']['path'] == str(root / 'expected.json')
        and evidence['complete']['path'] == str(root / 'complete.json'))
    info = root.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    names = {'expected.json', 'complete.json', 'journal.jsonl', 'auth-new-locked.json',
        'guard-' + module.AUTH, 'guard-' + module.UI}
    require({path.name for path in root.iterdir()} == names)
    complete_raw = pinned(evidence['complete'])
    complete = decode(complete_raw)
    require(type(complete) is dict and set(complete) == {'schema', 'expected_sha256', 'boot_id', 'native_boot',
        'owner_unlock_required', 'audio_started', 'historical_cleanup', 'fresh_services', 'retained_guards'}
        and complete['schema'] == 'sage.lantern.b-release-recovery-complete.v1'
        and complete['expected_sha256'] == sha(raw) and complete['boot_id'] == policy.expected['boot_id']
        and module.valid_boot(complete['native_boot']) and complete['owner_unlock_required'] is True
        and complete['audio_started'] is False and complete['historical_cleanup'] == 'unclaimed'
        and complete['retained_guards'] == sorted({module.SAGE, *module.COLD})
        and type(complete['fresh_services']) is dict and set(complete['fresh_services']) == {module.AUTH, module.UI})
    for service, pin in complete['fresh_services'].items():
        require(type(pin) is dict and set(pin) == {'pid', 'start_ticks', 'invocation', 'image_sha256'}
            and type(pin['pid']) is int and pin['pid'] > 0 and type(pin['start_ticks']) is int and pin['start_ticks'] > 0
            and type(pin['invocation']) is str and re.fullmatch('[0-9a-f]{32}', pin['invocation']) is not None
            and pin['invocation'] != '0' * 32 and pin['invocation'] != policy.expected['services'][service]['invocation']
            and pin['image_sha256'] == policy.expected['services'][service]['image_sha256'])
        require(read(root / ('guard-' + service)) == policy.guard and not os.path.lexists(policy.guards[service]))
    require(complete['fresh_services'][module.AUTH]['pid'] != complete['fresh_services'][module.UI]['pid']
        and complete['fresh_services'][module.AUTH]['invocation'] != complete['fresh_services'][module.UI]['invocation'])
    journal = [decode(line) for line in read(root / 'journal.jsonl').splitlines()]
    require(journal == [{'phase': phase} for phase in ('intent', 'remove-' + module.AUTH,
        'remove-' + module.UI, 'start_auth', 'start_ui', 'complete')])
    original_raw = pinned(policy_pin)
    require(module.read(policy.state / 'expected.json') == original_raw
        and decode(original_raw) == policy.expected and not os.path.lexists(policy.state / 'released.json'))
    failed = module.read(policy.state / 'transition.jsonl')
    native_complete = module.read(policy.state / 'complete.json')
    require(sha(failed) == FAILED_JOURNAL_SHA and sha(native_complete) == NATIVE_COMPLETE_SHA
        and decode(failed.splitlines()[-1]) == {'phase': 'release_failed_processes_stopped_auth_cleanup_unknown'})
    original_complete = decode(native_complete)
    require(original_complete['boot_id'] == complete['boot_id'] and original_complete['native_boot'] == complete['native_boot'])
    auth_pin = complete['fresh_services'][module.AUTH]
    name = auth_pin['invocation'] + '.json'
    require(name not in HISTORICAL_AUTH)
    archived = read(root / 'auth-new-locked.json')
    module.require(decode(archived)['instance_id'] == auth_pin['invocation'])
    policy.preflight.report(decode(archived), auth_pin['invocation'], current=True)
    for filename, checksum in HISTORICAL_AUTH.items():
        require(sha(module.read(module.REPORTS / filename, policy.uid, 1024, 0o600)) == checksum)
    if current_auth:
        require({path.name for path in module.REPORTS.iterdir()} == set(HISTORICAL_AUTH) | {name}
            and module.read(module.REPORTS / name, policy.uid, 1024, 0o600) == archived
            and policy.capture_process(module.AUTH) == auth_pin)
    require(pinned(evidence['expected']) == raw and pinned(evidence['complete']) == complete_raw
        and module.read(policy.state / 'transition.jsonl') == failed
        and module.read(policy.state / 'complete.json') == native_complete)
    prior_recovery_failure()
    return complete


def recovered_policy_contained(evidence, policy, module, policy_pin):
    receipt = recovered_release(evidence, policy, module, policy_pin, current_auth=True)
    policy.files(changed=True)
    policy.mesh.b_no_prior_lifetime()
    policy.services((module.UI, module.IDLE), guarded=module.RETAINED, completed=True,
        fresh={module.AUTH: receipt['fresh_services'][module.AUTH]})
    policy.system._sleep_idle(time.monotonic() + 30)
    policy.mesh.b_other_producers()
    pin = policy.expected['services'][module.SAGE]
    policy.listener.listener_owner(pin['pid'], pin['start_ticks'], policy.expected['boot_id'])
    health = module.get(8080, '/v1/dashboard/health')
    module.validate_health(health, receipt['native_boot'], policy.expected['after']['version'], False)
    auth_state = module.get(3196, '/state')
    require(type(health.get('vault_locked')) is bool and type(auth_state) is dict
        and set(auth_state) == {'locked'} and type(auth_state['locked']) is bool
        and auth_state['locked'] is health['vault_locked'])
    policy.preflight.governance(module.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
        module.get(26657, '/status'), policy.policy.CHAIN, module.get(26657, '/abci_info'))
    require(module.get(26657, '/unconfirmed_txs?limit=1')['result']['n_txs'] == '0')
    require(recovered_release(evidence, policy, module, policy_pin, current_auth=True) == receipt)
    return receipt


def transfer(backend):
    require('partial_mesh' not in getattr(backend, 'expected', {}))
    backend.check()
    backend.claim()
    try:
        backend.check()
        for path, raw in backend.guards.items():
            backend.check()
            backend.record('remove_intent', str(path))
            backend.archive(path, raw)
            backend.remove(path, raw)
            backend.reload()
            backend.check()
            backend.record('removed', str(path))
        backend.publish()
        backend.check()
        backend.record('complete', None)
        return dict(transferred=True, phase=backend.phase, services_started=False,
            commissioning_performed=False, retry_authorized=False)
    except BaseException:
        contained = False
        try:
            contained = backend.failure() is True
        except BaseException:
            pass
        try:
            backend.record('failed_contained' if contained else 'failed_containment_unverified', None)
        except BaseException:
            pass
        raise


def failed_mesh_inventory(pin):
    document = decode(pinned(pin))
    require(sha(encoded(document)) == FAILED_MESH_INVENTORY_SHA)
    require(type(document) is dict and set(document) == {'schema', 'files'}
        and document['schema'] == 'sage.lantern.b-failed-mesh-inventory.v1'
        and type(document['files']) is dict and 8 <= len(document['files']) <= 32)
    directory = ROOT / 'mesh'
    require({path.name for path in directory.iterdir()} == set(document['files'])
        and 'complete.json' not in document['files'])
    contents = {}
    for name, checksum in document['files'].items():
        require(type(name) is str and Path(name).name == name and name not in ('.', '..'))
        contents[name] = read(directory / name)
        require(sha(contents[name]) == checksum)
    failures = [raw for name, raw in contents.items() if name.endswith('-failed_contained.json')]
    require(len(failures) == 1 and sha(failures[0]) == FAILED_MESH_SHA
        and decode(failures[0]) == dict(phase='failed_contained', path=None,
            expected_sha256=sha(contents['intent.json'])))
    require({path.name for path in directory.iterdir()} == set(contents))
    return contents


def reconcile_mesh(backend):
    require(backend.phase == 'mesh' and 'partial_mesh' in backend.expected)
    backend.claim()
    started = time.monotonic()
    try:
        backend.record('reconciliation_check_started', None)
        backend.check()
        backend.record('reconciliation_check_validated', {'elapsed_seconds': time.monotonic() - started})
        backend.record('complete', None)
        return dict(reconciled=True, original_failed=True, services_started=False, retry_authorized=False)
    except BaseException as error:
        backend.record('reconciliation_failed', {'exception': type(error).__name__,
            'elapsed_seconds': time.monotonic() - started, 'historical_cause': 'unproven'})
        raise


class Backend:
    def __init__(self, expected, raw, *, completed_sha256=None):
        common = {'schema', 'phase', 'coordinator', 'policy_expected', 'handoff_sha256'}
        if 'recovery_evidence' in expected:
            common.add('recovery_evidence')
        if 'partial_mesh' in expected:
            common.add('partial_mesh')
        if 'mesh_reconciliation' in expected:
            common.add('mesh_reconciliation')
        phase = expected.get('phase')
        require(phase in ('mesh', 'native'))
        extra = {'mesh_expected', 'mesh_receipt'} if phase == 'mesh' else {'audio_handoff', 'audio_tools', 'audio_plan_sha256',
            'startup_service', 'mesh_handoff_sha256'}
        require(set(expected) == common | extra and expected['schema'] == 'sage.lantern.b-policy-guard-handoff.v1'
            and expected['coordinator']['sha256'] == POLICY_SHA)
        require(sha(read(Path(__file__).absolute())) == expected['handoff_sha256'])
        self.expected, self.raw, self.phase = expected, raw, phase
        self.directory = ROOT / phase
        if 'partial_mesh' in expected:
            require(phase == 'mesh' and 'mesh_reconciliation' not in expected)
            self.directory = ROOT / 'mesh-publication-reconciliation'
        if 'mesh_reconciliation' in expected:
            require(phase == 'native')
        if completed_sha256 is None:
            require(not os.path.lexists(self.directory))
        else:
            require(phase == 'native' and read(self.directory / 'intent.json') == raw)
            complete = read(self.directory / 'complete.json')
            require(sha(complete) == completed_sha256 and decode(complete) ==
                dict(phase='complete', path=None, expected_sha256=sha(raw)))
        self.policy_module = load(expected['coordinator'])
        policy_expected_raw = pinned(expected['policy_expected'])
        self.policy = self.policy_module.Backend(decode(policy_expected_raw))
        self.policy_raw = policy_expected_raw
        self.mesh = self.policy.mesh
        self.removed = set()
        self.native_before = None
        require(self.policy_module.absent_idle_pin(self.policy_module.IDLE,
            self.policy.expected['services'][self.policy_module.IDLE]))
        self.guards = {self.policy.guards[name]: self.policy.guard for name in self.policy_module.COLD}
        if phase == 'mesh':
            preserved = failed_mesh_inventory(expected['partial_mesh']) if 'partial_mesh' in expected else None
            if preserved is not None:
                original = decode(preserved['intent.json'])
                require(original['handoff_sha256'] == ORIGINAL_HANDOFF_SHA
                    and original == {key: (ORIGINAL_HANDOFF_SHA if key == 'handoff_sha256' else value)
                        for key, value in expected.items() if key != 'partial_mesh'})
                self.mesh_raw = preserved['before-expected.json']
                require(sha(self.mesh_raw) == expected['mesh_expected']['sha256'])
            else:
                self.mesh_raw = pinned(expected['mesh_expected'])
            self.mesh_expected = decode(self.mesh_raw)
            require(self.mesh_expected['boot_id'] == self.policy.expected['boot_id'])
            self.next_mesh = project_mesh(self.mesh_expected, self.guards)
            require(expected['mesh_receipt']['path'] == str(self.mesh.B_GUARD / 'complete.json'))
            self.receipt_raw = preserved['before-complete.json'] if preserved is not None else pinned(expected['mesh_receipt'])
            require(sha(self.receipt_raw) == expected['mesh_receipt']['sha256'])
            receipt = mesh_receipt(self.receipt_raw, self.mesh_expected)
            self.storage_epoch = receipt['storage_epoch']
            self.next_receipt = dict(receipt, expected_sha256=sha(encoded(self.next_mesh)))
            self.metadata = {self.mesh.B_GUARD / 'expected.json': encoded(self.mesh_expected),
                self.mesh.B_GUARD / 'complete.json': self.receipt_raw}
            if preserved is not None:
                require(sha(encoded(self.next_mesh)) == PROJECTED_MESH_SHA
                    and sha(encoded(self.next_receipt)) == PROJECTED_RECEIPT_SHA)
                for path, raw in self.guards.items():
                    require(preserved[path.parent.name + '.guard'] == raw and not os.path.lexists(path))
                self.removed = set(self.guards)
                self.metadata = {self.mesh.B_GUARD / 'expected.json': encoded(self.next_mesh),
                    self.mesh.B_GUARD / 'complete.json': encoded(self.next_receipt)}
        else:
            self.guards = {self.policy.guards[self.policy_module.SAGE]: self.policy.guard_native}
            mesh_directory = ROOT / ('mesh-publication-reconciliation' if 'mesh_reconciliation' in expected else 'mesh')
            mesh_complete = read(mesh_directory / 'complete.json')
            mesh_intent = read(mesh_directory / 'intent.json')
            if 'mesh_reconciliation' in expected:
                require(expected['mesh_reconciliation'] == decode(mesh_intent)['partial_mesh'])
                failed_mesh_inventory(expected['mesh_reconciliation'])
            require(sha(mesh_complete) == expected['mesh_handoff_sha256']
                and decode(mesh_complete) == dict(phase='complete', path=None, expected_sha256=sha(mesh_intent)))
            prior = decode(mesh_intent)
            require(prior['phase'] == 'mesh' and prior['policy_expected'] == expected['policy_expected']
                and prior['coordinator'] == expected['coordinator']
                and prior.get('recovery_evidence') == expected.get('recovery_evidence')
                and expected['audio_handoff']['sha256'] == AUDIO_HANDOFF_SHA
                and expected['startup_service']['sha256'] == STARTUP_SERVICE_SHA)
            require(all(not os.path.lexists(self.policy.guards[name]) for name in self.policy_module.COLD))
            self.audio = load(expected['audio_handoff'])
            self.tools_raw = pinned(expected['audio_tools'])
            self.audio_owner = self.audio.Native(decode(self.tools_raw), self.tools_raw)
            if completed_sha256 is not None:
                self.removed = set(self.guards)

    def native(self, *, live_only=False):
        module, policy = self.policy_module, self.policy
        if not live_only:
            policy.files(changed=True)
            require(decode(read(policy.state / 'expected.json')) == policy.expected)
        if live_only:
            receipt = self.scan_receipt
        elif 'recovery_evidence' in self.expected:
            receipt = recovered_release(self.expected['recovery_evidence'], policy, module,
                self.expected['policy_expected'], current_auth=self.phase == 'mesh')
        else:
            require(decode(read(policy.state / 'transition.jsonl').splitlines()[-1]) ==
                {'phase': 'released_owner_unlock_required'})
            receipt = decode(read(policy.state / 'released.json'))
            require(receipt['boot_id'] == policy.expected['boot_id'] and receipt['locked'] is True
                and receipt['retained_guards'] == sorted({module.SAGE, *module.COLD}))
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == receipt['boot_id'])
        policy.absent_idle(policy.properties(module.IDLE))
        value = policy.properties(module.SAGE)
        pin = policy.expected['services'][module.SAGE]
        fragment = '/etc/systemd/system/' + module.SAGE
        originals = {path for path in policy.expected['files'] if path.startswith(fragment + '.d/')}
        guard = policy.guards[module.SAGE]
        removed = guard in self.removed
        require(value['LoadState'] == 'loaded' and value['Transient'] == 'no'
            and value['ActiveState'] == 'active' and value['MainPID'] == str(pin['pid'])
            and value['InvocationID'] == pin['invocation'] and value['Job'] in ('', '0')
            and value['NeedDaemonReload'] == 'no' and value['FragmentPath'] == fragment
            and value['ControlGroup'] == '/system.slice/' + module.SAGE
            and value['KillMode'] == 'control-group' and value['Slice'] == 'system.slice'
            and value['Restart'] == ('on-failure' if removed else 'no')
            and set(value['DropInPaths'].split()) == originals | (set() if removed else {str(guard)}))
        policy.listener.listener_owner(pin['pid'], pin['start_ticks'], policy.expected['boot_id'])
        executable = Path('/proc') / str(pin['pid']) / 'exe'
        info = executable.stat()
        identity = tuple(getattr(info, name) for name in
            ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns', 'st_uid', 'st_mode'))
        if live_only:
            require(identity == self.scan_native_image)
        else:
            with executable.open('rb') as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == policy.expected['after']['binary_sha256'])
            self.scan_native_image = identity
            self.scan_receipt = receipt
        health = module.get(8080, '/v1/dashboard/health')
        module.validate_health(health, receipt['native_boot'], policy.expected['after']['version'], False)
        if self.native_before is None:
            self.native_before = {key: value[key] for key in ('MainPID', 'InvocationID', 'ControlGroup')}
        require(all(value[key] == member for key, member in self.native_before.items()))
        if not removed:
            require(read(guard) == policy.guard_native)

    def containment(self, *, live_only=False):
        if live_only:
            self.native(live_only=True)
        else:
            self.native()
        if self.phase == 'native':
            self.audio_check()
            return
        system = self.policy.system
        deadline = time.monotonic() + 30
        self.mesh.directory(self.mesh.B_GUARD, 0o700)
        for service in self.mesh.SERVICES + self.mesh.B_SERVICES:
            require(read(self.mesh.b_guard_path(service)) == self.mesh.b_guard_bytes())
            system._guard(service, deadline, str(self.mesh.B_GUARD))
            value = self.policy.properties(service)
            require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                and value['Job'] in ('', '0') and value['Restart'] == 'no')
            system._empty_tree(system._cgroup('/system.slice/' + service), deadline)
        system._sleep_idle(deadline)

    def audio_check(self):
        plan, record = self.audio_owner.snapshot()
        require(sha(self.audio.canonical(plan)) == self.expected['audio_plan_sha256']
            and plan['boot_id'] == self.policy.expected['boot_id'])
        documents = self.audio.documents(plan, self.audio_owner.tools, record)
        directory = self.audio.HANDOFF / plan['boot_id']
        self.audio_owner.coordinator.validate_full_config(decode(documents[directory / 'startup.json']))
        complete = decode(read(directory / 'complete.json'))
        require(complete == dict(schema='sage.lantern.first-audio-handoff-result.v1', unit_id='unit-b',
            boot_id=plan['boot_id'], files={str(path): sha(raw) for path, raw in documents.items()},
            services_started=False, services_enabled=False, reboot_accepted=False))
        require(all(read(path) == raw for path, raw in documents.items())
            and read(STARTUP) == documents[directory / 'startup.json'])
        require(self.expected['startup_service']['path'] == STARTUP_SERVICE)
        pinned(self.expected['startup_service'])
        self.policy.system._empty_tree(self.policy.system._cgroup('/system.slice/sage-lantern-startup.service'),
            time.monotonic() + 5)
        value = self.policy.properties('sage-lantern-startup.service')
        require(value['LoadState'] == 'loaded' and value['Transient'] == 'no'
            and value['FragmentPath'] == STARTUP_SERVICE and value['DropInPaths'] == ''
            and value['NeedDaemonReload'] == 'no' and value['Restart'] == 'no'
            and value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
            and value['Job'] in ('', '0'))
        link = Path('/etc/systemd/system/multi-user.target.wants/sage-lantern-startup.service')
        parents(link)
        require(link.is_symlink() and link.resolve() == Path(STARTUP_SERVICE))

    def check(self):
        if 'partial_mesh' in self.expected:
            failed_mesh_inventory(self.expected['partial_mesh'])
        if 'mesh_reconciliation' in self.expected:
            failed_mesh_inventory(self.expected['mesh_reconciliation'])
        require(sha(read(Path(__file__).absolute())) == self.expected['handoff_sha256'])
        require(pinned(self.expected['policy_expected']) == self.policy_raw)
        pinned(self.expected['coordinator'])
        self.containment()
        for path, raw in self.guards.items():
            require(not os.path.lexists(path) if path in self.removed else read(path) == raw)
        if self.phase == 'native':
            return
        for path, raw in self.metadata.items():
            require(read(path) == raw)
        require(not os.path.lexists(self.mesh.STATE) and not os.path.lexists(self.mesh.B_GUARD / 'release-intent.json'))
        require(all(not os.path.lexists(path) for path, mode in self.mesh.DESTINATIONS.values()))
        current = project_mesh(self.mesh_expected, {path: self.guards[path] for path in self.removed})
        self.mesh.b_services(self.policy.system, current, time.monotonic() + 30,
            self.mesh.SERVICES + self.mesh.B_SERVICES, loaded=True)
        preparer = self.mesh.load_preparer(self.mesh_expected)
        arguments = dict(self.mesh_expected['preparation_args'])
        for name in ('manifest', 'radio_inventory', 'current_sage_policy'):
            arguments[name] = Path(arguments[name])
        raw, config, binding = preparer.inputs(types.SimpleNamespace(**arguments))
        require(all(type(config.get(name)) is type(value) and config[name] == value
            for name, value in self.policy_module.SCAN_BINDING.items()))
        observation = preparer.observe(config, binding)
        require(observation['boot_id'] == self.policy.expected['boot_id']
            and observation['storage_epoch'] == self.storage_epoch)
        if 'partial_mesh' in self.expected:
            started = time.monotonic()
            def live_guard():
                require(time.monotonic() - started < 120)
                self.containment(live_only=True)
            self.mesh.b_empty_workflows(config, observation['storage_epoch'], live_guard)
            self.containment()
            require(all(read(path) == raw for path, raw in self.metadata.items()))
        else:
            self.mesh.b_empty_workflows(config, observation['storage_epoch'], self.containment)
        if 'partial_mesh' in self.expected:
            failed_mesh_inventory(self.expected['partial_mesh'])

    def claim(self):
        parents(ROOT)
        if not os.path.lexists(ROOT):
            ROOT.mkdir(mode=0o700)
            sync(ROOT.parent)
        info = ROOT.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        self.directory.mkdir(mode=0o700)
        sync(ROOT)
        write(self.directory / 'intent.json', self.raw)

    def record(self, phase, path):
        name = 'complete.json' if phase == 'complete' else f'{time.time_ns()}-{phase}.json'
        write(self.directory / name, encoded(dict(phase=phase, path=path, expected_sha256=sha(self.raw))))

    def archive(self, path, raw):
        require(read(path) == raw)
        write(self.directory / (path.parent.name + '.guard'), raw)

    def remove(self, path, raw):
        require(path in self.guards and read(path) == raw)
        self.containment()
        path.unlink()
        self.removed.add(path)
        sync(path.parent)

    def reload(self):
        self.policy_module.systemctl('daemon-reload')

    def failure(self):
        restored = True
        if self.phase == 'native':
            for path, raw in self.guards.items():
                try:
                    if os.path.lexists(path):
                        require(read(path) == raw)
                    else:
                        write(path, raw, 0o644)
                    self.removed.discard(path)
                except BaseException:
                    restored = False
        try:
            self.reload()
        except BaseException:
            restored = False
        try:
            self.containment()
        except BaseException:
            restored = False
        return restored

    def publish(self):
        if self.phase != 'mesh':
            return
        updates = {self.mesh.B_GUARD / 'expected.json': encoded(self.next_mesh),
            self.mesh.B_GUARD / 'complete.json': encoded(self.next_receipt)}
        for path, raw in updates.items():
            self.check()
            self.record('metadata_replace_intent', str(path))
            write(self.directory / ('before-' + path.name), self.metadata[path])
            temporary = path.with_name(path.name + '.policy-handoff-new')
            write(temporary, raw)
            require(read(path) == self.metadata[path])
            self.containment()
            os.replace(temporary, path)
            self.metadata[path] = raw
            sync(path.parent)
            self.check()
        write(self.directory / 'mesh-expected.json', encoded(self.next_mesh))


def verify_completed_native(expected_raw, completed_sha256):
    backend = Backend(decode(expected_raw), expected_raw, completed_sha256=completed_sha256)
    backend.check()
    result = dict(verified=True, boot_id=backend.policy.expected['boot_id'],
        policy_expected=backend.expected['policy_expected'], native_guard_removed=True)
    if 'recovery_evidence' in backend.expected:
        result['recovery_evidence'] = backend.expected['recovery_evidence']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux')
    raw = pinned(dict(path=args.expected, sha256=args.expected_sha256))
    def interrupted(signum, frame):
        raise InterruptedError('b_guard_handoff_interrupted')
    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(signum, interrupted)
    signal.alarm(300)
    backend = Backend(decode(raw), raw)
    if args.apply:
        result = reconcile_mesh(backend) if 'partial_mesh' in backend.expected else transfer(backend)
    else:
        backend.check()
        result = dict(inspected=True, transferred=False, phase=backend.phase)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B guard handoff refused/unknown; preserve all guards and journals; no retry.') from None
