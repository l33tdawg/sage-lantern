import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


ROOT = Path(__file__).resolve().parents[1] / 'lantern'


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


MODULE = module('resident_continuation', 'continue-resident-voice-reactivation.py')
BASE = module('frozen_reactivation_tests', 'reactivate-resident-vault-voice.py')


def checkpoint():
    return dict(schema='sage.lantern.resident-voice-reset-continuation.v1',
        helper={'path': '/helper', 'sha256': MODULE.HELPER_SHA},
        expected={'path': '/expected', 'sha256': MODULE.EXPECTED_SHA},
        journals={name: 'a' * 64 for name in MODULE.JOURNALS}, trees={'/old': [1, 2, 0, 0, 448]}, broker_exit={})


@pytest.mark.parametrize('fault', [None, 'helper', 'expected', 'missing_journal', 'extra_done', 'empty_trees', 'schema'])
def test_only_exact_frozen_failed_checkpoint_is_accepted(fault):
    value = checkpoint()
    if fault in ('helper', 'expected'):
        value[fault]['sha256'] = '0' * 64
    if fault == 'missing_journal':
        value['journals'].pop(next(iter(value['journals'])))
    if fault == 'extra_done':
        value['journals']['08-reset_done.json'] = 'a' * 64
    if fault == 'empty_trees':
        value['trees'] = {}
    if fault == 'schema':
        value['schema'] = 'generic-resume'
    if fault:
        with pytest.raises(ValueError):
            MODULE.validate(value)
    else:
        MODULE.validate(value)


def broker_value():
    value = {field: '0' for field in BASE.EXIT_FIELDS}
    value.update(ActiveState='inactive', SubState='dead', Result='success', ExecMainPID='123', InvocationID='a' * 32,
                 ExecMainStartTimestampMonotonic='10', ExecMainExitTimestampMonotonic='20', ExecMainCode='1', ExecMainStatus='1')
    return value


@pytest.mark.parametrize('mask', range(64))
def test_broker_adoption_accepts_only_exact_retained_or_coherent_gc_without_command(mask):
    before = broker_value()
    observed = dict(before)
    cleared = dict(ExecMainPID='0', InvocationID='', ExecMainStartTimestampMonotonic='0',
                   ExecMainExitTimestampMonotonic='0', ExecMainCode='0', ExecMainStatus='0')
    for index, (field, value) in enumerate(cleared.items()):
        if mask & (1 << index):
            observed[field] = value
    backend = SimpleNamespace(backend=SimpleNamespace(properties=Mock(return_value=observed), empty=Mock()),
                              owner=SimpleNamespace(inactive=Mock()),
                              expected={'exited_services': {BASE.BROKER: before}}, command=Mock())
    if mask in (0, 63):
        MODULE.broker_inactive(BASE, backend, {'broker_exit': before})
        backend.backend.empty.assert_called_once_with(BASE.BROKER)
        backend.owner.inactive.assert_called_once_with(BASE.BROKER)
    else:
        with pytest.raises(ValueError):
            MODULE.broker_inactive(BASE, backend, {'broker_exit': before})
    backend.command.assert_not_called()


@pytest.mark.parametrize('fault', ['active', 'failed', 'pid', 'restarts', 'new_invocation', 'job_or_cgroup'])
def test_broker_not_inactive_or_identity_changed_refuses(fault):
    before = broker_value()
    observed = dict(before)
    if fault in ('active', 'failed'):
        observed['ActiveState'] = fault
    if fault == 'pid':
        observed['MainPID'] = '55'
    if fault == 'restarts':
        observed['NRestarts'] = '1'
    if fault == 'new_invocation':
        observed['InvocationID'] = 'b' * 32
    empty = Mock(side_effect=ValueError if fault == 'job_or_cgroup' else None)
    backend = SimpleNamespace(backend=SimpleNamespace(properties=lambda *args: observed, empty=empty),
                              owner=SimpleNamespace(inactive=Mock()), expected={'exited_services': {BASE.BROKER: before}})
    with pytest.raises(ValueError):
        MODULE.broker_inactive(BASE, backend, {'broker_exit': before})


@pytest.mark.parametrize('failure', [None, 'current', 'reset_voice', 'release_broker', 'release_voice', 'reconcile', 'fresh', 'start'])
def test_remaining_actions_only_and_no_fabricated_old_done(failure):
    calls = []
    def action(name):
        calls.append(name)
        if failure == name:
            raise ValueError
        if name == 'start':
            return {'broker_active': True, 'voice_active': True}
    backend = SimpleNamespace(current=lambda: action('current'),
        expected={'exited_services': {BASE.VOICE: {'ActiveState': 'failed'}}},
        reset_service=lambda service: action('reset_voice' if service == BASE.VOICE else 'FORBIDDEN'),
        release=lambda service: action('release_voice' if service == BASE.VOICE else 'release_broker'),
        reconcile=lambda: action('reconcile'), owner=SimpleNamespace(fresh=lambda: action('fresh'), start=lambda: action('start')))
    journal = []
    def record(phase, details=None):
        journal.append((phase, details))
    if failure:
        with pytest.raises(ValueError):
            MODULE.transition(BASE, backend, record)
        assert calls[-1] == failure
        assert 'new_lifetime_start_done' not in [phase for phase, details in journal]
    else:
        result = MODULE.transition(BASE, backend, record)
        assert [name for name in calls if name != 'current'] == ['reset_voice', 'release_broker', 'release_voice', 'reconcile', 'fresh', 'start']
        assert result['broker_reset_command_performed'] is False and result['old_reset_command_outcome'] == 'unknown'
        assert result['old_drain_verified'] is False
        assert journal[0] == ('adopt_observed_inactive_broker', {'reset_command_performed': False, 'old_command_outcome': 'unknown'})
    assert 'FORBIDDEN' not in calls


@pytest.mark.parametrize('fault', [None, 'extra_record', 'wrong_hash', 'wrong_phase', 'wrong_expected', 'invented_details'])
def test_old_journal_bytes_and_exact_membership_are_bound(tmp_path, monkeypatch, fault):
    original = tmp_path / 'old'
    original.mkdir(mode=0o700)
    (original / 'supervisor').mkdir()
    expected_raw = b'{}'
    containment = b'{"historical":"unknown"}'
    (original / 'expected.json').write_bytes(expected_raw)
    (original / 'containment.json').write_bytes(containment)
    expected_sha = MODULE.sha(expected_raw)
    monkeypatch.setattr(MODULE, 'EXPECTED_SHA', expected_sha)
    value = checkpoint()
    value['expected']['sha256'] = expected_sha
    for name, phase in MODULE.JOURNALS.items():
        record = dict(phase=phase, expected_sha256=expected_sha, details=None)
        if name == next(iter(MODULE.JOURNALS)):
            if fault == 'wrong_phase':
                record['phase'] = 'different'
            if fault == 'wrong_expected':
                record['expected_sha256'] = 'a' * 64
            if fault == 'invented_details':
                record['details'] = {'drained': True}
        raw = json.dumps(record).encode()
        (original / name).write_bytes(raw)
        value['journals'][name] = MODULE.sha(raw)
    if fault == 'extra_record':
        (original / '08-reset_done.json').write_text('{}')
    if fault == 'wrong_hash':
        value['journals'][next(iter(MODULE.JOURNALS))] = 'a' * 64
    monkeypatch.setattr(MODULE, 'OLD_STATE', original)
    actual_lstat = Path.lstat
    monkeypatch.setattr(Path, 'lstat', lambda path, **kwargs: SimpleNamespace(st_mode=0o40700, st_uid=0)
                        if path == original else actual_lstat(path, **kwargs))
    monkeypatch.setattr(MODULE, 'read', lambda path: b'source' if str(path) == '/helper' else Path(path).read_bytes())
    monkeypatch.setattr(MODULE, 'pin', lambda pin: b'source' if pin['path'] == '/helper' else expected_raw)
    backend = SimpleNamespace(expected={'containment': {'sha256': MODULE.sha(containment)}})
    base = SimpleNamespace(__file__='/helper')
    if fault:
        with pytest.raises(ValueError):
            MODULE.old_journals(base, value, backend)
    else:
        MODULE.old_journals(base, value, backend)
        assert not (original / '08-reset_done.json').exists()


def test_construction_adopts_moves_without_relocating_or_rewriting_expected(monkeypatch):
    expected = {'original': True}
    def initialize(self, actual):
        assert actual == expected
        self.destinations = {BASE.SUPERVISOR: '/archive', BASE.VOICE_RUN: '/run/archive', BASE.BOOTSTRAP: '/run/archive.json'}
        self.released = set()
        self.expected = actual
    fake_base = SimpleNamespace(Native=type('FakeNative', (), {'__init__': initialize}), STATE=MODULE.OLD_STATE,
                                SUPERVISOR=BASE.SUPERVISOR, VOICE_RUN=BASE.VOICE_RUN, BOOTSTRAP=BASE.BOOTSTRAP, BROKER=BASE.BROKER)
    value = checkpoint()
    monkeypatch.setattr(MODULE, 'pin', lambda pin: json.dumps(expected).encode())
    backend = MODULE.construct(fake_base, value)
    assert backend.moved == {BASE.SUPERVISOR, BASE.VOICE_RUN, BASE.BOOTSTRAP}
    assert backend.initial_trees == value['trees']
    assert backend.reset == {BASE.BROKER} and backend.released == set()
    assert backend.expected == expected


@pytest.mark.parametrize('held', [False, True])
def test_default_readonly_locks_archived_owner_and_never_creates_attempt(tmp_path, monkeypatch, capsys, held):
    import fcntl
    old_lock = tmp_path / 'owner.lock'
    old_lock.touch()
    source = b'SUPERVISOR = __import__("pathlib").Path("/old/supervisor")\n'
    value = checkpoint()
    raw = json.dumps(value).encode()
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE, 'STATE', tmp_path / 'new-attempt')
    monkeypatch.setattr(MODULE, 'pin', lambda pin: source if pin['path'] == '/helper' else raw)
    monkeypatch.setattr(MODULE, 'transition', lambda *args: pytest.fail('readonly mutation'))
    calls = []
    backend = SimpleNamespace(current=lambda: calls.append('current'), reconcile=lambda: calls.append('reconcile'),
                              location=lambda path: old_lock, lock=None)
    def close():
        if backend.lock is not None:
            os.close(backend.lock)
        calls.append('close')
    backend.close = close
    monkeypatch.setattr(MODULE, 'construct', lambda *args: backend)
    other = os.open(old_lock, os.O_RDONLY)
    try:
        if held:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with pytest.raises(BlockingIOError):
                MODULE.main(['--checkpoint', '/checkpoint', '--checkpoint-sha256', MODULE.sha(raw)])
            assert calls == ['current', 'close']
        else:
            MODULE.main(['--checkpoint', '/checkpoint', '--checkpoint-sha256', MODULE.sha(raw)])
            assert calls == ['current', 'reconcile', 'close']
            output = json.loads(capsys.readouterr().out)
            assert output['started'] is False and output['old_command_outcome'] == 'unknown'
        assert not MODULE.STATE.exists()
    finally:
        os.close(other)


def test_guard_backup_is_written_only_in_new_attempt(tmp_path, monkeypatch):
    expected = {'original': True}
    guard = tmp_path / 'guard.conf'
    guard.write_bytes(b'reviewed guard')
    target = tmp_path / 'new-attempt'
    target.mkdir()
    monkeypatch.setattr(MODULE, 'STATE', target)
    def initialize(self, actual):
        self.expected = actual
        self.destinations = {BASE.SUPERVISOR: '/archive', BASE.VOICE_RUN: '/run/archive', BASE.BOOTSTRAP: '/run/archive.json'}
    writes = []
    fake_base = SimpleNamespace(Native=type('FakeNative', (), {'__init__': initialize}), STATE=MODULE.OLD_STATE,
        SUPERVISOR=BASE.SUPERVISOR, VOICE_RUN=BASE.VOICE_RUN, BOOTSTRAP=BASE.BOOTSTRAP, BROKER=BASE.BROKER, VOICE=BASE.VOICE,
        trusted=lambda path: path.read_bytes(), write=lambda path, raw: writes.append((path, raw)), sync=lambda path: None)
    monkeypatch.setattr(MODULE, 'pin', lambda pin: json.dumps(expected).encode())
    backend = MODULE.construct(fake_base, checkpoint())
    backend.reset = {BASE.BROKER, BASE.VOICE}
    backend.released = set()
    backend.reconcile = Mock()
    backend.current = Mock()
    backend.command = Mock()
    backend.recovery = SimpleNamespace(GUARDS={BASE.BROKER: guard})
    backend.receipt = {'guards': {str(guard): MODULE.sha(guard.read_bytes())}}
    backend.release(BASE.BROKER)
    assert writes == [(target / (BASE.BROKER + '.guard'), b'reviewed guard')]
    assert not guard.exists()
    backend.command.assert_called_once_with(['daemon-reload'])


@pytest.mark.parametrize('loaded', [True, False])
def test_loaded_inactive_zero_broker_never_retries_nonzero_reset(monkeypatch, loaded):
    recovery = module('recovery_loaded_broker_test', 'recover-stale-audio-epoch.py')
    import sage_lantern.suspend_system as suspend
    zero = {field: '0' for field in BASE.EXIT_FIELDS}
    zero.update(ActiveState='inactive', SubState='dead', Result='success', InvocationID='')
    observed = dict(LoadState='loaded' if loaded else 'not-found', ActiveState='inactive', MainPID='0',
                    Job='', ControlGroup='', Slice='system.slice', KillMode='control-group')
    calls = []
    def empty_tree(group, deadline):
        assert group == '/system.slice/' + BASE.BROKER
        calls.append('empty_tree')
    monkeypatch.setattr(suspend, 'SuspendSystem', lambda: SimpleNamespace(_cgroup=lambda group: group, _empty_tree=empty_tree))
    containment = recovery.Backend.__new__(recovery.Backend)
    containment.expected = {'services': {BASE.BROKER: {'pid': 99999999}}}
    containment.deadline = 10
    containment.properties = lambda *args: observed
    def properties(service, fields):
        assert service == BASE.BROKER and fields == BASE.EXIT_FIELDS
        return zero
    def reset_would_fail(arguments):
        raise subprocess.CalledProcessError(1, ['/usr/bin/systemctl', *arguments])
    backend = SimpleNamespace(expected={'exited_services': {BASE.BROKER: zero}},
        backend=SimpleNamespace(properties=properties, empty=containment.empty),
        owner=SimpleNamespace(inactive=Mock()), command=Mock(side_effect=reset_would_fail))
    if loaded:
        MODULE.broker_inactive(BASE, backend, {'broker_exit': zero})
        assert calls == ['empty_tree']
        backend.owner.inactive.assert_called_once_with(BASE.BROKER)
    else:
        with pytest.raises(ValueError):
            MODULE.broker_inactive(BASE, backend, {'broker_exit': zero})
        assert calls == []
    backend.command.assert_not_called()


def test_duplicate_checkpoint_fields_refuse():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"broker_exit":{},"broker_exit":{}}')
