"""Seat credentials and read races; no TakeControl/TakeDevice or physical probes."""
import json
import os
from pathlib import Path
import stat
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sage_lantern.desktop_native import NativeError
from sage_lantern.desktop_seat import SeatAnchor, controller_pid, device_fds, saved_session


def state():
    return dict(UID='1000', ACTIVE='1', TYPE='wayland', CLASS='user', SEAT='seat0',
                LEADER='100', CONTROLLER=':1.37', DEVICES='226:0 13:64 ')


def deps(value=None):
    value = state() if value is None else value
    return dict(read=lambda _: value, resolve=lambda _: 123,
                devices=lambda _: {(226, 0), (13, 64)})


def test_exact_controller_pid_and_registered_open_devices_bind_seat():
    anchor = SeatAnchor.enroll(123, 1000, '3', '100', **deps())
    assert anchor.matches(**deps())
    assert anchor.controller == ':1.37'
    assert not anchor.matches(**{**deps(), 'resolve': lambda _: 124})
    assert not anchor.matches(**deps({**state(), 'CONTROLLER': ':1.38'}))


@pytest.mark.parametrize('field,value', [('UID', '1001'), ('ACTIVE', '0'),
    ('TYPE', 'x11'), ('CLASS', 'greeter'), ('SEAT', 'seat1'), ('LEADER', '101'),
    ('DEVICES', '226:0'), ('DEVICES', '13:64'), ('DEVICES', '226:0 /tmp/fake')])
def test_other_session_or_unregistered_device_refuses(field, value):
    with pytest.raises(NativeError):
        SeatAnchor.enroll(123, 1000, '3', '100', **deps({**state(), field: value}))


@pytest.mark.parametrize('opened', [set(), {(226, 0)}, {(13, 64)},
    {(226, 0), (13, 65)}, {(226, 1), (13, 64)}])
def test_absent_or_other_child_device_descriptors_refuse(opened):
    with pytest.raises(NativeError):
        SeatAnchor.enroll(123, 1000, '3', '100',
            **{**deps(), 'devices': lambda _: opened})


def test_logind_state_change_or_bus_owner_disappearance_during_read_refuses():
    reads = iter([state(), {**state(), 'CONTROLLER': ':1.38'}])
    with pytest.raises(NativeError):
        SeatAnchor.enroll(123, 1000, '3', '100', **{**deps(), 'read': lambda _: next(reads)})
    resolves = iter([123, 124])
    with pytest.raises(NativeError):
        SeatAnchor.enroll(123, 1000, '3', '100', **{**deps(), 'resolve': lambda _: next(resolves)})
    anchor = SeatAnchor.enroll(123, 1000, '3', '100', **deps())
    assert not anchor.matches(**{**deps(), 'read': Mock(side_effect=FileNotFoundError())})


def test_saved_session_ignores_unrelated_fields_and_rejects_duplicates():
    read = Mock(return_value=b'UID=1000\nCONTROLLER=:1.37\nUNRELATED=ignored\n')
    assert saved_session('3', read=read) == dict(UID='1000', CONTROLLER=':1.37')
    read.assert_called_once_with('/run/systemd/sessions/3')
    with pytest.raises(NativeError):
        saved_session('3', read=lambda _: b'UID=1000\nUID=1000\n')
    for name in ('../3', '/tmp/state', '', 'a' * 33):
        with pytest.raises(NativeError):
            saved_session(name, read=Mock(side_effect=AssertionError('unsafe path')))


def test_controller_resolution_only_calls_dbus_pid_query():
    run = Mock(return_value=SimpleNamespace(stdout=b'{"type":"u","data":[123]}'))
    assert controller_pid(':1.37', run=run) == 123
    argv = run.call_args.args[0]
    assert argv[-3:] == ['GetConnectionUnixProcessID', 's', ':1.37']
    assert run.call_args.kwargs['timeout'] == 2
    for name in ('1.37', ':1.37;cmd', 'org.gnome.Shell', None):
        with pytest.raises(NativeError):
            controller_pid(name, run=Mock(side_effect=AssertionError('unsafe bus name')))


@pytest.mark.parametrize('value', [{'type': 'u', 'data': [True]},
    {'type': 'u', 'data': [0]}, {'type': 'u', 'data': [123, 124]},
    {'type': 's', 'data': ['123']}, {'type': 'u', 'data': [123], 'extra': 1}])
def test_malformed_bus_pid_reply_refuses(value):
    with pytest.raises(NativeError):
        controller_pid(':1.37', run=lambda *a, **k: SimpleNamespace(stdout=json.dumps(value)))


def test_fd_observation_uses_only_kernel_character_device_ids(tmp_path, monkeypatch):
    directory = tmp_path / '123/fd'; directory.mkdir(parents=True)
    for fd in ('1', '2', '3'):
        (directory / fd).symlink_to('/dev/null')
    paths = {'1': '/dev/dri/card0', '2': '/dev/input/event0', '3': '/owner/private/file'}
    monkeypatch.setattr(os, 'readlink', lambda p: paths[p.name])
    original = Path.stat
    def info(path, *a, **kw):
        if path.parent == directory:
            if path.name == '3':
                raise AssertionError('must not inspect private descriptors')
            return SimpleNamespace(st_mode=stat.S_IFCHR, st_rdev=os.makedev(
                226 if path.name == '1' else 13, 0 if path.name == '1' else 64))
        return original(path, *a, **kw)
    monkeypatch.setattr(Path, 'stat', info)
    assert device_fds(123, proc=tmp_path) == {(226, 0), (13, 64)}
