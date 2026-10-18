"""Real local Unix streams with synthetic peer/pidfd ownership fixtures.

These exercise the production Handler and admission logic. They do not claim
Linux SO_PEERCRED/pidfd attestation on the management Mac.
"""
import importlib.util
import json
from pathlib import Path
import socket
import socketserver
import tempfile
import threading
from types import SimpleNamespace
import pytest
from test_desktop_auth import rig, PIN
from test_desktop_native import session

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('desktop_adapter_transport',
    ROOT / 'scripts/lantern/desktop-native-adapter.py')
ADAPTER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ADAPTER)

@pytest.fixture
def transport(rig, monkeypatch):
    monkeypatch.setattr(ADAPTER, 'pidfd_alive', lambda _: True)
    peer = [123, 1000]
    monkeypatch.setattr(ADAPTER, 'kernel_peer', lambda _: tuple(peer))
    valid = [True]
    native = session(rig)
    class Harness(ADAPTER.NativeServer):
        def __init__(self, path):
            self.anchor = SimpleNamespace(binding='fixture-pid-start-session', pid=123, uid=1000)
            self.broker = native.broker
            self.pidfd = 7
            self.path = path
            self.identity = None
            self.slot = threading.Lock()
            self.native = self.client = None
            socketserver.UnixStreamServer.__init__(self, str(path), ADAPTER.Handler)
            info = path.lstat()
            self.identity = info.st_dev, info.st_ino
        def peer_valid(self):
            return valid[0]
    with tempfile.TemporaryDirectory(dir=Path('/tmp').resolve(), prefix='dn-') as directory:
        server = Harness(Path(directory) / 'native.sock')
        thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            yield SimpleNamespace(server=server, peer=peer, valid=valid)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
            native.close()

class Client:
    def __init__(self, path):
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.settimeout(2)
        self.socket.connect(str(path))
        self.stream = self.socket.makefile('rb')
    def send(self, payload):
        self.socket.sendall(json.dumps(payload).encode() + b'\n')
    def reply(self):
        return json.loads(self.stream.readline(514))
    def request(self, payload):
        self.send(payload)
        return self.reply()
    def close(self):
        self.stream.close()
        self.socket.close()

@pytest.mark.parametrize('peer', [(124, 1000), (123, 0), (123, 1001)])
def test_real_stream_rejects_any_other_pid_or_uid_before_pin(transport, rig, peer):
    transport.peer[:] = peer
    client = Client(transport.server.path)
    try:
        try:
            client.send({'action': 'unlock', 'pin': PIN})
            assert client.socket.recv(1) == b''
        except (ConnectionResetError, BrokenPipeError):
            pass
        rig.verifier.assert_not_called()
    finally:
        client.close()

def test_real_stream_returns_only_boolean_authority_and_eof_revokes(transport, rig):
    client = Client(transport.server.path)
    try:
        assert client.request({'action': 'open', 'mode': 'desktop'}) == {
            'ok': True, 'result': {'authorized': False}}
        assert client.request({'action': 'unlock', 'pin': PIN}) == {
            'ok': True, 'result': {'authorized': True}}
        assert rig.broker.desktop.active is not None
    finally:
        client.close()
    assert transport.server.slot.acquire(timeout=2)
    transport.server.slot.release()
    assert rig.broker.desktop.active is None

def test_disconnect_while_verifier_is_running_fences_before_response(transport, rig):
    client = Client(transport.server.path)
    entered, release = threading.Event(), threading.Event()
    def verify(pin):
        entered.set()
        assert release.wait(2)
        return True
    rig.verifier.side_effect = verify
    try:
        client.request({'action': 'open', 'mode': 'desktop'})
        client.send({'action': 'unlock', 'pin': PIN})
        assert entered.wait(1)
        client.close()
        # The production EOF watcher closes/fences without waiting for PIN I/O.
        original = transport.server.native
        deadline = threading.Event()
        for _ in range(20):
            if original.closed:
                break
            deadline.wait(.05)
        assert original.closed
    finally:
        release.set()
        client.close()
    assert transport.server.slot.acquire(timeout=2)
    transport.server.slot.release()
    assert rig.broker.desktop.active is None
    assert rig.broker.desktop.pending is None

def test_second_connection_cannot_replace_live_guard(transport, rig):
    first = Client(transport.server.path)
    second = None
    try:
        first.request({'action': 'open', 'mode': 'desktop'})
        first.request({'action': 'unlock', 'pin': PIN})
        guard = rig.broker.desktop.guard
        second = Client(transport.server.path)
        try:
            second.send({'action': 'open', 'mode': 'kiosk'})
            assert second.socket.recv(1) == b''
        except (ConnectionResetError, BrokenPipeError):
            pass
        assert rig.broker.desktop.guard == guard
        assert first.request({'action': 'presence'})['result']['authorized']
    finally:
        first.close()
        if second:
            second.close()
