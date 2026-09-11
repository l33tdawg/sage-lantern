import importlib.util
import os
from pathlib import Path
import stat
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('inactive_display_maintenance', ROOT / 'scripts/lantern/prepare-runtime-maintenance.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    marker = tmp_path / 'enabled'
    monkeypatch.setattr(MODULE, 'DISPLAY_ENABLE', marker)
    group = tmp_path / 'group'
    group.mkdir()
    (group / 'cgroup.events').write_text('populated 0\n')
    unit = dict(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0',
                InvocationID='', NRestarts='0', ControlGroup='', Job='')
    commands = []
    def show(service, deadline, fields):
        if fields == ('ConditionResult', 'UnitFileState'):
            return {'ConditionResult': 'no', 'UnitFileState': 'disabled'}
        return {field: unit[field] for field in fields}
    system = types.SimpleNamespace(clock=lambda: 0, _no_job=lambda job: job == '',
        _show=show, _cgroup=lambda path: group,
        _empty_tree=lambda *args: None, _command=lambda args, deadline: commands.append(args))
    return marker, group, unit, system, commands


@pytest.mark.parametrize('fault', ['none', 'enabled', 'dangling', 'populated', 'condition',
    'foreign', 'active', 'pid', 'job', 'invocation', 'restart'])
def test_inactive_profile_requires_exact_disabled_empty_display(fixture, fault):
    marker, group, unit, system, commands = fixture
    service = MODULE.DISPLAY
    if fault == 'enabled': marker.write_bytes(b'')
    if fault == 'dangling': marker.symlink_to(marker.parent / 'absent')
    if fault == 'populated': (group / 'cgroup.events').write_text('populated 1\n')
    if fault == 'condition': system._show = lambda *args: {'ConditionResult': 'yes'}
    if fault == 'foreign': service = MODULE.VOICE
    if fault == 'active': unit['ActiveState'] = 'active'
    if fault == 'pid': unit['MainPID'] = '12'
    if fault == 'job': unit['Job'] = '19'
    if fault == 'invocation': unit['InvocationID'] = 'a' * 32
    if fault == 'restart': unit['NRestarts'] = '1'
    if fault == 'none': MODULE.inactive_display(system, service, unit)
    else:
        with pytest.raises(ValueError): MODULE.inactive_display(system, service, unit)
    assert commands == []


@pytest.mark.parametrize('policy_present', [False, True])
def test_real_stop_and_restore_preserve_inactive_without_start_or_stop(present_policy, tmp_path, policy_present):
    marker, group, unit, system, commands = present_policy
    if not policy_present: marker.unlink()
    digest = MODULE.DISPLAY_POLICY_SHA if policy_present else None
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    service = MODULE.DISPLAY
    backend.expected = {'files': {}, 'services': {service: {'unit': unit, 'process': None}}}
    if policy_present: backend.expected['files'][str(marker)] = digest
    backend.auth_final_sha256 = 'a' * 64
    backend.installed = True
    backend.guarded = {service}
    backend.stopped = set()
    backend.running = {}
    backend.restore_attempted = set()
    backend.system = system
    backend.show = lambda selected: dict(unit)
    backend.current_after_auth = lambda: MODULE.inactive_display(system, service, unit, policy_sha256=digest)
    backend.stop = lambda selected: pytest.fail('must not stop inactive display')
    backend.stop_importer(service)
    assert backend.stopped == {service}
    path = tmp_path / 'guard'
    path.write_bytes(backend.guard())
    backend.guard_path = lambda selected: path
    backend.helper = types.SimpleNamespace(public=lambda name: name.read_bytes(), sync=lambda name: None)
    records = []
    backend.record = lambda phase, details=None: records.append((phase, details))
    backend._restore(service)
    assert not path.exists() and backend.running == {} and backend.guarded == set()
    assert commands == [['daemon-reload']]
    assert records[-1] == ('preserved_inactive_' + service, {'started': False})
    assert not any(phase.startswith('start_') for phase, details in records)


def test_absent_display_is_never_adopted_as_an_importer(fixture, monkeypatch):
    marker, group, unit, system, commands = fixture
    backend = MODULE.Maintenance.__new__(MODULE.Maintenance)
    backend.expected = {'files': {}, 'services': {MODULE.DISPLAY: {'unit': unit, 'process': None}}}
    backend.system = system
    backend.show = lambda service: unit
    backend.self_identity = {'start_ticks': 1, 'exe_dev': 2, 'exe_ino': 3}
    monkeypatch.setattr(MODULE, 'SERVICES', (MODULE.DISPLAY,))
    observed = []
    monkeypatch.setattr(MODULE, 'importer_inventory', lambda allowed: observed.append(allowed) or {})
    backend.exclude_importers(initial=True)
    assert len(observed) == 2 and all(len(allowed) == 1 for allowed in observed)


@pytest.fixture
def present_policy(fixture, monkeypatch):
    marker, group, unit, system, commands = fixture
    marker.write_bytes(b'fresh unit-a\n')
    marker.chmod(0o644)
    real_fstat = os.fstat
    real_lstat = Path.lstat
    def root_metadata(info):
        fields = {field: getattr(info, field) for field in MODULE.FILE_FIELDS}
        fields.update(st_uid=0, st_gid=0)
        if stat.S_ISDIR(info.st_mode): fields['st_mode'] &= ~0o022
        return types.SimpleNamespace(**fields)
    monkeypatch.setattr(MODULE.os, 'fstat', lambda descriptor: root_metadata(real_fstat(descriptor)))
    monkeypatch.setattr(Path, 'lstat', lambda path: root_metadata(real_lstat(path)))
    return fixture


@pytest.mark.parametrize('fault', ['none', 'wrong_pin', 'wrong_bytes', 'wrong_mode', 'hardlink',
    'symlink', 'absent', 'no_pin', 'enabled', 'state_race', 'policy_removed', 'policy_substituted'])
def test_present_policy_is_exact_and_revalidated(present_policy, monkeypatch, fault):
    marker, group, unit, system, commands = present_policy
    digest = MODULE.DISPLAY_POLICY_SHA
    observed = dict(unit)
    if fault == 'wrong_pin': digest = 'a' * 64
    if fault == 'wrong_bytes': marker.write_bytes(b'fresh unit-b\n')
    if fault == 'wrong_mode': marker.chmod(0o600)
    if fault == 'hardlink': os.link(marker, marker.parent / 'second')
    if fault == 'symlink':
        target = marker.with_name('target'); marker.rename(target); marker.symlink_to(target)
    if fault == 'absent': marker.unlink()
    if fault == 'no_pin': digest = None
    if fault == 'enabled':
        previous = system._show
        system._show = lambda service, deadline, fields: ({'ConditionResult': 'no', 'UnitFileState': 'enabled'}
            if fields == ('ConditionResult', 'UnitFileState') else previous(service, deadline, fields))
    if fault == 'state_race': system._empty_tree = lambda *args: unit.update(MainPID='123')
    if fault == 'policy_removed': system._empty_tree = lambda *args: marker.unlink()
    if fault == 'policy_substituted': system._empty_tree = lambda *args: marker.write_bytes(b'fresh unit-b\n')
    if fault == 'none': MODULE.inactive_display(system, MODULE.DISPLAY, observed, policy_sha256=digest)
    else:
        with pytest.raises((ValueError, OSError)):
            MODULE.inactive_display(system, MODULE.DISPLAY, observed, policy_sha256=digest)
    assert commands == []


def test_policy_replaced_during_descriptor_read_is_rejected(present_policy, monkeypatch):
    marker, group, unit, system, commands = present_policy
    real_read = os.read
    def replaced(descriptor, size):
        raw = real_read(descriptor, size)
        replacement = marker.with_name('replacement')
        replacement.write_bytes(b'fresh unit-a\n'); replacement.chmod(0o644)
        os.replace(replacement, marker)
        return raw
    monkeypatch.setattr(MODULE.os, 'read', replaced)
    with pytest.raises(ValueError): MODULE.display_policy(MODULE.DISPLAY_POLICY_SHA)


@pytest.mark.parametrize('field,value', [('st_uid', 994), ('st_gid', 993)])
def test_policy_must_be_root_root(present_policy, monkeypatch, field, value):
    original = MODULE.os.fstat
    def changed(descriptor):
        info = original(descriptor)
        setattr(info, field, value)
        return info
    monkeypatch.setattr(MODULE.os, 'fstat', changed)
    with pytest.raises(ValueError): MODULE.display_policy(MODULE.DISPLAY_POLICY_SHA)
