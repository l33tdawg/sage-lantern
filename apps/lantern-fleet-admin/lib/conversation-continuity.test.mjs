/**
 * Chat continuity: the wiring a reopened conversation depends on.
 *
 * Rook's grouping helper is pinned by `conversation-replay.test.mjs`, and the
 * transport fence by `conversation-request.test.mjs`. What neither covers is the
 * screen-level wiring that makes reopening a saved chat work at all, and every
 * clause below is there because a plausible edit would silently break one of
 * them:
 *
 *   - a selection must fence the drawer, the capture lane and the composer while
 *     it is in flight, or a second gesture can start against a thread that is
 *     being replaced;
 *   - a selection that fails must not close the list, because the reader is
 *     still where they were;
 *   - the open thread's name has to come from the thread that is open. Reading
 *     it from the snapshot instead was the shape that gave a reopened chat the
 *     name of the chat it replaced;
 *   - the capture owner is mounted once, outside the subtree a selection
 *     re-renders. A second instance, or one inside the thread region, would
 *     unmount a recording when the reader switched chats.
 *
 * The browser replay in
 * `work/ember-chat-continuity-20261009/replay/` drives these same rules against
 * the real components with parser-shaped payloads. It re-states the expressions
 * below rather than mounting `screen.tsx`, so this file is what keeps that
 * re-statement honest: if the product moves, these fail first.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (path) => readFileSync(new URL(path, import.meta.url), 'utf8');
const screen = source('../app/device/screen.tsx');
const conversations = source('../app/device/conversations.tsx');
const threads = source('./conversation-threads.ts');

test('a selection fences every route into the thread that is being replaced', () => {
  // The drawer's own controls are disabled by `busy`, and `busy` has to include
  // the selection or a second thread can be pressed mid-flight.
  assert.match(screen, /const busy = clearing \|\| chats\.selecting \|\| touchBusy \|\| recording \|\| reviewing \|\| !!draft \|\| view\.phase === 'processing';/);
  // The capture lane is fenced separately: it must not rest on the drawer's
  // computed busy, because the composer is rendered outside the drawer.
  assert.match(screen, /const captureBlocked = chats\.selecting \|\| touchBusy \|\| recording \|\| reviewing \|\| !!draft \|\| view\.phase === 'processing';/);
  // The one capture owner is told the device is not available while the thread
  // changes, so it cannot open a typed review against the outgoing chat.
  assert.match(screen, /connected=\{connected && !clearing && !chats\.selecting && !draft &&/);
});

test('a selection that fails leaves the list open', () => {
  // Pressing the thread already showing only closes the list. Pressing a
  // different one closes it *inside* the success branch, so a refusal — an
  // unreachable device, a reply from a superseded authority — leaves the reader
  // looking at the list they can retry from.
  assert.match(screen, /if \(thread\.id === chats\.current\?\.thread_id\) setShowChats\(false\);/);
  assert.match(screen, /else void chats\.select\(thread\.id\)\.then\(\(ok\) => \{ if \(ok\) \{/);
});

test('the open thread is named from the open thread', () => {
  // The title is looked up by the id the conversation is actually on. Taking it
  // from the snapshot, or from the first entry in the list, is what gives a
  // reopened chat the name of the chat it replaced.
  assert.match(screen, /threadTitle=\{chats\.threads\.find\(\(thread\) => thread\.id === chats\.current\?\.thread_id\)\?\.title\}/);
  // The exchanges come from the active snapshot and nothing else.
  assert.match(screen, /<ChatExchanges items=\{\(chats\.current\?\.items \|\| \[\]\)\.filter\(/);
});

test('there is exactly one capture owner, and it is not inside the thread region', () => {
  assert.equal((screen.match(/<TalkControls/g) || []).length, 1);
  // It is rendered in the shell's footer, after the conversation region closes.
  const owner = screen.indexOf('<TalkControls');
  const exchanges = screen.indexOf('<ChatExchanges');
  assert.ok(exchanges >= 0 && exchanges < owner, 'the capture owner renders after the exchange region, not inside it');
  assert.ok(screen.indexOf('<Chat ') < owner, 'the Chat frame closes before the capture owner renders');
});

test('the hook keeps both guards that refuse a superseded selection', () => {
  // A second selection cannot start while one is in flight.
  assert.match(conversations, /if \(selectingRef\.current\) return false;/);
  assert.match(conversations, /selectingRef\.current = true;/);
  // A reply that lands after the world moved is dropped rather than adopted,
  // and the drop is silent: the surface was already cleared by the same event,
  // so a per-selection error on top of it would name a selection that no longer
  // belongs to anything on screen.
  assert.match(conversations, /if \(generation\.current !== epoch\) return false;/);
  assert.match(conversations, /catch \{ if \(generation\.current === epoch\) setError\('Could not open this chat\. Try again\.'\); return false; \}/);
  // A selection is never replayed by the history retry path: only a protected
  // history read may be retried, and only within its original deadline.
  assert.match(threads, /if \(action !== 'history\/list' \|\| attempt >= 2/);
});
