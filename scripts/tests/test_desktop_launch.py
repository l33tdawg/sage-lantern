"""Synthetic root-launch identity/failure tests; never spawn an owner's Shell."""
import importlib.util
import json
from pathlib import Path
import socket
import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from sage_lantern.desktop_launch import (BRIDGE, NOTIFY, ROOT_CGROUP,
    BrokerLaunchOnce, LaunchOnce, active_session, bridge_peer,
    existing_compositor, ready_datagram, shell_environment, spawn_shell)
from sage_lantern.desktop_native import NativeError, PeerAnchor
from test_desktop_native import peer

ROOT = Path(__file__).resolve().parents[2]


def session():
    return dict(Id='3', User='1000', Type='wayland', Class='user', Active='yes',
                Remote='no', Seat='seat0', Leader='100')


def test_session_is_derived_from_logind_and_never_from_a_request():
    run = Mock(return_value=SimpleNamespace(stdout=b'3 1000 sage seat0 tty2\n'))
    assert active_session(1000, run=run, read=lambda _: session()) == session()
    assert '--no-legend' in run.call_args.args[0]


@pytest.mark.parametrize('field,value', [('User', '1001'), ('Class', 'greeter'),
    ('Active', 'no'), ('Remote', 'yes'), ('Seat', 'seat1'), ('Type', 'x11'), ('Id', '4')])
def test_absent_inactive_or_wrong_session_refuses(field, value):
    observed = {**session(), field: value}
    with pytest.raises(NativeError):
        active_session(1000, run=lambda *a, **k: SimpleNamespace(stdout=b'3 1000 sage\n'),
                       read=lambda _: observed)


def test_ambiguous_session_is_not_a_launch_choice():
    with pytest.raises(NativeError):
        active_session(1000, run=lambda *a, **k: SimpleNamespace(stdout=b'3 1000 sage\n4 1000 sage\n'),
                       read=lambda n: {**session(), 'Id': n})


def bridge():
    return dict(uid=1000, cgroup='0::/user.slice/user-1000.slice/user@1000.service/'
                'session.slice/org.gnome.Shell@ubuntu.service',
                argv=[b'/usr/bin/python3', b'-I', BRIDGE.encode()])


@pytest.mark.parametrize('value,uid', [(bridge(), 1000), (bridge(), 0),
    ({**bridge(), 'uid': 1001}, 1000), ({**bridge(), 'cgroup': ROOT_CGROUP}, 1000),
    ({**bridge(), 'argv': [b'/usr/bin/python3', b'/tmp/fake']}, 1000)])
def test_bridge_claims_cannot_override_kernel_uid_or_exact_unit(value, uid):
    assert bridge_peer(123, uid, 1000, snapshot=lambda _: value) is (value == bridge() and uid == 1000)


def policy():
    return dict(uid=1000, gid=1000, account='sage', home='/home/sage', overlay='/root-owned/overlay')


def test_shell_environment_is_constructed_without_inherited_input(monkeypatch):
    monkeypatch.setenv('LD_PRELOAD', '/tmp/forged.so')
    monkeypatch.setenv('PRIVATE_SECRET', 'synthetic-only')
    value = shell_environment(policy(), session())
    assert value['NOTIFY_SOCKET'] == NOTIFY
    assert value['XDG_SESSION_ID'] == '3'
    assert value['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/1000/bus'
    assert 'LD_PRELOAD' not in value and 'PRIVATE_SECRET' not in value


def test_child_drops_to_fresh_nss_groups_and_does_not_inherit_fds(monkeypatch):
    monkeypatch.setattr('sage_lantern.desktop_launch.pwd.getpwnam',
        lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000, pw_dir='/home/sage', pw_name='sage'))
    monkeypatch.setattr('sage_lantern.desktop_launch.os.getgrouplist', lambda *a: [1000, 29])
    popen = Mock()
    spawn_shell(policy(), session(), popen=popen)
    args, kwargs = popen.call_args
    assert args == (['/usr/bin/gnome-shell', '--mode=ubuntu'],)
    assert kwargs['user'] == kwargs['group'] == 1000
    assert kwargs['extra_groups'] == [1000, 29] and kwargs['close_fds'] is True
    assert 'preexec_fn' not in kwargs  # No unsafe Python fork callback with threads.


def test_root_created_child_enrollment_fences_parent_cgroup_and_notify(peer):
    peer.process.update(parent=99, cgroup=ROOT_CGROUP,
        environment={'G_RESOURCE_OVERLAYS': peer.anchor.overlay, 'NOTIFY_SOCKET': NOTIFY})
    anchor = PeerAnchor.enroll(123, 1000, '3', peer.anchor.overlay, 'a' * 64,
                              parent=99, notify=NOTIFY, **peer.deps)
    assert anchor.matches(123, 1000, **peer.deps)
    for field, wrong in [('parent', 100), ('cgroup', bridge()['cgroup']), ('environment', {})]:
        old = peer.process[field]
        peer.process[field] = wrong
        assert not anchor.matches(123, 1000, **peer.deps)
        peer.process[field] = old


@pytest.mark.parametrize('body,pid,uid,flags', [(b'READY=1', 123, 1000, 0),
    (b'READY=1', 124, 1000, 0), (b'READY=1', 123, 0, 0), (b'READY=0', 123, 1000, 0),
    (b'READY=1\nSTATUS=claimed', 123, 1000, 0), (b'READY=1', 123, 1000, socket.MSG_TRUNC)])
def test_ready_requires_exact_child_kernel_credentials(body, pid, uid, flags, monkeypatch):
    monkeypatch.setattr(socket, 'SCM_CREDENTIALS', 2, raising=False)
    ancillary = [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack('3i', pid, uid, 1000))]
    assert ready_datagram(body, ancillary, flags, 123, 1000) is (
        body == b'READY=1' and pid == 123 and uid == 1000 and flags == 0)
    assert not ready_datagram(body, [], flags, 123, 1000)


def test_launch_failure_never_restarts_replaces_or_kills_a_child():
    spawn = Mock(side_effect=OSError('synthetic startup failure'))
    once = LaunchOnce(lambda: (policy(), session()), spawn)
    with pytest.raises(OSError):
        once.start()
    with pytest.raises(NativeError, match='held'):
        once.start()
    spawn.assert_called_once()


def test_live_child_is_never_replaced_or_terminated():
    child = Mock(pid=123)
    once = LaunchOnce(
        lambda: (policy(), session()), Mock(return_value=child),
        pidfd_open=lambda pid: 7)
    assert once.start()[2] is child
    assert once.fd == 7
    with pytest.raises(NativeError):
        once.start()
    child.kill.assert_not_called()
    child.terminate.assert_not_called()


def test_existing_compositor_or_wayland_socket_holds_start(tmp_path):
    proc = tmp_path / 'proc'
    runtime = tmp_path / 'runtime'
    proc.mkdir(); runtime.mkdir(); (proc / '123').mkdir()
    assert existing_compositor(1000, proc=proc, runtime=runtime,
        snapshot=lambda _: dict(uid=1000, executable='/usr/bin/gnome-shell'))
    (runtime / 'wayland-0').touch()
    assert existing_compositor(1000, proc=proc, runtime=runtime,
        snapshot=Mock(side_effect=AssertionError('must not inspect once socket exists')))


def authority_module():
    spec = importlib.util.spec_from_file_location('guarded_authority_test',
        ROOT / 'scripts/lantern/guarded-session-authority.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_activation_is_root_owned_exact_policy_and_requires_both_gates(monkeypatch):
    module = authority_module()
    marker = dict(schema='sage.lantern.desktop-pin-activation.v1', qualified_power=True,
                  fresh_session_authorized=True, launch_policy_sha256='a' * 64)
    monkeypatch.setattr(module, 'trusted_file', lambda _: json.dumps(marker).encode())
    assert module.activation_check({'activation_file': '/fixture'}, 'a' * 64)
    for field in ('qualified_power', 'fresh_session_authorized'):
        marker[field] = False
        assert not module.activation_check({'activation_file': '/fixture'}, 'a' * 64)
        marker[field] = True
    assert not module.activation_check({'activation_file': '/fixture'}, 'b' * 64)


def test_authority_requires_fresh_non_authorizing_capture_selection(monkeypatch):
    module = authority_module()
    monkeypatch.setattr(module, 'activation_check', lambda *_: True)
    monkeypatch.setattr(module, 'existing_compositor', lambda *_: False)
    monkeypatch.setattr(module, 'active_session', lambda *_: session())
    monkeypatch.setattr(module, 'active_session', lambda *_: session())
    selection = SimpleNamespace(
        machine_id='a' * 32,
        boot_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        capture_id='cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa',
        generation=7,
        policy_sha256='d' * 64,
        runtime_authorized=False)
    authority = module.Authority(
        policy(), lambda: True, 'a' * 64, Mock(), Mock())
    with pytest.raises(NativeError, match='capture_selection_required'):
        authority.prepare()
    authority.selection_provider = lambda: SimpleNamespace(
        **{**vars(selection), 'runtime_authorized': True})
    with pytest.raises(NativeError, match='capture_selection_required'):
        authority.prepare()
    authority.selection_provider = lambda: selection
    assert authority.prepare() == (policy(), session())
    assert authority.selection is selection


def test_broker_authority_requires_recorder_and_uses_staged_pidfd():
    module = authority_module()
    with pytest.raises(NativeError, match='settlement_owner_required'):
        module.Authority(
            policy(), lambda: True, 'a' * 64, Mock(), Mock(),
            broker_factory=Mock())
    selection = SimpleNamespace(
        capture_id='cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa',
        generation=7, policy_sha256='d' * 64)
    child = SimpleNamespace(pid=123, pidfd=9)
    broker = Mock()
    calls = []
    def stage(record, **kwargs):
        calls.append(kwargs)
        assert record(child, SimpleNamespace(
            launch_id='launch', capture_id=selection.capture_id,
            generation=7, policy_sha256=selection.policy_sha256)) is True
        return child
    broker.stage_until_recorded.side_effect = stage
    once = BrokerLaunchOnce(
        lambda: (policy(), session()), lambda: broker,
        lambda staged, request, selected: selected is selection, lambda: selection)
    assert once.start()[2] is child
    assert once.fd == 9
    assert calls[0]['capture_id'] == selection.capture_id
    assert calls[0]['generation'] == 7
    broker.close.assert_called_once()


def test_broker_authority_requires_clean_startup_recovery(monkeypatch):
    module = authority_module()
    monkeypatch.setattr(module, 'activation_check', lambda *_: True)
    monkeypatch.setattr(module, 'existing_compositor', lambda *_: False)
    monkeypatch.setattr(module, 'active_session', lambda *_: session())
    selection = SimpleNamespace(
        machine_id='a' * 32,
        boot_id='aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee',
        capture_id='cccccccc-dddd-eeee-ffff-aaaaaaaaaaaa',
        generation=7, policy_sha256='d' * 64, runtime_authorized=False)
    authority = module.Authority(
        policy(), lambda: True, 'a' * 64, Mock(), Mock(),
        selection_provider=lambda: selection,
        broker_factory=Mock(),
        settlement_recorder=lambda *_: True,
        settlement_recovery=lambda: (SimpleNamespace(status='uncertain'),))
    assert authority.recover() is False
    with pytest.raises(NativeError):
        authority.prepare()
    authority.settlement_recovery = lambda: ()
    assert authority.recover() is True
    assert authority.prepare() == (policy(), session())


def test_settlement_runtime_composes_provider_recorder_and_recovery(tmp_path):
    module = authority_module()
    tmp_path.chmod(0o700)
    selection, recorder, recovery = module.load_settlement_runtime(
        ROOT / 'software/security/desktop_loader_candidate',
        tmp_path, '/synthetic/capture-policy.json')
    assert callable(selection) and callable(recorder) and callable(recovery)
    assert recovery() == ()


def test_authority_hold_closes_grants_without_process_termination():
    module = authority_module()
    authority = module.Authority(policy(), lambda: True, 'a' * 64, Mock(), Mock())
    child = Mock(); authority.launch.child = child
    authority.native = Mock()
    authority.hold()
    assert authority.held.is_set()
    authority.native.server_close.assert_called_once()
    child.kill.assert_not_called(); child.terminate.assert_not_called()


def test_close_before_native_worker_start_cannot_wait_on_a_missing_server_loop():
    authority = authority_module().Authority(policy(), lambda: True, 'a' * 64, Mock(), Mock())
    authority.native = Mock()
    authority.close()
    authority.native.server_close.assert_called_once()
    authority.native.shutdown.assert_not_called()


@pytest.mark.parametrize('readiness', ['exact', 'wrong_child', 'eof'])
def test_authority_enrolls_only_its_spawned_child_before_readiness(monkeypatch, readiness):
    module = authority_module()
    monkeypatch.setattr(socket, 'SCM_CREDENTIALS', 2, raising=False)
    monkeypatch.setattr(module.os, 'pidfd_open', lambda pid: 7, raising=False)
    monkeypatch.setattr(module.os, 'getpid', lambda: 99)
    monkeypatch.setattr(module.threading, 'Thread', Mock())
    clock = iter([0, 0, 100])
    monkeypatch.setattr(module.time, 'monotonic', lambda: next(clock))
    ready, connection = Mock(), Mock()
    ready.recvmsg.return_value = (b'READY=1', [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
        struct.pack('3i', 123 if readiness == 'exact' else 124, 1000, 1000))], 0, None)
    def selected(read, *args):
        return (read if read == [ready] or readiness == 'eof' else [], [], [])
    monkeypatch.setattr(module.select, 'select', selected)
    anchor = SimpleNamespace(binding='synthetic-root-created-child', leader='100')
    enroll = Mock(return_value=anchor)
    monkeypatch.setattr(module.PeerAnchor, 'enroll', enroll)
    seat = Mock()
    monkeypatch.setattr(module.SeatAnchor, 'enroll', seat)
    p = {**policy(), 'executable_sha256': 'b' * 64,
         'native_socket': '/synthetic/native.sock', 'broker_socket': '/synthetic/broker.sock', 'broker_uid': 997}
    child = Mock(pid=123)
    adapter = Mock()
    authority = module.Authority(p, lambda: True, 'a' * 64, adapter, ready)
    authority.launch.start = Mock(return_value=(p, session(), child))
    authority.valid = lambda: True
    if readiness == 'exact':
        authority.start(connection)
    else:
        with pytest.raises(NativeError):
            authority.start(connection)
    enroll.assert_called_once_with(123, 1000, '3', p['overlay'], 'b' * 64, parent=99, notify=NOTIFY)
    if readiness == 'exact':
        seat.assert_called_once_with(123, 1000, '3', '100')
        adapter.NativeServer.assert_called_once()
    else:
        seat.assert_not_called()
        adapter.NativeServer.assert_not_called()
    child.kill.assert_not_called(); child.terminate.assert_not_called()


def test_child_ready_without_seat_control_cannot_publish_pin_authority(monkeypatch):
    module = authority_module()
    monkeypatch.setattr(socket, 'SCM_CREDENTIALS', 2, raising=False)
    monkeypatch.setattr(module.os, 'pidfd_open', lambda _: 7, raising=False)
    monkeypatch.setattr(module.PeerAnchor, 'enroll', Mock(return_value=SimpleNamespace(leader='100')))
    monkeypatch.setattr(module.SeatAnchor, 'enroll', Mock(side_effect=NativeError('seat_refused')))
    monkeypatch.setattr(module.select, 'select', lambda read, *a: (read if read == [ready] else [], [], []))
    ready = Mock()
    ready.recvmsg.return_value = (b'READY=1', [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS,
        struct.pack('3i', 123, 1000, 1000))], 0, None)
    adapter = Mock()
    authority = module.Authority(policy(), lambda: True, 'a' * 64, adapter, ready)
    p = {**policy(), 'executable_sha256': 'b' * 64}
    child = Mock(pid=123)
    authority.launch.start = Mock(return_value=(p, session(), child))
    authority.valid = lambda: True
    with pytest.raises(NativeError, match='seat_refused'):
        authority.start(Mock())
    adapter.NativeServer.assert_not_called()
    child.kill.assert_not_called(); child.terminate.assert_not_called()


def test_seat_controller_loss_invalidates_authority_even_with_live_pidfd(monkeypatch):
    module = authority_module()
    monkeypatch.setattr(module, 'pidfd_alive', lambda _: True)
    authority = module.Authority(policy(), lambda: True, 'a' * 64, Mock(), Mock())
    authority.fd = 7
    authority.anchor = Mock(pid=123, uid=1000)
    authority.seat = Mock()
    authority.seat.matches.return_value = True
    assert authority.valid()
    authority.seat.matches.return_value = False
    assert not authority.valid()


@pytest.mark.parametrize('when', ['before', 'during_bind', 'after_worker_start'])
def test_hold_during_native_publication_never_resurrects_authority(monkeypatch, when):
    module = authority_module()
    adapter = Mock()
    p = {**policy(), 'native_socket': '/synthetic/native.sock',
         'broker_socket': '/synthetic/broker.sock', 'broker_uid': 997}
    authority = module.Authority(p, lambda: True, 'a' * 64, adapter, Mock())
    authority.seat = Mock()
    authority.valid = lambda: not authority.held.is_set()
    worker = Mock()
    monkeypatch.setattr(module.threading, 'Thread', Mock(return_value=worker))
    server = Mock()
    def construct(*a, **k):
        if when == 'during_bind':
            authority.held.set()  # Another thread may set this while publication is locked.
        return server
    adapter.NativeServer.side_effect = construct
    if when == 'before':
        authority.hold()
    if when == 'after_worker_start':
        worker.start.side_effect = authority.held.set
    with pytest.raises(NativeError):
        authority.publish_native(p)
    if when == 'before':
        adapter.NativeServer.assert_not_called()
    else:
        server.server_close.assert_called_once()
    if when != 'after_worker_start':
        worker.start.assert_not_called()
    assert not authority.valid()


def test_candidate_units_have_no_automatic_shell_restart_or_shutdown():
    root = (ROOT / 'software/systemd/sage-lantern-guarded-session.service').read_text()
    bridge = (ROOT / 'software/systemd/gnome-shell-pin-candidate.conf').read_text()
    assert 'Restart=no' in root and 'KillMode=process' in root
    assert 'NoNewPrivileges=true' in root and 'LimitCORE=0' in root
    assert 'ConditionPathExists=/etc/sage-lantern/desktop-pin-activation.json' in root
    assert 'OnFailure=\n' in bridge and 'TimeoutStartSec=infinity' in bridge
