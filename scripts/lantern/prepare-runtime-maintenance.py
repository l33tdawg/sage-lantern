#!/usr/bin/env python3
"""Separate runtime replacement lane; never historical drain or native update.

Read-only CLI: apply is disabled pending complete integration review.
The transition prepares UI/producer containment and signed settled-work proof,
then freezes a narrow maintenance handoff before stopping current auth. Only a
fresh positive current-auth receipt plus empty importing-service cgroups may
lead to the exact candidate replacement from an immutable maintenance venv.
Native SAGE stays running. Basic restore requires newly started auth locked.
Old receipts, supervisor state and capture evidence are never rewritten.
The same-process tail requires fresh owner unlock and explicit capture approval
before real commissioning and broker-owned startup of a distinct lifetime.
No vault close, native restart, automatic retry or rollback.
"""

import argparse
import fcntl
import hashlib
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
import uuid
import zipfile


STATE = Path('/var/lib/sage-lantern-runtime-maintenance')
SYSTEMD = Path('/etc/systemd/system')
SYSTEMD_IMAGE = '/usr/lib/systemd/systemd'
PROC = Path('/proc')
UI = 'sage-lantern-fleet-admin.service'
VOICE = 'sage-lantern-voice-loop.service'
BROKER = 'sage-lantern-mesh-runtime.service'
AUTH = 'sage-lantern-device-auth.service'
NATIVE = 'sage-lantern-personal-sage.service'
MODEL = 'sage-lantern-llm.service'
DISPLAY = 'sage-lantern-display-power.service'
DISPLAY_ENABLE = Path('/etc/sage-lantern-display-power.enabled')
DISPLAY_POLICY_SHA = 'b8d921dbb6507b9f3f11a644d0af10a63de8058a6f4961f4f94bd2d26e454faf'
MESH_CONFIG = Path('/etc/sage-lantern/mesh-runtime.json')
RECEIPTS = Path('/var/lib/sage-lantern/device-lock/drain')
SERVICE_PARENTS = frozenset((Path('/var/lib/sage-lantern'),
    Path('/var/lib/sage-lantern/device-lock'), RECEIPTS))
IDENTITY_FIELDS = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid')
FILE_FIELDS = IDENTITY_FIELDS + ('st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
STOP = (UI, VOICE, BROKER)
KEEP = (AUTH, NATIVE, MODEL)
BASIC = ('sage-lantern-camera.service', 'sage-lantern-display-power.service', 'sage-lantern-status.service')
SERVICES = STOP + KEEP + BASIC
EXTRA = ('sage-lantern-idle-suspend.service', 'sage-lantern-device-agent.service',
         'sage-lantern-startup.service')
VOICE_RUN = Path('/run/sage-lantern-voice')
VOICE_REQUEST = Path('/run/sage-lantern-mesh-control/voice-drain-request.json')
RETRIEVAL_V1 = '36a94a18a83889ceb5860d40ace998410d1bc3fcfbb806af9731199f331306a3'
OWNER_LEDGERS_V2 = 'b0cfc4f94333960a579fda7e1c3b41bfcd4c7206c2a0441715af980133e36d17'
RADIO_OWNER_COMBINED = '2175cd19e275597860cdd877a1007156366667021d721766023a569d83bee519'
OWNER_LEDGER_SDK = 'd61c829045a0ad30bccbb32b54734c5b14ba81236fc79f14d7dee69489478ae9'
COMPACT_PROMPT_V1 = 'fd39c10773de0726e333b296a1dfc5dab3058753edfb5925dd2b46abfcd4ddd3'
FIELDS = ('LoadState', 'ActiveState', 'SubState', 'MainPID', 'InvocationID',
          'Job', 'ControlGroup', 'Slice', 'KillMode', 'FragmentPath',
          'DropInPaths', 'NeedDaemonReload', 'Result', 'NRestarts', 'Restart', 'RuntimeDirectoryPreserve')


def require(value):
    if not value:
        raise ValueError('runtime_maintenance_refused')


def validate(value, *, candidate_sha256=RETRIEVAL_V1):
    require(type(value) is dict and set(value) == {'schema', 'boot_id', 'access_epoch',
        'helper', 'collector', 'installed_wheel', 'candidate_wheel', 'site', 'files',
        'services', 'actor', 'storage_epoch', 'workflow_sha256', 'receipts', 'receipt_parents',
        'maintenance_site', 'runtime_tool', 'native_health', 'native_listener_inode', 'native_observer', 'extra_services',
        'environment_builder', 'environment_complete', 'voice_instance', 'audio_tail', 'audio_tools',
        'capture_snapshot', 'audio_inventory', 'model_files', 'model_argv', 'startup_before'})
    require(value['schema'] == 'sage.lantern.runtime-maintenance.v1'
            and str(uuid.UUID(value['boot_id'])) == value['boot_id']
            and type(value['access_epoch']) is str and bool(value['access_epoch'])
            and type(value['storage_epoch']) is str and bool(value['storage_epoch'])
            and re.fullmatch('[0-9a-f]{64}', value['workflow_sha256']))
    require(value['site'] == '/opt/sage-lantern/venv/lib/python3.11/site-packages'
            and set(value['services']) == set(SERVICES)
            and type(value['actor']) is dict and set(value['actor']) == {'chain_id', 'agent_id'})
    require(type(value['maintenance_site']) is str and Path(value['maintenance_site']).is_absolute()
            and value['maintenance_site'] != value['site'])
    for field in ('helper', 'collector', 'installed_wheel', 'candidate_wheel', 'runtime_tool', 'native_observer',
                  'environment_builder', 'environment_complete', 'audio_tail'):
        pin = value[field]
        require(type(pin) is dict and set(pin) == {'path', 'sha256'}
                and type(pin['path']) is str and Path(pin['path']).is_absolute()
                and re.fullmatch('[0-9a-f]{64}', pin['sha256']))
    require(candidate_sha256 in (RETRIEVAL_V1, COMPACT_PROMPT_V1, OWNER_LEDGERS_V2, RADIO_OWNER_COMBINED,
        '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'))
    require(value['candidate_wheel']['sha256'] == candidate_sha256)
    require(type(value['audio_tools']) is dict and set(value['audio_tools']) == {
        'helper', 'collector', 'runtime_wheel', 'adapter', 'coordinator'}
        and value['audio_tools']['runtime_wheel'] == value['candidate_wheel']
        and value['audio_tools']['helper'] == value['helper']
        and value['audio_tools']['collector'] == value['collector'])
    require(type(value['capture_snapshot']) is dict and type(value['audio_inventory']) is dict
            and type(value['model_files']) is list and value['model_files']
            and set(value['model_files']) <= set(value['files']) and type(value['model_argv']) is list
            and type(value['startup_before']) is dict and set(value['startup_before']) == {'config', 'latest', 'coordinator'})
    require(type(value['extra_services']) is dict and set(value['extra_services']) == set(EXTRA))
    instance = value['voice_instance']
    require(type(instance) is dict and set(instance) == {'instance_id', 'pid', 'start_ticks', 'boot_id', 'invocation_id'}
            and str(uuid.UUID(instance['instance_id'])) == instance['instance_id']
            and instance['boot_id'] == value['boot_id']
            and type(instance['pid']) is int and instance['pid'] > 0
            and re.fullmatch('[1-9][0-9]*', instance['start_ticks'])
            and re.fullmatch('[0-9a-f]{32}', instance['invocation_id']))
    require(type(value['native_health']) is dict
            and set(value['native_health']) == {'boot_id', 'version', 'encrypted'}
            and value['native_health']['encrypted'] is True
            and str(uuid.UUID(value['native_health']['boot_id'])) == value['native_health']['boot_id']
            and type(value['native_health']['version']) is str
            and re.fullmatch('[1-9][0-9]{0,19}', value['native_listener_inode']))
    for field in ('files', 'receipts'):
        require(type(value[field]) is dict and bool(value[field]))
        for name, digest in value[field].items():
            require(type(name) is str and Path(name).is_absolute()
                    and re.fullmatch('[0-9a-f]{64}', digest))
    for name in value['receipts']:
        require(re.fullmatch('/var/lib/sage-lantern/device-lock/drain/[0-9a-f]{32}\\.json', name))
    require(type(value['receipt_parents']) is dict
            and set(value['receipt_parents']) == {str(path) for path in SERVICE_PARENTS})
    for metadata in value['receipt_parents'].values():
        require(type(metadata) is dict and set(metadata) == set(IDENTITY_FIELDS)
                and all(type(member) is int for member in metadata.values()))


def directory_identity(info):
    return {name: getattr(info, name) for name in IDENTITY_FIELDS}


def configured_native_port(config):
    endpoint = config['endpoint']
    require(type(endpoint) is str and re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{0,4}', endpoint)
            is not None and int(endpoint.rsplit(':', 1)[1]) <= 65535)
    return int(endpoint.rsplit(':', 1)[1])


def display_policy(digest):
    require(digest == DISPLAY_POLICY_SHA)
    parents = {parent: directory_identity(parent.lstat()) for parent in DISPLAY_ENABLE.parents}
    require(all(stat.S_ISDIR(info['st_mode']) and info['st_uid'] == 0
                and not info['st_mode'] & 0o022 for info in parents.values()))
    descriptor = os.open(DISPLAY_ENABLE, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_gid == 0
                and stat.S_IMODE(before.st_mode) == 0o644 and before.st_nlink == 1 and before.st_size == 13)
        raw = os.read(descriptor, 14)
        require(raw == b'fresh unit-a\n' and hashlib.sha256(raw).hexdigest() == digest
                and file_identity(os.fstat(descriptor)) == file_identity(before)
                and file_identity(DISPLAY_ENABLE.lstat()) == file_identity(before)
                and all(directory_identity(parent.lstat()) == info for parent, info in parents.items()))
    finally:
        os.close(descriptor)


def inactive_display(system, service, unit, *, policy_sha256=None):
    require(service == DISPLAY
            and unit['LoadState'] == 'loaded' and unit['ActiveState'] == 'inactive'
            and unit['SubState'] == 'dead' and unit['MainPID'] == '0'
            and unit['InvocationID'] == '' and unit['NRestarts'] == '0'
            and unit['ControlGroup'] in ('', '/system.slice/' + service)
            and system._no_job(unit['Job'])
            and system._show(service, system.clock() + 5, ('ConditionResult', 'UnitFileState'))
                == {'ConditionResult': 'no', 'UnitFileState': 'disabled'})
    if policy_sha256 is None:
        require(not os.path.lexists(DISPLAY_ENABLE))
    else:
        display_policy(policy_sha256)
    root = system._cgroup('/system.slice/' + service)
    system._empty_tree(root, system.clock() + 5)
    if root.exists():
        require(dict(line.split() for line in (root / 'cgroup.events').read_text().splitlines()).get('populated') == '0')
    require(system._show(service, system.clock() + 5, tuple(unit)) == unit
            and system._show(service, system.clock() + 5, ('ConditionResult', 'UnitFileState'))
                == {'ConditionResult': 'no', 'UnitFileState': 'disabled'})
    if policy_sha256 is None:
        require(not os.path.lexists(DISPLAY_ENABLE))
    else:
        display_policy(policy_sha256)


def file_identity(info):
    return {name: getattr(info, name) for name in FILE_FIELDS}


def read_receipt(path, uid, parent_pins):
    """Only this fixed receipt hierarchy may be owned by the pinned auth UID."""
    path = Path(path)
    require(path.parent == RECEIPTS and re.fullmatch('[0-9a-f]{32}\\.json', path.name)
            and type(uid) is int and uid > 0)
    descriptors = []
    try:
        descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(descriptor)
        current = Path('/')
        for part in path.parent.parts[1:]:
            descriptor = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                 dir_fd=descriptor)
            descriptors.append(descriptor)
            current /= part
            info = os.fstat(descriptor)
            require(stat.S_ISDIR(info.st_mode) and not info.st_mode & 0o022)
            if current in SERVICE_PARENTS:
                require(directory_identity(info) == parent_pins[str(current)]
                        and info.st_uid in (0, uid) and stat.S_IMODE(info.st_mode) == 0o700)
            else:
                require(info.st_uid == 0)
        leaf = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        descriptors.append(leaf)
        before = os.fstat(leaf)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == uid and before.st_nlink == 1
                and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 4096)
        raw = os.read(leaf, 4097)
        require(len(raw) == before.st_size and file_identity(os.fstat(leaf)) == file_identity(before)
                and file_identity(os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)) == file_identity(before))
        for parent, opened in zip(path.parent.parents[::-1], descriptors):
            require(directory_identity(parent.lstat()) == directory_identity(os.fstat(opened)))
        require(directory_identity(path.parent.lstat()) == directory_identity(os.fstat(descriptor)))
        return raw
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def current_auth_confirmed(raw, instance, decoder):
    value = decoder(raw)
    require(type(value) is dict and set(value) == {'version', 'instance_id', 'closing',
        'drained', 'local_revoked', 'upstream_cleanup'} and type(value['version']) is int
        and value['version'] == 1 and value['instance_id'] == instance
        and value['closing'] is True and value['drained'] is True
        and value['local_revoked'] is True and value['upstream_cleanup'] == 'confirmed')
    return value


def importer_inventory(allowed, *, proc=Path('/proc')):
    """Conservatively exclude every unaccounted Python or target-venv process.

    Command bytes are bounded, hashed locally and never returned. This is
    exclusion, not a claim that Python processes have actually imported SAGE.
    Allowed PIDs require exact executable inode and start ticks, including self.
    """
    entries = list(proc.iterdir())
    require(len(entries) <= 32768)
    found = {}
    for root in entries:
        if not root.name.isdecimal():
            continue
        try:
            raw = (root / 'stat').read_bytes()
            require(len(raw) <= 8192)
            fields = raw.rsplit(b')', 1)[1].split()
            if fields[0] == b'Z':
                continue
            before = (int(root.name), int(fields[19]))
            if int(fields[6]) & 0x00200000:
                require((root / 'cmdline').read_bytes() == b'' and not (root / 'exe').exists())
                continue
            executable = (root / 'exe').stat()
            name = os.readlink(root / 'exe')
            with (root / 'cmdline').open('rb') as stream:
                arguments = stream.read(65537)
            require(len(arguments) <= 65536)
            candidate = ('python' in Path(name).name.lower()
                or b'/opt/sage-lantern/venv' in arguments or b'sage_lantern' in arguments)
            if not candidate:
                continue
            identity = dict(start_ticks=before[1], exe_dev=executable.st_dev, exe_ino=executable.st_ino)
            require(allowed.get(before[0]) == identity)
            require((root / 'stat').read_bytes().rsplit(b')', 1)[1].split()[19] == str(before[1]).encode()
                    and file_identity((root / 'exe').stat()) == file_identity(executable))
            found[before[0]] = identity
        except FileNotFoundError:
            if root.exists():
                require((root / 'stat').read_bytes().rsplit(b')', 1)[1].split()[0] == b'Z')
    require(found == allowed)
    return found


def handoff(backend):
    """Freeze signed authorization before closing this exact current auth instance.

    The backend is a root composition, not a request-selected callback. Its
    post-auth checker must not reuse an owner access epoch. This transition is
    not reachable from the CLI until the complete concrete tail is reviewed.
    """
    backend.current()
    evidence = backend.reconcile()
    backend.record('signed_handoff', evidence)
    backend.freeze_handoff(evidence)
    backend.current()
    backend.record('inhibit_current_auth_intent')
    backend.inhibit_auth()
    backend.current()
    backend.record('inhibit_current_auth_done')
    backend.record('stop_current_auth_intent')
    backend.stop_auth()
    backend.current_after_auth()
    backend.confirm_current_auth_receipt()
    backend.current_after_auth()
    backend.record('stop_current_auth_done')
    for service in ('sage-lantern-camera.service', 'sage-lantern-display-power.service',
                    'sage-lantern-status.service'):
        backend.current_after_auth()
        backend.record('stop_importer_' + service + '_intent')
        backend.stop_importer(service)
        backend.current_after_auth()
        backend.record('stop_importer_' + service + '_done')
    backend.verify_all_importers_empty()
    backend.current_after_auth()
    phase = getattr(backend, 'replacement_phase', 'runtime_replace')
    require(phase in ('runtime_replace', 'vision_configure'))
    backend.record(phase + '_intent')
    backend.install_pinned_runtime()
    backend.verify_installed_runtime()
    backend.current_after_auth()
    backend.record(phase + '_done')
    backend.restore_auth_locked()
    backend.current_after_auth()
    backend.restore_basic_locked()
    backend.current_after_auth()
    backend.record('basic_restored_owner_unlock_required')
    return dict(runtime_updated=phase == 'runtime_replace', audio_activated=False, owner_unlock_required=True,
                new_commissioning_required=True, historical_drain_verified=False)


def load(path, digest):
    import hashlib
    path = Path(path)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        require(len(raw) == before.st_size and file_identity(os.fstat(descriptor)) == file_identity(before)
                and file_identity(path.lstat()) == file_identity(before) and hashlib.sha256(raw).hexdigest() == digest)
    finally:
        os.close(descriptor)
    module = types.ModuleType('pinned_maintenance_dependency')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def maintenance_environment_sources(helper, collector, expected, build_expected, installed_sources):
    site = Path(expected['maintenance_site'])
    if expected['candidate_wheel']['sha256'] not in (OWNER_LEDGERS_V2, RADIO_OWNER_COMBINED):
        sources = collector.runtime_sources(helper, expected['installed_wheel']['path'], site)
        for name, digest in sources.items():
            counterpart = Path(expected['site']) / Path(name).relative_to(site)
            require(installed_sources[str(counterpart)] == digest)
        return sources
    require(type(build_expected) is dict and type(build_expected.get('wheels')) is list)
    runtime = [pin for pin in build_expected['wheels'] if pin.get('sha256') == expected['candidate_wheel']['sha256']]
    sdk = [pin for pin in build_expected['wheels'] if pin.get('sha256') == OWNER_LEDGER_SDK]
    require(len(runtime) == len(sdk) == 1)
    helper.pinned(runtime[0])
    sources = collector.runtime_sources(helper, runtime[0]['path'], site)
    with zipfile.ZipFile(io.BytesIO(helper.pinned(sdk[0]))) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)))
        modules = {name for name in names if name.startswith('sage_sdk/') and name.endswith('.py')}
        require(modules and modules == {str(path.relative_to(site)) for path in (site / 'sage_sdk').rglob('*.py')})
        for name in modules:
            require('..' not in Path(name).parts and helper.sha(archive.read(name))
                == sources[str(site / name)])
    return sources


class Maintenance:
    def __init__(self, expected, *, candidate_sha256=RETRIEVAL_V1):
        validate(expected, candidate_sha256=candidate_sha256)
        self.expected = expected
        self.helper = load(**dict(path=expected['helper']['path'], digest=expected['helper']['sha256']))
        self.collector = load(expected['collector']['path'], expected['collector']['sha256'])
        self.runtime_tool = load(expected['runtime_tool']['path'], expected['runtime_tool']['sha256'])
        environment_builder = load(expected['environment_builder']['path'], expected['environment_builder']['sha256'])
        complete = self.helper.decode(self.helper.pinned(expected['environment_complete']))
        require(set(complete) == {'schema', 'expected_sha256', 'inventory_sha256', 'venv', 'files'}
                and complete['schema'] == 'sage.lantern.maintenance-environment-complete.v1')
        environment = Path(complete['venv'])
        require(Path(expected['environment_complete']['path']) == environment.parent / 'complete.json'
                and Path(expected['maintenance_site']).parents[2] == environment)
        build_expected = self.helper.public(environment.parent / 'intent.json')
        require(self.helper.sha(build_expected) == complete['expected_sha256'])
        manifest = self.helper.public(environment.parent / 'inventory.json')
        require(self.helper.sha(manifest) == complete['inventory_sha256']
                and environment_builder.inventory(environment) == self.helper.decode(manifest))
        self.environment_metadata = {str(environment / name): self.helper.metadata((environment / name).lstat())
                                     for name in self.helper.decode(manifest)}
        self.sources = self.collector.runtime_sources(self.helper, expected['installed_wheel']['path'],
                                                     Path(expected['site']))
        maintenance_site = Path(expected['maintenance_site'])
        require(sys.flags.isolated and sys.dont_write_bytecode
                and Path(sys.prefix) == maintenance_site.parents[2]
                and not Path(sys.prefix).is_relative_to('/opt/sage-lantern/venv'))
        self.maintenance_sources = maintenance_environment_sources(self.helper, self.collector,
            expected, self.helper.decode(build_expected), self.sources)
        for field in ('installed_wheel', 'candidate_wheel'):
            self.helper.pinned(expected[field])
        self.old_wheel = self.helper.pinned(expected['installed_wheel'])
        self.new_wheel = self.helper.pinned(expected['candidate_wheel'])
        self.runtime_tool.verify_installed(self.old_wheel)
        self.runtime_tool.wheel_files(self.new_wheel)
        from sage_lantern.suspend_system import SuspendSystem
        import sage_lantern.suspend_system
        require(Path(sage_lantern.suspend_system.__file__).parent == maintenance_site / 'sage_lantern')
        self.system = SuspendSystem()
        self.native_observer = load(expected['native_observer']['path'], expected['native_observer']['sha256'])
        self.audio_tail = load(expected['audio_tail']['path'], expected['audio_tail']['sha256'])
        self.audio_coordinator = load(expected['audio_tools']['coordinator']['path'], expected['audio_tools']['coordinator']['sha256'])
        adapter = load(expected['audio_tools']['adapter']['path'], expected['audio_tools']['adapter']['sha256'])
        require(adapter.HELPER_SHA == expected['helper']['sha256']
                and adapter.COLLECTOR_SHA == expected['collector']['sha256'])
        for pin in [*expected['audio_tools'].values(), *expected['startup_before'].values()]:
            self.helper.pinned(pin)
        self.audio_coordinator.verify_wheel(expected['candidate_wheel'])
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        ColdBrokerBootstrap(expected['audio_inventory'])
        require(all(expected['files'].get(path) == digest for path, digest in expected['audio_inventory'].items()))
        self.guarded = set()
        self.stopped = set()
        self.locks = []
        self.tree = None
        self.sequence = 0
        self.stop_outcomes = {}
        self.post_auth = False
        self.handoff_sha256 = None
        self.storage_client = None
        self.storage_descriptor = None
        self.storage_probe = None
        self.auth_final_sha256 = None
        self.installed = False
        self.running = {}
        self.new_auth_receipt = None
        self.restore_attempted = set()
        self.voice_transition = None
        self.activation_access = None
        self.audio_owner = None
        self.audio_evidence = None
        self.audio_failure_handled = False
        self.reset_services = set()
        self.tree_location = self.helper.SUPERVISOR
        self.self_identity = dict(start_ticks=int(Path('/proc/self/stat').read_bytes().rsplit(b')', 1)[1].split()[19]),
            exe_dev=Path('/proc/self/exe').stat().st_dev, exe_ino=Path('/proc/self/exe').stat().st_ino)
        self.file_metadata = {}
        for name, digest in expected['files'].items():
            require(self.helper.file_hash(name) == digest)
            self.file_metadata[name] = self.helper.metadata(Path(name).lstat())
        require(SYSTEMD_IMAGE in expected['files'])
        for service, value in expected['services'].items():
            require(type(value) is dict and set(value) == {'unit', 'process'})
            unit = value['unit']
            require(set(unit) == set(FIELDS))
            require({unit['FragmentPath'], *unit['DropInPaths'].split()} <= set(expected['files']))
            require(unit['FragmentPath'] == str(SYSTEMD / service)
                    and unit['Slice'] == 'system.slice' and unit['KillMode'] == 'control-group')
        self.current()
        self.exclude_importers(initial=True)
        require(not os.path.lexists(VOICE_REQUEST) and self.voice_document('instance.json') == expected['voice_instance'])
        require(self.system._show('sage-lantern-startup.service', self.system.clock() + 5,
                                  ('UnitFileState',)) == {'UnitFileState': 'enabled'})
        require(self.collector.model_argv(self.system, self.helper) == expected['model_argv'])
        self.model_pin = self.helper.Recovery.model_ready(self)
        require(self.model_pin is not None)
        original_capture = self.audio_tail.installed_audio(self, ['--inspect'])
        require(self.audio_tail.predicted_capture(original_capture['snapshot'],
            self.runtime_tool.wheel_files(self.new_wheel), expected['site'], self.helper.sha) == expected['capture_snapshot'])

    def show(self, service):
        return self.system._show(service, self.system.clock() + 5, FIELDS)

    def process(self, unit):
        pid = unit['MainPID']
        require(pid.isdecimal() and int(pid) > 0 and re.fullmatch('[0-9a-f]{32}', unit['InvocationID']))
        root = PROC / pid
        fields = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        require(fields[0] not in ('Z', 'T', 't', 'X')
                and unit['ControlGroup'] in {'/system.slice/' + service for service in SERVICES}
                and (root / 'cgroup').read_text() == '0::' + unit['ControlGroup'] + '\n')
        before = fields[19]
        executable = os.stat(root / 'exe')
        result = dict(pid=int(pid), start_ticks=int(before), uid=root.stat().st_uid,
            exe_dev=executable.st_dev, exe_ino=executable.st_ino,
            executable=os.readlink(root / 'exe'),
            argv_sha256=self.helper.sha((root / 'cmdline').read_bytes()))
        require((root / 'stat').read_text().rsplit(')', 1)[1].split()[19] == before
                and file_identity(os.stat(root / 'exe')) == file_identity(executable))
        require(result['executable'] in self.expected['files']
                and self.helper.metadata(Path(result['executable']).stat()) == self.file_metadata[result['executable']]
                and (executable.st_dev, executable.st_ino) ==
                    (self.file_metadata[result['executable']]['st_dev'], self.file_metadata[result['executable']]['st_ino']))
        return result

    def starting_process(self, service, unit, observed, deadline):
        require(service in (MODEL, AUTH, UI) + BASIC and self.system.clock() < deadline
                and unit['ActiveState'] in ('activating', 'active')
                and unit['ControlGroup'] == '/system.slice/' + service
                and re.fullmatch('[1-9][0-9]*', unit['MainPID'])
                and re.fullmatch('[0-9a-f]{32}', unit['InvocationID'])
                and unit['InvocationID'] != self.expected['services'][service]['unit']['InvocationID'])
        root = PROC / unit['MainPID']
        before = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        require(before[0] not in ('Z', 'T', 't', 'X')
                and (root / 'cgroup').read_text() == '0::/system.slice/' + service + '\n')
        identity = (unit['MainPID'], int(before[19]), unit['InvocationID'])
        require(observed is None or observed[:3] == identity)
        required = self.expected['services'][service]['process']['executable']
        allowed = (SYSTEMD_IMAGE, required)
        first = os.stat(root / 'exe')
        image = os.readlink(root / 'exe')
        second = os.stat(root / 'exe')
        require(image in allowed and all(name in self.file_metadata for name in allowed))
        for name in allowed:
            require(self.helper.metadata(Path(name).stat()) == self.file_metadata[name])
        pins = {(self.file_metadata[name]['st_dev'], self.file_metadata[name]['st_ino']): name for name in allowed}
        first_image = pins.get((first.st_dev, first.st_ino))
        second_image = pins.get((second.st_dev, second.st_ino))
        require(first_image in allowed and second_image in allowed
                and not (first_image == required and second_image == SYSTEMD_IMAGE)
                and not (observed is not None and observed[3] and first_image != required))
        uid = root.stat().st_uid
        target_uid = self.expected['services'][service]['process']['uid']
        if service == 'sage-lantern-camera.service':
            require(self.system._show(service, deadline, ('User', 'DynamicUser')) ==
                    {'User': 'sage-lantern-camera', 'DynamicUser': 'yes'})
            require(uid >= 0)
        else:
            require(uid in (0, target_uid))
        after = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        fresh = self.show(service)
        require(after[19] == before[19] and after[0] not in ('Z', 'T', 't', 'X')
                and fresh['MainPID'] == unit['MainPID'] and fresh['InvocationID'] == unit['InvocationID']
                and fresh['ControlGroup'] == unit['ControlGroup']
                and self.system.clock() < deadline)
        if first_image != required or second_image != required:
            return None, (*identity, False)
        process = self.process(unit)
        require(process['executable'] == required and process['start_ticks'] == identity[1]
                and (process['uid'] > 0 if service == 'sage-lantern-camera.service' else process['uid'] == target_uid))
        return process, (*identity, True)

    def guard_path(self, service):
        return SYSTEMD / (service + '.d') / 'zzzz-runtime-maintenance.conf'

    def guard(self):
        return ('[Unit]\nConditionPathExists=!' + str(STATE)
                + '\n[Service]\nRestart=no\nRuntimeDirectoryPreserve=yes\n').encode()

    def empty(self, service, value):
        require(value['ActiveState'] in ('inactive', 'failed') and value['MainPID'] == '0'
                and self.system._no_job(value['Job']) and value['Slice'] == 'system.slice'
                and value['InvocationID'] in ('', self.expected['services'][service]['unit']['InvocationID']))
        group = '/system.slice/' + service
        require(value['ControlGroup'] in ('', group))
        root = self.system._cgroup(group)
        deadline = self.system.clock() + 5
        self.system._empty_tree(root, deadline)
        if root.exists():
            events = dict(line.split() for line in (root / 'cgroup.events').read_text().splitlines())
            require(events.get('populated') == '0')
        require(self.show(service) == value)

    def current(self):
        from sage_lantern.device_access import access_epoch
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        if self.activation_access is not None:
            require(access_epoch() == self.activation_access)
        self.native_live()
        if self.post_auth:
            require(self.handoff_sha256 is not None
                    and self.helper.sha(self.helper.public(STATE / 'signed-handoff.json')) == self.handoff_sha256
                    and self.storage_probe.snapshot().epoch == self.expected['storage_epoch'])
        else:
            require(access_epoch() == self.expected['access_epoch'])
        self.system._sleep_idle(self.system.clock() + 10)
        for name, original in self.file_metadata.items():
            require(self.helper.metadata(Path(name).lstat()) == original)
        for name, original in self.environment_metadata.items():
            require(self.helper.metadata(Path(name).lstat()) == original)
        for name, digest in self.sources.items():
            require(self.helper.sha(self.collector.trusted(name)) == digest)
        for name, digest in self.maintenance_sources.items():
            require(self.helper.sha(self.collector.trusted(name)) == digest)
        self.receipts_current()
        for service in SERVICES:
            value = self.show(service)
            original = self.expected['services'][service]
            require(value['LoadState'] == 'loaded' and value['NeedDaemonReload'] == 'no'
                    and self.system._no_job(value['Job']))
            paths = set(original['unit']['DropInPaths'].split())
            if service in self.guarded:
                path = self.guard_path(service)
                require(self.helper.public(path) == self.guard() and STATE.is_dir() and value['Restart'] == 'no'
                        and value['RuntimeDirectoryPreserve'] == 'yes')
                paths.add(str(path))
                self.system._guard(service, self.system.clock() + 5, str(STATE))
            else:
                require(value['Restart'] == original['unit']['Restart']
                        and value['RuntimeDirectoryPreserve'] == original['unit']['RuntimeDirectoryPreserve'])
            require(set(value['DropInPaths'].split()) == paths
                    and all(value[field] == original['unit'][field]
                            for field in ('FragmentPath', 'Slice', 'KillMode', 'NRestarts')))
            if service in self.running:
                require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                        and value['InvocationID'] == self.running[service]['invocation']
                        and self.process(value) == self.running[service]['process']
                        and self.show(service) == value)
            elif service in self.stopped:
                self.empty(service, value)
                if original['process'] is None:
                    inactive_display(self.system, service, value,
                        policy_sha256=self.expected['files'].get(str(DISPLAY_ENABLE)))
            elif original['process'] is None:
                inactive_display(self.system, service, value,
                    policy_sha256=self.expected['files'].get(str(DISPLAY_ENABLE)))
                require(all(value[field] == original['unit'][field] for field in FIELDS
                            if field not in ('DropInPaths', 'Restart', 'RuntimeDirectoryPreserve')))
            else:
                require(value['ActiveState'] == 'active' and value['SubState'] == 'running'
                        and value['InvocationID'] == original['unit']['InvocationID']
                        and self.process(value) == original['process'] and self.show(service) == value)
        if self.tree is not None:
            require(self.helper.tree(self.tree_location) == self.tree)
        if self.audio_owner is not None:
            require(self.audio_owner.active() == self.audio_evidence
                    and self.audio_evidence['vault_epoch'] == self.expected['storage_epoch'])
        for service in EXTRA:
            self.extra_current(service)

    def extra_current(self, service):
        require(service in EXTRA)
        expected = self.expected['extra_services'][service]
        fields = ('LoadState', 'ActiveState', 'Job')
        value = self.system._show(service, self.system.clock() + 5, fields)
        require(value['LoadState'] in ('loaded', 'not-found')
                and value['ActiveState'] in ('inactive', 'failed') and self.system._no_job(value['Job']))
        if value['LoadState'] == 'not-found':
            require(expected == value and service not in self.guarded
                    and not os.path.lexists(self.guard_path(service)))
        else:
            value = self.show(service)
            require(value['MainPID'] == '0' and value['ControlGroup'] in ('', '/system.slice/' + service)
                    and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group'
                    and value['NeedDaemonReload'] == 'no')
            paths = set(expected['DropInPaths'].split())
            if service in self.guarded:
                paths.add(str(self.guard_path(service)))
                require(self.helper.public(self.guard_path(service)) == self.guard() and value['Restart'] == 'no'
                        and value['RuntimeDirectoryPreserve'] == 'yes')
                self.system._guard(service, self.system.clock() + 5, str(STATE))
            else:
                require(value['Restart'] == expected['Restart']
                        and value['RuntimeDirectoryPreserve'] == expected['RuntimeDirectoryPreserve'])
            require(set(value['DropInPaths'].split()) == paths
                    and all(value[field] == expected[field] for field in FIELDS
                            if field not in ('DropInPaths', 'Restart', 'RuntimeDirectoryPreserve'))
                    and {value['FragmentPath'], *expected['DropInPaths'].split()} <= set(self.expected['files']))
        root = self.system._cgroup('/system.slice/' + service)
        self.system._empty_tree(root, self.system.clock() + 5)
        if root.exists():
            events = dict(line.split() for line in (root / 'cgroup.events').read_text().splitlines())
            require(events.get('populated') == '0')

    def exclude_importers(self, *, initial=False):
        allowed = {os.getpid(): self.self_identity}
        if initial:
            for service in SERVICES:
                process = self.expected['services'][service]['process']
                if process is None:
                    inactive_display(self.system, service, self.show(service),
                        policy_sha256=self.expected['files'].get(str(DISPLAY_ENABLE)))
                    continue
                if 'python' in Path(process['executable']).name.lower():
                    allowed[process['pid']] = {key: process[key] for key in ('start_ticks', 'exe_dev', 'exe_ino')}
        require(importer_inventory(allowed) == importer_inventory(allowed))

    def native_live(self):
        import http.client
        from sage_lantern.mesh_gateway_root import backend_process_current
        unit = self.show(NATIVE)
        original = self.expected['services'][NATIVE]
        require(unit['InvocationID'] == original['unit']['InvocationID']
                and self.process(unit) == original['process'])
        raw = self.helper.public(MESH_CONFIG)
        require(self.helper.sha(raw) == self.expected['files'][str(MESH_CONFIG)])
        config = self.helper.decode(raw)
        require({name: config[name] for name in ('chain_id', 'agent_id')} == self.expected['actor'])
        port = configured_native_port(config)
        pin = dict(backend_pin=dict(pid=original['process']['pid'],
            start_ticks=str(original['process']['start_ticks']), boot_id=self.expected['boot_id'],
            listener_inode=self.expected['native_listener_inode']), role=dict(endpoint=config['endpoint']))
        require(backend_process_current(pin))
        def get(port, route):
            connection = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
            try:
                connection.request('GET', route)
                response = connection.getresponse()
                raw = response.read(262145)
                require(response.status == 200 and len(raw) <= 262144)
                return self.helper.decode(raw)
            finally:
                connection.close()
        health = get(port, '/v1/dashboard/health')
        require(type(health) is dict and health.get('encrypted') is True
                and {name: health.get(name) for name in self.expected['native_health']} == self.expected['native_health'])
        self.native_observer.governance(get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            get(26657, '/status'), self.expected['actor']['chain_id'], get(26657, '/abci_info'))
        require(self.helper.public(MESH_CONFIG) == raw and backend_process_current(pin)
                and self.show(NATIVE) == unit and self.process(unit) == original['process'])

    def receipts_current(self):
        receipt_root = RECEIPTS
        require({str(path) for path in receipt_root.iterdir()} == set(self.expected['receipts']) |
                ({str(self.new_auth_receipt)} if self.new_auth_receipt is not None else set()))
        for name, digest in self.expected['receipts'].items():
            raw = read_receipt(name, self.expected['services'][AUTH]['process']['uid'],
                               self.expected['receipt_parents'])
            if self.post_auth and Path(name).name == self.auth_instance() + '.json':
                current_auth_confirmed(raw, self.auth_instance(), self.helper.decode)
                if self.auth_final_sha256 is not None:
                    require(self.helper.sha(raw) == self.auth_final_sha256)
            else:
                require(self.helper.sha(raw) == digest)

    def reconcile(self):
        require(not self.stopped or self.stopped == set(STOP))
        scan = self.collector.signed_inventory if self.stopped else self.collector.signed_observation
        epoch, digest = scan(self.helper, self, self.expected)
        require(epoch == self.expected['storage_epoch'] and digest == self.expected['workflow_sha256'])
        return dict(storage_epoch=epoch, workflow_sha256=digest)

    def reconcile_audio(self, *, prestart_owner=None):
        producers = {VOICE, BROKER}

        def contained():
            require(self.installed and self.post_auth and self.handoff_sha256
                and self.activation_access is not None and self.audio_owner is None
                and self.stopped == set(STOP + BASIC + (AUTH,))
                and AUTH in self.running and UI in self.running
                and not producers.intersection(self.running)
                and self.tree is not None and self.locks)
            self.current_after_auth()
            if prestart_owner is None:
                require(producers <= self.guarded)
            else:
                from sage_lantern.deployment_bringup import ColdBrokerBootstrap
                require(type(prestart_owner) is ColdBrokerBootstrap
                    and prestart_owner.inventory == self.expected['audio_inventory']
                    and not producers.intersection(self.guarded))
                prestart_owner.fresh()
            names = sorted(name for name in self.tree if name.endswith('.lock'))
            require(len(self.locks) == len(names))
            for descriptor, name in zip(self.locks, names):
                require(self.helper.metadata(os.fstat(descriptor)) == self.tree[name]['metadata'])
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for service in sorted(producers):
                self.empty(service, self.show(service))
            return True

        def inspect(rows, store, identity, guard, config):
            def fresh():
                require(guard() is True)
                return contained()
            return self.helper.inspect_retained_inventory(
                rows, self.expected['actor'], store, identity, fresh, config)

        contained()
        epoch, digest = self.collector._signed_scan(self.helper, self, self.expected, inspection=inspect)
        contained()
        require(epoch == self.expected['storage_epoch'] and digest == self.expected['workflow_sha256'])
        return dict(storage_epoch=epoch, workflow_sha256=digest)

    def inspect_inventory(self, rows, store, identity, guard, config):
        def contained():
            require(self.stopped == set(STOP) and set(STOP) <= self.guarded
                and not self.running and self.tree is not None and self.locks)
            names = sorted(name for name in self.tree if name.endswith('.lock'))
            require(len(self.locks) == len(names))
            for descriptor, name in zip(self.locks, names):
                require(self.helper.metadata(os.fstat(descriptor)) == self.tree[name]['metadata'])
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            require(guard() is True)
            for service in STOP:
                self.empty(service, self.show(service))
            return True
        return self.helper.inspect_retained_inventory(rows, self.expected['actor'], store, identity, contained, config)

    def record(self, phase, details=None):
        self.sequence += 1
        self.helper.write(STATE / f'{self.sequence:02d}-{phase}.json', self.helper.canonical(dict(
            phase=phase, expected_sha256=self.helper.sha(self.helper.canonical(self.expected)),
            historical_outcomes='unknown', details=details)))

    def inhibit(self, service):
        self.current()
        self.record('inhibit_' + service + '_intent')
        parent = self.guard_path(service).parent
        if not os.path.lexists(parent):
            self.helper.parents(parent)
            parent.mkdir(mode=0o755)
            self.helper.sync(parent.parent)
        self.helper.parents(parent / 'probe')
        self.helper.write(self.guard_path(service), self.guard())
        self.guarded.add(service)
        self.system._command(['daemon-reload'], self.system.clock() + 15)
        self.current()
        self.record('inhibit_' + service + '_done')

    def stop(self, service):
        require(set(STOP) <= self.guarded and service in STOP + BASIC)
        self.current()
        if service == VOICE:
            self.request_voice_shutdown()
        self.record('stop_' + service + '_intent')
        result = 'command_returned'
        try:
            self.system._command(['stop', service], self.system.clock() + 60)
        except Exception as error:
            result = type(error).__name__
        self.empty(service, self.show(service))
        self.stopped.add(service)
        self.current()
        self.stop_outcomes[service] = dict(command=result, unit=self.show(service), drain_verified=False)
        self.record('stop_' + service + '_done', self.stop_outcomes[service])
        if service == VOICE:
            self.voice_outcomes()

    def voice_document(self, name):
        require(name in ('instance.json', 'drain-receipt.json'))
        uid = self.expected['services'][VOICE]['process']['uid']
        info = VOICE_RUN.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == uid and not info.st_mode & 0o022)
        self.helper.parents(VOICE_RUN)
        parent = os.open(VOICE_RUN, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            require(file_identity(os.fstat(parent)) == file_identity(info))
            descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            try:
                before = os.fstat(descriptor)
                require(stat.S_ISREG(before.st_mode) and before.st_uid == uid
                        and stat.S_IMODE(before.st_mode) == 0o600 and before.st_nlink == 1
                        and 0 < before.st_size <= 16384)
                raw = os.read(descriptor, 16385)
                require(len(raw) == before.st_size and file_identity(os.fstat(descriptor)) == file_identity(before)
                        and file_identity(os.stat(name, dir_fd=parent, follow_symlinks=False)) == file_identity(before))
            finally:
                os.close(descriptor)
            require(file_identity(VOICE_RUN.lstat()) == file_identity(info))
        finally:
            os.close(parent)
        return self.helper.decode(raw)

    def request_voice_shutdown(self):
        require(self.voice_transition is None and not os.path.lexists(VOICE_REQUEST))
        instance = self.voice_document('instance.json')
        process = self.expected['services'][VOICE]['process']
        require(instance == self.expected['voice_instance'] and instance['pid'] == process['pid']
                and instance['start_ticks'] == str(process['start_ticks'])
                and instance['invocation_id'] == self.expected['services'][VOICE]['unit']['InvocationID'])
        transition = str(uuid.uuid4())
        self.record('voice_shutdown_request_intent', dict(transition_id=transition))
        self.helper.parents(VOICE_REQUEST)
        descriptor = os.open(VOICE_REQUEST, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), 0o644)
            stream.write(self.helper.canonical(dict(instance, schema='sage.lantern.voice-drain-request.v1',
                                                    transition_id=transition)))
            stream.flush()
            os.fsync(stream.fileno())
        self.helper.sync(VOICE_REQUEST.parent)
        self.voice_transition = transition
        self.current()
        self.record('voice_shutdown_request_done', dict(transition_id=transition))

    def voice_outcomes(self):
        require(VOICE in self.stopped and self.voice_transition is not None)
        value = self.voice_document('drain-receipt.json')
        require(value.get('schema') == 'sage.lantern.voice-drain.v1'
                and value.get('transition_id') == self.voice_transition
                and all(type(value.get(key)) is type(member) and value.get(key) == member
                        for key, member in self.expected['voice_instance'].items()))
        checks = value.get('checks')
        require(type(checks) is dict and checks.get('history_outcome_known') is True
                and checks.get('http_outcome_known') is True)
        self.record('voice_local_outcomes_known', dict(receipt_sha256=self.helper.sha(self.helper.canonical(value)),
            historical_drain_verified=False, queued_writes='not_asserted'))

    def lock(self):
        self.current()
        self.tree = self.helper.tree(self.helper.SUPERVISOR)
        for name, entry in sorted(self.tree.items()):
            if name.endswith('.lock'):
                descriptor = os.open(self.helper.SUPERVISOR / name, os.O_RDONLY | os.O_NOFOLLOW)
                self.locks.append(descriptor)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                require(self.helper.metadata(os.fstat(descriptor)) == entry['metadata'])
        self.current()

    def close(self):
        if self.storage_client is not None:
            self.storage_client.__exit__(None, None, None)
            self.storage_client = None
        if self.storage_descriptor is not None:
            os.close(self.storage_descriptor)
            self.storage_descriptor = None
        for descriptor in self.locks:
            os.close(descriptor)
        self.locks.clear()

    def auth_instance(self):
        return self.expected['services'][AUTH]['unit']['InvocationID']

    def freeze_handoff(self, evidence):
        require(self.stopped == set(STOP) and self.tree is not None and self.locks
                and self.handoff_sha256 is None and evidence == dict(
                    storage_epoch=self.expected['storage_epoch'], workflow_sha256=self.expected['workflow_sha256']))
        self.current()
        from sage_lantern.mesh_daemon import load_config, load_identity
        from sage_lantern.message_contacts import RecipientID
        from sage_lantern.message_storage import MessageStorageProbe
        from sage_sdk import SageClient
        config = load_config()
        require(self.expected['actor'] == dict(chain_id=config['chain_id'], agent_id=config['agent_id']))
        identity, self.storage_descriptor = load_identity(config)
        self.storage_client = SageClient(config['endpoint'], identity, timeout=5, trust_env=False)
        client = self.storage_client.__enter__()
        self.storage_probe = MessageStorageProbe(client, actor=RecipientID(**self.expected['actor']),
                                                 endpoint=config['endpoint'])
        require(self.storage_probe.snapshot().epoch == self.expected['storage_epoch'])
        self.current()
        body = self.helper.canonical(dict(schema='sage.lantern.runtime-maintenance-handoff.v1',
            boot_id=self.expected['boot_id'], expected_sha256=self.helper.sha(self.helper.canonical(self.expected)),
            reconciliation=evidence, current_auth_instance=self.auth_instance(),
            historical_receipts=self.expected['receipts'], historical_outcomes='unknown',
            native=self.expected['services'][NATIVE], candidate_wheel=self.expected['candidate_wheel']))
        self.helper.write(STATE / 'signed-handoff.json', body)
        self.handoff_sha256 = self.helper.sha(body)

    def inhibit_auth(self):
        require(self.handoff_sha256 is not None)
        for service in (AUTH,) + BASIC:
            self.inhibit(service)
        for service in EXTRA:
            if self.expected['extra_services'][service]['LoadState'] == 'loaded':
                self.inhibit(service)

    def stop_auth(self):
        require(self.handoff_sha256 is not None and {AUTH, *BASIC} <= self.guarded)
        self.current()
        self.system._command(['stop', AUTH], self.system.clock() + 60)
        self.empty(AUTH, self.show(AUTH))
        self.post_auth = True
        self.stopped.add(AUTH)
        self.confirm_current_auth_receipt()
        self.current_after_auth()

    def current_after_auth(self):
        if not self.post_auth:
            self.current()
            return
        require(AUTH in self.stopped)
        self.current()
        if AUTH in self.running and self.activation_access is None:
            self.locked()

    def confirm_current_auth_receipt(self):
        require(self.post_auth and AUTH in self.stopped)
        self.empty(AUTH, self.show(AUTH))
        raw = read_receipt(RECEIPTS / (self.auth_instance() + '.json'),
            self.expected['services'][AUTH]['process']['uid'], self.expected['receipt_parents'])
        current_auth_confirmed(raw, self.auth_instance(), self.helper.decode)
        digest = self.helper.sha(raw)
        require(self.auth_final_sha256 in (None, digest))
        self.auth_final_sha256 = digest

    def stop_importer(self, service):
        require(service in BASIC and self.auth_final_sha256 is not None)
        if self.expected['services'][service]['process'] is None:
            self.current_after_auth()
            require(service in self.guarded)
            inactive_display(self.system, service, self.show(service),
                policy_sha256=self.expected['files'].get(str(DISPLAY_ENABLE)))
            self.stopped.add(service)
            self.current_after_auth()
            return
        self.stop(service)

    def verify_all_importers_empty(self):
        require(set(STOP + BASIC + (AUTH,)) == self.stopped and self.auth_final_sha256 is not None)
        self.current_after_auth()
        self.exclude_importers()
        self.current_after_auth()

    def install_pinned_runtime(self):
        self.verify_all_importers_empty()
        require(not self.installed and not self.running
                and self.helper.pinned(self.expected['candidate_wheel']) == self.new_wheel
                and self.helper.pinned(self.expected['installed_wheel']) == self.old_wheel)
        self.runtime_tool.verify_installed(self.old_wheel)
        self.helper.write(STATE / 'runtime-before.whl', self.old_wheel)
        self.current_after_auth()
        self.exclude_importers()
        subprocess.run(['/opt/sage-lantern/venv/bin/python', '-I', '-B', '-m', 'pip', 'install',
            '--no-index', '--no-deps', '--no-cache-dir', '--no-compile', '--force-reinstall',
            self.expected['candidate_wheel']['path']], timeout=60, check=True,
            env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C',
                 'PIP_CONFIG_FILE': '/dev/null', 'PIP_DISABLE_PIP_VERSION_CHECK': '1',
                 'PYTHONDONTWRITEBYTECODE': '1'}, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.runtime_tool.verify_installed(self.new_wheel)
        cache = Path(self.expected['site']) / 'sage_lantern/__pycache__'
        if cache.exists():
            require(stat.S_ISDIR(cache.lstat().st_mode) and cache.lstat().st_uid == 0)
            entries = list(cache.iterdir())
            require(len(entries) <= 1024)
            for entry in entries:
                info = entry.lstat()
                require(entry.suffix == '.pyc' and stat.S_ISREG(info.st_mode)
                        and info.st_uid == 0 and info.st_nlink == 1)
            for entry in entries:
                entry.unlink()
            cache.rmdir()
        self.sources = self.collector.runtime_sources(self.helper, self.expected['candidate_wheel']['path'],
                                                     Path(self.expected['site']))
        self.installed = True
        self.current_after_auth()

    def verify_installed_runtime(self):
        require(self.installed)
        self.runtime_tool.verify_installed(self.new_wheel)
        self.current_after_auth()

    def locked(self):
        import http.client
        require(AUTH in self.running)
        before = self.show(AUTH)
        process = self.process(before)
        def listener():
            rows = [line.split() for line in Path('/proc/net/tcp').read_text().splitlines()[1:]]
            matches = [row for row in rows if len(row) >= 10 and row[3] == '0A'
                       and row[1].endswith(':0C7C')]
            if not matches:
                raise ConnectionRefusedError('auth_listener_not_bound')
            require(len(matches) == 1 and matches[0][1] == '0100007F:0C7C')
            inode = matches[0][9]
            entries = list(Path('/proc', str(process['pid']), 'fd').iterdir())
            require(len(entries) <= 4096)
            owned = False
            for entry in entries:
                try:
                    owned |= os.readlink(entry) == 'socket:[' + inode + ']'
                except FileNotFoundError:
                    pass
            require(owned)
            return inode
        inode = listener()
        connection = http.client.HTTPConnection('127.0.0.1', 3196, timeout=2)
        try:
            connection.request('GET', '/state')
            response = connection.getresponse()
            raw = response.read(16385)
            require(response.status == 200 and len(raw) <= 16384
                    and self.helper.decode(raw).get('locked') is True)
        finally:
            connection.close()
        require(listener() == inode and self.show(AUTH) == before and self.process(before) == process)

    def restored_identity(self, service, value, process):
        original = self.expected['services'][service]['process']
        require(process['argv_sha256'] == original['argv_sha256']
                and process['executable'] == original['executable']
                and value['InvocationID'] != self.expected['services'][service]['unit']['InvocationID'])
        if service == 'sage-lantern-camera.service':
            identity = self.system._show(service, self.system.clock() + 5, ('User', 'DynamicUser'))
            require(identity == {'User': 'sage-lantern-camera', 'DynamicUser': 'yes'} and process['uid'] > 0)
        else:
            require(process['uid'] == original['uid'])

    def restore(self, service):
        try:
            return self._restore(service)
        except BaseException:
            if service in self.restore_attempted:
                outcome = self.contain_restore(service)
                try:
                    self.record('restore_failure_' + service, outcome)
                except BaseException:
                    pass
            raise

    def contain_restore(self, service):
        require(service in self.restore_attempted and service in (AUTH,) + BASIC + (UI,))
        prior_alarm = signal.alarm(0)
        prior_handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        for number in prior_handlers:
            signal.signal(number, signal.SIG_IGN)
        signal.alarm(90)
        result = dict(guards_verified=False, containment_verified=False, shutdown_outcome='unknown')
        try:
            require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
            for name, metadata in self.file_metadata.items():
                require(self.helper.metadata(Path(name).lstat()) == metadata)
            path = self.guard_path(service)
            if not os.path.lexists(path):
                self.helper.write(path, self.guard())
            require(self.helper.public(path) == self.guard())
            self.guarded.add(service)
            self.system._command(['daemon-reload'], self.system.clock() + 10)
            value = self.show(service)
            original = self.expected['services'][service]['unit']
            require(value['FragmentPath'] == original['FragmentPath'] and value['NeedDaemonReload'] == 'no'
                    and set(value['DropInPaths'].split()) == set(original['DropInPaths'].split()) | {str(path)}
                    and value['Restart'] == 'no' and value['RuntimeDirectoryPreserve'] == 'yes'
                    and value['KillMode'] == 'control-group'
                    and value['Slice'] == 'system.slice')
            self.system._guard(service, self.system.clock() + 5, str(STATE))
            result['guards_verified'] = True
            try:
                self.system._command(['stop', service], self.system.clock() + 60)
            except Exception:
                pass
            after = self.show(service)
            require(after['LoadState'] == 'loaded' and after['ActiveState'] in ('inactive', 'failed')
                    and after['MainPID'] == '0' and self.system._no_job(after['Job'])
                    and after['ControlGroup'] in ('', '/system.slice/' + service))
            root = self.system._cgroup('/system.slice/' + service)
            self.system._empty_tree(root, self.system.clock() + 5)
            if root.exists():
                events = dict(line.split() for line in (root / 'cgroup.events').read_text().splitlines())
                require(events.get('populated') == '0')
            require(self.show(service) == after and self.helper.public(path) == self.guard())
            self.system._guard(service, self.system.clock() + 5, str(STATE))
            self.running.pop(service, None)
            result['containment_verified'] = True
        except BaseException:
            pass
        finally:
            signal.alarm(0)
            for number, handler in prior_handlers.items():
                signal.signal(number, handler)
            if prior_alarm:
                signal.alarm(prior_alarm)
        return result

    def _restore(self, service):
        require(self.installed and service in (AUTH,) + BASIC + (UI,)
                and service in self.guarded and service in self.stopped
                and service not in self.running)
        self.current_after_auth()
        self.record('release_' + service + '_intent')
        self.restore_attempted.add(service)
        path = self.guard_path(service)
        require(self.helper.public(path) == self.guard())
        path.unlink()
        self.helper.sync(path.parent)
        self.guarded.remove(service)
        self.system._command(['daemon-reload'], self.system.clock() + 15)
        self.current_after_auth()
        self.record('release_' + service + '_done')
        if self.expected['services'][service]['process'] is None:
            inactive_display(self.system, service, self.show(service),
                policy_sha256=self.expected['files'].get(str(DISPLAY_ENABLE)))
            self.record('preserved_inactive_' + service, dict(started=False))
            return
        self.record('start_' + service + '_intent')
        self.system._command(['start', '--no-block', service], self.system.clock() + 5)
        deadline = self.system.clock() + 20
        observed = None
        while True:
            value = self.show(service)
            require(value['ActiveState'] in ('activating', 'active') and self.system.clock() < deadline)
            if int(value['MainPID']) > 0:
                process, observed = self.starting_process(service, value, observed, deadline)
                if process is None:
                    time.sleep(0.1)
                    continue
                original = self.expected['services'][service]['process']
                require(process['argv_sha256'] == original['argv_sha256'])
                self.restored_identity(service, value, process)
                require(self.system._no_job(value['Job']))
                self.running[service] = dict(invocation=value['InvocationID'], process=process)
                break
            time.sleep(0.1)
        if service == AUTH:
            self.new_auth_receipt = RECEIPTS / (value['InvocationID'] + '.json')
            while True:
                try:
                    self.locked()
                    break
                except ConnectionRefusedError:
                    require(self.system.clock() < deadline)
                    time.sleep(0.1)
        self.current_after_auth()
        self.record('start_' + service + '_done')

    def restore_auth_locked(self):
        self.restore(AUTH)

    def restore_basic_locked(self):
        require(AUTH in self.running)
        for service in BASIC + (UI,):
            self.restore(service)

    def retain_audio_failure(self, owner):
        self.audio_failure_handled = True
        prior_alarm = signal.alarm(0)
        handlers = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        for number in handlers:
            signal.signal(number, signal.SIG_IGN)
        signal.alarm(180)
        outcome = dict(guards_verified=False, containment_verified=False, historical_outcomes='unknown')
        try:
            require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
            for name, original in self.file_metadata.items():
                require(self.helper.metadata(Path(name).lstat()) == original)
            for service in (BROKER, VOICE):
                path = self.guard_path(service)
                if not os.path.lexists(path):
                    self.helper.write(path, self.guard())
                require(self.helper.public(path) == self.guard())
                self.guarded.add(service)
            self.system._command(['daemon-reload'], self.system.clock() + 10)
            for service in (BROKER, VOICE):
                value = self.show(service)
                original = self.expected['services'][service]['unit']
                require(value['FragmentPath'] == original['FragmentPath']
                        and value['Restart'] == 'no' and value['RuntimeDirectoryPreserve'] == 'yes'
                        and value['NeedDaemonReload'] == 'no'
                        and set(value['DropInPaths'].split()) == set(original['DropInPaths'].split()) |
                            {str(self.guard_path(service))})
                self.system._guard(service, self.system.clock() + 5, str(STATE))
            outcome['guards_verified'] = True
            if owner is not None:
                require(owner.contain() is True)
            for service in (BROKER, VOICE):
                value = self.show(service)
                require(value['ActiveState'] in ('inactive', 'failed') and value['MainPID'] == '0'
                        and self.system._no_job(value['Job'])
                        and value['ControlGroup'] in ('', '/system.slice/' + service))
                root = self.system._cgroup('/system.slice/' + service)
                self.system._empty_tree(root, self.system.clock() + 5)
                if root.exists():
                    events = dict(line.split() for line in (root / 'cgroup.events').read_text().splitlines())
                    require(events.get('populated') == '0')
                require(self.show(service) == value)
                self.system._guard(service, self.system.clock() + 5, str(STATE))
            outcome['containment_verified'] = True
        except BaseException:
            pass
        finally:
            signal.alarm(0)
            for number, handler in handlers.items():
                signal.signal(number, handler)
            if prior_alarm:
                signal.alarm(prior_alarm)
        try:
            self.record('audio_failure_retained', outcome)
        except BaseException:
            pass
        return outcome


def prepare(backend, *, approved_capture, wait_seconds):
    require(approved_capture is True and type(wait_seconds) is int and 1 <= wait_seconds <= 300)
    backend.current()
    backend.reconcile()
    require(not STATE.exists())
    backend.helper.parents(STATE)
    STATE.mkdir(mode=0o700)
    backend.helper.sync(STATE.parent)
    backend.helper.write(STATE / 'expected.json', backend.helper.canonical(backend.expected))
    backend.record('intent')
    try:
        for service in STOP:
            backend.inhibit(service)
        backend.reconcile()
        for service in STOP:
            backend.stop(service)
        backend.lock()
        evidence = backend.reconcile()
        backend.current()
        result = dict(producers_contained=True, historical_drain_verified=False,
            historical_outcomes='unknown', replacement_authorized=False,
            remaining_blocker='runtime_importer_quiescence_and_installer_handoff',
            candidate_wheel=backend.expected['candidate_wheel'], reconciliation=evidence,
            native_restarted=False, vault_closed=False, automatic_resume=False)
        backend.record('contained', result)
        handoff(backend)
        return backend.audio_tail.run(backend, backend.expected['audio_tools'],
                                      approved_capture=approved_capture, wait_seconds=wait_seconds)
    except BaseException:
        if not backend.audio_failure_handled:
            from sage_lantern.deployment_bringup import ColdBrokerBootstrap
            backend.retain_audio_failure(ColdBrokerBootstrap(backend.expected['audio_inventory']))
        raise
    finally:
        backend.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--helper', required=True)
    parser.add_argument('--helper-sha256', required=True)
    parser.add_argument('--candidate-sha256', choices=(RETRIEVAL_V1, COMPACT_PROMPT_V1, OWNER_LEDGERS_V2, RADIO_OWNER_COMBINED), default=RETRIEVAL_V1)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--approve-microphone-capture', action='store_true')
    parser.add_argument('--wait-owner-seconds', type=int, default=120)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0)
    require(not args.apply or (args.approve_microphone_capture and 1 <= args.wait_owner_seconds <= 300))
    helper = load(args.helper, args.helper_sha256)
    expected = helper.decode(helper.pinned(dict(path=args.expected, sha256=args.expected_sha256)))
    require(expected['helper'] == dict(path=args.helper, sha256=args.helper_sha256))
    def interrupted(signum, frame):
        raise RuntimeError('maintenance_interrupted')
    for signum in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    signal.alarm(900)
    backend = Maintenance(expected, candidate_sha256=args.candidate_sha256)
    try:
        require(not STATE.exists())
        if args.apply:
            result = prepare(backend, approved_capture=args.approve_microphone_capture, wait_seconds=args.wait_owner_seconds)
        else:
            result = dict(preflight=True, replacement_authorized=False, reconciliation=backend.reconcile())
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.alarm(0)
        backend.close()


if __name__ == '__main__':
    main()
