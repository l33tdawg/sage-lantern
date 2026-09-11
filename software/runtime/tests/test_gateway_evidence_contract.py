import copy
import hashlib
import socket

import pytest

from sage_lantern.gateway_evidence import handle
from sage_lantern import gateway_evidence
from sage_lantern.mesh_voice_ipc import receive, send
from sage_lantern.public_mesh_protocol import canonical


@pytest.fixture
def channel():
    client, server = socket.socketpair()
    client.settimeout(0.1)
    server.settimeout(0.1)
    try:
        yield client, server
    finally:
        client.close()
        server.close()


def request(settings):
    return dict(nonce='a' * 32, config_digest=hashlib.sha256(canonical(settings)).hexdigest())


def test_response_binds_nonce_and_exact_config(channel):
    client, server = channel
    settings = {'synthetic': 'not a production configuration'}
    payload = request(settings)
    send(client, payload)
    inspected = []
    def inspect(value):
        inspected.append(copy.deepcopy(value))
        return True
    handle(server, config_reader=lambda: {'public_gateway': settings}, inspect=inspect,
           peer_reader=lambda connection: (123, 0, 0))
    assert receive(client, 512) == {**payload, 'current': True}
    assert inspected == [settings]


@pytest.mark.parametrize('uid', [1, 1000, 65534])
def test_non_root_denied_before_config_or_inspection(channel, uid):
    client, server = channel
    def forbidden(*args):
        pytest.fail('unauthorized caller reached privileged inspection')
    with pytest.raises(Exception):
        handle(server, config_reader=forbidden, inspect=forbidden,
               peer_reader=lambda connection: (123, uid, uid))


@pytest.mark.parametrize('change', [
    {'nonce': 'a' * 31}, {'nonce': 1}, {'config_digest': 'g' * 64},
    {'config_digest': None}, {'path': '/proc/1/mem'}, {'current': True},
])
def test_malformed_request_never_selects_inspection_target(channel, change):
    client, server = channel
    payload = {**request({}), **change}
    send(client, payload)
    def forbidden(*args):
        pytest.fail('malformed request reached privileged inspection')
    with pytest.raises(Exception):
        handle(server, config_reader=forbidden, inspect=forbidden,
               peer_reader=lambda connection: (123, 0, 0))


def test_different_config_digest_rejected_before_inspection(channel):
    client, server = channel
    send(client, request({'old': True}))
    def forbidden(*args):
        pytest.fail('mismatched configuration reached inspection')
    with pytest.raises(Exception):
        handle(server, config_reader=lambda: {'public_gateway': {'new': True}},
               inspect=forbidden, peer_reader=lambda connection: (123, 0, 0))


def test_config_change_during_inspection_denies_evidence(channel):
    client, server = channel
    settings = {'revision': 1}
    payload = request(settings)
    send(client, payload)
    snapshots = iter([{'public_gateway': settings}, {'public_gateway': {'revision': 2}}])
    handle(server, config_reader=lambda: next(snapshots), inspect=lambda value: True,
           peer_reader=lambda connection: (123, 0, 0))
    assert receive(client, 512) == {**payload, 'current': False}


@pytest.mark.parametrize('result', [False, None, 1, 'true'])
def test_only_exact_positive_inspection_is_evidence(channel, result):
    client, server = channel
    payload = request({})
    send(client, payload)
    handle(server, config_reader=lambda: {'public_gateway': {}}, inspect=lambda value: result,
           peer_reader=lambda connection: (123, 0, 0))
    assert receive(client, 512) == {**payload, 'current': False}


@pytest.mark.parametrize('operation', ['recv', 'sendall'])
def test_deadline_exceeded_inside_io_never_returns_success(monkeypatch, operation):
    clock = [10.0]
    monkeypatch.setattr(gateway_evidence.time, 'monotonic', lambda: clock[0])
    class SlowConnection:
        def settimeout(self, seconds):
            assert seconds == 1.0

        def recv(self, size):
            clock[0] = 11.0
            return b'x'

        def sendall(self, data):
            clock[0] = 11.0
    bounded = gateway_evidence.DeadlineConnection(SlowConnection(), 11.0)
    with pytest.raises(Exception):
        getattr(bounded, operation)(1 if operation == 'recv' else b'x')


def test_incremental_reads_share_one_deadline(monkeypatch):
    clock = [10.0]
    timeouts = []
    monkeypatch.setattr(gateway_evidence.time, 'monotonic', lambda: clock[0])
    class IncrementalConnection:
        def settimeout(self, seconds):
            timeouts.append(seconds)

        def recv(self, size):
            clock[0] += 0.25
            return b'x'
    bounded = gateway_evidence.DeadlineConnection(IncrementalConnection(), 11.0)
    assert bounded.recv(1) == b'x'
    assert bounded.recv(1) == b'x'
    assert bounded.recv(1) == b'x'
    with pytest.raises(Exception):
        bounded.recv(1)
    assert timeouts == [1.0, 0.75, 0.75, 0.5, 0.5, 0.25, 0.25]
