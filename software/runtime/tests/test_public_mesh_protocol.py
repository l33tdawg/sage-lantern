"""Real local SDK signing, deterministic synthetic identities, no key files/network."""

import uuid

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.community_public import CommunityPublic
from sage_lantern import public_mesh_protocol as protocol


NOW = 1700000000
IDENTITY = AgentIdentity(SigningKey(b"\x01" * 32))
GATEWAY = SigningKey(b"\x02" * 32)
GATEWAY_KEY = bytes(GATEWAY.verify_key).hex()


def proposal(answer="A reusable derivative.", operation_id=None):
    return CommunityPublic(None, None, chain_id="commons", domain="public-domain", author=IDENTITY.agent_id).prepare(
        operation_id=operation_id or str(uuid.uuid4()), question="What is this?", answer=answer)


def request(value=None, **overrides):
    options = dict(request_id=str(uuid.uuid4()), now=NOW, access_check=lambda: True, approval_check=lambda item: True)
    options.update(overrides)
    return protocol.seal_submission(value or proposal(), IDENTITY, GATEWAY_KEY, **options)


def opened(outbound):
    return protocol.open_request(outbound.wire, GATEWAY, author=IDENTITY.agent_id, chain_id="commons", domain="public-domain", now=NOW)


def test_real_sdk_body_and_signature_compatible():
    outbound = request()
    payload = opened(outbound)
    client = SageClient("http://127.0.0.1:19101", IDENTITY, trust_env=False)
    client._client.close()
    captured = []
    def handler(incoming):
        captured.append(incoming.content)
        raise httpx.ReadTimeout("synthetic")
    client._client = httpx.Client(base_url="http://127.0.0.1:19101", transport=httpx.MockTransport(handler), trust_env=False)
    try:
        with pytest.raises(httpx.ReadTimeout):
            client.propose(content=outbound.proposal.content, memory_type="inference", domain_tag="public-domain", confidence=0.5, classification=0)
    finally:
        client._client.close()
    assert captured == [protocol.submit_body(outbound.proposal)]
    protocol.verify_http(payload["auth"], IDENTITY.agent_id, "POST", "/v1/memory/submit", captured[0], NOW)
    assert b"reusable derivative" not in outbound.wire
    assert len(outbound.wire) <= 4096 and not outbound.wire.startswith(b"LME1.")


@pytest.mark.parametrize("change", ["author", "chain", "domain", "key", "expiry", "tamper", "chat"])
def test_wrong_binding_or_stale_wire(change):
    outbound = request()
    options = dict(author=IDENTITY.agent_id, chain_id="commons", domain="public-domain", now=NOW)
    key, wire = GATEWAY, outbound.wire
    if change in {"author", "chain", "domain"}:
        options[change if change != "chain" else "chain_id"] = "a" * 64
    elif change == "key":
        key = SigningKey(b"\x03" * 32)
    elif change == "expiry":
        options["now"] = NOW + 120
    elif change == "tamper":
        wire = wire[:-1] + b"!"
    else:
        wire = b"LME1." + wire[5:]
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_request(wire, key, **options)


def test_approval_exact_and_oversize_refuses_without_truncation():
    item = proposal()
    seen = []
    outbound = request(item, approval_check=lambda value: seen.append(value) or value == item)
    assert seen == [item] and opened(outbound)["proposal"]["content"] == item.content
    with pytest.raises(protocol.PublicMeshError, match="not_approved"):
        request(item, approval_check=lambda value: False)
    with pytest.raises(protocol.PublicMeshError, match="oversize"):
        request(proposal("x" * 1200))


def test_http_auth_cannot_be_retargeted_even_with_signed_outer():
    outbound = request()
    payload = opened(outbound)
    payload["auth"] = IDENTITY.sign_request("POST", "/v1/memory/submit", b"different", timestamp=NOW)
    wire = protocol._seal(payload, IDENTITY._signing_key, GATEWAY_KEY)
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_request(wire, GATEWAY, author=IDENTITY.agent_id, chain_id="commons", domain="public-domain", now=NOW)


def test_response_correlates_nonce_and_requires_durable_consumption():
    outbound = request()
    payload = opened(outbound)
    wire = protocol.seal_result(payload, protocol.result_data("unknown"), GATEWAY, now=NOW)
    consumed = set()
    def consume(identifier, digest):
        key = identifier, digest
        if key in consumed:
            return False
        consumed.add(key)
        return True
    value = protocol.open_result(wire, IDENTITY, request=outbound, now=NOW, consume=consume, access_check=lambda: True)
    assert all(value[flag] is False for flag in protocol.FLAGS)
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_result(wire, IDENTITY, request=outbound, now=NOW, consume=consume, access_check=lambda: True)
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_result(wire, IDENTITY, request=request(outbound.proposal), now=NOW, consume=consume, access_check=lambda: True)


def test_gateway_cannot_assert_acceptance_or_extra_proof_fields():
    outbound = request()
    result = protocol.result_data("unknown")
    result["acceptance_available"] = True
    wire = protocol.seal_result(opened(outbound), result, GATEWAY, now=NOW)
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_result(wire, IDENTITY, request=outbound, now=NOW, consume=lambda *args: True, access_check=lambda: True)


def test_numeric_false_is_not_a_valid_security_flag():
    outbound = request()
    result = protocol.result_data("unknown")
    result["cache_eligible"] = 0
    wire = protocol.seal_result(opened(outbound), result, GATEWAY, now=NOW)
    with pytest.raises(protocol.PublicMeshError):
        protocol.open_result(wire, IDENTITY, request=outbound, now=NOW, consume=lambda *args: True, access_check=lambda: True)


def test_fresh_resigning_changes_request_not_approved_content():
    item = proposal()
    first = opened(request(item))
    second = opened(request(item))
    assert first["proposal"] == second["proposal"]
    assert first["auth"] != second["auth"]


@pytest.mark.parametrize("chain_size", [1, 128])
@pytest.mark.parametrize("state,status", [("unknown", None), ("unavailable", None), ("receipt", None),
    *[("observed", value) for value in ("proposed", "validated", "committed", "challenged", "deprecated")]])
def test_shared_response_bound_covers_real_signed_sealed_results(chain_size, state, status):
    from dataclasses import asdict
    from sage_lantern.community_public import PublicReceipt
    from sage_lantern.mesh_fragments import split_envelope
    item = CommunityPublic(None, None, chain_id="c" * chain_size, domain="public-domain", author=IDENTITY.agent_id).prepare(
        operation_id=str(uuid.uuid4()), question="Synthetic question", answer="Synthetic derivative")
    receipt = PublicReceipt(item.operation_id, item.content_hash, item.chain_id,
        "ffffffff-ffff-ffff-ffff-ffffffffffff", "f" * 64, protocol.MAX_INTEGER)
    now = protocol.MAX_INTEGER - 120
    outbound = protocol.seal_poll(item, receipt, IDENTITY, GATEWAY_KEY, request_id=str(uuid.uuid4()), now=now,
        access_check=lambda: True, approval_check=lambda item: True)
    payload = protocol.open_request(outbound.wire, GATEWAY, author=IDENTITY.agent_id,
        chain_id=item.chain_id, domain=item.domain, now=now)
    result = protocol.result_data(state, receipt=asdict(receipt) if state in ("receipt", "observed") else None,
        observed_status=status)
    wire = protocol.seal_result(payload, result, GATEWAY, now=protocol.MAX_INTEGER - 1)
    assert len(split_envelope(wire)) <= protocol.response_fragment_bound(item, gateway_key=GATEWAY_KEY)
    assert protocol.open_result(wire, IDENTITY, request=outbound, now=protocol.MAX_INTEGER - 1,
        consume=lambda identifier, digest: identifier == outbound.request_id and digest == outbound.digest,
        access_check=lambda: True) == result


def test_shared_response_bound_rejects_unvalidated_proposal_and_key():
    with pytest.raises(protocol.PublicMeshError):
        protocol.response_fragment_bound({}, gateway_key=GATEWAY_KEY)
    with pytest.raises(protocol.PublicMeshError):
        protocol.response_fragment_bound(proposal(), gateway_key="invalid")
