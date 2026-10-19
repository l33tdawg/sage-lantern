from dataclasses import replace
import importlib.util
import ast
import io
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sage_lantern.suspend_kiosk import KioskBinding, Process


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('display_session', ROOT / 'scripts/lantern/lantern-display-session.py')
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def session_properties(**changes):
    fields = dict(Id='92', User='1000', Name='radxa', Seat='seat0', Active='yes', Remote='no',
                  Type='x11', Class='user', Service='gdm-autologin', State='active',
                  Scope='session-92.scope', Display=':1')
    fields.update(changes)
    return '\n'.join(name + '=' + value for name, value in fields.items())


def environment(**changes):
    fields = dict(DISPLAY=':1', XAUTHORITY='/run/user/1000/gdm/Xauthority',
                  XDG_SESSION_ID='92', XDG_SESSION_TYPE='x11', XDG_SEAT='seat0')
    fields.update(changes)
    return b'\0'.join(name.encode() + b'=' + value.encode() for name, value in fields.items()) + b'\0'


def fixture_session(display=':1', session='92', pid=334384, ticks=12345):
    binding = KioskBinding('radxa', 1000, 'unit-a')
    launcher = Process(pid, 100, ticks, 1000, '/usr/bin/bash', binding.launcher_argv[0], 'S')
    return MODULE.Session(binding, session, launcher, display, '/run/user/1000/gdm/Xauthority', (1,), (2,))


@pytest.mark.parametrize('changes', [dict(User='0'), dict(Name='gdm'), dict(Seat='seat1'),
                                    dict(Active='no'), dict(Remote='yes'), dict(Type='wayland'),
                                    dict(Class='greeter'), dict(Service='sshd'), dict(State='closing'),
                                    dict(Scope='session-c1.scope'), dict(Id='c1')])
def test_remote_greeter_inactive_wrong_user_sessions_rejected(changes):
    with pytest.raises(ValueError):
        MODULE.session_fields(session_properties(**changes), fixture_session().binding, '92')


def test_exact_active_gdm_session_and_password_login_supported():
    for service in ('gdm-autologin', 'gdm-password'):
        assert MODULE.session_fields(session_properties(Service=service), fixture_session().binding, '92')['Display'] == ':1'
    with pytest.raises(ValueError):
        MODULE.properties(session_properties() + '\nUser=1000')


@pytest.mark.parametrize('display', ['', ':', 'localhost:1', 'remote:0', 'unix:1', ':12345', ':1.123', ':1\n', ':1;id'])
def test_nonlocal_or_malformed_environment_rejected(display):
    with pytest.raises(ValueError):
        MODULE.selected_environment(environment(DISPLAY=display), fixture_session().binding, '92', '')


@pytest.mark.parametrize('changes', [dict(XAUTHORITY='/tmp/auth'), dict(XDG_SESSION_ID='c1'),
                                    dict(XDG_SESSION_TYPE='wayland'), dict(XDG_SEAT='seat1')])
def test_launcher_environment_must_match_authoritative_session(changes):
    with pytest.raises(ValueError):
        MODULE.selected_environment(environment(**changes), fixture_session().binding, '92', ':1')


def test_environment_does_not_inherit_remote_display_or_leak_other_values(monkeypatch):
    monkeypatch.setenv('DISPLAY', 'remote:0')
    raw = environment() + b'PRIVATE_UNRELATED_VALUE=not-returned\0'
    assert MODULE.selected_environment(raw, fixture_session().binding, '92', ':1') == (
        ':1', '/run/user/1000/gdm/Xauthority')
    with pytest.raises(ValueError):
        MODULE.selected_environment(raw + b'DISPLAY=:0\0', fixture_session().binding, '92', ':1')
    with pytest.raises(ValueError):
        MODULE.selected_environment(environment(), fixture_session().binding, '92', ':0')


@pytest.fixture
def native(monkeypatch):
    backend = Mock()
    current = fixture_session()
    backend.snapshot.return_value = {current.launcher.pid: current.launcher}
    backend.read.return_value = current.launcher
    backend.exited.return_value = False
    backend.fence_state.return_value = 'absent'
    monkeypatch.setattr(MODULE, 'LinuxProcesses', lambda: backend)
    runner = MODULE.Native()
    runner.command = Mock(side_effect=['92', session_properties(), '/user.slice/user-1000.slice/session-92.scope', '92'])
    runner.snapshot = lambda *args: backend.snapshot()
    def opening(path, *args, **kwargs):
        if path.name == 'environ':
            return io.BytesIO(environment())
        assert path.name == 'cgroup'
        return io.BytesIO(b'0::/user.slice/user-1000.slice/session-92.scope\n')
    monkeypatch.setattr(Path, 'open', opening)
    monkeypatch.setattr(MODULE, 'authority_identity', lambda *args: (1,))
    monkeypatch.setattr(MODULE, 'socket_identity', lambda *args: (2,))
    return runner, backend, current


def test_native_binds_active_92_display1_not_greeter_display0(native):
    runner, backend, current = native
    assert runner.inspect(current.binding) == current
    backend.open.assert_called_once_with(current.launcher)
    backend.close.assert_called_once()
    arguments = runner.command.call_args_list[1].args[0]
    assert '--all' in arguments
    assert arguments[5:] == [argument for field in MODULE.FIELDS for argument in ('-p', field)]
    assert not any(',' in argument for argument in arguments)


def test_native_rejects_session_scope_mismatch(native):
    runner, backend, current = native
    runner.command.side_effect = ['92', session_properties(), '/user.slice/user-1000.slice/session-c1.scope']
    with pytest.raises(ValueError, match='cgroup'):
        runner.inspect(current.binding)


def test_native_rejects_launcher_in_different_session(native, monkeypatch):
    runner, backend, current = native
    monkeypatch.setattr(Path, 'open', lambda *args, **kwargs: io.BytesIO(b'0::/user.slice/user-1000.slice/session-c1.scope\n'))
    with pytest.raises(ValueError, match='ambiguous or absent'):
        runner.inspect(current.binding)


def test_native_rejects_pid_reuse_during_environment_read(native):
    runner, backend, current = native
    backend.read.side_effect = [current.launcher, replace(current.launcher, start_ticks=999)]
    with pytest.raises(ValueError, match='launcher changed'):
        runner.inspect(current.binding)


def test_native_rejects_arbitrary_profile_and_duplicate_launchers(native):
    runner, backend, current = native
    backend.snapshot.return_value = {1: replace(current.launcher, argv=('bash', '/usr/local/bin/sage-lantern-kiosk', '--profile-dir', '/tmp/other'))}
    with pytest.raises(ValueError, match='ambiguous or absent'):
        runner.inspect(current.binding)


def test_native_rejects_duplicate_verified_launchers(native):
    runner, backend, current = native
    backend.snapshot.return_value = {334384: current.launcher, 334385: replace(current.launcher, pid=334385)}
    with pytest.raises(ValueError, match='ambiguous or absent'):
        runner.inspect(current.binding)


def test_native_retains_sleep_fence_without_x_actions(native):
    runner, backend, current = native
    backend.fence_state.return_value = 'present'
    with pytest.raises(ValueError, match='containment'):
        runner.inspect(current.binding)
    runner.command.assert_not_called()


def test_fresh_b_environment_uses_its_own_uid_and_session():
    binding = KioskBinding('operator', 1001, 'unit-b')
    fields = session_properties(User='1001', Name='operator', Id='93', Scope='session-93.scope', Display=':2')
    assert MODULE.session_fields(fields, binding, '93')['Name'] == 'operator'
    raw = environment(DISPLAY=':2', XAUTHORITY='/run/user/1001/gdm/Xauthority', XDG_SESSION_ID='93')
    assert MODULE.selected_environment(raw, binding, '93', ':2') == (':2', '/run/user/1001/gdm/Xauthority')


class StopAfter:
    def __init__(self, count):
        self.remaining = count
        self.now = 0.0

    def is_set(self):
        return self.remaining == 0

    def wait(self, delay):
        self.remaining = max(0, self.remaining - 1)
        self.now += delay


def test_supervisor_session_replacement_kills_old_group_before_new_launch():
    first = fixture_session()
    second = fixture_session(display=':2', session='93', pid=334500, ticks=13000)
    native = Mock()
    native.inspect.side_effect = [first, second, second]
    old, new = Mock(), Mock()
    old.poll.return_value = new.poll.return_value = None
    native.launch.side_effect = [old, new]
    stopped = StopAfter(15)
    MODULE.supervise(native, stopped, lambda: first.binding, clock=lambda: stopped.now)
    actions = [(call[0], call[1]) for call in native.mock_calls if call[0] in ('launch', 'stop')]
    assert actions == [('launch', (first,)), ('stop', (old, False)), ('launch', (second,)), ('stop', (new, True))]


@pytest.mark.parametrize('change', ['session', 'pid', 'ticks', 'uid', 'argv', 'authority', 'socket', 'display'])
def test_any_binding_change_invalidates_lease(change):
    first = fixture_session()
    changes = {
        'session': dict(session_id='93'), 'pid': dict(launcher=replace(first.launcher, pid=999)),
        'ticks': dict(launcher=replace(first.launcher, start_ticks=999)),
        'uid': dict(launcher=replace(first.launcher, uid=0)),
        'argv': dict(launcher=replace(first.launcher, argv=('bash',))),
        'authority': dict(authority_pin=(3,)), 'socket': dict(socket_pin=(4,)), 'display': dict(display=':0'),
    }
    assert not MODULE.same_session(first, replace(first, **changes[change]))
    assert MODULE.same_session(first, replace(first, launcher=replace(first.launcher, state='R')))


def test_session_loss_or_policy_revocation_never_runs_stale_wake_cleanup():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    native.cheap.side_effect = ValueError('session gone')
    child = native.launch.return_value
    child.poll.return_value = None
    stopped = StopAfter(2)
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    native.stop.assert_called_once_with(child, False)


def test_no_activation_without_approved_policy(monkeypatch):
    monkeypatch.setattr(MODULE, '_read_binding_file', Mock(side_effect=FileNotFoundError))
    native = Mock()
    MODULE.supervise(native, StopAfter(1), MODULE.approved_binding)
    native.inspect.assert_not_called()
    native.launch.assert_not_called()


def test_policy_binding_explicit_legacy_and_no_automatic_fallback(monkeypatch):
    monkeypatch.setattr(MODULE, '_read_binding_file', lambda *args: 'fresh unit-a\n')
    monkeypatch.setattr(MODULE, 'load_kiosk_binding', Mock(side_effect=FileNotFoundError))
    with pytest.raises(FileNotFoundError):
        MODULE.approved_binding()
    monkeypatch.setattr(MODULE, '_read_binding_file', lambda *args: 'legacy unit-a\n')
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda *args: SimpleNamespace(pw_uid=1000, pw_dir='/home/radxa'))
    assert MODULE.approved_binding() == KioskBinding.legacy()
    monkeypatch.setattr(MODULE, '_read_binding_file', lambda *args: 'legacy unit-b\n')
    with pytest.raises(ValueError):
        MODULE.approved_binding()


def test_launch_drops_privileges_clears_environment_and_passes_pinned_authority(monkeypatch):
    current = fixture_session()
    runner = MODULE.Native()
    runner.inspect = Mock(return_value=current)
    runner.cheap = Mock()
    runner.await_parked = Mock()
    monkeypatch.setattr(MODULE, '_trusted_file', lambda *args: None)
    monkeypatch.setattr(MODULE, 'sealed_authority', lambda *args: 42)
    monkeypatch.setattr(MODULE, 'Child', lambda process: process)
    monkeypatch.setattr(MODULE.os, 'close', Mock())
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda *args: SimpleNamespace(pw_uid=1000, pw_gid=1000,
                                                                         pw_name='radxa', pw_dir='/home/radxa'))
    launching = Mock()
    monkeypatch.setattr(MODULE.subprocess, 'Popen', launching)
    monkeypatch.setenv('DISPLAY', 'evil:0')
    monkeypatch.setenv('PYTHONPATH', '/evil')
    runner.launch(current)
    kwargs = launching.call_args.kwargs
    assert kwargs['user'] == kwargs['group'] == 1000
    assert kwargs['extra_groups'] == () and kwargs['start_new_session'] is True
    assert kwargs['pass_fds'] == (42,)
    assert kwargs['env']['DISPLAY'] == ':1'
    assert 'PYTHONPATH' not in kwargs['env'] and 'XAUTHORITY' not in kwargs['env']
    assert kwargs['env']['LANTERN_AUTH_FD'] == '42'
    MODULE.os.close.assert_called_once_with(42)


def test_check_only_does_not_launch_or_read_authority_contents(monkeypatch, capsys):
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    monkeypatch.setattr(MODULE, 'Native', lambda: native)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE, 'binding_for', lambda *args: current.binding)
    assert MODULE.main(['--check', '--unit-id', 'unit-a']) == 0
    native.launch.assert_not_called()
    assert '"display": ":1"' in capsys.readouterr().out


def test_native_stop_invalid_lease_sends_kill_not_term(monkeypatch):
    process = Mock(pid=500)
    monkeypatch.setattr(MODULE.os, 'pidfd_open', lambda pid: 42, raising=False)
    child = MODULE.Child(process)
    sending = Mock()
    monkeypatch.setattr(MODULE.os, 'killpg', sending)
    closing = Mock()
    monkeypatch.setattr(MODULE.os, 'close', closing)
    MODULE.Native().stop(child, False)
    assert sending.call_args_list[0].args == (500, signal.SIGKILL)
    assert all(call.args[1] != signal.SIGTERM for call in sending.call_args_list)
    child.process.wait.assert_called_once_with(timeout=1)
    closing.assert_called_once_with(42)


def test_child_exit_check_does_not_reap_leader_before_group_kill(monkeypatch):
    process = Mock(pid=500)
    monkeypatch.setattr(MODULE.os, 'pidfd_open', lambda pid: 42, raising=False)
    watcher = Mock()
    watcher.poll.return_value = [(42, 1)]
    monkeypatch.setattr(MODULE.select, 'poll', lambda: watcher, raising=False)
    child = MODULE.Child(process)
    assert child.poll() == 0
    process.poll.assert_not_called()
    process.wait.assert_not_called()


@pytest.mark.parametrize('change', ['owner', 'mode', 'symlink', 'hardlink', 'empty', 'oversized'])
def test_authority_metadata_rejects_unsafe_credentials(monkeypatch, change):
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o700, st_uid=0)
    file = dict(st_mode=stat.S_IFREG | 0o600, st_uid=1000, st_nlink=1, st_size=64)
    mutations = dict(owner=dict(st_uid=0), mode=dict(st_mode=stat.S_IFREG | 0o644),
                     symlink=dict(st_mode=stat.S_IFLNK | 0o777), hardlink=dict(st_nlink=2),
                     empty=dict(st_size=0), oversized=dict(st_size=65537))
    file.update(mutations[change])
    monkeypatch.setattr(Path, 'lstat', lambda path: SimpleNamespace(**file) if path.name == 'Xauthority' else directory)
    with pytest.raises(ValueError, match='unsafe authority'):
        MODULE.authority_identity('/run/user/1000/gdm/Xauthority', 1000)


def test_socket_cannot_be_regular_file_or_foreign_owned(monkeypatch):
    directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o1777, st_uid=0)
    for mode, uid in ((stat.S_IFREG | 0o777, 0), (stat.S_IFSOCK | 0o777, 120)):
        monkeypatch.setattr(Path, 'lstat', lambda path: directory if path.name == '.X11-unix'
                            else SimpleNamespace(st_mode=mode, st_uid=uid))
        with pytest.raises(ValueError, match='unsafe X socket'):
            MODULE.socket_identity(':1', 1000)


def test_authority_snapshot_is_sealed_memory_only_and_closed_on_failure(monkeypatch):
    current = replace(fixture_session(), authority_pin=(0, 1, 1000, stat.S_IFREG | 0o600, 1, 4, 0, 0))
    info = SimpleNamespace(st_dev=0, st_ino=1, st_uid=1000, st_mode=stat.S_IFREG | 0o600,
                           st_nlink=1, st_size=4, st_mtime_ns=0, st_ctime_ns=0)
    monkeypatch.setattr(MODULE, 'authority_identity', lambda *args: current.authority_pin)
    monkeypatch.setattr(MODULE.os, 'open', lambda *args: 41)
    monkeypatch.setattr(MODULE.os, 'fstat', lambda *args: info)
    monkeypatch.setattr(MODULE.os, 'read', lambda *args: b'test')
    monkeypatch.setattr(MODULE.os, 'memfd_create', lambda *args: 42, raising=False)
    for name, value in (('MFD_ALLOW_SEALING', 2), ('MFD_CLOEXEC', 1)):
        monkeypatch.setattr(MODULE.os, name, value, raising=False)
    for name, value in (('F_ADD_SEALS', 1033), ('F_SEAL_WRITE', 8), ('F_SEAL_GROW', 4),
                        ('F_SEAL_SHRINK', 2), ('F_SEAL_SEAL', 1)):
        monkeypatch.setattr(MODULE.fcntl, name, value, raising=False)
    monkeypatch.setattr(MODULE.os, 'write', lambda *args: 4)
    monkeypatch.setattr(MODULE.os, 'fchown', Mock())
    monkeypatch.setattr(MODULE.os, 'fchmod', Mock())
    closing = Mock()
    monkeypatch.setattr(MODULE.os, 'close', closing)
    sealing = Mock()
    monkeypatch.setattr(MODULE.fcntl, 'fcntl', sealing)
    assert MODULE.sealed_authority(current) == 42
    sealing.assert_called_once_with(42, 1033, 15)
    closing.assert_called_once_with(41)
    closing.reset_mock()
    sealing.side_effect = OSError('cannot seal')
    with pytest.raises(OSError):
        MODULE.sealed_authority(current)
    assert [call.args for call in closing.call_args_list] == [(42,), (41,)]


def test_steady_state_uses_cheap_checks_not_full_rediscovery():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    native.launch.return_value.poll.return_value = None
    stopped = StopAfter(9)
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    assert native.inspect.call_count == 1
    assert native.launch.call_count == 1
    assert native.cheap.call_count == 9
    native.launch.return_value.pause.assert_not_called()


def test_full_reconciliation_pauses_before_slow_work_and_resumes_only_after_checks():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    native.launch.return_value.poll.return_value = None
    stopped = StopAfter(12)
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    calls = [call[0] for call in native.mock_calls]
    pause = calls.index('launch().pause')
    assert calls[pause:pause + 5] == ['launch().pause', 'inspect', 'cheap', 'launch().renew', 'launch().resume']
    assert native.inspect.call_count == 2


def test_slow_reconciliation_cannot_renew_or_resume_expired_result():
    current = fixture_session()
    native = Mock()
    stopped = StopAfter(12)
    observations = 0
    def inspect(*args, **kwargs):
        nonlocal observations
        observations += 1
        if observations == 2:
            stopped.now += MODULE.VERIFY_SECONDS + 0.01
        return current
    native.inspect.side_effect = inspect
    child = native.launch.return_value
    child.poll.return_value = None
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    assert child.resume.call_count == child.renew.call_count == 1
    native.stop.assert_called_once_with(child, False)


def test_slow_acquisition_does_not_launch_unleased_helper():
    current = fixture_session()
    native = Mock()
    stopped = StopAfter(1)
    def inspect(*args, **kwargs):
        stopped.now += MODULE.VERIFY_SECONDS + 0.01
        return current
    native.inspect.side_effect = inspect
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    native.launch.assert_not_called()


def test_slow_cheap_check_revokes_without_wake():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    native.launch.return_value.poll.return_value = None
    stopped = StopAfter(2)
    native.cheap.side_effect = lambda *args, **kwargs: setattr(stopped, 'now', stopped.now + 1)
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    native.stop.assert_called_once_with(native.launch.return_value, False)


def test_helper_crashes_have_exponential_backoff():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    stopped = StopAfter(40)
    attempts = []
    def launch(*args, **kwargs):
        attempts.append(stopped.now)
        child = Mock()
        child.poll.return_value = 0
        return child
    native.launch.side_effect = launch
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    assert 3 <= len(attempts) <= 5
    gaps = [second - first for first, second in zip(attempts, attempts[1:])]
    assert gaps[:3] == [1.5, 2.5, 4.5]


def test_stop_during_acquisition_never_launches_or_runs_more_commands():
    current = fixture_session()
    native = Mock()
    stopped = StopAfter(1)
    def inspect(*args, **kwargs):
        stopped.remaining = 0
        return current
    native.inspect.side_effect = inspect
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    native.launch.assert_not_called()
    assert native.inspect.call_count == 1


def test_shutdown_uses_cheap_validation_not_full_inspection():
    current = fixture_session()
    native = Mock()
    native.inspect.return_value = current
    native.launch.return_value.poll.return_value = None
    stopped = StopAfter(1)
    MODULE.supervise(native, stopped, lambda: current.binding, clock=lambda: stopped.now)
    assert native.inspect.call_count == 1
    assert native.cheap.call_args.args[1] - stopped.now <= 0.25
    native.stop.assert_called_once_with(native.launch.return_value, True)


def test_commands_share_aggregate_deadline_and_stop_before_next_spawn(monkeypatch):
    now = [10.0]
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(MODULE, '_trusted_file', lambda *args: None)
    budgets = []
    def run(*args, **kwargs):
        budgets.append(kwargs['timeout'])
        now[0] += 1.1
        return SimpleNamespace(stdout=b'92\n')
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    native = MODULE.Native()
    assert native.command(['/usr/bin/loginctl'], deadline=12) == '92'
    with pytest.raises(ValueError, match='deadline'):
        native.command(['/usr/bin/loginctl'], deadline=12)
    assert budgets == pytest.approx([2.0, 0.9])
    stopped = threading.Event()
    stopped.set()
    with pytest.raises(ValueError, match='stopping'):
        native.command(['/usr/bin/loginctl'], deadline=20, stopped=stopped)
    assert len(budgets) == 2


def test_watchdog_kills_while_main_loop_is_waiting_and_cannot_be_revived(monkeypatch):
    monkeypatch.setattr(MODULE.os, 'pidfd_open', lambda pid: 42, raising=False)
    killed = threading.Event()
    signals = []
    def sending(pid, action):
        signals.append(action)
        if action == signal.SIGKILL:
            killed.set()
    monkeypatch.setattr(MODULE.os, 'killpg', sending)
    child = MODULE.Child(Mock(pid=500))
    child.poll = lambda: None
    child.renew(time.monotonic() + 0.03)
    assert killed.wait(1)
    with pytest.raises(ValueError, match='expired'):
        child.renew(time.monotonic() + 8)
    with pytest.raises(ValueError, match='expired'):
        child.resume()
    assert signal.SIGCONT not in signals


def test_old_watchdog_callback_cannot_kill_a_renewed_lease(monkeypatch):
    monkeypatch.setattr(MODULE.os, 'pidfd_open', lambda pid: 42, raising=False)
    timer = Mock()
    monkeypatch.setattr(MODULE.threading, 'Timer', lambda *args, **kwargs: timer)
    sending = Mock()
    monkeypatch.setattr(MODULE.os, 'killpg', sending)
    child = MODULE.Child(Mock(pid=500))
    child.poll = lambda: None
    first = time.monotonic() + 8
    child.renew(first)
    child.renew(first + 1)
    child._expire(first)
    sending.assert_not_called()
    child._expire(first + 1)
    sending.assert_called_once_with(500, signal.SIGKILL)


def test_new_child_parks_before_helper_exec_and_stop_grace_is_bounded(monkeypatch):
    assert MODULE.CHILD.index('SIGSTOP') < MODULE.CHILD.index('os.execv')
    monkeypatch.setattr(MODULE.os, 'pidfd_open', lambda pid: 42, raising=False)
    monkeypatch.setattr(MODULE.os, 'killpg', Mock())
    monkeypatch.setattr(MODULE.os, 'close', Mock())
    watcher = Mock()
    monkeypatch.setattr(MODULE.select, 'poll', lambda: watcher, raising=False)
    child = MODULE.Child(Mock(pid=500))
    child.poll = lambda: None
    child.deadline = time.monotonic() + 8
    child.finish(True)
    assert watcher.poll.call_args.args[0] <= 1000
    child.process.wait.assert_called_once_with(timeout=1)


def test_cheap_native_check_has_no_subprocess_or_snapshot(native):
    runner, backend, current = native
    runner.snapshot = Mock(side_effect=AssertionError('no full scan'))
    runner.cheap(current, time.monotonic() + 1)
    runner.command.assert_not_called()
    runner.snapshot.assert_not_called()


@pytest.fixture
def idle_query(monkeypatch):
    current = fixture_session()
    runner = MODULE.Native()
    runner.inspect = Mock(return_value=current)
    runner.cheap = Mock()
    runner.await_parked = Mock()
    trusted = Mock()
    monkeypatch.setattr(MODULE, '_trusted_file', trusted)
    monkeypatch.setattr(MODULE, 'sealed_authority', Mock(return_value=42))
    monkeypatch.setattr(MODULE.os, 'close', Mock())
    monkeypatch.setattr(MODULE.pwd, 'getpwnam', lambda *args: SimpleNamespace(
        pw_uid=1000, pw_gid=1000, pw_name='radxa', pw_dir='/home/radxa'))
    process = Mock(pid=500, returncode=0)
    monkeypatch.setattr(MODULE.subprocess, 'Popen', Mock(return_value=process))
    child = Mock(descriptor=43)
    monkeypatch.setattr(MODULE, 'Child', Mock(return_value=child))
    monkeypatch.setattr(MODULE.select, 'select', Mock(side_effect=lambda *args: (args[0], [], [])))
    monkeypatch.setattr(MODULE.os, 'read', Mock(side_effect=[b'123\n', b'']))
    return runner, current, process, child, trusted


def test_x11_check_only_executes_fixed_installed_idle_query(idle_query, monkeypatch):
    runner, current, process, child, trusted = idle_query
    monkeypatch.setenv('DISPLAY', 'remote:0')
    monkeypatch.setenv('PYTHONPATH', '/untrusted')
    assert runner.check_x11(current.binding) == current
    arguments, = MODULE.subprocess.Popen.call_args.args
    assert arguments == ['/usr/bin/python3', '-I', '-B', '-c', MODULE.QUERY_CHILD]
    tree = ast.parse(MODULE.QUERY_CHILD)
    execution = tree.body[-1].value
    assert ast.literal_eval(execution.args[1]) == [
        '/usr/bin/python3', '-I', '-B', MODULE.HELPER, '--query-idle']
    assert MODULE.QUERY_CHILD.index('SIGSTOP') < MODULE.QUERY_CHILD.index('os.execv')
    options = MODULE.subprocess.Popen.call_args.kwargs
    assert options['user'] == options['group'] == 1000
    assert options['extra_groups'] == () and options['pass_fds'] == (42,)
    assert options['env'] == {**MODULE.ENV, 'HOME': '/home/radxa', 'USER': 'radxa',
                              'DISPLAY': ':1', 'LANTERN_AUTH_FD': '42'}
    assert options['stderr'] == subprocess.DEVNULL
    assert Path(MODULE.HELPER) in [call.args[0] for call in trusted.call_args_list]
    assert runner.inspect.call_count == 2
    deadline = runner.inspect.call_args_list[0].kwargs['deadline']
    assert runner.inspect.call_args_list[1].kwargs['deadline'] == deadline
    child.renew.assert_called_once_with(deadline)
    child.resume.assert_called_once_with()
    assert all(call.args == (False,) for call in child.finish.call_args_list)
    process.stdout.close.assert_called_once_with()
    MODULE.os.close.assert_called_once_with(42)


@pytest.mark.parametrize('output', [b'', b'None\n', b'-1\n', b'1.2\n', b'12\n34\n',
                                    b' 1\n', b'18446744073709551616\n', b'1' * 22])
def test_x11_check_rejects_missing_invalid_or_oversize_idle(idle_query, output):
    runner, current, process, child, _ = idle_query
    MODULE.os.read.side_effect = [output, b'']
    with pytest.raises(ValueError, match='idle'):
        runner.check_x11(current.binding)
    child.finish.assert_called_with(False)
    process.stdout.close.assert_called_once_with()
    MODULE.os.close.assert_called_once_with(42)


def test_x11_check_rejects_helper_failure(idle_query):
    runner, current, process, _, _ = idle_query
    process.returncode = 1
    with pytest.raises(ValueError, match='invalid idle'):
        runner.check_x11(current.binding)


def test_x11_check_timeout_kills_and_closes_without_wake(idle_query):
    runner, current, process, child, _ = idle_query
    MODULE.select.select.side_effect = lambda *args: ([], [], [])
    with pytest.raises(ValueError, match='timed out'):
        runner.check_x11(current.binding)
    child.finish.assert_called_once_with(False)
    process.stdout.close.assert_called_once_with()
    MODULE.os.close.assert_called_once_with(42)


def test_x11_check_rejects_changed_session_after_query(idle_query):
    runner, current, _, _, _ = idle_query
    runner.inspect.side_effect = [current, replace(current, display=':0')]
    with pytest.raises(ValueError, match='session changed'):
        runner.check_x11(current.binding)


def test_x11_check_rejects_late_postinspection(idle_query, monkeypatch):
    runner, current, _, _, _ = idle_query
    now = [10.0]
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: now[0])
    def inspect(*args, **kwargs):
        if runner.inspect.call_count == 2:
            now[0] = 13.0
        return current
    runner.inspect.side_effect = inspect
    with pytest.raises(ValueError, match='deadline'):
        runner.check_x11(current.binding)


def test_x11_check_untrusted_installed_source_never_spawns(idle_query):
    runner, current, _, _, trusted = idle_query
    trusted.side_effect = [None, RuntimeError('untrusted executable path')]
    with pytest.raises(RuntimeError, match='untrusted'):
        runner.check_x11(current.binding)
    MODULE.sealed_authority.assert_not_called()
    MODULE.subprocess.Popen.assert_not_called()


def test_x11_check_launch_failure_closes_authority(idle_query):
    runner, current, _, _, _ = idle_query
    MODULE.subprocess.Popen.side_effect = OSError('spawn failed')
    with pytest.raises(OSError):
        runner.check_x11(current.binding)
    MODULE.os.close.assert_called_once_with(42)


def test_x11_cli_reports_capability_only_and_never_activates(monkeypatch, capsys):
    current = fixture_session()
    native = Mock()
    native.check_x11.return_value = current
    monkeypatch.setattr(MODULE, 'Native', lambda: native)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    binding = Mock(return_value=current.binding)
    monkeypatch.setattr(MODULE, 'binding_for', binding)
    assert MODULE.main(['--check-x11', '--unit-id', 'unit-a']) == 0
    binding.assert_called_once_with('fresh', 'unit-a')
    assert capsys.readouterr().out == ('{"verified": true, "activated": false, '
                                     '"unit_id": "unit-a", "display": ":1", "idle_query": true}\n')
    native.launch.assert_not_called()
    assert MODULE.main(['--check-x11']) == 2


@pytest.mark.parametrize('unit,legacy', [('unit-b', False), ('unit-a', True)])
def test_x11_cli_passes_explicit_binding_selection(monkeypatch, unit, legacy):
    native = Mock()
    native.check_x11.return_value = fixture_session()
    monkeypatch.setattr(MODULE, 'Native', lambda: native)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    binding = Mock(return_value=fixture_session().binding)
    monkeypatch.setattr(MODULE, 'binding_for', binding)
    assert MODULE.main(['--check-x11', '--unit-id', unit] + (['--legacy'] if legacy else [])) == 0
    binding.assert_called_once_with('legacy' if legacy else 'fresh', unit)


def test_x11_parking_timeout_never_resumes_query(idle_query):
    runner, current, process, child, _ = idle_query
    runner.await_parked.side_effect = ValueError('deadline expired')
    with pytest.raises(ValueError, match='deadline'):
        runner.check_x11(current.binding)
    child.resume.assert_not_called()
    child.finish.assert_called_once_with(False)
    process.stdout.close.assert_called_once_with()
    MODULE.os.close.assert_called_once_with(42)


@pytest.mark.parametrize('unsafe', ['helper', 'parent'])
def test_x11_source_trust_rejects_symlink_or_writable_parent(idle_query, monkeypatch, unsafe):
    runner, current, _, _, _ = idle_query
    from sage_lantern.suspend_kiosk import _trusted_file
    def trust(path):
        if path == Path(MODULE.HELPER):
            _trusted_file(path)
    monkeypatch.setattr(MODULE, '_trusted_file', trust)
    def metadata(path):
        mode = stat.S_IFREG | 0o755 if path == Path(MODULE.HELPER) else stat.S_IFDIR | 0o755
        if unsafe == 'helper' and path == Path(MODULE.HELPER):
            mode = stat.S_IFLNK | 0o777
        if unsafe == 'parent' and path == Path('/usr/local/libexec'):
            mode = stat.S_IFDIR | 0o777
        return SimpleNamespace(st_mode=mode, st_uid=0, st_nlink=1)
    monkeypatch.setattr(Path, 'lstat', metadata)
    with pytest.raises(RuntimeError, match='untrusted'):
        runner.check_x11(current.binding)
    MODULE.subprocess.Popen.assert_not_called()


@pytest.mark.parametrize('unit', ['unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'])
def test_approved_policy_matches_exact_installed_fleet_unit(monkeypatch, unit):
    binding = KioskBinding('operator', 1001, unit)
    monkeypatch.setattr(MODULE, '_read_binding_file', lambda path: f'fresh {unit}\n')
    monkeypatch.setattr(MODULE, 'load_kiosk_binding', lambda: binding)
    assert MODULE.approved_binding() == binding
    monkeypatch.setattr(MODULE, 'load_kiosk_binding', lambda: KioskBinding('operator', 1001,
        'unit-b' if unit != 'unit-b' else 'unit-a'))
    with pytest.raises(ValueError):
        MODULE.approved_binding()


@pytest.mark.parametrize('policy', ['fresh unit-g\n', 'fresh unit-C\n', 'fresh unit-c', 'legacy unit-c\n'])
def test_other_or_nonexact_fleet_display_policy_refused(monkeypatch, policy):
    monkeypatch.setattr(MODULE, '_read_binding_file', lambda path: policy)
    with pytest.raises(ValueError):
        MODULE.approved_binding()
