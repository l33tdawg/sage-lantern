"""Inert continuity controls; runtime/source imports are chosen by PYTHONPATH.

Uses real local SQLite/history writes in pytest temporary directories, an
injected broker context, and no network, devices, audio, model, or real tokens.
"""
import importlib.util
import json
from pathlib import Path
from uuid import UUID, uuid4, uuid5

import pytest

from sage_lantern.active_conversation import ActiveConversation
from sage_lantern.conversation_service import ConversationService
from sage_lantern.conversation_store import ConversationStore, ConversationStoreError
from sage_lantern.conversation_workflow_store import ConversationWorkflowStore
from sage_lantern.session_context import SessionContext
from test_conversation_workflow_store import ProtectedWorkflows


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('storage_continuity_voice', ROOT / 'scripts/lantern/unit-a-voice-loop.py')
VOICE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VOICE)
TOKEN = 'synthetic-storage-owner'
NAMESPACE = UUID('85fc645f-757a-4386-aaf5-f82076c47036')


def uid(n):
    return f'00000000-0000-4000-8000-{n:012d}'


def item(n=1, **extra):
    return dict(session_id=uid(n), created_at=f'2026-10-09T10:00:{n:02d}Z',
                question=f'Question {n}', answer=f'Answer {n}', has_photo=False, **extra)


@pytest.fixture
def broker(monkeypatch):
    value = {'token': TOKEN, 'context': SessionContext('a' * 32, 'b' * 64)}
    monkeypatch.setattr(VOICE, 'token_authorized', lambda token: token == value['token'])
    monkeypatch.setattr(VOICE, 'lock_required', lambda: True)
    monkeypatch.setattr(VOICE.device_access, 'session_context', lambda token: value['context'] if token == value['token'] else None)
    monkeypatch.setattr(VOICE.device_access, 'device_unlocked', lambda: value['token'] is not None)
    return value


def admitted(control, token=TOKEN, replace=None):
    options = {'device_token': token}
    if replace is not None:
        options.update(question='Corrected question', source_id=replace, correction_epoch=control.correction_epoch)
    _, auth = control.start(str(uuid4()), **options)
    if replace is None:
        control.command('stop', auth['session_id'], auth['token'])
    session = control.take(False)
    assert control.begin_processing(session['session_id'])
    return session['session_id']


def complete(control, history, question='Fixture question', answer='Fixture answer', token=TOKEN, replace=None):
    session = admitted(control, token, replace)
    result = dict(transcript=question, response=answer, **control.conversation_metadata(session))
    history.record(history.consent(), session, result, access_check=lambda: True)
    control.remember_completed_source(session, question, answer, token)
    control.finish(session)
    return session


def test_legacy_plain_payload_operation_identity_and_exact_duplicate_unchanged(tmp_path):
    store = ConversationStore(tmp_path.resolve() / 'legacy')
    try:
        value = item(reflection='Fixture reflection')
        ticket = store.approve(True)
        ids = store.enqueue(ticket, value)
        assert ids == [str(uuid5(NAMESPACE, uid(1) + ':' + kind)) for kind in ('interaction', 'reflection')]
        expected = json.dumps({'version': 1, 'kind': 'interaction', **{k: value[k] for k in ('session_id', 'created_at', 'question', 'answer', 'has_photo')}}, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        assert store.work(uid(1), 'interaction')['payload'] == expected
        assert store.enqueue(ticket, value) == ids
        assert len(store.list()) == 1
        with pytest.raises(ConversationStoreError):
            store.enqueue(ticket, {**value, 'question': 'Different payload'})
        assert store.work(uid(1), 'interaction')['payload'] == expected
    finally:
        store.close()


def test_thread_and_correction_metadata_persist_through_legacy_restart_and_service_items(tmp_path):
    directory = tmp_path.resolve() / 'legacy'
    store = ConversationStore(directory)
    value = item(thread_id=uid(9), supersedes=uid(2), reflection='Unverified fixture reflection')
    try:
        store.enqueue(store.approve(True), value)
        payload = json.loads(store.work(uid(1), 'interaction')['payload'])
        assert {field: payload[field] for field in ('thread_id', 'supersedes')} == {field: value[field] for field in ('thread_id', 'supersedes')}
        assert 'thread_id' not in json.loads(store.work(uid(1), 'reflection')['payload'])
    finally:
        store.close()
    reopened = ConversationStore(directory)
    try:
        row = ConversationService(reopened, None).items()[0]
        assert {field: row[field] for field in ('thread_id', 'supersedes')} == {field: value[field] for field in ('thread_id', 'supersedes')}
        assert row['transcript'] == value['question'] and row['response'] == value['answer']
        assert reopened.work(uid(1), 'interaction')['state'] == 'queued'
        assert reopened.enqueue(reopened.consent(), value)
        assert len(reopened.list()) == 1
    finally:
        reopened.close()


@pytest.mark.parametrize('changes', [
    {'thread_id': '../unsafe'}, {'thread_id': uid(9).upper().replace('00000000', 'AAAAAAAA', 1)},
    {'thread_id': uid(9).replace('-', '')}, {'thread_id': str(UUID(int=0))},
    {'thread_id': None}, {'thread_id': 123}, {'supersedes': uid(2)},
    {'thread_id': uid(9), 'supersedes': uid(1)},
    {'thread_id': uid(9), 'supersedes': str(UUID(int=0))},
    {'thread_id': uid(9), 'supersedes': 'bad'},
    {'thread_id': uid(9), 'supersedes': None}, {'thread_id': uid(9), 'extra': 'unknown'},
])
def test_invalid_thread_metadata_is_rejected_before_any_durable_mutation(tmp_path, changes):
    store = ConversationStore(tmp_path.resolve() / 'legacy')
    try:
        with pytest.raises(ConversationStoreError):
            store.enqueue(store.approve(True), item(**changes))
        assert store.list() == [] and store.operations() == []
    finally:
        store.close()


def test_real_local_history_select_continue_optout_lock_and_restart(tmp_path, broker):
    history_dir = tmp_path.resolve() / 'history'
    # Existing preferences are explicitly provisioned before attaching memory.
    VOICE.LocalHistory(history_dir).request('list', {})
    store = ConversationStore(tmp_path.resolve() / 'legacy')
    service = ConversationService(store, None)
    history = VOICE.LocalHistory(history_dir, service)
    control = VOICE.VoiceControl(30)
    try:
        first = complete(control, history, 'Cedar needs seven bottles', 'Cedar, seven')
        thread = control.conversation.thread_id
        history.request('settings', {'enabled': False})
        second = complete(control, history, 'Actually nine bottles', 'Cedar, nine')
        assert second == control.conversation.turns[-1].session_id
        assert 'Actually nine' in control.conversation.prepare(TOKEN).messages()[-2]['content']
        assert [row['session_id'] for row in history.request('list', {})['items']] == [first]
        history.request('settings', {'enabled': True})
        assert [row['session_id'] for row in history.request('list', {})['items']] == [first]
        old_snapshot = control.conversation.prepare(TOKEN)
        control.revoke_device(clear_community=False)
        broker.update(token='synthetic-reunlocked-owner', context=SessionContext('c' * 32, 'd' * 64))
        assert not control.conversation.turns
        assert not control.conversation.commit(old_snapshot, uid(7), 'Late', 'Late')
        token = broker['token']
        result, _ = control.conversation_request('/conversation/select', {'thread_id': thread}, token, history)
        assert result['thread_id'] == thread
        assert [row['session_id'] for row in result['items']] == [first]
        third = complete(control, history, 'What is the saved count?', 'Seven bottles', token)
        assert control.conversation.thread_id == thread
        assert [turn.session_id for turn in control.conversation.turns] == [first, third]
    finally:
        service.close()
    reopened = ConversationStore(tmp_path.resolve() / 'legacy')
    restarted_history = VOICE.LocalHistory(history_dir, ConversationService(reopened, None))
    restarted_control = VOICE.VoiceControl(30)
    try:
        assert not restarted_control.conversation.turns
        result, _ = restarted_control.conversation_request('/conversation/select', {'thread_id': thread}, token, restarted_history)
        assert result['thread_id'] == thread
        assert [row['session_id'] for row in result['items']] == [first, third]
        session = admitted(restarted_control, token)
        messages = restarted_control.conversation_messages(session)
        assert [row['content'] for row in messages] == ['Cedar needs seven bottles', 'Cedar, seven', 'What is the saved count?', 'Seven bottles']
        restarted_control.finish(session)
    finally:
        reopened.close()


def test_in_memory_correction_branch_survives_full_retained_history_restore():
    chat = ActiveConversation()
    saved = []
    for n in (1, 2, 3):
        assert chat.commit(chat.prepare(TOKEN), uid(n), f'Question {n}', f'Answer {n}')
        saved.append(chat.turns[-1].item(chat.thread_id))
    assert chat.commit(chat.prepare(TOKEN, replace_session=uid(2)), uid(4), 'Corrected question', 'Corrected answer')
    saved.append(chat.turns[-1].item(chat.thread_id))
    expected = [uid(1), uid(4)]
    assert [turn.session_id for turn in chat.turns] == expected
    restored = ActiveConversation()
    restored.restore(TOKEN, chat.thread_id, list(reversed(saved)))
    assert [turn.session_id for turn in restored.turns] == expected
    assert restored.prepare(TOKEN).messages() == chat.prepare(TOKEN).messages()


@pytest.mark.parametrize('backend', ['local-history', 'legacy-sage', 'protected-fixture'])
def test_completed_correction_restores_without_old_descendants_through_actual_history(tmp_path, broker, backend):
    history_dir = tmp_path.resolve() / 'history'
    VOICE.LocalHistory(history_dir).request('list', {})
    workflow = ProtectedWorkflows() if backend == 'protected-fixture' else None
    def open_store():
        if backend == 'local-history':
            return None
        if workflow is not None:
            return ConversationWorkflowStore(workflow, access_check=lambda: True)
        return ConversationStore(tmp_path.resolve() / 'legacy')
    store = open_store()
    history = VOICE.LocalHistory(history_dir, ConversationService(store, None) if store else None)
    control = VOICE.VoiceControl(30)
    try:
        first = complete(control, history, 'Cedar has seven bottles', 'Seven')
        target = complete(control, history, 'Add two more', 'Nine')
        descendant = complete(control, history, 'Multiply that by two', 'Eighteen')
        corrected = complete(control, history, 'Remove two instead', 'Five', replace=target)
        expected = [first, corrected]
        assert [turn.session_id for turn in control.conversation.turns] == expected
        saved = history.request('list', {})['items']
        assert {row['session_id'] for row in saved} == {first, target, descendant, corrected}
        thread = control.conversation.thread_id
    finally:
        if store:
            store.close()
    reopened = open_store()
    restored_history = VOICE.LocalHistory(history_dir, ConversationService(reopened, None) if reopened else None)
    fresh = VOICE.VoiceControl(30)
    try:
        result, _ = fresh.conversation_request('/conversation/select', {'thread_id': thread}, TOKEN, restored_history)
        assert [row['session_id'] for row in result['items']] == expected
        session = admitted(fresh)
        assert [row['content'] for row in fresh.conversation_messages(session)] == [
            'Cedar has seven bottles', 'Seven', 'Remove two instead', 'Five']
        fresh.finish(session)
    finally:
        if reopened:
            reopened.close()


def test_repeated_correction_and_later_followup_restore_current_branch():
    thread = uid(9)
    rows = [dict(session_id=uid(n), created_at=f'2026-10-09T10:00:{n:02d}Z',
                 transcript=f'Question {n}', response=f'Answer {n}', thread_id=thread,
                 **({'supersedes': uid({4: 2, 6: 4}[n])} if n in (4, 6) else {})) for n in range(1, 8)]
    restored = ActiveConversation()
    restored.restore(TOKEN, thread, list(reversed(rows)))
    assert [turn.session_id for turn in restored.turns] == [uid(1), uid(6), uid(7)]


def test_missing_correction_target_cannot_revive_unverifiable_prefix():
    thread = uid(9)
    rows = [dict(session_id=uid(3), thread_id=thread, created_at='2026-10-09T10:00:03Z', transcript='Discarded descendant', response='Old result'),
            dict(session_id=uid(4), thread_id=thread, created_at='2026-10-09T10:00:04Z', transcript='Latest correction', response='New result', supersedes=uid(2))]
    restored = ActiveConversation()
    restored.restore(TOKEN, thread, rows)
    assert [turn.session_id for turn in restored.turns] == [uid(4)]


def test_restore_chronology_uses_actual_instants_with_offset_timestamps():
    restored = ActiveConversation()
    rows = [dict(session_id=uid(1), thread_id=uid(9), created_at='2026-10-09T16:00:00+08:00', transcript='Earlier', response='A'),
            dict(session_id=uid(2), thread_id=uid(9), created_at='2026-10-09T09:00:00Z', transcript='Later', response='B')]
    restored.restore(TOKEN, uid(9), rows)
    assert [turn.session_id for turn in restored.turns] == [uid(1), uid(2)]


def test_saved_branch_filters_independent_threads_and_supports_legacy_identity():
    try:
        from sage_lantern.conversation_branch import effective_thread_items
    except ImportError:
        pytest.skip('New helper; baseline behavior is covered by restore controls above')
    legacy = {**item(1), 'created_at': '2026-10-09T08:00:00Z'}
    unrelated = item(2)
    continuation = item(3, thread_id=uid(1))
    foreign_correction = item(4, thread_id=uid(8), supersedes=uid(1))
    rows = [foreign_correction, unrelated, continuation, legacy]
    assert effective_thread_items(rows, uid(1)) == [legacy, continuation]
    assert effective_thread_items(rows, uid(2)) == [unrelated]
    assert rows[0] is foreign_correction
    restored = ActiveConversation()
    restored.restore(TOKEN, uid(1), [dict(transcript=row['question'], response=row['answer'], **row) for row in rows])
    assert [turn.session_id for turn in restored.turns] == [uid(1), uid(3)]
