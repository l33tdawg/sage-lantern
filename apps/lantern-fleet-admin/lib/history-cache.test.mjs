import test from 'node:test';
import assert from 'node:assert/strict';
import { HistoryCache, HISTORY_REFRESH_MS } from './history-cache.ts';
import { savedHistory, subscribeSavedHistory } from './saved-history.ts';
import { DeviceSessionError, setDeviceToken } from './device-session.ts';

const data = (name = 'A question') => ({ enabled: true, items: [{ session_id: 'synthetic-session', created_at: '2026-09-10T10:00:00Z', transcript: name, response: 'An answer' }] });
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no; }); return { promise, resolve, reject }; };
const settle = () => new Promise((resolve) => setImmediate(resolve));

test('one request populates both screens; fresh reopens do not fetch and refresh retains visible history', async () => {
  let now = 0; const requests = [];
  const cache = new HistoryCache(() => { const request = deferred(); requests.push(request); return request.promise; }, () => true, () => now);
  const first = cache.refresh();
  assert.equal(cache.refresh(), first);
  assert.equal(requests.length, 1);
  requests[0].resolve(data()); await first;
  assert.equal(cache.getSnapshot().history.items[0].transcript, 'A question');
  await cache.refresh(); assert.equal(requests.length, 1);
  now += HISTORY_REFRESH_MS;
  const next = cache.refresh();
  assert.equal(cache.getSnapshot().refreshing, true);
  assert.equal(cache.getSnapshot().history.items.length, 1);
  requests[1].resolve(data('New question')); await next;
  assert.equal(cache.getSnapshot().history.items[0].transcript, 'New question');
});

test('failed refresh keeps the last valid list; invalid data never replaces it', async () => {
  let result = data(); const cache = new HistoryCache(async () => result, () => true);
  await cache.refresh(); result = { enabled: true, items: [{ private: 'invalid' }] };
  assert.equal(await cache.refresh(true), null);
  assert.equal(cache.getSnapshot().history.items.length, 1);
  assert.match(cache.getSnapshot().error, /Could not refresh/);
});

test('freshness starts at successful completion and a failed refresh is not cached as success', async () => {
  let now = 0; let calls = 0; let fail = false;
  const cache = new HistoryCache(async () => { calls++; now += 10000; if (fail) throw new Error(); return data(); }, () => true, () => now);
  await cache.refresh(); now += 29000; await cache.refresh(); assert.equal(calls, 1);
  fail = true; await cache.refresh(true); assert.equal(calls, 2);
  fail = false; await cache.refresh(); assert.equal(calls, 3);
});

test('lock and late results cannot repopulate cleared memory', async () => {
  const request = deferred(); let allowed = true; let signal;
  const cache = new HistoryCache((value) => { signal = value; return request.promise; }, () => allowed);
  const pending = cache.refresh(); allowed = false; cache.clear();
  assert.equal(signal.aborted, true);
  request.resolve(data()); assert.equal(await pending, null);
  assert.equal(cache.getSnapshot().history, null);
});

test('server access denial clears cached text even before local lock polling catches up', async () => {
  let denied = false;
  const cache = new HistoryCache(async () => { if (denied) throw new DeviceSessionError(); return data(); }, () => true);
  await cache.refresh(); denied = true; await cache.refresh(true);
  assert.equal(cache.getSnapshot().history, null);
});

test('delete or uncertain mutation blocks reads and cannot resurrect an older request', async () => {
  const requests = [];
  const cache = new HistoryCache(() => { const request = deferred(); requests.push(request); return request.promise; }, () => true);
  const unsubscribe = cache.subscribe(() => {});
  const old = cache.refresh();
  const end = cache.beginChange();
  await cache.refresh(true); assert.equal(requests.length, 1);
  end(); assert.equal(requests.length, 2);
  requests[0].resolve(data('Deleted')); await old;
  assert.equal(cache.getSnapshot().history, null);
  requests[1].resolve({ enabled: true, items: [] }); await settle();
  assert.deepEqual(cache.getSnapshot().history.items, []);
  end(); assert.equal(requests.length, 2); unsubscribe();
});

test('a completed answer during a read queues one fresh read after that request', async () => {
  const requests = [];
  const cache = new HistoryCache(() => { const request = deferred(); requests.push(request); return request.promise; }, () => true);
  const first = cache.refresh(); cache.refresh(true); cache.refresh(true);
  assert.equal(requests.length, 1);
  requests[0].resolve(data()); await first;
  assert.equal(requests.length, 2);
  requests[1].resolve(data('Newly completed')); await settle();
  assert.equal(cache.getSnapshot().history.items[0].transcript, 'Newly completed');
});

test('shared cache is cleared on token changes, visibility loss, and final unmount', async () => {
  const originalDocument = globalThis.document; const originalFetch = globalThis.fetch;
  const document = new EventTarget(); document.hidden = false; globalThis.document = document;
  let calls = 0;
  globalThis.fetch = async () => { calls++; return new Response(JSON.stringify(data())); };
  let one, two;
  try {
    setDeviceToken('a'.repeat(43));
    one = subscribeSavedHistory(() => {}); two = subscribeSavedHistory(() => {});
    await settle(); assert.equal(calls, 1); assert.ok(savedHistory.getSnapshot().history);
    one(); one = null; assert.ok(savedHistory.getSnapshot().history);
    document.hidden = true; document.dispatchEvent(new Event('visibilitychange'));
    assert.equal(savedHistory.getSnapshot().history, null);
    document.hidden = false; document.dispatchEvent(new Event('visibilitychange'));
    await settle(); assert.equal(calls, 2);
    setDeviceToken(null); assert.equal(savedHistory.getSnapshot().history, null);
    setDeviceToken('b'.repeat(43)); await settle(); assert.equal(calls, 3);
    two(); two = null; assert.equal(savedHistory.getSnapshot().history, null);
  } finally {
    one?.(); two?.(); setDeviceToken(null); globalThis.document = originalDocument; globalThis.fetch = originalFetch;
  }
});
