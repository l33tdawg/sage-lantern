from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from sage_lantern.community_public import CANONICAL_DISCLOSURE, CommunityPublic, CommunityPublicError, OnlineNodeTrust


AUTHOR = "a" * 64
OPERATION = "00000000-0000-4000-8000-000000000001"
MEMORY = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def setup():
    profile = dict(agent_id=AUTHOR, role="member", clearance=0, home_domain="community-a",
                   enrollment_status="active", registration_status="active",
                   approval_required=False, can_read=True, can_write=True)
    client = Mock()
    client.get_profile.return_value = profile
    client.propose.return_value = dict(memory_id=MEMORY, tx_hash="b" * 64, status="proposed",
                                       committed=True, committed_height=10)
    journal = Mock()
    journal.reserve.return_value = True
    adapter = CommunityPublic(client, journal, chain_id="public-chain", domain="community-a", author=AUTHOR)
    proposal = adapter.prepare(operation_id=OPERATION, question="What is a tree?", answer="A woody plant.")
    return SimpleNamespace(client=client, journal=journal, adapter=adapter, proposal=proposal, profile=profile)


def test_exact_public_submission_pending_not_commit_claim(setup):
    result = setup.adapter.submit(setup.proposal, access_check=lambda: True)
    assert result.state == "pending"
    assert not result.acceptance_available and not result.chain_verified
    setup.client.propose.assert_called_once_with(content=setup.proposal.content, memory_type="inference",
                                                domain_tag="community-a", confidence=0.5, classification=0)
    setup.journal.reserve.assert_called_once_with(OPERATION, setup.proposal.content_hash)
    setup.journal.complete.assert_called_once_with(OPERATION, result.receipt)
    body = json.loads(setup.proposal.content)
    assert body["chain_id"] == "public-chain" and body["source_class"] == "generated-unverified"


@pytest.mark.parametrize("field,value", [("role", "root"), ("role", "admin"), ("clearance", 1),
    ("clearance", False), ("home_domain", "private"), ("agent_id", "c" * 64),
    ("enrollment_status", "pending_review"), ("registration_status", None),
    ("approval_required", True), ("can_write", 1), ("can_read", False)])
def test_no_private_or_unapproved_identity(setup, field, value):
    setup.profile[field] = value
    with pytest.raises(CommunityPublicError):
        setup.adapter.submit(setup.proposal, access_check=lambda: True)
    setup.journal.reserve.assert_not_called()
    setup.client.propose.assert_not_called()


def test_reservation_blocks_retry_after_unknown(setup):
    setup.client.propose.side_effect = RuntimeError("SECRET BODY")
    with pytest.raises(CommunityPublicError, match="^submission_unconfirmed$"):
        setup.adapter.submit(setup.proposal, access_check=lambda: True)
    setup.journal.reserve.return_value = False
    assert setup.adapter.submit(setup.proposal, access_check=lambda: True).reason == "reconcile_required"
    assert setup.client.propose.call_count == 1
    setup.journal.complete.assert_not_called()


def test_last_moment_consent_fence(setup):
    access = Mock(side_effect=[True, False])
    assert setup.adapter.submit(setup.proposal, access_check=access).reason == "admission_cancelled"
    setup.client.propose.assert_not_called()


@pytest.mark.parametrize("status", ["proposed", "validated", "committed", "challenged", "deprecated"])
def test_every_sdk_status_remains_unverified(setup, status):
    receipt = setup.adapter.submit(setup.proposal, access_check=lambda: True).receipt
    setup.client.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=AUTHOR,
        domain_tag="community-a", memory_type="inference", classification=0,
        content=setup.proposal.content, content_hash=setup.proposal.content_hash, status=status)
    result = setup.adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert result.content_bound and result.observed_status == status
    assert result.state == "pending" and result.reason == "acceptance_proof_unavailable"
    assert not result.chain_verified and not result.acceptance_available


@pytest.mark.parametrize("field,value", [("memory_id", OPERATION), ("submitting_agent", "f" * 64),
    ("domain_tag", "private"), ("memory_type", "fact"), ("classification", False),
    ("classification", 1), ("content", "substituted"), ("content_hash", "0" * 64)])
def test_content_binding_mismatch_no_acceptance(setup, field, value):
    receipt = setup.adapter.submit(setup.proposal, access_check=lambda: True).receipt
    record = dict(memory_id=MEMORY, submitting_agent=AUTHOR, domain_tag="community-a",
                  memory_type="inference", classification=0, content=setup.proposal.content,
                  content_hash=setup.proposal.content_hash, status="committed")
    record[field] = value
    setup.client.get_memory.return_value = record
    result = setup.adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert not result.content_bound and result.reason == "content_binding_failed"


def test_tampered_proposal_and_wrong_chain_receipt(setup):
    with pytest.raises(CommunityPublicError):
        setup.adapter.submit(replace(setup.proposal, chain_id="other"), access_check=lambda: True)
    receipt = setup.adapter.submit(setup.proposal, access_check=lambda: True).receipt
    with pytest.raises(CommunityPublicError):
        setup.adapter.poll(setup.proposal, replace(receipt, intended_chain_id="other"), access_check=lambda: True)
    setup.client.get_memory.assert_not_called()


def test_sdk_models_and_errors_do_not_escape(setup):
    profile = Mock()
    profile.model_dump.return_value = setup.profile
    setup.client.get_profile.return_value = profile
    receipt = setup.adapter.submit(setup.proposal, access_check=lambda: True).receipt
    setup.client.get_memory.side_effect = RuntimeError("SECRET")
    result = setup.adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert result.reason == "status_unavailable" and "SECRET" not in repr(result)


def trusted(setup, status="committed"):
    receipt = setup.adapter.submit(setup.proposal, access_check=lambda: True).receipt
    evidence = OnlineNodeTrust(setup.client, "https://community.example", "public-chain",
                               "reviewed-revision", True, CANONICAL_DISCLOSURE)
    verifier = Mock(return_value=evidence)
    adapter = CommunityPublic(setup.client, setup.journal, chain_id="public-chain",
                              domain="community-a", author=AUTHOR, online_verifier=verifier)
    setup.client.get_memory.return_value = dict(memory_id=MEMORY, submitting_agent=AUTHOR,
        domain_tag="community-a", memory_type="inference", classification=0,
        content=setup.proposal.content, content_hash=setup.proposal.content_hash, status=status)
    return adapter, receipt, verifier, evidence


@pytest.mark.parametrize("status,state", [("proposed", "pending"), ("validated", "pending"),
    ("committed", "accepted"), ("challenged", "challenged"), ("deprecated", "deprecated")])
def test_online_status_not_offline_or_cache_proof(setup, status, state):
    adapter, receipt, verifier, evidence = trusted(setup, status)
    access = Mock(return_value=True)
    result = adapter.poll(setup.proposal, receipt, access_check=access)
    assert result.state == state and result.acceptance_available and result.chain_verified
    assert result.trust_basis == "trusted_node_online"
    assert not result.offline_proof and not result.cache_eligible
    assert verifier.call_count == 2 and access.call_count == 3


@pytest.mark.parametrize("change", ["bool", "dict", "client", "chain_id", "transport_authenticated",
    "canonical_disclosure", "node_revision", "endpoint"])
def test_invalid_or_changed_trust_never_accepts(setup, change):
    adapter, receipt, verifier, evidence = trusted(setup)
    altered = {"bool": True, "dict": {}, "client": replace(evidence, client=Mock()),
        "chain_id": replace(evidence, chain_id="elsewhere"),
        "transport_authenticated": replace(evidence, transport_authenticated=1),
        "canonical_disclosure": replace(evidence, canonical_disclosure="unknown"),
        "node_revision": replace(evidence, node_revision="different"),
        "endpoint": replace(evidence, endpoint="https://other.example")}[change]
    verifier.side_effect = [evidence, altered]
    result = adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert result.state == "pending" and not result.acceptance_available


@pytest.mark.parametrize("stage", [0, 1, 2])
def test_access_revocation_around_poll(setup, stage):
    adapter, receipt, verifier, evidence = trusted(setup)
    calls = 0

    def access():
        nonlocal calls
        calls += 1
        return calls <= stage

    with pytest.raises(CommunityPublicError, match="observation_cancelled"):
        adapter.poll(setup.proposal, receipt, access_check=access)
    if stage < 2:
        setup.client.get_memory.assert_not_called()


def test_online_content_mismatch_and_verifier_failure(setup):
    adapter, receipt, verifier, evidence = trusted(setup)
    setup.client.get_memory.return_value["content"] = "substitution"
    result = adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert result.reason == "content_binding_failed" and not result.acceptance_available
    verifier.side_effect = RuntimeError("SECRET")
    result = adapter.poll(setup.proposal, receipt, access_check=lambda: True)
    assert result.reason == "status_unavailable" and "SECRET" not in repr(result)


def test_access_exception_is_sanitized(setup):
    adapter, receipt, verifier, evidence = trusted(setup)
    with pytest.raises(CommunityPublicError, match="^observation_cancelled$"):
        adapter.poll(setup.proposal, receipt, access_check=Mock(side_effect=RuntimeError("SECRET")))
