import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { conversationRequest } from './conversation-threads.ts';
import { DeviceSessionError, setDeviceToken } from './device-session.ts';
import { HistoryCache } from './history-cache.ts';

const originalFetch = globalThis.fetch;
const originalDocument = globalThis.document;
const settle = () => new Promise((resolve) => setImmediate(resolve));
const history = { enabled: true, items: [] };
const ok = () => new Response(JSON.stringify(history));
beforeEach(() => { globalThis.document = { hidden: false }; setDeviceToken('a'.repeat(43)); });
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
    document.hidden = false; setDeviceToken('a'.repeat(43));
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
