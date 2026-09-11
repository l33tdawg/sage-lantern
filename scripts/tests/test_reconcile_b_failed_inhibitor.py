import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import signal
import errno
import sys

import pytest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('reconcile_failed_b', ROOT / 'scripts/lantern/reconcile-b-failed-inhibitor.py')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


@pytest.fixture
def broker(monkeypatch):
    from sage_lantern import deployment_bringup as runtime, mesh_daemon, device_access
    inventory = {'/fixed/config': MODULE.sha(b'config')}
    monkeypatch.setattr(runtime, 'read_public', lambda path:
        b'config' if path == '/fixed/config' else b'policy')
    monkeypatch.setattr(mesh_daemon, 'load_config', lambda: {})
    monkeypatch.setattr(mesh_daemon, 'device_policy', lambda config: 'policy')
    monkeypatch.setattr(device_access, '_marker_state', lambda path: True)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: False)
    def show(unit, deadline, fields):
        return dict(FragmentPath=runtime.SYSTEMD + unit, LoadState='loaded',
            NeedDaemonReload='no', Restart='no', DropInPaths='')
    system = SimpleNamespace(_show=Mock(side_effect=show), clock=lambda: 0,
        verify_sleep_idle=Mock(side_effect=AssertionError('must not synthesize ordinary readiness')))
    return SimpleNamespace(inventory=inventory, system=system), runtime, device_access


def test_explicit_non_sleep_checks_do_not_fake_ordinary_result(broker):
    owner, runtime, access = broker
    assert MODULE.broker_inputs_non_sleep(owner) is None
    owner.system.verify_sleep_idle.assert_not_called()
    assert owner.system._show.call_count == 2


@pytest.mark.parametrize('change', ['inventory', 'policy', 'marker', 'fence', 'restart', 'dropin', 'reload', 'fragment'])
def test_non_sleep_predicates_fail_closed(broker, monkeypatch, change):
    owner, runtime, access = broker
    if change == 'inventory':
        owner.inventory['/fixed/config'] = 'bad'
    elif change == 'policy':
        monkeypatch.setattr(runtime, 'read_public', lambda path: b'config')
    elif change == 'marker':
        monkeypatch.setattr(access, '_marker_state', lambda path: False)
    elif change == 'fence':
        monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path: True)
    else:
        original = owner.system._show.side_effect
        field, value = {'restart': ('Restart', 'always'), 'dropin': ('DropInPaths', '/unknown'),
            'reload': ('NeedDaemonReload', 'yes'), 'fragment': ('FragmentPath', '/unknown')}[change]
        owner.system._show.side_effect = lambda *args: {**original(*args), field: value}
    with pytest.raises(ValueError):
        MODULE.broker_inputs_non_sleep(owner)


def test_transition_orders_replacement_before_retirement_and_ordinary_checks():
    events = []
    backend = Mock()
    backend.old.TARGETS = ['sleep.target', 'suspend.target']
    backend.identify.side_effect = lambda: events.append('owned')
    backend.inspect.side_effect = lambda: events.append('ordinary' if backend.ordinary is True else 'masked')
    backend.retire.side_effect = lambda: events.append('retire')
    backend.unmask.side_effect = lambda name: events.append(name)
    backend.native.policy.preflight.SuspendSystem.return_value.verify_sleep_idle.return_value.confirmed = True
    MODULE.transition(backend)
    assert events == ['owned', 'masked', 'retire', 'sleep.target', 'suspend.target', 'ordinary']
    backend.record.assert_called_once()
    assert backend.record.call_args.args[0] == 'release-authorized'


@pytest.mark.parametrize('stage', ['identify', 'inspect', 'retire', 'unmask'])
def test_transition_never_authorizes_after_failure(stage):
    backend = Mock()
    backend.old.TARGETS = ['sleep.target']
    getattr(backend, stage).side_effect = RuntimeError('private-message')
    with pytest.raises(RuntimeError):
        MODULE.transition(backend)
    backend.record.assert_not_called()


def test_ordinary_confirmation_required_not_truthy_mock():
    backend = Mock()
    backend.old.TARGETS = []
    backend.native.policy.preflight.SuspendSystem.return_value.verify_sleep_idle.return_value.confirmed = False
    with pytest.raises(ValueError):
        MODULE.transition(backend)
    backend.record.assert_not_called()


def test_diagnostics_do_not_include_private_exception_message():
    try:
        raise RuntimeError('SECRET private response')
    except RuntimeError as error:
        value = MODULE.failure(error, 'ordinary_completion')
    assert 'SECRET' not in str(value)
    assert value['historical_cause'] == 'unknown'
    assert value['frames'][-1]['function'] == 'test_diagnostics_do_not_include_private_exception_message'


@pytest.mark.parametrize('cleanup_fails', [False, True])
def test_cleanup_precedes_logging_even_when_journal_unwritable(monkeypatch, cleanup_fails):
    events = []
    backend = Mock(stage='unmask:sleep.target')
    def cleanup():
        events.append('cleanup')
        if cleanup_fails:
            raise TimeoutError()
    backend.cleanup.side_effect = cleanup
    backend.record.side_effect = lambda *args: events.append('record')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', Mock())
    MODULE.protected_failure(backend, InterruptedError())
    assert events == ['cleanup', 'record']
    assert backend.record.call_args.args[1]['containment_verified'] is (not cleanup_fails)
    assert MODULE.signal.alarm.call_args_list[1].args == (40,)


def test_pidfd_signal_only_after_replacement_proof(monkeypatch):
    backend = object.__new__(MODULE.Backend)
    backend.old_owner = {'holder': {'pid': 50}, 'wrapper': {'pid': 49}}
    backend.owner = None
    backend.pins = Mock()
    backend.protection = Mock()
    backend.record = Mock()
    monkeypatch.setattr(MODULE.os, 'pidfd_open', Mock(return_value=7), raising=False)
    monkeypatch.setattr(MODULE.os, 'close', Mock())
    monkeypatch.setattr(MODULE.signal, 'pidfd_send_signal', Mock(), raising=False)
    with pytest.raises(ValueError):
        backend.retire()
    MODULE.signal.pidfd_send_signal.assert_not_called()
    MODULE.os.close.assert_called_once_with(7)


def test_prior_json_duplicates_refused():
    with pytest.raises(ValueError):
        MODULE.decode('{"boot_id":"first","boot_id":"second"}')


def test_non_sleep_gate_does_not_edit_runtime_source():
    source = (ROOT / 'scripts/lantern/reconcile-b-failed-inhibitor.py').read_text()
    assert 'broker.inputs()' in source
    assert 'system.verify_sleep_idle().confirmed is True' in source
    assert 'historical_cause=\'unknown\'' in source
    assert 'setattr(' not in source and 'monkeypatch' not in source


def test_historical_epoch_separate_and_not_rewritten():
    historical = dict(unit_id='unit-b', boot_id='boot', access_epoch='historical',
        baseline={'source': 'pinned'}, model={'pid': 5}, tools_sha256='tools')
    original = MODULE.encoded(historical)
    MODULE.current_handoff(historical, {'source': 'pinned'}, {'pid': 5}, 'tools', 'boot', 'fresh', 'fresh')
    assert MODULE.encoded(historical) == original
    for epoch in (None, '', 'changed'):
        with pytest.raises(ValueError):
            MODULE.current_handoff(historical, {'source': 'pinned'}, {'pid': 5}, 'tools', 'boot', epoch, 'fresh')


@pytest.mark.parametrize('field', ['unit_id', 'boot_id', 'baseline', 'model', 'tools_sha256'])
def test_current_handoff_never_excuses_non_epoch_changes(field):
    historical = dict(unit_id='unit-b', boot_id='boot', access_epoch='old',
        baseline={'source': 'pinned'}, model={'pid': 5}, tools_sha256='tools')
    historical[field] = 'wrong'
    with pytest.raises(ValueError):
        MODULE.current_handoff(historical, {'source': 'pinned'}, {'pid': 5}, 'tools', 'boot', 'new', 'new')


def test_cleanup_restores_unlink_interruption_window(tmp_path):
    backend = object.__new__(MODULE.Backend)
    backend.owner = {'present': True}
    backend.owned = Mock()
    backend.raw = b'{}'
    backend.old = SimpleNamespace(TARGETS=['sleep.target'], MASK_ROOT=tmp_path,
        signature=MODULE.signature)
    backend.expected = {'masks': {'sleep.target': ['old-signature']}}
    backend.removed = set()
    backend.unlink_intent = {'sleep.target'}
    backend.helper = SimpleNamespace(parents=Mock(), sync=Mock())
    backend.environment = SimpleNamespace(reload=Mock(), mask_state=Mock(return_value={'sleep.target': ['new']}),
        idle=Mock())
    backend.record = Mock()
    backend.cleanup()
    assert (tmp_path / 'sleep.target').is_symlink()
    assert (tmp_path / 'sleep.target').readlink() == Path('/dev/null')
    backend.owned.assert_called()
    backend.environment.reload.assert_called_once()


def test_cleanup_does_not_overwrite_replacement_mask(tmp_path):
    backend = object.__new__(MODULE.Backend)
    backend.owner = {'present': True}
    backend.owned = Mock()
    backend.raw = b'{}'
    backend.old = SimpleNamespace(TARGETS=['sleep.target'], MASK_ROOT=tmp_path,
        signature=MODULE.signature)
    backend.expected = {'masks': {'sleep.target': ['old-signature']}}
    backend.unlink_intent = {'sleep.target'}
    backend.helper = SimpleNamespace(parents=Mock(), sync=Mock())
    (tmp_path / 'sleep.target').symlink_to('/unexpected')
    with pytest.raises(ValueError):
        backend.cleanup()
    assert (tmp_path / 'sleep.target').readlink() == Path('/unexpected')


def test_journal_failure_does_not_skip_protected_cleanup(monkeypatch):
    backend = Mock(stage='ordinary_completion')
    backend.record.side_effect = OSError()
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    MODULE.protected_failure(backend, RuntimeError())
    backend.cleanup.assert_called_once()


@pytest.fixture
def retirement(monkeypatch):
    backend = object.__new__(MODULE.Backend)
    backend.old_owner = {
        'wrapper': dict(pid=49, start_ticks=10, argv=['old-wrapper'], image_sha256='old-wrapper-image'),
        'holder': dict(pid=50, parent=49, start_ticks=11, argv=['old-holder'], image_sha256='old-holder-image')}
    backend.owner = {
        'wrapper': dict(pid=59, start_ticks=20, argv=['new-wrapper'], image_sha256='new-wrapper-image'),
        'holder': dict(pid=60, parent=59, start_ticks=21, argv=['new-holder'], image_sha256='new-holder-image')}
    backend.raw, backend.prior_raw = b'new', b'old'
    backend.releaser = SimpleNamespace(WHO='old-who')
    backend.retired = False
    backend.deadline = 100
    backend.pins = Mock()
    backend.masks = Mock()
    backend.record = Mock()
    state = dict(alive=True, row=True, now=0, mismatch=None, linger=False, wrapper_alive=False)
    events = []
    def process(pid):
        events.append(('observe', pid))
        for owner in (backend.old_owner, backend.owner):
            for name, pin in owner.items():
                if pin['pid'] == pid:
                    value = dict(pin)
                    value.setdefault('parent', 1)
                    if state['mismatch'] == pid:
                        value['start_ticks'] += 1
                    return value
        raise ProcessLookupError()
    def rows():
        value = [['sleep', MODULE.WHO, MODULE.sha(backend.raw), 'block', 0, 59]]
        if state['row']:
            value.append(['sleep', 'old-who', MODULE.sha(backend.prior_raw), 'block', 0, 49])
        return value
    backend.environment = SimpleNamespace(process=process, rows=rows)
    def send(descriptor, number):
        events.append(('signal', descriptor, number))
        state['alive'] = False
        if not state['linger']:
            state['row'] = False
    monkeypatch.setattr(MODULE.os, 'pidfd_open', Mock(return_value=7), raising=False)
    monkeypatch.setattr(MODULE.os, 'close', Mock())
    monkeypatch.setattr(MODULE.signal, 'pidfd_send_signal', Mock(side_effect=send), raising=False)
    monkeypatch.setattr(MODULE.os.path, 'lexists', lambda path:
        state['alive'] or (str(path) == '/proc/49' and state['wrapper_alive']))
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: state['now'])
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: state.update(now=state['now'] + seconds))
    return backend, state, events


def test_retire_success_proves_both_owners_then_exact_pidfd_signal(retirement):
    backend, state, events = retirement
    backend.retire()
    assert backend.retired is True
    MODULE.os.pidfd_open.assert_called_once_with(50)
    MODULE.signal.pidfd_send_signal.assert_called_once_with(7, signal.SIGKILL)
    MODULE.os.close.assert_called_once_with(7)
    signalled = events.index(('signal', 7, signal.SIGKILL))
    assert all(('observe', pid) in events[:signalled] for pid in (49, 50, 59, 60))
    assert any(call.args[0] == 'retired' for call in backend.record.call_args_list)


@pytest.mark.parametrize('pid', [49, 50, 59, 60])
def test_retire_identity_mismatch_never_signals(retirement, pid):
    backend, state, events = retirement
    state['mismatch'] = pid
    with pytest.raises(ValueError):
        backend.retire()
    MODULE.signal.pidfd_send_signal.assert_not_called()
    MODULE.os.close.assert_called_once_with(7)
    assert backend.retired is False
    backend.record.assert_not_called()


@pytest.mark.parametrize('where', ['open', 'signal'])
def test_retire_esrch_remains_unknown_not_success(retirement, where):
    backend, state, events = retirement
    error = ProcessLookupError(errno.ESRCH, 'gone')
    if where == 'open':
        MODULE.os.pidfd_open.side_effect = error
    else:
        MODULE.signal.pidfd_send_signal.side_effect = error
    with pytest.raises(ProcessLookupError):
        backend.retire()
    assert backend.retired is False
    assert not any(call.args[0] == 'retired' for call in backend.record.call_args_list)
    if where == 'open':
        MODULE.os.close.assert_not_called()
        MODULE.signal.pidfd_send_signal.assert_not_called()
    else:
        MODULE.os.close.assert_called_once_with(7)


def test_retire_wrapper_timeout_retains_unknown(retirement):
    backend, state, events = retirement
    state['wrapper_alive'] = True
    with pytest.raises(TimeoutError):
        backend.retire()
    assert backend.retired is False
    assert state['now'] >= 15
    MODULE.signal.pidfd_send_signal.assert_called_once()
    assert events.count(('observe', 60)) > 2


def test_retire_lingering_logind_row_refuses_success(retirement):
    backend, state, events = retirement
    state['linger'] = True
    with pytest.raises(ValueError):
        backend.retire()
    assert backend.retired is False
    assert not any(call.args[0] == 'retired' for call in backend.record.call_args_list)


@pytest.fixture
def partial_masks(tmp_path, monkeypatch):
    backend = object.__new__(MODULE.Backend)
    targets = ['sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target']
    backend.old = SimpleNamespace(TARGETS=targets, MASK_ROOT=tmp_path, signature=MODULE.signature)
    for name in targets:
        (tmp_path / name).symlink_to('/dev/null')
    originals = {name: MODULE.signature((tmp_path / name).lstat()) for name in targets}
    backend.expected = {'masks': originals}
    backend.raw = b'new-attempt'
    backend.owner = {'replacement_owned': True}
    backend.retired = True
    backend.removed = set()
    backend.unlink_intent = set()
    backend.pins = Mock()
    backend.owned = Mock()
    backend.record = Mock()
    backend.helper = SimpleNamespace(parents=Mock(), sync=Mock())
    environment = SimpleNamespace(removed=set(), masks=originals, reload=Mock(), idle=Mock())
    def mask_state():
        observed = {}
        for name in targets:
            path = tmp_path / name
            if name in environment.removed:
                assert not path.is_symlink() and not path.exists()
            else:
                assert path.is_symlink() and path.readlink() == Path('/dev/null')
                observed[name] = MODULE.signature(path.lstat())
                if environment.masks is not None:
                    assert observed[name] == environment.masks[name]
        return observed
    environment.mask_state = mask_state
    backend.environment = environment
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    return backend, tmp_path


@pytest.mark.parametrize('boundary', [1, 2, 3, 4])
@pytest.mark.parametrize('fault', ['after_boundary', 'reload_error', 'reload_cancel', 'unlink_cancel'])
def test_each_partial_unmask_boundary_restores_all_masks(partial_masks, monkeypatch, boundary, fault):
    backend, root = partial_masks
    target = backend.old.TARGETS[boundary - 1]
    old_unlink = Path.unlink
    if fault == 'unlink_cancel':
        def unlink(path, *args, **kwargs):
            old_unlink(path, *args, **kwargs)
            if path == root / target:
                raise InterruptedError('after unlink before removed bookkeeping')
        monkeypatch.setattr(Path, 'unlink', unlink)
    calls = {'reload': 0}
    def reload():
        calls['reload'] += 1
        if calls['reload'] == boundary:
            if fault == 'reload_error':
                raise OSError('reload failed')
            if fault == 'reload_cancel':
                raise InterruptedError('cancelled during reload')
    backend.environment.reload.side_effect = reload
    try:
        for name in backend.old.TARGETS[:boundary]:
            backend.stage = 'unmask:' + name
            backend.unmask(name)
        assert fault == 'after_boundary'
        raise InterruptedError('cancelled after boundary')
    except (InterruptedError, OSError) as error:
        MODULE.protected_failure(backend, error)
    assert all((root / name).readlink() == Path('/dev/null') for name in backend.old.TARGETS)
    assert backend.owner == {'replacement_owned': True}
    backend.owned.assert_called()
    records = {call.args[0]: call.args[1] for call in backend.record.call_args_list}
    assert records['failed-held']['containment_verified'] is True
    assert records['failed-held']['stage'] == 'unmask:' + target
    assert records['failed-held']['retry_authorized'] is False
    assert 'release-authorized' not in records
    assert 'complete' not in records
