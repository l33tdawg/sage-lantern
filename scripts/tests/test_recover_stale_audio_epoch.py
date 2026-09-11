import copy
import importlib.util
import json
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from sage_lantern import conversation_records as records


SPEC = importlib.util.spec_from_file_location('recover_stale',
    Path(__file__).parents[1] / 'lantern/recover-stale-audio-epoch.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def control():
    actor = dict(chain_id='sage-personal-test', agent_id='1' * 64)
    token = '11111111-1111-4111-8111-111111111111'
    payload = dict(schema=records.CONTROL_SCHEMA, binding=actor, enabled=False, generation=token,
        history_generation=None, dispatch_epoch=token, clear_epoch=token, suppressed=[])
    return dict(schema='sage.workflow-journal.v1', agent_id=actor['agent_id'],
        record_id=records.record_id(actor), revision=1, kind='conversation_control',
        payload=payload, trust='untrusted_auxiliary')


def test_control_only_counts_without_plaintext_or_initialization():
    row = control()
    before = copy.deepcopy(row)
    result = MODULE.control_inventory([row], row['payload']['binding']['chain_id'], row['agent_id'])
    assert result['counts'] == dict(conversation_control=1, conversation_session=0,
                                  mesh_outbound=0, mesh_inbound=0, public_proposal=0)
    assert result['private_media_inventory_verified'] is False
    assert 'payload' not in json.dumps(result)
    assert row == before


@pytest.mark.parametrize('fault', ['empty', 'extra', 'session', 'mesh', 'proposal', 'agent', 'id', 'revision', 'schema', 'payload'])
def test_any_non_control_or_invalid_record_blocks(fault):
    row = control()
    rows = [row]
    if fault == 'empty':
        rows = []
    elif fault == 'extra':
        rows.append(copy.deepcopy(row))
    elif fault in ('session', 'mesh', 'proposal'):
        row['kind'] = {'session': 'conversation_session', 'mesh': 'mesh_outbound', 'proposal': 'public_proposal'}[fault]
    elif fault == 'agent':
        row['agent_id'] = '2' * 64
    elif fault == 'id':
        row['record_id'] = '22222222-2222-4222-8222-222222222222'
    elif fault == 'revision':
        row['revision'] = True
    elif fault == 'schema':
        row['schema'] = 'other'
    else:
        row['payload']['extra'] = 'unreviewed'
    with pytest.raises(Exception):
        MODULE.control_inventory(rows, 'sage-personal-test', '1' * 64)


@pytest.fixture
def rig(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    state = root / 'journal'
    guards = {service: root / (service + '.conf') for service in MODULE.PRODUCERS}
    monkeypatch.setattr(MODULE, 'STATE', state)
    monkeypatch.setattr(MODULE, 'GUARDS', guards)
    original_stat = Path.lstat
    def root_stat(path):
        info = original_stat(path)
        return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
    monkeypatch.setattr(Path, 'lstat', root_stat)
    calls = []
    expected = dict(unit_id='unit-b', boot_id='test-boot', chain_id='sage-personal-test', agent_id='1' * 64,
                    services={service: dict(dropins=[], pid=10 + index) for index, service in enumerate((*MODULE.PRODUCERS, MODULE.SAGE))})
    class Fake:
        expected_raw = b'original-pinned-input'
        helper_sha256 = 'a' * 64
        def __init__(self):
            self.expected = expected
            self.deadline = MODULE.time.monotonic() + 300
            self.helper = SimpleNamespace(inspect=lambda *args: calls.append('inspect') or {'stale': True},
                                          public_bytes=lambda path: b'unchanged-commission')
            self.native = SimpleNamespace(service=lambda *args: calls.append('pin-process'),
                                          snapshot=lambda *args: '7' * 64 + ':9')
        def journal(self, phase, value):
            calls.append(phase)
            MODULE.write_new(state / (str(len(calls)) + '-' + phase + '.json'), MODULE.canonical(value))
        def commission_bytes(self, name):
            return self.helper.public_bytes(name)
        def command(self, args):
            calls.append(args)
        def guards(self):
            calls.append('verify-guards')
        def condition(self, service, *, inhibited):
            calls.append('condition-inhibited' if inhibited else 'condition-original')
        def unchanged(self):
            calls.append('unchanged')
        def remaining(self):
            return 250
        def contained(self):
            calls.append('contained')
            return {service: {'main_pid': 0, 'recursive_cgroup_empty': True} for service in MODULE.PRODUCERS}
        def reconcile(self):
            calls.append('reconcile')
            return '7' * 64 + ':9', MODULE.control_inventory([control()], 'sage-personal-test', '1' * 64)
        def archive(self):
            calls.append('archive')
            return {'original': {'sha256': 'b' * 64, 'archive_path': 'retained'}}
    return Fake(), state, guards, calls


def test_apply_orders_inhibition_containment_scan_archive_and_never_native_stop(rig):
    backend, state, guards, calls = rig
    result = MODULE.apply(backend)
    assert calls.index('verify-guards') < calls.index(['stop', '--no-block', MODULE.BROKER])
    assert calls.index('contained') < calls.index('reconcile') < calls.index('archive')
    assert [entry for entry in calls if isinstance(entry, list)] == [
        ['daemon-reload'], ['stop', '--no-block', MODULE.BROKER], ['stop', '--no-block', MODULE.VOICE]]
    for field in ('drain_verified', 'native_update_authorized', 'rebootstrap_authorized', 'llm_inference_idle_verified'):
        assert result[field] is False
    assert result['historical_work_disposition'] == 'unknown'
    assert result['scan_sha256'] == MODULE.sha((state / 'reconciliation.json').read_bytes())
    assert all(path.read_bytes() == MODULE.guard_bytes(service) for service, path in guards.items())
    assert json.loads((state / 'complete.json').read_bytes()) == result
    with pytest.raises(ValueError):
        MODULE.apply(backend)


@pytest.mark.parametrize('fault', ['inspect', 'guards', 'stop', 'timeout', 'reconcile', 'archive', 'epoch', 'commission'])
def test_failure_never_publishes_completion_or_removes_evidence(rig, monkeypatch, fault):
    backend, state, guards, calls = rig
    def fail(*args):
        raise TimeoutError('synthetic failure')
    if fault == 'inspect':
        backend.helper.inspect = fail
    elif fault == 'stop':
        original = backend.command
        backend.command = lambda args: fail() if args[0] == 'stop' else original(args)
    elif fault == 'timeout':
        backend.remaining = lambda: fail() if state.exists() else 250
    elif fault == 'epoch':
        backend.native.snapshot = lambda *args: '7' * 64 + ':10'
    elif fault == 'commission':
        original = backend.archive
        def archive():
            backend.helper.public_bytes = lambda path: b'changed-commission'
            return original()
        backend.archive = archive
    else:
        monkeypatch.setattr(backend, fault, fail)
    with pytest.raises((ValueError, TimeoutError)):
        MODULE.apply(backend)
    assert not (state / 'complete.json').exists()
    if fault == 'inspect':
        assert not state.exists()
    else:
        assert any(state.glob('*failed.json'))
        assert all(path.exists() for path in guards.values())


def test_command_allowlist_rejects_native_and_restart_before_subprocess():
    backend = object.__new__(MODULE.Backend)
    for command in (['stop', '--no-block', MODULE.SAGE], ['restart', MODULE.VOICE], ['reset-failed', MODULE.BROKER]):
        with pytest.raises(ValueError):
            backend.command(command)


def test_no_drain_forgery_in_guard_and_write_is_exclusive(tmp_path):
    raw = MODULE.guard_bytes(MODULE.VOICE)
    assert b'RuntimeDirectoryPreserve=yes' in raw and b'ConditionPathExists=!' in raw
    assert b'ExecStart' not in raw and b'drain-request' not in raw
    path = tmp_path / 'receipt'
    MODULE.write_new(path, b'original')
    with pytest.raises(FileExistsError):
        MODULE.write_new(path, b'replacement')
    assert path.read_bytes() == b'original'


@pytest.mark.parametrize('fault', [None, 'missing', 'positive', 'trigger', 'malformed'])
def test_effective_dbus_condition_must_actually_inhibit(monkeypatch, fault):
    backend = object.__new__(MODULE.Backend)
    backend.deadline = MODULE.time.monotonic() + 5
    text = 'a(sbbsi) 1 "ConditionPathExists" false true "' + str(MODULE.STATE) + '" 0'
    if fault == 'missing':
        text = 'a(sbbsi) 0'
    elif fault == 'positive':
        text = text.replace('false true', 'false false')
    elif fault == 'trigger':
        text = text.replace('false true', 'true true')
    elif fault == 'malformed':
        text += ' trailing'
    def run(command, **kwargs):
        assert command[:4] == ['/usr/bin/busctl', '--system', '--no-pager', 'get-property']
        assert kwargs['timeout'] <= 5
        return SimpleNamespace(stdout=text.encode())
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    if fault is None:
        backend.condition(MODULE.VOICE, inhibited=True)
    else:
        with pytest.raises(ValueError):
            backend.condition(MODULE.VOICE, inhibited=True)


def test_scan_containment_does_not_rehash_immutable_artifacts(monkeypatch):
    backend = object.__new__(MODULE.Backend)
    calls = []
    monkeypatch.setattr(backend, 'unchanged', lambda: pytest.fail('full hashes belong outside scan guard'))
    monkeypatch.setattr(backend, 'live_pins', lambda: calls.append('live'))
    monkeypatch.setattr(backend, 'guards', lambda: calls.append('guards'))
    monkeypatch.setattr(backend, 'empty', lambda service: calls.append(service) or True)
    monkeypatch.setattr(backend, 'remaining', lambda: 5)
    backend.contained()
    assert calls == ['live', 'guards', *MODULE.PRODUCERS]


def test_slow_preflight_refuses_before_mutation(rig):
    backend, state, guards, calls = rig
    backend.remaining = lambda: 199
    with pytest.raises(ValueError):
        MODULE.apply(backend)
    assert not state.exists() and not any(path.exists() for path in guards.values())


def test_default_preflight_has_no_mutations(rig):
    backend, state, guards, calls = rig
    observation, commissioning = MODULE.preflight(backend)
    assert observation == {'stale': True}
    assert len(commissioning) == len(MODULE.COMMISSION)
    assert not state.exists() and not any(path.exists() for path in guards.values())
    assert not any(isinstance(entry, list) for entry in calls)


@pytest.mark.parametrize('fault', [None, 'root_parent', 'wrong_parent_uid', 'ancestor_uid', 'parent_writable',
                                 'parent_link', 'leaf_uid', 'leaf_writable', 'hardlink', 'replaced_parent'])
def test_marker_reader_allows_only_known_service_parent(tmp_path, monkeypatch, fault):
    leaf = tmp_path / 'marker'
    leaf.write_bytes(b'{"schema":"synthetic"}\n')
    descriptor = os.open(leaf, os.O_RDONLY)
    real_info = os.fstat(descriptor)
    os.close(descriptor)
    fields = ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
    def identity(info):
        return tuple(getattr(info, name) for name in fields)
    def info(**changes):
        return SimpleNamespace(**{**{name: getattr(real_info, name) for name in fields}, **changes})
    file_info = info(st_uid=994 if fault == 'leaf_uid' else 0,
                     st_mode=stat.S_IFREG | (0o664 if fault == 'leaf_writable' else 0o644),
                     st_nlink=2 if fault == 'hardlink' else 1)
    parent_reads = []
    def lstat(path):
        if str(path) == '/var/lib/sage-lantern/LOCAL_VOICE_READY':
            return file_info
        uid, mode = 0, stat.S_IFDIR | 0o755
        inode = 5
        if str(path) == '/var/lib/sage-lantern':
            parent_reads.append(path)
            uid = 0 if fault == 'root_parent' else 994
            mode = stat.S_IFDIR | 0o700
            if fault == 'wrong_parent_uid':
                uid = 995
            elif fault == 'parent_writable':
                mode |= 0o020
            elif fault == 'parent_link':
                mode = stat.S_IFLNK | 0o777
            elif fault == 'replaced_parent' and len(parent_reads) > 1:
                inode = 6
        elif str(path) == '/var/lib' and fault == 'ancestor_uid':
            uid = 994
        return info(st_uid=uid, st_mode=mode, st_ino=inode)
    original_open = os.open
    def open_marker(path, flags):
        assert str(path) == '/var/lib/sage-lantern/LOCAL_VOICE_READY'
        assert flags & os.O_NOFOLLOW
        return original_open(leaf, os.O_RDONLY)
    monkeypatch.setattr(Path, 'lstat', lstat)
    monkeypatch.setattr(MODULE.os, 'open', open_marker)
    monkeypatch.setattr(MODULE.os, 'fstat', lambda descriptor: file_info)
    backend = object.__new__(MODULE.Backend)
    backend.expected = {'services': {MODULE.VOICE: {'uid': 994}}}
    backend.helper = SimpleNamespace(file_identity=identity,
                                    public_bytes=lambda path: pytest.fail('marker needs scoped reader'))
    if fault in (None, 'root_parent'):
        assert backend.commission_bytes('/var/lib/sage-lantern/LOCAL_VOICE_READY') == b'{"schema":"synthetic"}\n'
    else:
        with pytest.raises(ValueError):
            backend.commission_bytes('/var/lib/sage-lantern/LOCAL_VOICE_READY')


def test_other_commission_files_keep_root_only_reader():
    backend = object.__new__(MODULE.Backend)
    calls = []
    backend.helper = SimpleNamespace(public_bytes=lambda name: calls.append(name) or b'root-only')
    for name in MODULE.COMMISSION[:2]:
        assert backend.commission_bytes(name) == b'root-only'
    assert calls == list(MODULE.COMMISSION[:2])
    with pytest.raises(ValueError):
        backend.commission_bytes('/var/lib/sage-lantern/unrelated')
