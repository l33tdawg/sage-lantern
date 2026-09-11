"""Root IPC + real scheduler/protected SDK; serial and gateway IO are synthetic."""

from dataclasses import asdict
from datetime import datetime, timezone
import copy
import hashlib
import os
import struct
import time
import uuid

import pytest

from sage_lantern import mesh_daemon as module
from sage_lantern.mesh_community import MeshRequestAccess
from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.mesh_voice_ipc import MeshVoiceFacade
from sage_lantern.mesh_public_root import observation, progress
from sage_lantern.public_mesh_provider import public_fragments
from test_mesh_daemon import runtime, factory, socket_connector
from test_meshcore_transport import SerialDouble, frame
from test_public_mesh_protocol import IDENTITY, GATEWAY_KEY, proposal
from test_public_gateway_bridge import fixture


def prime_contact(root, runtime, authority):
    from sage_lantern.gateway_contact import _wire, respond_contact, fragments
    from sage_lantern.meshcore_transport import decode_incoming
    from test_public_mesh_protocol import GATEWAY
    access = MeshRequestAccess("a" * 32, lambda: True)
    with authority.grant(runtime.actor, access, ("public_begin",)) as context:
        def bootstrap(device):
            probe = root.probe
            probe.begin(access_check=runtime._access)
            stream = device._stream
            original = stream.write
            def write(data, timeout):
                command = data[3:]
                if command[:1] == b"\x02" and command[13:].startswith(b"LC1:"):
                    assert probe._get(runtime._access)["payload"]["body"]["data"]["state"] == "dispatching"
                    ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + device._key).digest()[:4]
                    stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
                    return SerialDouble.write(stream, data, timeout)
                return original(data, timeout)
            stream.write = write
            try:
                for _ in range(86):
                    if not probe.dispatch_one(device=device, access_check=runtime._access):
                        break
            finally:
                stream.write = original
            body = probe._get(runtime._access)["payload"]["body"]["data"]["probe"]
            wire = _wire(body, probe.identity._signing_key)
            reply = respond_contact(wire, GATEWAY, pins=probe.pins, author=probe.identity.agent_id,
                allowed_authors=frozenset({probe.identity.agent_id}), now=int(time.time()))
            for text in fragments(reply):
                raw = b"\x07" + probe.pins.radio_key[:6] + bytes(2) + struct.pack("<I", int(time.time())) + text.encode()
                probe.ingest_frame(decode_incoming(raw), access_check=runtime._access)
            assert probe.current(probe.pins)
        runtime._run(context, "public_begin", bootstrap)


@pytest.fixture
def public_runtime(runtime, monkeypatch, request):
    state, supervisor, resource, config, opened = runtime
    config["radio"]["public_key"] = state.device._key.hex()
    config["public_mesh"] = dict(identity_file="/var/lib/sage-lantern/identity/public.key", author=IDENTITY.agent_id,
        chain_id="commons", domain="public-domain", gateway_key=GATEWAY_KEY, radio_key=state.radio.hex(),
        storage_actor=state.identity.agent_id, storage_chain="local",
        timing=dict(frame_seconds=0.1, gateway_seconds=30, safety_seconds=10))
    module.validate_config(config)
    def load(configured):
        assert configured["agent_id"] == IDENTITY.agent_id and configured["identity_file"].endswith("/public.key")
        return IDENTITY, os.open(os.devnull, os.O_RDONLY)
    monkeypatch.setattr(module, "load_identity", load)
    resource._public_contact_check = lambda pins: state.binding and pins.radio_key == state.radio
    state.public_access = True
    epoch = lambda: "e" * 32 if state.public_access else None
    resource._epoch = epoch
    connector, workers = socket_connector(module.MeshDaemon(supervisor, resource, epoch_reader=epoch))
    facade = MeshVoiceFacade(connector=connector, epoch_reader=epoch)
    assert facade.ready(access_check=lambda: state.public_access) is True
    if request.node.originalname not in {"test_real_contact_probe_scheduler_capture_and_drain", "test_real_ready_arms_probe_without_contact_default_true"}:
        prime_contact(resource._public, resource._provider._runtime, resource._provider._authority)
        state.transmits.clear()
    yield state, resource, facade
    for worker in workers:
        worker.join(timeout=3)
        assert not worker.is_alive()


def approve(resource, item):
    provider = resource._provider
    access = MeshRequestAccess("a" * 32, lambda: True)
    with provider._grant(access, {"public_begin"}) as context:
        provider._runtime._run(context, "public_begin", lambda device:
            resource._public.journal.prepare_public_source(item,
                datetime.now(timezone.utc).isoformat(), str(uuid.uuid4())))


@pytest.mark.parametrize("field,value", [("author", "private"), ("storage_actor", "wrong"),
    ("storage_chain", "wrong"), ("gateway_key", "00" * 32), ("identity_file", "/etc/shadow"),
    ("timing", {"frame_seconds": True, "gateway_seconds": 30, "safety_seconds": 10}),
    ("domain", "public\n"), ("extra", True)])
def test_config_binding_rejected(public_runtime, field, value):
    state, resource, facade = public_runtime
    config = copy.deepcopy(resource.config)
    config["public_mesh"][field] = state.identity.agent_id if value == "private" else value
    with pytest.raises((ValueError, RuntimeError)):
        module.validate_config(config)


@pytest.mark.parametrize("failure", ["missing_approval", "missing_contact", "bad_contact_bool", "cancelled", "unsigned", "lock", "timing"])
def test_no_intent_or_send_on_missing_authority(public_runtime, failure):
    state, resource, facade = public_runtime
    item = proposal()
    if failure != "missing_approval":
        approve(resource, item)
    if failure == "missing_contact":
        resource._public.contact_check = None
        resource._public.probe.close()
    elif failure == "bad_contact_bool":
        resource._public.contact_check = lambda pins: 1
    elif failure in {"cancelled", "unsigned"}:
        payload = next(row["payload"] for row in state.rows.values() if row["payload"].get("operation_id") == item.operation_id)
        if failure == "cancelled":
            payload["result"] = {"cancelled": True}
        else:
            payload.pop("source_proof")
    elif failure == "lock":
        state.public_access = False
    elif failure == "timing":
        from sage_lantern.public_mesh_provider import PublicMeshTiming
        resource._provider._runtime._public_mesh.timing = PublicMeshTiming(30)
    before = copy.deepcopy(state.rows)
    with pytest.raises((ValueError, RuntimeError)):
        facade.public_begin(item, request_id=str(uuid.uuid4()), access_check=lambda: state.public_access, on_submit=lambda: None)
    assert state.rows == before and not state.transmits


def test_public_disabled_before_open(runtime):
    state, supervisor, resource, config, opened = runtime
    with pytest.raises(RuntimeError):
        resource.invoke("public_inspect", {"request_id": str(uuid.uuid4())}, lambda: True)
    assert opened == []


@pytest.mark.parametrize("flag", ["acceptance_available", "consensus_verified", "cache_eligible"])
@pytest.mark.parametrize("value", [True, 0])
def test_false_acceptance_flags_are_exact(flag, value):
    from sage_lantern.public_mesh_protocol import result_data
    response = result_data("unknown")
    response[flag] = value
    with pytest.raises(ValueError):
        observation(response)


def test_real_background_scheduling_through_ipc(public_runtime, fixture, monkeypatch):
    from sage_lantern.community_public_mesh import CommunityPublicMeshFacade
    from sage_lantern.community_service import CommunityService
    state, resource, facade = public_runtime
    sharing = CommunityPublicMeshFacade(facade)
    item = sharing.prepare(operation_id=str(uuid.uuid4()), question="Synthetic public question?",
        answer="Reviewed public answer.", access_check=lambda: True)
    stream = state.device._stream
    original = stream.write
    assembler = FragmentAssembler()
    sent_at = []
    bridge = fixture.bridge()
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LP1:"):
            time.sleep(0.1)
            sent_at.append(time.monotonic())
            wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            if wire is not None:
                fixture.now = int(time.time())
                response = bridge.handle(wire, author=IDENTITY.agent_id)
                for fragment in public_fragments(response):
                    state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", fixture.now) + fragment.encode())
            return SerialDouble.write(stream, data, timeout)
        if command == b"\x0a" and state.pending:
            time.sleep(0.1)
        return original(data, timeout)
    monkeypatch.setattr(stream, "write", write)
    request_id = str(uuid.uuid4())
    queued = sharing.queue(item, source_id=str(uuid.uuid4()), created_at=datetime.now(timezone.utc).isoformat(),
        request_id=request_id, approval_binding="b" * 64, access_check=lambda: True, on_submit=lambda: None)
    begun, _ = facade.public_inspect(request_id, access_check=lambda: True)
    assert begun.state == "queued" and not sent_at
    resource.config["messaging_enabled"] = True
    resource.config["dispatch_enabled"] = True
    resource.resume_background(lambda: True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        state_result, result = facade.public_inspect(request_id, access_check=lambda: True)
        if state_result.state == "consumed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("real scheduler did not finish bounded synthetic public roundtrip")
    assert len(sent_at) == begun.fragment_count
    assert all(later - earlier >= 0.09 for earlier, later in zip(sent_at, sent_at[1:]))
    assert result["state"] == "receipt" and result["trust_basis"] == "pinned_gateway"
    assert all(result[key] is False for key in ("acceptance_available", "consensus_verified", "cache_eligible"))
    assert resource._provider.pause_receiving(timeout=5) is True
    posts = list(fixture.calls)
    poll_id = str(uuid.uuid4())
    service = CommunityService(mesh_provider=facade)
    polling = service.dispatch("/community/shares/refresh", dict(id=queued["id"], request_id=poll_id, consent=True),
        lambda: True, session_id="fresh-owner-session")
    assert polling["state"] == "queued" and fixture.calls == posts
    resource.resume_background(lambda: True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        status, observed = sharing.inspect(item, request_id=poll_id, access_check=lambda: True)
        if status.state == "consumed":
            break
        time.sleep(0.05)
    else:
        pytest.fail("explicit signed status poll did not complete")
    assert observed["state"] == "observed" and observed["observed_status"] == "committed"
    assert observed["receipt"] == result["receipt"]
    service.lock()
    service = CommunityService(mesh_provider=facade)
    refreshed = service.dispatch("/community/shares/inspect", dict(id=queued["id"], request_id=poll_id),
        lambda: True, session_id="another-owner-session")
    assert refreshed["state"] == "pending" and refreshed["observed_status"] == "committed"
    assert all(refreshed[key] is False for key in ("acceptance_available", "consensus_verified", "cache_eligible"))
    assert all(observed[key] is False for key in ("acceptance_available", "consensus_verified", "cache_eligible"))
    assert [call for call in fixture.calls if call[0] == "POST"] == [call for call in posts if call[0] == "POST"]
    assert ("GET", "/v1/memory/" + result["receipt"]["memory_id"]) in fixture.calls
    assert resource._provider.pause_receiving(timeout=5) is True
    before = len(sent_at)
    with pytest.raises(RuntimeError):
        facade.public_begin(item, request_id=str(uuid.uuid4()), access_check=lambda: True, on_submit=lambda: None)
    assert len(sent_at) == before


def test_real_contact_probe_scheduler_capture_and_drain(public_runtime, monkeypatch):
    from sage_lantern.community_public_mesh import CommunityPublicMeshFacade
    from sage_lantern.gateway_contact import respond_contact, fragments
    from test_public_mesh_protocol import GATEWAY
    state, resource, transport = public_runtime
    resource._public.contact_check = None
    facade = CommunityPublicMeshFacade(transport)
    stream = state.device._stream
    original = stream.write
    assembler = FragmentAssembler()
    sent = []
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LC1:"):
            time.sleep(0.1)
            sent.append(time.monotonic())
            wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            if wire is not None:
                reply = respond_contact(wire, GATEWAY, pins=resource._public.probe.pins,
                    author=IDENTITY.agent_id, allowed_authors=frozenset({IDENTITY.agent_id}), now=int(time.time()))
                for text in fragments(reply):
                    state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", int(time.time())) + text.encode())
            return SerialDouble.write(stream, data, timeout)
        if command == b"\x0a" and state.pending:
            time.sleep(0.1)
        return original(data, timeout)
    monkeypatch.setattr(stream, "write", write)
    assert facade.readiness(access_check=lambda: True) == "transport_unavailable"
    assert not sent
    resource.config.update(messaging_enabled=True, dispatch_enabled=True)
    resource.resume_background(lambda: True)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        if facade.readiness(access_check=lambda: True) == "ready":
            break
        time.sleep(0.1)
    else:
        pytest.fail("scheduled signed contact probe did not complete")
    assert sent and all(later - earlier >= 0.09 for earlier, later in zip(sent, sent[1:]))
    assert resource._provider.pause_receiving(timeout=5) is True
    resource.supervisor.phase = "draining"
    assert resource.close(5) is True


def test_five_facade_routes_use_actual_root_ipc(public_runtime):
    from sage_lantern.community_public_mesh import CommunityPublicMeshFacade
    state, resource, transport = public_runtime
    facade = CommunityPublicMeshFacade(transport)
    assert facade.readiness(access_check=lambda: True) == "ready"
    item = facade.prepare(operation_id=str(uuid.uuid4()), question="Synthetic question?", answer="Approved derivative.", access_check=lambda: True)
    source = str(uuid.uuid4())
    created = datetime.now(timezone.utc).isoformat()
    request_id = str(uuid.uuid4())
    result = facade.queue(item, source_id=source, created_at=created, request_id=request_id,
        approval_binding="b" * 64, access_check=lambda: True, on_submit=lambda: None)
    assert result["state"] == "queued" and not state.transmits
    before = copy.deepcopy(state.rows)
    assert facade.queue(item, source_id=source, created_at=created, request_id=request_id,
        approval_binding="b" * 64, access_check=lambda: True, on_submit=lambda: None) == result
    assert state.rows == before
    page = facade.list(access_check=lambda: True)
    assert page["items"] == [dict(result, question="Synthetic question?")]
    assert facade.sources([source], access_check=lambda: True) == {"items": [{"source_id": source, "state": "queued"}]}
    fresh = CommunityPublicMeshFacade(transport)
    assert fresh.resolve(result["id"], access_check=lambda: True) == (item, request_id, created)


@pytest.mark.parametrize("answer", ["x" * 8192, "界" * 2730, "😀" * 2048])
def test_public_list_metadata_stays_inside_ipc_frame(answer):
    from sage_lantern.community_public import CommunityPublic
    from sage_lantern.mesh_public_root import RootPublicMesh, encoded
    root = object.__new__(RootPublicMesh)
    proposal = CommunityPublic(None, None, chain_id="commons", domain="public-domain", author=IDENTITY.agent_id)
    rows = [dict(id="mesh-public:" + str(uuid.uuid4()), state="pending", created_at="2026-09-08T00:00:00Z",
        question="q" * 4096, proposal=asdict(proposal.prepare(operation_id=str(uuid.uuid4()),
            question="q" * 4096, answer=answer))) for _ in range(20)]
    root._entries = lambda: rows
    root._summary = lambda body, question: body
    page = root._facade("public_list", {}, None)
    assert 0 < len(page["items"]) < 20 and page["more"] is True
    assert len(encoded(page)) <= 48000


def test_real_ready_arms_probe_without_contact_default_true(public_runtime):
    from sage_lantern.community_public_mesh import CommunityPublicMeshFacade
    state, resource, transport = public_runtime
    resource._public.contact_check = None
    facade = CommunityPublicMeshFacade(transport)
    assert facade.readiness(access_check=lambda: True) == "transport_unavailable"
    assert resource._public.probe.active() is True
    assert resource._public.probe.current(resource._public.pins) is False
    before = copy.deepcopy(state.rows)
    assert facade.readiness(access_check=lambda: True) == "transport_unavailable"
    assert state.rows == before and not state.transmits
