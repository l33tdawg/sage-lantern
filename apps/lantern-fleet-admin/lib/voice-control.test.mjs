import assert from 'node:assert/strict';
import test from 'node:test';
import { confirmTouchSession, parseTouchSession } from './voice-control.ts';
import { setDeviceToken } from './device-session.ts';

test('touch session requires scoped identity, secret and expected short lease', () => {
  const session = { session_id: 'abcd1234-1111-2222-3333-444444444444', token: 'long-random-private-token', lease_seconds: 4 };
  assert.deepEqual(parseTouchSession(session), session);
  for (const value of [null, {}, { ...session, token: '' }, { ...session, session_id: 'invalid' }, { ...session, lease_seconds: 3600 }]) {
    assert.throws(() => parseTouchSession(value));
  }
});

const session = { session_id: 'abcd1234-1111-2222-3333-444444444444', token: 'long-random-private-token', lease_seconds: 4 };
const requestId = '12345678-1111-2222-3333-444444444444';
const confirmed = { session_id: session.session_id, request_id: requestId, status: 'current' };

test('start confirmation binds the original request, capture secret and device session', async (context) => {
  const deviceToken = 'a'.repeat(43);
  setDeviceToken(deviceToken);
  context.after(() => setDeviceToken(null));
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return Response.json(confirmed);
  });
  await confirmTouchSession(session, requestId, deviceToken);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:3197/start/confirm');
  assert.deepEqual(JSON.parse(calls[0].options.body), { request_id: requestId, session_id: session.session_id, token: session.token });
  assert.equal(calls[0].options.headers['X-Lantern-Session'], deviceToken);
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.credentials, 'omit');
});

test('a superseded capture is refused even if the client has not polled the takeover', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => Response.json({ error: 'session_mismatch' }, { status: 409 }));
  await assert.rejects(confirmTouchSession(session, requestId), { status: 409 });
});

test('unrelated or malformed confirmation replies never establish ownership', async (context) => {
  let value;
  context.mock.method(globalThis, 'fetch', async () => Response.json(value));
  for (value of [null, [], {}, { ...confirmed, status: 'cancelled' },
    { ...confirmed, session_id: '87654321-1111-2222-3333-444444444444' },
    { ...confirmed, request_id: '87654321-1111-2222-3333-444444444444' }]) {
    await assert.rejects(confirmTouchSession(session, requestId), { message: 'Could not confirm recording. Try again.' });
  }
});

test('older runtimes and transport failures fail closed without another start', async (context) => {
  let status;
  const urls = [];
  context.mock.method(globalThis, 'fetch', async (url) => {
    urls.push(url);
    return Response.json({ error: 'unavailable' }, { status });
  });
  for (status of [404, 503]) await assert.rejects(confirmTouchSession(session, requestId), { status });
  assert.deepEqual(urls, Array(2).fill('http://127.0.0.1:3197/start/confirm'));
});

test('device session rotation before or during confirmation rejects the result', async (context) => {
  const original = 'a'.repeat(43);
  const replacement = 'b'.repeat(43);
  context.after(() => setDeviceToken(null));
  let calls = 0;
  context.mock.method(globalThis, 'fetch', async () => {
    calls += 1;
    setDeviceToken(replacement);
    return Response.json(confirmed);
  });
  setDeviceToken(replacement);
  await assert.rejects(confirmTouchSession(session, requestId, original));
  assert.equal(calls, 0);
  setDeviceToken(original);
  await assert.rejects(confirmTouchSession(session, requestId, original));
  assert.equal(calls, 1);
});

test('confirmation has a bounded command deadline', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  context.mock.method(globalThis, 'fetch', async (_url, options) => new Promise((_resolve, reject) => {
    options.signal.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true });
  }));
  const assertion = assert.rejects(confirmTouchSession(session, requestId), { name: 'AbortError' });
  context.mock.timers.tick(2500);
  await assertion;
});
