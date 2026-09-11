"""Real runtime/SDK/crypto with synthetic HTTP and serial; no hardware."""

from dataclasses import replace
import argparse
import importlib.util
import json
from pathlib import Path
import threading
import uuid

import pytest

from sage_lantern.community_service import CommunityError, CommunityMeshProvider, CommunityService
from sage_lantern.mesh_community import MeshActionAuthority, MeshRequestAccess
from sage_lantern.mesh_runtime import MeshRuntimeError
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from test_mesh_runtime import factory


@pytest.fixture
def integrated(factory):
    factory.adopt()
    provider = CommunityMeshProvider.from_config(factory.config,
        coordinator=factory.scheduler, activation_check=factory.arguments["activation_check"],
        verify_binding=factory.arguments["verify_binding"],
        storage_lifecycle_check=factory.arguments["storage_lifecycle_check"])
    factory.runtime = provider._runtime
    factory.service = CommunityService(mesh_provider=provider)
    factory.provider = provider
    factory.call = lambda route, body={}: factory.service.dispatch("/community/" + route, body,
        lambda: factory.authorized, session_id="synthetic-session")
    factory.contact = factory.call("contacts/list")["items"][0]["id"]
    factory.body = dict(recipient_id=factory.contact, paragraphs=["Approved synthetic message"],
                        request_id=str(uuid.uuid4()), consent=True)
    return factory


def test_real_runtime_facade_send_list_open_and_join(integrated):
    state = integrated
    assert state.call("status")["messaging"] == dict(available=True, reason="ready")
    result = state.call("messages/send", state.body)
    assert result["state"] == "queued" and not state.transmits
    row = state.rows[state.body["request_id"]]
    assert row["payload"]["body"]["state"] == "queued"
    assert state.call("messages/list", {"box": "outbox"})["items"][0]["text"] == state.body["paragraphs"][0]
    count = len(state.transmits)
    assert state.call("messages/open", {"id": result["id"], "share_read_receipt": False})["state"] == "queued"
    assert len(state.transmits) == count
    assert state.service.close(timeout=5) is True and state.released


def test_duplicate_send_never_retransmits(integrated):
    state = integrated
    state.call("messages/send", state.body)
    count = len(state.transmits)
    with pytest.raises(CommunityError):
        state.call("messages/send", state.body)
    assert len(state.transmits) == count
    assert state.service.close(timeout=5) is False


@pytest.mark.parametrize("change", ["lock", "auth", "activation"])
def test_revocation_on_serial_worker_denies_queue(integrated, monkeypatch, change):
    state = integrated
    original = state.runtime.queue_message
    def queue(context, **kwargs):
        if change == "lock":
            state.service.lock()
        elif change == "auth":
            state.authorized = False
        else:
            state.active = False
        return original(context, **kwargs)
    monkeypatch.setattr(state.runtime, "queue_message", queue)
    with pytest.raises(CommunityError):
        state.call("messages/send", state.body)
    assert state.body["request_id"] not in state.rows and not state.transmits


@pytest.mark.parametrize("change", ["content", "message_id", "radio_public_key", "consent_binding"])
def test_authority_rejects_changed_content_and_expires(integrated, monkeypatch, change):
    state = integrated
    original = state.runtime.queue_message
    captured = []
    def queue(context, **kwargs):
        captured.append(context)
        replacements = dict(content="Not the approved message", message_id=str(uuid.uuid4()),
                            radio_public_key=b"x" * 32, consent_binding="different-consent")
        return original(context, **{**kwargs, change: replacements[change]})
    monkeypatch.setattr(state.runtime, "queue_message", queue)
    with pytest.raises(CommunityError):
        state.call("messages/send", state.body)
    assert state.body["request_id"] not in state.rows and not state.transmits
    assert not state.provider._authority.authorized(captured[0], "queue_message", None)
    assert not state.provider._authority.authorized(replace(captured[0]), "queue_message", None)


def test_explicit_consent_enqueues_multifragment_bytes_without_transmitting(integrated):
    state = integrated
    body = {**state.body, "paragraphs": ["é" * 200, "Second paragraph"], "consent": False}
    with pytest.raises(CommunityError):
        state.call("messages/send", body)
    assert not state.transmits and body["request_id"] not in state.rows
    body["consent"] = True
    state.call("messages/send", body)
    assert not state.transmits
    assert state.call("messages/list", {"box": "outbox"})["items"][0]["text"] == "\n\n".join(body["paragraphs"])
    assert state.service.close(timeout=5) is True


def test_worker_timeout_revokes_queued_grant(integrated, monkeypatch):
    state = integrated
    entered, release = threading.Event(), threading.Event()
    state.runtime.config = replace(state.runtime.config, operation_timeout=0.02)
    original = state.runtime.queue_message
    futures = []
    def queue(context, **kwargs):
        futures.append(state.scheduler.submit(lambda device: (entered.set(), release.wait(2))))
        assert entered.wait(1)
        return original(context, **kwargs)
    monkeypatch.setattr(state.runtime, "queue_message", queue)
    try:
        with pytest.raises(CommunityError):
            state.call("messages/send", state.body)
    finally:
        release.set()
        for future in futures:
            future.result(2)
    assert entered.is_set()
    state.scheduler.submit(lambda device: None).result(2)
    assert state.body["request_id"] not in state.rows and not state.transmits


def test_no_read_receipt_or_background_authority(integrated):
    state = integrated
    access = MeshRequestAccess("a" * 32, lambda: state.authorized)
    with state.provider._authority.grant(state.runtime.actor, access, ("open_message",)) as context:
        for operation in ("receive", "contact_update", "queue_receipt", "retry_message", "dispatch_receipt"):
            assert state.provider._authority.authorized(context, operation, None) is False
        with pytest.raises(MeshRuntimeError):
            state.runtime.queue_receipt(context, local_id=str(uuid.uuid4()), kind="read",
                radio_public_key=state.radio, issued_at=1, expires_at=2)
    with pytest.raises(ValueError):
        CommunityMeshProvider.from_runtime(state.runtime, MeshActionAuthority())
    with pytest.raises(ValueError):
        CommunityMeshProvider(state.runtime.mailbox, state.runtime.registry,
            runtime_ready=state.runtime.ready, runtime_close=state.runtime.close)
    assert not state.transmits


def test_voice_controller_injection_auth_and_join(integrated, monkeypatch):
    path = Path(__file__).resolve().parents[3] / "scripts/lantern/unit-a-voice-loop.py"
    spec = importlib.util.spec_from_file_location("mesh_voice_controller", path)
    controller = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(controller)
    monkeypatch.setattr(controller, "lock_required", lambda: False)
    monkeypatch.setattr(controller, "token_authorized", lambda token: integrated.authorized and token == "synthetic-token")
    loop = controller.VoiceLoop(argparse.Namespace(max_seconds=30, drain_timeout=1),
                                mesh_provider=integrated.provider)
    service = loop.control.community
    check = loop.control.community_access("synthetic-token")
    assert service.dispatch("/community/status", {}, check, session_id="token-hash")["messaging"]["available"] is True
    loop.control.quiesce()
    with pytest.raises(CommunityError):
        service.dispatch("/community/status", {}, check, session_id="token-hash")
    assert service.close(timeout=5) is True and integrated.released


def test_old_generation_grant_cannot_revive_on_new_unlock(integrated, monkeypatch):
    captured = []
    original = integrated.runtime.ready
    def ready(context):
        captured.append(context)
        return original(context)
    monkeypatch.setattr(integrated.runtime, "ready", ready)
    integrated.call("status")
    integrated.service.lock()
    integrated.authorized = True
    integrated.call("status")
    assert captured[0].epoch != captured[1].epoch
    assert not integrated.provider._authority.authorized(captured[0], "status", None)


def test_http_queue_does_not_wait_for_radio_ack(integrated, monkeypatch):
    def lost_ack(**kwargs):
        raise TimeoutError("synthetic lost serial acknowledgment")
    monkeypatch.setattr(integrated.device, "send_text", lost_ack)
    assert integrated.call("messages/send", integrated.body)["state"] == "queued"
    assert integrated.rows[integrated.body["request_id"]]["payload"]["body"]["state"] == "queued"
    assert integrated.service.close(timeout=5) is True


def test_voice_ipc_facade_guard_challenge_without_local_factory(monkeypatch):
    from sage_lantern.mesh_runtime import MeshRuntime
    def forbidden(*args, **kwargs):
        raise AssertionError("voice must not construct a local radio factory")
    monkeypatch.setattr(MeshRuntime, "from_config", forbidden)
    allowed = [True]
    requests = []
    class Connection:
        def __init__(self):
            self.raw = bytearray()
        def settimeout(self, timeout):
            assert 0 < timeout <= 60
        def reply(self, value):
            encoded = json.dumps(value).encode()
            self.raw.extend(len(encoded).to_bytes(4, "big") + encoded)
        def sendall(self, raw):
            value = json.loads(raw[4:])
            requests.append(value)
            if value.get("op") == "bind":
                self.reply({"broker_id": "a" * 32, "actor": {"chain_id": "local", "agent_id": "d" * 64}})
            elif value.get("method") == "ready":
                self.reply({"guard": "c" * 32})
            elif "guard" in value:
                self.reply({"result": value["allowed"]})
            else:
                assert value["method"] == "close"
                self.reply({"result": True})
        def recv(self, count):
            result = bytes(self.raw[:count])
            del self.raw[:count]
            return result
        def close(self):
            pass
    facade = MeshVoiceFacade(connector=Connection, epoch_reader=lambda: "b" * 32)
    service = CommunityService(mesh_provider=facade)
    result = service.dispatch("/community/status", {}, lambda: allowed[0], session_id="synthetic-session")
    assert result["messaging"] == {"available": True, "reason": "ready"}
    assert {"guard": "c" * 32, "allowed": True} in requests
    allowed[0] = False
    count = len(requests)
    with pytest.raises(CommunityError):
        service.dispatch("/community/status", {}, lambda: allowed[0], session_id="synthetic-session")
    assert len(requests) == count
    assert service.close(timeout=5) is True
