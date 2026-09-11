import copy
from dataclasses import replace

import pytest

from sage_lantern.mesh_envelope import Peer, open_envelope
from sage_lantern.mesh_receipt_sender import SCHEMA
from sage_lantern.workflow_store import WorkflowOutcomeUnknown
from test_mesh_background import background, incoming, valid_sender_radio
from test_community_mesh_runtime import integrated, factory


def job(state, operation, callback):
    with state.provider._authority.receive_grant(state.runtime.actor, operation,
            lambda: state.consent, state.lease_factory, lambda: state.authorized) as (context, storage):
        def invoke(device):
            state.runtime._local.receive_stop = state.runtime._receive_stop
            with state.runtime._context(context, operation):
                state.runtime._local.receive_storage = storage
                state.runtime._local.inventory = device.get_contacts(authorize=state.runtime._access)
                return callback(device)
        return state.runtime._submit(invoke).result(timeout=state.config.operation_timeout)


def receive_all(state):
    while state.pending:
        job(state, "receive", state.runtime._poll)


def receipt_rows(state):
    return [row for row in state.rows.values() if row["payload"].get("body", {}).get("schema") == SCHEMA]


@pytest.fixture
def enabled(background):
    background.consent = replace(background.consent, delivery_receipts_enabled=True)
    return background


def test_queue_after_signed_durable_admission_never_claims_read_or_transmits(enabled):
    state = enabled
    local_id = incoming(state)
    receive_all(state)
    row, = receipt_rows(state)
    body, approved = state.runtime.receipts._verified(row)
    assert body["state"] == "queued" and approved["local_id"] == local_id
    original = state.rows[local_id]["payload"]["attestation"]["body"]
    assert approved["issued_at"] == original["admitted_at"]
    assert approved["original_id"] == original["remote_id"]
    assert approved["original_digest"] == original["envelope_digest"]
    assert approved["render_ack_binding"] == "" and approved["kind"] == "delivery"
    receipt = open_envelope(approved["wire"], state.remote._signing_key,
        expected_sender=state.runtime.receipts.sender,
        expected_recipient=Peer("remote", bytes.fromhex(state.remote.agent_id)), now=approved["issued_at"])
    assert receipt.kind == "delivery" and receipt.original_id == original["remote_id"]
    assert not state.transmits and not state.held
    assert state.rows[state.config.receiver_slot]["payload"]["state"] == "idle"


def test_disabled_default_does_not_queue_receipt(background):
    incoming(background)
    receive_all(background)
    assert not receipt_rows(background) and not background.transmits


def test_partial_and_duplicate_do_not_create_new_receipt_or_replace_wire(enabled):
    state = enabled
    local_id = incoming(state)
    frames = list(state.pending)
    assert len(frames) > 1
    job(state, "receive", state.runtime._poll)
    assert local_id not in state.rows and not receipt_rows(state)
    receive_all(state)
    original = copy.deepcopy(receipt_rows(state))
    state.pending.extend(frames)
    receive_all(state)
    assert receipt_rows(state) == original and not state.transmits


@pytest.mark.parametrize("change", ["lock", "drain", "consent", "lease"])
def test_revocation_after_durable_admission_retains_capture_without_receipt(enabled, monkeypatch, change):
    state = enabled
    state.consent = replace(state.consent, receive_while_locked=False, dispatch_while_locked=False)
    local_id = incoming(state)
    ingest = state.runtime.inbox.ingest
    def revoke(*args):
        result = ingest(*args)
        if result.status == "admitted":
            assert local_id in state.rows
            if change == "lock":
                state.authorized = False
            elif change == "drain":
                state.active = False
            elif change == "consent":
                state.consent = replace(state.consent, delivery_receipts_enabled=False)
            else:
                state.lease_live = False
        return result
    monkeypatch.setattr(state.runtime.inbox, "ingest", revoke)
    with pytest.raises(Exception):
        receive_all(state)
    assert local_id in state.rows and not receipt_rows(state) and not state.transmits
    assert state.rows[state.config.receiver_slot]["payload"]["state"] == "captured"


@pytest.mark.parametrize("committed", [False, True])
def test_failed_or_unknown_receipt_write_preserves_inbox_and_capture(enabled, monkeypatch, committed):
    state = enabled
    local_id = incoming(state)
    put = state.runtime.store.put
    def failure(identifier, kind, revision, payload, **kwargs):
        if payload.get("body", {}).get("schema") == SCHEMA:
            assert local_id in state.rows
            if committed:
                put(identifier, kind, revision, payload, **kwargs)
                raise WorkflowOutcomeUnknown()
            raise RuntimeError("synthetic failure")
        return put(identifier, kind, revision, payload, **kwargs)
    monkeypatch.setattr(state.runtime.store, "put", failure)
    with pytest.raises(Exception):
        receive_all(state)
    assert local_id in state.rows and len(receipt_rows(state)) == int(committed)
    assert state.rows[state.config.receiver_slot]["payload"]["state"] == "captured"
    assert not state.transmits
    if committed:
        assert state.runtime.receipts._uncertain


def test_changed_consent_blocks_dispatch_and_preserves_queue(enabled):
    state = enabled
    incoming(state)
    receive_all(state)
    before = copy.deepcopy(receipt_rows(state))
    state.consent = replace(state.consent, revision="c" * 64)
    with pytest.raises(Exception):
        job(state, "background_dispatch", state.runtime._dispatch_pending)
    assert receipt_rows(state) == before and not state.transmits


def test_queue_when_receive_locked_consented_but_dispatch_waits_for_unlock(enabled):
    state = enabled
    state.consent = replace(state.consent, dispatch_while_locked=False)
    state.authorized = False
    incoming(state)
    receive_all(state)
    assert len(receipt_rows(state)) == 1
    with pytest.raises(Exception):
        job(state, "background_dispatch", state.runtime._dispatch_pending)
    assert not state.transmits
    state.authorized = True
    job(state, "background_dispatch", state.runtime._dispatch_pending)
    assert len(state.transmits) == 1


def test_delivery_policy_cannot_authorize_read_or_retry_or_expired_grant(enabled):
    state = enabled
    incoming(state)
    receive_all(state)
    row, = receipt_rows(state)
    _, approved = state.runtime.receipts._verified(row)
    action = state.runtime.receipts._action(approved, "dispatch")
    authority = state.provider._authority
    with authority.receive_grant(state.runtime.actor, "background_dispatch",
            lambda: state.consent, state.lease_factory, lambda: state.authorized) as (context, storage):
        assert authority.receipt_policy(context, action).render_ack_binding == ""
        assert authority.receipt_policy(context, replace(action, kind="read")) is None
        assert authority.receipt_policy(context, replace(action, operation="retry", decision_binding="retry")) is None
        assert authority.receipt_policy(replace(context), action) is None
    assert authority.receipt_policy(context, action) is None


def test_duplicate_after_complete_serial_dispatch_does_not_retransmit(enabled):
    state = enabled
    incoming(state)
    frames = list(state.pending)
    receive_all(state)
    for _ in range(64):
        row, = receipt_rows(state)
        if row["payload"]["body"]["state"] == "serial_accepted":
            break
        job(state, "background_dispatch", state.runtime._dispatch_pending)
    assert row["payload"]["body"]["state"] == "serial_accepted"
    before = copy.deepcopy(receipt_rows(state))
    transmitted = len(state.transmits)
    state.pending.extend(frames)
    receive_all(state)
    job(state, "background_dispatch", state.runtime._dispatch_pending)
    assert receipt_rows(state) == before and len(state.transmits) == transmitted


def test_failed_capture_clear_keeps_queued_receipt_and_same_identity(enabled, monkeypatch):
    state = enabled
    local_id = incoming(state)
    write = state.runtime.receiver._write
    def fail_clear(revision, payload):
        if payload["state"] == "idle" and receipt_rows(state):
            raise RuntimeError("synthetic clear failure")
        return write(revision, payload)
    monkeypatch.setattr(state.runtime.receiver, "_write", fail_clear)
    with pytest.raises(Exception):
        receive_all(state)
    assert local_id in state.rows and len(receipt_rows(state)) == 1
    assert state.rows[state.config.receiver_slot]["payload"]["state"] == "captured"
    assert not state.transmits


def test_unknown_dispatch_is_not_requeued_by_duplicate_message(enabled, monkeypatch):
    state = enabled
    incoming(state)
    frames = list(state.pending)
    receive_all(state)
    original = state.device.send_text
    def unknown(**kwargs):
        original(**kwargs)
        raise RuntimeError("synthetic lost dispatch result")
    monkeypatch.setattr(state.device, "send_text", unknown)
    with pytest.raises(Exception):
        job(state, "background_dispatch", state.runtime._dispatch_pending)
    row, = receipt_rows(state)
    assert row["payload"]["body"]["state"] == "dispatching"
    before = copy.deepcopy(receipt_rows(state))
    capture_before = copy.deepcopy(state.rows[state.config.receiver_slot])
    transmitted = len(state.transmits)
    state.pending.extend(frames)
    with pytest.raises(Exception):
        receive_all(state)
    assert receipt_rows(state) == before and len(state.transmits) == transmitted
    assert state.rows[state.config.receiver_slot] == capture_before
    assert list(state.pending) == frames


@pytest.mark.parametrize("kind,status", [("read", "admitted"), ("delivery", "duplicate"), (None, "partial"), (None, "rejected")])
def test_receipt_or_partial_input_never_generates_receipt(kind, status):
    from sage_lantern.mesh_delivery_receipts import MeshDeliveryReceipts
    from sage_lantern.mesh_inbox import IngestResult
    MeshDeliveryReceipts(None, None).after_admission(IngestResult(status, envelope_kind=kind))
