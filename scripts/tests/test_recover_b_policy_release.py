import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('b_release_recovery',
    Path(__file__).resolve().parents[1] / 'lantern/recover-b-policy-release.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


@pytest.mark.parametrize('change', [None, 'bytes', 'missing', 'extra'])
def test_prior_failed_recovery_must_remain_exact(tmp_path, monkeypatch, change):
    evidence = {'journal.jsonl': b'unknown', 'failed.json': b'stopped', 'error.json': b'predicate'}
    for name, raw in evidence.items():
        (tmp_path / name).write_bytes(raw)
    monkeypatch.setattr(module, 'PRIOR_RECOVERY', tmp_path)
    monkeypatch.setattr(module, 'PRIOR_FILES', {name: module.digest(raw) for name, raw in evidence.items()})
    monkeypatch.setattr(module, 'DIAGNOSTIC_RECOVERY', tmp_path)
    monkeypatch.setattr(module, 'DIAGNOSTIC_FILES', module.PRIOR_FILES)
    monkeypatch.setattr(module, 'read', lambda path: path.read_bytes())
    if change == 'bytes':
        (tmp_path / 'failed.json').write_bytes(b'invented success')
    elif change == 'missing':
        (tmp_path / 'error.json').unlink()
    elif change == 'extra':
        (tmp_path / 'complete.json').write_bytes(b'invented')
    if change is None:
        module.prior_recovery()
    else:
        with pytest.raises(RuntimeError):
            module.prior_recovery()


@pytest.mark.parametrize('fault', [None, 'intent', 'remove', 'start_auth', 'readiness', 'start_ui', 'final', 'publish', 'log_and_cleanup',
    'terminate_after_remove', 'terminate_after_auth_start', 'deadline_after_remove'])
def test_recovery_exclusive_bounded_and_preserves_old_journal(tmp_path, fault):
    old = tmp_path / 'old-journal'
    old.write_bytes(b'failed release evidence')
    recovery = tmp_path / 'recovery'
    guards = {name: tmp_path / (name + '.guard') for name in ('auth', 'ui', 'native', 'model')}
    for path in guards.values():
        path.write_bytes(b'guard')
    commands, cleanup, checks = [], [], []
    def write(path, raw, *args):
        if (fault == 'intent' and path.name == 'expected.json') or (fault == 'publish' and path.name == 'complete.json'):
            raise OSError('injected')
        if fault == 'log_and_cleanup' and path.name in ('expected.json', 'error.json'):
            raise OSError('injected')
        with path.open('xb') as stream:
            stream.write(raw)
    def check(*args, **kwargs):
        checks.append(kwargs)
        if len(checks) == 3 and fault in ('terminate_after_remove', 'deadline_after_remove'):
            assert not guards['auth'].exists()
            module.signal.raise_signal(module.signal.SIGTERM if fault == 'terminate_after_remove' else module.signal.SIGALRM)
        if fault == 'remove' and len(checks) == 3:
            raise RuntimeError()
        if fault == 'final' and set(kwargs.get('fresh', {})) == {'auth', 'ui'}:
            raise RuntimeError()
        return dict(boot_id='native-boot')
    def command(*args, **kwargs):
        commands.append(args)
        if args == ('start', 'auth') and fault == 'terminate_after_auth_start':
            module.signal.raise_signal(module.signal.SIGTERM)
        if args == ('start', 'auth') and fault == 'start_auth':
            raise TimeoutError()
        if args == ('start', 'ui') and fault == 'start_ui':
            raise TimeoutError()
    def ready():
        if fault == 'readiness':
            raise TimeoutError()
        return dict(invocation='new')
    def contain():
        cleanup.append(True)
        if fault in ('terminate_after_remove', 'terminate_after_auth_start', 'deadline_after_remove'):
            assert module.signal.getsignal(module.signal.SIGTERM) == module.signal.SIG_IGN
            module.signal.raise_signal(module.signal.SIGTERM)
        if fault == 'log_and_cleanup':
            raise OSError()
        return True
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'new.json').write_bytes(b'{}')
    backend = SimpleNamespace(check=check, recovery=recovery, guards=guards, guard=b'guard', released=set(),
        io=SimpleNamespace(write_new=write, sync_directory=lambda path: None),
        restart=SimpleNamespace(record=lambda descriptor, value: None), wait_auth_ready=ready,
        wait_ui_ready=lambda auth: dict(pid=3), uid=994, expected=dict(boot_id='boot'),
        contain_release_failure=contain)
    policy = SimpleNamespace(AUTH='auth', UI='ui', IDLE='idle', RETAINED={'native', 'model'},
        REPORTS=reports, read=lambda path, *args: path.read_bytes(), systemctl=command,
        get=lambda *args: {'locked': True})
    if fault:
        with pytest.raises((OSError, RuntimeError, TimeoutError, module.RecoveryCancelled, module.RecoveryDeadline)):
            module.recover(policy, backend, b'{}')
        assert cleanup == [True]
        assert not (recovery / 'complete.json').exists()
    else:
        receipt = module.recover(policy, backend, b'{}')
        assert receipt['historical_cleanup'] == 'unclaimed'
        assert receipt['owner_unlock_required'] is True
        assert commands.count(('start', 'auth')) == commands.count(('start', 'ui')) == 1
        with pytest.raises(FileExistsError):
            module.recover(policy, backend, b'{}')
    assert old.read_bytes() == b'failed release evidence'
    assert guards['native'].read_bytes() == guards['model'].read_bytes() == b'guard'
    assert not any(args[0] == 'start' and args[1] not in ('auth', 'ui') for args in commands)
    if (recovery / 'error.json').exists():
        assert set(json.loads((recovery / 'error.json').read_bytes())) == {'stage', 'error_class', 'readiness'}


def test_recovery_command_deadline_and_separate_cleanup_budget():
    calls = []
    policy = SimpleNamespace(systemctl=lambda *args, **kwargs: calls.append(kwargs['timeout']))
    backend = SimpleNamespace()
    boundary = module.RecoveryBoundary(policy, backend)
    prior = {number: module.signal.getsignal(number)
        for number in (module.signal.SIGTERM, module.signal.SIGINT, module.signal.SIGALRM)}
    try:
        boundary.enter()
        boundary.deadline = module.time.monotonic() - 1
        with pytest.raises(module.RecoveryDeadline):
            policy.systemctl('start', 'auth', timeout=45)
        assert not calls
        boundary.cleanup()
        policy.systemctl('stop', 'auth', timeout=500)
        assert 0 < calls[0] <= 120
        assert backend.deadline == boundary.deadline
    finally:
        boundary.close()
    assert module.signal.getitimer(module.signal.ITIMER_REAL) == (0.0, 0.0)
    assert all(module.signal.getsignal(number) == handler for number, handler in prior.items())
