import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { setDeviceToken } from './device-session.ts';
import { parseRenderChallenge, parseReadReceipt, readReceiptLabel, receiptEligible, RenderedReceiptFlow } from './read-receipts.ts';

const id = `mesh:inbox:00000000-0000-4000-8000-000000000001:${'a'.repeat(64)}`;
const challenge_id = '00000000-0000-4000-8000-000000000002';
const receipt_id = '00000000-0000-4000-8000-000000000003';
const token = 't'.repeat(43);
const challenge = { id, challenge_id, expires_at: 160 };
const queued = { id, receipt_id, state: 'queued' };

test('constructor/fetch cannot create a receipt; rendered begin and explicit consent are separate', async () => {
  setDeviceToken(token);
  const calls = [];
  const flow = new RenderedReceiptFlow(id, async (action, body) => {
    calls.push([action, body]); return action.endsWith('render-challenge') ? challenge : queued;
  }, () => 100);
  try {
    assert.equal(calls.length, 0);
    await assert.rejects(flow.acknowledge(true));
    assert.equal(calls.length, 0);
    assert.deepEqual(await flow.beginRendered(), challenge);
    assert.deepEqual(calls, [['messages/render-challenge', { id, rendered: true }]]);
    await assert.rejects(flow.acknowledge(false));
    await assert.rejects(flow.acknowledge(1));
    assert.equal(calls.length, 1);
    assert.deepEqual(await flow.acknowledge(true), queued);
    assert.deepEqual(calls[1], ['messages/rendered', { id, challenge_id, rendered: true, share_read_receipt: true }]);
    await assert.rejects(flow.acknowledge(true));
    assert.equal(calls.length, 2);
  } finally { flow.close(); setDeviceToken(null); }
});

test('unknown dispatch never retries and is never called delivered/read', async () => {
  setDeviceToken(token);
  let calls = 0;
  const flow = new RenderedReceiptFlow(id, async (action) => {
    calls++; if (action === 'messages/rendered') throw new Error('lost reply'); return challenge;
  }, () => 100);
  try {
    await flow.beginRendered();
    assert.equal((await flow.acknowledge(true)).state, 'unknown');
    await assert.rejects(flow.acknowledge(true));
    assert.equal(calls, 2);
    assert.match(readReceiptLabel('serial_accepted'), /delivery unconfirmed/);
    assert.match(readReceiptLabel('queued'), /waiting to send/);
  } finally { flow.close(); setDeviceToken(null); }
});

test('exact ID, expiry, UUID and outcome schemas reject invented receipt authority', () => {
  assert.equal(receiptEligible(id), true);
  assert.equal(receiptEligible(id.replace('inbox', 'outbox')), false);
  assert.equal(receiptEligible('message-1'), false);
  for (const value of [{ ...challenge, id: 'other' }, { ...challenge, expires_at: 100 }, { ...challenge, expires_at: 161 },
    { ...challenge, expires_at: true }, { ...challenge, challenge_id: 'no' }, { ...challenge, private_text: 'no' }]) {
    assert.throws(() => parseRenderChallenge(value, id, 100));
  }
  for (const value of [{ ...queued, id: 'other' }, { ...queued, receipt_id: null }, { ...queued, state: 'read' },
    { ...queued, state: 'delivered' }, { ...queued, state: 'accepted' }, { ...queued, public: true }]) {
    assert.throws(() => parseReadReceipt(value, id));
  }
});

test('old session cannot begin or acknowledge; expired consent never submits', async () => {
  setDeviceToken(token);
  let now = 100; let calls = 0;
  const flow = new RenderedReceiptFlow(id, async () => { calls++; return challenge; }, () => now);
  try {
    await flow.beginRendered(); now = 160;
    await assert.rejects(flow.acknowledge(true));
    now = 100; setDeviceToken('n'.repeat(43));
    await assert.rejects(flow.acknowledge(true));
    assert.equal(calls, 1);
  } finally { flow.close(); setDeviceToken(null); }
});

test('unmount or stale challenge response cannot enable another message', async () => {
  setDeviceToken(token);
  let resolve;
  const flow = new RenderedReceiptFlow(id, () => new Promise((done) => { resolve = done; }), () => 100);
  try {
    const pending = flow.beginRendered(); flow.close(); resolve(challenge);
    await assert.rejects(pending);
    await assert.rejects(flow.acknowledge(true));
  } finally { flow.close(); setDeviceToken(null); }
});

test('real request helper stays loopback/session-bound for both routes', async () => {
  const original = globalThis.fetch;
  setDeviceToken(token);
  const calls = [];
  globalThis.fetch = async (url, options) => {
    calls.push(url);
    assert.equal(options.headers['X-Lantern-Session'], token);
    assert.equal(options.redirect, 'error'); assert.equal(options.credentials, 'omit');
    assert.equal(options.cache, 'no-store');
    return Response.json(url.endsWith('render-challenge') ? challenge : queued);
  };
  const flow = new RenderedReceiptFlow(id, undefined, () => 100);
  try {
    await flow.beginRendered(); await flow.acknowledge(true);
    assert.deepEqual(calls, ['http://127.0.0.1:3197/community/messages/render-challenge', 'http://127.0.0.1:3197/community/messages/rendered']);
  } finally { flow.close(); globalThis.fetch = original; setDeviceToken(null); }
});

test('UI binds a separately rendered inbox detail; unchecked explicit button only', () => {
  const messages = readFileSync(new URL('../app/device/messages.tsx', import.meta.url), 'utf8');
  const control = readFileSync(new URL('../app/device/read-receipt.tsx', import.meta.url), 'utf8');
  assert.match(messages, /messages\/open'.*share_read_receipt: false/);
  assert.match(messages, /message.box === 'inbox' && message.id === selected.id && receiptEligible/);
  assert.match(messages, /key=\{message.id\}/);
  assert.match(control, /useState\(false\)/);
  assert.match(control, /requestAnimationFrame/);
  assert.match(control, /current\.beginRendered\(\)/);
  assert.match(control, /disabled=\{!checked\}/);
  assert.match(control, /Send read receipt/);
  assert.match(control, /document.hidden/);
  assert.doesNotMatch(control, /localStorage|sessionStorage|dangerouslySetInnerHTML|shares\/submit/);
});
