import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { conversationRequest } from './conversation-threads.ts';
import { DeviceSessionError, observeSessionContext, parseSessionContext, setDeviceToken } from './device-session.ts';
import { HistoryCache } from './history-cache.ts';

const originalFetch = globalThis.fetch;
const originalDocument = globalThis.document;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const history = { enabled: true, items: [] };
/**
 * The authority a locked device is operating under.
 *
 * A locked device stamps every successful protected reply, and its client knows
 * the authority from a validated status reading. Both halves are modelled here:
 * a test that fabricated a locked device whose replies carry no stamp would be
 * testing a device that does not exist.
 */
const STAMP = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
const OTHER = { schema: 'sage.lantern.session-context.v1', epoch: 'c'.repeat(32), session_ref: 'b'.repeat(64) };
const establish = (reading = STAMP) => observeSessionContext(parseSessionContext(reading));
const ok = () => new Response(JSON.stringify({ ...history, session_context: STAMP }));
beforeEach(() => { globalThis.document = { hidden: false }; setDeviceToken('a'.repeat(43)); establish(); });
afterEach(() => { setDeviceToken(null); globalThis.fetch = originalFetch; globalThis.document = originalDocument; });

test('busy history read retries without flashing a cache error or duplicating a concurrent read', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? new Response('{}', { status: 503 }) : ok();
  const cache = new HistoryCache((signal) => conversationRequest('history/list', {}, signal), () => true);
  const pending = cache.refresh();
  await settle();
  assert.equal(cache.refresh(), pending);
  assert.equal(cache.getSnapshot().refreshing, true);
  assert.equal(cache.getSnapshot().error, '');
  assert.equal(calls, 1);
  context.mock.timers.tick(1250);
  assert.deepEqual(await pending, history);
  assert.equal(calls, 2);
  assert.equal(cache.getSnapshot().error, '');
});

test('persistent service failure stops after three attempts; reopening can retry immediately', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response('{}', { status: 503 }); };
  const cache = new HistoryCache((signal) => conversationRequest('history/list', {}, signal), () => true);
  const pending = cache.refresh();
  await settle(); context.mock.timers.tick(1250);
  await settle(); context.mock.timers.tick(1250);
  assert.equal(await pending, null);
  assert.equal(calls, 3);
  assert.match(cache.getSnapshot().error, /Could not refresh/);
  globalThis.fetch = async () => { calls++; return ok(); };
  assert.deepEqual(await cache.refresh(), history);
  assert.equal(calls, 4);
});

test('access denial is never retried and clears previously cached history', async () => {
  let calls = 0;
  globalThis.fetch = async () => ++calls === 1 ? ok() : new Response('{}', { status: 403 });
  const cache = new HistoryCache((signal) => conversationRequest('history/list', {}, signal), () => true);
  await cache.refresh(); await cache.refresh(true);
  assert.equal(calls, 2);
  assert.equal(cache.getSnapshot().history, null);
});

test('selection and non-transient errors are never replayed', async () => {
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response('{}', { status: 503 }); };
  await assert.rejects(conversationRequest('conversation/select', { thread_id: 'example' }));
  assert.equal(calls, 1);
  globalThis.fetch = async () => { calls++; return new Response('{}', { status: 400 }); };
  await assert.rejects(conversationRequest('history/list'));
  assert.equal(calls, 2);
});

test('lock, visibility loss and session replacement cancel a queued retry', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  for (const reason of ['abort', 'hidden', 'session']) {
    document.hidden = false; setDeviceToken('a'.repeat(43)); establish();
    let calls = 0; const controller = new AbortController();
    globalThis.fetch = async () => { calls++; return new Response('{}', { status: 503 }); };
    const pending = conversationRequest('history/list', {}, controller.signal);
    const rejected = assert.rejects(pending, (error) => reason === 'abort' ? error.name === 'AbortError' : error instanceof DeviceSessionError);
    await settle();
    if (reason === 'abort') controller.abort();
    else if (reason === 'hidden') document.hidden = true;
    else setDeviceToken('b'.repeat(43));
    context.mock.timers.tick(1250);
    await rejected;
    assert.equal(calls, 1);
  }
});

test('retries stay inside the original ten-second history deadline', async (context) => {
  context.mock.timers.enable({ apis: ['setTimeout'] });
  let calls = 0;
  globalThis.fetch = async () => {
    calls++;
    await new Promise((resolve) => setTimeout(resolve, 9500));
    return new Response('{}', { status: 503 });
  };
  const pending = conversationRequest('history/list');
  const rejected = assert.rejects(pending, { name: 'AbortError' });
  context.mock.timers.tick(9500); await settle();
  context.mock.timers.tick(500); await rejected;
  assert.equal(calls, 1);
});

test('a history read that outlived its authority is refused rather than adopted', async () => {
  // Correctly issued under A, then the epoch moves while the reply is in flight:
  // the body is true when it was written and obsolete when it lands.
  globalThis.fetch = async () => {
    establish(OTHER);
    return new Response(JSON.stringify({ ...history, session_context: STAMP }));
  };
  await assert.rejects(conversationRequest('history/list'), (error) => error instanceof DeviceSessionError);
  // The positive control: the same request, the same stamped reply, nothing
  // moving underneath it. The read is adopted, and the stamp does not travel on
  // into the state the parsers and caches hold.
  establish();
  globalThis.fetch = async () => new Response(JSON.stringify({ ...history, session_context: STAMP }));
  assert.deepEqual(await conversationRequest('history/list'), history);
});

test('a locked device reply with no stamp is refused, not read as legacy', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify(history));
  await assert.rejects(conversationRequest('conversation/state'), (error) => error instanceof DeviceSessionError);
});
