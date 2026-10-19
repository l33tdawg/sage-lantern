import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { parseSpokenResponses, saveSpokenResponses, loadSpokenResponses,
  SPOKEN_GET_ACTION, SPOKEN_SET_ACTION } from './spoken-responses.ts';
import { setDeviceToken } from './device-session.ts';

const settings = { revision: 0, spoken_responses: false };

test('spoken responses reject anything the runtime would not send', () => {
  assert.deepEqual(parseSpokenResponses(settings), settings);
  assert.deepEqual(parseSpokenResponses({ revision: 3, spoken_responses: true }),
    { revision: 3, spoken_responses: true });
  for (const changes of [
    { revision: -1 },
    { revision: 1.5 },
    { spoken_responses: 1 },
    { spoken_responses: 'true' },
    { spoken_responses: undefined },
    { unexpected: true },
  ]) assert.throws(() => parseSpokenResponses({ ...settings, ...changes }), /unavailable/);
  for (const wrong of [null, undefined, [], 'ok', 1, true]) {
    assert.throws(() => parseSpokenResponses(wrong), /unavailable/);
  }
});

test('spoken responses use authenticated compare-and-set and never silently unlock', async (context) => {
  setDeviceToken(null);
  await assert.rejects(loadSpokenResponses(), /locked/);
  setDeviceToken('a'.repeat(43));
  context.after(() => setDeviceToken(null));
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, `http://127.0.0.1:3196/${SPOKEN_SET_ACTION}`);
    // The exact body the runtime's validator accepts: no extra keys.
    assert.deepEqual(JSON.parse(options.body), {
      token: 'a'.repeat(43), expected_revision: 0, spoken_responses: true,
    });
    return Response.json({ revision: 1, spoken_responses: true });
  });
  assert.equal((await saveSpokenResponses(settings, true)).revision, 1);
});

test('the read action carries only a token and sends it as a POST', async (context) => {
  setDeviceToken('b'.repeat(43));
  context.after(() => setDeviceToken(null));
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, `http://127.0.0.1:3196/${SPOKEN_GET_ACTION}`);
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), { token: 'b'.repeat(43) });
    return Response.json({ revision: 0, spoken_responses: false });
  });
  assert.equal((await loadSpokenResponses()).spoken_responses, false);
});

test('an off switch is written as a real false, never a falsy stand-in', async (context) => {
  setDeviceToken('c'.repeat(43));
  context.after(() => setDeviceToken(null));
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body);
    assert.equal(body.spoken_responses, false);
    assert.equal(typeof body.spoken_responses, 'boolean');
    return Response.json({ revision: 2, spoken_responses: false });
  });
  await saveSpokenResponses({ revision: 1, spoken_responses: true }, false);
});

// The cross-language half of this contract is pinned in the runtime, where the
// validator actually lives: software/runtime/tests/test_spoken_settings_contract.py
// asserts the same two action strings and the same reply keys. If either side is
// renamed, exactly one of the two fails, and the seam is where a rename would
// otherwise pass unnoticed.
test('the action strings are the ones the runtime contract freezes', () => {
  assert.equal(SPOKEN_GET_ACTION, 'settings/spoken/get');
  assert.equal(SPOKEN_SET_ACTION, 'settings/spoken/set');
});

test('the panel is wired for a person, not just for a test', () => {
  const panel = readFileSync(new URL('../app/device/spoken-responses.tsx', import.meta.url), 'utf8');
  assert.match(panel, /role="switch"/);
  assert.match(panel, /Read answers aloud/);
  // Honest wording: the screen answer is unconditional, sound is the addition.
  assert.match(panel, /always appear on screen/i);
  assert.match(panel, /role="alert"/);
});
