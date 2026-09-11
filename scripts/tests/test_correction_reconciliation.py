import json
import uuid
import argparse
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

from sage_lantern.correction_attempts import CorrectionAttempts
from sage_lantern.workflow_store import WorkflowUnavailable
from test_workflow_store import setup
from test_question_correction import MODULE, TOKEN, control, payload, request


@pytest.fixture
def bound(control, setup):
    ledger = CorrectionAttempts(setup.store)
    control.correction_journal = lambda operation, **arguments: getattr(ledger, operation)(**arguments)
    return control, ledger, setup


def test_real_handler_worker_take_completion_and_lost_response(bound):
    control, ledger, storage = bound
    body = payload(control)
    status, result = request(control, body)
    assert status == 201
    session = control.take(False)
    assert session['question'] == body['question']
    assert session['request_id'] == body['request_id']
    assert control.begin_processing(session['session_id'])
    assert request(control, body)[0] == 409
    observed = control.reconcile_correction({'source_id': body['source_id']}, TOKEN)
    assert observed['status'] == 'accepted' and observed['session_id'] == result['session_id']
    ledger.finish(source_id=body['source_id'], attempt_id=body['request_id'], session_id=result['session_id'],
                  succeeded=True, access_check=lambda: True)
    control.finish(result['session_id'])
    assert CorrectionAttempts(storage.store).reconcile(body['source_id'], access_check=lambda: True)['status'] == 'completed'
    persisted = json.dumps(storage.records)
    assert body['question'] not in persisted and TOKEN not in persisted
    assert not any(key in persisted for key in ('image_base64', 'correction_fingerprint'))


def test_fresh_token_rotation_reconcile_not_old_token(bound, monkeypatch):
    control, ledger, storage = bound
    body = payload(control)
    assert request(control, body)[0] == 201
    control.revoke_device()
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == 'new-owner-token')
    with pytest.raises(MODULE.ControlError):
        control.reconcile_correction({'source_id': body['source_id']}, TOKEN)
    assert control.reconcile_correction({'source_id': body['source_id']}, 'new-owner-token')['status'] == 'accepted'


def test_journal_commit_lost_response_prevents_admission_and_restart_retry(bound):
    control, ledger, storage = bound
    body = payload(control)
    storage.put_status = 'timeout'
    status, result = request(control, body)
    assert status == 503 and result == {'error': 'correction_outcome_unknown'}
    assert control.owner is None
    storage.put_status = 200
    restored = CorrectionAttempts(storage.store)
    assert restored.reconcile(body['source_id'], access_check=lambda: True)['status'] == 'reserved'
    calls = []
    restored.submit(source_id=body['source_id'], source_kind='history', attempt_id=str(uuid.uuid4()),
                    access_check=lambda: True, admit=lambda: calls.append(True))
    assert calls == []


def test_lock_before_worker_admission_keeps_uncertain_evidence(bound):
    control, ledger, storage = bound
    source = str(uuid.uuid4())
    preview = ledger.reconcile(source, access_check=lambda: True, approval_context='a' * 64)
    approval = dict(challenge=preview['challenge'], expected_revision=preview['expected_revision'],
                    consent=True, acknowledge_unknown=True)
    calls = []
    count = [0]
    def check():
        return count[0] < 2
    original = storage.store.put
    def put(*args, **kwargs):
        result = original(*args, **kwargs)
        count[0] += 1
        return result
    storage.store.put = put
    with pytest.raises(WorkflowUnavailable):
        ledger.submit(source_id=source, source_kind='history', attempt_id=str(uuid.uuid4()),
                      access_check=check, admit=lambda: calls.append(True), new_execution=approval,
                      approval_context='a' * 64)
    assert calls == []
    assert ledger.reconcile(source, access_check=lambda: True)['status'] == 'admitting'


def test_missing_journal_fails_closed(control):
    body = payload(control)
    control.correction_journal = None
    assert request(control, body)[0] == 503
    assert control.owner is None


def test_reconciliation_http_is_authenticated_metadata_only(bound):
    control, ledger, storage = bound
    body = payload(control)
    assert request(control, body)[0] == 201
    query = {'source_id': body['source_id']}
    status, response = request(control, query, path='/correct/reconcile')
    assert status == 200 and response['status'] == 'accepted'
    assert request(control, query, path='/correct/reconcile', token='wrong')[0] == 403
    assert request(control, dict(query, question='private'), path='/correct/reconcile')[0] == 400
    assert body['question'] not in json.dumps(response) and TOKEN not in json.dumps(response)


@pytest.mark.parametrize('failure', [False, True])
def test_normal_worker_completion_hook_or_unknown_failure(bound, tmp_path, failure):
    control, ledger, storage = bound
    body = payload(control)
    assert request(control, body)[0] == 201
    session = control.take(False)
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, audio_dir=tmp_path))
    loop.control = control
    loop.capture = Mock(side_effect=AssertionError('no audio capture'))
    loop.require_session_access = Mock()
    loop.begin_history_session = Mock()
    loop.write_state = Mock()
    loop.process = Mock(side_effect=RuntimeError('synthetic failure') if failure else None)
    loop.run_session(session)
    loop.capture.assert_not_called()
    assert loop.process.call_args.kwargs == {'question': body['question']}
    assert control.owner is None
    observed = CorrectionAttempts(storage.store).reconcile(body['source_id'], access_check=lambda: True)
    assert observed['status'] == ('failed' if failure else 'completed')
    assert observed['retry_allowed'] is False


def test_accepted_write_unknown_cancels_owner_without_erasing_evidence(bound):
    control, ledger, storage = bound
    body = payload(control)
    original = storage.store.put
    calls = []
    def put(*args, **kwargs):
        calls.append(True)
        if len(calls) == 3:
            storage.put_status = 'timeout'
        return original(*args, **kwargs)
    storage.store.put = put
    assert request(control, body)[0] == 503
    assert control.owner['action'] == 'cancel'
    storage.put_status = 200
    assert ledger.reconcile(body['source_id'], access_check=lambda: True)['status'] == 'accepted'


def test_service_activity_epoch_and_drain_fence(bound):
    from sage_lantern.conversation_service import ConversationService
    control, ledger, storage = bound
    service = ConversationService(None, None)
    service.protected_storage = True
    service._owner = SimpleNamespace(valid=lambda: True)
    service._correction_attempts = ledger
    epoch = ['a' * 32]
    service._epoch_reader = lambda: epoch[0]
    source = str(uuid.uuid4())
    assert service.correction_operation('reconcile', source_id=source,
        access_check=lambda: True)['status'] == 'unrecorded'
    storage.change = lambda: epoch.__setitem__(0, 'b' * 32)
    with pytest.raises(WorkflowUnavailable):
        service.correction_operation('reconcile', source_id=source, access_check=lambda: True)
    assert service._active_threads == {}
    service.stopped.set()
    with pytest.raises(RuntimeError):
        service.correction_operation('reconcile', source_id=source, access_check=lambda: True)
