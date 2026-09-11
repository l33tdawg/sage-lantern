import assert from 'node:assert/strict';
import test from 'node:test';
import { handoffQuestion } from './question-flow.ts';

const status = () => ({ observed_at: new Date().toISOString(), local_voice_ready: true,
  audio: { present: true, ready: true }, services: { voice_loop: 'active', llm: 'active', personal_sage: 'active' },
  voice: { phase: 'processing', session_id: 'current', touch_ready: true, transcript: '', response: '' } });
const draft = { sessionId: 'current', question: 'Which films feature Hugo Weaving?' };

test('submitted question survives the same-session empty-transcript handoff', () => {
  assert.equal(handoffQuestion(status(), true, draft), draft.question);
  const done = status(); done.voice.phase = 'complete'; done.voice.response = 'An answer.';
  assert.equal(handoffQuestion(done, true, draft), draft.question);
});
test('handoff text does not escape session, cancellation, readiness or freshness boundaries', () => {
  assert.equal(handoffQuestion(status(), false, draft), null);
  assert.equal(handoffQuestion(null, true, draft), null);
  assert.equal(handoffQuestion(status(), true, null), null);
  for (const phase of ['idle', 'error', 'listening', 'review', 'unavailable']) {
    const changed = status(); changed.voice.phase = phase;
    assert.equal(handoffQuestion(changed, true, draft), null);
  }
  const other = status(); other.voice.session_id = 'other';
  assert.equal(handoffQuestion(other, true, draft), null);
  const stale = status(); stale.observed_at = new Date(Date.now() - 16000).toISOString();
  assert.equal(handoffQuestion(stale, true, draft), null);
  const down = status(); down.services.voice_loop = 'inactive';
  assert.equal(handoffQuestion(down, true, draft), null);
});
