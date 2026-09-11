import copy
import uuid
from dataclasses import asdict

import pytest

from sage_lantern.maintenance_owner_ledgers import OwnerLedgerInspection, correction
from sage_lantern.correction_attempts import SCHEMA
from sage_lantern.workflow_store import WorkflowUnavailable
from test_mesh_contact_owner import owner
from test_mesh_contact_owner import preview, confirm
from test_mesh_contact_reconciliation import reserve, reconciler, show, approve
from test_mesh_outbox import setup, ingest, receipt_wire, MESSAGE
from sage_lantern.public_workflow_store import PublicWorkflowStore
from sage_lantern.mesh_outbox import REPLAY_NAMESPACE, _canonical
from sage_lantern.community_public import CommunityPublic, PublicReceipt


def attempt(ledger, state='reserved'):
    source = str(uuid.uuid4())
    key = str(uuid.uuid5(uuid.NAMESPACE_URL, SCHEMA + ':' + ledger.owner['chain_id'] + ':'
        + ledger.owner['agent_id'] + ':' + source))
    value = dict(schema=SCHEMA, actor=ledger.owner, source_id=source, source_kind='current',
        attempt_id=str(uuid.uuid4()), session_id=None if state in {'reserved', 'admitting'} else str(uuid.uuid4()),
        state=state, previous=None, authorization=dict(expected_revision=0,
            acknowledge_unknown=True, new_execution=True))
    return dict(schema='sage.workflow-journal.v1', agent_id=ledger.identity.agent_id,
        record_id=key, revision=1, kind='correction_attempt', payload=value, trust='untrusted_auxiliary')


def inspect(ledger, rows, check=lambda: True):
    return OwnerLedgerInspection(rows, ledger.owner, ledger.store, ledger.identity, containment=check)


@pytest.mark.parametrize('state', ['reserved', 'admitting', 'accepted', 'completed', 'failed'])
def test_every_correction_state_retained_not_settled_or_reexecuted(owner, state):
    _, ledger, state_data = owner
    row = attempt(ledger, state)
    original = copy.deepcopy(row)
    assert inspect(ledger, [row]).inspect(row) == 'retained_unknown'
    assert row == original and state_data['puts'] == 0


@pytest.mark.parametrize('field,value', [('actor', {}), ('state', 'queued'), ('session_id', str(uuid.uuid4())),
    ('source_kind', 'other'), ('attempt_id', str(uuid.UUID(int=0)))])
def test_malformed_correction_never_admitted(owner, field, value):
    _, ledger, _ = owner
    row = attempt(ledger)
    row['payload'][field] = value
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [row]).inspect(row)


def test_exact_previous_archive_and_revision_required(owner):
    _, ledger, state = owner
    old = attempt(ledger, 'accepted')
    archive_id = str(uuid.uuid5(uuid.UUID(old['record_id']), old['payload']['attempt_id']))
    archive = dict(old, record_id=archive_id, revision=1,
        payload=dict(schema='sage.lantern.correction-history.v1', record=copy.deepcopy(old)))
    current = copy.deepcopy(old)
    current['revision'] = 2
    current['payload'].update(attempt_id=str(uuid.uuid4()), previous=archive_id,
        authorization=dict(expected_revision=1, acknowledge_unknown=True, new_execution=True))
    checker = inspect(ledger, [current, archive])
    assert checker.inspect(current) == checker.inspect(archive) == 'retained_unknown'
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [current]).inspect(current)
    archive['payload']['record']['revision'] = 2
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [current, archive]).inspect(current)
    assert state['puts'] == 0


def test_containment_must_remain_current(owner):
    _, ledger, _ = owner
    row = attempt(ledger)
    live = [True]
    checker = inspect(ledger, [row], lambda: live[0])
    live[0] = False
    with pytest.raises(WorkflowUnavailable):
        checker.finish()
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [row], lambda: False)


def test_contact_reserved_signed_evidence_retained(owner):
    _, ledger, state = owner
    ledger.reserve(0, 1, 'f' * 64)
    row = state['rows'][ledger.identifier]
    before = copy.deepcopy(state)
    assert inspect(ledger, [row]).inspect(row) == 'retained_unknown'
    assert state == before
    row['payload']['body']['card_revision'] += 1
    with pytest.raises(Exception):
        inspect(ledger, [row]).inspect(row)


@pytest.mark.parametrize('kind', ['public_proposal', 'mesh_outbound', 'unknown'])
def test_no_generic_kind_whitelist(owner, kind):
    _, ledger, _ = owner
    row = attempt(ledger)
    row['kind'] = kind
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [row]).inspect(row)


def test_strict_authorization_revision_and_boolean(owner):
    _, ledger, _ = owner
    row = attempt(ledger)
    for value in (True, -1, 1):
        row['payload']['authorization']['expected_revision'] = value
        with pytest.raises(WorkflowUnavailable):
            correction(row, ledger.owner)


@pytest.mark.parametrize('result,admitted', [({'cancelled': True}, True),
    (None, False), ({'id': 'synthetic', 'state': 'sent', 'created_at': '2026-09-08T00:00:00Z'}, False),
    ({'cancelled': 1}, False)])
def test_public_http_sent_is_not_acceptance(owner, result, admitted):
    _, ledger, state = owner
    public = PublicWorkflowStore(ledger.store, chain_id='public', domain='commons', author=ledger.identity.agent_id,
        access_check=lambda: True, drain_check=lambda: False, identity=ledger.identity)
    operation = str(uuid.uuid4())
    payload = public._base('http', operation, 'a' * 64)
    payload['result'] = result
    row = dict(attempt(ledger), kind='public_proposal', record_id=public._id('http', operation), payload=payload)
    checker = inspect(ledger, [row])
    if admitted:
        assert checker.inspect(row) == 'retained_terminal'
    else:
        with pytest.raises(WorkflowUnavailable):
            checker.inspect(row)
    assert state['puts'] == 0


def test_mesh_receipts_and_replay_links_all_required_no_dispatch(setup):
    setup.queue()
    outbox = setup.outbox
    get = lambda key: outbox.store.get(key, kind='mesh_outbound', access_check=setup.access)
    original = get(MESSAGE)
    actor = dict(chain_id=setup.sender.chain_id, agent_id=setup.identity.agent_id)
    checker = OwnerLedgerInspection([original], actor, outbox.store, setup.identity, containment=setup.access)
    with pytest.raises(WorkflowUnavailable):
        checker.inspect(original)
    ingest(setup, receipt_wire(setup))
    original = get(MESSAGE)
    body = original['payload']['body']
    item = body['receipts'][0]
    receipt = outbox._receipt(body, item['wire'], item['verified_at'])
    scopes = [dict(sender=receipt.sender.to_dict(), receipt_id=receipt.message_id),
        dict(schema='sage.lantern.mesh-receipt-slot.v1', receiver=outbox.sender.to_dict(),
             original_id=MESSAGE, kind=receipt.kind)]
    rows = [original] + [get(str(uuid.uuid5(REPLAY_NAMESPACE, _canonical(scope).decode('ascii')))) for scope in scopes]
    writes, radios = list(setup.writes), list(setup.radios)
    checker = OwnerLedgerInspection(rows, actor, outbox.store, setup.identity, containment=setup.access)
    assert all(checker.inspect(row) == 'retained_terminal' for row in rows)
    assert checker.finish()
    assert setup.writes == writes and setup.radios == radios
    with pytest.raises(WorkflowUnavailable):
        OwnerLedgerInspection(rows[:-1], actor, outbox.store, setup.identity,
            containment=setup.access).inspect(original)


def test_expired_issued_card_retained_without_current_contact_claim(owner):
    handler, ledger, state = owner
    confirm(handler, preview(handler))
    ledger.clock = lambda: 10**9
    rows = list(state['rows'].values())
    before = copy.deepcopy(state)
    checker = inspect(ledger, rows)
    assert checker.inspect(rows[0]) == 'retained_issued'
    assert checker.finish() and state == before


def test_signed_abandonment_keeps_all_original_audit_and_completion(owner):
    _, ledger, state = owner
    reserve(owner)
    handler = reconciler(owner)
    approve(handler, show(handler))
    rows = list(state['rows'].values())
    before = copy.deepcopy(state)
    checker = inspect(ledger, rows)
    assert sorted(checker.inspect(row) for row in rows) == [
        'retained_abandoned', 'retained_abandoned', 'retained_unknown']
    assert checker.finish() and state == before
    without_audit = [row for row in rows if row['payload'].get('body', {}).get('schema') !=
        'sage.lantern.owner-contact-reconciliation-audit.v1']
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, without_audit).inspect(state['rows'][ledger.identifier])


def test_sdk_public_receipt_must_match_actual_proposal(owner):
    _, ledger, state = owner
    public = PublicWorkflowStore(ledger.store, chain_id='public', domain='commons', author=ledger.identity.agent_id,
        access_check=lambda: True, drain_check=lambda: False, identity=ledger.identity)
    operation = str(uuid.uuid4())
    proposal = CommunityPublic(None, None, chain_id='public', domain='commons', author=ledger.identity.agent_id).prepare(
        operation_id=operation, question='Synthetic question', answer='Synthetic answer')
    receipt = PublicReceipt(operation, proposal.content_hash, 'public', str(uuid.uuid4()), 'b' * 64, 4)
    payload = public._base('sdk', operation, proposal.content_hash)
    payload.update(proposal=asdict(proposal), created_at='2026-09-08T00:00:00Z', reserved=True, result=asdict(receipt))
    row = dict(attempt(ledger), kind='public_proposal', record_id=public._id('sdk', operation), payload=payload)
    assert inspect(ledger, [row]).inspect(row) == 'retained_terminal'
    row['payload']['result']['content_hash'] = 'f' * 64
    with pytest.raises(WorkflowUnavailable):
        inspect(ledger, [row]).inspect(row)
    assert state['puts'] == 0
