"""Saved-chat context through the real handler, service, CLI and request builder.

Storage stays in tmp_path. Broker evidence and SDK responses are synthetic; the
context HTTP adapter is routed in process, and no model or device is contacted.
"""
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sage_lantern
from sage_lantern.adapters import LanternConversationContext, LlamaCppHTTP
from sage_lantern.cli import _conversation_input
from sage_lantern.conversation_sage import ConversationSage
from sage_lantern.conversation_service import ConversationService
from sage_lantern.conversation_store import ConversationStore
from sage_lantern.session_context import SessionContext


REPO = next(parent for parent in Path(__file__).resolve().parents
            if (parent / 'scripts/lantern/unit-a-voice-loop.py').is_file()
            and (parent / 'software/runtime/src/sage_lantern/__init__.py').is_file())
TOKEN = 'synthetic-shared-seam-owner'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# These imports deliberately read the actual shared files, not prior candidates.
VOICE = load('chat_context_flow_voice', REPO / 'scripts/lantern/unit-a-voice-loop.py')
SDK_FIXTURE = load('chat_context_flow_sdk_fixture', REPO / 'software/runtime/tests/test_conversation_service.py')


def http(control, history, path, payload):
    raw = json.dumps(payload).encode()
    handler = VOICE.VoiceHandler.__new__(VOICE.VoiceHandler)
    handler.headers = Message()
    for key, value in {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
                       'X-Lantern-Voice': 'control', 'X-Lantern-Session': TOKEN,
                       'Content-Type': 'application/json', 'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    handler.server = SimpleNamespace(control=control, history=history)
    handler.path, handler.command = path, 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', f'POST {path} HTTP/1.1'
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.do_POST()
    headers, body = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    return int(headers.split()[1]), json.loads(body), body


class Seam:
    def __init__(self, tmp_path):
        self.store_path = tmp_path.resolve() / 'private-history'
        self.history_path = tmp_path.resolve() / 'history-preferences'
        VOICE.LocalHistory(self.history_path).request('list', {})
        self.control = VOICE.VoiceControl(30)
        self.open()

    def open(self):
        self.store = ConversationStore(self.store_path)
        self.client = SDK_FIXTURE.FakeConversationSDK(self.store)
        self.backend = ConversationSage(self.client, agent_id='private-agent', domain='local-field-knowledge',
            classification=1, journal=self.store, is_tombstoned=self.store.is_suppressed, ledger_consent=True)
        self.memory = ConversationService(self.store, self.backend)
        self.history = VOICE.LocalHistory(self.history_path, self.memory)

    def saved_chat(self, rows):
        thread = str(uuid4())
        ids = []
        ticket = self.history.consent()
        for question, answer in rows:
            session = str(uuid4())
            self.history.record(ticket, session, {'transcript': question, 'response': answer, 'thread_id': thread},
                                access_check=lambda: True)
            ids.append(session)
        self.memory.close()
        self.open()
        assert len(self.history.request('list', {})['items']) == len(rows)
        status, body, _ = http(self.control, self.history, '/conversation/select', {'thread_id': thread})
        assert status == 200 and body['thread_id'] == thread
        assert [row['session_id'] for row in body['items']] == ids
        return thread, ids

    def process(self, *, source_id=None, question=None):
        options = {'device_token': TOKEN}
        if source_id is not None:
            options.update(question=question, source_id=source_id, correction_epoch=self.control.correction_epoch)
        _, credentials = self.control.start(str(uuid4()), **options)
        if source_id is None:
            self.control.command('stop', credentials['session_id'], credentials['token'])
        active = self.control.take(False)
        assert self.control.begin_processing(active['session_id'])
        return active['session_id']

    def model_request(self, query, session, monkeypatch):
        # Actual child adapter request is routed to the actual handler in process.
        def urlopen(request, *, timeout):
            assert request.full_url == 'http://127.0.0.1:3197/history/context' and timeout == 30
            assert request.get_header('X-lantern-session') == TOKEN
            status, _, raw = http(self.control, self.history, '/history/context', json.loads(request.data))
            assert status == 200
            return io.BytesIO(raw)
        monkeypatch.setattr('urllib.request.urlopen', urlopen)
        context = LanternConversationContext().recall(query)
        messages = self.control.conversation_messages(session)
        stdin = json.dumps({'question': query, 'messages': messages}, ensure_ascii=False).encode()
        question, parsed_messages = _conversation_input(io.BytesIO(stdin), text=True)
        body = json.loads(LlamaCppHTTP('http://127.0.0.1:1', 'inert-seam-model')._request(
            question, context, conversation=parsed_messages).data)
        assert body['messages'][-1]['role'] == 'user' and query in body['messages'][-1]['content']
        assert self.client.count('propose') == 0 and self.client.count('list_memories') == 0
        assert self.client.count('get_profile') > 0 and self.client.count('domain_access_sample') > 0
        return body, context, messages


@pytest.fixture
def seam(tmp_path, monkeypatch):
    assert Path(sage_lantern.__file__).resolve().is_relative_to(REPO / 'software/runtime/src')
    context = SessionContext('a' * 32, 'b' * 64)
    monkeypatch.setattr(VOICE, 'lock_required', lambda: True)
    monkeypatch.setattr(VOICE, 'token_authorized', lambda token: token == TOKEN)
    monkeypatch.setattr(VOICE.device_access, 'session_context', lambda token: context if token == TOKEN else None)
    monkeypatch.setattr(VOICE.device_access, 'device_unlocked', lambda: True)
    monkeypatch.setenv('LANTERN_DEVICE_TOKEN', TOKEN)
    value = Seam(tmp_path)
    yield value
    value.memory.close()


def test_shared_saved_chat_recovers_constraint_outside_recent_model_turns(seam, monkeypatch):
    constraint = 'Cedar needs exactly 7 bottles, and no peanuts.'
    rows = [(constraint, 'Cedar: 7 bottles, no peanuts.'),
            *[(f'Trivia followup {index}', f'Answer {index}') for index in range(1, 8)]]
    thread, _ = seam.saved_chat(rows)
    session = seam.process()
    body, context, recent = seam.model_request('How many Cedar bottles, and what food restriction?', session, monkeypatch)
    assert all(constraint not in row['content'] for row in recent)
    assert constraint in json.dumps(body, ensure_ascii=False)
    assert sum(len(part.encode('utf-8')) for part in context) <= 3072
    scoped = json.loads(context[0])
    assert scoped['topic'] == thread
    assert any(row['text'] == constraint and row['attribution'] == 'user_statement_not_verified_fact' for row in scoped['entries'])


def test_shared_new_chat_empty_snapshot_excludes_retained_old_chats(seam, monkeypatch):
    old = 'OLD-CHAT-PRIVATE-FIXTURE: Cedar needs 7 bottles, no peanuts.'
    seam.saved_chat([(old, 'OLD-CHAT-ANSWER')])
    status, _, _ = http(seam.control, seam.history, '/clear', {})
    assert status == 202
    seam.control.finish(seam.control.owner['session_id'])
    session = seam.process()
    body, context, recent = seam.model_request('Start a fresh question.', session, monkeypatch)
    assert not context and not recent
    assert old not in json.dumps(body) and 'OLD-CHAT-ANSWER' not in json.dumps(body)
    assert len(body['messages']) == 2
    assert len(seam.store.list()) == 1


def test_shared_correction_prefix_excludes_saved_target_and_descendants(seam, monkeypatch):
    prefix = 'Preserve this Cedar allergy constraint: no peanuts.'
    rows = [(prefix, 'No peanuts.'), ('STALE-TARGET-FIXTURE: send 99 bottles.', 'STALE-TARGET-ANSWER'),
            ('STALE-DESCENDANT-FIXTURE: dispatch that amount.', 'STALE-DESCENDANT-ANSWER')]
    _, ids = seam.saved_chat(rows)
    query = 'Correction: send 9 bottles instead.'
    session = seam.process(source_id=ids[1], question=query)
    body, context, recent = seam.model_request(query, session, monkeypatch)
    assert [row['content'] for row in recent] == [prefix, 'No peanuts.']
    serialized = json.dumps({'context': context, 'request': body}, ensure_ascii=False)
    assert prefix in serialized and query in serialized
    assert 'STALE-TARGET' not in serialized and 'STALE-DESCENDANT' not in serialized
    assert [row['session_id'] for row in seam.store.list()] == list(reversed(ids))
