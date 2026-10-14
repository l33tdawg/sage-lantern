/**
 * Back from a menu destination returns to the screen the menu was opened over.
 *
 * The menu lives in the shared header, so it opens on top of whatever screen the
 * user is on — Home, Chat, Messages. Only Settings used to record that origin;
 * the other four entries navigated without one (and Nearby cleared it outright),
 * so Back fell through to Chat and lost the screen the user actually left. The
 * case Rook named is Home→Messages→menu→Past questions→Back, which must land on
 * Messages.
 *
 * This mounts the real `screen.tsx` and drives its real handlers. Only the child
 * screens are replaced, and they are replaced with prop recorders so the parent's
 * own decision — which page it renders, and which Home control it asks to focus —
 * is what is asserted.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import * as deviceState from './device-state.ts';
import * as liveVoice from './live-voice.ts';
import * as homeNavigation from './home-navigation.ts';
import * as questionFlow from './question-flow.ts';
import * as questionCorrection from './question-correction.ts';
import * as photoPreview from './photo-preview.ts';
import * as deviceSession from './device-session.ts';
import * as voiceControl from './voice-control.ts';
import * as savedHistoryModule from './saved-history.ts';
import * as community from './community.ts';

const require = createRequire(import.meta.url);
const real = { fetch: globalThis.fetch, requestAnimationFrame: globalThis.requestAnimationFrame, setInterval: globalThis.setInterval, clearInterval: globalThis.clearInterval };
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.setInterval = () => 0;
globalThis.clearInterval = () => {};
// The status poll reschedules itself; a 503 keeps `connected` false, which is
// enough for navigation and keeps every assertion about the parent's own state.
globalThis.fetch = async () => new Response('{}', { status: 503 });

test.after(() => {
  globalThis.fetch = real.fetch;
  globalThis.requestAnimationFrame = real.requestAnimationFrame;
  globalThis.setInterval = real.setInterval;
  globalThis.clearInterval = real.clearInterval;
});

/** The flattened text of a compiled JSX child list. */
const label = (children) => (Array.isArray(children) ? children : [children])
  .filter((child) => typeof child === 'string').join('').trim();

function mount() {
  const slots = [];
  const effects = [];
  const buttons = [];
  /** Every child screen's props, keyed by the marker it stands for. */
  const seen = {};
  let cursor = 0;
  const document = new EventTarget();
  document.hidden = false;
  const chats = { reset() {}, selecting: false, items: [], current: null, error: '', history: null,
    threads: [], refresh: async () => true, select: async () => true, historyError: false, refreshing: false };
  const hooks = { ...React,
    useState(initial) {
      const index = cursor++;
      slots[index] ??= { value: typeof initial === 'function' ? initial() : initial };
      return [slots[index].value, (value) => { slots[index].value = typeof value === 'function' ? value(slots[index].value) : value; }];
    },
    useRef(initial) { const index = cursor++; slots[index] ??= { current: initial }; return slots[index]; },
    useCallback(fn) { return fn; },
    useMemo(fn) { return fn(); },
    useEffect(callback, dependencies) { schedule(callback, dependencies); },
    useLayoutEffect(callback, dependencies) { schedule(callback, dependencies); },
  };
  function schedule(callback, dependencies) {
    const index = cursor++;
    const previous = slots[index];
    // No dependency array means "after every render", as in React.
    const changed = !previous || !dependencies
      || dependencies.some((value, offset) => value !== previous.dependencies[offset]);
    if (!changed) return;
    previous?.cleanup?.();
    slots[index] = { dependencies };
    effects.push(() => { slots[index].cleanup = callback(); });
  }
  const compiled = ts.transpileModule(readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const cjs = { exports: {} };
  const runtime = require('react/jsx-runtime');
  const jsx = (method) => (tag, properties, key) => {
    if (tag === 'button') buttons.push(properties);
    return runtime[method](tag, properties, key);
  };
  const nulled = new Proxy({}, { get: () => () => null });
  const page = (marker) => ({ default: (props) => { seen[marker] = props; return null; } });
  const load = (name) => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? { ...runtime, jsx: jsx('jsx'), jsxs: jsx('jsxs') }
      : name === 'next/image' ? { default: () => null }
        : name === './icons' ? nulled
          : name === './drawer-navigation' ? { useDrawerNavigation: () => {} }
            : name === './conversations' ? { ChatExchanges: () => null, useConversations: () => chats }
              : name === './home' ? page('home')
                // `./chat` is the frame screen.tsx renders for the main page;
                // `./chat-shell` is what it replaced. Both publish the same
                // marker, because what these cases assert is which page the
                // parent chose, not which component draws it.
                : name === './chat' || name === './chat-shell' ? page('chat')
                  : name === './messages' ? page('messages')
                    : name === './settings' ? page('settings')
                      : name === './history' ? page('history')
                        : name === './status-placeholder' ? null
                          : name === './nearby' ? page('nearby')
                            : name === './about' ? page('about')
                              : name === './sharing' ? page('sharing')
                                : name === './gallery' ? page('gallery')
                                  : name === './camera' ? page('camera')
                                    : name === './device-header' ? page('header')
                                      : ['./talk', './answer-wait', './question-turn', './question-correction', './question-review', './suggested-question'].includes(name) ? { default: () => null }
                                        : name.endsWith('.css') ? {}
                                          : name === '../../lib/device-state' ? deviceState
                                            : name === '../../lib/live-voice' ? liveVoice
                                            : name === '../../lib/home-navigation' ? homeNavigation
                                              : name === '../../lib/question-flow' ? questionFlow
                                                : name === '../../lib/question-correction' ? questionCorrection
                                                  : name === '../../lib/photo-preview' ? photoPreview
                                                    : name === '../../lib/device-session' ? deviceSession
                                                      : name === '../../lib/voice-control' ? voiceControl
                                                        : name === '../../lib/saved-history' ? savedHistoryModule
                                                          : name === '../../lib/community' ? community
                                                            : name === './share' ? page('sharing-screen')
                                                                : name === './agent' ? page('agent')
                                                                  : name === './heartbeat' ? page('heartbeat')
                                                                    : name === './hardware' ? page('hardware')
                                                              : require(name);
  new Function('require', 'module', 'exports', 'document', compiled)(load, cjs, cjs.exports, document);
  return {
    seen,
    // Flush scheduled effects before re-running the hooks, as React does, and
    // clear what the stubs recorded so `showing()` describes this render only.
    render(props = { homeEnabled: true }) {
      while (effects.length) effects.shift()();
      for (const marker of Object.keys(seen)) delete seen[marker];
      cursor = 0; buttons.length = 0;
      return renderToStaticMarkup(React.createElement(cjs.exports.default, props));
    },
    async settle() { for (let round = 0; round < 4; round += 1) { while (effects.length) effects.shift()(); await new Promise(setImmediate); } },
    menu(entry) {
      seen.header.onMenu();
      this.render();
      const found = buttons.find((value) => label(value.children) === entry);
      assert.ok(found, `menu entry ${entry}`);
      found.onClick();
      this.render();
    },
    /**
     * Press the first collected button the predicate accepts.
     *
     * The camera shortcut is an icon-only control, so a case has to find it by
     * its accessible label rather than by text.
     */
    press(match) {
      const found = buttons.find(match);
      assert.ok(found, 'button');
      found.onClick();
      this.render();
    },
    /** Which page the parent is rendering, by the one child that appeared. */
    showing() { return Object.keys(seen).filter((marker) => marker !== 'header'); },
    close() { while (effects.length) effects.shift()(); for (const slot of slots) slot?.cleanup?.(); },
  };
}

const open = async (view, props) => { view.render(props); await view.settle(); view.render(props); };

test('Home to Messages, menu, Past questions, Back returns to Messages', async () => {
  const view = mount();
  try {
    await open(view);
    assert.deepEqual(view.showing(), ['home']);
    // Home's Messages tile records Home as the origin.
    view.seen.home.onOpenTile('messages');
    view.render();
    assert.deepEqual(view.showing(), ['messages']);
    view.seen.messages.close();                                   // leave again to re-enter below
    view.render();
    assert.deepEqual(view.showing(), ['home']);
    view.seen.home.onOpenTile('messages');
    view.render();
    // The menu opens on top of Messages, so Messages is the origin.
    view.menu('Past questions');
    assert.deepEqual(view.showing(), ['history']);
    view.seen.history.close();
    view.render();
    assert.deepEqual(view.showing(), ['messages'], 'Back must return to the screen the menu was opened over');
  } finally { view.close(); }
});

test('a menu destination opened over Chat returns to Chat', async () => {
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenChat();
    view.render();
    assert.deepEqual(view.showing(), ['chat']);
    view.menu('Past questions');
    assert.deepEqual(view.showing(), ['history']);
    view.seen.history.close();
    view.render();
    assert.deepEqual(view.showing(), ['chat']);
  } finally { view.close(); }
});

test('a Home origin restores the typed tile target the menu entry belongs to', async () => {
  const view = mount();
  try {
    await open(view);
    view.menu('Nearby');
    assert.deepEqual(view.showing(), ['nearby']);
    view.seen.nearby.close();
    view.render();
    assert.deepEqual(view.showing(), ['home']);
    // Home is asked to focus its own Nearby tile, not a control that did not
    // open the screen.
    assert.deepEqual(view.seen.home.focusRequest.target, { kind: 'tile', destination: 'nearby' });
  } finally { view.close(); }
});

test('a menu-only destination from Home names no Home control, and none is invented', async () => {
  const view = mount();
  try {
    await open(view);
    view.menu('About');
    assert.deepEqual(view.showing(), ['about']);
    view.seen.about.close();
    view.render();
    assert.deepEqual(view.showing(), ['home']);
    // Home has no control for About, so Back must not claim one. Focus goes to
    // the menu button that actually opened it instead.
    assert.equal(view.seen.home.focusRequest, null);
  } finally { view.close(); }
});

test("the header's Messages entry returns to the screen it was pressed over", async () => {
  // The header is shared, so Messages opens over Settings rather than over Chat.
  // It used to clear the origin outright, which dropped a user who checked their
  // messages from Settings into Chat and lost the screen they had left.
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenSettings();
    view.render();
    assert.deepEqual(view.showing(), ['settings']);
    view.seen.header.onMessages();
    view.render();
    assert.deepEqual(view.showing(), ['messages']);
    // Pressing it again on the screen it already opens must not record Messages as
    // its own origin, which would make Back a no-op.
    view.seen.header.onMessages();
    view.render();
    assert.deepEqual(view.showing(), ['messages']);
    view.seen.messages.close();
    view.render();
    assert.deepEqual(view.showing(), ['settings'], 'the screen it was pressed over is the origin');
    view.seen.settings.close();
    view.render();
    assert.deepEqual(view.showing(), ['home'], 'and Settings kept its own origin through it');
    assert.deepEqual(view.seen.home.focusRequest.target, { kind: 'settings' });
  } finally { view.close(); }
});

test("the header's Nearby entry records Home as its origin, with Home's own tile", async () => {
  // The same entry as the menu's Nearby, from the same shared header, so it names
  // the same Home control rather than the menu button it did not come from.
  const view = mount();
  try {
    await open(view);
    view.seen.header.onNearby();
    view.render();
    assert.deepEqual(view.showing(), ['nearby']);
    view.seen.nearby.close();
    view.render();
    assert.deepEqual(view.showing(), ['home']);
    assert.deepEqual(view.seen.home.focusRequest.target, { kind: 'tile', destination: 'nearby' });
  } finally { view.close(); }
});

test('re-entering the destination already on screen leaves the true origin intact', async () => {
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenTile('messages');
    view.render();
    view.menu('Settings');
    assert.deepEqual(view.showing(), ['settings']);
    // Tapping Settings again from Settings must not record Settings as its own
    // origin, which would make Back a no-op.
    view.menu('Settings');
    assert.deepEqual(view.showing(), ['settings']);
    view.seen.settings.close();
    view.render();
    assert.deepEqual(view.showing(), ['messages'], 'the origin Messages recorded is still the true one');
  } finally { view.close(); }
});

test('Home settings gear returns to Home and its gear', async () => {
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenSettings();
    view.render();
    assert.deepEqual(view.showing(), ['settings']);
    view.seen.settings.close();
    view.render();
    assert.deepEqual(view.showing(), ['home']);
    assert.deepEqual(view.seen.home.focusRequest.target, { kind: 'settings' });
  } finally { view.close(); }
});

/**
 * Back out of a Settings subsection returns to Settings, not to Home.
 *
 * `SECONDARY_SCREEN_DECISIONS.md:34` is the contract — "A Back action restores
 * the originating view and focus, rather than always landing in Chat." For
 * Agent, Heartbeat and Hardware opened from Settings, the originating view is
 * Settings, and the code already delivers that: `openMenuRoute` records
 * `{ page: 'settings' }` because Settings is the screen the entry was made from.
 * The two-press return still ends at Home, because the origin Settings itself
 * holds is the gear that opened it.
 *
 * These are regression locks, not a behaviour change. They exist because a
 * two-press walk of the deployed build reported the *first* Back landing on
 * Home, which this file's mount of the same frozen revision contradicts. The
 * walk could not read the parent's own page value — its own report says so —
 * while these assertions are exactly that value.
 */
test('Back from a Settings subsection returns to Settings, and a second Back to Home', async () => {
  for (const [subsection, marker] of [['agent', 'agent'], ['heartbeat', 'heartbeat'], ['hardware', 'hardware']]) {
    const view = mount();
    try {
      await open(view);
      view.seen.home.onOpenSettings();
      view.render();
      assert.deepEqual(view.showing(), ['settings'], 'the gear opens Settings');
      view.seen.settings.onOpen(subsection);
      view.render();
      assert.deepEqual(view.showing(), [marker], `${subsection} opens from its Settings row`);
      view.seen[marker].close();
      view.render();
      assert.deepEqual(view.showing(), ['settings'], `Back from ${subsection} must return to the screen that opened it`);
      view.seen.settings.close();
      view.render();
      assert.deepEqual(view.showing(), ['home'], 'the second Back returns to Home');
      assert.deepEqual(view.seen.home.focusRequest.target, { kind: 'settings' }, 'and restores the gear');
    } finally { view.close(); }
  }
});

test('leaving Settings for a subsection does not consume the Home origin', async () => {
  // The subsection replaces `backTo` while it is open; if it were cleared rather
  // than restored, the second Back would fall through to Chat (`main`) instead of
  // the gear the user actually came from.
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenSettings();
    view.render();
    view.seen.settings.onOpen('hardware');
    view.render();
    view.seen.hardware.close();
    view.render();
    view.seen.settings.close();
    view.render();
    assert.deepEqual(view.showing(), ['home'], 'Home, not Chat');
  } finally { view.close(); }
});

test('with no Home screen, Chat is the root and carries no Back it cannot honour', async () => {
  // The release default. Chat is then the surface the unit starts on, so a Back
  // control would have nowhere to go - and the frame is only given one when
  // there is a Home to return to. The composer and the New chat policy are not
  // conditional on that: they are the screen's own controls.
  const view = mount();
  try {
    await open(view, { homeEnabled: false });
    const markup = view.render({ homeEnabled: false });
    assert.deepEqual(view.showing(), ['chat'], 'the main page is the root surface');
    assert.match(markup, /device-chat-active/, 'Chat owns the footer row it sizes itself');
    assert.equal(view.seen.chat.onBack, undefined, 'no Back control without a destination');
    assert.equal(typeof view.seen.chat.onNewChat, 'function', 'New chat stays available');
  } finally { view.close(); }
});

test('Back out of the Camera returns to the Vision tile, not Chat', async () => {
  // The walk's High defect: the camera was the one route that recorded no
  // origin, and its own close forced the main page, so Home -> Vision -> Back
  // rendered Chat and the tile the user left was gone.
  const view = mount();
  try {
    await open(view);
    assert.deepEqual(view.showing(), ['home']);
    view.seen.home.onOpenTile('vision');
    view.render();
    assert.deepEqual(view.showing(), ['camera']);
    view.seen.camera.close();
    view.render();
    assert.deepEqual(view.showing(), ['home'], 'the tile the user left, not Chat');
  } finally { view.close(); }
});

test('Back out of the Camera returns to the Gallery when it was opened there', async () => {
  // The same defect reached from the Gallery's camera shortcut, which also
  // recorded Home — skipping the screen the camera was actually opened over.
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenTile('gallery');
    view.render();
    assert.deepEqual(view.showing(), ['gallery']);
    view.seen.gallery.onOpenCamera();
    view.render();
    assert.deepEqual(view.showing(), ['camera']);
    view.seen.camera.close();
    view.render();
    assert.deepEqual(view.showing(), ['gallery'], 'the screen it was opened over');
  } finally { view.close(); }
});

test('the Chat camera shortcut records Chat, so Back returns to it', async () => {
  // Recording the origin is what makes Back deterministic: without it the slot
  // holds whatever an earlier route left behind.
  const view = mount();
  try {
    await open(view);
    view.seen.home.onOpenTile('chat');
    view.render();
    assert.deepEqual(view.showing(), ['chat']);
    view.press((props) => props['aria-label'] === 'Open camera');
    assert.deepEqual(view.showing(), ['camera']);
    view.seen.camera.close();
    view.render();
    assert.deepEqual(view.showing(), ['chat']);
  } finally { view.close(); }
});
