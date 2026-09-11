import copy
from dataclasses import replace

import pytest

from sage_lantern.mesh_contact_owner import ContactIssuanceLedger, OwnerContactHandlers
from sage_lantern.mesh_contact_reconciliation import OwnerContactReconciliation, read_audit, read_completion, verify_abandoned
from sage_lantern.workflow_store import WorkflowOutcomeUnknown
from test_mesh_contact_owner import owner, preview, confirm


def reconciler(owner):
    _, ledger, state = owner
    return OwnerContactReconciliation(ledger, read_profile=lambda: state['profile'],
        read_binding=lambda: state['binding'], access_check=lambda: state['active'], clock=lambda: state.get('now', 1000))


def show(handler, action='abandon'):
    return handler.handle(dict(op='owner-contact-reconcile-preview', action=action,
        broker_id='b' * 32, epoch='e' * 32), peer_uid=0)


def approve(handler, shown, **changes):
    request = dict(op='owner-contact-reconcile-confirm', challenge=shown['challenge'],
        plan_sha256=shown['plan_sha256'], consent=True, broker_id='b' * 32, epoch='e' * 32)
    return handler.handle({**request, **changes}, peer_uid=0)


def reserve(owner):
    handler, ledger, state = owner
    state['unknown'] = True
    with pytest.raises(Exception):
        confirm(handler, preview(handler))
    state['unknown'] = False
    return copy.deepcopy(state['rows'][ledger.identifier])


def test_abandon_retains_signed_original_and_monotonic_next_issue(owner, monkeypatch):
    issuance, ledger, state = owner
    original = reserve(owner)
    handler = reconciler(owner)
    shown = show(handler)
    assert state['puts'] == 1 and 'card' not in shown
    with monkeypatch.context() as context:
        context.setattr('sage_lantern.mesh_contact_issuance.sign_contact_card', lambda *args, **kwargs: pytest.fail('must not re-sign card'))
        result = approve(handler, shown)
    assert result['state'] == 'abandoned' and result['revision'] == 1
    audit = read_audit(ledger, original)
    assert audit['revision'] == 1 and audit['payload']['body']['original'] == original
    assert audit['payload']['body']['decision'] == 'abandon_authorized'
    abandoned = copy.deepcopy(state['rows'][ledger.identifier])
    verify_abandoned(ledger, abandoned)
    completion = copy.deepcopy(read_completion(ledger, abandoned))
    assert abandoned['payload']['body']['plan_sha256'] == original['payload']['body']['plan_sha256']
    restarted = ContactIssuanceLedger(ledger.store, ledger.identity, access_check=ledger.access, clock=lambda: 1000)
    renewed = OwnerContactHandlers(restarted, read_profile=lambda: state['profile'],
        read_binding=lambda: state['binding'], access_check=lambda: state['active'], clock=lambda: 1000)
    assert confirm(renewed, preview(renewed))['revision'] == 2
    assert read_audit(ledger, original) == audit
    assert read_completion(ledger, abandoned) == completion
    verify_abandoned(ledger, abandoned)


@pytest.mark.parametrize('fault', ['cancel', 'restart', 'locked', 'epoch', 'profile', 'expired', 'consent', 'digest', 'challenge'])
def test_fresh_approval_and_cancel_do_not_mutate_reserved(owner, fault):
    _, ledger, state = owner
    original = reserve(owner)
    handler = reconciler(owner)
    shown = show(handler)
    changes = {}
    if fault == 'cancel':
        assert handler.handle(dict(op='owner-contact-reconcile-cancel', broker_id='b' * 32, epoch='e' * 32), peer_uid=0) == {'cancelled': True}
    if fault == 'restart': handler = reconciler(owner)
    if fault == 'locked': state['active'] = False
    if fault == 'epoch': state['binding']['epoch'] = 'f' * 32
    if fault == 'profile': state['profile'] = replace(state['profile'], revision=2, public_discoverable=True)
    if fault == 'expired': state['now'] = 1061
    if fault == 'consent': changes['consent'] = False
    if fault == 'digest': changes['plan_sha256'] = 'f' * 64
    if fault == 'challenge': changes['challenge'] = 'bad'
    with pytest.raises(Exception): approve(handler, shown, **changes)
    assert state['puts'] == 1 and state['rows'][ledger.identifier] == original
    assert handler.pending is None


def test_unknown_archive_requires_fresh_confirmation_reuses_exact_evidence(owner):
    _, ledger, state = owner
    original = reserve(owner)
    handler = reconciler(owner)
    shown = show(handler)
    state['unknown'] = True
    with pytest.raises(WorkflowOutcomeUnknown): approve(handler, shown)
    state['unknown'] = False
    audit = copy.deepcopy(read_audit(ledger, original))
    assert state['rows'][ledger.identifier] == original
    with pytest.raises(Exception): approve(handler, shown)
    restarted = reconciler(owner)
    assert approve(restarted, show(restarted))['state'] == 'abandoned'
    assert read_audit(ledger, original) == audit and state['puts'] == 4


def test_unknown_completed_cas_acknowledge_no_replay(owner, monkeypatch):
    _, ledger, state = owner
    reserve(owner)
    handler = reconciler(owner)
    shown = show(handler)
    put = ledger.store.put
    def uncertain(identifier, *args, **kwargs):
        result = put(identifier, *args, **kwargs)
        if identifier == ledger.identifier:
            raise WorkflowOutcomeUnknown()
        return result
    with monkeypatch.context() as context:
        context.setattr(ledger.store, 'put', uncertain)
        with pytest.raises(WorkflowOutcomeUnknown): approve(handler, shown)
    assert ledger.current()['state'] == 'abandoned'
    assert read_completion(ledger, state['rows'][ledger.identifier]) is None
    with pytest.raises(Exception): preview(owner[0])
    restarted = reconciler(owner)
    with pytest.raises(Exception): show(restarted, 'abandon')
    assert approve(restarted, show(restarted, 'acknowledge'))['state'] == 'abandoned'
    assert state['puts'] == 4


def test_racing_reconcilers_only_one_cas_and_retained_audit(owner):
    _, ledger, state = owner
    original = reserve(owner)
    first, second = reconciler(owner), reconciler(owner)
    first_plan, second_plan = show(first), show(second)
    approve(first, first_plan)
    with pytest.raises(Exception): approve(second, second_plan)
    assert state['puts'] == 4 and read_audit(ledger, original)['payload']['body']['original'] == original


def test_issued_export_exact_bytes_no_signing_or_writes(owner, monkeypatch):
    issuance, _, state = owner
    issued = confirm(issuance, preview(issuance))
    handler = reconciler(owner)
    with pytest.raises(Exception): show(handler, 'abandon')
    shown = show(handler, 'export')
    assert 'card' not in shown and issued['card'] not in str(shown)
    monkeypatch.setattr('sage_lantern.mesh_contact_issuance.sign_contact_card', lambda *args, **kwargs: pytest.fail('must not re-sign card'))
    assert approve(handler, shown) == issued
    with pytest.raises(Exception): approve(handler, shown)
    assert state['puts'] == 2


def test_export_cancel_and_new_revision_race_do_not_export(owner):
    issuance, _, state = owner
    confirm(issuance, preview(issuance))
    handler = reconciler(owner)
    shown = show(handler, 'export')
    confirm(issuance, preview(issuance))
    with pytest.raises(Exception): approve(handler, shown)
    assert state['puts'] == 4


def test_audit_tamper_blocks_monotonic_continuation(owner):
    issuance, ledger, state = owner
    original = reserve(owner)
    handler = reconciler(owner)
    approve(handler, show(handler))
    audit = read_audit(ledger, original)
    state['rows'][audit['record_id']]['payload']['body']['original']['payload']['body']['plan_sha256'] = '0' * 64
    with pytest.raises(Exception): ledger.current()
    with pytest.raises(Exception): preview(issuance)


def test_nonroot_and_wrong_action_no_writes(owner):
    _, _, state = owner
    reserve(owner)
    handler = reconciler(owner)
    with pytest.raises(Exception): handler.handle({}, peer_uid=994)
    with pytest.raises(Exception): show(handler, 'export')
    assert state['puts'] == 1


def test_inflight_original_finish_wins_without_false_abandon_completion(owner, monkeypatch):
    from sage_lantern.mesh_contacts import sign_contact_card
    issuance, ledger, state = owner
    shown_issue = preview(issuance)
    plan = shown_issue['plan']
    state['unknown'] = True
    with pytest.raises(Exception): confirm(issuance, shown_issue)
    state['unknown'] = False
    original = copy.deepcopy(state['rows'][ledger.identifier])
    card = sign_contact_card(ledger.identity, chain_id=plan['binding']['chain_id'],
        radio_public_key=plan['binding']['radio_public_key'], display_name=plan['display_name'],
        revision=plan['revision'], issued_at=plan['issued_at'], expires_at=plan['expires_at'],
        now=1000, findable=plan['findable'], messaging_allowed=plan['messaging_allowed'])
    handler = reconciler(owner)
    shown = show(handler)
    put = ledger.store.put
    def concurrent_finish(identifier, *args, **kwargs):
        result = put(identifier, *args, **kwargs)
        if identifier != ledger.identifier:
            ledger.finish(plan, card)
        return result
    with monkeypatch.context() as context:
        context.setattr(ledger.store, 'put', concurrent_finish)
        with pytest.raises(Exception): approve(handler, shown)
    assert ledger.current()['state'] == 'issued' and ledger.current()['card'] == card.decode()
    assert read_audit(ledger, original)['payload']['body']['decision'] == 'abandon_authorized'
    assert len(state['rows']) == 2
    assert approve(handler, show(handler, 'export'))['card'] == card.decode()


@pytest.mark.parametrize('persist', [False, True])
def test_completion_archive_unknown_requires_fresh_acknowledgement(owner, monkeypatch, persist):
    issuance, ledger, state = owner
    reserve(owner)
    handler = reconciler(owner)
    shown = show(handler)
    put = ledger.store.put
    def uncertain(identifier, kind, revision, payload, **kwargs):
        if payload.get('schema', '').endswith('reconciliation-completion.v1'):
            if persist: put(identifier, kind, revision, payload, **kwargs)
            raise WorkflowOutcomeUnknown()
        return put(identifier, kind, revision, payload, **kwargs)
    with monkeypatch.context() as context:
        context.setattr(ledger.store, 'put', uncertain)
        with pytest.raises(WorkflowOutcomeUnknown): approve(handler, shown)
    assert ledger.current()['state'] == 'abandoned'
    if not persist:
        with pytest.raises(Exception): preview(issuance)
    restarted = reconciler(owner)
    shown = show(restarted, 'acknowledge')
    before = state['puts']
    assert approve(restarted, shown)['state'] == 'abandoned'
    assert state['puts'] == before + (0 if persist else 1)
    assert confirm(issuance, preview(issuance))['revision'] == 2


def test_lock_after_archive_stops_cas_preserves_intent(owner, monkeypatch):
    _, ledger, state = owner
    original = reserve(owner)
    handler = reconciler(owner)
    put = ledger.store.put
    def locked(identifier, *args, **kwargs):
        result = put(identifier, *args, **kwargs)
        state['active'] = False
        return result
    shown = show(handler)
    with monkeypatch.context() as context:
        context.setattr(ledger.store, 'put', locked)
        with pytest.raises(Exception): approve(handler, shown)
    assert state['rows'][ledger.identifier] == original and len(state['rows']) == 2
    state['active'] = True
    with pytest.raises(Exception): approve(handler, shown)
    assert approve(handler, show(handler))['state'] == 'abandoned'


def test_export_cancel_and_expiry_no_card(owner):
    issuance, _, state = owner
    confirm(issuance, preview(issuance))
    handler = reconciler(owner)
    shown = show(handler, 'export')
    handler.handle(dict(op='owner-contact-reconcile-cancel', broker_id='b' * 32, epoch='e' * 32), peer_uid=0)
    with pytest.raises(Exception): approve(handler, shown)
    state['now'] = 1700
    with pytest.raises(Exception): approve(handler, show(handler, 'export'))
    assert state['puts'] == 2
