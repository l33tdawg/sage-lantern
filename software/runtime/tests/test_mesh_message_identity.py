"""Paired source regressions: real crypto/SDK journals, synthetic RF frames."""

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sage_sdk import SageClient

from sage_lantern.mesh_contacts import sign_contact_card, verify_contact_card
from sage_lantern.mesh_envelope import Peer, message_digest, open_envelope, seal_delivery_receipt, seal_message, seal_read_receipt
from sage_lantern.mesh_inbox import INBOX_SCHEMA, MeshInbox, inbox_id
from sage_lantern.mesh_outbox import MAX_APPROVALS, MeshOutboxError
from sage_lantern.message_contacts import RecipientID
from sage_lantern.message_storage import MessageStorageProbe
from sage_lantern.workflow_store import MAX_PAYLOAD, WorkflowStore
from test_mesh_inbox import feed
from test_mesh_outbox import MESSAGE, lapsed, setup


@pytest.fixture
def receiver(setup):
    state = SimpleNamespace(rows={}, failure=None)
    def handler(request):
        if request.url.path == '/v1/messages/storage':
            return httpx.Response(200, json=dict(schema='sage.message-storage.v1', instance_id='b'*64,
                agent_id=setup.peer_identity.agent_id, encryption_expected=True, vault_active=True,
                vault_generation='1', stable=True, canonical_send_idempotency=True, encrypted_send_admission=True))
        if request.url.path == '/v1/workflows':
            return httpx.Response(200, json=dict(schema='sage.workflow-journal.v1', items=[],
                next_after=None, has_more=False))
        identifier = request.url.path.rsplit('/', 1)[-1]
        if request.method == 'GET':
            return httpx.Response(200 if identifier in state.rows else 404, json=state.rows.get(identifier, {}))
        body = json.loads(request.content)
        revision = state.rows.get(identifier, {}).get('revision', 0)
        if revision != body['expected_revision']:
            return httpx.Response(409, json={})
        state.rows[identifier] = dict(schema='sage.workflow-journal.v1', agent_id=setup.peer_identity.agent_id,
            record_id=identifier, revision=revision+1, kind=body['kind'], payload=body['payload'], trust='untrusted_auxiliary')
        return httpx.Response(200, json=state.rows[identifier])
    endpoint = 'http://127.0.0.1:19101'
    client = SageClient(endpoint, setup.peer_identity, timeout=2, trust_env=False)
    client._client.close()
    client._client = httpx.Client(base_url=endpoint, timeout=2, trust_env=False,
        follow_redirects=False, transport=httpx.MockTransport(handler))
    store = WorkflowStore(client, storage=MessageStorageProbe(client,
        actor=RecipientID(setup.peer.chain_id, setup.peer_identity.agent_id), endpoint=endpoint))
    state.new = lambda: MeshInbox(store, recipient=setup.peer, recipient_signing_key=setup.peer_identity._signing_key,
        resolve_peer=lambda message: setup.sender, access_check=setup.access, storage_check=setup.access,
        clock=lambda: setup.now)
    state.inbox = state.new()
    yield state
    client._client.close()


def renew(setup):
    progress = setup.outbox.inspect(MESSAGE, access_check=setup.access)
    lapsed(setup, now=setup.stored()['body']['approved']['expires_at'], card_expires=80000)
    return setup.outbox.reapprove(MESSAGE, expected_revision=progress.revision, window_seconds=600,
        approval_binding='owner-renewed', consent_binding='consent-current', access_check=setup.access)


def assertion(setup, original, *, kind='delivery', issued_at=None, expires_at=None):
    values = dict(sender=setup.peer, recipient=setup.sender,
        message_id=str(uuid.uuid4()), original_id=MESSAGE, original_digest=original.digest,
        issued_at=setup.now if issued_at is None else issued_at,
        expires_at=setup.now+3600 if expires_at is None else expires_at)
    if kind == 'read':
        return seal_read_receipt(setup.peer_identity._signing_key, **values, explicit_user_read=True).wire
    return seal_delivery_receipt(setup.peer_identity._signing_key, **values).wire


def opened_attempt(setup):
    return open_envelope(setup.stored()['body']['approved']['wire'], setup.peer_identity._signing_key,
        expected_sender=setup.sender, expected_recipient=setup.peer, now=setup.now)


def inbox_messages(receiver):
    return [row for row in receiver.rows.values() if row['payload']['schema'] == INBOX_SCHEMA]


@pytest.mark.parametrize('check', ['renewed_delivery', 'delayed_receipt'])
def test_original_admitted_receipt_withheld_then_reapproval(setup, receiver, check):
    setup.queue()
    wire = setup.stored()['body']['approved']['wire']
    assert feed(receiver.inbox, wire.encode())[-1].status == 'admitted'
    local_id = inbox_id(setup.sender, MESSAGE)
    original = receiver.inbox.historical_read(local_id)
    withheld = assertion(setup, original)
    renew(setup)
    if check == 'renewed_delivery':
        renewed = setup.stored()['body']['approved']['wire']
        result = feed(receiver.new(), renewed.encode())[-1]
        assert (result.status, result.record_id) == ('duplicate', local_id)
        messages = [r for r in receiver.rows.values() if r['payload']['schema'] == INBOX_SCHEMA]
        assert len(messages) == 1 and messages[0]['revision'] == 1
    else:
        setup.outbox = setup.make()
        delivered = setup.outbox.ingest_receipt(MESSAGE, wire=withheld, access_check=setup.access)
        assert delivered.state == 'delivered'
        assert setup.outbox.ingest_receipt(MESSAGE, wire=withheld, access_check=setup.access) == delivered


def test_original_admission_renewed_duplicate_and_delayed_receipt_across_restarts(setup, receiver):
    setup.queue()
    original = opened_attempt(setup)
    original_wire = setup.stored()['body']['approved']['wire']
    assert feed(receiver.inbox, original_wire.encode())[-1].status == 'admitted'
    delayed = assertion(setup, original)
    original_record = json.loads(json.dumps(inbox_messages(receiver)[0]))
    setup.outbox = setup.make()
    renewed = renew(setup)
    assert renewed.state == 'queued' and renewed.next_fragment == 0
    current = opened_attempt(setup)
    assert current.message_id == original.message_id == MESSAGE
    assert current.attempt_id and current.digest != original.digest
    assert current.logical_digest == original.logical_digest == original.digest
    assert feed(receiver.new(), setup.stored()['body']['approved']['wire'].encode())[-1].status == 'duplicate'
    assert inbox_messages(receiver) == [original_record]
    setup.outbox = setup.make()
    delivered = setup.outbox.ingest_receipt(MESSAGE, wire=delayed, access_check=setup.access)
    writes = list(setup.writes)
    setup.outbox = setup.make()
    assert setup.outbox.ingest_receipt(MESSAGE, wire=delayed, access_check=setup.access) == delivered
    assert delivered.state == 'delivered' and setup.writes == writes
    assert setup.stored()['body']['approved']['attempt_id'] == current.attempt_id
    assert setup.radios == []


def test_renewed_first_old_wire_is_expired_and_repeated_new_attempt_is_duplicate(setup, receiver):
    setup.queue()
    original_wire = setup.stored()['body']['approved']['wire']
    renew(setup)
    renewed_wire = setup.stored()['body']['approved']['wire']
    assert feed(receiver.inbox, renewed_wire.encode())[-1].status == 'admitted'
    old = feed(receiver.new(), original_wire.encode())[-1]
    assert (old.status, old.reason) == ('rejected', 'invalid_envelope')
    assert feed(receiver.new(), renewed_wire.encode())[-1].status == 'duplicate'
    renew(setup)
    assert feed(receiver.new(), setup.stored()['body']['approved']['wire'].encode())[-1].status == 'duplicate'
    assert len(inbox_messages(receiver)) == 1


@pytest.mark.parametrize('change', ['content', 'logical_issued_at', 'logical_expires_at', 'kind'])
def test_same_message_id_changed_signed_logical_identity_is_equivocation(setup, receiver, change):
    setup.queue()
    original = opened_attempt(setup)
    assert feed(receiver.inbox, setup.stored()['body']['approved']['wire'].encode())[-1].status == 'admitted'
    saved = json.loads(json.dumps(inbox_messages(receiver)))
    renew(setup)
    values = dict(sender=setup.sender, recipient=setup.peer, message_id=MESSAGE,
        content=original.content, issued_at=setup.now, expires_at=setup.now+600,
        attempt_id=str(uuid.uuid4()), logical_issued_at=original.issued_at,
        logical_expires_at=original.expires_at)
    if change == 'kind':
        forged = seal_delivery_receipt(setup.identity._signing_key, sender=setup.sender, recipient=setup.peer,
            message_id=MESSAGE, original_id=str(uuid.uuid4()), original_digest=original.digest,
            issued_at=setup.now, expires_at=setup.now+600)
    else:
        values[change] = 'Changed signed private content' if change == 'content' else values[change]-1
        forged = seal_message(setup.identity._signing_key, **values)
    result = feed(receiver.new(), forged.wire.encode())[-1]
    assert (result.status, result.reason) == ('rejected', 'equivocation')
    assert inbox_messages(receiver) == saved


@pytest.mark.parametrize('order', [('old_delivery', 'new_read', 'new_delivery', 'old_read'),
    ('new_read', 'old_delivery', 'old_read', 'new_delivery'),
    ('new_delivery', 'old_read', 'old_delivery', 'new_read')])
def test_mixed_attempt_receipt_order_is_monotonic_and_bounded(setup, order):
    setup.queue()
    original = opened_attempt(setup)
    wires = {'old_delivery': assertion(setup, original), 'old_read': assertion(setup, original, kind='read')}
    renew(setup)
    current = opened_attempt(setup)
    wires.update(new_delivery=assertion(setup, current), new_read=assertion(setup, current, kind='read'))
    rank = 0
    for name in order:
        setup.outbox = setup.make()
        progress = setup.outbox.ingest_receipt(MESSAGE, wire=wires[name], access_check=setup.access)
        rank = max(rank, 2 if name.endswith('read') else 1)
        assert progress.state == ('read' if rank == 2 else 'delivered')
    writes = list(setup.writes)
    rows = setup.row_count()
    for wire in wires.values():
        assert setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access).state == 'read'
    assert setup.writes == writes and setup.row_count() == rows
    assert len(setup.stored()['body']['receipts']) <= 2
    assert setup.radios == []


@pytest.mark.parametrize('attempt', ['original', 'renewed'])
def test_first_seen_expired_receipt_cannot_promote_any_attempt(setup, attempt):
    setup.queue()
    original = opened_attempt(setup)
    old_receipt = assertion(setup, original, expires_at=setup.now+10)
    renew(setup)
    wire = old_receipt if attempt == 'original' else assertion(setup, opened_attempt(setup), expires_at=setup.now+10)
    setup.now += 10
    before, writes = setup.stored(), list(setup.writes)
    with pytest.raises(MeshOutboxError):
        setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access)
    assert setup.stored() == before and setup.writes == writes


@pytest.mark.parametrize('mistake', ['unknown_digest', 'before_attempt_issue'])
def test_signed_receipt_needs_specific_attempt_digest_and_its_issue_time(setup, mistake):
    setup.queue()
    renew(setup)
    renewed = opened_attempt(setup)
    if mistake == 'unknown_digest':
        # A random plausible digest grants no authority, even with the right message UUID.
        wire = seal_delivery_receipt(setup.peer_identity._signing_key, sender=setup.peer,
            recipient=setup.sender, message_id=str(uuid.uuid4()), original_id=MESSAGE,
            original_digest='0'*64, issued_at=setup.now, expires_at=setup.now+600).wire
    else:
        wire = assertion(setup, renewed, issued_at=renewed.issued_at-1)
    before, writes = setup.stored(), list(setup.writes)
    with pytest.raises(MeshOutboxError):
        setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access)
    assert setup.stored() == before and setup.writes == writes


def test_historical_accepted_old_receipt_remains_evidence_after_its_expiry(setup):
    setup.queue()
    original = opened_attempt(setup)
    wire = assertion(setup, original, expires_at=1200)
    renew(setup)
    delivered = setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access)
    setup.now = 1200
    setup.outbox = setup.make()
    assert setup.outbox.ingest_receipt(MESSAGE, wire=wire, access_check=setup.access) == delivered


@pytest.mark.parametrize('denial', ['guard', 'contact', 'cancel'])
def test_denied_reapproval_preserves_original_identity_and_history(setup, denial):
    setup.queue()
    original = setup.stored()
    if denial == 'cancel':
        setup.outbox.cancel(MESSAGE, expected_revision=1, decision_binding='owner-cancel', access_check=setup.access)
    lapsed(setup)
    before, writes = setup.stored(), list(setup.writes)
    if denial == 'guard':
        setup.allowed = False
    elif denial == 'contact':
        setup.contact_allowed = False
    with pytest.raises(MeshOutboxError):
        setup.outbox.reapprove(MESSAGE, expected_revision=before['body']['revision'], window_seconds=600,
            approval_binding='owner-renewed', consent_binding='consent-current', access_check=setup.access)
    assert setup.stored() == before and setup.writes == writes
    assert setup.stored()['body']['approved'] == original['body']['approved']
    assert setup.radios == []


def test_cancel_after_renewal_stops_work_but_allows_delayed_authentic_assertion(setup):
    setup.queue()
    delayed = assertion(setup, opened_attempt(setup))
    progress = renew(setup)
    cancelled = setup.outbox.cancel(MESSAGE, expected_revision=progress.revision,
        decision_binding='owner-stop-renewal', access_check=setup.access)
    lapsed(setup)
    before = setup.stored()
    with pytest.raises(MeshOutboxError):
        renew(setup)
    assert cancelled.state == 'cancelled' and setup.stored() == before
    assert setup.outbox.ingest_receipt(MESSAGE, wire=delayed, access_check=setup.access).state == 'delivered'
    assert setup.radios == []


@pytest.mark.parametrize('fault', ['before', 'after'])
def test_reapproval_cas_unknown_restart_does_not_generate_extra_attempt(setup, fault):
    setup.queue()
    original = setup.stored()
    lapsed(setup)
    setup.fault = fault
    with pytest.raises(MeshOutboxError):
        setup.outbox.reapprove(MESSAGE, expected_revision=1, window_seconds=600,
            approval_binding='owner-renewed', consent_binding='consent-current', access_check=setup.access)
    stored = setup.stored()
    setup.fault = None
    setup.outbox = setup.make()
    progress = setup.outbox.inspect(MESSAGE, access_check=setup.access)
    if fault == 'before':
        assert stored == original and progress.revision == 1
        renewed = setup.outbox.reapprove(MESSAGE, expected_revision=1, window_seconds=600,
            approval_binding='owner-renewed', consent_binding='consent-current', access_check=setup.access)
        assert renewed.revision == 2
    else:
        assert progress.revision == 2 and len(stored['body']['attempt_history']) == 2
        with pytest.raises(MeshOutboxError):
            setup.outbox.reapprove(MESSAGE, expected_revision=1, window_seconds=600,
                approval_binding='owner-retry-unknown', consent_binding='consent-current', access_check=setup.access)
        assert setup.stored() == stored
    assert len(setup.stored()['body']['attempt_history']) == 2
    assert setup.radios == []


@pytest.mark.parametrize('denial', ['guard', 'contact'])
def test_reapproval_commit_then_authority_denial_cannot_dispatch(setup, denial):
    setup.queue()
    lapsed(setup)
    def deny_after_commit(state):
        if state == 'queued':
            if denial == 'guard':
                setup.allowed = False
            else:
                setup.contact_allowed = False
    setup.after_commit = deny_after_commit
    # The post-commit check may refuse the return; either outcome leaves one durable renewal.
    try:
        setup.outbox.reapprove(MESSAGE, expected_revision=1, window_seconds=600,
            approval_binding='owner-renewed', consent_binding='consent-current', access_check=setup.access)
    except MeshOutboxError:
        pass
    body = setup.stored()['body']
    assert body['revision'] == 2 and len(body['attempt_history']) == 2
    with pytest.raises(MeshOutboxError):
        setup.outbox.dispatch_one(MESSAGE, device=setup.radio(), radio_timestamp=500,
            access_check=setup.access)
    assert setup.stored()['body'] == body and setup.radios == []


def resign_body(setup, change):
    """Exercise semantic history validation independently of outer signature rejection."""
    def tamper(payload):
        body = payload['body']
        change(body)
        signed = setup.outbox._proof(body)
        payload.clear()
        payload.update(signed)
    setup.tamper(tamper)


@pytest.mark.parametrize('fault', ['missing', 'empty', 'nonlist', 'duplicate', 'tampered_digest',
    'tampered_time', 'extra_field', 'reversed', 'too_many', 'no_legacy', 'wrong_current', 'message_id_attempt',
    'overlapping_windows', 'repeated_attempt_id'])
def test_authenticated_invalid_attempt_history_is_refused(setup, fault):
    setup.queue()
    renew(setup)
    renew(setup)
    renew(setup)
    def change(body):
        history = body['attempt_history']
        if fault == 'missing':
            del body['attempt_history']
        elif fault == 'empty':
            body['attempt_history'] = []
        elif fault == 'nonlist':
            body['attempt_history'] = {}
        elif fault == 'duplicate':
            history.insert(1, dict(history[1]))
        elif fault == 'tampered_digest':
            history[0]['content_digest'] = '0'*64
        elif fault == 'tampered_time':
            history[0]['issued_at'] += 1
        elif fault == 'extra_field':
            history[0]['wire'] = 'LME1.forbidden'
        elif fault == 'reversed':
            history.reverse()
        elif fault == 'too_many':
            body['attempt_history'] = history * 4
        elif fault == 'no_legacy':
            del history[0]
        elif fault == 'wrong_current':
            history.pop()
        elif fault in ('overlapping_windows', 'repeated_attempt_id'):
            target = history[1] if fault == 'overlapping_windows' else history[2]
            if fault == 'overlapping_windows':
                target['expires_at'] = history[2]['issued_at']+1
            else:
                target['attempt_id'] = history[1]['attempt_id']
            target['content_digest'] = message_digest(sender=setup.sender, recipient=setup.peer,
                message_id=MESSAGE, content=body['approved']['content'], issued_at=target['issued_at'],
                expires_at=target['expires_at'], attempt_id=target['attempt_id'],
                logical_issued_at=body['approved']['logical_issued_at'],
                logical_expires_at=body['approved']['logical_expires_at'])
        else:
            history[1]['attempt_id'] = MESSAGE
    resign_body(setup, change)
    before = setup.stored()
    setup.outbox = setup.make()
    with pytest.raises(MeshOutboxError):
        setup.outbox.inspect(MESSAGE, access_check=setup.access)
    with pytest.raises(MeshOutboxError):
        setup.outbox.read_sent(MESSAGE, access_check=setup.access)
    assert setup.stored() == before and setup.radios == []


def test_attempt_history_cap_retains_every_digest_and_refuses_ninth_approval(setup):
    setup.queue()
    snapshots = [opened_attempt(setup)]
    for _ in range(MAX_APPROVALS-1):
        setup.outbox = setup.make()
        renew(setup)
        snapshots.append(opened_attempt(setup))
    body = setup.stored()['body']
    assert len(body['attempt_history']) == MAX_APPROVALS == 8
    assert [item['content_digest'] for item in body['attempt_history']] == [item.digest for item in snapshots]
    assert body['attempt_history'][0]['attempt_id'] is None
    assert len({item.attempt_id for item in snapshots[1:]}) == 7
    assert {item.logical_digest for item in snapshots} == {snapshots[0].digest}
    before, writes = setup.stored(), list(setup.writes)
    with pytest.raises(MeshOutboxError):
        renew(setup)
    assert setup.stored() == before and setup.writes == writes
    # No digest eviction means a receipt for even the oldest attempt can still correlate.
    delayed = assertion(setup, snapshots[0])
    assert setup.outbox.ingest_receipt(MESSAGE, wire=delayed, access_check=setup.access).state == 'delivered'


def test_maximum_chain_content_history_and_two_receipts_fit_workflow_bound(setup):
    chain = 'H'*128
    setup.sender = Peer(chain, setup.sender.public_key)
    setup.peer = Peer(chain, setup.peer.public_key)
    setup.outbox.sender = setup.sender
    def contact(_action):
        card = sign_contact_card(setup.peer_identity, chain_id=chain, radio_public_key=setup.radio_key.hex(),
            display_name='Synthetic boundary peer', revision=1, issued_at=100, expires_at=80000,
            now=setup.now, findable=True, messaging_allowed=True)
        return verify_contact_card(card, now=setup.now)
    setup.outbox.verify_contact = contact
    content = 'x' + '\t'*511
    setup.outbox.queue(message_id=MESSAGE, recipient=setup.peer, radio_public_key=setup.radio_key,
        content=content, approval_binding='a'*128, consent_binding='c'*128,
        issued_at=100, expires_at=1000, access_check=setup.access)
    original = opened_attempt(setup)
    for _ in range(MAX_APPROVALS-1):
        previous = setup.outbox.inspect(MESSAGE, access_check=setup.access)
        lapsed(setup, now=setup.stored()['body']['approved']['expires_at'])
        setup.outbox.reapprove(MESSAGE, expected_revision=previous.revision, window_seconds=600,
            approval_binding='a'*128, consent_binding='c'*128, access_check=setup.access)
    assert opened_attempt(setup).content == content
    assert len(setup.stored()['body']['attempt_history']) == MAX_APPROVALS
    for kind in ('delivery', 'read'):
        result = setup.outbox.ingest_receipt(MESSAGE, wire=assertion(setup, original, kind=kind),
            access_check=setup.access)
        assert result.state == ('delivered' if kind == 'delivery' else 'read')
    payload = json.dumps(setup.stored(), ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode('utf-8')
    assert len(payload) <= MAX_PAYLOAD == 16384
