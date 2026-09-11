import copy
import hashlib
import struct
import uuid
from dataclasses import replace

import pytest

from sage_lantern.gateway_contact import GatewayContactProbe, respond_contact, fragments, _wire, _open
from sage_lantern.public_mesh_provider import PublicMeshTiming, PublicMeshProvider, public_fragments
from sage_lantern.public_mesh_protocol import seal_submission
from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.meshcore_transport import decode_incoming
from test_public_mesh_provider import lantern, RADIO
from test_public_mesh_protocol import IDENTITY, GATEWAY, NOW
from test_meshcore_transport import frame, SerialDouble
from test_public_gateway_bridge import fixture


@pytest.fixture
def contact(lantern):
    state = lantern
    state.elapsed = 0
    probe = GatewayContactProbe(state.store, IDENTITY, state.pins, active=lambda: state.access,
        timing=PublicMeshTiming(0.1), clock=lambda: state.now, monotonic=lambda: state.elapsed)
    stream = state.device._stream
    original = stream.write
    assembler = FragmentAssembler()
    state.contact_wire = None
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b"\x02" and command[13:].startswith(b"LC1:"):
            state.contact_wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
            ack = hashlib.sha256(command[3:7] + b"\0" + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b"\x06\x00" + ack + struct.pack("<I", 100)))
            return SerialDouble.write(stream, data, timeout)
        return original(data, timeout)
    stream.write = write
    return state, probe


def response(contact):
    state, probe = contact
    assert probe.begin(access_check=state.check) is False
    for _ in range(86):
        if not probe.dispatch_one(device=state.device, access_check=state.check):
            break
    assert state.contact_wire is not None
    return respond_contact(state.contact_wire, GATEWAY, pins=state.pins,
                           author=IDENTITY.agent_id, allowed_authors=frozenset({IDENTITY.agent_id}), now=state.now)


def receive(contact, wire):
    state, probe = contact
    for text in fragments(wire):
        raw = b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", state.now) + text.encode()
        probe.ingest_frame(decode_incoming(raw), access_check=state.check)


def test_actual_signed_roundtrip_and_no_cached_contact_authority(contact):
    state, probe = contact
    assert probe.current(state.pins) is False
    reply = response(contact)
    assert probe.current(state.pins) is False
    receive(contact, reply)
    assert probe.current(state.pins) is True
    deadline = probe._deadline
    state.elapsed += 10
    receive(contact, reply)
    assert probe._deadline == deadline
    state.elapsed = deadline
    assert probe.current(state.pins) is False
    assert probe.drain(access_check=state.check) is True


@pytest.mark.parametrize("field", ["nonce", "request_id", "author", "gateway_key", "radio_key", "chain_id", "domain", "expires_at"])
def test_wrong_signed_binding_never_authorizes(contact, field):
    state, probe = contact
    reply = response(contact)
    body = _open(reply, state.pins.gateway_key)
    body[field] = body[field] + 1 if type(body[field]) is int else "wrong"
    with pytest.raises(Exception):
        receive(contact, _wire(body, GATEWAY))
    assert probe.current(state.pins) is False


def test_expired_response_and_restart_do_not_mint_freshness(contact):
    state, probe = contact
    reply = response(contact)
    restarted = GatewayContactProbe(state.store, IDENTITY, state.pins, active=lambda: True, clock=lambda: NOW)
    assert restarted.current(state.pins) is False
    with pytest.raises(Exception):
        receive((state, restarted), reply)
    state.elapsed = 121
    with pytest.raises(Exception):
        receive(contact, reply)
    assert probe.current(state.pins) is False


def test_lost_persist_reply_denies_contact_and_drain(contact):
    state, probe = contact
    reply = response(contact)
    state.fail_put = True
    with pytest.raises(Exception):
        receive(contact, reply)
    assert probe.current(state.pins) is False
    with pytest.raises(Exception):
        probe.drain(access_check=state.check)


def test_timing_rejected_before_intent(contact):
    state, probe = contact
    probe._timing = PublicMeshTiming(30)
    before = copy.deepcopy(state.rows)
    with pytest.raises(Exception):
        probe.begin(access_check=state.check)
    assert state.rows == before


def admitted_request(contact):
    state, probe = contact
    receive(contact, response(contact))
    request = seal_submission(state.item, IDENTITY, state.pins.gateway_key,
        request_id=str(uuid.uuid4()), now=state.now, access_check=state.check,
        approval_check=lambda item: state.approved and item == state.item)
    token = probe.admit(request, pins=state.pins, access_check=state.check)
    return request, token


@pytest.mark.parametrize("damage", ["token", "uuid", "digest", "proposal", "deadline", "pins", "replay", "released", "restart"])
def test_admission_cannot_be_forged_reassigned_or_replayed(contact, damage):
    state, probe = contact
    request, token = admitted_request(contact)
    pins = state.pins
    if damage == "token":
        token = object()
    elif damage == "uuid":
        request = replace(request, request_id=str(uuid.uuid4()))
    elif damage == "digest":
        request = replace(request, digest="f" * 64)
    elif damage == "proposal":
        request = replace(request, proposal=replace(request.proposal, operation_id=str(uuid.uuid4())))
    elif damage == "deadline":
        request = replace(request, expires_at=request.expires_at + 1)
    elif damage == "pins":
        pins = replace(pins, chain_id="other")
    elif damage == "replay":
        with pytest.raises(Exception):
            probe.admit(request, pins=pins, access_check=state.check)
        return
    elif damage == "released":
        probe.release(token, request.request_id)
        token = None
    else:
        probe = GatewayContactProbe(state.store, IDENTITY, state.pins, active=state.check,
            clock=lambda: state.now, monotonic=lambda: state.elapsed)
    with pytest.raises(Exception):
        probe.admitted(token, request, pins=pins, access_check=state.check)


@pytest.mark.parametrize("damage", ["authorization", "configuration", "wall_deadline", "monotonic_deadline", "clock_backwards"])
def test_admission_keeps_live_gates_and_original_deadline(contact, damage):
    state, probe = contact
    request, token = admitted_request(contact)
    if damage == "authorization":
        state.access = False
    elif damage == "configuration":
        probe._active = lambda: False
    elif damage == "wall_deadline":
        state.now = request.expires_at
    elif damage == "monotonic_deadline":
        state.elapsed = 120
    else:
        state.elapsed = -1
    with pytest.raises(Exception):
        probe.admitted(token, request, pins=state.pins, access_check=state.check)


def test_actual_lp1_exchange_outlives_lc1_freshness_not_request_deadline(contact, fixture):
    state, probe = contact
    receive(contact, response(contact))
    state.elapsed, state.now = 59, NOW + 59
    provider = PublicMeshProvider(state.store, IDENTITY, state.pins,
        approval_check=lambda item: state.approved and item == state.item,
        contact_check=probe.current, contact_admission=probe,
        timing=PublicMeshTiming(0.1), clock=lambda: state.now)
    progress = provider.begin(state.item, request_id=str(uuid.uuid4()), device=state.device, access_check=state.check)
    state.elapsed, state.now = 61, NOW + 61
    assert probe.current(state.pins) is False
    for _ in range(progress.fragment_count):
        progress = provider.dispatch_one(progress.request_id, device=state.device, access_check=state.check)
    assert progress.state == "awaiting_response"
    assembler = FragmentAssembler()
    for command in state.transmits:
        wire = assembler.accept(state.device._key, "SL1:" + command[13:].decode()[4:])
    state.elapsed, state.now = 121, NOW + 121
    fixture.now = state.now
    sealed = fixture.bridge().handle(wire, author=IDENTITY.agent_id)
    for text in public_fragments(sealed):
        message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", state.now) + text.encode())
        result = provider.ingest_frame(message, access_check=state.check)
    assert result["state"] == "receipt" and result["cache_eligible"] is False
    assert provider.inspect(progress.request_id, access_check=state.check)[0].state == "consumed"
    assert provider.receive(sealed, access_check=state.check) is None
    with pytest.raises(Exception):
        provider.begin(state.item, request_id=str(uuid.uuid4()), device=state.device, access_check=state.check)
    assert len([call for call in fixture.calls if call[0] == "POST"]) == 1
    assert provider.close()


@pytest.mark.parametrize("phase", ["send", "receive"])
@pytest.mark.parametrize("revocation", ["authorization", "approval", "configuration", "pins", "deadline"])
def test_bound_provider_rechecks_revocation_before_io(contact, fixture, phase, revocation):
    state, probe = contact
    receive(contact, response(contact))
    probe._active = lambda: state.contact
    provider = PublicMeshProvider(state.store, IDENTITY, state.pins,
        approval_check=lambda item: state.approved and item == state.item,
        contact_check=probe.current, contact_admission=probe,
        timing=PublicMeshTiming(0.1), clock=lambda: state.now)
    progress = provider.begin(state.item, request_id=str(uuid.uuid4()), device=state.device, access_check=state.check)
    message = None
    if phase == "receive":
        for _ in range(progress.fragment_count):
            progress = provider.dispatch_one(progress.request_id, device=state.device, access_check=state.check)
        request = provider._live[progress.request_id]
        sealed = fixture.bridge().handle(request.wire, author=IDENTITY.agent_id)
        text = public_fragments(sealed)[0]
        message = decode_incoming(b"\x07" + RADIO[:6] + bytes(2) + struct.pack("<I", state.now) + text.encode())
    if revocation == "authorization":
        state.access = False
    elif revocation == "approval":
        state.approved = False
    elif revocation == "configuration":
        state.contact = False
    elif revocation == "pins":
        provider.pins = replace(state.pins, gateway_key="f" * 64)
    else:
        state.elapsed = 120
    rows, writes = copy.deepcopy(state.rows), len(state.transmits)
    with pytest.raises(Exception):
        if phase == "send":
            provider.dispatch_one(progress.request_id, device=state.device, access_check=state.check)
        else:
            provider.ingest_frame(message, access_check=state.check)
    assert state.rows == rows and len(state.transmits) == writes
    assert provider.close()


def test_new_contact_proof_cannot_revive_expired_admission(contact):
    state, probe = contact
    request, token = admitted_request(contact)
    state.elapsed, state.now = 121, NOW + 121
    receive(contact, response(contact))
    assert probe.current(state.pins)
    with pytest.raises(Exception):
        probe.admitted(token, request, pins=state.pins, access_check=state.check)
