import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import * as community from './community.ts';
import * as shareRefresh from './share-refresh.ts';
import { setDeviceToken } from './device-session.ts';

const require = createRequire(import.meta.url);
const source = { source_id: '11111111-1111-4111-8111-111111111111', question: 'What is a seed?', answer: 'A seed can grow into a plant.', has_photo: false };
const available = { schema: 'sage.lantern.community.v1', messaging: { available: false, reason: 'encrypted_storage_unavailable' }, sharing: { available: true, reason: 'ready' }, dictation: { available: false } };

function harness(request, selectedSource = source) {
  const slots = [];
  const effects = [];
  let cursor = 0;
  const document = new EventTarget();
  document.hidden = false;
  const buttons = [];
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: initial };
      return [slots[index].value, (value) => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useRef(initial) {
      const index = cursor++;
      slots[index] ??= { current: initial };
      return slots[index];
    },
    useEffect(callback, dependencies) {
      const index = cursor++;
      if (!slots[index] || dependencies.some((value, offset) => value !== slots[index].dependencies[offset])) {
        slots[index]?.cleanup?.();
        slots[index] = { dependencies };
        effects.push(() => { slots[index].cleanup = callback(); });
      }
    },
  };
  const compiled = ts.transpileModule(readFileSync(new URL('../app/device/share.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const module = { exports: {} };
  const runtime = require('react/jsx-runtime');
  const jsx = (method) => (tag, props, key) => {
    if (tag === 'button') buttons.push(props);
    return runtime[method](tag, props, key);
  };
  const load = (name) => name === 'react' ? hooks : name === 'react/jsx-runtime' ? { ...runtime, jsx: jsx('jsx'), jsxs: jsx('jsxs') }
    : name === '../../lib/share-refresh' ? shareRefresh : name === '../../lib/community' ? { ...community, communityRequest: request }
    : name === './icons' ? { BackIcon: () => null, ShareIcon: () => null } : require(name);
  new Function('require', 'module', 'exports', 'document', compiled)(load, module, module.exports, document);
  return {
    render() { cursor = 0; buttons.length = 0; return renderToStaticMarkup(React.createElement(module.exports.default, { source: selectedSource, close() {} })); },
    click(label) { const button = buttons.find((value) => value.children === label); assert.ok(button && !button.disabled, label); button.onClick(); },
    hide() { document.hidden = true; document.dispatchEvent(new Event('visibilitychange')); },
    async settle() { while (effects.length) effects.shift()(); await new Promise(setImmediate); },
    close() { for (const slot of slots) slot?.cleanup?.(); },
  };
}

test('actual Share controls queue once, inspect, and retain request across list reload', async (context) => {
  setDeviceToken('a'.repeat(43));
  context.after(() => setDeviceToken(null));
  const id = 'mesh-public:' + crypto.randomUUID();
  const created_at = '2026-09-08T01:00:00Z';
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    const body = JSON.parse(options.body);
    calls.push({ url, body });
    return new Response(JSON.stringify({ id, request_id: body.request_id, created_at,
      state: url.endsWith('/refresh') ? 'queued' : 'pending', observed_status: url.endsWith('/refresh') ? null : 'committed',
      trust_basis: 'pinned_gateway', acceptance_available: false, consensus_verified: false, cache_eligible: false }),
      { headers: { 'Content-Type': 'application/json' } });
  });
  const view = harness(async (action) => {
    if (action === 'status') return available;
    assert.equal(action, 'shares/list');
    return { items: [{ id, question: 'Public question', state: 'pending', created_at }], more: false };
  }, null);
  try {
    view.render(); await view.settle(); view.render();
    assert.equal(calls.length, 0);
    view.click('Request updated status'); await view.settle();
    assert.match(view.render(), /Check status request/);
    const request = calls[0].body.request_id;
    view.click('Reload saved submissions'); view.render(); await view.settle();
    assert.match(view.render(), new RegExp(request));
    view.click('Check status request'); await view.settle();
    const html = view.render();
    assert.match(html, /Gateway report: committed/);
    assert.doesNotMatch(html, /Accepted publicly/);
    assert.deepEqual(calls[1].body, { id, request_id: request });
    assert.equal(calls.length, 2);
    view.hide();
    assert.doesNotMatch(view.render(), /Public question|Gateway report: committed/);
  } finally { view.close(); }
});

for (const scenario of ['pending', 'status failure', 'provider unavailable', 'preview failure', 'ready']) {
  test(`private preview renders with safe submission gates: ${scenario}`, async () => {
    const calls = [];
    const view = harness(async (action) => {
      calls.push(action);
      if (scenario === 'pending') return new Promise(() => {});
      if (scenario === 'status failure') throw new Error('offline');
      if (action === 'status') return scenario === 'provider unavailable' ? { ...available, sharing: { available: false, reason: 'public_identity_unavailable' } } : available;
      assert.equal(action, 'shares/preview');
      if (scenario === 'preview failure') throw new Error('preview denied');
      return { preview_id: source.source_id, digest: 'a'.repeat(64), question: source.question, answer: source.answer, photo_included: false, expires_at: new Date(Date.now() + 60000).toISOString() };
    });
    try {
      const initial = view.render();
      assert.ok(initial.includes(source.question) && initial.includes(source.answer));
      await view.settle();
      const html = view.render();
      assert.ok(html.includes(source.question) && html.includes(source.answer));
      assert.match(html, /Private preview/);
      assert.match(html, /<button[^>]*disabled=""[^>]*>Submit publicly<\/button>/);
      assert.ok(!calls.includes('shares/submit'));
      if (scenario.includes('failure') || scenario === 'provider unavailable') {
        assert.match(html, /Public sending isn’t ready yet/);
        assert.doesNotMatch(html, /Checking public sending/);
      }
      if (scenario === 'provider unavailable') assert.deepEqual(calls, ['status']);
      if (scenario === 'ready' || scenario === 'preview failure') assert.deepEqual(calls, ['status', 'shares/preview']);
    } finally { view.close(); }
  });
}
