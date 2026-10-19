import importlib.util
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1] / 'lantern'


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


phase = load('provision-postcommission-startup')
handoff = load('handoff-fresh-audio-lifetime')
BOOT = '11111111-1111-4111-8111-111111111111'


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'])
def test_fresh_install_does_not_assume_enable_creates_state_directory(monkeypatch, tmp_path, unit):
    installer = load('install-startup-coordinator')
    coordinator = load('startup-coordinator')
    backend, snapshot = fixture_backend(monkeypatch, tmp_path, unit)
    monkeypatch.setattr(coordinator, 'COORDINATOR', tmp_path / 'coordinator')
    monkeypatch.setattr(coordinator, 'CONFIG', tmp_path / 'installed-config')
    monkeypatch.setattr(coordinator, 'JOURNALS', tmp_path / 'absent-state-parent' / 'sage-lantern-startup')
    monkeypatch.setattr(installer, 'SERVICE', tmp_path / 'installed-service')
    monkeypatch.setattr(installer, 'load_coordinator', lambda digest: coordinator)
    for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel'):
        path = tmp_path / name
        path.write_bytes(name.encode())
        backend.backend.tools[name] = dict(path=str(path), sha256=handoff.sha(name.encode()))
    documents = handoff.documents(snapshot, backend.backend.tools, b'receipt')
    for path, raw in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    def pinned(pin):
        raw = Path(pin['path']).read_bytes()
        assert handoff.sha(raw) == pin['sha256']
        return raw
    monkeypatch.setattr(coordinator, 'pinned', pinned)
    monkeypatch.setattr(coordinator, 'verify_wheel', lambda pin: pinned(pin))
    original_lstat = Path.lstat
    def root_directories(path):
        actual = original_lstat(path)
        if path.is_dir():
            return SimpleNamespace(st_uid=0, st_mode=0o40755)
        return actual
    monkeypatch.setattr(Path, 'lstat', root_directories)
    monkeypatch.setattr(installer.os, 'geteuid', lambda: 0)
    service = tmp_path / 'service-input'
    service.write_bytes(b'[Service]\nExecStart=/opt/sage-lantern/venv/bin/python -I -B /opt/sage-lantern/libexec/startup-coordinator.py\nStateDirectory=sage-lantern-startup\nStateDirectoryMode=0700\n')
    config = handoff.HANDOFF / BOOT / 'startup.json'
    monkeypatch.setattr(sys, 'argv', ['installer', '--config', str(config),
        '--config-sha256', handoff.sha(config.read_bytes()), '--service', str(service),
        '--service-sha256', handoff.sha(service.read_bytes()), '--coordinator-sha256',
        backend.backend.tools['coordinator']['sha256'], '--apply'])
    calls = []
    monkeypatch.setattr(installer.subprocess, 'run', lambda command, **kwargs: calls.append(command))
    assert not os.path.lexists(coordinator.JOURNALS.parent)
    installer.main()
    assert coordinator.CONFIG.read_bytes() == config.read_bytes()
    assert installer.SERVICE.read_bytes() == service.read_bytes()
    assert calls == [['/usr/bin/systemctl', 'daemon-reload'],
        ['/usr/bin/systemctl', 'disable', 'sage-lantern-llm.service'],
        ['/usr/bin/systemctl', 'enable', installer.SERVICE.name]]
    assert not os.path.lexists(coordinator.JOURNALS.parent)
    with pytest.raises(FileNotFoundError):
        coordinator.Journal(BOOT, 'a' * 64)
    assert not os.path.lexists(coordinator.JOURNALS.parent)


def fixture_backend(monkeypatch, tmp_path, unit='unit-b'):
    monkeypatch.setattr(handoff, 'HANDOFF', tmp_path / 'handoff')
    monkeypatch.setattr(handoff, 'LIFETIMES', tmp_path / 'lifetimes')
    tools = dict(unit_id=unit, **{name: dict(path='/opt/' + name, sha256='a'*64)
        for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel')})
    snapshot = dict(schema='sage.lantern.fresh-audio-handoff.v1', unit_id=unit, boot_id=BOOT,
        bootstrap_sha256=handoff.sha(b'receipt'), baseline={'actor': unit}, access_epoch=1)
    backend = object.__new__(phase.Phase)
    backend.handoff = handoff
    backend.dependencies = dict(unit_id=unit)
    backend.dependencies_raw = b'dependencies'
    backend.integrity = lambda: None
    backend.absent_service = lambda: None
    backend.service = b'service'
    backend.backend = SimpleNamespace(tools=tools, snapshot=lambda: (dict(snapshot), b'receipt'),
        coordinator=SimpleNamespace(validate_full_config=lambda config: None))
    backend.installer = SimpleNamespace(SERVICE=tmp_path/'service', startup=SimpleNamespace(CONFIG=tmp_path/'config'))
    return backend, snapshot


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'])
def test_real_inspect_readonly_independent_documents(monkeypatch, tmp_path, unit):
    backend, snapshot = fixture_backend(monkeypatch, tmp_path, unit)
    result = backend.inspect('handoff')
    assert result['snapshot']['unit_id'] == unit
    assert result['handoff_receipt_sha256'] is None
    assert list(tmp_path.iterdir()) == []


def test_foreign_unit_inspect_refused(monkeypatch, tmp_path):
    backend, snapshot = fixture_backend(monkeypatch, tmp_path)
    backend.dependencies['unit_id'] = 'unit-a'
    with pytest.raises(ValueError):
        backend.inspect('handoff')
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('destination', ['service', 'config', 'handoff', 'lifetimes'])
def test_existing_state_refused(monkeypatch, tmp_path, destination):
    backend, snapshot = fixture_backend(monkeypatch, tmp_path)
    path = tmp_path/destination
    if destination in ('handoff', 'lifetimes'):
        path = path/BOOT
        path.mkdir(parents=True)
    else:
        path.write_bytes(b'existing')
    with pytest.raises(ValueError):
        backend.inspect('handoff')


def test_real_handoff_receipt_required_before_install(monkeypatch, tmp_path):
    backend, snapshot = fixture_backend(monkeypatch, tmp_path)
    documents = handoff.documents(snapshot, backend.backend.tools, b'receipt')
    directory = handoff.HANDOFF/BOOT
    directory.mkdir(parents=True)
    original = dict(snapshot, access_epoch=0)
    (directory/'intent.json').write_bytes(handoff.canonical(original))
    for path, raw in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    complete = dict(schema='sage.lantern.fresh-audio-handoff-result.v1', unit_id='unit-b', boot_id=BOOT,
        files={str(path): handoff.sha(raw) for path, raw in documents.items()},
        services_started=False, services_enabled=False, reboot_accepted=False)
    (directory/'complete.json').write_bytes(handoff.canonical(complete))
    monkeypatch.setattr(handoff, 'trusted', lambda path: Path(path).read_bytes())
    calls = []
    backend.installer.preflight = lambda config, service: calls.append((config, service))
    result = backend.inspect('install-startup')
    assert result['handoff_receipt_sha256'] == handoff.sha(handoff.canonical(complete))
    assert len(calls) == 1
    (directory/'complete.json').write_bytes(b'{}')
    with pytest.raises(ValueError):
        backend.inspect('install-startup')


@pytest.mark.parametrize('failure', ['install', 'snapshot', 'complete'])
def test_partial_install_preserves_failure_without_retry(failure):
    expected = dict(phase='install-startup', snapshot={'unit_id': 'unit-b'})
    calls = []
    def record(name, value):
        calls.append(name)
        if name == failure:
            raise OSError('private error not recorded')
    def install(value):
        calls.append('install')
        if failure == 'install':
            raise TimeoutError
    backend = SimpleNamespace(inspect=lambda value: expected, claim=lambda *args: calls.append('claim'),
        install=install, integrity=lambda: None, record=record,
        backend=SimpleNamespace(snapshot=lambda: ({'changed': True} if failure == 'snapshot' else expected['snapshot'], b'')))
    with pytest.raises((ValueError, OSError, TimeoutError)):
        phase.apply(backend, expected, BOOT)
    assert calls.count('install') == 1 and calls[-1] == 'failed'


@pytest.mark.parametrize('field,value', [('MainPID', '12'), ('ActiveState', 'active'), ('Job', '8'),
    ('LoadState', 'not-found'), ('NeedDaemonReload', 'yes'), ('ControlGroup', '/foreign')])
def test_real_systemd_parser_rejects_noninactive(field, value):
    from sage_lantern.suspend_system import SuspendSystem
    state = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', Job='0',
        ControlGroup='', Slice='system.slice', NeedDaemonReload='no', DropInPaths='', Transient='no',
        FragmentPath='/etc/systemd/system/sage-lantern-startup.service')
    state[field] = value
    system = SuspendSystem()
    system.run = lambda *args, **kwargs: SimpleNamespace(returncode=0,
        stdout=''.join(f'{key}={entry}\n' for key, entry in state.items()).encode())
    with pytest.raises(ValueError):
        phase.startup_inactive(system)


def test_real_systemd_parser_empty_cgroup(monkeypatch):
    from sage_lantern.suspend_system import SuspendSystem
    state = dict(LoadState='loaded', ActiveState='inactive', MainPID='0', Job='0',
        ControlGroup='', Slice='system.slice', NeedDaemonReload='no', DropInPaths='', Transient='no',
        FragmentPath='/etc/systemd/system/sage-lantern-startup.service')
    system = SuspendSystem()
    system.run = lambda *args, **kwargs: SimpleNamespace(returncode=0,
        stdout=''.join(f'{key}={entry}\n' for key, entry in state.items()).encode())
    system.list_directories = lambda path: []
    system.read_file = lambda *args: b''
    phase.startup_inactive(system)
    system.read_file = lambda *args: b'123\n'
    with pytest.raises(ValueError):
        phase.startup_inactive(system)


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'])
def test_generated_handoff_real_installer_preflight(monkeypatch, tmp_path, unit):
    installer = load('install-startup-coordinator')
    coordinator = load('startup-coordinator')
    backend, snapshot = fixture_backend(monkeypatch, tmp_path, unit)
    coordinator_path = tmp_path/'coordinator'
    monkeypatch.setattr(coordinator, 'COORDINATOR', coordinator_path)
    monkeypatch.setattr(coordinator, 'CONFIG', tmp_path/'installed-config')
    monkeypatch.setattr(installer, 'SERVICE', tmp_path/'installed-service')
    monkeypatch.setattr(installer, 'startup', coordinator)
    for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel'):
        path = tmp_path/name
        path.write_bytes(name.encode())
        backend.backend.tools[name] = dict(path=str(path), sha256=handoff.sha(name.encode()))
    documents = handoff.documents(snapshot, backend.backend.tools, b'receipt')
    def pinned(pin):
        raw = Path(pin['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == pin['sha256']
        return raw
    monkeypatch.setattr(coordinator, 'pinned', pinned)
    wheel_checks = []
    monkeypatch.setattr(coordinator, 'verify_wheel', lambda pin: wheel_checks.append(pinned(pin)))
    service = b'ExecStart=/opt/sage-lantern/venv/bin/python -I -B /opt/sage-lantern/libexec/startup-coordinator.py\n'
    config = documents[handoff.HANDOFF/BOOT/'startup.json']
    with pytest.raises(FileNotFoundError):
        installer.preflight(config, service)
    for path, raw in documents.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    original_lstat = Path.lstat
    def root_directories(path):
        actual = original_lstat(path)
        if path.is_dir():
            return SimpleNamespace(st_uid=0, st_mode=0o40755)
        return actual
    monkeypatch.setattr(Path, 'lstat', root_directories)
    assert installer.preflight(config, service)['schema'] == 'sage.lantern.startup-coordinator.v1'
    assert wheel_checks == [b'runtime_wheel']
    next(path for path in documents if path.name == 'new-lifetime.json').write_bytes(b'changed')
    with pytest.raises(AssertionError):
        installer.preflight(config, service)


@pytest.mark.parametrize('change', [{}, {'FragmentPath': '/run/systemd/system/sage-lantern-startup.service'},
    {'DropInPaths': '/run/override.conf'}, {'Transient': 'yes'}, {'LoadState': 'loaded'}, {'Job': '3'}])
def test_real_absent_unit_contract(change):
    from sage_lantern.suspend_system import SuspendSystem
    state = dict(LoadState='not-found', ActiveState='inactive', MainPID='0', Job='0',
        ControlGroup='', Slice='', NeedDaemonReload='no', DropInPaths='', Transient='no', FragmentPath='')
    state.update(change)
    system = SuspendSystem()
    system.run = lambda *args, **kwargs: SimpleNamespace(returncode=0,
        stdout=''.join(f'{key}={entry}\n' for key, entry in state.items()).encode())
    system.list_directories = lambda path: []
    system.read_file = lambda *args: b''
    if change:
        with pytest.raises(ValueError):
            phase.startup_inactive(system, installed=False)
    else:
        phase.startup_inactive(system, installed=False)
