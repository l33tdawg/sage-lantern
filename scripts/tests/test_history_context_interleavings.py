"""Independent actual-handler interleavings; no services, devices, or private data."""
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
SPEC = importlib.util.spec_from_file_location('independent_binding_voice', SOURCE.resolve())
VOICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VOICE)
TOKEN = 'synthetic-independent-owner'


@pytest.fixture
def broker(monkeypatch):
    value = {'tokens': {TOKEN}, 'context': SessionContext('a' * 32, 'b' * 64)}
    monkeypatch.setattr(VOICE, 'lock_required', lambda: True)
    monkeypatch.setattr(VOICE, 'token_authorized', lambda token: token in value['tokens'])
    monkeypatch.setattr(VOICE.device_access, 'session_context', lambda token: value['context'] if token in value['tokens'] else None)
    return value


def processing(control, token=TOKEN):
    _, credential = control.start(str(uuid4()), device_token=token)
    control.command('stop', credential['session_id'], credential['token'])
    active = control.take(False)
    assert control.begin_processing(active['session_id'])
    return active['session_id']


def seed(control, question):
    session = processing(control)
    control.remember_completed_source(session, question, 'Synthetic answer', TOKEN)
    control.finish(session)


class Memory:
    def __init__(self, during=None):
        self.calls = []
        self.during = during

    def context(self, query, **scope):
        self.calls.append((query, scope))
        if self.during is not None:
            self.during()
        return ['GLOBAL-LEGACY-FIXTURE' if not scope else '\n'.join(row['transcript'] for row in scope['selected_items'])]


class ReadBoundary(io.BytesIO):
    def __init__(self, data, during):
        super().__init__(data)
        self.during = during

    def read(self, size=-1):
        if self.during is not None:
            callback, self.during = self.during, None
            callback()
        return super().read(size)


def request(control, memory, *, during_read=None, token=TOKEN, protected=False, during_headers=None):
    raw = json.dumps({'query': 'Query admitted for the original request'}).encode()
    handler = VOICE.VoiceHandler.__new__(VOICE.VoiceHandler)
    handler.headers = Message()
    for key, value in {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
                       'X-Lantern-Voice': 'control', 'X-Lantern-Session': token,
                       'Content-Type': 'application/json', 'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    history = VOICE.LocalHistory(ROOT / 'never-initialized-history', memory)
    if protected:
        history.media_mode = 'protected'
    handler.server = SimpleNamespace(control=control, history=history)
    handler.path, handler.command = '/history/context', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /history/context HTTP/1.1'
    handler.rfile = ReadBoundary(raw, during_read)
    handler.wfile = io.BytesIO()
    if during_headers:
        original = handler.end_headers
        def end_headers():
            original()
            during_headers()
        handler.end_headers = end_headers
    handler.do_POST()
    headers, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body) if body else None, body


@pytest.mark.parametrize('protected', [False, True])
def test_owner_replacement_during_body_read_cannot_rebind_old_query_to_new_chat(broker, protected):
    control = VOICE.VoiceControl(30)
    seed(control, 'ORIGINAL-CHAT-A')
    original = processing(control)
    def replacement():
        control.finish(original)
        control.conversation.clear()
        seed(control, 'NEW-CHAT-B-PRIVATE-FIXTURE')
        processing(control)
    memory = Memory()
    status, body, raw = request(control, memory, during_read=replacement, protected=protected)
    assert status in {403, 409}
    assert 'context' not in body and b'NEW-CHAT-B-PRIVATE-FIXTURE' not in raw
    assert not memory.calls
    assert control.conversation.turns[0].question == 'NEW-CHAT-B-PRIVATE-FIXTURE'


def test_owner_finish_during_body_read_cannot_fall_back_to_general_recall(broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'ORIGINAL-CHAT-A')
    original = processing(control)
    memory = Memory()
    status, body, raw = request(control, memory, during_read=lambda: control.finish(original))
    assert status in {403, 409}
    assert 'context' not in body and b'GLOBAL-LEGACY-FIXTURE' not in raw
    assert not memory.calls


def test_general_request_admitted_without_owner_does_not_pick_up_new_processing_scope(broker):
    control = VOICE.VoiceControl(30)
    def begin():
        seed(control, 'NEW-CHAT-B-PRIVATE-FIXTURE')
        processing(control)
    memory = Memory()
    status, body, _ = request(control, memory, during_read=begin)
    assert status == 200 and body['context'] == ['GLOBAL-LEGACY-FIXTURE']
    assert memory.calls[0][1] == {}


@pytest.mark.parametrize('change', ['cancel', 'complete', 'clear', 'epoch'])
def test_same_owner_changes_during_body_read_refuse_before_lookup(broker, change):
    control = VOICE.VoiceControl(30)
    seed(control, 'PREVIOUS-AUTHORISED-TURN')
    session = processing(control)
    def mutate():
        if change == 'cancel':
            control.cancel(session)
        elif change == 'complete':
            control.remember_completed_source(session, 'New result', 'Completed', TOKEN)
        elif change == 'clear':
            control.conversation.clear()
        else:
            broker['context'] = SessionContext('c' * 32, 'd' * 64)
    memory = Memory()
    status, body, _ = request(control, memory, during_read=mutate)
    assert status in {403, 409} and 'context' not in body
    assert not memory.calls


def test_other_authorised_token_cannot_use_current_owner_scope(broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'OWNER-A-FIXTURE')
    processing(control)
    other = 'synthetic-authorised-other-session'
    broker['tokens'].add(other)
    memory = Memory()
    status, body, _ = request(control, memory, token=other)
    assert status in {403, 409} and 'context' not in body and not memory.calls


def test_owner_snapshot_replacement_at_same_revision_invalidates_captured_selection(broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'PREFIX')
    target = processing(control)
    control.remember_completed_source(target, 'EDIT-TARGET', 'Old answer', TOKEN)
    control.finish(target)
    processing(control)
    captured = control.owner['conversation']
    replacement = control.conversation.prepare(TOKEN, replace_session=target)
    assert captured.revision == replacement.revision and captured is not replacement
    memory = Memory(lambda: control.owner.update(conversation=replacement))
    status, body, raw = request(control, memory)
    assert status == 409 and body == {'error': 'conversation_changed'}
    assert b'EDIT-TARGET' not in raw
    assert control.conversation.turns[0].question == 'PREFIX'


@pytest.mark.parametrize('change', ['cancel', 'complete', 'clear', 'replace', 'epoch', 'lock'])
def test_selected_lookup_changes_before_response_are_fenced(broker, change):
    control = VOICE.VoiceControl(30)
    seed(control, 'PRIOR-PRIVATE-FIXTURE')
    session = processing(control)
    def mutate():
        if change == 'cancel':
            control.cancel(session)
        elif change == 'complete':
            control.remember_completed_source(session, 'Completed result', 'Answer', TOKEN)
        elif change == 'clear':
            control.conversation.clear()
        elif change == 'replace':
            control.finish(session)
            processing(control)
        elif change == 'epoch':
            broker['context'] = SessionContext('c' * 32, 'd' * 64)
        else:
            broker['tokens'].clear()
    memory = Memory(mutate)
    status, body, raw = request(control, memory)
    assert status in {403, 409} and 'context' not in body and b'PRIOR-PRIVATE-FIXTURE' not in raw
    if change == 'cancel':
        assert control.conversation.turns[0].question == 'PRIOR-PRIVATE-FIXTURE'
        assert control.device_token == TOKEN


def test_cancel_after_headers_sends_no_context_and_preserves_valid_chat(broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'RETAINED-CHAT-FIXTURE')
    session = processing(control)
    _, _, raw = request(control, Memory(), during_headers=lambda: control.cancel(session))
    assert b'RETAINED-CHAT-FIXTURE' not in raw
    assert control.device_token == TOKEN and control.conversation.turns[0].question == 'RETAINED-CHAT-FIXTURE'


def test_stable_empty_and_correction_prefix_keep_their_frozen_scope(broker):
    control = VOICE.VoiceControl(30)
    seed(control, 'PREFIX')
    target = processing(control)
    control.remember_completed_source(target, 'EDIT-TARGET', 'Old answer', TOKEN)
    control.finish(target)
    seed(control, 'DESCENDANT')
    corrected = control.conversation.prepare(TOKEN, replace_session=target)
    session = processing(control)
    control.owner['conversation'] = corrected
    memory = Memory()
    status, body, _ = request(control, memory)
    assert status == 200 and body['context'] == ['PREFIX']
    assert [row['transcript'] for row in memory.calls[0][1]['selected_items']] == ['PREFIX']
    control.finish(session)
    control.conversation.clear()
    processing(control)
    memory = Memory()
    status, body, _ = request(control, memory)
    assert status == 200 and body['context'] == ['']
    assert memory.calls[0][1]['selected_items'] == []
