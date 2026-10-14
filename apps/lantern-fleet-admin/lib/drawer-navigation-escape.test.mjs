/**
 * Escape still closes an open drawer after its focused control is disabled.
 *
 * A refused selection disables the control that had focus, so the browser drops
 * focus onto the body. The drawer's key handler lives on the panel and a key
 * pressed on the body never travels through it, so Escape stopped working while
 * the drawer was still open — reproduced in the review browser as
 * `{open: true, focusTag: 'BODY'}` with the same key closing the drawer when
 * focus was inside it.
 *
 * The drawer therefore keeps the keyboard when focus falls to the body that way,
 * and loses it as soon as focus moves to a different surface. These cases mount
 * the real `drawer-navigation.ts` against a minimal DOM, drive its real
 * listeners, and assert only what the hook did to the caller's own `close`.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import ts from 'typescript';

class FakeNode {}
globalThis.Node = FakeNode;

const tick = () => {};
const real = {
  document: globalThis.document,
  getComputedStyle: globalThis.getComputedStyle,
  matchMedia: globalThis.matchMedia,
  requestAnimationFrame: globalThis.requestAnimationFrame,
  cancelAnimationFrame: globalThis.cancelAnimationFrame,
};

/** A node with just the surface this hook touches. */
function element(id) {
  const listeners = new Map();
  const node = new FakeNode();
  node.id = id;
  node.isConnected = true;
  node.children = [];
  node.contains = (candidate) => candidate === node || node.children.includes(candidate);
  node.closest = () => null;
  node.getClientRects = () => [{}];
  node.querySelectorAll = () => [];
  node.focus = () => {};
  node.addEventListener = (type, listener) => {
    if (!listeners.has(type)) listeners.set(type, new Set());
    listeners.get(type).add(listener);
  };
  node.removeEventListener = (type, listener) => listeners.get(type)?.delete(listener);
  node.dispatch = (type, event) => { for (const listener of [...(listeners.get(type) ?? [])]) listener(event); };
  return node;
}

/** A document that records listeners and lets a case fire at a chosen target. */
function makeDocument() {
  const listeners = new Map();
  const body = element('body');
  const documentElement = element('html');
  return {
    activeElement: null,
    body,
    documentElement,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(listener);
    },
    removeEventListener(type, listener) { listeners.get(type)?.delete(listener); },
    fire(type, event) { for (const listener of [...(listeners.get(type) ?? [])]) listener(event); },
  };
}

const keyEvent = (target, key) => ({
  key, target, shiftKey: false, defaultPrevented: false, propagationStopped: false,
  preventDefault() { this.defaultPrevented = true; },
  stopPropagation() { this.propagationStopped = true; },
});

/** Load a private copy of the real hook, so its module state starts empty. */
function loadHook() {
  const source = readFileSync(new URL('../app/device/drawer-navigation.ts', import.meta.url), 'utf8');
  const compiled = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const effects = [];
  const hooks = {
    useState: (initial) => [typeof initial === 'function' ? initial() : initial, tick],
    useEffect: (callback) => { effects.push(callback); },
  };
  const loaded = { exports: {} };
  new Function('require', 'module', 'exports', compiled)((name) => {
    if (name === 'react') return hooks;
    throw new Error(`unexpected import ${name}`);
  }, loaded, loaded.exports);
  return { useDrawerNavigation: loaded.exports.useDrawerNavigation, effects };
}

test.beforeEach(() => {
  globalThis.document = makeDocument();
  globalThis.getComputedStyle = () => ({ visibility: 'visible' });
  globalThis.matchMedia = () => ({ matches: true, addEventListener: tick, removeEventListener: tick });
  globalThis.requestAnimationFrame = () => 0;
  globalThis.cancelAnimationFrame = tick;
});

test.after(() => {
  globalThis.document = real.document;
  globalThis.getComputedStyle = real.getComputedStyle;
  globalThis.matchMedia = real.matchMedia;
  globalThis.requestAnimationFrame = real.requestAnimationFrame;
  globalThis.cancelAnimationFrame = real.cancelAnimationFrame;
});

test('Escape pressed on the body closes the drawer that owned the keyboard', () => {
  const { useDrawerNavigation, effects } = loadHook();
  const panel = element('panel');
  const outside = element('outside');
  document.activeElement = outside;
  const closeCalls = [];
  useDrawerNavigation({ current: panel }, true, () => closeCalls.push('closed'), true);
  effects.forEach((effect) => effect());

  // The refusal: focus was inside the drawer, then its control was disabled.
  document.fire('focusin', { target: panel });
  document.activeElement = document.body;
  document.fire('keydown', keyEvent(document.body, 'Escape'));

  assert.deepEqual(closeCalls, ['closed']);
});

test('a drawer that never held focus does not take Escape from the body', () => {
  const { useDrawerNavigation, effects } = loadHook();
  const panel = element('panel');
  const outside = element('outside');
  document.activeElement = outside;
  const closeCalls = [];
  useDrawerNavigation({ current: panel }, true, () => closeCalls.push('closed'), true);
  effects.forEach((effect) => effect());

  // Focus moved to another surface, then that control was disabled too.
  document.fire('focusin', { target: outside });
  document.activeElement = document.body;
  document.fire('keydown', keyEvent(document.body, 'Escape'));

  assert.deepEqual(closeCalls, []);
});

test('one Escape closes the innermost drawer only', () => {
  const { useDrawerNavigation, effects } = loadHook();
  const outer = element('outer');
  const inner = element('inner');
  outer.children.push(inner);
  const outside = element('outside');
  document.activeElement = outside;
  const closed = [];
  useDrawerNavigation({ current: outer }, true, () => closed.push('outer'), true);
  useDrawerNavigation({ current: inner }, true, () => closed.push('inner'), true);
  effects.forEach((effect) => effect());

  document.fire('focusin', { target: inner });
  document.activeElement = document.body;
  document.fire('keydown', keyEvent(document.body, 'Escape'));

  assert.deepEqual(closed, ['inner']);
});

test('an unmounted drawer leaves no body handler behind', () => {
  const { useDrawerNavigation, effects } = loadHook();
  const panel = element('panel');
  const outside = element('outside');
  document.activeElement = outside;
  const closeCalls = [];
  useDrawerNavigation({ current: panel }, true, () => closeCalls.push('closed'), true);
  const cleanups = effects.map((effect) => effect());

  document.fire('focusin', { target: panel });
  document.activeElement = document.body;
  cleanups.forEach((cleanup) => cleanup?.());
  document.fire('keydown', keyEvent(document.body, 'Escape'));

  assert.deepEqual(closeCalls, []);
});

test('Escape from inside the panel still closes the drawer', () => {
  const { useDrawerNavigation, effects } = loadHook();
  const panel = element('panel');
  const outside = element('outside');
  document.activeElement = outside;
  const closeCalls = [];
  useDrawerNavigation({ current: panel }, true, () => closeCalls.push('closed'), true);
  effects.forEach((effect) => effect());

  const event = keyEvent(panel, 'Escape');
  panel.dispatch('keydown', event);

  assert.deepEqual(closeCalls, ['closed']);
  assert.equal(event.propagationStopped, true);
});
