import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { DEFAULT_LOCK_SECONDS, LOCK_DELAYS, parseLockSettings, loadLockSettings, saveLockSettings } from './lock-settings.ts';
import { setDeviceToken } from './device-session.ts';

const token = 'a'.repeat(43);
const settings = { revision: 7, lock_seconds: 300 };

function unlocked(context) {
  setDeviceToken(token);
  context.after(() => setDeviceToken(null));
}

test('strict lock schema accepts only bounded minutes and independent safe revision', () => {
  assert.equal(DEFAULT_LOCK_SECONDS, 300);
  assert.deepEqual(LOCK_DELAYS, [60, 120, 300, 600, 900]);
  for (const lock_seconds of LOCK_DELAYS) {
    assert.deepEqual(parseLockSettings({ revision: 0, lock_seconds }), { revision: 0, lock_seconds });
  }
  assert.notEqual(parseLockSettings(settings), settings);
  for (const value of [null, [], {}, true, { lock_seconds: 300 }, { revision: 0 },
    ...[-1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1, '7', true].map((revision) => ({ ...settings, revision })),
    ...[0, 30, 59, 180, 901, 1800, '300', null, true].map((lock_seconds) => ({ ...settings, lock_seconds })),
    { ...settings, display_off_seconds: 30 }, { ...settings, deep_sleep_seconds: 600 }, { ...settings, extra: true }]) {
    assert.throws(() => parseLockSettings(value), /Lock settings unavailable/);
  }
});

test('locked session refuses GET and SET without a network request', async (context) => {
  setDeviceToken(null);
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new Error('must not fetch'); });
  await assert.rejects(loadLockSettings(), /Device locked/);
  await assert.rejects(saveLockSettings(settings, 60), /Device locked/);
  assert.equal(fetch.mock.callCount(), 0);
  assert.throws(() => setDeviceToken('invalid'), /Invalid device session/);
});

test('GET uses authenticated POST with fixed local route and no cookies or redirects', async (context) => {
  unlocked(context);
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:3196/settings/lock/get');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), { token });
    assert.deepEqual(options.headers, { 'Content-Type': 'application/json', 'X-Lantern-Auth': 'control' });
    assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.redirect, 'error');
    assert.ok(options.signal instanceof AbortSignal);
    return Response.json(settings);
  });
  assert.deepEqual(await loadLockSettings(), settings);
});

test('SET sends only lock revision CAS and selected seconds', async (context) => {
  unlocked(context);
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:3196/settings/lock/set');
    assert.equal(options.method, 'POST');
    assert.deepEqual(JSON.parse(options.body), { token, expected_revision: 7, lock_seconds: 120 });
    return Response.json({ revision: 8, lock_seconds: 120 });
  });
  assert.deepEqual(await saveLockSettings(settings, 120), { revision: 8, lock_seconds: 120 });
  assert.deepEqual(settings, { revision: 7, lock_seconds: 300 });
});

test('invalid local selection or power schema never reaches network', async (context) => {
  unlocked(context);
  const fetch = context.mock.method(globalThis, 'fetch', async () => { throw new Error('must not fetch'); });
  await assert.rejects(saveLockSettings(settings, 30));
  await assert.rejects(saveLockSettings({ ...settings, display_off_seconds: 60 }, 120));
  await assert.rejects(saveLockSettings({ ...settings, revision: -1 }, 120));
  assert.equal(fetch.mock.callCount(), 0);
});

test('revision conflict and transport failure do not retry or expose backend details', async (context) => {
  unlocked(context);
  const fetch = context.mock.method(globalThis, 'fetch', async () => new Response('private conflict details', { status: 409 }));
  await assert.rejects(saveLockSettings(settings, 600), { message: 'Lock settings unavailable' });
  assert.equal(fetch.mock.callCount(), 1);
  fetch.mock.mockImplementation(async () => { throw new Error('private transport details'); });
  await assert.rejects(loadLockSettings(), { message: 'Lock settings unavailable' });
});

test('malformed, oversized and non-JSON responses fail closed', async (context) => {
  unlocked(context);
  const fetch = context.mock.method(globalThis, 'fetch');
  for (const response of [Response.json({ ...settings, extra: true }), Response.json({ revision: 1, lock_seconds: 0 }),
    new Response(JSON.stringify(settings)), new Response('not-json', { headers: { 'Content-Type': 'application/json' } }),
    new Response(' '.repeat(4097), { headers: { 'Content-Type': 'application/json' } }),
    new Response(new Uint8Array([255]), { headers: { 'Content-Type': 'application/json' } })]) {
    fetch.mock.mockImplementation(async () => response);
    await assert.rejects(loadLockSettings(), { message: 'Lock settings unavailable' });
  }
});

test('in-flight lock or session replacement rejects otherwise valid responses', async (context) => {
  unlocked(context);
  const fetch = context.mock.method(globalThis, 'fetch');
  for (const replacement of [null, 'b'.repeat(43)]) {
    setDeviceToken(token);
    fetch.mock.mockImplementation(async () => { setDeviceToken(replacement); return Response.json(settings); });
    await assert.rejects(loadLockSettings(), /Device locked/);
  }
});

test('hidden screen cannot initiate or accept a lock settings request', async (context) => {
  unlocked(context);
  const previous = Object.getOwnPropertyDescriptor(globalThis, 'document');
  const document = { hidden: true };
  Object.defineProperty(globalThis, 'document', { configurable: true, value: document });
  context.after(() => { if (previous) Object.defineProperty(globalThis, 'document', previous); else delete globalThis.document; });
  const fetch = context.mock.method(globalThis, 'fetch', async () => { document.hidden = true; return Response.json(settings); });
  await assert.rejects(loadLockSettings(), /Device locked/);
  assert.equal(fetch.mock.callCount(), 0);
  document.hidden = false;
  await assert.rejects(loadLockSettings(), /Device locked/);
});

test('timeout aborts one request without retry', async (context) => {
  unlocked(context);
  context.mock.timers.enable({ apis: ['setTimeout'] });
  const fetch = context.mock.method(globalThis, 'fetch', async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new Error('aborted')), { once: true });
  }));
  const pending = loadLockSettings();
  const rejected = assert.rejects(pending, { message: 'Lock settings unavailable' });
  context.mock.timers.tick(2500);
  await rejected;
  assert.equal(fetch.mock.callCount(), 1);
});

test('panel has independently saved lock setting and explicit after-lock power semantics', () => {
  const panel = readFileSync(new URL('../app/device/power-settings.tsx', import.meta.url), 'utf8');
  assert.match(panel, /Lock after/);
  assert.match(panel, /idle time since your last interaction/);
  assert.match(panel, /Screen-off and deep-sleep delays start only after lock/);
  assert.match(panel, /saveLockSettings\(lockSettings, lockDelay\)/);
  assert.match(panel, /savePowerSettings\(settings, display, sleep\)/);
  assert.match(panel, /Save lock delay/);
  assert.match(panel, /Save sleep settings/);
  assert.match(panel, /aria-describedby="lock-delay-help"/);
  assert.match(panel, /does not turn deep sleep on/);
});
