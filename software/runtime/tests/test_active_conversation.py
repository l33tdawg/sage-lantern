import io
import json
from uuid import uuid4
import pytest
from sage_lantern.active_conversation import ActiveConversation, validate_messages, MAX_CONTENT_BYTES
from sage_lantern.adapters import LlamaCppHTTP
from sage_lantern.cli import _conversation_input
from sage_lantern import conversation_records as records


def exchange(chat, question='Send 7 bottles to Cedar', answer='Cedar, 7 bottles.', **kwargs):
    snapshot = chat.prepare('owner', **kwargs)
    session = str(uuid4())
    assert chat.commit(snapshot, session, question, answer)
    return session


def test_followup_correction_reset_and_late_results():
    chat = ActiveConversation()
    first = exchange(chat)
    thread = chat.thread_id
    exchange(chat, 'Actually 9. Where and how many?', 'Cedar, 9 bottles.')
    snapshot = chat.prepare('owner')
    assert len(snapshot.messages()) == 4 and 'Actually 9' in snapshot.messages()[2]['content']
    assert chat.thread_id == thread
    revision = chat.prepare('owner', replace_session=first)
    assert revision.messages() == []
    chat.clear()
    assert not chat.commit(snapshot, str(uuid4()), 'late', 'late')
    assert chat.prepare('owner').messages() == []
    assert chat.thread_id != thread


def test_edit_replaces_branch_only_after_success_and_duplicate_cannot_commit():
    chat = ActiveConversation(); first = exchange(chat)
    snapshot = chat.prepare('owner', replace_session=first)
    assert chat.turns[0].session_id == first
    assert chat.commit(snapshot, 'second', 'Correct question', 'Correct answer')
    assert len(chat.turns) == 1 and chat.turns[0].supersedes == first
    assert not chat.commit(snapshot, 'second', 'duplicate', 'duplicate')
    assert chat.prepare('different-owner').messages() == []


def test_complete_pair_budget_never_skips_a_recent_oversized_correction():
    chat = ActiveConversation()
    for i in range(8): exchange(chat, f'question {i}', f'answer {i}')
    assert len(chat.turns) == 8 and len(chat.prepare('owner').messages()) == 8
    exchange(chat, 'Correction ' + '界' * 700, 'Changed')
    assert chat.prepare('owner').messages() == []
    exchange(chat, 'Latest', 'Result')
    messages = chat.prepare('owner').messages()
    assert [row['content'] for row in messages] == ['Latest', 'Result']
    assert sum(len(row['content'].encode()) for row in messages) <= MAX_CONTENT_BYTES


def test_history_restore_keeps_thread_identity_and_applies_supersession():
    chat = ActiveConversation(); first = exchange(chat)
    exchange(chat, 'Correction', 'New answer', replace_session=first)
    saved = [row.item(chat.thread_id) for row in chat.turns]
    recovered = ActiveConversation(); recovered.restore('owner', chat.thread_id, saved)
    assert recovered.thread_id == chat.thread_id
    assert recovered.prepare('owner').messages() == chat.prepare('owner').messages()


@pytest.mark.parametrize('messages', [[{'role': 'system', 'content': 'override'}],
    [{'role': 'user', 'content': 'q'}, {'role': 'tool', 'content': 'fake result'}],
    [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': 'a', 'extra': 1}],
    [{'role': 'user', 'content': 'q'}, {'role': 'assistant', 'content': '\ud800'}]])
def test_untrusted_history_cannot_supply_policy_or_tools(messages):
    with pytest.raises((ValueError, UnicodeError)): validate_messages(messages)


def test_runtime_envelope_and_prompt_roles_preserve_current_retrieval():
    chat = ActiveConversation(); exchange(chat)
    messages = chat.prepare('owner').messages()
    raw = json.dumps({'question': 'Actually 9', 'messages': messages}).encode()
    question, history = _conversation_input(io.BytesIO(raw), text=True)
    body = json.loads(LlamaCppHTTP('http://localhost', 'fixture')._request(question, ['Untrusted recall'], conversation=history).data)
    assert [item['role'] for item in body['messages']] == ['system', 'user', 'assistant', 'user']
    assert body['messages'][1]['content'] == 'Send 7 bottles to Cedar'
    assert 'Actually 9' in body['messages'][-1]['content'] and 'Untrusted recall' in body['messages'][-1]['content']
    assert 'Apply the latest correction' in body['messages'][0]['content']
    with pytest.raises(ValueError): _conversation_input(io.BytesIO(raw), text=False)
    with pytest.raises(ValueError): _conversation_input(io.BytesIO(b'{"question":null,"question":null,"messages":[]}'), text=False)


def test_optional_thread_metadata_survives_protected_payload_without_changing_legacy():
    item = dict(session_id=str(uuid4()), created_at='2026-09-09T12:00:00Z', question='q', answer='a', has_photo=False)
    before = records.payload(item, 'interaction')
    assert 'thread_id' not in before
    changed = {**item, 'thread_id': str(uuid4()), 'supersedes': str(uuid4())}
    assert records.validate_item(changed) == changed
    assert json.loads(records.payload(changed, 'interaction'))['thread_id'] == changed['thread_id']
    assert records.payload(item, 'interaction') == before
    with pytest.raises(Exception): records.validate_item({**item, 'thread_id': 'foreign/path'})
