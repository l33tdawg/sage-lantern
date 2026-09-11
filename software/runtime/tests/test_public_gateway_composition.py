"""Production root factory/composition with synthetic HTTP, USB and root leases."""

import copy
import hashlib
import json
import struct
import threading
import time
import uuid
from types import SimpleNamespace

import httpx
import pytest

from sage_lantern.public_gateway_bridge import PublicGatewayBridge, LocalPublicREST
from sage_lantern.public_gateway_composition import RootPublicGateway, SCHEMA, validate_gateway_config
from sage_lantern.public_mesh_provider import public_fragments
from sage_lantern.storage_supervisor_socket import CaptureLease
from sage_lantern import public_mesh_protocol as protocol
from test_public_mesh_protocol import GATEWAY, GATEWAY_KEY, IDENTITY, request
from test_community_mesh_runtime import integrated, factory
from test_meshcore_transport import frame
from sage_lantern.mesh_fragments import FragmentAssembler
from sage_lantern.gateway_contact import fragments as contact_fragments, _wire as contact_wire, _open as open_contact


@pytest.fixture
def role(integrated):
    state = integrated
    state.configured = state.calibrated = state.contact_current = state.lease_ready = True
    state.http_calls, state.held = [], set()
    state.now = int(time.time())
    def rest_handler(incoming):
        headers = {key: incoming.headers[key] for key in ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}
        protocol.verify_http(headers, IDENTITY.agent_id, incoming.method, incoming.url.path,
            incoming.content, int(headers["X-Timestamp"]))
        state.http_calls.append((incoming.method, incoming.url.path))
        if incoming.url.path == '/v1/agent/me':
            return httpx.Response(200, json=dict(agent_id=IDENTITY.agent_id, role='member', enrollment_status='active',
                registration_status='active', approval_required=False, can_read=True, can_write=True,
                clearance=0, home_domain='public-domain'))
        assert incoming.method == 'POST' and incoming.url.path == '/v1/memory/submit'
        assert json.loads(incoming.content)['classification'] == 0
        return httpx.Response(200, json=dict(memory_id='10000000-0000-4000-8000-000000000001',
            tx_hash='a' * 64, status='proposed', committed_height=42))
    rest = LocalPublicREST('http://127.0.0.1:19101', transport=httpx.MockTransport(rest_handler))
    state.bridge = PublicGatewayBridge(state.runtime.store, rest, GATEWAY, chain_id='commons', domain='public-domain',
        authors=frozenset({IDENTITY.agent_id}), clock=lambda: state.now, access_check=lambda: state.authorized,
        storage_check=lambda: state.active, backend_check=lambda endpoint, chain: state.configured)
    state.settings = dict(schema=SCHEMA, role='public_gateway', enabled=True, endpoint=rest.endpoint,
        chain_id='commons', domain='public-domain', gateway_key=GATEWAY_KEY,
        storage_actor=state.runtime.actor.agent_id, storage_chain=state.runtime.actor.chain_id,
        pins=[dict(radio_key=state.radio.hex(), author=IDENTITY.agent_id)],
        timing=dict(frame_seconds=0.1, gateway_seconds=30, safety_seconds=10),
        operation_seconds=50, lease_seconds=55, poll_interval=0.01)
    def lease(consent):
        assert consent.actor == state.runtime.actor
        def transport(body):
            if body['op'] == 'acquire':
                token = uuid.uuid4().hex * 2
                state.held.add(token)
                return {'lease': token}
            if body['op'] == 'check':
                return {'allowed': state.active and state.lease_ready and body['lease'] in state.held}
            state.held.remove(body['lease'])
            return {'finished': True}
        return CaptureLease('a' * 32, transport=transport)
    state.make_role = lambda: RootPublicGateway(state.provider, state.bridge, state.settings,
        config_check=lambda settings: state.configured and settings == state.settings,
        contact_check=lambda pin: state.contact_current,
        timing_check=lambda settings: state.calibrated,
        lease_budget_check=lambda seconds: state.lease_ready and seconds <= 55,
        lease_factory=lease, unlocked_check=lambda: state.authorized)
    state.role = state.make_role()
    stream = state.device._stream
    original_write = stream.write
    def write(data, timeout):
        command = data[3:]
        if command[:1] == b'\x02':
            assert any(row['payload'].get('body', {}).get('data', {}).get('state') == 'dispatching'
                       for row in state.rows.values())
            state.transmits.append(command)
            ack = hashlib.sha256(command[3:7] + b'\0' + command[13:] + state.device._key).digest()[:4]
            stream.incoming.extend(frame(b'\x06\x00' + ack + struct.pack('<I', 100)))
            return len(data)
        return original_write(data, timeout)
    stream.write = write
    yield state
    state.provider.pause_receiving(timeout=5)
    state.role.close(5)
    rest.close()


@pytest.mark.parametrize('change', [dict(role='lantern'), dict(enabled=1), dict(endpoint='http://example.com'),
    dict(operation_seconds=10), dict(lease_seconds=30), dict(poll_interval=1),
    dict(operation_seconds=float('inf')), dict(extra='unreviewed'), dict(storage_actor='f' * 64)])
def test_exact_gateway_config_rejects_legacy_or_unsafe_bounds(role, change):
    validate_gateway_config(role.settings, role.bridge)
    with pytest.raises(Exception):
        validate_gateway_config(dict(role.settings, **change), role.bridge)
    assert not role.http_calls


@pytest.mark.parametrize('gate', ['configured', 'calibrated', 'lease_ready', 'contact_current', 'authorized'])
def test_startup_fails_before_role_attachment_without_real_gates(role, gate):
    setattr(role, gate, False)
    with pytest.raises(Exception):
        role.role.start(access_check=lambda: role.authorized)
    assert role.runtime._poller is None and not role.http_calls and not role.transmits


def test_config_pins_are_exact_and_cannot_duplicate_prefix(role):
    changed = copy.deepcopy(role.settings)
    changed['pins'] *= 2
    with pytest.raises(Exception):
        validate_gateway_config(changed, role.bridge)


def wait_for(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.01)
    raise AssertionError('root gateway worker did not finish synthetic exchange')


@pytest.mark.parametrize('traffic', ['LP1', 'LC1'])
def test_actual_root_loop_capture_response_dispatch_and_clean_close(role, traffic):
    if traffic == 'LP1':
        outbound = request(now=role.now)
        fragments = public_fragments(outbound.wire)
    else:
        probe = dict(schema='sage.lantern.gateway-contact.v1', kind='probe', request_id=str(uuid.uuid4()),
            nonce='b' * 64, author=IDENTITY.agent_id, gateway_key=GATEWAY_KEY, radio_key=role.device._key.hex(),
            chain_id='commons', domain='public-domain', issued_at=role.now, expires_at=role.now + 120)
        fragments = contact_fragments(contact_wire(probe, IDENTITY._signing_key))
    for text in fragments:
        role.pending.append(b'\x07' + role.radio[:6] + bytes(2) + struct.pack('<I', role.now) + text.encode())
    original_timeout = role.runtime.config.operation_timeout
    assert role.role.start(access_check=lambda: role.authorized) is True
    assert role.runtime._public_gateway is role.role and role.runtime.config.operation_timeout == original_timeout
    def sent():
        return any(row['payload'].get('body', {}).get('data', {}).get('state') == 'sent' for row in role.rows.values())
    wait_for(sent)
    assert role.provider.pause_receiving(timeout=5) is True and not role.held
    assembler = FragmentAssembler()
    wire = None
    for command in role.transmits:
        assert command[13:].startswith((traffic + ':').encode())
        wire = assembler.accept(role.device._key, 'SL1:' + command[13:].decode()[4:])
    assert wire is not None
    if traffic == 'LP1':
        result = protocol.open_result(wire, IDENTITY, request=outbound, now=role.now,
            consume=lambda identifier, digest: identifier == outbound.request_id and digest == outbound.digest,
            access_check=lambda: role.authorized)
        assert result['state'] == 'receipt' and all(result[key] is False for key in protocol.FLAGS)
        assert sum(method == 'POST' for method, path in role.http_calls) == 1
    else:
        assert open_contact(wire, GATEWAY_KEY) == dict(probe, kind='reply')
        assert not role.http_calls
    assert role.provider.close(5) is True and role.released


def test_lc1_wrong_author_retains_original_capture_without_rest(role):
    probe = dict(schema='sage.lantern.gateway-contact.v1', kind='probe', request_id=str(uuid.uuid4()),
        nonce='b' * 64, author=IDENTITY.agent_id, gateway_key=GATEWAY_KEY, radio_key=role.device._key.hex(),
        chain_id='commons', domain='public-domain', issued_at=role.now, expires_at=role.now + 120)
    for text in contact_fragments(contact_wire(probe, GATEWAY)):
        role.pending.append(b'\x07' + role.radio[:6] + bytes(2) + struct.pack('<I', role.now) + text.encode())
    assert role.role.start(access_check=lambda: role.authorized)
    wait_for(lambda: not role.runtime._poller.is_alive())
    assert role.rows[role.config.receiver_slot]['payload']['state'] == 'captured'
    assert not role.http_calls and not role.transmits


def queue_submission(role):
    outbound = request(now=role.now)
    for text in public_fragments(outbound.wire):
        role.pending.append(b'\x07' + role.radio[:6] + bytes(2) + struct.pack('<I', role.now) + text.encode())
    return outbound


def test_gateway_job_allows_calibrated_over_30_seconds_without_widening_ui_timeout(role, monkeypatch):
    import sage_lantern.mesh_runtime as runtime_module
    offset = [0]
    monkeypatch.setattr(runtime_module, 'time', SimpleNamespace(monotonic=lambda: time.monotonic() + offset[0], time=time.time))
    original = role.bridge._rest.request
    def elapsed(method, path, headers, body=b''):
        if method == 'POST':
            offset[0] = 31
        return original(method, path, headers, body)
    monkeypatch.setattr(role.bridge._rest, 'request', elapsed)
    queue_submission(role)
    assert role.role.start(access_check=lambda: role.authorized)
    wait_for(lambda: any(row['payload'].get('body', {}).get('data', {}).get('state') == 'sent' for row in role.rows.values()))
    assert role.runtime.config.operation_timeout == 10 and not role.runtime._uncertain
    assert role.provider.close(5) is True


def test_shutdown_during_canonical_post_retains_owner_capture_and_inflight_receipt(role, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    original = role.bridge._rest.request
    def held(method, path, headers, body=b''):
        if method == 'POST':
            entered.set()
            assert release.wait(5)
        return original(method, path, headers, body)
    monkeypatch.setattr(role.bridge._rest, 'request', held)
    queue_submission(role)
    assert role.role.start(access_check=lambda: role.authorized)
    try:
        assert entered.wait(10)
        assert role.provider.close(0.05) is False
        assert not role.released and role.held
    finally:
        release.set()
    wait_for(lambda: not role.runtime._poller.is_alive())
    assert not role.held
    assert any(row['payload'].get('state') == 'receipt' for row in role.rows.values())
    assert role.rows[role.config.receiver_slot]['payload']['state'] == 'captured'
    assert role.provider.close(5) is False and not role.released and not role.transmits
    assert sum(method == 'POST' for method, path in role.http_calls) == 1


def test_root_role_does_not_auto_resubmit_after_serial_uncertainty(role, monkeypatch):
    original = role.device.send_text
    calls = []
    def lost(**kwargs):
        result = original(**kwargs)
        calls.append(result)
        raise OSError('synthetic serial reply uncertainty')
    monkeypatch.setattr(role.device, 'send_text', lost)
    queue_submission(role)
    assert role.role.start(access_check=lambda: role.authorized)
    wait_for(lambda: not role.runtime._poller.is_alive())
    assert len(calls) == 1 and len(role.transmits) == 1
    assert role.provider.close(5) is False and not role.released


def test_gateway_and_lantern_public_roles_cannot_share_demux(role):
    assert role.role.start(access_check=lambda: role.authorized)
    with pytest.raises(Exception):
        role.runtime.attach_public_gateway(role.role)
    assert role.runtime._public_gateway is role.role


def test_lc1_then_lp1_share_one_root_owner_and_distinct_durable_slots(role):
    probe = dict(schema='sage.lantern.gateway-contact.v1', kind='probe', request_id=str(uuid.uuid4()),
        nonce='c' * 64, author=IDENTITY.agent_id, gateway_key=GATEWAY_KEY, radio_key=role.device._key.hex(),
        chain_id='commons', domain='public-domain', issued_at=role.now, expires_at=role.now + 120)
    for text in contact_fragments(contact_wire(probe, IDENTITY._signing_key)):
        role.pending.append(b'\x07' + role.radio[:6] + bytes(2) + struct.pack('<I', role.now) + text.encode())
    outbound = queue_submission(role)
    assert role.role.start(access_check=lambda: role.authorized)
    def sent_both():
        return sum(row['payload'].get('body', {}).get('data', {}).get('state') == 'sent'
                   for row in role.rows.values()) == 2
    wait_for(sent_both)
    assert role.provider.pause_receiving(5) is True
    assembler, completed = FragmentAssembler(), {}
    for command in role.transmits:
        wire = assembler.accept(role.device._key, 'SL1:' + command[13:].decode()[4:])
        if wire is not None:
            completed[command[13:16].decode()] = wire
    assert open_contact(completed['LC1'], GATEWAY_KEY) == dict(probe, kind='reply')
    result = protocol.open_result(completed['LP1'], IDENTITY, request=outbound, now=role.now,
        consume=lambda identifier, digest: identifier == outbound.request_id and digest == outbound.digest,
        access_check=lambda: role.authorized)
    assert result['state'] == 'receipt' and sum(method == 'POST' for method, path in role.http_calls) == 1
    assert role.provider.close(5) is True


def test_gateway_role_cannot_grant_private_inbox_indexing(role):
    role.pending.append(b'\x07' + role.radio[:6] + bytes(2) + struct.pack('<I', role.now) + b'SL1:private-traffic')
    before = set(role.rows)
    assert role.role.start(access_check=lambda: role.authorized)
    wait_for(lambda: not role.runtime._poller.is_alive())
    assert role.rows[role.config.receiver_slot]['payload']['state'] == 'captured'
    assert set(role.rows) - before == set()
    assert not role.http_calls and not role.transmits
