from dataclasses import replace
from types import SimpleNamespace
import uuid
import json

import pytest

import sage_lantern.deployment_drain as module
from sage_lantern.storage_supervisor import StorageSupervisorError
from test_storage_supervisor import setup
from test_deployment_drain import drained


@pytest.mark.parametrize("fault", [None, "uid", "epoch", "producer", "phase", "resource", "joined", "shutdown", "busy"])
def test_active_evidence_requires_live_owned_storage_and_producer(setup, fault):
    supervisor, backend, state = setup
    supervisor.start()
    supervisor.own_resource(SimpleNamespace(close=lambda timeout: True))
    request = dict(op="verify-deployment-active", nonce=str(uuid.uuid4()))
    if fault == "epoch":
        state["snapshot"] = replace(state["snapshot"], vault_generation="2")
    elif fault == "producer":
        backend.instance = replace(backend.instance, pid=999)
    elif fault == "phase":
        supervisor.phase = "recovery_required"
    elif fault == "resource":
        supervisor._resource = None
    elif fault == "joined":
        supervisor._resource_joined = True
    elif fault == "shutdown":
        supervisor._shutdown_requested = True
    elif fault == "busy":
        supervisor._control.acquire()
    try:
        if fault is None:
            result = module.verify_broker_active_request(supervisor, request, peer_uid=0)["result"]
            assert result["nonce"] == request["nonce"]
            assert result["vault_epoch"] == state["snapshot"].epoch
            assert result["voice_pid"] == 901
            assert backend.events == ["start_voice"]
        else:
            with pytest.raises((module.DeploymentDrainError, StorageSupervisorError)):
                module.verify_broker_active_request(supervisor, request, peer_uid=1000 if fault == "uid" else 0)
    finally:
        if fault == "busy":
            supervisor._control.release()


@pytest.mark.parametrize("fault", [None, "drain", "resume", "epoch", "broker"])
def test_resume_completion_requires_distinct_live_broker_same_epoch(monkeypatch, fault):
    proof = dict(transition_id=str(uuid.uuid4()), broker_id="a" * 32, vault_epoch="c" * 64 + ":1")
    calls = []
    def verify(value):
        calls.append("verify")
        assert value == proof
        return fault != "drain"
    def resume(operation, transition):
        calls.append(operation)
        assert operation == "resume" and transition == proof["transition_id"]
        return fault != "resume"
    def active():
        calls.append("active")
        return dict(broker_id=proof["broker_id"] if fault == "broker" else "b" * 32,
            vault_epoch="c" * 64 + ":2" if fault == "epoch" else proof["vault_epoch"])
    monkeypatch.setattr(module, "verify_root_broker", verify)
    monkeypatch.setattr(module, "root_control", resume)
    monkeypatch.setattr(module, "verify_root_broker_active", active)
    if fault is None:
        assert module.resume_root_broker(proof)["broker_id"] == "b" * 32
    else:
        with pytest.raises(module.DeploymentDrainError):
            module.resume_root_broker(proof)
    assert calls == (["verify"] if fault == "drain" else ["verify", "resume"] if fault == "resume"
                     else ["verify", "resume", "active"])


@pytest.mark.parametrize("fault", [None, "legacy", "nonce", "pid", "epoch", "extra"])
def test_active_transport_rejects_unbound_or_legacy_reply(monkeypatch, fault):
    import sage_lantern.mesh_voice_ipc as ipc
    sent, closed = [], []
    connection = SimpleNamespace(settimeout=lambda timeout: None, close=lambda: closed.append(True))
    monkeypatch.setattr(module.os, "geteuid", lambda: 0)
    monkeypatch.setattr(ipc, "connect", lambda: connection)
    monkeypatch.setattr(ipc, "send", lambda connection, request: sent.append(request))
    def receive(connection, limit):
        value = dict(nonce=sent[0]["nonce"], broker_id="a" * 32, vault_epoch="b" * 64 + ":1",
                     broker_pid=100, voice_pid=101)
        if fault == "legacy":
            return {"result": True}
        if fault == "nonce":
            value["nonce"] = str(uuid.uuid4())
        elif fault == "pid":
            value["voice_pid"] = True
        elif fault == "epoch":
            value["vault_epoch"] = "b" * 64 + ":0"
        elif fault == "extra":
            value["ready"] = True
        return {"result": value}
    monkeypatch.setattr(ipc, "receive", receive)
    if fault is None:
        assert module.verify_root_broker_active()["voice_pid"] == 101
    else:
        with pytest.raises(module.DeploymentDrainError):
            module.verify_root_broker_active()
    assert closed == [True]


@pytest.mark.parametrize("fault", [None, "mutation", "release", "model", "auth", "resume", "surfaces", "kiosk"])
def test_explicit_completion_or_retained_recovery(drained, monkeypatch, fault):
    import sage_lantern.deployment_completion as completion
    owner, journal, state = drained
    owner.prepare()
    state.aborted = lambda: True
    def release():
        if fault == "release":
            return False
        state.fence = False
        return True
    state.release_fence = release
    state.kiosk.resume = lambda: fault != "kiosk"
    actions = []
    def command(arguments, **kwargs):
        assert journal.load().active and owner._marker.exists()
        actions.append(arguments)
        if ((fault == "model" and arguments[-1] == completion.LLM)
                or (fault == "auth" and arguments[-1] == "auth")
                or (fault == "surfaces" and arguments[-1] == "display")):
            raise RuntimeError("synthetic start failure")
    runner = SimpleNamespace(command=command, model_baseline_ready=lambda: True,
        model_idle=lambda slots: True, public_json=lambda *args: [], locked=lambda: True,
        AUTH="auth", CAMERA="camera", UI="ui", DISPLAY="display",
        display_power=lambda value: None, service_identity=lambda service: (123, "a" * 32))
    active = {"broker_id": "b" * 32, "vault_epoch": "d" * 64 + ":1"}
    def resume(proof):
        if fault == "resume":
            raise RuntimeError("synthetic resume failure")
        return active
    monkeypatch.setattr(completion, "managed", lambda: True)
    monkeypatch.setattr(module, "verify_root_broker", lambda proof, **kwargs: True)
    monkeypatch.setattr(completion, "resume_root_broker", resume)
    monkeypatch.setattr(completion, "verify_root_broker_active", lambda: active)
    def mutation(lease):
        assert lease.check()
        if fault == "mutation":
            raise RuntimeError("synthetic mutation failure")
        lease.mutate("vision_dropins", lambda: None)
    if fault is None:
        result = owner.apply_and_complete(mutation, completion=completion.DeploymentCompletion(runner))
        assert result["completed"] and not result["idle_rearmed"] and not result["capture_verified"]
        assert not state.fence and not owner._marker.exists() and not journal.load().active
        assert journal.load().snapshot.completed
    else:
        with pytest.raises((RuntimeError, module.DeploymentDrainError)):
            owner.apply_and_complete(mutation, completion=completion.DeploymentCompletion(runner))
        assert state.fence and owner._marker.exists() and journal.load().needs_recovery
    assert all("sage-lantern-voice-loop.service" not in action for action in actions)
    assert all("restart" not in action and "stop" not in action for action in actions)
    assert owner.check() is False


@pytest.mark.parametrize("fault", [None, "action", "postcheck"])
def test_bounded_mutation_retains_intent_until_live_postcheck(drained, fault):
    owner, journal, state = drained
    owner.prepare()
    path = journal.directory / ("deployment-" + state.transition_id + "-dropins.json")
    def action():
        assert json.loads(path.read_text())["phase"] == "intent"
        if fault == "action":
            raise RuntimeError("synthetic mutation failure")
        if fault == "postcheck":
            state.broker_ok = False
        return "written"
    if fault is None:
        assert owner.run(lambda lease: lease.mutate("dropins", action)) == "written"
    else:
        with pytest.raises(RuntimeError):
            owner.run(lambda lease: lease.mutate("dropins", action))
    assert json.loads(path.read_text())["phase"] == ("done" if fault is None else "intent")
    assert state.fence and journal.load().needs_recovery
