import assert from 'node:assert/strict';
import test from 'node:test';
import { liveVoicePollDelay, mergeLiveVoiceStatus, mergeTelemetryStatus, parseLiveVoiceStatus } from './live-voice.ts';
import { establishedSessionContext, fenceProtectedReply, observeSessionContext, setDeviceToken } from './device-session.ts';

const now = Date.now();
const stamp = new Date(now).toISOString();
const context = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
const other = { ...context, epoch: 'c'.repeat(32), session_ref: 'd'.repeat(64) };
const live = (overrides = {}) => parseLiveVoiceStatus({ schema: 'sage.lantern.voice-status.v1', observed_at: stamp,
  session_context: context, voice: { phase: 'processing', session_id: 'turn-1', transcript: 'Question', response: 'New answer', ...overrides } });
const full = () => ({ schema: 'sage.lantern.local-status.v1', observed_at: stamp,
  session_context: live().session_context, local_voice_ready: true, audio: { present: true, detail: 'ready', ready: true },
  radio: { present: true, detail: 'ready' }, services: { voice_loop: 'active' },
  voice: { phase: 'processing', session_id: 'turn-1', transcript: 'Question', response: 'Old answer', touch_ready: true, correction_ready: true } });

test('live parser validates actual content and cannot grant readiness', () => {
  assert.equal(live({ touch_ready: true, review_ready: true }).voice.touch_ready, undefined);
  for (const overrides of [{ response: 42 }, { phase: null }, { progress_stage: 'pretend' }, { review_id: 'malformed' }]) {
    assert.throws(() => live(overrides));
  }
});

test('fresh same-publication telemetry is preserved, not invented by voice', () => {
  const value = mergeLiveVoiceStatus(full(), live(), stamp, now);
  assert.equal(value.voice.response, 'New answer');
  assert.equal(value.voice.touch_ready, true);
  assert.equal(value.audio.ready, true);
  assert.equal(mergeLiveVoiceStatus(null, live(), null, now).local_voice_ready, false);
});

test('expired or failed telemetry cannot keep readiness alive through fast voice reads', () => {
  for (const observed of [null, new Date(now - 15001).toISOString(), 'invalid']) {
    const value = mergeLiveVoiceStatus(full(), live(), observed, now);
    assert.deepEqual(value.services, {});
    assert.equal(value.local_voice_ready, false);
    assert.equal(value.voice.touch_ready, undefined);
    assert.equal(value.voice.response, 'New answer');
  }
});

test('new account, session or phase cannot inherit verified voice capabilities', () => {
  const changed = parseLiveVoiceStatus({ schema: 'sage.lantern.voice-status.v1', observed_at: stamp,
    session_context: other, voice: live().voice });
  assert.equal(mergeLiveVoiceStatus(full(), changed, stamp, now).local_voice_ready, false);
  for (const patch of [{ session_id: 'turn-2' }, { phase: 'complete' }]) {
    assert.equal(mergeLiveVoiceStatus(full(), live(patch), stamp, now).voice.correction_ready, undefined);
  }
});

test('slow telemetry cannot overwrite live text, phase, session or freshness', () => {
  const current = mergeLiveVoiceStatus(full(), live(), stamp, now);
  const old = { ...full(), observed_at: new Date(now - 500).toISOString(), voice: { ...full().voice, phase: 'idle', session_id: 'older-turn' } };
  const merged = mergeTelemetryStatus(current, old);
  assert.deepEqual(merged.voice, current.voice);
  assert.equal(merged.observed_at, current.observed_at);
  assert.equal(mergeTelemetryStatus(current, { ...old, session_context: changedContext() }), null);
});
function changedContext() { return { epoch: other.epoch, session_ref: other.session_ref }; }

test('capability updates only attach to the exact checked publication', () => {
  const current = mergeLiveVoiceStatus(full(), live(), stamp, now);
  const telemetry = full(); telemetry.voice.correction_ready = false;
  assert.equal(mergeTelemetryStatus(current, telemetry).voice.correction_ready, false);
  telemetry.voice.phase = 'complete';
  assert.equal(mergeTelemetryStatus(current, telemetry).voice.correction_ready, true);
});

test('telemetry replies issued before an A-B-A authority move are rejected', () => {
  setDeviceToken('A'.repeat(43)); observeSessionContext(live().session_context);
  const issued = establishedSessionContext();
  observeSessionContext(changedContext()); observeSessionContext(live().session_context);
  assert.notEqual(fenceProtectedReply(issued, live().session_context), 'ok');
  setDeviceToken(null);
});

test('initial unknown-authority telemetry skips safely and the next retry restores readiness', () => {
  setDeviceToken('C'.repeat(43));
  const firstIssue = establishedSessionContext();
  observeSessionContext(live().session_context);
  assert.notEqual(fenceProtectedReply(firstIssue, live().session_context), 'ok');
  const retryIssue = establishedSessionContext();
  assert.equal(fenceProtectedReply(retryIssue, live().session_context), 'ok');
  const current = mergeLiveVoiceStatus(null, live(), null, now);
  assert.equal(mergeTelemetryStatus(current, full()).voice.touch_ready, true);
  setDeviceToken(null);
});

test('100ms polling is limited to active turns or pending capture', () => {
  for (const phase of ['listening', 'review', 'processing']) assert.equal(liveVoicePollDelay({ ...full(), voice: { ...full().voice, phase } }), 100);
  for (const phase of ['idle', 'complete', 'error']) assert.equal(liveVoicePollDelay({ ...full(), voice: { ...full().voice, phase } }), 700);
  assert.equal(liveVoicePollDelay(null, true), 100);
  assert.equal(liveVoicePollDelay(null), 700);
});
