import copy
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('install_mesh',
    Path(__file__).resolve().parents[1] / 'lantern/install-prepared-fresh-mesh.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def prepared(monkeypatch):
    import sage_lantern.mesh_daemon as mesh
    monkeypatch.setattr(mesh, 'device_policy', lambda config: 'reviewed policy\n')
    raw = {'identity': b'public', 'policy': b'reviewed'}
    config, binding = {'unit_id': 'unit-b'}, 'usb-binding'
    observed = {'boot_id': 'boot', 'storage_epoch': 'epoch:3'}
    encoded = lambda value: json.dumps(value, sort_keys=True).encode()
    artifacts = {'mesh-runtime.json': encoded(config), '20-radio.conf': b'reviewed policy\n'}
    args = SimpleNamespace(sage_binary_sha256='a' * 64)
    receipt = dict(schema='sage.lantern.fresh-mesh-preparation.v1', unit_id='unit-b',
        inputs={name: MODULE.sha(data) for name, data in raw.items()},
        artifacts={name: MODULE.sha(data) for name, data in artifacts.items()}, observation=observed,
        private_identity_verified=True, storage_verified=True, usb_binding_verified=True,
        sage_binary_sha256=args.sage_binary_sha256, current_config_validated=True,
        commissioning_diff_explained=False, radio_handshake_verified=False,
        capture_verified=False, installed=False, activated=False, ready=False)
    events = []
    def inputs(args):
        events.append('inputs')
        return raw, config, binding
    def observe(actual_config, actual_binding):
        events.append('observe')
        assert actual_config == config and actual_binding == binding
        return observed
    return SimpleNamespace(inputs=inputs, observe=observe, encoded=encoded), args, receipt, artifacts, events


@pytest.mark.parametrize('fault', [None, 'unit', 'boot', 'receipt_hash', 'numeric_true', 'readiness', 'artifact',
                                 'stale_epoch', 'changed_inputs', 'changed_observation'])
def test_reuses_actual_preparer_and_never_mints_proof(monkeypatch, fault):
    preparer, args, receipt, artifacts, events = prepared(monkeypatch)
    receipt = copy.deepcopy(receipt)
    if fault == 'receipt_hash':
        receipt['inputs']['identity'] = 'wrong'
    if fault == 'numeric_true':
        receipt['storage_verified'] = 1
    if fault == 'readiness':
        receipt['ready'] = True
    if fault == 'artifact':
        artifacts['20-radio.conf'] = b'extra DeviceAllow'
    if fault == 'stale_epoch':
        receipt['observation']['storage_epoch'] = 'epoch:2'
    if fault == 'changed_inputs':
        original = preparer.inputs
        def changing(args):
            value = original(args)
            return ({'changed': b'new'}, *value[1:]) if events.count('inputs') > 1 else value
        preparer.inputs = changing
    if fault == 'changed_observation':
        original = preparer.observe
        def changing(*args):
            value = original(*args)
            return dict(value, storage_epoch='epoch:4') if events.count('observe') > 1 else value
        preparer.observe = changing
    if fault:
        with pytest.raises(ValueError):
            MODULE.validate_prepared(preparer, args, receipt, artifacts,
                'unit-a' if fault == 'unit' else 'unit-b', 'other' if fault == 'boot' else 'boot')
    else:
        MODULE.validate_prepared(preparer, args, receipt, artifacts, 'unit-b', 'boot')
        assert events == ['inputs', 'observe', 'inputs', 'observe']


@pytest.mark.parametrize('fault', [None, 'before', 'first_write', 'after_first', 'second_write'])
def test_apply_exact_absent_destinations_with_retained_partial_state(monkeypatch, fault):
    installer = MODULE.Installer.__new__(MODULE.Installer)
    installer.installed = set()
    installer.artifacts = {'mesh-runtime.json': b'config', '20-radio.conf': b'policy'}
    events = []
    def current():
        events.append('check')
        if fault == 'before' or fault == 'after_first' and installer.installed:
            raise ValueError
    def write(path, raw, mode):
        events.append((str(path), raw, mode))
        if fault == 'first_write' and raw == b'config' or fault == 'second_write' and raw == b'policy':
            raise FileExistsError
    installer.current = current
    monkeypatch.setattr(MODULE, 'write_new', write)
    if fault:
        with pytest.raises((ValueError, FileExistsError)):
            installer.apply(events.append)
        assert '20-radio.conf_verified' not in events
    else:
        result = installer.apply(events.append)
        assert result['installed'] is True and result['activated'] is False
        assert result['services_started'] is False and result['capture_verified'] is False
        assert installer.installed == set(MODULE.DESTINATIONS)
        assert [event for event in events if isinstance(event, tuple)] == [
            ('/etc/sage-lantern/mesh-runtime.json', b'config', 0o600),
            ('/etc/systemd/system/sage-lantern-mesh-runtime.service.d/20-radio.conf', b'policy', 0o644)]
    if fault in ('after_first', 'second_write'):
        assert installer.installed == {'mesh-runtime.json'}


def test_write_never_overwrites_or_follows_destination_link(monkeypatch, tmp_path):
    monkeypatch.setattr(MODULE, 'directory', lambda path: None)
    path = tmp_path / 'mesh-runtime.json'
    MODULE.write_new(path, b'original', 0o600)
    with pytest.raises(FileExistsError):
        MODULE.write_new(path, b'changed', 0o600)
    link = tmp_path / 'link'
    link.symlink_to(path)
    with pytest.raises(FileExistsError):
        MODULE.write_new(link, b'changed', 0o600)
    assert path.read_bytes() == b'original'


def test_duplicate_pins_reject():
    with pytest.raises(ValueError):
        MODULE.decode('{"unit_id":"unit-a","unit_id":"unit-b"}')


@pytest.mark.parametrize('unit,mode,allowed', [('unit-a', None, True), ('unit-b', None, True),
    ('unit-b', 'fresh-b-v1', True), ('unit-a', 'fresh-b-v1', False), ('unit-b', 'anything', False)])
def test_b_mode_is_explicit_not_an_a_bypass(unit, mode, allowed):
    expected = {'unit_id': unit}
    if mode is not None:
        expected['containment'] = mode
    if allowed:
        assert MODULE.fresh_b(expected) == (mode == 'fresh-b-v1')
    else:
        with pytest.raises(ValueError):
            MODULE.fresh_b(expected)


def b_system(monkeypatch, *, fault=None):
    pins = {'/etc/systemd/system/' + service: MODULE.sha(b'unit') for service in MODULE.SERVICES + MODULE.B_SERVICES}
    expected = {'boot_id': 'test-boot', 'service_files': pins}
    monkeypatch.setattr(Path, 'read_text', lambda path: 'test-boot')
    monkeypatch.setattr(MODULE, 'b_no_prior_lifetime', lambda: None)
    monkeypatch.setattr(MODULE, 'b_other_producers', lambda: None)
    monkeypatch.setattr(MODULE, 'read', lambda path, mode=None: MODULE.b_guard_bytes() if str(path).endswith('zzzz-b-first-mesh.conf') else b'unit')
    events = []
    class System:
        def _show(self, service, deadline, fields):
            value = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', Job='', ControlGroup='',
                         Slice='system.slice', KillMode='control-group', Restart='no',
                         FragmentPath='/etc/systemd/system/' + service,
                         DropInPaths=str(MODULE.b_guard_path(service)), NeedDaemonReload='no')
            if fault == 'active': value['ActiveState'] = 'active'
            if fault == 'pid': value['MainPID'] = '12'
            if fault == 'job': value['Job'] = '15'
            if fault == 'restart': value['Restart'] = 'on-failure'
            if fault == 'extra_dropin': value['DropInPaths'] += ' /extra.conf'
            if fault == 'reload': value['NeedDaemonReload'] = 'yes'
            return value
        def _guard(self, service, deadline, fence):
            events.append(('guard', service, fence))
            if fault == 'missing_guard': raise ValueError
        def _cgroup(self, path): return path
        def _empty_tree(self, path, deadline):
            events.append(('empty', path))
            if fault == 'populated': raise ValueError
        def _sleep_idle(self, deadline):
            if fault == 'sleep_job': raise ValueError
    return System(), expected, events


@pytest.mark.parametrize('fault', [None, 'active', 'pid', 'job', 'restart', 'extra_dropin',
                                 'reload', 'missing_guard', 'populated', 'sleep_job'])
def test_b_loaded_containment_checks_all_four_producers(monkeypatch, fault):
    system, expected, events = b_system(monkeypatch, fault=fault)
    if fault:
        with pytest.raises(ValueError):
            MODULE.b_services(system, expected, 100, MODULE.SERVICES + MODULE.B_SERVICES, loaded=True)
    else:
        MODULE.b_services(system, expected, 100, MODULE.SERVICES + MODULE.B_SERVICES, loaded=True)
        assert len([event for event in events if event[0] == 'guard']) == 4
        assert len([event for event in events if event[0] == 'empty']) == 4
        assert all(event[2] == str(MODULE.B_GUARD) for event in events if event[0] == 'guard')


@pytest.mark.parametrize('rows', [[{'unknown': True}], [{'kind': 'public_proposal'}], [{'kind': 'conversation_session'}]])
def test_fresh_b_refuses_any_workflow_not_just_mesh(monkeypatch, rows):
    import sage_lantern.mesh_daemon as mesh
    import sage_lantern.device_access as access
    import sage_lantern.message_storage as storage
    import sage_lantern.workflow_store as workflows
    import sage_sdk
    monkeypatch.setattr(mesh, 'load_identity', lambda config: ('identity', 123))
    monkeypatch.setattr(MODULE.os, 'close', lambda descriptor: None)
    monkeypatch.setattr(access, 'access_epoch', lambda: 'epoch')
    class Client:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(sage_sdk, 'SageClient', Client)
    monkeypatch.setattr(storage, 'MessageStorageProbe', lambda *args, **kwargs: SimpleNamespace(snapshot=lambda: SimpleNamespace(epoch='storage')))
    monkeypatch.setattr(workflows, 'WorkflowStore', lambda *args, **kwargs: SimpleNamespace(scan=lambda **kwargs: rows))
    with pytest.raises(ValueError):
        MODULE.b_empty_workflows({'endpoint': 'http://127.0.0.1:8080', 'chain_id': 'chain', 'agent_id': '12' * 32}, 'storage', lambda: None)


def test_release_requires_real_install_completion(monkeypatch):
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: False)
    monkeypatch.setattr(MODULE, 'read', lambda *args: b'{"schema":"invented"}')
    with pytest.raises(ValueError):
        MODULE.release_b({'unit_id': 'unit-b', 'containment': 'fresh-b-v1'})


def test_b_guard_does_not_reuse_native_maintenance():
    assert b'cold-native-update' not in MODULE.b_guard_bytes()
    assert b'ConditionPathExists=!/var/lib/sage-lantern-b-mesh-containment' in MODULE.b_guard_bytes()
    assert b'Restart=no' in MODULE.b_guard_bytes()


@pytest.mark.parametrize('fault', [None, 'second_write', 'reload', 'post_fence_work'])
def test_b_establish_real_files_and_retained_partial(monkeypatch, tmp_path, fault):
    import sage_lantern.suspend_system as system_module
    fence = tmp_path / 'fence'
    monkeypatch.setattr(MODULE, 'B_GUARD', fence)
    monkeypatch.setattr(MODULE, 'STATE', tmp_path / 'install')
    monkeypatch.setattr(MODULE, 'directory', lambda *args: None)
    monkeypatch.setattr(MODULE, 'b_guard_path', lambda service: tmp_path / 'units' / (service + '.d') / 'guard.conf')
    (tmp_path / 'units').mkdir()
    monkeypatch.setattr(MODULE, 'DESTINATIONS', {'mesh-runtime.json': (tmp_path / 'mesh.json', 0o600), '20-radio.conf': (tmp_path / 'radio.conf', 0o644)})
    config = {'unit_id': 'unit-b'}
    preparer = SimpleNamespace(inputs=lambda args: ({}, config, 'binding'),
        observe=lambda *args: {'boot_id': 'boot', 'storage_epoch': 'epoch'})
    monkeypatch.setattr(MODULE, 'load_preparer', lambda expected: preparer)
    events = []
    class System:
        def _remaining(self, deadline): pass
        def _command(self, args, deadline):
            events.append(args)
            if fault == 'reload': raise ValueError('reload')
    monkeypatch.setattr(system_module, 'SuspendSystem', System)
    def services(system, expected, deadline, installed, **kwargs):
        for service in installed:
            assert MODULE.b_guard_path(service).read_bytes() == MODULE.b_guard_bytes()
        events.append(('checked', len(installed), kwargs.get('loaded', False)))
    monkeypatch.setattr(MODULE, 'b_services', services)
    scans = []
    def scan(config, epoch, guard):
        guard(); scans.append(epoch)
        if fault == 'post_fence_work' and len(scans) == 2: raise ValueError('unknown work')
    monkeypatch.setattr(MODULE, 'b_empty_workflows', scan)
    original = MODULE.write_new
    def write(path, raw, mode):
        if fault == 'second_write' and path == MODULE.b_guard_path(MODULE.SERVICES[1]):
            raise OSError('disk failure')
        original(path, raw, mode)
    monkeypatch.setattr(MODULE, 'write_new', write)
    expected = {'unit_id': 'unit-b', 'containment': 'fresh-b-v1', 'boot_id': 'boot',
                'preparation_args': {'manifest': '/manifest', 'radio_inventory': '/radio', 'current_sage_policy': '/policy'}}
    if fault:
        with pytest.raises((ValueError, OSError)):
            MODULE.establish_b(expected, apply=True)
        assert fence.exists() and (fence / 'expected.json').exists()
        assert not (fence / 'complete.json').exists()
    else:
        result = MODULE.establish_b(expected, apply=True)
        assert result['contained'] is True
        assert scans == ['epoch', 'epoch']
        assert ['daemon-reload'] in events
        assert (fence / 'complete.json').exists()
    assert not (tmp_path / 'mesh.json').exists()


def test_fresh_b_inspection_dispatch_does_not_construct_a_installer(monkeypatch, capsys):
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: False)
    raw = b'{"unit_id":"unit-b","containment":"fresh-b-v1"}'
    monkeypatch.setattr(MODULE, 'read', lambda path: raw)
    called = []
    monkeypatch.setattr(MODULE, 'establish_b', lambda expected, apply: called.append(apply) or {'checked': True})
    monkeypatch.setattr(MODULE, 'Installer', lambda expected: pytest.fail('wrong path'))
    MODULE.main(['--expected', '/expected', '--expected-sha256', MODULE.sha(raw), '--establish-fresh-b-containment'])
    assert called == [False]


@pytest.mark.parametrize('fault', [None, 'changed_guard'])
def test_b_release_archives_exact_guards_without_removing_evidence(monkeypatch, tmp_path, fault):
    fence, state = tmp_path / 'fence', tmp_path / 'state'
    fence.mkdir();state.mkdir()
    monkeypatch.setattr(MODULE, 'B_GUARD', fence)
    monkeypatch.setattr(MODULE, 'STATE', state)
    monkeypatch.setattr(MODULE, 'directory', lambda *args: None)
    monkeypatch.setattr(MODULE, 'b_guard_path', lambda service: tmp_path / (service + '.guard'))
    monkeypatch.setattr(MODULE, 'read', lambda path, mode=None: Path(path).read_bytes())
    expected = {'unit_id': 'unit-b', 'containment': 'fresh-b-v1', 'boot_id': 'boot'}
    receipt = dict(schema='sage.lantern.b-mesh-installed.v1', expected_sha256=MODULE.sha(json.dumps(expected, sort_keys=True).encode()))
    (state / 'complete.json').write_text(json.dumps(receipt))
    destinations = {'mesh-runtime.json': (tmp_path / 'mesh', 0o600), '20-radio.conf': (tmp_path / 'radio', 0o644)}
    monkeypatch.setattr(MODULE, 'DESTINATIONS', destinations)
    for path, _ in destinations.values(): path.write_bytes(b'artifact')
    for service in MODULE.SERVICES + MODULE.B_SERVICES:
        MODULE.b_guard_path(service).write_bytes(MODULE.b_guard_bytes())
    if fault:
        MODULE.b_guard_path(MODULE.SERVICES[0]).write_bytes(b'changed')
    commands = []
    system = SimpleNamespace(_command=lambda args, deadline: commands.append(args),
        _show=lambda *args: {'DropInPaths': '', 'NeedDaemonReload': 'no'})
    preparer = SimpleNamespace(inputs=lambda args: ({}, {}, {}), observe=lambda *args: {'storage_epoch': 'epoch'})
    installer = SimpleNamespace(current=lambda: None, system=system, deadline=100, args=None,
        preparer=preparer, receipt_raw=b'{}', artifacts={name: b'artifact' for name in destinations})
    monkeypatch.setattr(MODULE, 'Installer', lambda *args, **kwargs: installer)
    monkeypatch.setattr(MODULE, 'b_services', lambda *args, **kwargs: None)
    monkeypatch.setattr(MODULE, 'validate_prepared', lambda *args: None)
    monkeypatch.setattr(MODULE, 'b_empty_workflows', lambda config, epoch, guard: guard())
    if fault:
        with pytest.raises(ValueError): MODULE.release_b(expected, apply=True)
        assert MODULE.b_guard_path(MODULE.SERVICES[0]).read_bytes() == b'changed'
        assert not (fence / 'released.json').exists()
    else:
        assert MODULE.release_b(expected, apply=True)['released'] is True
        assert all(not MODULE.b_guard_path(service).exists() for service in MODULE.SERVICES + MODULE.B_SERVICES)
        assert len(list(fence.glob('*.released-guard'))) == 4
        assert commands == [['daemon-reload']]
        assert fence.exists() and (state / 'complete.json').exists()


@pytest.mark.parametrize('fault', [None, 'empty_owner_lock', 'items', 'unexpected_file', 'legacy_database',
    'linked_preferences', 'hardlinked_preferences', 'preferences_mode', 'directory_mode',
    'lock_payload', 'invalid_generation', 'numeric_boolean', 'extra_field', 'wrong_uid', 'wrong_gid'])
def test_real_fresh_installer_history_layout(tmp_path, monkeypatch, fault):
    if not hasattr(os, 'listxattr'):
        monkeypatch.setattr(os, 'listxattr', lambda path: [], raising=False)
    spec = importlib.util.spec_from_file_location('real_app_installer',
        Path(__file__).resolve().parents[1] / 'lantern/install-fleet-appliance-local.py')
    app = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(app)
    uid, gid = (os.getuid(), os.getgid()) if os.getuid() else (994, 993)
    parent = tmp_path / 'var/lib/sage-lantern'
    parent.mkdir(parents=True)
    app.provision_conversation_owner(tmp_path, uid, gid)
    history = parent / 'history'
    preferences = history / 'history.json'
    original = preferences.read_bytes()
    if fault in ('items', 'invalid_generation', 'numeric_boolean', 'extra_field'):
        value = json.loads(original)
        if fault == 'items': value['items'] = [{'unknown': True}]
        if fault == 'invalid_generation': value['generation'] = 'not-a-uuid'
        if fault == 'numeric_boolean': value['enabled'] = 1
        if fault == 'extra_field': value['pending'] = []
        preferences.write_text(json.dumps(value))
    if fault == 'unexpected_file': (history / 'photo.jpg').write_bytes(b'photo')
    if fault == 'legacy_database': (history / 'conversations-v1/state.db').write_bytes(b'db')
    if fault == 'linked_preferences':
        preferences.rename(history / 'original.json'); preferences.symlink_to(history / 'original.json')
    if fault == 'hardlinked_preferences': os.link(preferences, parent / 'another-link')
    if fault == 'preferences_mode': preferences.chmod(0o644)
    if fault == 'directory_mode': history.chmod(0o755)
    if fault in ('empty_owner_lock', 'lock_payload'):
        lock = history / 'conversations-v1/owner.lock'
        lock.write_bytes(b'' if fault == 'empty_owner_lock' else b'unknown')
        lock.chmod(0o600)
        if os.getuid() == 0: os.chown(lock, uid, gid)
    if fault == 'wrong_uid': uid += 1
    if fault == 'wrong_gid': gid += 1
    if fault in (None, 'empty_owner_lock'):
        MODULE.b_fresh_history(history, uid, gid)
        assert preferences.read_bytes() == original
    else:
        with pytest.raises((ValueError, OSError)):
            MODULE.b_fresh_history(history, uid, gid)
