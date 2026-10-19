"""Actual HTTP-handler continuity controls with synthetic owner/broker evidence."""
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sage_lantern.session_context import SessionContext


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'scripts/lantern/unit-a-voice-loop.py'
SPEC = importlib.util.spec_from_file_location('context_binding_voice', SOURCE.resolve())
VOICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VOICE)
TOKEN = 'synthetic-continuity-owner'


@pytest.fixture
def broker(monkeypatch):
    state = {'token': TOKEN, 'context': SessionContext('a' * 32, 'b' * 64)}
    monkeypatch.setattr(VOICE, 'lock_required', lambda: True)
    monkeypatch.setattr(VOICE, 'token_authorized', lambda token: token == state['token'])
    monkeypatch.setattr(VOICE.device_access, 'session_context',
                        lambda token: state['context'] if token == state['token'] else None)
    return state


def process(control):
    _, credential = control.start(str(uuid4()), device_token=TOKEN)
    control.command('stop', credential['session_id'], credential['token'])
    active = control.take(False)
    assert control.begin_processing(active['session_id'])
    return active['session_id']


def seed(control, question, answer='Fixture answer'):
    session = process(control)
    control.remember_completed_source(session, question, answer, TOKEN)
    control.finish(session)
    return session


class Memory:
    def __init__(self, before_return=None):
        self.calls = []
        self.before_return = before_return

    def context(self, query, **scope):
        self.calls.append((query, scope))
        if self.before_return:
            self.before_return()
        if not scope:
            return ['Authorised general memory lookup']
        return ['\n'.join(item['transcript'] for item in scope['selected_items'])]


def request(control, history, payload=None, token=TOKEN):
    raw = json.dumps({'query': 'Continue our plan'} if payload is None else payload).encode()
    handler = VOICE.VoiceHandler.__new__(VOICE.VoiceHandler)
    handler.headers = Message()
    for name, value in {
        'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
        'X-Lantern-Voice': 'control', 'X-Lantern-Session': token,
        'Content-Type': 'application/json', 'Content-Length': str(len(raw)),
    }.items():
        handler.headers[name] = value
    handler.path, handler.command = '/history/context', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /history/context HTTP/1.1'
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.server = SimpleNamespace(control=control, history=history)
    handler.do_POST()
    headers, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body), body


def test_selected_saved_chat_supplies_the_authoritative_branch(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    thread = str(uuid4())
    original = str(uuid4())
    rows = [{'session_id': original, 'thread_id': thread,
             'created_at': '2026-10-09T00:00:00Z',
             'transcript': 'Use Cedar and seven bottles', 'response': 'Noted', 'has_photo': False}]
    control.conversation_request('/conversation/select', {'thread_id': thread}, TOKEN,
                                 SimpleNamespace(request=lambda *args, **kwargs: {'items': rows}))
    process(control)
    memory = Memory()
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, memory))
    assert status == 200 and body['context'] == ['Use Cedar and seven bottles']
    query, scope = memory.calls[0]
    assert query == 'Continue our plan' and scope['thread_id'] == thread
    assert [item['session_id'] for item in scope['selected_items']] == [original]


def test_unsaved_previous_turns_remain_in_the_current_chat(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'Remember the offline deadline')
    process(control)
    memory = Memory()
    assert request(control, VOICE.LocalHistory(tmp_path, memory))[1]['context'] == ['Remember the offline deadline']
    assert not list(tmp_path.iterdir())


def test_new_chat_empty_snapshot_does_not_fall_back_to_old_chats(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'OLD-CHAT-CONTENT')
    control.conversation.clear()
    process(control)
    memory = Memory()
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, memory))
    assert status == 200 and body['context'] == ['']
    assert memory.calls[0][1]['selected_items'] == []


def test_correction_recall_uses_prefix_before_edited_question(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'Keep this prefix')
    target = seed(control, 'Edit this question')
    seed(control, 'Discard this descendant')
    snapshot = control.conversation.prepare(TOKEN, replace_session=target)
    session = process(control)
    control.owner['conversation'] = snapshot
    memory = Memory()
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, memory))
    assert status == 200 and body['context'] == ['Keep this prefix']
    assert control.owner['session_id'] == session


@pytest.mark.parametrize('change', ['cancel', 'clear', 'finish', 'lock', 'epoch'])
def test_late_context_cannot_publish_after_its_binding_changes(tmp_path, broker, change):
    control = VOICE.VoiceControl(30)
    seed(control, 'PRIVATE-CONTINUITY-CONTENT')
    session = process(control)
    def changed():
        if change == 'cancel':
            control.cancel(session)
        elif change == 'clear':
            control.conversation.clear()
        elif change == 'finish':
            control.finish(session)
        elif change == 'lock':
            broker['token'] = None
        else:
            broker['context'] = SessionContext('c' * 32, 'd' * 64)
    memory = Memory(changed)
    status, body, raw = request(control, VOICE.LocalHistory(tmp_path, memory))
    assert status in {403, 409}
    assert 'context' not in body and b'PRIVATE-CONTINUITY-CONTENT' not in raw


def test_cancel_during_encoding_preserves_the_previous_chat(tmp_path, broker, monkeypatch):
    control = VOICE.VoiceControl(30)
    seed(control, 'Retain this prior turn')
    session = process(control)
    original = VOICE.json.dumps
    def encode(value, *args, **kwargs):
        if type(value) is dict and 'context' in value:
            control.cancel(session)
        return original(value, *args, **kwargs)
    monkeypatch.setattr(VOICE.json, 'dumps', encode)
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, Memory()))
    assert status == 409 and body == {'error': 'conversation_changed'}
    assert control.conversation.turns[0].question == 'Retain this prior turn'


def test_client_cannot_supply_a_different_thread(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    process(control)
    memory = Memory()
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, memory),
                              {'query': 'Continue', 'thread_id': str(uuid4())})
    assert status == 503 and 'context' not in body and not memory.calls


def test_wrong_token_is_refused_before_context_lookup(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    process(control)
    memory = Memory()
    assert request(control, VOICE.LocalHistory(tmp_path, memory), token='foreign')[0] == 403
    assert not memory.calls


def test_general_lookup_without_inference_keeps_legacy_contract(tmp_path, broker):
    control = VOICE.VoiceControl(30)
    memory = Memory()
    status, body, _ = request(control, VOICE.LocalHistory(tmp_path, memory))
    assert status == 200 and body['context'] == ['Authorised general memory lookup']
    assert memory.calls[0][1] == {}
