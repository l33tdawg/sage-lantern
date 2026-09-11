"""Synthetic serial/HTTP only; actual SDK proofs, protected CAS and gateway codec."""

import copy
import hashlib
import json
import struct
import time
from types import SimpleNamespace
import uuid

import httpx
from nacl.signing import SigningKey, VerifyKey
import pytest
from sage_sdk import AgentIdentity, SageClient

from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.mesh_envelope import Peer
from sage_lantern.meshcore_transport import MeshCoreClient, decode_incoming
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.public_mesh_provider import PublicMeshPins, PublicMeshProvider, PublicMeshTiming, public_fragments, PUBLIC_BURST
from sage_lantern import public_mesh_protocol as protocol
from sage_lantern.workflow_store import WorkflowStore
from test_meshcore_transport import SerialDouble, frame, handshake_bytes
from test_meshcore_contacts import start, contact, end
from test_public_gateway_bridge import fixture
from test_public_mesh_protocol import IDENTITY, GATEWAY, GATEWAY_KEY, NOW, proposal
from test_mesh_runtime import factory
from test_community_mesh_runtime import integrated
from test_mesh_background import background, wait_until
from sage_lantern.mesh_community import MeshRequestAccess


RADIO = bytes(SigningKey(b"p" * 32).verify_key)
LOCAL_RADIO = bytes(SigningKey(b"l" * 32).verify_key)


@pytest.fixture
def lantern():
    state = SimpleNamespace(rows={}, access=True, approved=True, contact=True, now=NOW, fail_put=False,
                            transmits=[], puts=0, lose_ack=False)
    identity = AgentIdentity(SigningKey(b"n" * 32))
    endpoint = "http://127.0.0.1:19102"
    def handler(request):
        headers = {key: request.headers[key] for key in ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}
        protocol.verify_http(headers, identity.agent_id, request.method, request.url.raw_path.decode(),
                             request.content, int(headers["X-Timestamp"]))
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", agent_id=identity.agent_id,
                instance_id="f" * 64, vault_generation="1", encryption_expected=True, vault_active=True,
                stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            limit = int(request.url.params.get("limit", "20"))
            after = request.url.params.get("after", "")
            rows = [row for key, row in sorted(state.rows.items()) if key > after]
            more = len(rows) > limit
            rows = rows[:limit]
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=rows,
                next_after=rows[-1]["record_id"] if more else None, has_more=more))
        identifier = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            return httpx.Response(200 if identifier in state.rows else 404, json=state.rows.get(identifier, {}))
        body = json.loads(request.content)
        revision = state.rows.get(identifier, {}).get("revision", 0)
        if revision != body["expected_revision"]:
            return httpx.Response(409, json={})
        row = dict(schema="sage.workflow-journal.v1", record_id=identifier, agent_id=identity.agent_id,
            revision=revision + 1, kind=body["kind"], payload=copy.deepcopy(body["payload"]), trust="untrusted_auxiliary")
        state.rows[identifier] = row
        state.puts += 1
        if state.fail_put:
            raise httpx.ReadTimeout("synthetic lost CAS reply")
        return httpx.Response(200, json=row)
    client = SageClient(endpoint, identity, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, trust_env=False, transport=httpx.MockTransport(handler))
    state.store = WorkflowStore(client, storage=MessageStorageProbe(client,
        actor=RecipientID("private-lantern", identity.agent_id), endpoint=endpoint))
    state.item = proposal()
    state.pins = PublicMeshPins(GATEWAY_KEY, RADIO, "commons", "public-domain")
    state.make = lambda: PublicMeshProvider(state.store, IDENTITY, state.pins,
        approval_check=lambda item: state.approved and item == state.item,
        contact_check=lambda pins: state.contact and pins == state.pins, clock=lambda: state.now,
        timing=PublicMeshTiming(0.1))
    state.provider = state.make()
    stream = SerialDouble(handshake_bytes(key=LOCAL_RADIO), chunk=10000)
    state.device = MeshCoreClient(stream, expected_public_key=LOCAL_RADIO)
    state.device.handshake()
    original = stream.write
    def write(data, timeout):
        command = data[3:]
        if command == b"\x04":
            stream.incoming.extend(frame(start(1)) + frame(contact(RADIO)) + frame(end(123)))
        elif command[:1] == b"\x02":
            assert any(row["payload"]["body"]["data"].get("state") == "dispatching" for row in state.rows.values())
            state.transmits.append(command)
            if state.lose_ack:
                raise TimeoutError("synthetic unknown serial outcome")
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + LOCAL_RADIO).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
        return original(data, timeout)
    stream.write = write
    state.check = lambda: state.access
    state.begin = lambda **kwargs: state.provider.begin(state.item, request_id=kwargs.pop("request_id", str(uuid.uuid4())),
        device=state.device, access_check=state.check, **kwargs)
    yield state
    state.provider.close()
    client._client.close()


def dispatch(lantern):
    progress = lantern.begin()
    for _ in range(progress.fragment_count):
        progress = lantern.provider.dispatch_one(progress.request_id, device=lantern.device, access_check=lantern.check)
    assembler = FragmentAssembler()
    wire = None
    for command in lantern.transmits:
        assert command[13:].startswith(b"LP1:")
        wire = assembler.accept(LOCAL_RADIO, "SL1:" + command[13:].decode()[4:])
    assert wire is not None and progress.state == "awaiting_response"
    return progress, wire


def test_shared_response_bound_drives_admission(lantern, monkeypatch):
    import sage_lantern.public_mesh_provider as provider_module
    calls = []
    def bound(item, *, gateway_key):
        calls.append((item, gateway_key))
        return 4096
    monkeypatch.setattr(provider_module, "response_fragment_bound", bound)
    before = copy.deepcopy(lantern.rows)
    with pytest.raises(protocol.PublicMeshError):
        lantern.begin()
    assert calls == [(lantern.item, lantern.pins.gateway_key)]
    assert lantern.rows == before and not lantern.transmits


def test_real_gateway_roundtrip_durable_nonce_restart_no_acceptance(lantern, fixture):
    progress, wire = dispatch(lantern)
    raw = protocol.open_request(wire, GATEWAY, author=IDENTITY.agent_id, chain_id="commons", domain="public-domain", now=NOW)
    assert raw["proposal"]["content"] == lantern.item.content
    response = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
    stored = json.dumps(lantern.rows)
    assert "profile_auth" not in stored and "X-Nonce" not in stored and wire.decode() not in stored
    assert lantern.provider.close() is True
    lantern.provider = lantern.make()
    result = None
    for text in public_fragments(response):
        message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
        result = lantern.provider.ingest_frame(message, access_check=lantern.check)
    assert result["state"] == "receipt" and result["trust_basis"] == "pinned_gateway"
    assert all(result[key] is False for key in protocol.FLAGS)
    assert lantern.provider.receive(response, access_check=lantern.check) is None
    assert lantern.provider.close() is True
    lantern.provider = lantern.make()
    assert lantern.provider.receive(response, access_check=lantern.check) is None
    assert lantern.provider.inspect(progress.request_id, access_check=lantern.check)[1] == result
    assert lantern.provider.drain(access_check=lantern.check) is True
    assert sum(method == "POST" for method, _ in fixture.calls) == 1


def test_restart_cannot_regenerate_or_resend_http_packet(lantern):
    progress = lantern.begin()
    lantern.provider.close()
    lantern.provider = lantern.make()
    assert lantern.provider.inspect(progress.request_id, access_check=lantern.check)[0].state == "unknown"
    for call in (lambda: lantern.begin(), lambda: lantern.begin(request_id=progress.request_id),
                 lambda: lantern.provider.dispatch_one(progress.request_id, device=lantern.device, access_check=lantern.check)):
        with pytest.raises(protocol.PublicMeshError):
            call()
    assert not lantern.transmits


@pytest.mark.parametrize("gate", ["approved", "access", "contact"])
def test_missing_exact_gate_never_admits(lantern, gate):
    setattr(lantern, gate, False)
    with pytest.raises(protocol.PublicMeshError):
        lantern.begin()
    assert not lantern.rows and not lantern.transmits


def test_unknown_dispatch_survives_restart_without_retry(lantern):
    progress = lantern.begin()
    lantern.lose_ack = True
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.dispatch_one(progress.request_id, device=lantern.device, access_check=lantern.check)
    assert lantern.provider.close() is False
    lantern.provider = lantern.make()
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.dispatch_one(progress.request_id, device=lantern.device, access_check=lantern.check)
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.drain(access_check=lantern.check)
    assert len(lantern.transmits) == 1


def test_signed_metadata_tampering_denied(lantern):
    progress = lantern.begin()
    lantern.rows[progress.request_id]["payload"]["body"]["data"]["digest"] = "0" * 64
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.inspect(progress.request_id, access_check=lantern.check)
    assert not lantern.transmits


def test_response_cas_uncertainty_never_exposes_or_reconsumes(lantern, fixture):
    progress, wire = dispatch(lantern)
    response = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
    lantern.fail_put = True
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.receive(response, access_check=lantern.check)
    assert lantern.provider.close() is False
    lantern.fail_put = False
    lantern.provider = lantern.make()
    assert lantern.provider.receive(response, access_check=lantern.check) is None
    assert lantern.provider.inspect(progress.request_id, access_check=lantern.check)[0].state == "consumed"


@pytest.mark.parametrize("change", ["nonce", "digest", "gateway", "flags", "expired"])
def test_wrong_response_never_consumes(lantern, change):
    progress, wire = dispatch(lantern)
    request = protocol.open_request(wire, GATEWAY, author=IDENTITY.agent_id,
        chain_id="commons", domain="public-domain", now=NOW)
    result = protocol.result_data("unknown")
    if change == "flags":
        result["cache_eligible"] = True
    response = protocol.seal_result(request, result, GATEWAY, now=NOW)
    if change in {"nonce", "digest", "gateway"}:
        payload = protocol._open(response, IDENTITY._signing_key, GATEWAY_KEY, IDENTITY.agent_id, NOW)
        if change == "nonce":
            payload["request_id"] = str(uuid.uuid4())
        elif change == "digest":
            payload["request_digest"] = "0" * 64
        key = SigningKey(b"w" * 32) if change == "gateway" else GATEWAY
        response = protocol._seal(payload, key, IDENTITY.agent_id)
    if change == "expired":
        lantern.now += 120
    before = copy.deepcopy(lantern.rows)
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.receive(response, access_check=lantern.check)
    assert lantern.rows == before
    assert lantern.provider.inspect(progress.request_id, access_check=lantern.check)[1] is None


def test_partial_response_survives_provider_recreation(lantern, fixture):
    progress, wire = dispatch(lantern)
    response = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
    fragments = public_fragments(response)
    def message(text):
        return decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
    assert lantern.provider.ingest_frame(message(fragments[0]), access_check=lantern.check) is None
    lantern.provider.close()
    lantern.provider = lantern.make()
    for text in fragments[1:]:
        result = lantern.provider.ingest_frame(message(text), access_check=lantern.check)
    assert result["state"] == "receipt"
    assert lantern.provider.inspect(progress.request_id, access_check=lantern.check)[0].state == "consumed"


def test_runtime_routes_public_without_private_chat_ingest(factory, fixture, monkeypatch):
    state = factory
    state.adopt()
    private_id = str(uuid.uuid4())
    now = int(time.time())
    state.runtime.queue_message(state.context, message_id=private_id,
        recipient=Peer("remote", bytes.fromhex(state.remote.agent_id)), radio_public_key=state.radio,
        content="Private message remains queued", consent_binding="contact-policy", issued_at=now, expires_at=now + 900)
    item = proposal()
    pins = PublicMeshPins(GATEWAY_KEY, state.radio, "commons", "public-domain")
    provider = PublicMeshProvider(state.runtime.store, IDENTITY, pins, approval_check=lambda value: value == item,
        contact_check=lambda value: state.binding and value == pins, clock=lambda: NOW, timing=PublicMeshTiming(0.1))
    state.runtime.attach_public_mesh(provider)
    transmitted = []
    stream = state.device._stream
    original_write = stream.write
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LP1:"):
            assert any(row["payload"].get("body", {}).get("data", {}).get("state") == "dispatching"
                       for row in state.rows.values())
            transmitted.append(command)
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            return SerialDouble.write(stream, data, timeout)
        return original_write(data, timeout)
    monkeypatch.setattr(stream, "write", write)
    progress = state.runtime.public_begin(state.context, item, request_id=str(uuid.uuid4()))
    for _ in range((progress.fragment_count + PUBLIC_BURST - 1) // PUBLIC_BURST):
        state.runtime._run(state.context, "background_dispatch", state.runtime._dispatch_pending)
    with monkeypatch.context() as guarded:
        guarded.setattr(state.runtime.store, "scan", lambda **kwargs: pytest.fail("private inventory scan during public response wait"))
        state.runtime._run(state.context, "background_dispatch", state.runtime._dispatch_pending)
    assert state.rows[private_id]["payload"]["body"]["state"] == "queued"
    assert state.rows[private_id]["payload"]["body"]["next_fragment"] == 0
    assembler = FragmentAssembler()
    for command in transmitted:
        wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
    response = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
    monkeypatch.setattr(state.runtime.inbox, "ingest", lambda *args: pytest.fail("public frame entered private chat"))
    for fragment in public_fragments(response):
        state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", NOW) + fragment.encode())
        assert state.runtime.poll_once(state.context) == "captured"
    final, result = state.runtime.public_inspect(state.context, progress.request_id)
    assert final.state == "consumed" and result["state"] == "receipt"
    assert all(result[key] is False for key in protocol.FLAGS)
    assert not state.transmits and state.runtime.close(timeout=5) is True


@pytest.mark.parametrize("change", ["missing_timing", "too_slow", "oversize"])
def test_admission_budget_and_complete_content_limit_precede_reservation(lantern, change):
    if change == "missing_timing":
        lantern.provider.timing = None
    elif change == "too_slow":
        lantern.provider.timing = PublicMeshTiming(30)
    else:
        lantern.item = proposal("x" * 1100)
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.preflight(lantern.item)
    with pytest.raises(protocol.PublicMeshError):
        lantern.begin()
    assert not lantern.rows and not lantern.transmits


def test_response_slots_allocate_free_space_despite_old_hash_collision(lantern):
    buckets = {}
    pair = None
    for index in range(100):
        wire = b"LPM1." + bytes([index]) * 150
        bucket = int.from_bytes(hashlib.sha256(wire).digest()[:2], "big") % 8
        if bucket in buckets:
            pair = buckets[bucket], wire
            break
        buckets[bucket] = wire
    assert pair is not None
    for wire in pair:
        text = public_fragments(wire)[0]
        message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
        assert lantern.provider.ingest_frame(message, access_check=lantern.check) is None
    rows = list(lantern.rows.values())
    assert len(rows) == 2
    assert {row["payload"]["body"]["data"]["digest"] for row in rows} == {hashlib.sha256(wire).hexdigest() for wire in pair}
    before = copy.deepcopy(lantern.rows)
    lantern.now += 121
    wire = b"LPM1." + b"new" * 100
    text = public_fragments(wire)[0]
    message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
    lantern.provider.ingest_frame(message, access_check=lantern.check)
    assert len(lantern.rows) == 2
    changed = [identifier for identifier in before if before[identifier] != lantern.rows[identifier]]
    assert len(changed) == 1 and lantern.rows[changed[0]]["revision"] == before[changed[0]]["revision"] + 1


def test_full_response_slots_never_evict_live_assemblies(lantern):
    for index in range(8):
        text = public_fragments(b"LPM1." + bytes([index]) * 150)[0]
        message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
        lantern.provider.ingest_frame(message, access_check=lantern.check)
    before = copy.deepcopy(lantern.rows)
    text = public_fragments(b"LPM1." + b"ninth" * 40)[0]
    message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
    with pytest.raises(protocol.PublicMeshError):
        lantern.provider.ingest_frame(message, access_check=lantern.check)
    assert len(before) == 8 and lantern.rows == before


@pytest.mark.parametrize("answer", ["x", "x" * 500])
def test_default_interval_public_worker_completes_live_window(background, fixture, monkeypatch, answer):
    state = background
    item = proposal(answer)
    pins = PublicMeshPins(GATEWAY_KEY, state.radio, "commons", "public-domain")
    started = time.monotonic()
    clock = lambda: NOW + int(time.monotonic() - started)
    provider = PublicMeshProvider(state.runtime.store, IDENTITY, pins, approval_check=lambda value: value == item,
        contact_check=lambda value: state.binding and value == pins, clock=clock, timing=PublicMeshTiming(0.1))
    state.runtime.attach_public_mesh(provider)
    stream = state.device._stream
    original_write = stream.write
    assembler = FragmentAssembler()
    counts = dict(request=0, response=0)
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LP1:"):
            counts["request"] += 1
            wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            if wire is not None:
                fixture.now = clock()
                response = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
                fragments = public_fragments(response)
                counts["response"] = len(fragments)
                assert len(fragments) <= provider._response_count(item)
                for fragment in fragments:
                    state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", fixture.now) + fragment.encode())
            return SerialDouble.write(stream, data, timeout)
        return original_write(data, timeout)
    monkeypatch.setattr(stream, "write", write)
    request_id = str(uuid.uuid4())
    with state.provider._authority.grant(state.runtime.actor,
            MeshRequestAccess("a" * 32, lambda: state.authorized), ("public_begin",)) as context:
        progress = state.runtime.public_begin(context, item, request_id=request_id)
    state.stop_after = lambda: state.rows[request_id]["payload"]["body"]["data"]["state"] == "consumed"
    state.provider.start_receiving(consent_reader=lambda: state.consent,
        lease_factory=state.lease_factory, unlocked_check=lambda: state.authorized, interval=1)
    wait_until(lambda: state.runtime._receive_stop.is_set(), timeout=20)
    assert state.provider.pause_receiving(timeout=5) is True
    final = state.rows[request_id]["payload"]["body"]["data"]
    assert final["state"] == "consumed" and clock() < final["expires_at"]
    assert counts["request"] == progress.fragment_count and counts["response"] > 1
    assert all(final["result"][key] is False for key in protocol.FLAGS)
    assert sum(method == "POST" for method, _ in fixture.calls) == 1
    assert not state.transmits and state.service.close(timeout=5) is True


@pytest.mark.parametrize("enabled", [False, True])
def test_malformed_public_frame_uses_existing_rejection_path(factory, enabled):
    state = factory
    state.adopt()
    if enabled:
        pins = PublicMeshPins(GATEWAY_KEY, state.radio, "commons", "public-domain")
        state.runtime.attach_public_mesh(PublicMeshProvider(state.runtime.store, IDENTITY, pins,
            approval_check=lambda item: False, contact_check=lambda value: state.binding and value == pins,
            clock=lambda: NOW))
    state.pending.append(b"\x07" + state.radio[:6] + bytes(2) + struct.pack("<I", NOW) + b"LP1:invalid")
    assert state.runtime.poll_once(state.context) == "captured"
    assert state.runtime.poll_once(state.context) == "idle"
    assert not state.transmits and state.runtime.close(timeout=5) is True
