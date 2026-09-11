from dataclasses import asdict, replace
from types import SimpleNamespace
import uuid
import socket

import pytest

from sage_lantern.deployment_drain import DeploymentDrainError, verify_broker_request, verify_root_broker
from sage_lantern.message_storage import MessageStorageSnapshot
from sage_lantern.storage_supervisor import StorageSupervisorError
from sage_lantern.mesh_daemon import MeshDaemon
from sage_lantern.mesh_voice_ipc import send, receive
from test_storage_supervisor import setup
from test_deployment_drain_system import DeploymentSystem


@pytest.fixture
def broker(setup):
    supervisor, backend, state = setup
    fake = DeploymentSystem()
    fake.units["sage-lantern-voice-loop.service"].update(MainPID="0", InvocationID="a" * 32, ActiveState="inactive")
    backend.system = fake.adapter()
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    proof = supervisor.drain(str(uuid.uuid4()))
    request = dict(op="verify-deployment-drain", proof=asdict(proof), nonce=str(uuid.uuid4()))
    return supervisor, backend, state, fake, proof, request


@pytest.mark.parametrize("peer_uid", [0, 994, 1000])
def test_audio_status_dispatch_preserves_kernel_uid_and_request(monkeypatch, peer_uid):
    import sage_lantern.audio_readiness as audio
    supervisor = object()
    daemon = MeshDaemon(supervisor, object())
    request = {"op": "verify-audio-operational", "nonce": str(uuid.uuid4())}
    calls = []
    def handler(owner, value, *, peer_uid, listener_ack):
        assert listener_ack is daemon._listener_ack
        calls.append((owner, value, peer_uid))
        return {"result": {"ready": False, "nonce": value["nonce"]}}
    monkeypatch.setattr(audio, 'operational_broker_request', handler)
    client, server = socket.socketpair()
    try:
        client.settimeout(0.1)
        send(client, request)
        daemon.handle(server, pid=123, uid=peer_uid)
        assert receive(client, 256) == {"result": {"ready": False, "nonce": request["nonce"]}}
        assert calls == [(supervisor, request, peer_uid)]
    finally:
        client.close()
        server.close()


def test_listener_exchange_dispatch_uses_owned_cache_and_kernel_credentials(monkeypatch):
    daemon = MeshDaemon(object(), object())
    calls = []
    request = {'op': 'ack-voice-listener'}
    def exchange(connection, supervisor, value, *, pid, uid):
        assert supervisor is daemon.supervisor
        calls.append((value, pid, uid))
        send(connection, {'result': True})
    monkeypatch.setattr(daemon._listener_ack, 'exchange', exchange)
    client, server = socket.socketpair()
    try:
        client.settimeout(0.1)
        send(client, request)
        daemon.handle(server, pid=321, uid=994)
        assert receive(client, 128) == {'result': True}
        assert calls == [(request, 321, 994)]
    finally:
        client.close()
        server.close()


def test_actual_retained_supervisor_proof_after_model_stop(broker):
    supervisor, backend, state, fake, proof, request = broker
    fake.units["sage-lantern-llm.service"].update(MainPID="0", ActiveState="inactive")
    assert verify_broker_request(supervisor, proof, request, peer_uid=0) == {
        "result": {"proof": asdict(proof), "nonce": request["nonce"]}}
    assert not any(call[1] == "stop" for call, _ in fake.calls)


@pytest.mark.parametrize("fault", [None, "uid", "missing_proof", "copied_proof", "resumed"])
@pytest.mark.parametrize("operation", ["verify-deployment-drain", "verify-deployment-completion"])
def test_daemon_dispatch_verifies_retained_proof_over_socket(broker, fault, operation):
    supervisor, backend, state, fake, proof, request = broker
    request["op"] = operation
    daemon = MeshDaemon(supervisor, SimpleNamespace())
    daemon._proof = proof
    uid = 0
    if fault == "uid":
        uid = 1000
    elif fault == "missing_proof":
        daemon._proof = None
    elif fault == "copied_proof":
        daemon._proof = replace(proof)
    elif fault == "resumed":
        supervisor.phase = "active"
    client, server = socket.socketpair()
    try:
        client.settimeout(0.1)
        server.settimeout(0.1)
        send(client, request)
        if fault is None:
            daemon.handle(server, pid=123, uid=uid)
            assert receive(client, 1024) == {"result": {"nonce": request["nonce"], "proof": asdict(proof)}}
        else:
            with pytest.raises((DeploymentDrainError, StorageSupervisorError)):
                daemon.handle(server, pid=123, uid=uid)
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("fault", [None, "uid", "malformed", "producer", "epoch"])
def test_daemon_active_evidence_dispatch_is_root_only_and_live(setup, fault):
    supervisor, backend, state = setup
    supervisor.start()
    resource = SimpleNamespace(close=lambda timeout: True)
    supervisor.own_resource(resource)
    daemon = MeshDaemon(supervisor, resource)
    request = {"op": "verify-deployment-active", "nonce": str(uuid.uuid4())}
    if fault == "malformed":
        request["proof"] = {}
    elif fault == "producer":
        backend.instance = replace(backend.instance, pid=999)
    elif fault == "epoch":
        state["snapshot"] = replace(state["snapshot"], vault_generation="2")
    client, server = socket.socketpair()
    try:
        client.settimeout(0.1)
        server.settimeout(0.1)
        send(client, request)
        if fault is None:
            daemon.handle(server, pid=123, uid=0)
            evidence = receive(client, 1024)["result"]
            assert evidence["nonce"] == request["nonce"]
            assert evidence["voice_pid"] == 901
            assert evidence["broker_id"] == supervisor.broker_id
            assert evidence["vault_epoch"] == state["snapshot"].epoch
        else:
            with pytest.raises((DeploymentDrainError, StorageSupervisorError)):
                daemon.handle(server, pid=123, uid=1000 if fault == "uid" else 0)
    finally:
        client.close()
        server.close()


@pytest.mark.parametrize("fault", ["copy", "uid", "epoch", "vault", "shutdown", "resource", "leases", "phase", "voice", "owner", "lock"])
def test_lost_authority_is_not_a_shape_check(broker, fault):
    supervisor, backend, state, fake, proof, request = broker
    uid = 0
    if fault == "copy":
        proof = replace(proof)
    elif fault == "uid":
        uid = 1000
    elif fault == "epoch":
        state["snapshot"] = MessageStorageSnapshot("c" * 64, "2")
    elif fault == "vault":
        backend.vault = (801, "a" * 32)
    elif fault == "shutdown":
        supervisor._shutdown_requested = True
    elif fault == "resource":
        supervisor._resource_joined = False
    elif fault == "leases":
        supervisor._leases["orphan"] = 999
    elif fault == "phase":
        supervisor.phase = "vault_closed"
    elif fault == "voice":
        fake.units["sage-lantern-voice-loop.service"].update(MainPID="999", ActiveState="active")
    elif fault == "owner":
        supervisor._closed = True
    else:
        supervisor._control.acquire()
    try:
        with pytest.raises((DeploymentDrainError, StorageSupervisorError)):
            verify_broker_request(supervisor, proof, request, peer_uid=uid)
    finally:
        if fault == "lock":
            supervisor._control.release()
        if fault == "owner":
            supervisor._closed = False


@pytest.mark.parametrize("fault", [None, "nonce", "proof", "legacy"])
def test_fixed_ipc_requires_fresh_correlated_root_reply(monkeypatch, fault):
    import sage_lantern.mesh_voice_ipc as ipc
    sent = []
    closed = []
    connection = SimpleNamespace(settimeout=lambda timeout: None, close=lambda: closed.append(True))
    monkeypatch.setattr(ipc, "connect", lambda: connection)
    monkeypatch.setattr(ipc, "send", lambda connection, value: sent.append(value))
    monkeypatch.setattr("sage_lantern.deployment_drain.os.geteuid", lambda: 0)
    def response(connection, limit):
        if fault == "legacy":
            return {"result": True}
        return {"result": {"nonce": "wrong" if fault == "nonce" else sent[0]["nonce"],
                           "proof": {} if fault == "proof" else sent[0]["proof"]}}
    monkeypatch.setattr(ipc, "receive", response)
    proof = dict(broker_id="a" * 32, vault_epoch="b" * 64 + ":1", transition_id=str(uuid.uuid4()))
    if fault is None:
        assert verify_root_broker(proof) is True
    else:
        with pytest.raises(DeploymentDrainError):
            verify_root_broker(proof)
    assert closed == [True] and sent[0]["op"] == "verify-deployment-drain"
