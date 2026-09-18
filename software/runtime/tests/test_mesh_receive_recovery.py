"""Synthetic SDK transport; no device, radio, serial or native file access."""

import hashlib
import json
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.mesh_receive_recovery import (SCHEMA, ReceiveRecovery, ReceiveRecoveryError,
                                                audit_identifier, canonical)
from sage_lantern.mesh_receiver import _payload
from sage_lantern.workflow_store import WorkflowStore


SLOT = "00000000-0000-4000-8000-000000000001"
OPERATION = "00000000-0000-4000-8000-000000000002"


@pytest.fixture
def rig():
    identity = AgentIdentity(SigningKey(bytes(range(32))))
    fixture = SimpleNamespace(rows={}, wrote=[])

    def handler(request):
        assert request.headers["X-Agent-ID"] == identity.agent_id
        assert request.headers["X-Signature"]
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", instance_id="b" * 64,
                agent_id=identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation="1", stable=True, canonical_send_idempotency=True,
                encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            after = request.url.params.get("after")
            limit = int(request.url.params.get("limit", "20"))
            ids = [identifier for identifier in sorted(fixture.rows)
                   if after is None or identifier > after]
            page = ids[:limit]
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1",
                items=[fixture.rows[identifier] for identifier in page],
                next_after=page[-1] if len(ids) > limit else None, has_more=len(ids) > limit))
        record_id = request.url.path.rsplit("/", 1)[-1]
        if request.method == "GET":
            row = fixture.rows.get(record_id)
            return httpx.Response(200 if row else 404, json=row or {})
        body = json.loads(request.content)
        current = fixture.rows[record_id]["revision"] if record_id in fixture.rows else 0
        if body["expected_revision"] != current:
            return httpx.Response(409, json={"error": "conflict"})
        fixture.wrote.append(record_id)
        row = dict(schema="sage.workflow-journal.v1", agent_id=identity.agent_id,
                   record_id=record_id, revision=current + 1, kind=body["kind"],
                   payload=body["payload"], trust="untrusted_auxiliary")
        fixture.rows[record_id] = row
        return httpx.Response(200, json=row)

    endpoint = "http://127.0.0.1:19101"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
        follow_redirects=False, transport=httpx.MockTransport(handler))
    probe = MessageStorageProbe(client, actor=RecipientID("home", identity.agent_id), endpoint=endpoint)
    store = WorkflowStore(client, storage=probe)
    fixture.store = store
    fixture.recovery = ReceiveRecovery(store, slot_id=SLOT, access_check=lambda: True)
    fixture.put = lambda record_id, payload: store.put(record_id, "mesh_inbound", 0, payload,
                                                      access_check=lambda: True)
    yield fixture
    client._client.close()


def interrupted():
    return _payload("interrupted", OPERATION)


def test_state_reports_the_interrupted_capture_without_writing(rig):
    with pytest.raises(ReceiveRecoveryError, match="receive_slot_missing"):
        rig.recovery.state()
    rig.put(SLOT, interrupted())
    before = dict(rig.rows)
    view = rig.recovery.state()
    assert view == {"slot_id": SLOT, "state": "interrupted", "operation_id": OPERATION, "revision": 1,
                    "recoveries": []}
    assert rig.rows == before and rig.wrote == [SLOT]
    rig.recovery.recover(operation_id=OPERATION, reason="bench")
    after = rig.recovery.state()
    assert after["state"] == "idle" and after["operation_id"] is None and after["revision"] == 2
    assert len(after["recoveries"]) == 1
    entry = after["recoveries"][0]
    assert entry["audit_id"] == audit_identifier(SLOT, OPERATION) and entry["operation_id"] == OPERATION
    assert entry["reason"] == "bench" and isinstance(entry["recovered_at"], int)
    assert entry["original_sha256"] == hashlib.sha256(canonical(interrupted())).hexdigest()


def test_recovery_preserves_the_audit_then_frees_the_same_slot(rig):
    rig.put(SLOT, interrupted())
    result = rig.recovery.recover(operation_id=OPERATION, reason="bench test interrupt",
                                  clock=lambda: 1789000000)
    identifier = audit_identifier(SLOT, OPERATION)
    audit = rig.rows[identifier]["payload"]
    assert audit == {"schema": SCHEMA, "slot_id": SLOT, "operation_id": OPERATION,
                     "original_revision": 1,
                     "original_sha256": hashlib.sha256(canonical(interrupted())).hexdigest(),
                     "recovered_at": 1789000000, "reason": "bench test interrupt",
                     "outcome": "interrupted_receive_abandoned",
                     "replayed": False, "delivery_claim": False}
    assert result == {"slot_id": SLOT, "state": "idle", "operation_id": OPERATION,
                      "audit_id": identifier,
                      "audit_sha256": hashlib.sha256(canonical(audit)).hexdigest(),
                      "already_recovered": False, "replayed": False, "delivery_claim": False}
    # The audit is durable before the slot changes, and the slot UUID is kept.
    assert rig.wrote == [SLOT, identifier, SLOT]
    assert rig.rows[SLOT]["payload"] == _payload("idle")
    assert rig.rows[SLOT]["record_id"] == SLOT


def test_repeated_recovery_proves_the_existing_audit_instead_of_rewriting(rig):
    rig.put(SLOT, interrupted())
    first = rig.recovery.recover(operation_id=OPERATION, reason="first")
    written = list(rig.wrote)
    second = rig.recovery.recover(operation_id=OPERATION, reason="second")
    assert second == dict(first, already_recovered=True)
    assert rig.wrote == written


def test_unsettled_or_mismatched_slots_are_refused(rig):
    with pytest.raises(ReceiveRecoveryError, match="receive_slot_missing"):
        rig.recovery.recover(operation_id=OPERATION, reason="no slot")
    rig.put(SLOT, _payload("idle"))
    with pytest.raises(ReceiveRecoveryError, match="receive_not_interrupted"):
        rig.recovery.recover(operation_id=OPERATION, reason="idle slot")
    rig.rows[SLOT]["payload"] = _payload("receiving", OPERATION)
    with pytest.raises(ReceiveRecoveryError, match="receive_not_interrupted"):
        rig.recovery.recover(operation_id=OPERATION, reason="in flight")
    rig.rows[SLOT]["payload"] = interrupted()
    with pytest.raises(ReceiveRecoveryError, match="receive_not_interrupted"):
        rig.recovery.recover(operation_id="00000000-0000-4000-8000-000000000003", reason="other")
    assert rig.rows[SLOT]["payload"] == interrupted()


def test_audit_is_not_accepted_without_its_binding(rig):
    rig.put(SLOT, interrupted())
    other = "00000000-0000-4000-8000-000000000004"
    rig.put(audit_identifier(SLOT, other), {"schema": SCHEMA, "slot_id": SLOT, "operation_id": other,
                                            "original_revision": 1, "original_sha256": "c" * 64,
                                            "recovered_at": 1, "reason": "foreign",
                                            "outcome": "interrupted_receive_abandoned",
                                            "replayed": False, "delivery_claim": False})
    with pytest.raises(ReceiveRecoveryError, match="audit_conflict"):
        # Same slot, different operation: the interrupted row keeps its own binding.
        rig.rows[audit_identifier(SLOT, OPERATION)] = rig.rows[audit_identifier(SLOT, other)]
        rig.rows[audit_identifier(SLOT, OPERATION)]["record_id"] = audit_identifier(SLOT, OPERATION)
        rig.recovery.recover(operation_id=OPERATION, reason="mismatch")
    assert rig.rows[SLOT]["payload"] == interrupted()
