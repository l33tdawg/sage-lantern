import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

// Screen-level integration rules for the Home 2a wiring. The component behaviour
// itself is exercised in a real browser (work/ember-lantern-home-20261002); these
// assertions pin the ordering decisions that a later edit could quietly undo,
// each of which was a defect found in review rather than a hypothetical.
const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
const talk = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
const camera = readFileSync(new URL('../app/device/camera.tsx', import.meta.url), 'utf8');
const gallery = readFileSync(new URL('../app/device/gallery.tsx', import.meta.url), 'utf8');
const home = readFileSync(new URL('../app/device/home.tsx', import.meta.url), 'utf8');
const navigation = readFileSync(new URL('../lib/home-navigation.ts', import.meta.url), 'utf8');

test('an intentional Home start always runs the New chat policy before recording', () => {
  const start = screen.slice(screen.indexOf('const startNewChat'), screen.indexOf('const leaveHome'));
  // The reset must not be conditional on the activation being a "new-chat" one:
  // an idle device can still have a loaded thread, and a successful reset that is
  // never followed by a start is the case the reviewer reproduced.
  assert.match(start, /const outcome = await clearAnswer\(\);/);
  assert.doesNotMatch(start, /first\.activation === 'new-chat'/);
  assert.ok(start.indexOf('clearAnswer') < start.indexOf('captureController.current?.start('), 'the reset precedes the start');
  // The token is re-checked after every await, so a gesture that ended during the
  // reset cannot leave a recording behind it.
  assert.ok((start.match(/token !== activationToken\.current/g) || []).length >= 2, 'intent checked after each await');
});

test('the readiness used to dispatch a start excludes this screen own reset', () => {
  // Reading the reset's own `clearing` back as external busy is what produced
  // "POST /clear -> 200" with no /start at all.
  assert.match(screen, /const captureBlocked = chats\.selecting \|\| touchBusy \|\| recording \|\| reviewing \|\| !!draft \|\| view\.phase === 'processing';/);
  assert.doesNotMatch(screen, /const captureBlocked = clearing \|\|/);
  assert.match(screen, /liveInputs\.current\.blocked,/);
});

test('an authoritative review reaches the existing Chat editor', () => {
  assert.match(screen, /homeEnabled && next\.voice\.phase === 'review' && next\.voice\.session_id && reviewRouted\.current !== next\.voice\.session_id/);
  assert.match(screen, /setPage\(\(current\) => \(current === 'home' \? 'main' : current\)\)/);
  // Routing a poll must not run the destructive policy as a side effect.
  const route = screen.slice(screen.indexOf('reviewRouted.current !== next.voice.session_id'), screen.indexOf('setStatus(next)'));
  assert.doesNotMatch(route, /clearAnswer/);
});

test('Home keeps its own panel: the conversation drawer never covers it', () => {
  assert.match(css, /\.device-screen\.device-home-active > \.device-chat-list \{ display: none; \}/);
  assert.match(css, /\.device-navigation-footer\[hidden\] \{ display: none; \}/);
  assert.match(css, /\.device-thread-layout > \.device-home \{ grid-column: 2; grid-row: 2; min-width: 0; \}/);
});

test('viewing the other surface stays possible while a turn is live', () => {
  // One shared capture owner survives the move, so navigation is not a context
  // change: only the destructive New chat action keeps the busy guard.
  assert.match(screen, /if \(page === 'home'\) \{\s*fenceActivation\(\);\s*captureController\.current\?\.withdrawStart\(\);\s*\}/);
  assert.match(screen, /const leaveHome = \(destination: Page\) => \{/);
  assert.match(screen, /if \(correction && busy\) return;/);
});

test('leaving Home withdraws a pending start at the owner, not only at the parent', () => {
  // The reproduced defect: `leaveHome` bumped the parent's activation token
  // while TalkControls.start compared a different intent token, so Chat adopted
  // a recording the user had already left Home during. Fencing the parent alone
  // cannot withdraw a command already awaiting its acknowledgement.
  const leave = screen.slice(screen.indexOf('const leaveHome'), screen.indexOf('const openHomeTile'));
  assert.match(leave, /captureController\.current\?\.withdrawStart\(\);/);
  // Withdrawing a pending start must not stop an accepted recording: that is
  // what `cancel` is for, and it is not called from plain navigation.
  assert.doesNotMatch(leave, /\.cancel\(\)/);
  assert.match(talk, /const withdrawStart = \(\) => \{ intent\.current \+= 1; \};/);
  assert.match(talk, /withdrawStart,/);
});

test('a start is dispatched only against a reading taken after the reset was acknowledged', () => {
  // The reset binding acknowledges `clearing` before its worker releases the
  // recording slot, and a start inside that window is refused 409 voice_busy.
  const start = screen.slice(screen.indexOf('const startNewChat'), screen.indexOf('const leaveHome'));
  assert.ok(start.indexOf('await clearAnswer()') < start.indexOf('resetSlotWait('), 'the slot wait follows the reset');
  assert.ok(start.indexOf('resetSlotWait(') < start.indexOf('captureController.current?.start('), 'the slot wait precedes the start');
  assert.match(start, /statusReadings\.current > mark/);
  assert.match(start, /if \(slot === 'withdrawn'\) return;/);
  assert.match(start, /if \(slot !== 'ready'\) \{/);
});

test('the epoch fence replaces the expectation counter, and a session id is not an epoch', () => {
  // The counter that used to excuse this screen's own session publications is
  // gone: the context is derived from the epoch *and* the token, so it survives
  // a clear or a start and changes only on a real rotation.
  assert.doesNotMatch(screen, /expectedSessionChanges/);
  assert.match(screen, /const observation = observeSessionContext\(next\.session_context\);/);
  // A reading that cannot be compared is refused, not rendered.
  assert.match(screen, /if \(observation === 'refused'\) throw new Error\('Status context unavailable'\);/);
  // A real rotation fences the activation and drops what was protected under the
  // old authority, before the new reading is rendered.
  const rotation = screen.slice(screen.indexOf("if (observation === 'changed')"), screen.indexOf('if (next.voice.session_id !== session.current)'));
  assert.match(rotation, /fenceActivation\(\);/);
  assert.match(rotation, /setCorrection\(null\);/);
  // Called through a ref: the poll must not be rebuilt when the conversations
  // hook returns a new closure, which is what depending on it directly would do.
  assert.match(rotation, /resetChats\.current\(\);/);
  assert.match(screen, /const resetChats = useRef\(chats\.reset\);/);
  // The session-id branch no longer fences: a new voice session is a publication,
  // and admission is decided by the parent's correlation and the runtime's own
  // confirmation instead.
  const sessionBranch = screen.slice(screen.indexOf('if (next.voice.session_id !== session.current)'), screen.indexOf('const held = liveInputs.current.ownership'));
  assert.doesNotMatch(sessionBranch, /fenceActivation\(\)/);
  // The fence still reaches the capture owner, not only this screen's own token.
  const fence = screen.slice(screen.indexOf('const fenceActivation'), screen.indexOf('const abandonActivation'));
  assert.match(fence, /captureController\.current\?\.withdrawStart\(\);/);
  assert.match(fence, /applyActivation\(IDLE_ACTIVATION\);/);
});

test('a session the owner does not hold is cancelled rather than kept alive', () => {
  // An owner intent token cannot be compared with a session the device has not
  // published yet, so the fence cannot judge a publication during a pending
  // start. Once the owner has adopted one, the two are comparable — and a device
  // publishing somebody else's live session means ours was superseded.
  const supersede = screen.slice(screen.indexOf('const held = liveInputs.current.ownership'));
  assert.match(supersede.slice(0, 700), /held\.owned && held\.sessionId && next\.voice\.session_id && next\.voice\.session_id !== held\.sessionId/);
  // Only a live phase: an idle reading may simply predate our own publication, and
  // cancelling on that would end a legitimate recording the moment it started.
  assert.match(supersede.slice(0, 700), /\['listening', 'review', 'processing'\]\.includes\(next\.voice\.phase\)/);
  assert.match(supersede.slice(0, 700), /fenceActivation\(\);/);
  assert.match(supersede.slice(0, 700), /captureController\.current\?\.cancel\(\);/);
  // It is judged in the poll, off the fresh reading, not in an effect.
  assert.doesNotMatch(screen, /setSelectedId\(null\)/);
  assert.ok(screen.indexOf('const held = liveInputs.current.ownership') < screen.indexOf('setStatus(next); setConnected(true);'));
});

test('a live turn closes the Home destinations that would cancel it', () => {
  // Presentation and dispatch both apply the policy, because a disabled button
  // is not a guard.
  assert.match(screen, /if \(busy && !destinationKeepsCapture\(destination\)\) return;/);
  assert.match(screen, /locked=\{busy\}/);
  assert.match(home, /const block = tileBlock\(tile\.id, available, locked\);/);
  assert.match(home, /const command = tileCommand\(tile\.id, available, locked\);/);
});

test('a Home shortcut and a header entry both return to the screen they were opened over', () => {
  // The origin is recorded when the route is opened rather than inferred from
  // the page being left, so a shortcut and a header entry can both reach the
  // same screen and still Back to different places.
  // The origin the entry displaced travels with it as `parent`, the same rule
  // the menu destinations use. The camera and the gallery link to each other,
  // so without it Home -> Gallery -> Camera, Back, Back had nothing left to
  // restore and fell through to Chat with focus on the shared Menu button.
  assert.match(screen, /const openRoute = \(destination: Page, origin: BackOrigin\) => \{\s*const displaced = backTo\.current;\s*backTo\.current = \{ \.\.\.origin, \.\.\.\(displaced \? \{ parent: displaced \} : \{\}\) \};\s*setPage\(destination\);/);
  // Chat is a route like the others. It used to record no origin at all, so
  // Back out of it fell through to the header Home control, which changes the
  // page but raises no focus request, and the user landed on Home with focus on
  // the document body rather than on the tile they had just left.
  assert.match(screen, /if \(destination === 'chat'\) \{\s*leaveHome\('main'\);\s*backTo\.current = \{ page: 'home', homeFocus: \{ kind: 'tile', destination: 'chat' \} \};\s*return;\s*\}/);
  assert.match(screen, /backTo\.current = \{ page: 'home', homeFocus: \{ kind: 'tile', destination: TILE_FOR_ROUTE\[route\] \} \};/);
  assert.match(screen, /setHomeFocus\(\(current\) => \(\{ target, nonce: \(current\?\.nonce \?\? 0\) \+ 1 \}\)\);/);
  // The header's own Messages and Nearby entries are the same journey as the
  // menu's: both are controls in the shared header, opened over whatever screen
  // the user is on, and each records that screen. They used to clear the origin
  // outright, which dropped a user who read their messages from Settings into
  // Chat and lost the screen they left.
  assert.match(screen, /onNearby=\{\(\) => openHeaderRoute\('status', 'nearby'\)\}/);
  assert.match(screen, /onMessages=\{\(\) => openHeaderRoute\('messages', 'messages'\)\}/);
  assert.match(screen, /focusRequest=\{homeFocus\}/);
});

test('every menu destination records the screen it was opened from', () => {
  // The menu lives in the shared header, so it opens over whatever screen the
  // user is on, and that screen is the origin. Only Settings used to record one;
  // the rest navigated without it and Nearby cleared it outright, so Back fell
  // through to Chat and lost the screen the menu was opened over. This is the
  // exact case of Home→Messages→menu→Past questions→Back, which must land back
  // on Messages rather than Chat.
  const open = screen.slice(screen.indexOf('const HOME_TARGET_FOR_MENU'), screen.indexOf('/** The Home tile a route belongs to'));
  assert.ok(open.length > 0, 'the menu route helper is present');
  // The origin is assigned, not merged, so a stale origin cannot survive — and
  // the origin it displaces is carried along as `parent`, because Settings to a
  // subsection is two steps in and has to be two steps out. Without the parent
  // the second Back had nothing left to restore and fell through to Chat.
  assert.match(open, /\.\.\.\(page === 'home' && homeFocus \? \{ page: 'home' as const, homeFocus \} : \{ page, \.\.\.\(opener \? \{ focusRef: opener \} : \{\}\) \}\)/);
  assert.match(open, /const displaced = backTo\.current;/);
  // Re-entering the destination already on screen must not record it as its own
  // origin, which would make Back a no-op on a screen that has one true origin.
  assert.match(open, /if \(page === destination\) return;/);
  // Leaving Home withdraws a pending start for every destination, not only
  // Settings, which is what `leaveHome` is for.
  assert.match(open, /leaveHome\(destination\);/);
  // Home has a control for its gear and for two of its tiles, and none at all
  // for the menu-only destinations.
  assert.match(open, /settings: \{ kind: 'settings' \},/);
  assert.match(open, /messages: \{ kind: 'tile', destination: 'messages' \},/);
  assert.match(open, /status: \{ kind: 'tile', destination: 'nearby' \},/);
  // Every one of the five menu destinations goes through it.
  for (const destination of ['history', 'status', 'settings', 'about']) {
    assert.match(screen, new RegExp(`<button onClick=\\{\\(\\) => openHeaderRoute\\('${destination}', 'menu'\\)\\}>`));
  }
  // The sharing entry keeps its own reset, and only on a real navigation.
  assert.match(screen, /openHeaderRoute\('sharing', 'menu', \(\) => setShareSource\(undefined\)\)/);
  // Home's gear is the same case: the origin is Home and its focus target is the
  // gear, since the menu itself is closed by then. The gear is inside Home, so it
  // is not a header control and records no opener of its own.
  assert.match(screen, /onOpenSettings=\{\(\) => openHeaderRoute\('settings'\)\}/);
  // A Home origin that names no target is a menu-only destination, which Home has
  // no control for, so the origin records the menu button that opened it — the one
  // control in the header the menu item leaves behind.
  assert.match(screen, /onClick=\{\(\) => openHeaderRoute\('about', 'menu'\)\}/);
  assert.match(screen, /if \(target\) requestAnimationFrame\(\(\) => focusHeaderControl\(target\)\);/);
});

test('the Settings gear is a typed focus target, not a Home destination', () => {
  // The gear has no tile and no route: adding it to HomeDestination would widen
  // every tile-only path with a value those paths cannot render.
  assert.match(navigation, /export type HomeFocusTarget = \{ kind: 'tile'; destination: HomeDestination \} \| \{ kind: 'settings' \};/);
  assert.doesNotMatch(navigation, /export type HomeDestination = [^;]*'settings'/);
  assert.match(navigation, /export type HomeDestination = 'chat' \| 'vision' \| 'agent' \| 'gallery' \| 'messages' \| 'nearby';/);
  // Home restores focus through the target's own ref, and the parent hands the
  // target down rather than a bare destination.
  assert.match(home, /target\.kind === 'settings' \? settingsRef\.current : tileRefs\.current\.get\(target\.destination\)/);
  assert.match(home, /const settingsRef = useRef<HTMLButtonElement>\(null\);/);
  assert.match(home, /ref=\{settingsRef\}/);
});

test('Back restores focus from the origin, not from the screen it leaves', () => {
  // Settings is reached through the shared menu, so the screen being left is
  // Settings whatever the user came from: reading the origin is the only way
  // Back can put focus back on the control that owns the screen it returns to.
  const back = screen.slice(screen.indexOf('const goBack = ()'), screen.indexOf('const busy = clearing'));
  // The control is the one recorded when the route was opened, named rather than
  // guessed, so two controls reaching the same screen cannot be confused.
  assert.match(back, /const target = origin\.focusRef;/);
  assert.match(back, /if \(target\) requestAnimationFrame\(\(\) => focusHeaderControl\(target\)\);/);
  // Guessing from the origin's page is what put focus on the Menu button for every
  // return to a screen the header has no entry for — Settings above all, where the
  // Menu button had opened nothing. That screen's own entry focus stands instead.
  assert.doesNotMatch(back, /focusPageControl\(origin\.page\)/);
  assert.doesNotMatch(back, /origin\.page === 'home'/);
  // With no recorded origin — the header's and the menu's own entries — the
  // screen being left *is* the origin, so the page is still the right input.
  assert.match(back, /requestAnimationFrame\(\(\) => focusHeaderControl\(page === 'messages' \? 'messages'/);
});

test('the camera Gallery shortcut returns to the camera, and Gallery opens from Home', () => {
  assert.match(screen, /onOpenGallery=\{\(\) => openRoute\('gallery', \{ page: 'camera', focusRef: 'camera' \}\)\}/);
  assert.match(screen, /page === 'gallery' \? <GalleryScreen/);
  assert.match(screen, /const TILE_FOR_ROUTE: Record<string, HomeDestination> = \{\s*messages: 'messages', status: 'nearby', gallery: 'gallery', agent: 'agent',\s*\};/);
  assert.match(camera, /onOpenGallery\?: \(\) => void/);
});

test('the Agent tile opens the Agent destination, and Back returns to the tile', () => {
  // The 2c counterpart of the Gallery route above. Agent is a Home destination
  // *and* a Settings entry, so both origins have to exist: the tile names the
  // tile it came from, and the Settings entry names Settings.
  assert.match(screen, /page === 'agent' \? <AgentScreen/);
  assert.match(screen, /: destination === 'agent' \? 'agent' : null/);
  assert.match(screen, /Object\.freeze\(\['chat', 'vision', 'messages', 'nearby', 'gallery', 'agent'\]\)/);
  // Heartbeat and Hardware have no Home tile: they are reached from the screens
  // that own them, so they must not appear in the available-tile list.
  assert.doesNotMatch(screen, /HOME_AVAILABLE[^\n]*'heartbeat'/);
  assert.doesNotMatch(screen, /HOME_AVAILABLE[^\n]*'hardware'/);
  // The Settings entry and Agent's Heartbeat button are controls inside the screens
  // being left, which unmount, so both record the origin without naming a header
  // control: the Menu button did not open either screen and must not claim it.
  assert.match(screen, /onOpen=\{\(destination\) => openHeaderRoute\(destination\)\}/);
  assert.match(screen, /onOpenHeartbeat=\{\(\) => openHeaderRoute\('heartbeat'\)\}/);
});

test('a gallery photo is staged in Chat without starting the microphone', () => {
  // Camera's own handoff deliberately starts capture; the gallery's must not,
  // because the user has not asked to speak. The distinction is the presence of
  // photoStart, which is what drives TalkControls' auto-start.
  const body = screen.slice(screen.indexOf('const stagePhotoInChat'));
  const stageBody = body.slice(0, body.indexOf('\n  };'));
  assert.match(stageBody, /setPhoto\(attachment\)/);
  assert.match(stageBody, /setPhotoStart\(undefined\)/);
  assert.doesNotMatch(stageBody, /setPhotoStart\(crypto\.randomUUID\(\)\)/);
  assert.doesNotMatch(stageBody, /captureController/);
  assert.match(screen, /onAskAboutPhoto=\{\(attachment\) => stagePhotoInChat\(attachment\)\}/);
});

test('the gallery reads real bytes and never calls a record an image without them', () => {
  assert.match(gallery, /parseHistoryPhoto\(await voiceCommand\('history\/photo'/);
  assert.match(gallery, /thumbnailBatches\(state\.items, THUMBNAIL_BATCH\)/);
  // Deletion is the combined history record, worded by the helper rather than
  // written inline, and it is never presented as a photo-only command. The
  // runtime's own `photo_removal` is passed through: `sage_backed` alone told
  // the owner the photo was gone while the same reply reported it retained.
  assert.match(gallery, /voiceCommand\('history\/delete', \{ session_id: item\.session_id \}\)/);
  assert.match(gallery, /deleteEffect\(selected, history\?\.sage_backed === true, history\?\.photo_removal\)/);
  assert.doesNotMatch(gallery, /Delete photo/);
  // Protected state: bytes are dropped at every authority boundary.
  assert.match(gallery, /subscribeDeviceSession\(\(\) => \{ forget\(\); setSelectedId\(null\); setConfirming\(false\); \}\)/);
  assert.match(gallery, /if \(document\.hidden\) \{ forget\(\); setSelectedId\(null\); \}/);
  assert.match(gallery, /if \(!mounted\.current \|\| document\.hidden \|\| generation\.current !== start\) return;/);
  // And never persisted.
  // The words appear in the file's own comment about what it must not do; the
  // assertion is about calls, so it requires a property access.
  assert.doesNotMatch(gallery, /(?:localStorage|sessionStorage|indexedDB)\s*[.[]/);
});

test('the six tiles still honour their own minimum height inside the real screen', () => {
  // `.device-screen button` is one class plus one element, so a bare
  // `.device-home-tile` loses to it and the tile minimum was silently suppressed
  // in the integrated Screen while the standalone fixture honoured it.
  const home = readFileSync(new URL('../app/device/home.css', import.meta.url), 'utf8');
  const blocks = home.split('}').map((block) => ({ selector: block.split('{')[0], body: block.split('{')[1] ?? '' }))
    .filter((block) => /\.device-home-tile(\s*[,:]|\s*$)/.test(`${block.selector} `) && /min-height:\s*\d+px/.test(block.body));
  const heights = blocks.map((block) => /min-height:\s*(\d+)px/.exec(block.body)?.[1]);
  // Enumerated, not asserted as a minimum: a new context that declares a tile
  // height has to be added here deliberately. The fifth is the very short
  // landscape panel (1024x600), where the tiles concede size so the middle
  // region can hold a readable answer instead of a line-and-a-half window.
  assert.deepEqual(heights.sort(), ['104', '104', '132', '160', '88']);
  for (const block of blocks) {
    assert.match(block.selector, /\.device-screen \.device-home-tile/, `${block.selector.trim()} must outrank .device-screen button`);
  }
  assert.match(home, /grid-auto-rows: 1fr;/);
});

test('lifecycle changes fence a pending activation', () => {
  // New session, disconnect and a hidden screen each invalidate an activation
  // that has not been acknowledged yet.
  assert.match(screen, /const fenceActivation = useCallback\(\(\) => \{\s*activationToken\.current \+= 1;/);
  assert.ok((screen.match(/fenceActivation\(\);/g) || []).length >= 3, 'fenced on session change, poll failure and navigation');
  assert.match(screen, /abandonActivation\(\);/);
  // The owner withdraws its own pending start when it is abandoned or unmounted.
  assert.match(talk, /const abandon = \(\) => \{\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*\/\/[^\n]*\n\s*intent\.current \+= 1;/);
});

test('a capture is only adopted, renewed or finished under the authority it was issued under', () => {
  // Captured before the first protected await, not from the reply that answers
  // it. A rotation landing inside the command is admitted under the new epoch, so
  // the delayed reply and every reading after it move together: the reply cannot
  // witness the authority the user actually activated, and comparing it with
  // itself is exactly how such a start would be adopted.
  assert.match(talk, /const issuedUnder = publishedContext\(\);/);
  const issuedAt = talk.indexOf('const issuedUnder = publishedContext();');
  assert.ok(issuedAt >= 0 && issuedAt < talk.indexOf("await voiceCommand('history/list'"), 'the expectation precedes the first protected read');
  assert.ok(issuedAt < talk.indexOf("await voiceCommand('start'"), 'the expectation precedes the start');
  assert.ok(issuedAt < talk.indexOf('await confirmTouchSession('), 'the expectation precedes the confirmation');
  // An authority that cannot be compared opens no recording, and one the
  // protected read outlived is refused before the start is dispatched at all.
  assert.match(talk, /if \(issuedUnder === undefined\) return refuseStart\(\);/);
  // The protected read is itself a reply, and it answers with the policy of the
  // epoch that admitted it. Judging only the current reading would let a rotation
  // this client has not caught up with decide whether the original bytes travel,
  // so the reply is fenced — against the captured expectation and against what
  // the device publishes now — before any of it is read.
  assert.match(talk, /if \(!sessionContextEqual\(listedUnder, issuedUnder\) \|\| !contextMatches\(listedUnder\)\) return refuseStart\(\);/);
  assert.match(talk, /try \{ listedUnder = replySessionContext\(listing\); \}/);
  assert.ok(talk.indexOf('replySessionContext(listing)') < talk.indexOf('parseDeviceHistory(listing)'),
    'the photo policy reply is fenced before it is parsed');
  // The reply is judged against that captured expectation, and it must still be
  // the authority the device publishes — either disagreement cancels the
  // credentials the reply carried rather than adopting them.
  assert.match(talk, /try \{ repliedUnder = replySessionContext\(started\); \}/);
  assert.match(talk, /if \(!sessionContextEqual\(repliedUnder, issuedUnder\) \|\| !contextMatches\(repliedUnder\)\) \{ cancelReturned\(\); return refuseStart\(\); \}/);
  assert.match(talk, /if \(!sessionContextEqual\(confirmedUnder, issuedUnder\) \|\| !contextMatches\(confirmedUnder\)\) \{\s*cancelReturned\(\);\s*return refuseStart\(\);/);
  // Adoption, renewal and the stopped capture are all anchored to the captured
  // authority, so a later protected step cannot reintroduce the reply's own.
  assert.match(talk, /admittedContext\.current = issuedUnder;/);
  // The heartbeat is compared before the lease is renewed again.
  const keepalive = talk.slice(talk.indexOf('const kept = await voiceCommand'), talk.indexOf('if (session.current === current && recordingActive.current) keepRecording(current);'));
  assert.match(keepalive, /const renewedUnder = replySessionContext\(kept\);/);
  assert.match(keepalive, /throw new Error\(SESSION_CONTEXT_UNAVAILABLE\);/);
  // A stopped capture whose context moved is not treated as this owner's outcome.
  assert.match(talk, /if \(!sessionContextEqual\(stoppedUnder, admittedContext\.current\) \|\| !contextMatches\(stoppedUnder\)\) \{/);
  // Admission is never inferred from the voice session id.
  assert.doesNotMatch(talk, /issuedUnder\s*=\s*[^;]*session_id/);
});

test('an obsolete poll reply is refused before the shared observation seam', () => {
  assert.match(screen, /import \{ deviceSessionHeaders, getDeviceToken, observeSessionContext, subscribeDeviceSession, establishedSessionContext, fenceProtectedReply \} from '\.\.\/\.\.\/lib\/device-session';/);
  const poll = screen.slice(screen.indexOf('const poll = async () => {'), screen.indexOf('const pollTelemetry = async () => {'));
  // The auth session the request is issued under is captured before dispatch...
  const issuedAt = poll.indexOf('const issuedUnder = getDeviceToken();');
  assert.ok(issuedAt >= 0 && issuedAt < poll.indexOf('await fetch('), 'the session is captured before the request is dispatched');
  // ...and a reply that outlived it, or the surface itself, is refused before it
  // can establish or roll back an authority every protected surface compares
  // against — a replaced token has already reset that observation, so such a
  // reply could only put the dead authority back.
  const refusedAt = poll.indexOf('if (disposed || getDeviceToken() !== issuedUnder || authorityRevision.current !== issuedRevision) return;');
  assert.ok(refusedAt >= 0, 'the obsolete reply is refused');
  assert.ok(refusedAt < poll.indexOf('observeSessionContext('), 'the refusal precedes the shared observation');
  assert.ok(refusedAt < poll.indexOf('setStatus(next)'), 'the refusal precedes every state update');
  // A current same-token rotation is untouched: it still notifies subscribers and
  // still drops what was protected under the epoch that moved.
  assert.match(poll, /if \(observation === 'changed'\) \{\s*fenceActivation\(\);/);
});

test('a refused start is reported to an active activation and silent to a withdrawn one', () => {
  // 409 on the confirmation is a refusal of this request, not proof of a takeover.
  assert.match(talk, /if \(failure instanceof VoiceControlError && failure\.status === 409\) return refuseStart\(\);/);
  // "Withdrawn" is narrower than `active()` being false. `active()` also compares
  // the device token it captured, so a re-key underneath a user who is still
  // standing there used to look exactly like an abandoned gesture — and the user
  // was told nothing at all, with no stop affordance, while believing they were
  // recording. Only an activation that is genuinely gone stays silent; when the
  // authority is the only thing that moved, the surface says the one thing that
  // is true either way.
  assert.match(talk, /if \(!active\(\)\) \{\s*const abandoned = !mounted\.current \|\| token !== intent\.current;\s*const authorityMoved = mounted\.current && !abandoned && getDeviceToken\(\) !== deviceToken;\s*if \(!authorityMoved\) return 'withdrawn';\s*setError\(START_NOT_STARTED_NOTICE\);\s*return 'not-started';\s*\}\s*if \(mounted\.current\) setError\(START_NOT_STARTED_NOTICE\);\s*return 'not-started';/);
  assert.match(talk, /export const START_NOT_STARTED_NOTICE = 'The recording did not start\. Tap to try again\.';/);
  // The same wording reaches Home, which renders the activation rather than the
  // capture owner's own notice.
  assert.match(screen, /if \(started === 'not-started'\) \{ applyActivation\(\{ pending: false, error: START_NOT_STARTED_NOTICE \}\); return; \}/);
  assert.match(screen, /import TalkControls, \{ START_NOT_STARTED_NOTICE, type TalkController, type TalkOwnership \} from '\.\/talk';/);
  // A withdrawn activation is still not reported as a failure.
  assert.match(screen, /if \(started === 'started' \|\| started === 'withdrawn'\) \{ applyActivation\(IDLE_ACTIVATION\); return; \}/);
});

test('the status reading carries the context the fence compares', () => {
  const state = readFileSync(new URL('../lib/device-state.ts', import.meta.url), 'utf8');
  assert.match(state, /session_context: readSessionContext\(value\.session_context\)/);
  assert.match(state, /session_context: SessionContextReading;/);
  const control = readFileSync(new URL('../lib/voice-control.ts', import.meta.url), 'utf8');
  assert.match(control, /export function replySessionContext\(value: unknown\): SessionContext \| null \{/);
  assert.match(control, /return parseSessionContext\(result\.session_context\);/);
});

test('a retained partial leaves through the one authority seam, not through the poll', () => {
  // Rook's re-freeze reproduced the defect this pins: `observeSessionContext`
  // drops the authority and *then* reports the refusal, so the retained words
  // were already outliving the authority that produced them before the poll's
  // catch ran — and a replaced token or a refusal first observed by another
  // surface never passes through this poll at all. The hold is protected
  // content, so it drops where every other protected surface drops its own.
  assert.match(screen, /useEffect\(\(\) => subscribeDeviceSession\(\(\) => \{ authorityRevision\.current \+= 1; setPartialTurn\(null\); \}\), \[\]\);/);
  assert.equal((screen.match(/subscribeDeviceSession\(/g) || []).length, 1,
    'the seam is subscribed once; a second invalidation channel is what it exists to prevent');
  // Nothing but an accepted processing reading ever sets the hold, so restoring
  // the old context cannot revive words that were already dropped: there is no
  // path from a reading back to the hold except the one behind this guard.
  assert.match(screen, /if \(acceptedView\.phase === 'processing' && next\.voice\.response\.trim\(\)\) \{\s*setPartialTurn\(\{/);
});

test('an ordinary transport interruption still retains the words', () => {
  // The clear belongs on the seam, not on "the poll failed". A disconnect inside
  // one unchanged authority is exactly the interruption retention exists for,
  // and the catch is where this screen reports the device as unreachable;
  // clearing there would delete the answer the Incomplete marker is meant to
  // keep. The authority fences that *do* clear it stay in place.
  const poll = screen.slice(screen.indexOf('const poll = async () => {'), screen.indexOf('const pollTelemetry = async () => {'));
  const fallback = poll.slice(poll.indexOf('} catch {'));
  assert.doesNotMatch(fallback, /setPartialTurn\(null\)/);
  assert.match(screen, /if \(observation === 'changed'\) \{\s*fenceActivation\(\);[\s\S]{0,400}?setPartialTurn\(null\);/);
});
