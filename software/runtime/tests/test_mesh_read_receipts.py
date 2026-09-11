"""Consent/render tests on real inbox, WorkflowStore and sender; synthetic SDK IO."""

from dataclasses import replace
import json
import threading

import httpx
import pytest
from sage_sdk.auth import AgentIdentity

from sage_lantern.mesh_read_receipts import MeshReadReceipts, ReadReceiptError, ReadReceiptSession, MAX_CHALLENGES, SCHEMA
from sage_lantern.mesh_receipt_sender import ReceiptAction
from sage_lantern.message_contacts import RecipientID
from test_mesh_inbox import rig, RECIPIENT, KEY
from test_mesh_receipt_sender import sender, RADIO, device_for


@pytest.fixture
def consent(sender):
    owner = RecipientID(RECIPIENT.chain_id, RECIPIENT.public_key.hex())
    session = ReadReceiptSession(owner, "root-epoch-1", "a" * 64)
    state = {"epoch": session.epoch, "allowed": True, "calls": []}
    original_transport = sender.rig.store.client._client._transport
    def storage(incoming):
        if incoming.method != "PUT":
            return original_transport.handle_request(incoming)
        data = json.loads(incoming.content)
        if data.get("payload", {}).get("body", {}).get("schema") != SCHEMA:
            return original_transport.handle_request(incoming)
        identifier = incoming.url.path.rsplit("/", 1)[1]
        with sender.lock:
            sender.rig.puts += 1
            revision = sender.rig.rows.get(identifier, {}).get("revision", 0)
            if revision != data["expected_revision"]:
                return httpx.Response(409, json={})
            row = dict(schema="sage.workflow-journal.v1", agent_id=owner.agent_id, record_id=identifier,
                       revision=revision + 1, kind=data["kind"], payload=data["payload"], trust="untrusted_auxiliary")
            sender.rig.rows[identifier] = row
            if sender.rig.after_put:
                sender.rig.after_put()
            if sender.rig.failure == (sender.rig.puts, "after"):
                raise httpx.ReadTimeout("synthetic persisted then timeout")
            return httpx.Response(200, json=row)
    sender.rig.store.client._client._transport = httpx.MockTransport(storage)
    def authorize(current, operation):
        state["calls"].append(operation)
        return state["allowed"] and current == session and current.epoch == state["epoch"]
    def create():
        return MeshReadReceipts(sender.rig.store, AgentIdentity(KEY), inbox=sender.rig.inbox, owner=owner,
                                authorize=authorize, clock=lambda: sender.rig.now)
    helper = create()
    return sender, session, state, helper, create


def challenge(consent):
    sender, session, _, helper, _ = consent
    return helper.begin_render(session, local_id=sender.local_id, radio_public_key=RADIO)


def approve(consent):
    _, session, _, helper, _ = consent
    pending = challenge(consent)
    return helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)


def test_fetch_and_begin_render_never_write_or_send(consent):
    sender, session, _, helper, _ = consent
    before = json.dumps(sender.rig.rows, sort_keys=True)
    helper.inbox.historical_read(sender.local_id)
    pending = challenge(consent)
    assert json.dumps(sender.rig.rows, sort_keys=True) == before
    assert pending.expires_at == sender.rig.now + 60
    assert helper.close() is True


@pytest.mark.parametrize("rendered,opt_in", [(False, True), (True, False), (1, True), (True, 1), (None, True)])
def test_explicit_both_required(consent, rendered, opt_in):
    sender, session, _, helper, _ = consent
    pending = challenge(consent)
    before = len(sender.rig.rows)
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=rendered, share_read_receipt=opt_in)
    assert len(sender.rig.rows) == before


def test_actual_sender_uses_exact_durable_consent_and_render_bindings(consent):
    sender, session, _, helper, create = consent
    intent = approve(consent)
    restarted = create()
    assert restarted.recover(session, local_id=sender.local_id) == intent
    sender.sender.policy = lambda action: restarted.permit(session, action)
    progress = sender.sender.queue(local_id=intent.local_id, kind="read", radio_public_key=intent.radio_public_key,
                                   issued_at=intent.issued_at, expires_at=intent.expires_at, access_check=sender.access)
    assert progress.receipt_id == intent.receipt_id and progress.state == "queued"
    approved = sender.approved(intent.receipt_id)
    assert approved["privacy_binding"] == intent.privacy_binding
    assert approved["render_ack_binding"] == intent.render_ack_binding
    assert "private synthetic" not in json.dumps(sender.rig.rows[helper._slot(sender.local_id)])
    device = device_for(sender, intent.receipt_id)
    sent = sender.sender.dispatch_one(intent.receipt_id, device=device, radio_timestamp=500, access_check=sender.access)
    assert sent.next_fragment == 1 and sent.state == "queued"


def test_forged_or_wrong_original_binding_denied(consent):
    sender, session, _, helper, _ = consent
    intent = approve(consent)
    original = sender.rig.inbox.historical_read(sender.local_id)
    action = ReceiptAction("dispatch", sender.local_id, "read", original.sender, original.message_id,
                           original.digest, intent.receipt_id, RADIO)
    assert helper.permit(session, action).render_ack_binding == intent.render_ack_binding
    for changed in (replace(action, original_digest="b" * 64), replace(action, original_sender=RECIPIENT),
                    replace(action, radio_public_key=b"x" * 32), replace(action, kind="delivery"),
                    replace(action, operation="retry", decision_binding="operator-retry")):
        with pytest.raises(ReadReceiptError):
            helper.permit(session, changed)


def test_old_epoch_or_session_cannot_ack_or_recover(consent):
    sender, session, state, helper, create = consent
    pending = challenge(consent)
    state["epoch"] = "root-epoch-2"
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    state["epoch"] = session.epoch
    intent = helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    for changed in (replace(session, epoch="root-epoch-2"), replace(session, session_binding="b" * 64)):
        with pytest.raises(ReadReceiptError):
            create().recover(changed, local_id=intent.local_id)


def test_expired_and_consumed_challenges_fail(consent):
    sender, session, _, helper, _ = consent
    pending = challenge(consent)
    sender.rig.now += 60
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    pending = challenge(consent)
    helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)


def test_durable_write_unknown_recovered_by_exact_read_no_new_put(consent):
    sender, session, _, helper, create = consent
    pending = challenge(consent)
    sender.rig.failure = (sender.rig.puts + 1, "after")
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    count = sender.rig.puts
    recovered = create().recover(session, local_id=sender.local_id)
    assert recovered.local_id == sender.local_id and sender.rig.puts == count
    assert helper.close() is False


def test_uncertain_serial_dispatch_never_receives_retry_permission(consent):
    sender, session, _, helper, _ = consent
    intent = approve(consent)
    sender.sender.policy = lambda action: helper.permit(session, action)
    progress = sender.sender.queue(local_id=intent.local_id, kind="read", radio_public_key=RADIO,
        issued_at=intent.issued_at, expires_at=intent.expires_at, access_check=sender.access)
    with pytest.raises(Exception):
        sender.sender.dispatch_one(progress.receipt_id, device=device_for(sender, progress.receipt_id, reply=False),
                                   radio_timestamp=500, access_check=sender.access)
    row = sender.rig.rows[progress.receipt_id]
    assert row["payload"]["body"]["state"] == "dispatching"
    with pytest.raises(Exception):
        sender.sender.retry_decision(progress.receipt_id, expected_revision=row["revision"], decision_binding="retry", access_check=sender.access)
    assert sender.rig.rows[progress.receipt_id]["payload"]["body"]["state"] == "dispatching"


def test_revocation_after_storage_write_no_permit(consent):
    sender, session, state, helper, _ = consent
    pending = challenge(consent)
    sender.rig.after_put = lambda: state.update(allowed=False)
    with pytest.raises(ReadReceiptError):
        helper.acknowledge_render(session, challenge_id=pending.challenge_id, rendered=True, share_read_receipt=True)
    with pytest.raises(ReadReceiptError):
        helper.recover(session, local_id=sender.local_id)
    assert helper.close() is False


def test_modified_auxiliary_consent_never_authorizes(consent):
    sender, session, _, helper, _ = consent
    approve(consent)
    row = sender.rig.rows[helper._slot(sender.local_id)]
    row["payload"]["body"]["radio_public_key"] = "a" * 64
    with pytest.raises(ReadReceiptError):
        helper.recover(session, local_id=sender.local_id)


def test_old_record_not_reauthorized_by_new_live_root_session(consent):
    sender, session, _, helper, create = consent
    approve(consent)
    new_session = replace(session, epoch="root-epoch-2", session_binding="b" * 64)
    restarted = create()
    restarted._authorize = lambda current, operation: current == new_session
    with pytest.raises(ReadReceiptError):
        restarted.recover(new_session, local_id=sender.local_id)


def test_expired_approval_does_not_issue_new_receipt(consent):
    sender, session, _, helper, _ = consent
    intent = approve(consent)
    sender.rig.now = intent.expires_at
    with pytest.raises(ReadReceiptError):
        helper.recover(session, local_id=sender.local_id)
    before = json.dumps(sender.rig.rows, sort_keys=True)
    with pytest.raises(ReadReceiptError):
        approve(consent)
    assert json.dumps(sender.rig.rows, sort_keys=True) == before


def test_duplicate_render_ack_does_not_replace_immutable_approval(consent):
    sender, session, _, helper, create = consent
    first = approve(consent)
    before = json.dumps(sender.rig.rows, sort_keys=True)
    with pytest.raises(ReadReceiptError):
        approve(consent)
    assert json.dumps(sender.rig.rows, sort_keys=True) == before
    assert create().recover(session, local_id=sender.local_id) == first


def test_challenge_bound_and_close_clear(consent):
    _, session, _, helper, _ = consent
    for _ in range(MAX_CHALLENGES):
        challenge(consent)
    with pytest.raises(ReadReceiptError):
        challenge(consent)
    assert helper.close() is True and not helper._challenges
    with pytest.raises(ReadReceiptError):
        challenge(consent)


def test_close_does_not_hold_condition_across_authorization_io(consent):
    _, _, _, helper, _ = consent
    entered, release = threading.Event(), threading.Event()
    def authorize(session, operation):
        entered.set()
        assert release.wait(3)
        return True
    helper._authorize = authorize
    errors = []
    def begin():
        try:
            challenge(consent)
        except ReadReceiptError:
            errors.append(True)
    worker = threading.Thread(target=begin)
    worker.start()
    assert entered.wait(3)
    assert helper.close(timeout=0) is False
    release.set()
    worker.join(3)
    assert not worker.is_alive() and errors and helper.close(timeout=0) is True
