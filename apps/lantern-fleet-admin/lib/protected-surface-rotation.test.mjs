/**
 * A same-token epoch rotation has to reach the surfaces that hold protected
 * community data, and it has to reach them through the one seam.
 *
 * The device token does not change when the access epoch rotates, so every
 * `token !== getDeviceToken()` poll in these components was blind to the only
 * event that invalidates what they hold: the chats screen kept rendering a
 * cached conversation and the draft in progress, and the read-receipt control
 * kept offering a consent challenge the old authority had issued.
 *
 * These drive the real components — compiled from source, mounted against the
 * real `device-session` seam and the real `RenderedReceiptFlow`, with only the
 * network replaced. The negative cases are the point: a rotation must drop the
 * cache and the challenge, must not reissue anything on its own, and must never
 * turn an in-flight receipt into a positive state.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import * as community from './community.ts';
import * as deviceSession from './device-session.ts';
import * as messageDictation from './message-dictation.ts';
import * as messageThreads from './message-threads.ts';
import * as readReceipts from './read-receipts.ts';

const require = createRequire(import.meta.url);
const token = 'a'.repeat(43);
const STAMP = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
const OTHER = { schema: 'sage.lantern.session-context.v1', epoch: 'c'.repeat(32), session_ref: 'b'.repeat(64) };
const id = `mesh:inbox:00000000-0000-4000-8000-000000000001:${'a'.repeat(64)}`;
const challengeId = '00000000-0000-4000-8000-000000000002';
const receiptId = '00000000-0000-4000-8000-000000000003';
const created_at = '2026-09-08T01:00:00Z';
const status = { schema: 'sage.lantern.community.v1', messaging: { available: true, reason: 'ready' }, sharing: { available: false, reason: 'public_identity_unavailable' }, dictation: { available: false } };

const establish = (reading = STAMP) => deviceSession.observeSessionContext(deviceSession.parseSessionContext(reading));
/** A locked device's successful reply carries the authority it was admitted under. */
const json = (body, stamp) => Response.json(stamp === undefined ? body : { ...body, session_context: stamp });

const real = { requestAnimationFrame: globalThis.requestAnimationFrame, cancelAnimationFrame: globalThis.cancelAnimationFrame, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval, fetch: globalThis.fetch };
let frameQueue = [];
let intervalQueue = [];

// `setInterval`/`requestAnimationFrame` are captured rather than run, so the
// hide path can be fired deliberately instead of racing a 250ms poll.
globalThis.requestAnimationFrame = (callback) => frameQueue.push(callback);
globalThis.cancelAnimationFrame = () => { frameQueue = []; };
globalThis.setInterval = (callback) => intervalQueue.push(callback);
globalThis.clearInterval = () => {};

/**
 * Mount one real component with the same slot-based hook harness the Share
 * controls use, so effects can be run and settled on demand.
 */
function harness(file, props) {
  const slots = [];
  const effects = [];
  const buttons = [];
  const inputs = [];
  let cursor = 0;
  const document = new EventTarget();
  document.hidden = false;
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++;
      // React calls a lazy initialiser exactly once, on the first render.
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, (value) => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
    useCallback(fn) { return fn; },
    useEffect(callback, dependencies) {
      const index = cursor++;
      if (!slots[index] || dependencies.some((value, offset) => value !== slots[index].dependencies[offset])) {
        slots[index]?.cleanup?.();
        slots[index] = { dependencies };
        effects.push(() => { slots[index].cleanup = callback(); });
      }
    },
  };
  const compiled = ts.transpileModule(readFileSync(new URL(`../app/device/${file}`, import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const cjs = { exports: {} };
  const runtime = require('react/jsx-runtime');
  const jsx = (method) => (tag, properties, key) => {
    if (tag === 'button') buttons.push(properties);
    if (tag === 'input') inputs.push(properties);
    return runtime[method](tag, properties, key);
  };
  const nulled = new Proxy({}, { get: () => () => null });
  const load = (name) => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? { ...runtime, jsx: jsx('jsx'), jsxs: jsx('jsxs') }
      : name === '../../lib/community' ? community
        : name === '../../lib/device-session' ? deviceSession
          : name === '../../lib/read-receipts' ? readReceipts
            : name === '../../lib/message-threads' ? messageThreads
              : name === '../../lib/message-dictation' ? messageDictation
                : name === './drawer-navigation' ? { useDrawerNavigation: () => {} }
                  : name === './icons' ? nulled
                    : ['./nearby-directory', './dictation', './question-keyboard', './read-receipt'].includes(name) ? { default: () => null }
                      : require(name);
  new Function('require', 'module', 'exports', 'document', compiled)(load, cjs, cjs.exports, document);
  return {
    // Flush scheduled effects before re-running the hooks, as React does. Without
    // this a re-render can replace a slot whose effect has not run yet, and the
    // cleanup that effect was about to create — a challenge expiry timer, say —
    // is overwritten and never cleared.
    render() { while (effects.length) effects.shift()(); cursor = 0; buttons.length = 0; inputs.length = 0; return renderToStaticMarkup(React.createElement(cjs.exports.default, props)); },
    click(label) { const button = buttons.find((value) => value.children === label); assert.ok(button && !button.disabled, `button ${label}`); return button.onClick(); },
    check() { const box = inputs.find((value) => value.type === 'checkbox'); assert.ok(box, 'consent checkbox'); box.onChange({ target: { checked: true } }); },
    async settle() { for (let round = 0; round < 6; round += 1) { while (effects.length) effects.shift()(); await new Promise(setImmediate); } },
    frames() { let drained = 0; while (frameQueue.length) { const pending = frameQueue.splice(0); for (const frame of pending) frame(); drained += pending.length; } return drained; },
    hide() { document.hidden = true; for (const interval of intervalQueue) interval(); },
    close() { while (effects.length) effects.shift()(); for (const slot of slots) slot?.cleanup?.(); },
  };
}

function before() {
  frameQueue = []; intervalQueue = [];
  deviceSession.setDeviceToken(token);
}

function after() {
  deviceSession.setDeviceToken(null);
  globalThis.fetch = real.fetch;
}

test.after(() => {
  globalThis.requestAnimationFrame = real.requestAnimationFrame;
  globalThis.cancelAnimationFrame = real.cancelAnimationFrame;
  globalThis.setInterval = real.setInterval;
  globalThis.clearInterval = real.clearInterval;
});

test('a rotation drops the cached chats instead of leaving them under a dead authority', async () => {
  before();
  try {
    establish(STAMP);
    let calls = 0;
    globalThis.fetch = async (url, options) => {
      calls += 1;
      const body = JSON.parse(options.body);
      assert.equal(options.headers['X-Lantern-Session'], token);
      if (url.endsWith('/status')) return json(status, STAMP);
      if (url.endsWith('/messages/list')) {
        return json({ items: [{ id: `message-${body.box}`, peer_name: 'a'.repeat(64), text: `Cached ${body.box} line.`, state: 'sent', created_at }], more: false }, STAMP);
      }
      return json({ items: [], more: false }, STAMP);
    };
    const view = harness('messages.tsx', { close() {} });
    try {
      view.render(); await view.settle(); view.render();
      assert.match(view.render(), /Cached (?:inbox|outbox) line\./);
      const loaded = calls;
      // The epoch rotates and the device token does not move, so the poll the
      // screen already runs cannot see this. The seam is the only thing that can.
      establish(OTHER);
      assert.match(view.render(), /Loading chats…/);
      assert.doesNotMatch(view.render(), /Cached (?:inbox|outbox) line\./);
      // Dropped, not refetched: reloading on its own would be a fresh read under
      // an authority the user has not been shown.
      await view.settle();
      assert.equal(calls, loaded);
    } finally { view.close(); }
  } finally { after(); }
});

test('a rotated consent challenge is dropped and never reissued on its own', async () => {
  before();
  try {
    establish(STAMP);
    let calls = 0;
    const expires_at = Math.floor(Date.now() / 1000) + 30;
    globalThis.fetch = async (url) => { calls += 1; assert.match(url, /render-challenge$/); return json({ id, challenge_id: challengeId, expires_at }, STAMP); };
    const view = harness('read-receipt.tsx', { id, prior: undefined, onOutcome() {} });
    try {
      view.render(); await view.settle();
      view.frames(); await view.settle(); view.render();
      assert.match(view.render(), /Send read receipt/);
      // The challenge was issued under the authority that has now moved.
      establish(OTHER);
      assert.match(view.render(), /The device session is no longer verified\. Reopen the message to review it again\./);
      assert.doesNotMatch(view.render(), /Send read receipt/);
      assert.equal(calls, 1);
      view.frames(); await view.settle();
      assert.equal(calls, 1, 'a rotation must not request a replacement consent challenge');
    } finally { view.close(); }
  } finally { after(); }
});

test('a receipt already dispatched is left uncertain, never replayed and never reported as queued', async () => {
  before();
  try {
    establish(STAMP);
    let release;
    const outcomes = [];
    const expires_at = Math.floor(Date.now() / 1000) + 30;
    globalThis.fetch = async (url) => {
      if (url.endsWith('render-challenge')) return json({ id, challenge_id: challengeId, expires_at }, STAMP);
      return new Promise((resolve) => { release = () => resolve(json({ id, receipt_id: receiptId, state: 'queued' }, STAMP)); });
    };
    const view = harness('read-receipt.tsx', { id, prior: undefined, onOutcome: (value) => outcomes.push(value.state) });
    try {
      view.render(); await view.settle();
      view.frames(); await view.settle(); view.render();
      view.check(); view.render();
      void view.click('Send read receipt');
      await view.settle();
      // The rotation lands while the acknowledgement is still in flight, and the
      // reply that finally arrives still claims the old authority's success.
      establish(OTHER);
      release();
      await view.settle(); view.render();
      assert.ok(outcomes.length >= 1);
      assert.ok(outcomes.every((state) => state === 'unknown'), `no positive state after a rotation, got ${outcomes}`);
      assert.doesNotMatch(view.render(), /Read receipt waiting to send|handed to radio/);
      assert.match(view.render(), /Do not send another receipt while its status is unknown|The device session is no longer verified/);
      assert.doesNotMatch(view.render(), /Send read receipt/);
    } finally { view.close(); }
  } finally { after(); }
});

test('a legacy device that publishes no context still gets a working challenge', async () => {
  before();
  try {
    // The unlocked path: no context is published, so an unstamped reply is the
    // correct shape rather than a refusal, and the consent flow stays usable.
    deviceSession.observeSessionContext(null);
    const outcomes = [];
    const expires_at = Math.floor(Date.now() / 1000) + 30;
    globalThis.fetch = async (url) => url.endsWith('render-challenge')
      ? json({ id, challenge_id: challengeId, expires_at })
      : json({ id, receipt_id: receiptId, state: 'queued' });
    const view = harness('read-receipt.tsx', { id, prior: undefined, onOutcome: (value) => outcomes.push(value.state) });
    try {
      view.render(); await view.settle();
      view.frames(); await view.settle(); view.render();
      assert.match(view.render(), /Send read receipt/);
      view.check(); view.render();
      await view.click('Send read receipt');
      await view.settle(); view.render();
      assert.deepEqual(outcomes, ['unknown', 'queued']);
    } finally { view.close(); }
  } finally { after(); }
});

test('a refused reading clears the cached chats through the same seam', async () => {
  before();
  try {
    establish(STAMP);
    let calls = 0;
    globalThis.fetch = async (url, options) => {
      calls += 1;
      const body = JSON.parse(options.body);
      if (url.endsWith('/status')) return json(status, STAMP);
      if (url.endsWith('/messages/list')) {
        return json({ items: [{ id: `message-${body.box}`, peer_name: 'a'.repeat(64), text: `Cached ${body.box} line.`, state: 'sent', created_at }], more: false }, STAMP);
      }
      return json({ items: [], more: false }, STAMP);
    };
    const view = harness('messages.tsx', { close() {} });
    try {
      view.render(); await view.settle(); view.render();
      assert.match(view.render(), /Cached (?:inbox|outbox) line\./);
      const loaded = calls;
      // The reading could not be compared, so the authority is *gone* — not
      // merely moved. Nothing retained under it may stay on screen, and nothing
      // may be refetched to replace it.
      deviceSession.observeSessionContext('invalid');
      assert.match(view.render(), /Loading chats…/);
      assert.doesNotMatch(view.render(), /Cached (?:inbox|outbox) line\./);
      await view.settle();
      assert.equal(calls, loaded);
    } finally { view.close(); }
  } finally { after(); }
});

test('a refused reading drops the consent challenge and cannot reissue it', async () => {
  before();
  try {
    establish(STAMP);
    let calls = 0;
    const expires_at = Math.floor(Date.now() / 1000) + 30;
    globalThis.fetch = async (url) => { calls += 1; assert.match(url, /render-challenge$/); return json({ id, challenge_id: challengeId, expires_at }, STAMP); };
    const view = harness('read-receipt.tsx', { id, prior: undefined, onOutcome() {} });
    try {
      view.render(); await view.settle();
      view.frames(); await view.settle(); view.render();
      assert.match(view.render(), /Send read receipt/);
      deviceSession.observeSessionContext('invalid');
      assert.match(view.render(), /The device session is no longer verified\./);
      assert.doesNotMatch(view.render(), /Send read receipt/);
      view.frames(); await view.settle();
      assert.equal(calls, 1, 'a refusal must not request a replacement consent challenge');
    } finally { view.close(); }
  } finally { after(); }
});

test('a rotation returns the device to the surface a fresh mount picks, a publication does not', () => {
  // Two separate events, and the parent has to keep them apart. A rotation empties
  // every protected surface, so leaving the user on the screen that was emptied is
  // a screen that silently discarded their work. A publication inside one authority
  // changes no authority, so it leaves them where they are.
  const source = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const start = source.indexOf("if (observation === 'changed')");
  const publicationAt = source.indexOf('if (next.voice.session_id !== session.current)');
  assert.ok(start > 0 && publicationAt > start, 'both branches are present, in that order');
  const rotation = source.slice(start, publicationAt);
  // The destination is the one the mount default picks, so an unlock and a rotation
  // cannot land on two different "neutral" surfaces.
  assert.match(rotation, /setPage\(homeEnabled \? 'home' : 'main'\);/);
  assert.match(source, /useState<Page>\(homeEnabled \? 'home' : 'main'\)/);
  // The origin chain and any pending Home focus belong to the authority that ended.
  assert.match(rotation, /backTo\.current = null;/);
  assert.match(rotation, /setHomeFocus\(null\);/);
  // The publication branch keeps the detached screens, Agent included: its draft is
  // screen state, and a publication is not a reason to discard it.
  const publication = source.slice(publicationAt);
  assert.match(publication, /current === 'messages' \|\| current === 'sharing' \|\| current === 'agent'/);
  assert.doesNotMatch(publication, /setPage\(homeEnabled \? 'home' : 'main'\)/);
});
