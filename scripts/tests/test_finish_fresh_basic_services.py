import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('basic_finish',
    Path(__file__).resolve().parents[1] / 'lantern/finish-fresh-basic-services.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


@pytest.mark.parametrize('fault', [None, 'current', 'start_camera', 'release_display', 'start_display'])
def test_transition_only_remaining_services_no_retry(monkeypatch, fault):
    monkeypatch.setattr(MODULE, 'REMAINING', ('camera', 'display'))
    events = []
    def action(name):
        events.append(name)
        if fault == name:
            raise ValueError
    backend = SimpleNamespace(current=lambda: action('current'),
        release=lambda service, record: action('release_' + service), start=lambda service: action('start_' + service))
    if fault:
        with pytest.raises(ValueError):
            MODULE.transition(backend, events.append)
        assert events[-1] == fault and fault + '_done' not in events
    else:
        result = MODULE.transition(backend, events.append)
        assert result['auth_restarted'] is False and result['status_restarted'] is False
        assert result['voice_activated'] is False and result['cryptographic_lock_verified'] is False
    assert not any('auth' in event or 'status' in event or 'install' in event for event in events)


@pytest.mark.parametrize('fault', [None, 'already_exec', 'pid_changed', 'invocation_changed', 'ticks_changed',
    'foreign_exe', 'foreign_inode', 'cgroup', 'stopped', 'never_exec', 'final_uid', 'final_argv', 'auth_unlocked'])
def test_bounded_start_only_same_systemd_preexec_then_strict_process(monkeypatch, fault):
    service = MODULE.REMAINING[0]
    events = []
    first = dict(ActiveState='active', Job='', MainPID='123', InvocationID='f' * 32, ExecStart='same')
    reads = []
    def process_read(pid):
        reads.append(pid)
        executable = '/python' if fault == 'already_exec' or len(reads) >= 3 and fault != 'never_exec' else '/systemd'
        if fault == 'foreign_exe':
            executable = '/foreign'
        return SimpleNamespace(pid=123, start_ticks=456 if fault != 'ticks_changed' or len(reads) == 1 else 457,
                               state='T' if fault == 'stopped' else 'S', executable=executable)
    shows = []
    def show(name):
        shows.append(name)
        value = dict(first)
        if len(shows) > 1:
            if fault == 'pid_changed':
                value['MainPID'] = '124'
            if fault == 'invocation_changed':
                value['InvocationID'] = 'e' * 32
        return value
    def verify(name, value):
        events.append('strict_process')
        if fault in ('final_uid', 'final_argv'):
            raise ValueError
        return SimpleNamespace(start_ticks=456)
    def locked():
        events.append('locked')
        if fault == 'auth_unlocked':
            raise ValueError
    backend = SimpleNamespace(released={service}, running={}, ticks={}, current=lambda: events.append('current'),
        observer=SimpleNamespace(deadline=100, system=SimpleNamespace(_command=lambda args, deadline: events.append(args))),
        show=show, finish_snapshot=lambda value, name: value,
        preflight=SimpleNamespace(LinuxProcesses=lambda: SimpleNamespace(read=process_read)),
        expected={'start': {service: {'executable': '/python'}}}, process=verify,
        locked=locked, native=lambda: events.append('native'))
    monkeypatch.setattr(Path, 'read_text', lambda path: 'wrong' if fault == 'cgroup' else '0::/system.slice/' + service + '\n')
    monkeypatch.setattr(Path, 'stat', lambda path: SimpleNamespace(st_dev=1,
        st_ino=2 if fault == 'foreign_inode' and str(path) == '/proc/123/exe' else 1))
    monkeypatch.setattr(MODULE.os, 'readlink', lambda path: '/systemd')
    clock = iter(range(0, 100, 2))
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(MODULE.time, 'sleep', lambda delay: None)
    if fault not in (None, 'already_exec'):
        with pytest.raises(ValueError):
            MODULE.bounded_start(backend, service)
        assert service not in backend.running
    else:
        MODULE.bounded_start(backend, service)
        assert backend.running[service] == first and backend.ticks[service] == 456
        assert events[-2:] == ['current', 'current']
    assert events.count(['start', service]) == 1


@pytest.mark.parametrize('service', ['sage-lantern-device-auth.service', MODULE.STATUS,
    'sage-lantern-voice-loop.service', 'sage-lantern-llm.service', 'sage-lantern-personal-sage.service'])
def test_start_scope_cannot_expand(service):
    with pytest.raises(ValueError):
        MODULE.bounded_start(SimpleNamespace(), service)


def test_duplicate_checkpoint_fields_rejected():
    with pytest.raises(ValueError):
        MODULE.decode(b'{"schema":1,"schema":2}')
