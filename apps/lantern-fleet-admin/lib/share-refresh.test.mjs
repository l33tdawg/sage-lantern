import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { observeSessionContext, parseSessionContext, setDeviceToken } from './device-session.ts';
import { parseShareObservation, ShareRefreshRequest, shareRefreshRequest } from './share-refresh.ts';

const id = 'mesh-public:11111111-1111-4111-8111-111111111111';
const requestId = '22222222-2222-4222-8222-222222222222';
const observation = { id, request_id: requestId, state: 'pending', created_at: '2026-09-08T01:00:00Z', observed_status: 'committed', trust_basis: 'pinned_gateway', acceptance_available: false, consensus_verified: false, cache_eligible: false };
const response = (body) => new Response(JSON.stringify(body), { headers: { 'Content-Type': 'application/json' } });
/**
 * The authority a locked device is operating under.
 *
 * A locked device stamps every successful protected reply, and its client knows
 * the authority from a validated status reading. Both halves are modelled here:
 * a test that fabricated a locked device whose replies carry no stamp would be
 * testing a device that does not exist.
 */
const stamp = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
const establish = () => observeSessionContext(parseSessionContext(stamp));

test('observation contract binds both IDs and preserves gateway versus direct trust', () => {
  assert.deepEqual(parseShareObservation(observation, id, requestId), observation);
  for (const patch of [{ state: 'accepted' }, { acceptance_available: true }, { consensus_verified: true }, { cache_eligible: true }, { id: 'other' }, { request_id: crypto.randomUUID() }, { extra: 'no' }, { observed_status: 'secret' }, { trust_basis: 'browser' }, { acceptance_available: 0 }, { state: ['pending'] }, { trust_basis: ['unverified'] }]) {
    assert.throws(() => parseShareObservation({ ...observation, ...patch }, id, requestId));
  }
  const direct = { ...observation, state: 'accepted', trust_basis: 'trusted_node_online', acceptance_available: true };
  assert.deepEqual(parseShareObservation(direct, id, requestId), direct);
  assert.throws(() => parseShareObservation({ ...direct, observed_status: 'validated' }, id, requestId));
});

test('explicit update queues once then inspects same ID; no proposal submission', async (context) => {
  setDeviceToken('a'.repeat(43));
  establish();
  context.after(() => setDeviceToken(null));
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body });
    return response({ ...observation, request_id: body.request_id, session_context: stamp,
      state: url.endsWith('/refresh') ? 'queued' : 'pending', observed_status: url.endsWith('/refresh') ? null : 'committed' });
  });
  const attempt = new ShareRefreshRequest(id);
  const signal = new AbortController().signal;
  await attempt.run('refresh', signal);
  const original = attempt.requestId;
  assert.equal(attempt.canRefresh, false);
  await assert.rejects(attempt.run('refresh', signal));
  await attempt.run('inspect', signal);
  assert.equal(attempt.requestId, original);
  assert.equal(attempt.canRefresh, true);
  assert.equal(calls.length, 2);
  assert.equal(calls[0].body.consent, true);
  assert.deepEqual(calls[1].body, { id, request_id: original });
  assert.ok(calls.every(({ url }) => !url.endsWith('/submit')));
  await attempt.run('refresh', signal);
  assert.notEqual(attempt.requestId, original);
});

test('lost response retains ID and uncertainty even after successful inspection', async (context) => {
  setDeviceToken('a'.repeat(43));
  establish();
  context.after(() => setDeviceToken(null));
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push(url);
    if (url.endsWith('/refresh')) throw new Error('response lost');
    return response({ ...observation, request_id: JSON.parse(options.body).request_id, session_context: stamp });
  });
  const attempt = new ShareRefreshRequest(id);
  const signal = new AbortController().signal;
  await assert.rejects(attempt.run('refresh', signal));
  const original = attempt.requestId;
  await attempt.run('inspect', signal);
  assert.equal(attempt.requestId, original);
  assert.equal(attempt.uncertain, true);
  await assert.rejects(attempt.run('refresh', signal));
  assert.equal(calls.length, 2);
});

test('lock, cancellation and concurrent clicks do not publish or retry late results', async (context) => {
  setDeviceToken('a'.repeat(43));
  establish();
  context.after(() => setDeviceToken(null));
  let release;
  const calls = [];
  context.mock.method(globalThis, 'fetch', (url, options) => {
    calls.push(url);
    return new Promise((resolve) => { release = () => resolve(response({ ...observation, request_id: JSON.parse(options.body).request_id })); });
  });
  const attempt = new ShareRefreshRequest(id);
  const controller = new AbortController();
  const pending = attempt.run('refresh', controller.signal);
  await assert.rejects(attempt.run('refresh', controller.signal));
  controller.abort();
  setDeviceToken(null);
  release();
  await assert.rejects(pending);
  assert.equal(attempt.result, null);
  assert.equal(attempt.uncertain, true);
  setDeviceToken('b'.repeat(43));
  await assert.rejects(attempt.run('inspect', new AbortController().signal));
  assert.equal(calls.length, 1);
});

test('share UI keeps explicit consent, inspection and list reload distinct', () => {
  const source = readFileSync(new URL('../app/device/share.tsx', import.meta.url), 'utf8');
  assert.match(source, /Request updated status/);
  assert.match(source, /Check status request/);
  assert.match(source, /Reload saved submissions/);
  // The request map is render-visible — each row renders its own request — so it
  // is state, not a ref. A ref read during render is not a dependency React
  // tracks, and reading one there is what the react-hooks/refs rule forbids.
  assert.match(source, /\[statusRequests, setStatusRequests\] = useState\(new Map<string, ShareRefreshRequest>\(\)\)/);
  assert.match(source, /activeRequest\.current\?\.abort\(\)/);
  assert.match(source, /listRequest\.current\?\.abort\(\)/);
  assert.match(source, /document\.removeEventListener\('visibilitychange', hide\)/);
  assert.match(source, /Public acceptance is not independently verified/);
  assert.doesNotMatch(source, /setInterval|localStorage|sessionStorage|dangerouslySetInnerHTML/);
});

test('navigation retains request identity; new owner session must inspect afresh', async (context) => {
  setDeviceToken('a'.repeat(43));
  establish();
  context.after(() => setDeviceToken(null));
  const identifier = 'mesh-public:' + crypto.randomUUID();
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push(url);
    return response({ ...observation, id: identifier, request_id: body.request_id, session_context: stamp });
  });
  const original = shareRefreshRequest(identifier);
  await original.run('refresh', new AbortController().signal);
  assert.equal(shareRefreshRequest(identifier), original);
  setDeviceToken('b'.repeat(43));
  // A replaced token resets the observed authority, so the new owner's writes are
  // admitted under a reading this client has to take again before it can judge
  // their replies.
  establish();
  const rebound = shareRefreshRequest(identifier);
  assert.notEqual(rebound, original);
  assert.equal(rebound.requestId, original.requestId);
  assert.equal(rebound.result, null);
  assert.equal(rebound.canRefresh, false);
  await assert.rejects(original.run('inspect', new AbortController().signal));
  await rebound.run('inspect', new AbortController().signal);
  assert.equal(rebound.canRefresh, true);
  assert.equal(calls.length, 2);
});
