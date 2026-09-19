"""Durable custodian service against the real WorkflowStore + MockTransport."""

import json
from types import SimpleNamespace

import httpx
from nacl.signing import SigningKey
import pytest
from sage_sdk.auth import AgentIdentity
from sage_sdk.client import SageClient

from sage_lantern.mesh_custody import parse_custody, wrap_custody
from sage_lantern.mesh_custodian import CustodianError, MeshCustodian, record_id, validate_status
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import WorkflowStore


KEY = SigningKey(bytes(range(32, 64)))
RECIPIENT = bytes.fromhex("112233445566")
OTHER = bytes.fromhex("aabbccddeeff")
DEPOSITOR = "peer-chain:" + "ab" * 32
OTHER_DEPOSITOR = "other-chain:" + "cd" * 32
ENVELOPE = "LME1." + "A" * 64


def deposit(now=1000, message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e7f",
            recipient=RECIPIENT, envelope=ENVELOPE):
    return parse_custody(wrap_custody(recipient_prefix=recipient, message_id=message_id,
                                      envelope_wire=envelope, expires_at=now + 3600,
                                      envelope_expires_at=now + 3600, now=now))


@pytest.fixture
def rig():
    fixture = SimpleNamespace(rows={}, live=True)
    identity = AgentIdentity(KEY)

    def handler(request):
        assert request.headers["X-Agent-ID"] == identity.agent_id and request.headers["X-Signature"]
        if request.url.path == "/v1/messages/storage":
            return httpx.Response(200, json=dict(schema="sage.message-storage.v1", instance_id="c" * 64,
                agent_id=identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation="1", stable=True, canonical_send_idempotency=True,
                encrypted_send_admission=True))
        if request.url.path == "/v1/workflows":
            if request.method == "GET":
                after = request.url.params.get("after")
                limit = int(request.url.params["limit"])
            else:
                body = json.loads(request.content)
                after, limit = body["after"], body["limit"]
            items = sorted(fixture.rows.values(), key=lambda row: row["record_id"])
            if after is not None:
                items = [item for item in items if item["record_id"] > after]
            page, has_more = items[:limit], len(items) > limit
            return httpx.Response(200, json=dict(schema="sage.workflow-journal.v1", items=page,
                next_after=page[-1]["record_id"] if has_more else None, has_more=has_more))
        identifier = request.url.path.rsplit("/", 1)[1]
        if request.method == "GET":
            return httpx.Response(200 if identifier in fixture.rows else 404, json=fixture.rows.get(identifier, {}))
        body = json.loads(request.content)
        revision = fixture.rows.get(identifier, {}).get("revision", 0)
        if revision != body["expected_revision"]:
            return httpx.Response(409, json={})
        fixture.rows[identifier] = dict(schema="sage.workflow-journal.v1", agent_id=identity.agent_id,
            record_id=identifier, revision=revision + 1, kind=body["kind"], payload=body["payload"],
            trust="untrusted_auxiliary")
        return httpx.Response(200, json=fixture.rows[identifier])

    endpoint = "http://127.0.0.1:19200"
    client = SageClient(endpoint, identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
        follow_redirects=False, transport=httpx.MockTransport(handler))
    probe = MessageStorageProbe(client, actor=RecipientID("custodian", identity.agent_id), endpoint=endpoint)
    fixture.store = WorkflowStore(client, storage=probe)
    fixture.new = lambda **bounds: MeshCustodian(fixture.store, access_check=lambda: fixture.live,
                                                 depositors=(DEPOSITOR,), clock=lambda: 1000, **bounds)
    yield fixture
    client._client.close()


def test_admit_dedupe_pending_and_stats(rig):
    custodian = rig.new()
    first = deposit()
    assert custodian.admit(first, source=DEPOSITOR, operation_id="00000000-0000-4000-8000-000000000001") == "stored"
    assert custodian.admit(first, source=DEPOSITOR) == "duplicate"
    assert custodian.pending_for(RECIPIENT, now=1000) == (first,)
    stats = validate_status(custodian.stats(now=1000))
    assert stats == dict(schema="sage.lantern.mesh-custody-status.v1", enabled=True, held=1,
                         bytes=len(ENVELOPE), recipients=1, oldest_expires_at=4600,
                         delivered=0, depositors=1)
    assert record_id(RECIPIENT, first.message_digest) in rig.rows


def test_refuses_unknown_depositor_expired_and_equivocation(rig):
    custodian = rig.new()
    assert custodian.admit(deposit(), source=OTHER_DEPOSITOR) == "refused"
    assert custodian.admit(deposit(), source=DEPOSITOR, now=5000) == "expired"
    assert custodian.admit(deposit(), source=DEPOSITOR) == "stored"
    different = deposit(envelope="LME1." + "B" * 64)
    assert custodian.admit(different, source=DEPOSITOR) == "rejected"


def test_confirm_delivered_and_quota(rig):
    custodian = rig.new(max_per_recipient=1)
    first, second = deposit(), deposit(message_id="6f0d5f4a-9a2b-4c3d-8e1f-2a3b4c5d6e70")
    assert custodian.admit(first, source=DEPOSITOR) == "stored"
    assert custodian.admit(second, source=DEPOSITOR) == "rejected"
    assert custodian.confirm_delivered(RECIPIENT, first.message_digest, now=1100) is True
    assert custodian.confirm_delivered(RECIPIENT, first.message_digest, now=1100) is False
    assert custodian.pending_for(RECIPIENT, now=1100) == ()
    stats = custodian.stats(now=1100)
    assert stats["held"] == 0 and stats["delivered"] == 1
    assert custodian.admit(second, source=DEPOSITOR) == "stored"


def test_tampered_row_fails_closed(rig):
    custodian = rig.new()
    item = deposit()
    assert custodian.admit(item, source=DEPOSITOR) == "stored"
    identifier = record_id(RECIPIENT, item.message_digest)
    rig.rows[identifier]["payload"]["wire"] = "SLC1." + "A" * 40
    with pytest.raises(CustodianError):
        custodian.stats()


def test_status_schema_is_strict():
    good = dict(schema="sage.lantern.mesh-custody-status.v1", enabled=True, held=0, bytes=0,
                recipients=0, oldest_expires_at=None, delivered=0, depositors=0)
    assert validate_status(good) == good
    with pytest.raises(CustodianError):
        validate_status({**good, "held": -1})
    with pytest.raises(CustodianError):
        validate_status({**good, "wire": "leak"})


def test_handover_progress_resumes_and_is_bounded(rig):
    custodian = rig.new()
    item = deposit()
    assert custodian.admit(item, source=DEPOSITOR) == "stored"
    assert custodian.handover_for(RECIPIENT, now=1000) == ((item, 0, 0),)
    assert custodian.note_handover(RECIPIENT, item.message_digest, next_index=2, complete=False, now=1000) is True
    assert custodian.handover_for(RECIPIENT, now=1001) == ((item, 2, 0),)
    assert custodian.note_handover(RECIPIENT, item.message_digest, next_index=3, complete=True, now=1002) is True
    assert custodian.handover_for(RECIPIENT, now=1002, min_interval=300) == ()
    resumed = custodian.handover_for(RECIPIENT, now=1302, min_interval=300)
    assert resumed[0][1] == 0 and resumed[0][2] == 1
    assert custodian.confirm_delivered(RECIPIENT, item.message_digest, now=1400) is True
    assert custodian.handover_for(RECIPIENT, now=1400) == ()
    assert custodian.note_handover(RECIPIENT, item.message_digest, next_index=1, complete=False, now=1400) is False
