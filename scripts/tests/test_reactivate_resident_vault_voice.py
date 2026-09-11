import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


SPEC = importlib.util.spec_from_file_location('reactivation', Path(__file__).resolve().parents[1] /
                                            'lantern/reactivate-resident-vault-voice.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('failure', [None, 'current', 'relocate_supervisor', 'relocate_voice', 'relocate_bootstrap',
                                   'reset_broker', 'reset_voice', 'release_broker', 'release_voice',
                                   'reconcile', 'fresh', 'start'])
def test_exact_transition_never_retries_or_starts_voice_directly(failure):
    calls = []
    def action(name):
        calls.append(name)
        if name == failure:
            raise ValueError
        if name == 'start':
            return {'broker_active': True, 'voice_active': True}
    names = {MODULE.SUPERVISOR: 'supervisor', MODULE.VOICE_RUN: 'voice', MODULE.BOOTSTRAP: 'bootstrap',
             MODULE.BROKER: 'broker', MODULE.VOICE: 'voice'}
    backend = SimpleNamespace(current=lambda: action('current'),
        relocate=lambda root: action('relocate_' + names[root]),
        reset_service=lambda service: action('reset_' + names[service]),
        release=lambda service: action('release_' + names[service]), reconcile=lambda: action('reconcile'),
        owner=SimpleNamespace(fresh=lambda: action('fresh'), start=lambda: action('start')))
    def record(phase, details=None):
        calls.append(phase)
    if failure:
        with pytest.raises(ValueError):
            MODULE.transition(backend, record)
        assert calls[-1] == failure and 'new_lifetime_start_done' not in calls
    else:
        result = MODULE.transition(backend, record)
        operations = [name for name in calls if name in {'relocate_supervisor', 'relocate_voice', 'relocate_bootstrap',
            'reset_broker', 'reset_voice', 'release_broker', 'release_voice', 'reconcile', 'fresh', 'start'}]
        assert operations == ['relocate_supervisor', 'relocate_voice', 'relocate_bootstrap',
            'reset_broker', 'reset_voice', 'release_broker', 'release_voice', 'reconcile', 'fresh', 'start']
        assert calls.count('start') == 1
        assert result['historical_work_disposition'] == 'unknown' and result['old_drain_verified'] is False
        assert result['recapture_performed'] is False and result['automatic_retry'] is False


@pytest.mark.parametrize('mask', range(64))
def test_reset_coherent_gc_or_retained_exit_only(mask):
    before = {field: '1' for field in MODULE.EXIT_FIELDS}
    before.update(ActiveState='failed', SubState='failed', MainPID='0', NRestarts='0', Result='exit-code')
    value = dict(before, ActiveState='inactive', SubState='dead', Result='success')
    changes = dict(ExecMainPID='0', InvocationID='', ExecMainStartTimestampMonotonic='0',
                   ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0')
    for index, (field, cleared) in enumerate(changes.items()):
        if mask & (1 << index):
            value[field] = cleared
    if mask in (0, 63):
        MODULE.exited(value, before, True)
    else:
        with pytest.raises(ValueError):
            MODULE.exited(value, before, True)
    with pytest.raises(ValueError):
        MODULE.exited(value, before, False)


@pytest.mark.parametrize('command', [['start', MODULE.VOICE], ['start', MODULE.BROKER], ['stop', MODULE.BROKER],
                                    ['restart', MODULE.SAGE], ['reset-failed', MODULE.AUTH]])
def test_direct_command_surface_never_starts_or_stops_services(command):
    backend = MODULE.Native.__new__(MODULE.Native)
    with pytest.raises(ValueError):
        backend.command(command)


@pytest.mark.parametrize('fault', [None, 'existing_destination', 'changed_archive'])
def test_same_filesystem_relocation_preserves_bytes_and_original_evidence(tmp_path, monkeypatch, fault):
    root = tmp_path / 'original'
    root.mkdir()
    source = root / 'fence.json'
    source.write_bytes(b'retained unknown outcome')
    destination = tmp_path / 'archive'
    if fault == 'existing_destination':
        destination.mkdir()
    backend = MODULE.Native.__new__(MODULE.Native)
    backend.destinations = {root: destination}
    backend.moved = set()
    backend.receipt = {'preserved_records': {str(source): {'sha256': MODULE.sha(source.read_bytes())}}}
    calls = []
    def current():
        calls.append('verify')
        if fault == 'changed_archive':
            raise ValueError
    backend.current = current
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    if fault:
        with pytest.raises(ValueError):
            backend.relocate(root)
        assert source.read_bytes() == b'retained unknown outcome' and not backend.moved
    else:
        original_inode = source.stat().st_ino
        backend.relocate(root)
        assert not root.exists() and backend.moved == {root}
        assert (destination / 'fence.json').read_bytes() == b'retained unknown outcome'
        assert (destination / 'fence.json').stat().st_ino == original_inode
        assert calls == ['verify', 'verify']


def test_release_copies_exact_guard_then_unlinks_and_reloads(tmp_path, monkeypatch):
    guard = tmp_path / 'guard.conf'
    guard.write_bytes(b'reviewed inhibition')
    state = tmp_path / 'state'
    state.mkdir()
    monkeypatch.setattr(MODULE, 'STATE', state)
    monkeypatch.setattr(MODULE, 'trusted', lambda path: path.read_bytes())
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    backend = MODULE.Native.__new__(MODULE.Native)
    backend.reset = {MODULE.BROKER, MODULE.VOICE}
    backend.released = set()
    backend.recovery = SimpleNamespace(GUARDS={MODULE.BROKER: guard})
    backend.receipt = {'guards': {str(guard): MODULE.sha(guard.read_bytes())}}
    calls = []
    backend.current = lambda: calls.append('current')
    backend.reconcile = lambda: calls.append('signed_reconcile')
    backend.command = lambda command: calls.append(command)
    backend.release(MODULE.BROKER)
    assert not guard.exists()
    assert (state / (MODULE.BROKER + '.guard')).read_bytes() == b'reviewed inhibition'
    assert calls == ['signed_reconcile', 'current', ['daemon-reload']]
    assert backend.released == {MODULE.BROKER}


def test_pinned_module_executes_only_hash_verified_bytes(tmp_path, monkeypatch):
    helper = tmp_path / 'helper.py'
    helper.write_bytes(b'VALUE = 1\n')
    monkeypatch.setattr(MODULE, 'trusted', lambda path: Path(path).read_bytes())
    pin = dict(path=str(helper), sha256=MODULE.sha(helper.read_bytes()))
    assert MODULE.load(pin, 'test_pinned').VALUE == 1
    helper.write_bytes(b'VALUE = 2\n')
    with pytest.raises(ValueError):
        MODULE.load(pin, 'test_changed')


def test_default_main_never_mutates_or_starts(tmp_path, monkeypatch, capsys):
    lock = tmp_path / 'owner.lock'
    lock.touch()
    monkeypatch.setattr(MODULE, 'SUPERVISOR', tmp_path)
    monkeypatch.setattr(MODULE, 'STATE', tmp_path / 'attempt')
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: b'{}')
    calls = []
    backend = SimpleNamespace(current=lambda: calls.append('current'), reconcile=lambda: calls.append('reconcile'),
        destinations={tmp_path: tmp_path / 'absent'}, lock=None)
    def close():
        os.close(backend.lock)
        calls.append('close')
    backend.close = close
    monkeypatch.setattr(MODULE, 'Native', lambda expected: backend)
    monkeypatch.setattr(MODULE, 'transition', lambda *args: pytest.fail('unexpected start'))
    MODULE.main(['--expected', '/fixture', '--expected-sha256', 'a' * 64])
    assert calls == ['current', 'reconcile', 'close']
    assert json.loads(capsys.readouterr().out)['started'] is False
    assert not MODULE.STATE.exists()


@pytest.mark.parametrize('fault', [None, 'auth_locked', 'model_unready', 'vault_locked', 'unencrypted',
                                  'old_version', 'old_boot', 'changed_commission', 'runtime_changed', 'new_job',
                                  'missing_guard', 'changed_guard', 'foreign_installed_inode'])
def test_concrete_current_requires_live_prerequisites_and_unchanged_commission(monkeypatch, fault):
    backend = MODULE.Native.__new__(MODULE.Native)
    before = {field: '0' for field in MODULE.EXIT_FIELDS}
    before.update(ActiveState='inactive', SubState='dead', Result='success', InvocationID='')
    services = {service: dict(pid=100 + index, start_ticks='123', uid=994)
                for index, service in enumerate((MODULE.SAGE, MODULE.AUTH, MODULE.MODEL))}
    backend.expected = dict(helpers={}, inspection={}, containment={}, native_completion={},
                           exited_services={service: before for service in (MODULE.BROKER, MODULE.VOICE)},
                           live_services=services, inventory={})
    backend.old = dict(boot_id='boot', chain_id='chain', services={
        MODULE.BROKER: dict(dropins=[]), MODULE.VOICE: dict(dropins=[], uid=994)})
    backend.live = {}
    backend.reset, backend.released = set(), set()
    guards = {service: Path('/etc/systemd/system') / (service + '.d') / 'guard.conf'
              for service in (MODULE.BROKER, MODULE.VOICE)}
    commissions = ('/etc/sage-lantern/audio-capture.json', '/etc/sage-lantern/audio-commissioned.json',
                   '/var/lib/sage-lantern/LOCAL_VOICE_READY')
    backend.recovery = SimpleNamespace(GUARDS=guards, guard_bytes=lambda service: b'guard', COMMISSION=commissions)
    backend.inspector = SimpleNamespace(SYSTEMD='/etc/systemd/system/')
    backend.receipt = dict(guards={str(path): MODULE.sha(b'guard') for path in guards.values()},
                           commissioning_sha256={path: MODULE.sha(b'commission') for path in commissions})
    def properties(service, fields):
        if fields == MODULE.EXIT_FIELDS:
            return before
        return dict(DropInPaths=str(guards[service]), NeedDaemonReload='no', Restart='no', KillMode='control-group',
                    FragmentPath='/etc/systemd/system/' + service)
    native = SimpleNamespace(files=Mock(), service=Mock())
    if fault == 'runtime_changed':
        native.files.side_effect = ValueError
    empty = Mock(side_effect=ValueError if fault == 'new_job' else None)
    commission_reader = Mock(return_value=b'changed' if fault == 'changed_commission' else b'commission')
    backend.backend = SimpleNamespace(remaining=lambda: 300, native=native, empty=empty, properties=properties,
                                      condition=Mock(), commission_bytes=commission_reader)
    backend.records = Mock()
    health = dict(version=MODULE.VERSION, boot_id='new-boot', encrypted=True, vault_locked=False)
    if fault in ('vault_locked', 'unencrypted', 'old_version', 'old_boot'):
        field, value = {'vault_locked': ('vault_locked', True), 'unencrypted': ('encrypted', False),
                        'old_version': ('version', 'old'), 'old_boot': ('boot_id', 'old')}[fault]
        health[field] = value
    backend.completion = dict(dashboard_boot='new-boot')
    def get(port, route):
        if port == 8080:
            return health
        if port == 3196:
            return {'locked': fault == 'auth_locked'}
        if port == 8081:
            return {'status': 'loading' if fault == 'model_unready' else 'ok'}
        return {}
    backend.observer = SimpleNamespace(get=get, deadline=300, system=SimpleNamespace(_sleep_idle=Mock()))
    backend.helpers = dict(update=SimpleNamespace(listener_owner=lambda *args: 'native-socket'),
                           preflight=SimpleNamespace(governance=Mock()))
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: b'pinned')
    def trusted(path):
        if fault == 'missing_guard':
            raise FileNotFoundError
        return b'changed' if fault == 'changed_guard' else b'guard'
    monkeypatch.setattr(MODULE, 'trusted', trusted)
    monkeypatch.setattr(MODULE, 'listener', lambda *args: 'socket')
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: False)
    metadata = SimpleNamespace(st_mode=0o100755, st_uid=0, st_nlink=1, st_dev=1, st_ino=1)
    original_lstat, original_stat = Path.lstat, Path.stat
    monkeypatch.setattr(Path, 'lstat', lambda path, **kwargs: metadata if str(path) == '/opt/sage-lantern/bin/sage-gui'
                        else original_lstat(path, **kwargs))
    monkeypatch.setattr(Path, 'stat', lambda path, **kwargs: SimpleNamespace(st_dev=1,
        st_ino=2 if fault == 'foreign_installed_inode' else 1) if str(path) == '/proc/100/exe' else original_stat(path, **kwargs))
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            backend.current()
    else:
        backend.current()
        assert [call.args[0] for call in commission_reader.call_args_list] == list(commissions)
        assert native.service.call_count == 3
