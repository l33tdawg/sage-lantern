import argparse
from email.message import Message
import importlib.util
import io
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest

SPEC = importlib.util.spec_from_file_location('review_voice', Path(__file__).parents[1] / 'lantern/unit-a-voice-loop.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = 'synthetic-review-owner'


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == TOKEN)
    monkeypatch.setattr(MODULE, 'lock_required', lambda: True)
    loop = MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, review_before_answer=True,
        audio_dir=tmp_path / 'old-audio', review_audio_dir=tmp_path / 'questions'))
    _, credentials = loop.control.start(str(uuid.uuid4()), device_token=TOKEN)
    session = loop.control.take(False)
    review = threading.Event()
    publications = []

    def write_state(phase, **values):
        publications.append((phase, values))
        if phase == 'review':
            review.set()

    def capture(session_id, path, **options):
        assert options == {'review': True}
        path.write_bytes(b'synthetic audio, no microphone')
        loop.control.command('stop', session_id, credentials['token'])
        return True

    loop.capture = Mock(side_effect=capture)
    loop.write_state = Mock(side_effect=write_state)
    loop.begin_history_session = Mock()
    loop.message_transcribe = Mock(return_value='What other films is the actor in?')
    loop.process = Mock()
    thread = threading.Thread(target=loop.run_session, args=(session,), daemon=True)
    thread.start()
    assert review.wait(2)
    value = argparse.Namespace(loop=loop, credentials=credentials, thread=thread, publications=publications,
        review_id=loop.control.owner['review_id'], tmp_path=tmp_path)
    try:
        yield value
    finally:
        loop.control.cancel(session['session_id'])
        thread.join(2)
        assert not thread.is_alive()


def body(value, **changes):
    result = dict(session_id=value.credentials['session_id'], review_id=value.review_id,
                  request_id=str(uuid.uuid4()), question='Which films feature this actor?')
    result.update(changes)
    return result


def test_review_never_runs_answer_or_history_and_removes_audio(setup):
    setup.loop.process.assert_not_called()
    setup.loop.begin_history_session.assert_not_called()
    assert not list((setup.tmp_path / 'questions').iterdir())
    assert setup.loop.control.busy()
    assert setup.loop.control.conversation.turns == ()


def test_submit_uses_exact_edited_text_once_and_same_conversation(setup):
    loop = setup.loop
    snapshot = loop.control.owner['conversation']
    payload = body(setup, question='Which films feature Hugo Weaving?')
    # Keep the worker from finishing until the duplicate admission is checked.
    release = threading.Event()
    loop.process.side_effect = lambda *args, **kwargs: release.wait(2)
    try:
        first = loop.control.review_command('submit', payload, TOKEN)
        assert loop.control.review_command('submit', payload, TOKEN) == first
        assert loop.control.owner['conversation'] is snapshot
        assert first['status'] == 'submitted'
    finally:
        release.set()
    setup.thread.join(2)
    loop.process.assert_called_once()
    assert loop.process.call_args.kwargs == {'question': payload['question']}
    loop.begin_history_session.assert_called_once_with(payload['session_id'])


@pytest.mark.parametrize('change', [dict(question=''), dict(question='a' * 4097), dict(question='bad\x00'),
    dict(question='\ud800'), dict(question=123), dict(review_id=str(uuid.uuid4())), dict(session_id=str(uuid.uuid4()))])
def test_invalid_or_stale_submit_cannot_start_inference(setup, change):
    with pytest.raises(MODULE.ControlError):
        setup.loop.control.review_command('submit', body(setup, **change), TOKEN)
    setup.loop.process.assert_not_called()
    assert setup.loop.control.owner['phase'] == 'review'


def test_foreign_session_cannot_submit(setup):
    with pytest.raises(MODULE.ControlError):
        setup.loop.control.review_command('submit', body(setup), 'different-device-session')
    setup.loop.process.assert_not_called()


@pytest.mark.parametrize('reason', ['cancel', 'lock', 'expiry', 'shutdown'])
def test_discard_never_runs_inference_or_history(setup, reason):
    loop = setup.loop
    if reason == 'cancel':
        loop.control.review_command('cancel', dict(session_id=setup.credentials['session_id'], review_id=setup.review_id), TOKEN)
    elif reason == 'lock':
        loop.control.revoke_device(clear_community=False)
    elif reason == 'expiry':
        with loop.control.lock:
            loop.control.owner['review_deadline'] = 0
    else:
        loop.stop_requested.set()
    setup.thread.join(2)
    assert not setup.thread.is_alive()
    loop.process.assert_not_called()
    loop.begin_history_session.assert_not_called()
    assert loop.control.owner is None
    assert setup.publications[-1][0] == 'idle'
    assert setup.publications[-1][1]['transcript'] == ''


def test_old_review_cannot_affect_a_later_recording(setup):
    loop = setup.loop
    payload = body(setup)
    loop.control.cancel(payload['session_id'])
    setup.thread.join(2)
    _, current = loop.control.start(str(uuid.uuid4()), device_token=TOKEN)
    with pytest.raises(MODULE.ControlError):
        loop.control.review_command('submit', payload, TOKEN)
    assert loop.control.owner['session_id'] == current['session_id']
    loop.control.finish(current['session_id'])


@pytest.mark.parametrize('fault', ['origin', 'token', 'stale', 'none'])
def test_http_review_route_checks_authority_before_submission(setup, fault):
    payload = body(setup)
    if fault == 'stale':
        payload['review_id'] = str(uuid.uuid4())
    raw = json.dumps(payload).encode()
    handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
    handler.headers = Message()
    headers = {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100', 'X-Lantern-Voice': 'control',
        'X-Lantern-Session': TOKEN, 'Content-Type': 'application/json', 'Content-Length': str(len(raw))}
    if fault == 'origin':
        headers['Origin'] = 'http://untrusted.invalid'
    if fault == 'token':
        headers['X-Lantern-Session'] = 'wrong'
    for key, value in headers.items():
        handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.command = '/review/submit', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /review/submit HTTP/1.1'
    handler.server = SimpleNamespace(control=setup.loop.control)
    handler.do_POST()
    response_headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert b'Cache-Control: no-store' in response_headers
    status = int(response_headers.split()[1])
    assert status == (200 if fault == 'none' else 409 if fault == 'stale' else 403)
    if fault == 'none':
        assert json.loads(response)['status'] == 'submitted'
        setup.thread.join(2)
        setup.loop.process.assert_called_once()
    else:
        setup.loop.process.assert_not_called()
