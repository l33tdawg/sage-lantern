import argparse
import base64
import hashlib
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

SPEC = importlib.util.spec_from_file_location('typed_voice', Path(__file__).parents[1] / 'lantern/unit-a-voice-loop.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = 'synthetic-typed-question-owner'


def jpeg(width=640, height=480, entropy=b'mock-entropy'):
    """The same synthetic JPEG the photo tests use: valid headers, no decoder."""
    frame = b'\x08' + height.to_bytes(2, 'big') + width.to_bytes(2, 'big') + b'\x03\x01\x11\x00\x02\x11\x00\x03\x11\x00'
    scan = b'\x03\x01\x00\x02\x11\x03\x11\x00\x3f\x00'
    return b'\xff\xd8\xff\xc0\x00\x11' + frame + b'\xff\xda\x00\x0c' + scan + entropy + b'\xff\xd9'


def serving(loop):
    """The capability the installed service has once its control server runs."""
    loop.serving = True
    loop.touch_ready = True
    loop.controller_thread = threading.current_thread()
    return loop


@pytest.fixture
def loop(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'token_authorized', lambda token: token == TOKEN)
    monkeypatch.setattr(MODULE, 'lock_required', lambda: True)
    monkeypatch.setattr(MODULE.device_access, 'session_context', lambda token:
        MODULE.SessionContext('a' * 32, 'b' * 64) if token == TOKEN else None)
    value = serving(MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, review_before_answer=False,
        audio_dir=tmp_path / 'old-audio', review_audio_dir=tmp_path / 'questions')))
    value.capture = Mock(side_effect=AssertionError('a typed question must never capture audio'))
    value.message_transcribe = Mock(side_effect=AssertionError('a typed question must never transcribe'))
    return value


def open_review(value, publications=None):
    """Open a typed session and run its worker until the review phase is published."""
    review = threading.Event()
    publications = publications if publications is not None else []

    def write_state(phase, **values):
        publications.append((phase, values))
        if phase == 'review':
            review.set()

    value.write_state = Mock(side_effect=write_state)
    value.begin_history_session = Mock()
    value.process = Mock()
    status, credentials = value.control.open_typed_question(str(uuid.uuid4()), TOKEN)
    assert status == 201
    session = value.control.take(False)
    thread = threading.Thread(target=value.run_session, args=(session,), daemon=True)
    thread.start()
    assert review.wait(2)
    return SimpleNamespace(loop=value, credentials=credentials, thread=thread, publications=publications,
                           review_id=value.control.owner['review_id'], session=session)


@pytest.fixture
def typed(loop):
    value = open_review(loop)
    try:
        yield value
    finally:
        loop.control.cancel(value.session['session_id'])
        value.thread.join(2)
        assert not value.thread.is_alive()


def submit_body(value, question='What grows in a rice paddy?'):
    return dict(session_id=value.credentials['session_id'], review_id=value.review_id,
                request_id=str(uuid.uuid4()), question=question)


def test_typed_session_reviews_without_capture_transcription_or_inference(typed):
    loop = typed.loop
    loop.capture.assert_not_called()
    loop.message_transcribe.assert_not_called()
    loop.process.assert_not_called()
    loop.begin_history_session.assert_not_called()
    assert typed.publications[-1][0] == 'review'
    assert typed.publications[-1][1] == dict(session_id=typed.credentials['session_id'],
                                             review_id=typed.review_id, transcript='', response='')
    assert loop.input_source == 'typed'
    assert loop.control.owner['source'] == 'typed'
    assert loop.control.owner['conversation'] is not None
    # No question audio directory is created, let alone a capture file.
    assert not list(loop.args.review_audio_dir.parent.glob('questions*'))


def test_submit_answers_the_exact_typed_text_once(typed):
    loop = typed.loop
    payload = submit_body(typed)
    first = loop.control.review_command('submit', payload, TOKEN)
    assert first == {'session_id': typed.credentials['session_id'], 'status': 'submitted'}
    assert loop.control.review_command('submit', payload, TOKEN) == first
    typed.thread.join(2)
    loop.process.assert_called_once()
    assert loop.process.call_args.kwargs == {'question': payload['question']}
    loop.begin_history_session.assert_called_once_with(payload['session_id'])
    loop.capture.assert_not_called()
    loop.message_transcribe.assert_not_called()


@pytest.mark.parametrize('change', [dict(question='   '), dict(question='a' * 4097),
    dict(question='bad\x00'), dict(review_id=str(uuid.uuid4())), dict(session_id=str(uuid.uuid4()))])
def test_invalid_or_stale_typed_submit_never_starts_inference(typed, change):
    payload = submit_body(typed)
    payload.update(change)
    with pytest.raises(MODULE.ControlError):
        typed.loop.control.review_command('submit', payload, TOKEN)
    typed.loop.process.assert_not_called()
    assert typed.loop.control.owner['phase'] == 'review'


def test_foreign_device_session_cannot_submit_typed_text(typed):
    with pytest.raises(MODULE.ControlError):
        typed.loop.control.review_command('submit', submit_body(typed), 'different-device-session')
    typed.loop.process.assert_not_called()


@pytest.mark.parametrize('reason', ['cancel', 'lock', 'expiry', 'shutdown'])
def test_discard_typed_session_never_answers_or_saves(typed, reason):
    loop = typed.loop
    if reason == 'cancel':
        loop.control.review_command('cancel', dict(session_id=typed.credentials['session_id'],
                                                   review_id=typed.review_id), TOKEN)
    elif reason == 'lock':
        loop.control.revoke_device(clear_community=False)
    elif reason == 'expiry':
        with loop.control.lock:
            loop.control.owner['review_deadline'] = 0
    else:
        loop.stop_requested.set()
    typed.thread.join(2)
    assert not typed.thread.is_alive()
    loop.process.assert_not_called()
    loop.begin_history_session.assert_not_called()
    assert loop.control.owner is None
    assert typed.publications[-1][0] == 'idle'
    assert typed.publications[-1][1]['transcript'] == ''


def test_typed_lane_is_not_blocked_by_a_missing_microphone(loop):
    # No audio device, no capture path: the lane advertises and opens anyway.
    assert loop.typed_available() is True
    assert loop.review_available() is True
    assert not hasattr(loop.args, 'alsa_device')
    status, credentials = loop.control.open_typed_question(str(uuid.uuid4()), TOKEN)
    assert status == 201 and credentials['lease_seconds'] == MODULE.LEASE_SECONDS
    loop.control.finish(credentials['session_id'])


def test_typed_lane_refuses_while_another_session_is_live(loop):
    status, first = loop.control.open_typed_question(str(uuid.uuid4()), TOKEN)
    assert status == 201
    with pytest.raises(MODULE.ControlError) as refusal:
        loop.control.open_typed_question(str(uuid.uuid4()), TOKEN)
    assert refusal.value.code == 'voice_busy'
    loop.control.finish(first['session_id'])


def test_typed_request_replay_returns_the_same_session_without_a_second_answer(loop):
    request_id = str(uuid.uuid4())
    first_status, first = loop.control.open_typed_question(request_id, TOKEN)
    second_status, second = loop.control.open_typed_question(request_id, TOKEN)
    assert (first_status, second_status) == (201, 200)
    assert first == second
    loop.control.finish(first['session_id'])


def test_typed_open_requires_the_device_session_and_the_capability(loop, monkeypatch):
    with pytest.raises(MODULE.ControlError) as unauthorized:
        loop.control.open_typed_question(str(uuid.uuid4()), 'not-the-owner')
    assert unauthorized.value.status == 403
    loop.touch_ready = False
    assert loop.typed_available() is False
    assert loop.review_available() is False
    with pytest.raises(MODULE.ControlError) as unavailable:
        loop.control.open_typed_question(str(uuid.uuid4()), TOKEN)
    assert unavailable.value.code == 'typed_unavailable'


def test_published_state_advertises_the_typed_lane_without_audio(tmp_path, monkeypatch):
    monkeypatch.setattr(MODULE, 'lock_required', lambda: False)
    loop = serving(MODULE.VoiceLoop(argparse.Namespace(max_seconds=30, review_before_answer=False,
        state=tmp_path / 'state.json')))
    loop.write_state('idle', session_id=None, transcript='', response='', detail='')
    published = json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))
    assert published['phase'] == 'idle'
    assert published['typed_ready'] is True
    assert published['review_ready'] is True
    # The lane is advertised without an audio binding and without capture proof.
    assert 'audio' not in published


def test_http_question_start_requires_the_device_session(loop):
    raw = json.dumps({'request_id': str(uuid.uuid4())}).encode()
    handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
    handler.headers = Message()
    for key, value in {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
                       'X-Lantern-Voice': 'control', 'X-Lantern-Session': 'wrong',
                       'Content-Type': 'application/json', 'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.command = '/question/start', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /question/start HTTP/1.1'
    handler.server = SimpleNamespace(control=loop.control)
    handler.do_POST()
    headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert int(headers.split()[1]) == 403
    assert json.loads(response) == {'error': 'device_locked'}
    assert loop.control.owner is None


def test_http_question_start_opens_review_for_the_device_session(loop):
    raw = json.dumps({'request_id': str(uuid.uuid4())}).encode()
    handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
    handler.headers = Message()
    for key, value in {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
                       'X-Lantern-Voice': 'control', 'X-Lantern-Session': TOKEN,
                       'Content-Type': 'application/json', 'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.command = '/question/start', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /question/start HTTP/1.1'
    handler.server = SimpleNamespace(control=loop.control)
    handler.do_POST()
    headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert int(headers.split()[1]) == 201
    credentials = json.loads(response)
    assert loop.control.owner['session_id'] == credentials['session_id']
    assert loop.control.owner['source'] == 'typed'
    assert loop.control.owner['device_token'] == TOKEN
    loop.control.finish(credentials['session_id'])


def test_typed_question_carries_an_attached_photo_through_the_same_session(loop, tmp_path):
    loop.control.photo_ready = True
    loop.control.photo_dir = tmp_path / 'photos'
    image = jpeg()
    status, credentials = loop.control.open_typed_question(str(uuid.uuid4()), TOKEN, image)
    assert status == 201
    session_id = credentials['session_id']
    assert loop.control.owner['image_digest'] == hashlib.sha256(image).hexdigest()
    # The review advertises the photo, and the answer path resolves it while
    # processing — this is what makes a typed photo question possible at all.
    review = {}
    loop.write_state = lambda phase, **values: review.update(phase=phase, **values)
    review_id = loop.control.begin_typed_review(session_id)
    assert loop.control.has_photo(session_id) is True
    loop.write_state('review', session_id=session_id, review_id=review_id, transcript='', response='')
    assert review['phase'] == 'review'
    loop.control.review_command('submit', dict(session_id=session_id, review_id=review_id,
                                               request_id=str(uuid.uuid4()),
                                               question='What colour is the square?'), TOKEN)
    path = loop.control.image_path(session_id)
    assert path is not None and path.read_bytes() == image
    assert path.stat().st_mode & 0o777 == 0o600
    loop.control.finish(session_id)
    assert not path.exists()


def test_typed_question_refuses_a_photo_without_the_photo_backend(loop, tmp_path):
    loop.control.photo_dir = tmp_path / 'photos'
    with pytest.raises(MODULE.ControlError) as refusal:
        loop.control.open_typed_question(str(uuid.uuid4()), TOKEN, jpeg())
    assert refusal.value.code == 'photo_unavailable'
    assert loop.control.owner is None
    assert not (tmp_path / 'photos').exists()


@pytest.mark.parametrize('change', [
    dict(image=b'not-a-jpeg'),
    dict(image=jpeg(1920, 1080)),
    dict(image=None, original=jpeg(1280, 720)),
])
def test_typed_question_refuses_invalid_or_original_only_photos(loop, tmp_path, change):
    loop.control.photo_ready = True
    loop.control.photo_dir = tmp_path / 'photos'
    with pytest.raises(MODULE.ControlError) as refusal:
        loop.control.open_typed_question(str(uuid.uuid4()), TOKEN, change.get('image'), change.get('original'))
    assert refusal.value.code in {'invalid_photo', 'original_requires_image'}
    assert loop.control.owner is None


def test_typed_photo_request_replay_must_match_the_same_image(loop, tmp_path):
    loop.control.photo_ready = True
    loop.control.photo_dir = tmp_path / 'photos'
    request_id = str(uuid.uuid4())
    first_status, first = loop.control.open_typed_question(request_id, TOKEN, jpeg())
    same_status, same = loop.control.open_typed_question(request_id, TOKEN, jpeg())
    assert (first_status, same_status) == (201, 200) and first == same
    with pytest.raises(MODULE.ControlError) as refusal:
        loop.control.open_typed_question(request_id, TOKEN, jpeg(entropy=b'other'))
    assert refusal.value.code == 'stale_request'
    loop.control.finish(first['session_id'])


def test_http_question_start_accepts_a_photo_body_and_rejects_unknown_fields(loop, tmp_path):
    loop.control.photo_ready = True
    loop.control.photo_dir = tmp_path / 'photos'
    payload = {'request_id': str(uuid.uuid4()),
               'image_base64': base64.b64encode(jpeg()).decode('ascii')}
    handler = MODULE.VoiceHandler.__new__(MODULE.VoiceHandler)
    handler.headers = Message()
    raw = json.dumps(payload).encode()
    for key, value in {'Host': '127.0.0.1:3197', 'Origin': 'http://127.0.0.1:3100',
                       'X-Lantern-Voice': 'control', 'X-Lantern-Session': TOKEN,
                       'Content-Type': 'application/json', 'Content-Length': str(len(raw))}.items():
        handler.headers[key] = value
    handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
    handler.path, handler.command = '/question/start', 'POST'
    handler.request_version, handler.requestline = 'HTTP/1.1', 'POST /question/start HTTP/1.1'
    handler.server = SimpleNamespace(control=loop.control)
    handler.do_POST()
    headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
    assert int(headers.split()[1]) == 201
    credentials = json.loads(response)
    assert loop.control.owner['image_digest'] == hashlib.sha256(jpeg()).hexdigest()
    loop.control.finish(credentials['session_id'])
    for extra in ({'purpose': 'answer'}, {'token': 'x'}, {'question': 'typed'}):
        bad = json.dumps({**payload, **extra}).encode()
        handler.wfile = io.BytesIO()
        handler.headers.replace_header('Content-Length', str(len(bad)))
        handler.rfile = io.BytesIO(bad)
        handler.do_POST()
        headers, response = handler.wfile.getvalue().split(b'\r\n\r\n', 1)
        assert int(headers.split()[1]) == 400
        assert json.loads(response) == {'error': 'invalid_request'}
