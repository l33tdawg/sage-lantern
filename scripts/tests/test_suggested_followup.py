import uuid
import pytest
from test_active_conversation_control import control, admitted
from test_question_correction import MODULE, TOKEN, request


def completed(control):
    session = admitted(control)
    control.remember_completed_source(session, 'Explain yeast.', 'Complete explanation.', TOKEN)
    control.finish(session)
    return session


def body(source, **changes):
    return dict(request_id=str(uuid.uuid4()), source_id=source, question='How does warmth affect it?', **changes)


def test_typed_followup_keeps_conversation_and_does_not_record(control):
    source = completed(control); thread = control.conversation.thread_id
    payload = body(source)
    code, auth = request(control, payload, path='/followup')
    assert code == 201
    assert request(control, payload, path='/followup') == (200, auth)
    owner = control.take(False)
    assert owner['question'] == payload['question'] and control.action(auth['session_id']) == 'stop'
    assert control.begin_processing(auth['session_id'])
    assert control.conversation_messages(auth['session_id'])[0]['content'] == 'Explain yeast.'
    assert control.conversation_metadata(auth['session_id']) == {'thread_id': thread}
    control.remember_completed_source(auth['session_id'], payload['question'], 'The next answer.', TOKEN)
    control.finish(auth['session_id'])
    assert len(control.conversation.turns) == 2
    assert request(control, payload, path='/followup')[0] == 409
    assert len(control.conversation.turns) == 2


def test_retries_cannot_change_text_source_or_owner(control):
    source = completed(control); payload = body(source)
    assert request(control, payload, path='/followup')[0] == 201
    for changed in ({**payload, 'question': 'Different'}, {**payload, 'source_id': str(uuid.uuid4())}):
        assert request(control, changed, path='/followup')[0] == 409
    assert request(control, payload, path='/followup', token='foreign')[0] == 403


def test_no_request_before_submit_and_stale_source_rejected(control):
    source = completed(control)
    assert control.owner is None and len(control.conversation.turns) == 1
    assert request(control, body(str(uuid.uuid4())), path='/followup')[0] == 409
    control.conversation.clear()
    assert request(control, body(source), path='/followup')[0] == 409
    assert control.owner is None


@pytest.mark.parametrize('change', [{'question': ''}, {'question': 'x' * 4097}, {'question': False},
                                   {'request_id': 'bad'}, {'source_id': '../bad'}, {'extra': 'field'}])
def test_invalid_submission_rejected(control, change):
    source = completed(control); payload = {**body(source), **change}
    assert request(control, payload, path='/followup')[0] == 400
    assert control.owner is None


def test_lock_and_restart_reject_old_suggestion(control):
    source = completed(control); payload = body(source)
    control.revoke_device(clear_community=False)
    assert request(control, payload, path='/followup')[0] == 409
    restarted = MODULE.VoiceControl(30)
    assert request(restarted, payload, path='/followup')[0] == 409
