import importlib.util
import fcntl
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('preproducer_recovery',
    Path(__file__).resolve().parents[1] / 'lantern/recover-preproducer-bootstrap.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def service_values():
    baseline = {key: '0' for key in MODULE.FIELDS}
    baseline.update(LoadState='loaded', ActiveState='inactive', SubState='dead', MainPID='0', Job='',
        ControlGroup='', Slice='system.slice', KillMode='control-group', Restart='no', Result='success',
        DropInPaths='', NeedDaemonReload='no')
    voice = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.VOICE)
    broker = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.BROKER,
        ActiveState='failed', SubState='failed', Result='exit-code', ExecMainStartTimestampMonotonic='100',
        ExecMainExitTimestampMonotonic='200', ExecMainCode='1', ExecMainStatus='1')
    model = dict(baseline, FragmentPath='/etc/systemd/system/' + MODULE.MODEL,
        ActiveState='active', SubState='running', MainPID='99', InvocationID='m' * 32)
    return {MODULE.VOICE: voice, MODULE.BROKER: broker, MODULE.MODEL: model}


@pytest.mark.parametrize('fault', [None, 'voice_started', 'voice_restarted', 'voice_pid', 'broker_job',
    'broker_running', 'empty_unverified', 'model_stopped', 'exit_order', 'configuration_changed'])
def test_preproducer_services_require_exact_history_and_current_containment(monkeypatch, fault):
    expected = service_values()
    if fault == 'voice_started':
        expected[MODULE.VOICE]['ExecMainStartTimestampMonotonic'] = '50'
    if fault == 'voice_restarted':
        expected[MODULE.VOICE]['NRestarts'] = '1'
    if fault == 'voice_pid':
        expected[MODULE.VOICE]['MainPID'] = '22'
    if fault == 'broker_job':
        expected[MODULE.BROKER]['Job'] = 'queued'
    if fault == 'broker_running':
        expected[MODULE.BROKER]['ActiveState'] = 'active'
    if fault == 'model_stopped':
        expected[MODULE.MODEL]['ActiveState'] = 'inactive'
    if fault == 'exit_order':
        expected[MODULE.BROKER]['ExecMainExitTimestampMonotonic'] = '99'
    def show(service):
        value = dict(expected[service])
        if fault == 'configuration_changed':
            value['DropInPaths'] = '/unknown.conf'
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    empties = []
    def empty(service):
        empties.append(service)
        if fault == 'empty_unverified':
            raise ValueError
    monkeypatch.setattr(MODULE, 'empty', empty)
    if fault:
        with pytest.raises(ValueError):
            MODULE.services(expected)
    else:
        MODULE.services(expected)
        assert empties == [MODULE.VOICE, MODULE.BROKER]


def test_reset_failed_does_not_rewrite_recorded_exit_identity(monkeypatch):
    expected = service_values()
    def show(service):
        value = dict(expected[service])
        if service == MODULE.BROKER:
            value.update(ActiveState='inactive', SubState='dead', Result='success')
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    MODULE.services(expected, reset=True)
    assert expected[MODULE.BROKER]['ActiveState'] == 'failed'
    assert expected[MODULE.BROKER]['ExecMainExitTimestampMonotonic'] == '200'


@pytest.mark.parametrize('mask', range(32))
def test_reset_accepts_only_complete_original_or_complete_gc_identity(monkeypatch, mask):
    expected = service_values()
    expected[MODULE.BROKER]['InvocationID'] = 'f' * 32
    original = json.loads(json.dumps(expected))
    cleared = {'InvocationID': '', 'ExecMainStartTimestampMonotonic': '0',
               'ExecMainExitTimestampMonotonic': '0', 'ExecMainCode': '0', 'ExecMainStatus': '0'}
    def show(service):
        value = dict(expected[service])
        if service == MODULE.BROKER:
            value.update(ActiveState='inactive', SubState='dead', Result='success')
            for index, (field, reset_value) in enumerate(cleared.items()):
                if mask & (1 << index):
                    value[field] = reset_value
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    if mask in (0, 31):
        MODULE.services(expected, reset=True)
    else:
        with pytest.raises(ValueError):
            MODULE.services(expected, reset=True)
    assert expected == original


def test_gc_alternative_never_applies_before_reset(monkeypatch):
    expected = service_values()
    def show(service):
        value = dict(expected[service])
        if service == MODULE.BROKER:
            value.update(ActiveState='inactive', SubState='dead', Result='success', InvocationID='',
                ExecMainStartTimestampMonotonic='0', ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0')
        return value
    monkeypatch.setattr(MODULE, 'show', show)
    monkeypatch.setattr(MODULE, 'empty', lambda service: None)
    with pytest.raises(ValueError):
        MODULE.services(expected)


@pytest.mark.parametrize('fault', [None, 'current', 'marker', 'receipt', 'commission', 'bootstrap', 'supervisor', 'reset'])
def test_ordered_archive_retains_failure_and_never_retries(fault):
    events = []
    names = {MODULE.MARKER: 'marker', MODULE.RECEIPT: 'receipt', MODULE.COMMISSION: 'commission', MODULE.BOOTSTRAP: 'bootstrap'}
    def action(name):
        events.append(name)
        if fault == name:
            raise ValueError
    backend = SimpleNamespace(current=lambda: action('current'), archive_file=lambda path: action(names[path]),
        archive_supervisor=lambda: action('supervisor'), reset_broker=lambda: action('reset'))
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, events.append)
        assert events[-1] == fault and 'reset_broker_done' not in events
    else:
        result = MODULE.transition(backend, events.append)
        assert [item for item in events if item in names.values() or item in ('supervisor', 'reset')] == [
            'marker', 'receipt', 'commission', 'bootstrap', 'supervisor', 'reset']
        assert result['historical_containment_verified'] is False and result['drain_claimed'] is False
        assert result['retry_authorized'] is False


@pytest.mark.parametrize('arguments', [['start', MODULE.BROKER], ['restart', MODULE.VOICE], ['stop', MODULE.MODEL],
                                      ['reset-failed', MODULE.VOICE], ['daemon-reload']])
def test_mutation_command_allowlist(arguments):
    with pytest.raises(ValueError):
        MODULE.command(arguments)


def test_archive_copy_is_verified_before_original_unlink(tmp_path, monkeypatch):
    marker = tmp_path / 'marker'
    marker.write_bytes(b'original')
    archive = tmp_path / 'archive'
    archive.mkdir()
    monkeypatch.setattr(MODULE, 'MARKER', marker)
    monkeypatch.setattr(MODULE, 'ATTEMPT', archive)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.moved = set()
    backend.expected = {'files': {str(marker): {'mode': 0o644, 'gid': 7}}}
    events = []
    def file(path, actual=None):
        chosen = path if actual is None else actual
        assert chosen.read_bytes() == b'original'
        events.append('source' if actual is None else 'verify_archive')
        return chosen.read_bytes()
    backend.file = file
    backend.current = lambda: events.append('current')
    def write(path, raw, mode, gid):
        assert (mode, gid) == (0o644, 7)
        with path.open('xb') as stream:
            stream.write(raw)
        events.append('durable_copy')
    monkeypatch.setattr(MODULE, 'write', write)
    monkeypatch.setattr(MODULE, 'sync', lambda path: events.append('sync_original_parent'))
    backend.archive_file(marker)
    assert events == ['source', 'durable_copy', 'verify_archive', 'current', 'sync_original_parent']
    assert not marker.exists() and (archive / marker.name).read_bytes() == b'original'
    assert marker in backend.moved


def test_existing_archive_refuses_without_touching_source(tmp_path, monkeypatch):
    marker, archive = tmp_path / 'marker', tmp_path / 'archive'
    marker.write_bytes(b'original')
    archive.mkdir()
    (archive / marker.name).write_bytes(b'existing')
    monkeypatch.setattr(MODULE, 'MARKER', marker)
    monkeypatch.setattr(MODULE, 'ATTEMPT', archive)
    backend = MODULE.Recovery.__new__(MODULE.Recovery)
    backend.moved = set()
    backend.file = lambda path: path.read_bytes()
    backend.expected = {'files': {str(marker): {'mode': 0o644, 'gid': 0}}}
    monkeypatch.setattr(MODULE, 'parents', lambda path: None)
    with pytest.raises(FileExistsError):
        backend.archive_file(marker)
    assert marker.read_bytes() == b'original'


def test_duplicate_expected_fields_refuse():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"boot_id":1,"boot_id":2}')


@pytest.mark.parametrize('fault', [None, 'wrong_uid', 'wrong_mode', 'link', 'other_parent', 'other_leaf'])
def test_service_owned_parent_exception_is_marker_only(monkeypatch, fault):
    def info(path):
        uid, mode = 0, 0o40755
        if path == MODULE.MARKER.parent:
            uid, mode = (995 if fault == 'wrong_uid' else 994), (0o40750 if fault == 'wrong_mode' else 0o40700)
            if fault == 'link':
                mode = 0o120777
        if fault == 'other_parent' and path == MODULE.MARKER.parent.parent:
            uid = 994
        return SimpleNamespace(st_uid=uid, st_mode=mode)
    monkeypatch.setattr(Path, 'lstat', info)
    path = MODULE.MARKER.parent / 'other' if fault == 'other_leaf' else MODULE.MARKER
    if fault:
        with pytest.raises(ValueError):
            MODULE.parents(path, marker_parent={'uid': 994, 'mode': 0o700})
    else:
        MODULE.parents(path, marker_parent={'uid': 994, 'mode': 0o700})
        with pytest.raises(ValueError):
            MODULE.parents(path)


def test_kernel_owner_flock_exclusion_and_release(tmp_path, monkeypatch):
    (tmp_path / 'owner.lock').touch(mode=0o600)
    monkeypatch.setattr(MODULE, 'SUPERVISOR', tmp_path)
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda name: SimpleNamespace(pw_uid=994))
    monkeypatch.setattr(MODULE.Recovery, 'current', lambda self: None)
    expected = dict(schema='sage.lantern.preproducer-recovery.v1', boot_id='boot', broker_id='a' * 32,
        files={str(path): {} for path in MODULE.FILES}, services={}, configuration={}, marker_parent={'uid': 994, 'mode': 0o700})
    competing = os.open(tmp_path / 'owner.lock', os.O_RDONLY)
    try:
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            MODULE.Recovery(expected)
        fcntl.flock(competing, fcntl.LOCK_UN)
        recovery = MODULE.Recovery(expected)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            recovery.close()
        fcntl.flock(competing, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(competing)
