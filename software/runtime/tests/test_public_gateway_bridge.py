"""Actual WorkflowStore/SDK against synthetic encrypted-capability REST fixtures."""

import json
from types import SimpleNamespace
import uuid

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.community_public import PublicReceipt
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.mesh_fragments import FragmentAssembler, split_envelope
from sage_lantern.workflow_store import WorkflowStore
from sage_lantern.public_gateway_bridge import LocalPublicREST, PublicGatewayBridge
from sage_lantern import public_mesh_protocol as protocol
from test_public_mesh_protocol import NOW, IDENTITY, GATEWAY, GATEWAY_KEY, proposal, request


MEMORY_ID = "00000000-0000-4000-8000-000000000001"
STORAGE_ENDPOINT = "http://127.0.0.1:19100"
PUBLIC_ENDPOINT = "http://127.0.0.1:19101"


@pytest.fixture
def fixture():
    value = SimpleNamespace(records={}, calls=[], storage_calls=[], enabled=True, lease=True, encrypted=True,
                            now=NOW, backend=True, post_timeout=False, lock_in_post=False, fail_put=None,
                            puts=0, status="committed", tamper_record=False, bad_profile=False, response_override=None)
    storage_identity = AgentIdentity(SigningKey(b"\x03" * 32))
    actor = RecipientID("gateway-private", storage_identity.agent_id)
    def storage_handler(incoming):
        value.storage_calls.append(incoming.url.path)
        path = incoming.url.path
        if path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", instance_id="b" * 64,
                agent_id=actor.agent_id, encryption_expected=value.encrypted, vault_active=True,
                vault_generation="1", stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if path == "/v1/workflows":
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=[], next_after=None, has_more=False))
        identifier = path.rsplit("/", 1)[-1]
        if incoming.method == "GET":
            return httpx.Response(200 if identifier in value.records else 404, json=value.records.get(identifier, {}))
        assert incoming.method == "PUT"
        body = json.loads(incoming.content)
        current = value.records.get(identifier)
        if (current["revision"] if current else 0) != body["expected_revision"]:
            return httpx.Response(409, json={"error": "synthetic CAS conflict"})
        value.puts += 1
        row = dict(schema="sage.workflow-journal.v1", agent_id=actor.agent_id, record_id=identifier,
                   revision=body["expected_revision"] + 1, kind=body["kind"], payload=body["payload"], trust="untrusted_auxiliary")
        value.records[identifier] = row
        if value.fail_put == value.puts:
            raise httpx.ReadTimeout("synthetic persisted but lost reply")
        return httpx.Response(200, json=row)
    client = SageClient(STORAGE_ENDPOINT, storage_identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=STORAGE_ENDPOINT, timeout=2, trust_env=False,
                                follow_redirects=False, transport=httpx.MockTransport(storage_handler))
    probe = MessageStorageProbe(client, actor=actor, endpoint=STORAGE_ENDPOINT)
    value.workflow = WorkflowStore(client, storage=probe)
    def rest_handler(incoming):
        headers = {key: incoming.headers[key] for key in ("X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce")}
        protocol.verify_http(headers, IDENTITY.agent_id, incoming.method, incoming.url.path, incoming.content, int(headers["X-Timestamp"]))
        assert str(incoming.url).startswith(PUBLIC_ENDPOINT + "/")
        value.calls.append((incoming.method, incoming.url.path))
        if incoming.url.path == "/v1/agent/me":
            return httpx.Response(200, json=dict(agent_id=IDENTITY.agent_id, role="member", enrollment_status="active",
                registration_status="active", approval_required=False, can_read=True, can_write=True,
                clearance=1 if value.bad_profile else 0, home_domain="public-domain"))
        if incoming.method == "POST":
            assert any(row["payload"]["state"] == "dispatching" for row in value.records.values())
            body = json.loads(incoming.content)
            value.content = body["content"]
            assert body["classification"] == 0 and body["memory_type"] == "inference" and "idempotency_key" not in body
            if value.lock_in_post:
                value.enabled = False
            if value.post_timeout:
                raise httpx.ReadTimeout("synthetic submit reached node; reply lost")
            return httpx.Response(200, json=value.response_override or dict(memory_id=MEMORY_ID, tx_hash="a" * 64,
                                                                          status="proposed", committed_height=42))
        row = next(iter(value.records.values()))["payload"]
        item = row["proposal"]
        return httpx.Response(200, json=dict(memory_id=MEMORY_ID, submitting_agent=IDENTITY.agent_id, domain_tag="public-domain",
            classification=1 if value.tamper_record else 0, memory_type="inference", content=item["content"],
            content_hash=item["content_hash"], status=value.status))
    value.rest = LocalPublicREST(PUBLIC_ENDPOINT, transport=httpx.MockTransport(rest_handler))
    def bridge():
        return PublicGatewayBridge(value.workflow, value.rest, GATEWAY, chain_id="commons", domain="public-domain",
            authors=frozenset({IDENTITY.agent_id}), clock=lambda: value.now, access_check=lambda: value.enabled,
            storage_check=lambda: value.lease, backend_check=lambda endpoint, chain: value.backend and endpoint == PUBLIC_ENDPOINT and chain == "commons")
    value.bridge = bridge
    yield value
    client._client.close()
    value.rest.close()


def exchange(fixture, outbound, bridge=None):
    wire = (bridge or fixture.bridge()).handle(outbound.wire, author=IDENTITY.agent_id)
    consumed = set()
    def consume(identifier, digest):
        if (identifier, digest) in consumed:
            return False
        consumed.add((identifier, digest))
        return True
    return protocol.open_result(wire, IDENTITY, request=outbound, now=fixture.now, consume=consume, access_check=lambda: fixture.enabled)


def posts(fixture):
    return [call for call in fixture.calls if call[0] == "POST"]


def test_submit_restart_and_poll_full_sdk_roundtrip(fixture):
    item = proposal()
    first = exchange(fixture, request(item))
    assert first["state"] == "receipt" and first["observed_status"] is None
    again = exchange(fixture, request(item), fixture.bridge())
    assert again["receipt"] == first["receipt"] and len(posts(fixture)) == 1
    outbound = protocol.seal_poll(item, PublicReceipt(**first["receipt"]), IDENTITY, GATEWAY_KEY,
        request_id=str(uuid.uuid4()), now=NOW, access_check=lambda: True, approval_check=lambda item: True)
    result = exchange(fixture, outbound)
    assert result["state"] == "observed" and result["observed_status"] == "committed"
    assert all(result[key] is False for key in protocol.FLAGS)
    assert len(posts(fixture)) == 1
    assert next(iter(fixture.records.values()))["payload"]["proposal"]["content"] == item.content


def test_lost_post_response_restart_never_resubmits(fixture):
    fixture.post_timeout = True
    item = proposal()
    assert exchange(fixture, request(item))["state"] == "unknown"
    fixture.post_timeout = False
    assert exchange(fixture, request(item), fixture.bridge())["state"] == "unknown"
    assert len(posts(fixture)) == 1


@pytest.mark.parametrize("write_number", [2, 3])
def test_journal_response_loss_never_forwards_twice(fixture, write_number):
    fixture.fail_put = write_number
    item = proposal()
    if write_number == 2:
        with pytest.raises(protocol.PublicMeshError):
            exchange(fixture, request(item))
    else:
        assert exchange(fixture, request(item))["state"] == "unknown"
    fixture.fail_put = None
    result = exchange(fixture, request(item), fixture.bridge())
    assert result["state"] == ("unknown" if write_number == 2 else "receipt")
    assert len(posts(fixture)) == (0 if write_number == 2 else 1)


def test_lock_mid_forward_preserves_receipt_under_storage_lease(fixture):
    fixture.lock_in_post = True
    item = proposal()
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, request(item))
    assert next(iter(fixture.records.values()))["payload"]["state"] == "receipt"
    fixture.enabled = True
    assert exchange(fixture, request(item))["state"] == "receipt"
    assert len(posts(fixture)) == 1


@pytest.mark.parametrize("gate", ["enabled", "encrypted", "lease", "backend"])
def test_gates_fail_before_rest(fixture, gate):
    setattr(fixture, gate, False)
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, request())
    assert not fixture.calls


def test_bad_profile_no_forward_and_new_fresh_request_can_resume_prepared(fixture):
    item = proposal()
    fixture.bad_profile = True
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, request(item))
    assert not posts(fixture)
    fixture.bad_profile = False
    assert exchange(fixture, request(item))["state"] == "receipt"
    assert len(posts(fixture)) == 1


def test_expired_packet_does_not_create_journal_or_send(fixture):
    outbound = request()
    fixture.now += 120
    with pytest.raises(protocol.PublicMeshError, match="expired"):
        exchange(fixture, outbound)
    assert not fixture.records and not fixture.calls


def test_same_operation_changed_content_denied(fixture):
    item = proposal()
    exchange(fixture, request(item))
    changed = proposal("Different approved derivative", item.operation_id)
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, request(changed))
    assert len(posts(fixture)) == 1


@pytest.mark.parametrize("status", ["proposed", "challenged", "deprecated"])
def test_terminal_and_pending_status_never_become_cache_acceptance(fixture, status):
    item = proposal()
    receipt = PublicReceipt(**exchange(fixture, request(item))["receipt"])
    fixture.status = status
    outbound = protocol.seal_poll(item, receipt, IDENTITY, GATEWAY_KEY, request_id=str(uuid.uuid4()), now=NOW,
                                  access_check=lambda: True, approval_check=lambda item: True)
    result = exchange(fixture, outbound)
    assert result["observed_status"] == status and all(result[key] is False for key in protocol.FLAGS)


def test_exact_get_contradictory_classification_refused(fixture):
    item = proposal()
    receipt = PublicReceipt(**exchange(fixture, request(item))["receipt"])
    fixture.tamper_record = True
    outbound = protocol.seal_poll(item, receipt, IDENTITY, GATEWAY_KEY, request_id=str(uuid.uuid4()), now=NOW,
                                  access_check=lambda: True, approval_check=lambda item: True)
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, outbound)


@pytest.mark.parametrize("endpoint", ["https://example.invalid", "http://localhost:8080", "http://127.0.0.1:8080/path", "http://127.0.0.1:99999"])
def test_only_fixed_loopback_backend(endpoint):
    with pytest.raises(protocol.PublicMeshError):
        LocalPublicREST(endpoint)


def test_malformed_post_receipt_stays_unknown(fixture):
    fixture.response_override = {"status": "committed", "memory_id": MEMORY_ID, "tx_hash": "not a receipt"}
    item = proposal()
    assert exchange(fixture, request(item))["state"] == "unknown"
    assert exchange(fixture, request(item))["state"] == "unknown" and len(posts(fixture)) == 1


def test_changed_endpoint_restart_cannot_create_second_operation(fixture):
    item = proposal()
    exchange(fixture, request(item))
    alternate = LocalPublicREST("http://127.0.0.1:19102", transport=httpx.MockTransport(lambda incoming: pytest.fail("must not forward")))
    try:
        bridge = PublicGatewayBridge(fixture.workflow, alternate, GATEWAY, chain_id="commons", domain="public-domain",
            authors=frozenset({IDENTITY.agent_id}), clock=lambda: NOW, access_check=lambda: True,
            storage_check=lambda: True, backend_check=lambda *args: True)
        with pytest.raises(protocol.PublicMeshError):
            exchange(fixture, request(item), bridge)
    finally:
        alternate.close()
    assert len(posts(fixture)) == 1


def test_unknown_gateway_author_rejected_without_any_io(fixture):
    with pytest.raises(protocol.PublicMeshError):
        fixture.bridge().handle(request().wire, author="b" * 64)
    assert not fixture.calls and not fixture.storage_calls


@pytest.mark.parametrize("response", [httpx.Response(302, headers={"location": "https://example.invalid"}),
                                      httpx.Response(200, content=b"x" * 65537)])
def test_redirect_and_oversized_response_fail_closed(response):
    calls = []
    def handler(incoming):
        calls.append(incoming.url)
        return response
    rest = LocalPublicREST(PUBLIC_ENDPOINT, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(protocol.PublicMeshError):
            rest.request("GET", "/v1/agent/me", IDENTITY.sign_request("GET", "/v1/agent/me", timestamp=NOW))
        assert len(calls) == 1
    finally:
        rest.close()


def test_lock_after_intent_before_forward_never_sends(fixture):
    bridge = fixture.bridge()
    original = bridge._put
    def put(item, revision, payload):
        row = original(item, revision, payload)
        if payload["state"] == "dispatching":
            fixture.enabled = False
        return row
    bridge._put = put
    item = proposal()
    with pytest.raises(protocol.PublicMeshError):
        exchange(fixture, request(item), bridge)
    assert not posts(fixture)
    fixture.enabled = True
    assert exchange(fixture, request(item))["state"] == "unknown"
    assert not posts(fixture)


def test_real_fragment_codec_roundtrip_to_rest_and_back(fixture):
    outbound = request()
    incoming = FragmentAssembler(clock=lambda: 0)
    assembled = None
    for fragment in reversed(split_envelope(outbound.wire)):
        assembled = incoming.accept(b"\x04" * 32, fragment)
    assert assembled == outbound.wire
    response = fixture.bridge().handle(assembled, author=IDENTITY.agent_id)
    returning = FragmentAssembler(clock=lambda: 0)
    result_wire = None
    for fragment in split_envelope(response):
        result_wire = returning.accept(b"\x05" * 32, fragment)
    result = protocol.open_result(result_wire, IDENTITY, request=outbound, now=NOW,
                                  consume=lambda *args: True, access_check=lambda: True)
    assert result["state"] == "receipt" and result["acceptance_available"] is False


def test_no_private_sources_or_auth_packets_in_gateway_journal(fixture):
    item = proposal()
    exchange(fixture, request(item))
    encoded = json.dumps(fixture.records)
    assert item.content_hash in encoded
    for forbidden in ("X-Signature", "X-Nonce", "profile_auth", "photo_reference", "source_id", "private_key"):
        assert forbidden not in encoded
