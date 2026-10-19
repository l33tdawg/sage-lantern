"""Synthetic broker/native-grant tests; never use an owner's PIN or session."""
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from sage_lantern.device_lock import DeviceLock, DeviceLockError
from sage_lantern.desktop_grant import DesktopGrant, DesktopGrantError
from sage_lantern.desktop_control import DesktopControlServer, dispatch, root_peer


SPEC = importlib.util.spec_from_file_location('desktop_test_broker',
    Path(__file__).resolve().parents[1] / 'lantern/unit-a-device-auth.py')
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)
PIN = '12345678'
BINDING = 'boot-fixture:uid1000:pid123:start456'


@pytest.fixture
def rig(tmp_path):
    now = [1000.0]
    verifier = Mock(return_value=True)
    verifier.cleanup_confirmed = True
    device = DeviceLock(tmp_path / 'private', verifier=verifier,
                        clock=lambda: now[0], wall_clock=lambda: now[0], session_seconds=120)
    broker = AUTH.AuthBroker(device, desktop_clock=lambda: now[0])
    yield SimpleNamespace(now=now, verifier=verifier, device=device, broker=broker)
    broker.shutdown()
    device.close()


def native(rig, *, activate=True):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    token = rig.broker.desktop_unlock(guard, PIN)['token']
    if activate:
        assert rig.broker.desktop_access(guard, token, 'activate') == {'authorized': True}
    return guard, token


def browser(rig):
    token = rig.broker.unlock(PIN)['token']
    assert rig.broker.activate(token) == {'authorized': True}
    return token


def test_native_ticket_has_no_browser_or_private_api_authority(rig):
    guard, token = native(rig, activate=False)
    assert rig.broker.desktop_access(guard, token, 'check') == {'authorized': False}
    assert not rig.device.pending(token)
    assert not rig.device.activate(token)
    assert not rig.device.authorized(token)
    assert rig.broker.state() == {'locked': True}
    assert rig.broker.desktop_access(guard, token, 'activate') == {'authorized': True}
    assert rig.broker.state() == {'locked': True}
    with pytest.raises(AUTH.RequestError):
        rig.broker.require_token(token)


def test_pending_and_active_native_grants_inhibit_sleep_readiness(rig):
    assert rig.broker.sleep_ready() == {'ready': True}
    guard, token = native(rig, activate=False)
    assert rig.broker.sleep_ready() == {'ready': False}
    assert rig.broker.desktop_access(guard, token, 'activate')['authorized']
    assert rig.broker.sleep_ready() == {'ready': False}
    rig.now[0] += 6
    assert rig.broker.sleep_ready() == {'ready': True}
    assert rig.broker.desktop.active is None
    assert rig.device.status() == {'locked': True}


def test_browser_authentication_cannot_authorize_native_desktop(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    token = browser(rig)
    assert rig.broker.state() == {'locked': False}
    assert rig.broker.desktop_access(guard, token, 'activate') == {'authorized': False}
    assert rig.broker.desktop_access(guard, token, 'presence') == {'authorized': False}


def test_native_and_browser_checks_use_one_persisted_attempt_budget(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    for i in range(5):
        if i % 2:
            rig.broker.unlock(PIN)
        else:
            rig.broker.desktop_unlock(guard, PIN)
    with pytest.raises(DeviceLockError, match='rate_limited'):
        rig.broker.desktop_unlock(guard, PIN)
    assert rig.verifier.call_count == 5
    assert rig.device.status() == {'locked': True}


def test_shared_budget_survives_broker_restart_without_native_grants(rig, tmp_path):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    for _ in range(5):
        rig.broker.desktop_unlock(guard, PIN)
    rig.broker.shutdown()
    rig.device.close()
    restarted = DeviceLock(tmp_path / 'private', verifier=rig.verifier,
                           clock=lambda: rig.now[0], wall_clock=lambda: rig.now[0])
    try:
        other = AUTH.AuthBroker(restarted, desktop_clock=lambda: rig.now[0])
        new_guard = other.desktop_open(BINDING, 'desktop')['guard']
        with pytest.raises(DeviceLockError, match='rate_limited'):
            other.desktop_unlock(new_guard, PIN)
        with pytest.raises(DesktopGrantError, match='stale_guard'):
            other.desktop_access(guard, 'x' * 43, 'check')
    finally:
        restarted.close()


def test_lost_pending_response_expires_and_never_unlocks(rig):
    guard, token = native(rig, activate=False)
    rig.now[0] += 30
    assert rig.broker.desktop_access(guard, token, 'activate') == {'authorized': False}


def test_presence_expiry_cannot_be_revived_by_activity_or_activation(rig):
    guard, token = native(rig)
    rig.now[0] += 6
    for action in ('check', 'presence', 'activity', 'activate'):
        assert rig.broker.desktop_access(guard, token, action) == {'authorized': False}


def test_presence_and_polling_do_not_extend_idle_and_policy_change_is_live(rig):
    guard, token = native(rig)
    for _ in range(100):
        rig.now[0] += 1
        assert rig.broker.desktop_access(guard, token, 'presence') == {'authorized': True}
        assert rig.broker.desktop_access(guard, token, 'activate') == {'authorized': True}
        assert rig.broker.desktop_access(guard, token, 'check') == {'authorized': True}
    rig.device.set_session_seconds(60)
    assert rig.broker.desktop_access(guard, token, 'presence') == {'authorized': False}


def test_only_explicit_activity_extends_idle(rig):
    rig.device.set_session_seconds(10)
    guard, token = native(rig)
    for i in range(15):
        rig.now[0] += 1
        assert rig.broker.desktop_access(guard, token, 'presence')['authorized']
        if i == 7:
            assert rig.broker.desktop_access(guard, token, 'activity')['authorized']
    for _ in range(2):
        rig.now[0] += 1
        assert rig.broker.desktop_access(guard, token, 'presence')['authorized']
    rig.now[0] += 1
    assert not rig.broker.desktop_access(guard, token, 'presence')['authorized']


@pytest.mark.parametrize('mode', ['kiosk', 'desktop'])
def test_mode_change_requires_fresh_pin_and_rejects_old_guard(rig, mode):
    guard, token = native(rig)
    console = browser(rig)
    new_guard = rig.broker.desktop_open(BINDING, mode)['guard']
    assert new_guard != guard
    assert not rig.device.authorized(console)
    with pytest.raises(DesktopGrantError, match='stale_guard'):
        rig.broker.desktop_access(guard, token, 'check')
    assert not rig.broker.desktop_access(new_guard, token, 'activate')['authorized']
    fresh = rig.broker.desktop_unlock(new_guard, PIN)['token']
    assert rig.broker.desktop_access(new_guard, fresh, 'activate')['authorized']


@pytest.mark.parametrize('action', ['lock', 'cancel', 'expire'])
def test_browser_relock_revokes_native_grant(rig, action):
    guard, token = native(rig)
    console = browser(rig)
    if action == 'lock':
        rig.broker.lock(console)
    elif action == 'cancel':
        rig.broker.cancel(console)
    else:
        rig.now[0] += 5
        rig.broker.desktop_access(guard, token, 'presence')
        rig.now[0] += 1
    assert not rig.broker.desktop_access(guard, token, 'check')['authorized']


def test_native_expiry_revokes_browser_even_when_console_keeps_presence(rig):
    guard, token = native(rig)
    console = browser(rig)
    rig.now[0] += 5
    assert rig.broker.presence(console)['authorized']
    rig.now[0] += 1
    assert not rig.broker.desktop_access(guard, token, 'check')['authorized']
    assert not rig.device.authorized(console)


def test_invalid_tokens_and_stale_guard_cannot_relock_another_grant(rig):
    guard, token = native(rig)
    assert not rig.broker.desktop_access(guard, 'x' * 43, 'activity')['authorized']
    with pytest.raises(DesktopGrantError, match='stale_guard'):
        rig.broker.desktop_lock('x' * 43)
    assert rig.broker.desktop_access(guard, token, 'check')['authorized']


def test_lock_during_pin_verification_fences_result_without_waiting(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    entered, release = threading.Event(), threading.Event()
    def verify(pin):
        entered.set()
        assert release.wait(2)
        return True
    rig.verifier.side_effect = verify
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.broker.desktop_unlock, guard, PIN)
        try:
            assert entered.wait(2)
            assert rig.broker.desktop_lock(guard) == {'locked': True}
        finally:
            release.set()
        with pytest.raises((DeviceLockError, DesktopGrantError), match='unlock_cancelled'):
            future.result(timeout=2)
    assert rig.broker.desktop.pending is None


def test_mode_switch_during_setup_precheck_cancels_before_verification(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    status = dict(enabled=True, recovery_backup_confirmed=True,
                  has_existing_vault=True, can_initialize=False)
    def precheck():
        rig.broker.desktop_open(BINDING, 'kiosk')
        return status
    rig.broker.vault = SimpleNamespace(status=precheck, cleanup_confirmed=True)
    with pytest.raises((DeviceLockError, DesktopGrantError)):
        rig.broker.desktop_unlock(guard, PIN)
    rig.verifier.assert_not_called()


def test_setup_and_positive_cleanup_are_required_before_native_ticket(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    status = dict(enabled=True, recovery_backup_confirmed=False,
                  has_existing_vault=True, can_initialize=False)
    rig.broker.vault = SimpleNamespace(status=lambda: status, cleanup_confirmed=True)
    with pytest.raises(AUTH.RequestError) as error:
        rig.broker.desktop_unlock(guard, PIN)
    assert error.value.code == 'setup_required'
    rig.verifier.assert_not_called()
    status['recovery_backup_confirmed'] = True
    rig.verifier.cleanup_confirmed = False
    with pytest.raises(DeviceLockError, match='unavailable'):
        rig.broker.desktop_unlock(guard, PIN)
    assert rig.broker.desktop.guard is None
    assert rig.broker.cleanup_unknown


def test_upstream_busy_is_charged_to_same_budget(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    assert rig.broker._begin_upstream()
    try:
        for _ in range(5):
            with pytest.raises(DeviceLockError, match='verification_busy'):
                rig.broker.desktop_unlock(guard, PIN)
        with pytest.raises(DeviceLockError, match='rate_limited'):
            rig.broker.desktop_unlock(guard, PIN)
    finally:
        rig.broker._end_upstream()
    rig.verifier.assert_not_called()


def test_shutdown_clock_failure_and_unknown_cleanup_fail_closed(rig):
    guard, token = native(rig)
    rig.now[0] -= 1
    with pytest.raises((DesktopGrantError, DeviceLockError), match='unavailable'):
        rig.broker.desktop_access(guard, token, 'check')
    assert rig.broker.desktop.active is None
    rig.broker.shutdown()
    with pytest.raises(DeviceLockError, match='unavailable'):
        rig.broker.desktop_open(BINDING, 'desktop')


def test_private_auth_storage_failure_revokes_native_grant(rig):
    guard, token = native(rig)
    with patch.object(rig.device, '_check_files', side_effect=OSError('synthetic')):
        with pytest.raises(DeviceLockError, match='unavailable'):
            rig.broker.desktop_access(guard, token, 'check')
    assert rig.broker.desktop.guard is None
    assert rig.broker.desktop.active is None


def test_shutdown_during_native_verification_never_issues_a_ticket(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    entered, release = threading.Event(), threading.Event()
    def verify(pin):
        entered.set()
        assert release.wait(2)
        return True
    rig.verifier.side_effect = verify
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(rig.broker.desktop_unlock, guard, PIN)
        try:
            assert entered.wait(2)
            rig.broker.shutdown()
            assert not rig.broker.drain_state(False)['drained']
        finally:
            release.set()
        with pytest.raises((DeviceLockError, DesktopGrantError)):
            future.result(timeout=2)
    assert rig.broker.desktop.guard is None
    assert rig.broker.desktop.pending is None


def test_repeated_failed_pin_does_not_issue_native_grants(rig):
    guard = rig.broker.desktop_open(BINDING, 'desktop')['guard']
    rig.verifier.return_value = False
    for _ in range(5):
        with pytest.raises(DeviceLockError, match='verification_failed'):
            rig.broker.desktop_unlock(guard, PIN)
    with pytest.raises(DeviceLockError, match='rate_limited'):
        rig.broker.desktop_unlock(guard, PIN)
    assert rig.broker.desktop.pending is None
    assert rig.broker.desktop.active is None


@pytest.mark.parametrize('raw', [
    b'{"action":"open","action":"lock"}\n',
    b'{"action":"open","binding":"x","mode":true}\n',
    b'{"action":"open","binding":"x","mode":"desktop","uid":0}\n',
    b'{"action":"open","binding":"x","mode":"desktop"}',
    b'{"action":"open","binding":NaN,"mode":"desktop"}\n',
    b'{"action":"check","guard":"x","token":"x"}\n',
    b'x' * 513 + b'\n',
])
def test_native_protocol_rejects_ambiguous_or_unbounded_input_before_auth(rig, raw):
    with pytest.raises((ValueError, DesktopGrantError)):
        dispatch(rig.broker, raw)
    assert rig.broker.desktop.guard is None
    rig.verifier.assert_not_called()


def test_native_authority_has_no_http_routes():
    assert not any('desktop' in path for path in AUTH.ROUTES)


def test_root_peer_uses_kernel_credentials_and_fails_closed(monkeypatch):
    monkeypatch.setattr(socket, 'SO_PEERCRED', 17, raising=False)
    connection = Mock()
    connection.getsockopt.return_value = struct.pack('3i', 123, 0, 1000)
    assert root_peer(connection)
    connection.getsockopt.return_value = struct.pack('3i', 123, 1000, 0)
    assert not root_peer(connection)
    connection.getsockopt.side_effect = OSError('fixture')
    assert not root_peer(connection)


def test_socket_path_refuses_public_parent_and_existing_file(rig):
    with tempfile.TemporaryDirectory(dir=Path('/tmp').resolve(), prefix='desk-') as directory:
        parent = Path(directory)
        parent.chmod(0o755)
        with pytest.raises(ValueError):
            DesktopControlServer(parent / 'auth.sock', rig.broker)
        parent.chmod(0o700)
        path = parent / 'auth.sock'
        path.write_text('preserve')
        with pytest.raises(ValueError):
            DesktopControlServer(path, rig.broker)
        assert path.read_text() == 'preserve'
        assert not rig.broker.closing


@pytest.mark.parametrize('peer_allowed', [True, False])
def test_real_unix_socket_root_admission_and_cleanup(rig, peer_allowed):
    with tempfile.TemporaryDirectory(dir=Path('/tmp').resolve(), prefix='desk-') as directory:
        path = Path(directory) / 'auth.sock'
        server = DesktopControlServer(path, rig.broker, peer_check=lambda sock: peer_allowed)
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        server.start()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(path))
                try:
                    client.sendall(json.dumps(dict(action='open', binding=BINDING, mode='desktop')).encode() + b'\n')
                    result = client.recv(1024)
                except (ConnectionResetError, BrokenPipeError):
                    result = b''
                if peer_allowed:
                    response = json.loads(result)
                    assert response['ok'] is True
                    assert set(response['result']) == {'guard'}
                else:
                    assert result == b''
                    assert rig.broker.desktop.guard is None
                rig.verifier.assert_not_called()
        finally:
            assert server.server_close()
        assert not path.exists()
        assert rig.broker.closing


def test_real_unix_socket_full_synthetic_native_flow(rig):
    with tempfile.TemporaryDirectory(dir=Path('/tmp').resolve(), prefix='desk-') as directory:
        path = Path(directory) / 'auth.sock'
        server = DesktopControlServer(path, rig.broker, peer_check=lambda sock: True)
        server.start()
        def request(payload):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(str(path))
                client.sendall(json.dumps(payload).encode() + b'\n')
                with client.makefile('rb') as stream:
                    return json.loads(stream.readline(514))
        try:
            opened = request(dict(action='open', binding=BINDING, mode='desktop'))
            guard = opened['result']['guard']
            ticket = request(dict(action='unlock', guard=guard, pin=PIN))
            token = ticket['result']['token']
            assert not request(dict(action='check', guard=guard, token=token))['result']['authorized']
            assert request(dict(action='activate', guard=guard, token=token))['result']['authorized']
            assert request(dict(action='presence', guard=guard, token=token))['result']['authorized']
            assert request(dict(action='activity', guard=guard, token=token))['result']['authorized']
            assert rig.device.status() == {'locked': True}
            assert request(dict(action='lock', guard=guard))['result'] == {'locked': True}
            assert not request(dict(action='activate', guard=guard, token=token))['result']['authorized']
            rig.verifier.assert_called_once_with(PIN)
        finally:
            assert server.server_close()


def test_unix_worker_budget_closes_excess_clients_without_dispatch(rig):
    with tempfile.TemporaryDirectory(dir=Path('/tmp').resolve(), prefix='desk-') as directory:
        path = Path(directory) / 'auth.sock'
        server = DesktopControlServer(path, rig.broker, peer_check=lambda sock: True)
        server.start()
        clients = []
        try:
            for _ in range(4):
                client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                client.settimeout(2)
                client.connect(str(path))
                clients.append(client)
            with server.changed:
                assert server.changed.wait_for(lambda: len(server.active) == 4, timeout=1)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as excess:
                excess.settimeout(2)
                excess.connect(str(path))
                assert excess.recv(1) == b''
            assert len(server.active) == 4
            assert rig.broker.desktop.guard is None
            rig.verifier.assert_not_called()
        finally:
            for client in clients:
                client.close()
            assert server.server_close()
