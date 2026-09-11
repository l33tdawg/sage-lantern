import argparse
import base64
from email.message import Message
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

from sage_lantern.orchestrator import VoiceOrchestrator
from sage_lantern.state import JsonlEventLog, StateMachine
from test_unit_a_voice_photo import jpeg
from test_workflow_store import setup
from sage_lantern.correction_attempts import CorrectionAttempts


SPEC = importlib.util.spec_from_file_location('correction_voice', Path(__file__).parents[1] / 'lantern/unit-a-voice-loop.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = 'synthetic-owner-token'


@pytest.fixture
def control(tmp_path, monkeypatch, setup):
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == TOKEN)
    monkeypatch.setattr(MODULE, 'lock_required', lambda: True)
    value = MODULE.VoiceControl(30, photo_ready=True, photo_dir=tmp_path / 'photos')
    ledger = CorrectionAttempts(setup.store)
    value.correction_journal = lambda operation, **arguments: getattr(ledger, operation)(**arguments)
    value.history_media_check = lambda: None
    value.completed_source = dict(source_id=str(uuid.uuid4()), question='Misheard question', answer='Old answer',
                                  has_photo=False, correction_photo=None)
    value.source_session_id = hashlib.sha256(TOKEN.encode()).hexdigest()
    return value


def payload(control, **changes):
    value = dict(request_id=str(uuid.uuid4()), source_id=control.completed_source['source_id'],
                 source_kind='current', question='Corrected 问题')
    value.update(changes)
    preview = control.reconcile_correction({'source_id': value['source_id']}, TOKEN)
    value['new_execution'] = dict(challenge=preview['challenge'], expected_revision=preview['expected_revision'],
                                  consent=True, acknowledge_unknown=True)
    return value


def request(control, body, *, token=TOKEN, origin='http://127.0.0.1:3100', history=None, path='/correct'):
    raw = json.dumps(body).encode()
    handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
    handler.headers = Message()
    for name, value in {'Host': '127.0.0.1:3197', 'Origin': origin, 'X-Lantern-Voice': 'control',
                        'X-Lantern-Session': token, 'Content-Type': 'application/json',
                        'Content-Length': str(len(raw))}.items():
        handler.headers[name] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.command = path, 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /correct HTTP/1.1'
    handler.server = SimpleNamespace(control=control, history=history)
    handler.do_POST()
    headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert b'Cache-Control: no-store' in headers
    return int(headers.split()[1]), json.loads(response)


def test_http_new_session_idempotence_and_busy_truth(control):
    body = payload(control)
    original = dict(control.completed_source)
    status, credentials = request(control, body)
    assert status == 201 and credentials['session_id'] != original['source_id']
    assert request(control, body) == (409, {'error': 'correction_reconciliation_required'})
    session = control.take(False)
    assert session['question'] == body['question']
    assert control.begin_processing(session['session_id'])
    status, result = request(control, dict(body, request_id=str(uuid.uuid4())))
    assert status == 409 and result == {'error': 'processing_busy'}
    with pytest.raises(MODULE.ControlError, match='processing_busy'):
        control.command('cancel', credentials['session_id'], credentials['token'])
    control.finish(session['session_id'])
    assert request(control, body)[0] == 409
    assert original['answer'] == 'Old answer'


@pytest.mark.parametrize('question', ['', '   ', 'a' * 4097, 'bad\x00question', 123, '\ud800'])
def test_invalid_question_no_owner(control, question):
    assert request(control, payload(control, question=question))[0] == 400
    assert control.owner is None


@pytest.mark.parametrize('fault', ['locked', 'origin', 'clear', 'revoke', 'foreign_source', 'quiesce'])
def test_access_reset_and_source_refusal(control, fault):
    body = payload(control)
    options = {}
    if fault == 'locked':
        options['token'] = 'foreign'
    if fault == 'origin':
        options['origin'] = 'http://evil.invalid'
    if fault == 'clear':
        control.clear(device_token=TOKEN)
    if fault == 'revoke':
        control.revoke_device(clear_community=False)
    if fault == 'foreign_source':
        body['source_id'] = str(uuid.uuid4())
    if fault == 'quiesce':
        control.closing.set()
    assert request(control, body, **options)[0] in (403, 404, 409, 503)
    assert control.owner is None or control.owner['source'] == 'clear'


def test_latest_photo_preserved_without_client_reupload(control):
    image, original = jpeg(), jpeg(1280, 720)
    control.completed_source.update(has_photo=True, correction_photo=(image, original))
    status, credentials = request(control, payload(control))
    assert status == 201
    session = control.take(False)
    assert control.begin_processing(session['session_id'])
    assert control.image_path(session['session_id']).read_bytes() == image
    assert control.original_bytes(session['session_id']) == original
    control.revoke_device(clear_community=False)
    control.finish(credentials['session_id'])
    assert control.completed_source is None
    assert not list(control.photo_dir.iterdir())


def test_history_photo_requires_protected_original_and_preview(control):
    body = payload(control, source_kind='history')
    image, original = jpeg(), jpeg(1280, 720)
    control.completed_source.update(has_photo=True,
        correction_photo=(jpeg(entropy=b'retained-preview'), jpeg(1280, 720, entropy=b'retained-original')))
    events = []
    def history_request(action, data, *, access_check):
        assert access_check()
        events.append(action)
        if action == 'list':
            return {'items': [{'session_id': body['source_id'], 'has_photo': True}]}
        return {'image_base64': base64.b64encode(original).decode()}
    history = SimpleNamespace(media_mode='protected', request=history_request)
    assert request(control, body, history=history) == (409, {'error': 'correction_preview_required'})
    body['image_base64'] = base64.b64encode(image).decode()
    assert request(control, body, history=history)[0] == 201
    assert control.owner['original'] == original
    assert events == ['list', 'photo', 'list', 'photo']


@pytest.mark.parametrize('kind', [None, '', 'latest', True, [], {}])
def test_source_kind_is_required_and_exact(control, kind):
    body = payload(control, source_kind=kind)
    if kind is None:
        body.pop('source_kind')
    assert request(control, body) == (400, {'error': 'invalid_correction'})
    assert control.owner is None


def test_current_never_falls_back_to_history(control):
    body = payload(control)
    control.completed_source = None
    history = SimpleNamespace(media_mode='protected', request=Mock())
    assert request(control, body, history=history) == (404, {'error': 'correction_source_unavailable'})
    history.request.assert_not_called()


def test_history_never_falls_back_to_current_photo(control):
    control.completed_source.update(has_photo=True, correction_photo=(jpeg(), jpeg(1280, 720)))
    body = payload(control, source_kind='history', image_base64=base64.b64encode(jpeg()).decode())
    history = SimpleNamespace(media_mode='protected', request=Mock(return_value={'items': []}))
    assert request(control, body, history=history) == (404, {'error': 'correction_source_unavailable'})
    assert control.owner is None


def test_source_kind_change_conflicts_with_reserved_request(control):
    body = payload(control)
    assert request(control, body)[0] == 201
    assert request(control, dict(body, source_kind='history'))[0] == 409


def test_run_session_uses_no_capture_and_fresh_history_consent(control, tmp_path, monkeypatch):
    body = payload(control)
    assert request(control, body)[0] == 201
    session = control.take(False)
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, audio_dir=tmp_path))
    loop.control = control
    loop.capture = Mock(side_effect=AssertionError('must not capture'))
    loop.require_session_access = Mock()
    loop.begin_history_session = Mock()
    loop.process = Mock()
    loop.run_session(session)
    loop.begin_history_session.assert_called_once_with(session['session_id'])
    assert loop.process.call_args.kwargs == {'question': body['question']}
    loop.capture.assert_not_called()
    assert loop.device_session is None and control.owner is None


def test_text_orchestrator_retains_context_photo_and_no_stt(tmp_path):
    state = StateMachine(JsonlEventLog(tmp_path / 'state.jsonl'))
    stt = Mock()
    context = Mock()
    context.recall.return_value = ['local context']
    model = Mock(supports_images=True)
    model.respond.return_value = 'New answer'
    progress = []
    orchestrator = VoiceOrchestrator(state, stt, context, model, None, tmp_path)
    result = orchestrator.run_text('Corrected 问题', image_path=tmp_path / 'photo.jpg', on_progress=progress.append)
    stt.transcribe.assert_not_called()
    context.recall.assert_called_once_with('Corrected 问题')
    model.respond.assert_called_once_with('Corrected 问题', ['local context'], image_path=tmp_path / 'photo.jpg')
    assert result.transcript == 'Corrected 问题' and 'stt' not in result.timings_ms
    assert progress == ['recalling', 'photo_analysis']
    assert 'Corrected' not in (tmp_path / 'state.jsonl').read_text()


@pytest.mark.parametrize('wrong_transcript', [False, True])
@pytest.mark.parametrize('lock_during', [False, True])
def test_real_supervised_stdin_event_path(control, tmp_path, monkeypatch, wrong_transcript, lock_during):
    body = payload(control)
    assert request(control, body)[0] == 201
    session = control.take(False)
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, audio_dir=tmp_path,
        runtime=Path('/mock/runtime'), config=tmp_path / 'config', processing_timeout=5))
    loop.control = control
    loop.capture = Mock(side_effect=AssertionError('no audio capture'))
    loop.write_state = Mock()
    original_popen = subprocess.Popen
    commands = []
    program = (
        'import sys,json; question=sys.stdin.buffer.read().decode(); '
        'print(json.dumps({"type":"progress","stage":"recalling"}),flush=True); '
        'print(json.dumps({"type":"progress","stage":"generating"}),flush=True); '
        'print(json.dumps({"type":"result","result":{"session_id":sys.argv[1],'
        '"transcript": "WRONG" if sys.argv[2]=="True" else question,"response":"New answer",'
        '"timings_ms":{}}}),flush=True)'
    )
    def launch(argv, **kwargs):
        commands.append(argv)
        assert '--question-stdin' in argv and '--audio' not in argv
        assert body['question'] not in ' '.join(argv)
        process = original_popen([sys.executable, '-B', '-c', program, session['session_id'], str(wrong_transcript)], **kwargs)
        if lock_during:
            monkeypatch.setattr(MODULE, 'token_authorized', lambda token: False)
        return process
    monkeypatch.setattr(MODULE.subprocess, 'Popen', launch)
    loop.run_session(session)
    assert len(commands) == 1
    assert not loop.children and control.owner is None
    phases = [call.args[0] for call in loop.write_state.call_args_list]
    assert ('complete' in phases) == (not wrong_transcript and not lock_during)
    if not wrong_transcript and not lock_during:
        complete = next(call.kwargs for call in loop.write_state.call_args_list if call.args[0] == 'complete')
        assert complete['transcript'] == body['question']
    loop.capture.assert_not_called()


def test_cli_text_uses_same_orchestrator_and_no_question_argv(tmp_path, monkeypatch, capsys):
    from sage_lantern import cli
    state = StateMachine(JsonlEventLog(tmp_path / 'state.jsonl'))
    stt = Mock()
    model = SimpleNamespace(respond=lambda question, context: 'New answer')
    orchestrator = VoiceOrchestrator(state, stt, SimpleNamespace(recall=lambda question: []), model, None, tmp_path)
    monkeypatch.setattr(cli.RuntimeConfig, 'load', lambda path: object())
    monkeypatch.setattr(cli, 'build_orchestrator', lambda config: orchestrator)
    monkeypatch.setattr(cli.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO('Corrected 问题'.encode())))
    session_id = str(uuid.uuid4())
    assert cli.main(['voice', '--config', '/fixture', '--question-stdin', '--session-id', session_id, '--events']) == 0
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]['result']['transcript'] == 'Corrected 问题'
    assert events[-1]['result']['session_id'] == session_id
    assert all(event.get('stage') != 'transcribing' for event in events)
    stt.transcribe.assert_not_called()


def test_accepted_question_preserves_whitespace_exactly():
    from sage_lantern.orchestrator import validated_question
    text = '  Corrected\t问题?\n'
    assert validated_question(text) == text


def status_module():
    spec = importlib.util.spec_from_file_location('correction_status', Path(__file__).parents[1] / 'lantern/unit-a-status-server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('flag', [None, False, True, 1, 'true', [], {}])
@pytest.mark.parametrize('live', [False, True])
def test_status_correction_flag_requires_exact_true_and_live_probe(monkeypatch, flag, live):
    status = status_module()
    value = {} if flag is None else {'correction_ready': flag}
    monkeypatch.setattr(status.Path, 'read_text', lambda *args, **kwargs: json.dumps(value))
    probe = Mock(return_value=live)
    monkeypatch.setattr(status, '_voice_correction_ready', probe)
    assert status._voice()['correction_ready'] is (flag is True and live)
    assert probe.call_count == (1 if flag is True else 0)


@pytest.mark.parametrize('raw', [b'{}', b'{"ok":true}', b'{"correction_ready":1}',
    b'{"correction_ready":"true"}', b'{"correction_ready":true}', b'[]', b'bad', b' ' * 1025])
def test_live_capability_probe_old_and_malformed_fail_closed(monkeypatch, raw):
    status = status_module()
    connection = Mock()
    connection.getresponse.return_value = SimpleNamespace(status=200, read=lambda maximum: raw)
    monkeypatch.setattr(status.http.client, 'HTTPConnection', lambda *args, **kwargs: connection)
    assert status._voice_correction_ready() is (raw == b'{"correction_ready":true}')
    connection.request.assert_called_once_with('GET', '/health')
    connection.close.assert_called_once()


def test_capability_requires_running_listener_and_redacts_on_lock(control, tmp_path, monkeypatch):
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, state=tmp_path / 'state.json'))
    assert loop.correction_available() is False
    loop.touch_ready = loop.serving = True
    loop.controller_thread = SimpleNamespace(is_alive=lambda: True)
    assert loop.correction_available() is False
    loop.control.correction_journal = control.correction_journal
    assert loop.correction_available() is True
    session_id = str(uuid.uuid4())
    loop.device_session = (session_id, TOKEN, True)
    loop.write_state('processing', session_id=session_id, transcript='Edited private question', response='')
    assert json.loads(loop.args.state.read_text())['correction_ready'] is True
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: False)
    loop.write_state('processing', session_id=session_id, transcript='Edited private question', response='')
    result = json.loads(loop.args.state.read_text())
    assert result['correction_ready'] is False and result['transcript'] == ''
    loop.control.closing.set()
    assert loop.correction_available() is False
