from dataclasses import replace
import socket
import threading
from types import SimpleNamespace

import pytest

from sage_lantern import voice_listener_ack as module
from sage_lantern.mesh_voice_ipc import MeshIPCError, receive, send
from sage_lantern.storage_supervisor import Producer, RootStorageSupervisor


@pytest.fixture
def supervisor():
    value = object.__new__(RootStorageSupervisor)
    value._control, value._mutex = threading.Lock(), threading.Lock()
    value._held = lambda: None
    value.phase, value._shutdown_requested, value._proof = 'active', False, None
    value._resource, value._resource_joined = object(), False
    value.broker_id, value._baseline, value._vault = 'a' * 32, 'epoch:1', ('10', 'b' * 32)
    value._producer = Producer(123, 994, dict(boot_id='boot', start_ticks='100', invocation_id='voice'))
    value._epoch = lambda: value._baseline
    value._backend = SimpleNamespace(vault_identity=lambda: value._vault, producer=lambda: value._producer)
    return value


def exchange(cache, supervisor, fault=None):
    client, broker = socket.socketpair()
    failures = []
    def respond():
        try:
            challenge = receive(client)
            if fault == 'nonce':
                challenge['listener_challenge'] = '0' * 64
            if fault == 'binding':
                challenge['binding'] = '0' * 64
            if fault == 'drain':
                supervisor.phase = 'draining'
            if fault == 'restart':
                supervisor._producer = replace(supervisor._producer, identity={'invocation_id': 'new'})
            send(client, {**challenge, 'listening': 1 if fault == 'integer' else True})
            if fault is None:
                assert receive(client) == {'result': True}
        except Exception as error:
            failures.append(error)
        finally:
            client.close()
    thread = threading.Thread(target=respond)
    thread.start()
    try:
        cache.exchange(broker, supervisor, {'op': module.OP}, pid=123, uid=994)
    finally:
        broker.close()
        thread.join(2)
    assert not thread.is_alive() and not failures


def test_nonce_exchange_and_age_are_not_startup_authority(supervisor, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    cache = module.ListenerAcknowledgements()
    assert not cache.verified(supervisor, pid=123, uid=994)
    exchange(cache, supervisor)
    assert cache.verified(supervisor, pid=123, uid=994)
    assert not cache.verified(supervisor, pid=124, uid=994)
    assert not cache.verified(supervisor, pid=123, uid=995)
    now[0] += module.MAX_AGE
    assert not cache.verified(supervisor, pid=123, uid=994)
    assert not module.ListenerAcknowledgements().verified(supervisor, pid=123, uid=994)


@pytest.mark.parametrize('fault', ['nonce', 'binding', 'integer', 'drain', 'restart'])
def test_failed_renewal_does_not_extend_old_ack(supervisor, monkeypatch, fault):
    now = [10.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    cache = module.ListenerAcknowledgements()
    exchange(cache, supervisor)
    original = cache._ack
    now[0] += 1
    with pytest.raises(MeshIPCError):
        exchange(cache, supervisor, fault)
    assert cache._ack == original
    assert cache.verified(supervisor, pid=123, uid=994) is (fault not in ('drain', 'restart'))
    now[0] = 10.0 + module.MAX_AGE
    assert not cache.verified(supervisor, pid=123, uid=994)


def test_inflight_renewal_retains_matching_fresh_ack(supervisor, monkeypatch):
    now = [10.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    cache = module.ListenerAcknowledgements()
    exchange(cache, supervisor)
    original = cache._ack
    now[0] += 1
    real_receive = module.receive
    def receive_renewal(*args):
        assert cache._ack == original
        assert cache.verified(supervisor, pid=123, uid=994)
        return real_receive(*args)
    monkeypatch.setattr(module, 'receive', receive_renewal)
    exchange(cache, supervisor)
    assert cache._ack[3] == 11.0
    now[0] = 10.0 + module.MAX_AGE
    assert cache.verified(supervisor, pid=123, uid=994)
    now[0] = 11.0 + module.MAX_AGE
    assert not cache.verified(supervisor, pid=123, uid=994)


@pytest.mark.parametrize('matching', [False, True])
def test_renewal_during_verification_requires_same_binding(supervisor, monkeypatch, matching):
    now = [10.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    cache = module.ListenerAcknowledgements()
    exchange(cache, supervisor)
    original = module.binding
    now[0] = 11.0
    def binding(*args, **kwargs):
        value = original(*args, **kwargs)
        with cache._mutex:
            cache._ack = (value if matching else 'other-binding', 123, 994, now[0])
        return value
    monkeypatch.setattr(module, 'binding', binding)
    assert cache.verified(supervisor, pid=123, uid=994) is matching


@pytest.mark.parametrize('change', ['broker', 'epoch', 'vault', 'producer', 'resource', 'drain', 'shutdown'])
def test_current_context_changes_invalidate_ack(supervisor, change):
    cache = module.ListenerAcknowledgements()
    exchange(cache, supervisor)
    if change == 'broker':
        supervisor.broker_id = 'c' * 32
    elif change == 'epoch':
        supervisor._baseline = 'epoch:2'
    elif change == 'vault':
        supervisor._vault = ('20', 'd' * 32)
    elif change == 'producer':
        supervisor._producer = replace(supervisor._producer, identity={'start_ticks': '200'})
    elif change == 'resource':
        supervisor._resource = object()
    elif change == 'drain':
        supervisor.phase = 'draining'
    else:
        supervisor._shutdown_requested = True
    assert not cache.verified(supervisor, pid=123, uid=994)


def test_binding_io_does_not_hold_supervisor_locks(supervisor):
    def epoch():
        assert not supervisor._control.locked() and not supervisor._mutex.locked()
        return supervisor._baseline
    supervisor._epoch = epoch
    assert module.binding(supervisor, pid=123, uid=994)
    supervisor._backend.producer = lambda: replace(supervisor._producer, pid=999)
    with pytest.raises(MeshIPCError):
        module.binding(supervisor, pid=123, uid=994)


@pytest.mark.parametrize('lock', ['_mutex', '_control'])
def test_busy_supervisor_never_waits_for_lock(supervisor, lock):
    held = getattr(supervisor, lock)
    held.acquire()
    try:
        with pytest.raises(MeshIPCError):
            module.binding(supervisor, pid=123, uid=994)
    finally:
        held.release()


def test_late_ack_not_published(supervisor, monkeypatch):
    now = [0.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    monkeypatch.setattr(module, 'send', lambda *args: None)
    monkeypatch.setattr(module.secrets, 'token_hex', lambda size: 'a' * 64)
    original = module.binding(supervisor, pid=123, uid=994)
    def answer(*args):
        now[0] = module.TIMEOUT
        return {'listener_challenge': 'a' * 64, 'binding': original, 'listening': True}
    monkeypatch.setattr(module, 'receive', answer)
    cache = module.ListenerAcknowledgements()
    with pytest.raises(MeshIPCError):
        cache.exchange(SimpleNamespace(settimeout=lambda timeout: None), supervisor,
                       {'op': module.OP}, pid=123, uid=994)
    assert not cache.verified(supervisor, pid=123, uid=994)


@pytest.mark.parametrize('fault', [None, 'stopped', 'dead_thread', 'wrong_address', 'not_listening'])
def test_voice_checks_owned_listener_before_ack(monkeypatch, fault):
    client, broker = socket.socketpair()
    monkeypatch.setattr(module, 'connect', lambda: client)
    answers = []
    def root():
        try:
            assert receive(broker) == {'op': module.OP}
            send(broker, {'listener_challenge': 'a' * 64, 'binding': 'b' * 64})
            answers.append(receive(broker))
            send(broker, {'result': True})
        except MeshIPCError:
            pass
        finally:
            broker.close()
    worker = threading.Thread(target=root)
    worker.start()
    listener = SimpleNamespace(family=socket.AF_INET, type=socket.SOCK_STREAM,
        getsockname=lambda: ('127.0.0.1', 9999 if fault == 'wrong_address' else 3197),
        getsockopt=lambda *args: 0 if fault == 'not_listening' else 1)
    args = (SimpleNamespace(socket=listener), SimpleNamespace(is_alive=lambda: fault != 'dead_thread'),
            SimpleNamespace(is_set=lambda: fault == 'stopped'))
    if fault is None:
        assert module.acknowledge(*args) is True
    else:
        with pytest.raises(MeshIPCError):
            module.acknowledge(*args)
    worker.join(2)
    assert not worker.is_alive()
    assert bool(answers) is (fault is None)
