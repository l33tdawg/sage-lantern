import copy
import base64
import uuid
from types import SimpleNamespace

import pytest

from sage_lantern.correction_attempts import CorrectionAttempts, KIND
from sage_lantern.workflow_store import WorkflowUnavailable
from test_workflow_store import setup
from test_question_correction import MODULE, TOKEN, control, payload, request
from test_unit_a_voice_photo import jpeg


CONTEXT = 'a' * 64


def approval(ledger, source, context=CONTEXT):
    view = ledger.reconcile(source, access_check=lambda: True, approval_context=context)
    assert view['retry_allowed'] is False and view['continuity'] == 'current_snapshot_only'
    return dict(challenge=view['challenge'], expected_revision=view['expected_revision'],
                consent=True, acknowledge_unknown=True)


def execute(ledger, source, calls, *, grant=None, context=CONTEXT, attempt=None):
    attempt = attempt or str(uuid.uuid4())
    def admit():
        session = str(uuid.uuid4())
        calls.append(session)
        return session
    return ledger.submit(source_id=source, source_kind='history', attempt_id=attempt,
        access_check=lambda: True, admit=admit, approval_context=context,
        new_execution=grant if grant is not None else approval(ledger, source, context))


@pytest.mark.parametrize('state', ['completed', 'failed', 'accepted', 'admitting', 'reserved'])
def test_explicit_repeat_retains_prior_evidence(setup, state):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    first = execute(ledger, source, calls)
    key = ledger._key(source)
    record = setup.records[key]
    record['payload']['state'] = state
    if state in {'reserved', 'admitting'}:
        record['payload']['session_id'] = None
    previous = copy.deepcopy(record)
    second = execute(ledger, source, calls)
    assert second['attempt_id'] != first['attempt_id'] and len(calls) == 2
    current = setup.records[key]
    archive = setup.records[current['payload']['previous']]
    assert archive['payload']['record'] == previous
    assert current['payload']['authorization']['expected_revision'] == previous['revision']
    assert current['payload']['source_id'] == source
    ledger.finish(source_id=source, attempt_id=second['attempt_id'], session_id=second['session_id'],
                  succeeded=True, access_check=lambda: True)
    third = execute(ledger, source, calls)
    assert len(calls) == 3 and third['attempt_id'] != second['attempt_id']
    assert archive['payload']['record'] == previous
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, attempt=first['attempt_id'])
    assert len(calls) == 3


@pytest.mark.parametrize('field,value', [('consent', False), ('acknowledge_unknown', False),
    ('expected_revision', True), ('expected_revision', 99), ('challenge', 'unknown')])
def test_approval_denial_never_admits(setup, field, value):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    grant = approval(ledger, source)
    grant[field] = value
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=grant)
    assert calls == [] and setup.records == {}


def test_single_use_context_restart_and_revision_fences(setup):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    grant = approval(ledger, source)
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=grant, context='b' * 64)
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=grant)
    fresh = approval(ledger, source)
    with pytest.raises(WorkflowUnavailable):
        execute(CorrectionAttempts(setup.store), source, calls, grant=fresh)
    left, right = approval(ledger, source), approval(ledger, source)
    execute(ledger, source, calls, grant=left)
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=right)
    assert len(calls) == 1


def test_epoch_change_and_restored_missing_record_require_new_explicit_approval(setup):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    execute(ledger, source, calls)
    stale = approval(ledger, source)
    setup.storage['vault_generation'] = '2'
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=stale)
    setup.records.clear()
    restored = CorrectionAttempts(setup.store)
    view = restored.reconcile(source, access_check=lambda: True, approval_context=CONTEXT)
    assert view['status'] == 'unrecorded' and view['risk_acknowledgement_required'] is True
    assert view['retry_allowed'] is False
    with pytest.raises(WorkflowUnavailable):
        restored.submit(source_id=source, source_kind='history', attempt_id=str(uuid.uuid4()),
                        access_check=lambda: True, admit=lambda: calls.append('bad'))
    execute(restored, source, calls)
    assert len(calls) == 2


def test_expired_preview_no_mutation(setup, monkeypatch):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    grant = approval(ledger, source)
    monkeypatch.setattr('sage_lantern.correction_attempts.time.monotonic', lambda: float('inf'))
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=grant)
    assert calls == [] and setup.records == {}


def test_archive_write_unknown_does_not_admit_or_erase_prior(setup):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    execute(ledger, source, calls)
    previous = copy.deepcopy(setup.records[ledger._key(source)])
    grant = approval(ledger, source)
    setup.put_status = 'timeout'
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls, grant=grant)
    assert setup.records[ledger._key(source)] == previous and len(calls) == 1
    setup.put_status = 200
    execute(ledger, source, calls)
    assert len(calls) == 2


def test_http_repeat_same_history_source_and_busy_denial(control):
    body = payload(control)
    source = body['source_id']
    status, first = request(control, body)
    assert status == 201
    view_status, view = request(control, {'source_id': source}, path='/correct/reconcile')
    assert view_status == 200
    repeated = dict(body, request_id=str(uuid.uuid4()), source_kind='history', question='New correction',
        new_execution=dict(challenge=view['challenge'], expected_revision=view['expected_revision'],
                           consent=True, acknowledge_unknown=True))
    history = SimpleNamespace(media_mode='protected', request=lambda *args, **kwargs:
                              {'items': [{'session_id': source, 'has_photo': False}]})
    assert request(control, repeated, history=history)[0] == 409
    control.finish(first['session_id'])
    status, second = request(control, repeated, history=history)
    assert status == 201 and first['session_id'] != second['session_id']
    session = control.take(False)
    assert session['source_id'] == source and session['question'] == 'New correction'
    assert request(control, repeated, history=history)[0] == 409


def test_delayed_accepted_failure_never_cancels_fresh_owner(control, setup, monkeypatch):
    from sage_lantern.workflow_store import WorkflowOutcomeUnknown
    body = payload(control)
    original = setup.store.put
    replacement = []
    def put(*args, **kwargs):
        result = original(*args, **kwargs)
        if result['payload'].get('state') == 'accepted':
            old_session = control.owner['session_id']
            control.revoke_device()
            control.finish(old_session)
            monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == 'fresh-owner')
            status, credentials = control.start(str(uuid.uuid4()), device_token='fresh-owner')
            assert status == 201
            replacement.append(dict(control.owner))
            raise WorkflowOutcomeUnknown()
        return result
    setup.store.put = put
    assert request(control, body)[0] == 503
    assert control.owner == replacement[0]
    assert control.device_token == 'fresh-owner'
    assert any(row['payload'].get('state') == 'accepted' for row in setup.records.values())


def test_unknown_source_cas_preserves_both_attempts_before_new_explicit_execution(setup):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    first = execute(ledger, source, calls)
    original = setup.store.put
    count = [0]
    def put(*args, **kwargs):
        count[0] += 1
        if count[0] == 2:
            setup.put_status = 'timeout'
        return original(*args, **kwargs)
    setup.store.put = put
    with pytest.raises(WorkflowUnavailable):
        execute(ledger, source, calls)
    uncertain = ledger._read(source, lambda: True)
    assert uncertain['payload']['state'] == 'reserved' and len(calls) == 1
    preserved = setup.records[uncertain['payload']['previous']]
    assert preserved['payload']['record']['payload']['attempt_id'] == first['attempt_id']
    setup.put_status = 200
    execute(ledger, source, calls)
    assert len(calls) == 2
    newest = ledger._read(source, lambda: True)
    assert setup.records[newest['payload']['previous']]['payload']['record'] == uncertain


def test_late_http_auth_recheck_never_revokes_replacement(control, monkeypatch):
    body = payload(control)
    original = control.correct
    replacement = []
    def correct(*args, **kwargs):
        result = original(*args, **kwargs)
        old_session = result[1]['session_id']
        control.revoke_device()
        control.finish(old_session)
        monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == 'fresh-owner')
        control.start(str(uuid.uuid4()), device_token='fresh-owner')
        replacement.append(dict(control.owner))
        return result
    control.correct = correct
    assert request(control, body)[0] == 403
    assert control.owner == replacement[0] and control.device_token == 'fresh-owner'


def test_repeat_history_photo_keeps_original_source_binding(control):
    preview, original = jpeg(), jpeg(1280, 720)
    control.completed_source.update(has_photo=True, correction_photo=(preview, original))
    body = payload(control)
    status, first = request(control, body)
    assert status == 201
    control.finish(first['session_id'])
    source = body['source_id']
    _, view = request(control, {'source_id': source}, path='/correct/reconcile')
    def history_request(action, value, **kwargs):
        assert kwargs['access_check']() is True
        if action == 'list':
            return {'items': [{'session_id': source, 'has_photo': True}]}
        assert action == 'photo' and value == {'session_id': source}
        return {'image_base64': base64.b64encode(original).decode()}
    history = SimpleNamespace(media_mode='protected', request=history_request)
    repeated = dict(body, request_id=str(uuid.uuid4()), source_kind='history',
        image_base64=base64.b64encode(preview).decode(),
        new_execution=dict(challenge=view['challenge'], expected_revision=view['expected_revision'],
                           consent=True, acknowledge_unknown=True))
    assert request(control, repeated, history=history)[0] == 201
    assert control.owner['original'] == original and control.owner['source_id'] == source


def test_failure_cancellation_requires_exact_session_token_and_epoch(control):
    body = payload(control)
    _, credentials = request(control, body)
    before = dict(control.owner)
    epoch = control.correction_epoch
    control.cancel_correction_attempt(str(uuid.uuid4()), TOKEN, epoch)
    control.cancel_correction_attempt(credentials['session_id'], 'wrong', epoch)
    control.cancel_correction_attempt(credentials['session_id'], TOKEN, epoch + 1)
    assert control.owner == before
    control.cancel_correction_attempt(credentials['session_id'], TOKEN, epoch)
    assert control.owner['action'] == 'cancel'
    assert control.device_token == TOKEN and control.correction_epoch == epoch


def test_late_previous_completion_cannot_overwrite_new_attempt(setup):
    ledger = CorrectionAttempts(setup.store)
    source, calls = str(uuid.uuid4()), []
    first = execute(ledger, source, calls)
    second = execute(ledger, source, calls)
    with pytest.raises(WorkflowUnavailable):
        ledger.finish(source_id=source, attempt_id=first['attempt_id'], session_id=first['session_id'],
                      succeeded=True, access_check=lambda: True)
    current = ledger.reconcile(source, access_check=lambda: True)
    assert current == second
