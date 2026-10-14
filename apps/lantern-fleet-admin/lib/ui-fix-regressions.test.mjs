import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

/**
 * Regression locks for the defects the 14 October 2026 browser audit found and this
 * lane repaired. Each test names the measured symptom it prevents from returning.
 */
const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
const chat = readFileSync(new URL('../app/device/chat.tsx', import.meta.url), 'utf8');
const home = readFileSync(new URL('../app/device/home.tsx', import.meta.url), 'utf8');
const gallery = readFileSync(new URL('../app/device/gallery.tsx', import.meta.url), 'utf8');
const threads = readFileSync(new URL('../app/device/threads.css', import.meta.url), 'utf8');
const galleryCss = readFileSync(new URL('../app/device/gallery.css', import.meta.url), 'utf8');

test('a clear invalidates saved history, and the window closes on a later reading', () => {
  // A clear changes saved history: the exchange it stops holding is retained as a past
  // conversation. The cache kept serving the snapshot from before the clear, and because
  // the only forced read was gated on a *completed* session, the conversation the user
  // had just cleared did not appear in Past chats for ~56s (measured 55914ms).
  const reset = screen.slice(screen.indexOf('const clearAnswer'), screen.indexOf('const headerPage'));
  assert.match(reset, /const endHistoryChange = savedHistory\.beginChange\(\);/);
  assert.match(reset, /if \(accepted\) settleHistoryChange\(current, endHistoryChange\);\s*else endHistoryChange\(\);/);
  // `/clear` is acknowledged before its worker releases the slot, so closing the window
  // on the acknowledgement reads the list from before the change and then stamps the
  // cache, which suppresses the next read for HISTORY_REFRESH_MS.
  assert.match(screen, /const settleHistoryChange = \(cleared: string \| undefined, end: \(\) => void\) => \{/);
  assert.match(screen, /if \(movedOn \|\| Date\.now\(\) - started >= 5000\) \{ end\(\); return; \}/);
});

test('a start invalidated by a replaced authority is reported rather than swallowed', () => {
  // `active()` compares the device token it captured, so a re-key underneath a user who
  // is still standing there was indistinguishable from an abandoned gesture: a 409 after
  // a token replacement told them nothing at all, with nothing recording.
  assert.match(screen, /const reportSupersededActivation = \(issuedToken: string \| null\) => \{\s*if \(getDeviceToken\(\) === issuedToken\) return;\s*setActivation\(\{ pending: false, error: START_NOT_STARTED_NOTICE \}\);\s*\};/);
  const reported = (screen.match(/if \(token !== activationToken\.current\) \{\s*reportSupersededActivation\(issuedToken\);\s*return;\s*\}/g) || []).length;
  assert.equal(reported, 2, 'both await points in the activation report a superseded authority');
  // The two doors that do not raise a VoiceControlError must reach the same
  // judgement instead of falling straight to a silent withdrawal: a replaced
  // authority refused by confirmTouchSession arrives as a PLAIN Error, and a
  // confirmation that outlives voiceCommand's 2500ms abort arrives as an abort.
  const talk = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
  assert.match(talk, /if \(failure instanceof VoiceControlError && failure\.status === 409\) return refuseStart\(\);\s*[\s\S]*?if \(!active\(\)\) return refuseStart\(\);/);
  assert.doesNotMatch(talk, /if \(!active\(\)\) return 'withdrawn';\s*throw failure;/);
  // The door BEFORE the confirmation refuses through the same judgement. A token
  // replaced a moment earlier returns from `!admitted()`, which the confirm's
  // catch never reaches — measured by the reviewer: the log showed POST /cancel
  // -> 200 and no /start/confirm at all, so the user was told nothing.
  assert.match(talk, /if \(!admitted\(\)\) \{[\s\S]*?cancelReturned\(\);\s*return refuseStart\(\);\s*\}/);
  assert.doesNotMatch(talk, /cancelReturned\(\);\s*return 'withdrawn';\s*\}/);
  // Structurally: exactly ONE bare withdrawal may exist, and it is refuseStart's
  // own deliberate silent branch. Three separate doors were found returning one
  // anyway — the confirmation's catch, the admission check ahead of it, and the
  // photo path's history read — so any new one now fails this test.
  assert.match(talk, /if \(!authorityMoved\) return 'withdrawn';/);
  assert.equal((talk.match(/return 'withdrawn';/g) || []).length, 1, 'only refuseStart may withdraw silently');
});

test('Chat takes the keyboard when it opens', () => {
  // Arriving from the Home tile left `activeElement` on the document body: the exit was
  // fixed to restore the tile, but nothing claimed focus on the way in, so a keyboard or
  // assistive-technology user landed nowhere.
  assert.match(chat, /const back = useRef<HTMLButtonElement>\(null\);[\s\S]*const active = document\.activeElement;\s*if \(active === null \|\| active === document\.body \|\| active === document\.documentElement\) back\.current\?\.focus\(\);/);
  // It must not claim focus a control already holds: doing that took it from the
  // composer's field and the on-screen keys wrote into nothing.
  assert.doesNotMatch(chat, /useEffect\(\(\) => \{ back\.current\?\.focus\(\); \}, \[\]\);/);
  assert.match(chat, /<button ref=\{back\} type="button" className="device-chat-back"/);
});

test('a cancelled press withdraws only the start it asked for', () => {
  // A cancelled *second* press carries no start of its own, yet it cancelled the accepted
  // session an earlier completed gesture opened — measured, the screen went from
  // `listening` to `idle` and the owned session was dropped, discarding recorded audio.
  const cancel = home.slice(home.indexOf('const pointerCancel'), home.indexOf('const activate'));
  assert.match(cancel, /if \(!startPending\.current\) return;\s*startPending\.current = false;\s*onCancelCapture\(\);/);
});

test('the gallery viewer and the grid it opens over do not share a ref', () => {
  // One ref object attached to two elements means only one of them can hold it, so the
  // effect that focuses the viewer on open could not be relied on to reach the viewer.
  assert.match(gallery, /const content = useRef<HTMLDivElement>\(null\);/);
  assert.match(gallery, /device-gallery-content" tabIndex=\{0\} ref=\{content\}/);
  assert.match(gallery, /device-gallery-viewer" ref=\{viewer\}/);
});

test('the portrait conversation list can be pinned open as a column', () => {
  // Portrait always matches the narrow query, so the list can only be the off-canvas
  // drawer there. The pin keeps it open as the same 220px column the landscape layout
  // uses, without the scrim or the focus trap, because it is not a modal.
  assert.match(screen, /const \[chatsPinned, setChatsPinned\] = useState\(false\);/);
  assert.match(screen, /className="device-chats-pin" aria-pressed=\{chatsPinned\}/);
  assert.match(screen, /\$\{chatsPinned \? ' device-chats-pinned' : ''\}/);
  assert.match(screen, /\{showChats && !chatsPinned && <button className="device-chat-scrim"/);
  assert.match(screen, /useDrawerNavigation\(chatDrawer, showChats && !chatsPinned, closeChats\)/);
  assert.match(threads, /\.device-chats-pinned\.device-thread-layout \{ grid-template-columns: 220px minmax\(0, 1fr\); \}/);
  assert.match(threads, /\.device-chats-pinned > \.device-chat-list \{/);
  // Closing outranks the pin: a Close that left the pane open would be a lie. It
  // also puts the keyboard back on the control that opens the list, because a
  // pinned list is not a modal and the drawer's own restore never runs for it —
  // measured, dismissing one left focus on the document body.
  assert.match(screen, /const closeChats = useCallback\(\(\) => \{[\s\S]*?const heldFocus = !!list && !!document\.activeElement && list\.contains\(document\.activeElement\);[\s\S]*?setShowChats\(false\);\s*setChatsPinned\(false\);[\s\S]*?if \(!heldFocus\) return;[\s\S]*?\.device-chat-list-button, \.device-history-shortcut/);
});

test('the narrow gallery fits its panel and keeps the viewer actions reachable', () => {
  // 360x800 before: the content box was laid out 368px wide inside a 360px panel, and
  // the shared Menu (68% visible), the Camera shortcut (86%) and the gallery content
  // (95%) were all cut by the panel edge. Measured after, panel asserted at 360x800:
  // content 336px in 360px and no clipped control at all. The bar wraps so its three
  // controls fit, and the grid columns may shrink rather than overflow.
  assert.match(galleryCss, /@media \(max-width: 520px\) \{[\s\S]*?\.device-gallery \.device-subheader \{ flex-wrap: wrap; \}/);
  assert.match(galleryCss, /@media \(max-width: 520px\) \{[\s\S]*?\.device-gallery-grid \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); gap: 10px; \}/);
  // 1024x600 before: the viewer's action row began at 554 and ran to 626 in a 600px
  // panel, 26px past the edge. Measured after: 474..546, inside it. The row is sticky to
  // the bottom of its scroll container and its buttons may shrink.
  assert.match(galleryCss, /\.device-gallery-actions\.device-gallery-primary \{\s*position: sticky;\s*bottom: 0;/);
  assert.match(galleryCss, /\.device-gallery-actions > button \{\s*flex: 1;\s*min-width: 0;\s*\}/);
});
