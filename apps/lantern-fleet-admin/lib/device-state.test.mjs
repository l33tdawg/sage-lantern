import assert from 'node:assert/strict';
import test from 'node:test';
import { answerIsVisible, deviceView, parseDeviceStatus, questionServiceRunning, statusIsFresh, typedLaneAvailable, voiceLaneNotice, waitingQuestion } from './device-state.ts';

test('waiting question is actual transcription before tokens, hidden on reset or disconnect', () => {
  const status = fixture('processing');
  status.voice.response = '';
  status.voice.progress_stage = 'generating';
  assert.equal(waitingQuestion(status, true), 'Question');
  assert.equal(answerIsVisible(status, true), false);
  assert.equal(waitingQuestion(status, false), null);
  assert.equal(waitingQuestion(null, true), null);
  for (const phase of ['idle', 'listening', 'complete', 'error']) {
    assert.equal(waitingQuestion({ ...status, voice: { ...status.voice, phase } }, true), null);
  }
  assert.equal(waitingQuestion({ ...status, voice: { ...status.voice, transcript: '  ' } }, true), null);
});

function fixture(phase = 'idle') {
  return {
    schema: 'sage.lantern.local-status.v1', observed_at: new Date().toISOString(),
    local_voice_ready: true, audio: { present: true, detail: 'Audio service ready', ready: true, capture_verified: false, ptt_ready: true },
    radio: { present: true, detail: 'USB detected' },
    voice: { phase, transcript: 'Question', response: 'Answer' },
    services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
  };
}

test('device phases reflect real status', () => {
  for (const [source, expected] of Object.entries({ idle: 'ready', listening: 'listening', processing: 'processing', complete: 'complete', error: 'error', unknown: 'unavailable' })) {
    assert.equal(deviceView(parseDeviceStatus(fixture(source)), true).phase, expected);
  }
  assert.equal(deviceView(fixture(), false).phase, 'unavailable');
  assert.equal(deviceView(null, false).phase, 'unavailable');
});

test('an unreadable voice publication is not reported as starting or taken over', () => {
  // `voice.phase` is any string on the wire, so the default branch is what a
  // blanked or unrecognised publication reaches — including after a rotation,
  // when the client is holding a reading it can no longer interpret. Saying the
  // voice service is starting, or that something took the device over, asserts
  // more than the reading supports.
  for (const phase of ['', 'starting_voice', 'quiescing', 'a-newer-runtime-phase']) {
    const view = deviceView(parseDeviceStatus(fixture(phase)), true);
    assert.equal(view.phase, 'unavailable');
    assert.equal(view.title, 'Voice status unavailable');
    assert.equal(view.hint, 'Waiting for the current device status.');
    assert.doesNotMatch(`${view.title} ${view.hint}`, /starting|takeover|took over/i);
  }
});

test('review is a separate gated phase and never displays an answer as started', () => {
  const status = fixture('review');
  status.voice.session_id = '11111111-1111-4111-8111-111111111111';
  status.voice.review_id = '22222222-2222-4222-8222-222222222222';
  status.voice.review_ready = true;
  assert.equal(deviceView(parseDeviceStatus(status), true).phase, 'review');
  assert.equal(answerIsVisible(status, true), false);
  assert.equal(waitingQuestion(status, true), null);
  assert.equal(deviceView(status, false).phase, 'unavailable');
  status.voice.review_ready = false;
  assert.equal(deviceView(status, true).phase, 'unavailable');
  status.voice.review_id = 'malformed';
  assert.throws(() => parseDeviceStatus(status));
});

test('non-speech gives a helpful offline retry hint', () => {
  const status = fixture('error');
  status.voice.detail = 'no_spoken_question';
  assert.equal(deviceView(status, true).title, 'No spoken question detected');
  assert.equal(deviceView(status, true).hint, 'Try again. If there’s music or noise nearby, move somewhere quieter.');
});

test('touchscreen welcome uses the requested tagline', () => {
  const status = fixture('idle');
  status.voice.touch_ready = true;
  assert.equal(deviceView(status, true).hint, 'Tap. Ask. Learn');
});

test('processing labels follow reported stages, not elapsed time', () => {
  const status = fixture('processing');
  status.voice.response = '';
  status.voice.has_photo = true;
  for (const [stage, title] of Object.entries({ starting: 'Starting…', transcribing: 'Understanding your question…', recalling: 'Checking saved knowledge…', photo_analysis: 'Analyzing your photo…', generating: 'Preparing your answer…', answering: 'Answering…', speaking: 'Preparing audio…' })) {
    status.voice.progress_stage = stage;
    assert.equal(deviceView(parseDeviceStatus(status), true).title, title);
  }
  status.voice.progress_stage = 'pretend_progress';
  assert.throws(() => parseDeviceStatus(status));
  delete status.voice.progress_stage;
  assert.equal(deviceView(parseDeviceStatus(status), true).title, 'Working locally…');
});

test('missing microphone and detected unready bridge are distinct', () => {
  const status = fixture();
  status.audio.present = false;
  assert.equal(deviceView(status, true).title, 'Microphone disconnected');
  status.audio.present = true;
  status.audio.detail = 'USB enumerated';
  status.audio.ready = false;
  assert.equal(deviceView(status, true).title, 'Audio is not ready');
});

test('operational readiness requires an explicit boolean, never a historical capture or wording', () => {
  const status = fixture();
  status.audio.detail = 'bridge ready';
  status.audio.capture_verified = true;
  for (const value of [undefined, false]) {
    status.audio.ready = value;
    assert.equal(deviceView(parseDeviceStatus(status), true).title, 'Audio is not ready');
  }
  for (const value of ['true', 1, null, {}]) {
    status.audio.ready = value;
    assert.throws(() => parseDeviceStatus(status));
  }
  status.audio.ready = true;
  status.audio.capture_verified = false;
  status.audio.detail = 'Diagnostic description may change';
  assert.equal(deviceView(parseDeviceStatus(status), true).phase, 'ready');
});

test('diagnostic capture evidence remains strictly typed but does not gate asking', () => {
  const status = fixture();
  for (const value of [undefined, false, true]) {
    status.audio.capture_verified = value;
    assert.equal(deviceView(parseDeviceStatus(status), true).phase, 'ready');
  }
  for (const value of ['true', 1, null, {}]) {
    status.audio.capture_verified = value;
    assert.throws(() => parseDeviceStatus(status));
  }
});

test('failed services and empty answers cannot report ready', () => {
  const status = fixture();
  status.services.ptt = 'failed';
  assert.equal(deviceView(status, true).phase, 'unavailable');
  const answer = fixture('complete');
  answer.voice.response = ' ';
  assert.equal(deviceView(answer, true).phase, 'error');
});

test('touch readiness does not require a working physical button', () => {
  const status = fixture();
  status.audio.ptt_ready = false;
  status.services.ptt = 'failed';
  assert.equal(deviceView(status, true).phase, 'unavailable');
  status.voice.touch_ready = true;
  assert.equal(deviceView(parseDeviceStatus(status), true).phase, 'ready');
  status.voice.phase = 'listening';
  status.voice.input_source = 'touch';
  assert.match(deviceView(status, true).hint, /Send/);
  // A live phase is judged by the session it publishes: a service flag alone
  // cannot rewrite an in-flight turn, and freshness is what expires it.
  assert.equal(questionServiceRunning(status), true);
  status.services.voice_loop = 'failed';
  assert.equal(questionServiceRunning(status), false);
  const idle = fixture();
  idle.voice.touch_ready = true;
  idle.services.voice_loop = 'failed';
  assert.equal(deviceView(idle, true).phase, 'unavailable');
});

test('a device without audio keeps a typed lane and never a dead end', () => {
  const status = fixture();
  status.audio = { present: false, detail: 'Configured platform capture device missing', ready: false, ptt_ready: false };
  status.services.ptt = 'inactive';
  status.voice.typed_ready = true;
  status.voice.touch_ready = true;
  const parsed = parseDeviceStatus(status);
  assert.equal(voiceLaneNotice(parsed), 'Microphone disconnected');
  assert.equal(typedLaneAvailable(parsed, true), true);
  assert.equal(typedLaneAvailable(parsed, false), false);
  const view = deviceView(parsed, true);
  assert.equal(view.phase, 'unavailable');
  assert.equal(view.title, 'Ask by typing');
  assert.match(view.hint, /Tap Type/);
  assert.match(view.hint, /Microphone disconnected/);
});

test('an answered turn stays visible after the audio lane drops', () => {
  const status = fixture('complete');
  status.voice.transcript = 'What grows in a rice paddy?';
  status.voice.response = 'Rice.';
  status.audio = { present: false, detail: 'missing', ready: false };
  status.services.voice_loop = 'inactive';
  status.voice.typed_ready = false;
  const view = deviceView(status, true);
  assert.equal(view.phase, 'complete');
  assert.equal(answerIsVisible(status, true), true);
});

test('a typed review renders only with the review credentials and the typed source', () => {
  const status = fixture('review');
  status.voice.session_id = '11111111-1111-4111-8111-111111111111';
  status.voice.review_id = '22222222-2222-4222-8222-222222222222';
  status.voice.review_ready = true;
  status.voice.typed_ready = true;
  status.voice.input_source = 'typed';
  status.voice.transcript = '';
  status.audio = { present: false, detail: 'missing', ready: false };
  const parsed = parseDeviceStatus(status);
  assert.equal(parsed.voice.input_source, 'typed');
  assert.equal(deviceView(parsed, true).title, 'Type your question');
  assert.equal(deviceView(parsed, true).phase, 'review');
  assert.throws(() => parseDeviceStatus({ ...status, voice: { ...status.voice, input_source: 'dictated' } }));
});

test('the typed lane advertises only when the question service says so', () => {
  const status = fixture();
  assert.equal(typedLaneAvailable(status, true), false);
  assert.equal(deviceView(status, true).phase, 'ready');
  const without = fixture();
  without.voice.typed_ready = true;
  without.services.voice_loop = 'inactive';
  assert.equal(deviceView(without, true).phase, 'unavailable');
  assert.equal(deviceView(without, true).title, 'Voice is not ready');
});

test('partial answers appear while processing, never as completed or stale', () => {
  const status = fixture('processing');
  status.voice.response = '';
  assert.equal(answerIsVisible(status, true), false);
  status.voice.response = 'First words';
  assert.equal(answerIsVisible(status, true), true);
  assert.equal(deviceView(status, true).phase, 'processing');
  assert.equal(answerIsVisible(status, false), false);
  status.voice.phase = 'error';
  assert.equal(answerIsVisible(status, true), false);
});

test('malformed status is rejected', () => {
  for (const value of [null, {}, { ...fixture(), observed_at: 'invalid' }, { ...fixture(), voice: {} }, { ...fixture(), radio: { present: true, detail: '', frequency_mhz: NaN } }]) {
    assert.throws(() => parseDeviceStatus(value));
  }
});

test('optional measurements must be finite and network status structured', () => {
  const value = { ...fixture(), network: { present: true, detail: 'Reported connection' }, temperature_millicelsius: 45000, storage_free_bytes: 1024, uptime_seconds: 60 };
  assert.equal(parseDeviceStatus(value).temperature_millicelsius, 45000);
  for (const fields of [{ network: 'wifi' }, { network: { present: true } }, { temperature_millicelsius: NaN }, { storage_free_bytes: 'many' }, { uptime_seconds: Infinity }]) {
    assert.throws(() => parseDeviceStatus({ ...value, ...fields }));
  }
});

test('stale and far-future timestamps cannot appear live', () => {
  const status = fixture();
  const now = Date.parse(status.observed_at);
  assert.equal(statusIsFresh(status, now), true);
  assert.equal(statusIsFresh(status, now + 15001), false);
  assert.equal(statusIsFresh(status, now - 30001), false);
});

test('approval and microphone failures offer specific recovery hints', () => {
  const status = fixture('error');
  status.voice.detail = 'HTTP 403';
  assert.equal(deviceView(status, true).title, 'Voice access needs setup');
  status.voice.detail = 'Microphone capture failed';
  assert.equal(deviceView(status, true).title, 'Could not record');
});
