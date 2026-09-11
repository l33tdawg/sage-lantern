"""Synthetic radio/REST; real serial owner, SDK, signatures and protected CAS."""

import copy
import hashlib
import json
import math
import struct
import threading
import uuid
from collections import deque
from dataclasses import replace

import httpx
from nacl.signing import SigningKey
import pytest

from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.mesh_serial_owner import SerialScheduler
from sage_lantern.mesh_receiver import MeshReceiver
from sage_lantern.meshcore_transport import MeshCoreClient, decode_incoming
from sage_lantern.public_gateway_bridge import LocalPublicREST, PublicGatewayBridge
from sage_lantern.public_gateway_runtime import PublicGatewayPin, PublicGatewayRuntime, SCHEMA
from sage_lantern.public_mesh_provider import PublicMeshTiming, public_fragments
from sage_lantern import public_mesh_protocol as protocol
from test_public_gateway_bridge import fixture, PUBLIC_ENDPOINT, MEMORY_ID
from test_public_mesh_protocol import IDENTITY, GATEWAY, NOW, request, proposal
from test_meshcore_transport import SerialDouble, handshake_bytes, frame
from test_meshcore_contacts import start, contact, end


RADIO = bytes(SigningKey(b"p" * 32).verify_key)
GATEWAY_RADIO = bytes(SigningKey(b"g" * 32).verify_key)


@pytest.fixture
def gateway(fixture):
    state = fixture
    state.contact = True
    state.operation_allowed = True
    state.radio_keys = [RADIO]
    state.transmits, state.threads = [], []
    state.incoming = deque()
    state.lose_ack = state.lock_after_send = False
    state.released = False
    def rest_handler(incoming):
        headers = {key: incoming.headers[key] for key in ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}
        protocol.verify_http(headers, IDENTITY.agent_id, incoming.method, incoming.url.path,
                             incoming.content, int(headers["X-Timestamp"]))
        state.calls.append((incoming.method, incoming.url.path))
        if incoming.url.path == "/v1/agent/me":
            return httpx.Response(200, json=dict(agent_id=IDENTITY.agent_id, role="member", enrollment_status="active",
                registration_status="active", approval_required=False, can_read=True, can_write=True,
                clearance=0, home_domain="public-domain"))
        assert incoming.method == "POST" and incoming.url.path == "/v1/memory/submit"
        assert any(row["payload"].get("state") == "dispatching" for row in state.records.values())
        assert any(row["payload"].get("body", {}).get("data", {}).get("state") == "forwarding" for row in state.records.values())
        value = json.loads(incoming.content)
        assert value["classification"] == 0 and value["memory_type"] == "inference"
        if state.post_timeout:
            raise httpx.ReadTimeout("synthetic unknown canonical submission")
        return httpx.Response(200, json=dict(memory_id=MEMORY_ID, tx_hash="a" * 64, status="proposed", committed_height=42))
    rest = LocalPublicREST(PUBLIC_ENDPOINT, transport=httpx.MockTransport(rest_handler))
    state.bridge = PublicGatewayBridge(state.workflow, rest, GATEWAY, chain_id="commons", domain="public-domain",
        authors=frozenset({IDENTITY.agent_id}), clock=lambda: state.now, access_check=lambda: state.enabled,
        storage_check=lambda: state.lease, backend_check=lambda endpoint, chain: state.backend)
    stream = SerialDouble(handshake_bytes(key=GATEWAY_RADIO), chunk=10000)
    state.device = MeshCoreClient(stream, expected_public_key=GATEWAY_RADIO)
    state.device.handshake()
    original = stream.write
    def write(data, timeout):
        command = data[3:]
        state.threads.append(threading.current_thread().name)
        if command == b"\x04":
            stream.incoming.extend(frame(start(len(state.radio_keys))) +
                b"".join(frame(contact(key)) for key in state.radio_keys) + frame(end(123)))
        elif command[:1] == b"\x02":
            assert any(row["payload"].get("body", {}).get("data", {}).get("state") == "dispatching"
                       for row in state.records.values())
            state.transmits.append(command)
            if state.lose_ack:
                raise TimeoutError("synthetic unknown USB dispatch")
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + GATEWAY_RADIO).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            if state.lock_after_send:
                state.enabled = False
        elif command == b"\x0a":
            stream.incoming.extend(frame(state.incoming.popleft() if state.incoming else b"\x0a"))
        return original(data, timeout)
    stream.write = write
    state.scheduler = SerialScheduler(state.device, release=lambda: setattr(state, "released", True))
    state.pin = PublicGatewayPin(RADIO, IDENTITY.agent_id)
    state.make = lambda: PublicGatewayRuntime(state.bridge, state.scheduler, (state.pin,),
        contact_check=lambda pin: pin == state.pin and state.contact,
        lifecycle_check=lambda: state.lease, timing=PublicMeshTiming(0.1))
    state.runtime = state.make()
    state.access = lambda: state.enabled and state.operation_allowed
    state.capture = lambda text: decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", NOW) + text.encode())
    state.ingest = lambda text: state.runtime.ingest_frame(state.capture(text), access_check=state.access).result(3)
    yield state
    state.runtime.close(3)
    state.scheduler.close(3)
    rest.close()


def assemble_request(state, outbound):
    progress = None
    for text in public_fragments(outbound.wire):
        progress = state.ingest(text)
    return progress


def response(state, outbound, progress):
    for unused in range(progress.fragment_count - progress.next_fragment):
        progress = state.runtime.dispatch_one(progress.slot_id, access_check=state.access).result(3)
    assembler = FragmentAssembler()
    wire = None
    for command in state.transmits:
        assert command[7:13] == RADIO[:6] and command[13:].startswith(b"LP1:")
        wire = assembler.accept(GATEWAY_RADIO, "SL1:" + command[13:].decode()[4:])
    assert wire is not None and progress.state == "serial_accepted"
    return protocol.open_result(wire, IDENTITY, request=outbound, now=state.now,
        consume=lambda identifier, digest: identifier == outbound.request_id and digest == outbound.digest,
        access_check=state.access)


def posts(state):
    return [call for call in state.calls if call[0] == "POST"]


def test_lp1_reassembly_bridge_and_real_serial_response(gateway, monkeypatch):
    def no_index(*args, **kwargs):
        raise AssertionError("gateway must not scan a private index")
    monkeypatch.setattr(gateway.workflow, "scan", no_index)
    monkeypatch.setattr(gateway.workflow, "list", no_index)
    outbound = request()
    progress = assemble_request(gateway, outbound)
    assert progress.state == "queued" and progress.next_fragment == 0 and not gateway.transmits
    result = response(gateway, outbound, progress)
    assert result["state"] == "receipt" and result["observed_status"] is None
    assert all(result[key] is False for key in protocol.FLAGS) and len(posts(gateway)) == 1
    assert set(gateway.threads) == {"lantern-mesh-owner"}
    assert gateway.runtime.close(3) is True and not gateway.released
    assert gateway.scheduler.submit(lambda device: device is gateway.device).result(3) is True


def test_restart_preserves_partial_assembly_and_response_queue(gateway):
    outbound = request()
    fragments = public_fragments(outbound.wire)
    gateway.ingest(fragments[0])
    assert not gateway.calls
    assert gateway.runtime.close(3) is True
    gateway.runtime = gateway.make()
    for text in reversed(fragments[1:]):
        progress = gateway.ingest(text)
    assert progress.state == "queued" and len(posts(gateway)) == 1
    assert gateway.runtime.close(3) is True
    gateway.runtime = gateway.make()
    assert response(gateway, outbound, progress)["state"] == "receipt"


def test_duplicate_complete_request_does_not_forward_or_reset_response(gateway):
    outbound = request()
    progress = assemble_request(gateway, outbound)
    advanced = gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    before = copy.deepcopy(gateway.records)
    duplicate = assemble_request(gateway, outbound)
    assert duplicate == advanced and gateway.records == before and len(posts(gateway)) == 1


def test_canonical_unknown_is_transported_not_resubmitted(gateway):
    gateway.post_timeout = True
    outbound = request()
    progress = assemble_request(gateway, outbound)
    assert response(gateway, outbound, progress)["state"] == "unknown"
    assemble_request(gateway, outbound)
    assert len(posts(gateway)) == 1


def test_unknown_usb_dispatch_survives_restart_without_retry(gateway):
    progress = assemble_request(gateway, request())
    gateway.lose_ack = True
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert len(gateway.transmits) == 1 and gateway.runtime.close(3) is False
    gateway.runtime = gateway.make()
    assert gateway.runtime.inspect(access_check=gateway.access).result(3)[0].state == "unknown"
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert len(gateway.transmits) == 1 and gateway.runtime.close(3) is False


def test_unknown_forwarding_is_not_reinvoked_after_restart(gateway, monkeypatch):
    outbound = request()
    calls = []
    original = PublicGatewayBridge.handle
    def lost(*args, **kwargs):
        calls.append(True)
        original(*args, **kwargs)
        raise OSError("synthetic response lost after receipt")
    monkeypatch.setattr(PublicGatewayBridge, "handle", lost)
    with pytest.raises(Exception):
        assemble_request(gateway, outbound)
    assert gateway.runtime.close(3) is False
    gateway.runtime = gateway.make()
    assert assemble_request(gateway, outbound).state == "unknown"
    assert calls == [True] and len(posts(gateway)) == 1 and not gateway.transmits


def test_lock_after_usb_preserves_exact_progress(gateway):
    progress = assemble_request(gateway, request())
    gateway.lock_after_send = True
    result = gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert result.next_fragment == 1 and result.state == "queued"
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert len(gateway.transmits) == 1 and gateway.runtime.close(3) is True


@pytest.mark.parametrize("change", ["untrusted_contact", "missing_radio", "wrong_author", "expiry", "budget"])
def test_authentication_and_timing_fail_before_public_post(gateway, change):
    outbound = request()
    if change == "untrusted_contact":
        gateway.contact = False
    elif change == "missing_radio":
        gateway.radio_keys = []
    elif change == "wrong_author":
        from sage_sdk import AgentIdentity
        identity = AgentIdentity(SigningKey(b"z" * 32))
        gateway.bridge._authors = frozenset({IDENTITY.agent_id, identity.agent_id})
        gateway.runtime = PublicGatewayRuntime(gateway.bridge, gateway.scheduler, (PublicGatewayPin(RADIO, identity.agent_id),),
            contact_check=lambda pin: True, lifecycle_check=lambda: gateway.lease, timing=PublicMeshTiming(0.1))
    elif change == "expiry":
        gateway.now = NOW + 120
    else:
        gateway.runtime._timing = PublicMeshTiming(3)
    with pytest.raises(Exception):
        assemble_request(gateway, outbound)
    assert not posts(gateway) and not gateway.transmits


def test_private_and_channel_traffic_never_indexed(gateway):
    before = copy.deepcopy(gateway.records)
    assert gateway.ingest("SL1:private ciphertext") is None
    channel = decode_incoming(b"\x08\x00\x00\x00" + struct.pack("<I", NOW) + b"LP1:untrusted")
    assert gateway.runtime.ingest_frame(channel, access_check=gateway.access).result(3) is None
    assert gateway.records == before and not gateway.calls
    forged = replace(gateway.capture("LP1:invalid"), sender_prefix=b"wrong!")
    with pytest.raises(Exception):
        gateway.runtime.ingest_frame(forged, access_check=gateway.access).result(3)
    assert gateway.records == before


def test_fixed_slots_and_per_peer_bound(gateway):
    for unused in range(2):
        gateway.ingest(public_fragments(request().wire)[0])
    before = copy.deepcopy(gateway.records)
    with pytest.raises(Exception):
        gateway.ingest(public_fragments(request().wire)[0])
    assert gateway.records == before and len(gateway.runtime.inspect(access_check=gateway.access).result(3)) == 2


def test_corrupt_signed_slot_or_unavailable_storage_denies_drain(gateway):
    gateway.ingest(public_fragments(request().wire)[0])
    row = next(row for row in gateway.records.values() if row["payload"].get("body", {}).get("schema") == SCHEMA)
    row["payload"]["body"]["data"]["expires_at"] += 1
    assert gateway.runtime.close(3) is False
    gateway.runtime = gateway.make()
    gateway.lease = False
    assert gateway.runtime.close(3) is False


def test_root_receiver_durable_capture_demultiplexes_without_nested_owner(gateway):
    outbound = request()
    receiver = MeshReceiver(gateway.workflow, gateway.device, slot_id=str(uuid.uuid4()),
        access_check=gateway.access, storage_check=lambda: gateway.lease)
    results = []
    for text in public_fragments(outbound.wire):
        gateway.incoming.append(gateway.capture(text).raw_frame)
        assert gateway.scheduler.submit(lambda device: receiver.receive_next()).result(3) == 'captured'
        def process(device):
            def consume(operation_id, message):
                results.append(gateway.runtime.ingest_on_owner(device, message, access_check=gateway.access))
                return results[-1] is not None
            return receiver.process_pending(consume)
        assert gateway.scheduler.submit(process).result(3) is True
    assert results[-1].state == 'queued'
    assert response(gateway, outbound, results[-1])["state"] == 'receipt'
    assert receiver.close(3) is True
    with pytest.raises(Exception):
        gateway.runtime.ingest_on_owner(gateway.device, gateway.capture(text), access_check=gateway.access)


def test_unknown_dispatch_intent_put_never_touches_usb(gateway):
    progress = assemble_request(gateway, request())
    gateway.fail_put = gateway.puts + 1
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert not gateway.transmits and gateway.runtime.close(3) is False
    gateway.runtime = gateway.make()
    assert gateway.runtime.inspect(access_check=gateway.access).result(3)[0].state == 'unknown'
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert not gateway.transmits


def test_expired_response_cannot_dispatch_after_restart(gateway):
    progress = assemble_request(gateway, request())
    assert gateway.runtime.close(3) is True
    gateway.runtime = gateway.make()
    gateway.now += 120
    assert gateway.runtime.inspect(access_check=gateway.access).result(3)[0].state == 'expired'
    with pytest.raises(Exception):
        gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert not gateway.transmits


def test_close_waits_inflight_without_releasing_shared_owner(gateway, monkeypatch):
    outbound = request()
    fragments = public_fragments(outbound.wire)
    for text in fragments[:-1]:
        gateway.ingest(text)
    entered, release = threading.Event(), threading.Event()
    original = PublicGatewayBridge.handle
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(PublicGatewayBridge, 'handle', held)
    pending = gateway.runtime.ingest_frame(gateway.capture(fragments[-1]), access_check=gateway.access)
    try:
        assert entered.wait(3)
        assert gateway.runtime.close(0) is False and not gateway.released
        with pytest.raises(Exception):
            gateway.runtime.inspect(access_check=gateway.access)
    finally:
        release.set()
    with pytest.raises(Exception):
        pending.result(3)
    assert not posts(gateway)
    assert gateway.runtime.close(3) is False and not gateway.released


def test_queued_owner_job_rechecks_lock_before_any_storage(gateway):
    entered, release = threading.Event(), threading.Event()
    blocker = gateway.scheduler.submit(lambda device: (entered.set(), release.wait(3)))
    assert entered.wait(3)
    before = copy.deepcopy(gateway.records)
    future = gateway.runtime.ingest_frame(gateway.capture(public_fragments(request().wire)[0]), access_check=gateway.access)
    gateway.enabled = False
    release.set()
    blocker.result(3)
    with pytest.raises(Exception):
        future.result(3)
    assert gateway.records == before and not gateway.calls


def test_per_request_guard_rechecked_between_profile_and_post(gateway, monkeypatch):
    original = gateway.bridge._rest.request
    def revoked(method, path, headers, body=b''):
        result = original(method, path, headers, body)
        if path == '/v1/agent/me':
            gateway.operation_allowed = False
        return result
    monkeypatch.setattr(gateway.bridge._rest, 'request', revoked)
    with pytest.raises(Exception):
        assemble_request(gateway, request())
    assert not posts(gateway) and not gateway.transmits
    assert gateway.runtime.close(3) is False


def test_shared_response_budget_survives_elapsed_request_and_rest_time(gateway, monkeypatch):
    outbound = request()
    fragments = public_fragments(outbound.wire)
    gateway.runtime._timing = timing = PublicMeshTiming(0.7)
    bound = protocol.response_fragment_bound(outbound.proposal, gateway_key=outbound.gateway_key)
    assert timing.seconds(len(fragments), bound) < 120
    arrival = NOW + math.ceil(len(fragments) * timing.frame_seconds)
    assert timing.seconds(0, 86) > outbound.expires_at - arrival
    original = gateway.bridge._rest.request
    def elapsed_rest(method, path, headers, body=b''):
        gateway.now += 15
        return original(method, path, headers, body)
    monkeypatch.setattr(gateway.bridge._rest, 'request', elapsed_rest)
    for index, text in enumerate(fragments):
        gateway.now = NOW + math.ceil((index + 1) * timing.frame_seconds)
        progress = gateway.ingest(text)
    assert progress.state == 'queued' and len(posts(gateway)) == 1
    assert gateway.now == arrival + 30 and progress.fragment_count <= bound
    dispatched_at = gateway.now
    for index in range(progress.fragment_count):
        gateway.now = dispatched_at + math.ceil((index + 1) * timing.frame_seconds)
        progress = gateway.runtime.dispatch_one(progress.slot_id, access_check=gateway.access).result(3)
    assert gateway.now < outbound.expires_at and response(gateway, outbound, progress)['state'] == 'receipt'


def test_elapsed_request_with_insufficient_response_budget_still_refused(gateway):
    outbound = request()
    fragments = public_fragments(outbound.wire)
    gateway.runtime._timing = timing = PublicMeshTiming(0.7)
    bound = protocol.response_fragment_bound(outbound.proposal, gateway_key=outbound.gateway_key)
    for text in fragments[:-1]:
        gateway.ingest(text)
    gateway.now = outbound.expires_at - math.floor(timing.seconds(0, bound))
    with pytest.raises(protocol.PublicMeshError):
        gateway.ingest(fragments[-1])
    assert not posts(gateway) and not gateway.transmits


def test_response_exceeding_shared_bound_retains_unknown_without_resubmit(gateway, monkeypatch):
    original = PublicGatewayBridge.handle
    def oversized(*args, **kwargs):
        return original(*args, **kwargs) + b'x' * 1000
    monkeypatch.setattr(PublicGatewayBridge, 'handle', oversized)
    outbound = request()
    with pytest.raises(protocol.PublicMeshError):
        assemble_request(gateway, outbound)
    assert len(posts(gateway)) == 1 and not gateway.transmits
    assert assemble_request(gateway, outbound).state == 'unknown'
    assert len(posts(gateway)) == 1 and gateway.runtime.close(3) is False
