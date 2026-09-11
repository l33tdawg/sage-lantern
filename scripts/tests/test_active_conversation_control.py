import uuid
from types import SimpleNamespace
import pytest
from test_question_correction import MODULE, TOKEN, request

@pytest.fixture
def control(monkeypatch):
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == TOKEN)
    monkeypatch.setattr(MODULE, 'lock_required', lambda: True)
    return MODULE.VoiceControl(30)


def admitted(control):
    _, auth = control.start(str(uuid.uuid4()), device_token=TOKEN)
    control.command('stop', auth['session_id'], auth['token'])
    session = control.take(False)
    assert control.begin_processing(session['session_id'])
    return session['session_id']


def test_completed_only_same_owner_and_lock_clear(control):
    session = admitted(control)
    assert control.conversation_messages(session) == []
    metadata = control.conversation_metadata(session)
    control.remember_completed_source(session, 'Cedar, 7 bottles', 'Understood', TOKEN)
    control.finish(session)
    followup = admitted(control)
    assert control.conversation_metadata(followup)['thread_id'] == metadata['thread_id']
    assert control.conversation_messages(followup)[0]['content'] == 'Cedar, 7 bottles'
    control.revoke_device(clear_community=False)
    control.remember_completed_source(followup, 'Late', 'Late', TOKEN)
    assert control.conversation_messages(followup) == [] and not control.conversation.turns


def test_cancel_does_not_commit_and_new_chat_is_fresh(control):
    first = admitted(control)
    control.remember_completed_source(first, 'q', 'a', TOKEN); control.finish(first)
    second = admitted(control); control.cancel(second)
    control.remember_completed_source(second, 'cancelled', 'partial', TOKEN); control.finish(second)
    assert len(control.conversation.turns) == 1
    assert request(control, {}, path='/clear')[0] == 202
    assert not control.conversation.turns


def test_authorized_select_and_reopen_with_legacy_identity(control):
    first = admitted(control)
    control.remember_completed_source(first, 'Legacy saved question', 'Saved answer', TOKEN); control.finish(first)
    item = control.conversation.turns[0].item(control.conversation.thread_id)
    item.pop('thread_id')
    calls = []
    def history(action, payload, access_check):
        assert access_check(); calls.append(action); return {'items': [item]}
    saved = SimpleNamespace(request=history)
    code, body = request(control, {'thread_id': first}, path='/conversation/select', history=saved)
    assert code == 200 and body['thread_id'] == first and len(body['items']) == 1
    assert request(control, {}, path='/conversation/state', token='foreign')[0] == 403
    assert request(control, {'thread_id': str(uuid.uuid4())}, path='/conversation/select', history=saved)[0] == 404
    session = admitted(control)
    assert 'Legacy saved' in control.conversation_messages(session)[0]['content']
    assert request(control, {'thread_id': first}, path='/conversation/select', history=saved)[0] == 409


def test_revoke_during_history_load_cannot_restore(control):
    def history(*args, **kwargs):
        control.revoke_device(clear_community=False)
        return {'items': [{'session_id': identifier, 'created_at': '2026-09-09T00:00:00Z', 'transcript': 'q', 'response': 'a'}]}
    identifier = str(uuid.uuid4())
    assert request(control, {'thread_id': identifier}, path='/conversation/select', history=SimpleNamespace(request=history))[0] == 409
    assert not control.conversation.turns


@pytest.mark.parametrize('text', [False, True])
def test_real_child_pipe_carries_history_without_argv_or_disk(control, tmp_path, monkeypatch, text):
    import argparse, subprocess, sys, json
    from pathlib import Path
    from unittest.mock import Mock
    first = admitted(control)
    control.remember_completed_source(first, 'Cedar, 7 bottles', 'Understood', TOKEN); control.finish(first)
    if text:
        _, auth = control.start(str(uuid.uuid4()), device_token=TOKEN, question='Follow up',
                                correction_epoch=control.correction_epoch)
        session = control.take(False)['session_id']; assert control.begin_processing(session)
    else:
        session = admitted(control)
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, audio_dir=tmp_path,
        runtime=Path('/mock/runtime'), config=tmp_path / 'config', processing_timeout=5))
    loop.control = control; loop.write_state = Mock(); loop.device_session = (session, TOKEN, True)
    original_popen = subprocess.Popen
    program = '''import sys,json
value=json.load(sys.stdin)
assert value['messages'][0]['content']=='Cedar, 7 bottles'
assert len(value['messages'])==2
question=value['question'] or 'Spoken follow up'
print(json.dumps({'type':'result','result':{'session_id':sys.argv[1],'transcript':question,'response':'Cedar, 7 bottles.','timings_ms':{}}}),flush=True)
'''
    def launch(argv, **kwargs):
        assert '--conversation-stdin' in argv
        assert 'Cedar' not in ' '.join(argv) and 'Follow up' not in ' '.join(argv)
        return original_popen([sys.executable, '-B', '-c', program, session], **kwargs)
    monkeypatch.setattr(MODULE.subprocess, 'Popen', launch)
    loop.process(session, tmp_path / 'unused.wav', question='Follow up' if text else None)
    assert any(call.args[0] == 'complete' for call in loop.write_state.call_args_list)
    assert not loop.children and not list(tmp_path.glob('*conversation*'))


def test_revoke_during_response_encoding_redacts_thread(control, monkeypatch):
    session = admitted(control)
    control.remember_completed_source(session, 'Private fixture', 'Answer', TOKEN); control.finish(session)
    original = MODULE.json.dumps
    def encode(value, *args, **kwargs):
        if type(value) is dict and 'thread_id' in value:
            control.revoke_device(clear_community=False)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(MODULE.json, 'dumps', encode)
    assert request(control, {}, path='/conversation/state') == (403, {'error': 'device_locked'})


def test_clear_after_snapshot_before_handler_cannot_publish_old_thread(control, monkeypatch):
    session = admitted(control)
    control.remember_completed_source(session, 'Private fixture', 'Answer', TOKEN); control.finish(session)
    original = control.conversation_request
    def read(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        control.revoke_device(clear_community=False)
        return snapshot
    monkeypatch.setattr(control, 'conversation_request', read)
    assert request(control, {}, path='/conversation/state') == (403, {'error': 'device_locked'})

