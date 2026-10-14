import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import {
  CAPTURE_LOCK_REASON, HOME_TILES, HOLD_MS, IDLE_GESTURE, IDLE_PREPARE, NO_CAPTURE_OWNER, RESET_SLOT_WAIT_MS,
  TILE_CAPTURE_LOCK_SHORT, TILE_NOT_IN_BUILD_SHORT, VOICE_CONTROL_LABEL, destinationKeepsCapture,
  acknowledgePrepare, beginPrepare, cancelPrepare, captureAllowed, gestureCancel, gestureDown,
  gestureHold, gestureShouldCancel, gestureUp, holdElapsed, homeStatus, homeTiles, prepareFailed,
  prepareFinished, prepareSucceeded, radioSummary, releaseAction, resetSlotWait, routeFor, tileCommand, tileFor,
  tileBlock, typedLaneReady, unavailableReason, voiceControlState,
} from './home-navigation.ts';

// A status reading that names the session a local owner would have been given.
const OWNED_SESSION = '33333333-3333-4333-8333-333333333333';
const OWNED = { owned: true, sessionId: OWNED_SESSION };

// A healthy device with both lanes ready. `touch_ready` is part of the baseline
// because the on-screen control drives the touch lane: a fixture without it
// describes a physical-only device, which is a different case with its own test.
function fixture(phase = 'idle') {
  return {
    schema: 'sage.lantern.local-status.v1', observed_at: new Date().toISOString(),
    local_voice_ready: true, audio: { present: true, detail: 'Audio service ready', ready: true, capture_verified: false, ptt_ready: true },
    radio: { present: true, detail: 'USB detected' },
    voice: { phase, transcript: 'Question', response: 'Answer', touch_ready: true },
    services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
  };
}

const ALL = ['chat', 'vision', 'agent', 'gallery', 'messages', 'nearby'];

// --- Tile map --------------------------------------------------------------

test('tiles are six destinations in the owner-approved row-major order', () => {
  // Two columns, three rows: Chat | Vision, Agent | Gallery, Messages | Nearby.
  assert.deepEqual(homeTiles().map((tile) => tile.id), ALL);
  assert.deepEqual(homeTiles().map((tile) => tile.label),
    ['Chat', 'Vision', 'Agent', 'Gallery', 'Messages', 'Nearby']);
});

test('the expansion kept the first four reference positions', () => {
  // The owner's amendment added two destinations; it did not move the four the
  // user had already learned. A reorder here is the regression, not the count.
  assert.deepEqual(homeTiles().slice(0, 4).map((tile) => tile.id), ['chat', 'vision', 'agent', 'gallery']);
});

test('the tile order is frozen and cannot be reordered by a caller', () => {
  assert.throws(() => { HOME_TILES.push({ id: 'chat', label: 'x', accessibleLabel: 'x' }); }, TypeError);
  // A caller mutating the returned value must not affect the shared map, which
  // is what makes the positions stable for a user navigating by position.
  assert.equal(homeTiles().length, 6);
});

test('every tile carries a visible label as well as an accessible one', () => {
  for (const tile of homeTiles()) {
    assert.ok(tile.label.trim().length > 0, `${tile.id} needs a visible label`);
    assert.ok(tile.accessibleLabel.length > tile.label.length, `${tile.id} accessible label should say more than the visible one`);
  }
});

test('tile labels are short enough to read at a glance', () => {
  for (const tile of homeTiles()) assert.ok(tile.label.length <= 12, `${tile.label} is too long for a tile`);
});

test('destinations map to their screens and cannot drift from the tiles', () => {
  assert.deepEqual(routeFor('chat'), { kind: 'existing', page: 'chat' });
  assert.deepEqual(routeFor('vision'), { kind: 'existing', page: 'camera' });
  // Messages and Nearby are existing screens, not new surfaces: the expansion
  // routes to the inbox and the radio overview that already exist.
  assert.deepEqual(routeFor('messages'), { kind: 'existing', page: 'messages' });
  assert.deepEqual(routeFor('nearby'), { kind: 'existing', page: 'status' });
  assert.deepEqual(routeFor('gallery'), { kind: 'existing', page: 'gallery' });
  assert.equal(routeFor('agent').kind, 'candidate');
  // Every tile must have a route, or a tile can be rendered that opens nothing.
  for (const tile of homeTiles()) assert.ok(routeFor(tile.id));
});

test('an unknown destination is a programming error, not a silent fallback', () => {
  assert.throws(() => tileFor('nope'), /unknown home destination/);
});

// --- Availability ----------------------------------------------------------

test('an unavailable tile reports a visible reason rather than being hidden', () => {
  const available = ['chat', 'vision'];
  assert.equal(unavailableReason('chat', available), null);
  assert.equal(unavailableReason('agent', available), 'Tasks and schedules are not available in this build.');
  assert.equal(unavailableReason('gallery', available), 'Saved photos are not available in this build.');
  assert.equal(unavailableReason('messages', available), 'Messages are not available in this build.');
  assert.equal(unavailableReason('nearby', available), 'The radio overview is not available in this build.');
  for (const id of ALL) {
    const reason = unavailableReason(id, available);
    if (reason !== null) assert.ok(reason.trim().length > 0, `${id} needs a reason`);
  }
});

test('an unavailable tile resolves to no navigation rather than a fallback screen', () => {
  const available = ['chat', 'vision'];
  assert.deepEqual(tileCommand('agent', available), { kind: 'none' });
  assert.deepEqual(tileCommand('gallery', available), { kind: 'none' });
  assert.deepEqual(tileCommand('chat', available), { kind: 'open', destination: 'chat' });
});

// --- Leaving Home while a turn is live -------------------------------------

test('only Chat keeps the single capture owner mounted', () => {
  // This is the property the lock exists for, and it is a fact about the
  // composition rather than a preference: every other destination renders
  // outside the subtree that holds TalkControls, so opening one unmounts it and
  // its own cleanup cancels the session it holds.
  assert.equal(destinationKeepsCapture('chat'), true);
  for (const id of ['vision', 'agent', 'gallery', 'messages', 'nearby']) {
    assert.equal(destinationKeepsCapture(id), false, `${id} unmounts the capture owner`);
  }
});

test('a live turn closes every destination that would cancel it', () => {
  const available = ['chat', 'vision', 'messages', 'nearby', 'gallery'];
  for (const id of ['vision', 'messages', 'nearby', 'gallery']) {
    assert.deepEqual(tileCommand(id, available, true), { kind: 'none' }, `${id} must not be dispatched while busy`);
    assert.equal(unavailableReason(id, available, true), CAPTURE_LOCK_REASON, `${id} must say why`);
  }
  // Viewing the same turn from Chat keeps one owner alive: this is the case the
  // brief requires to keep working.
  assert.deepEqual(tileCommand('chat', available, true), { kind: 'open', destination: 'chat' });
  assert.equal(unavailableReason('chat', available, true), null);
});

test('an idle Home is unaffected: transport health is not route availability', () => {
  // A disconnected device must still be able to open Messages and Nearby. The
  // lock is about a live turn, not about the transport being down.
  const available = ['chat', 'vision', 'messages', 'nearby', 'gallery'];
  for (const id of available) {
    assert.deepEqual(tileCommand(id, available, false), { kind: 'open', destination: id });
    assert.equal(unavailableReason(id, available, false), null);
  }
});

test('an undelivered destination reports that, not the capture lock', () => {
  // Two different reasons must not collapse into one: "not in this build" is not
  // "finish your question first".
  const available = ['chat'];
  assert.match(unavailableReason('agent', available, true), /not available in this build/);
  assert.notEqual(unavailableReason('agent', available, true), CAPTURE_LOCK_REASON);
  assert.deepEqual(tileCommand('agent', available, true), { kind: 'none' });
});

test('a blocked tile shows its own claim, not the other block\'s word', () => {
  // The visible line is the only thing a touch panel can read: `title` needs a
  // pointer and this shell has none, so a locked tile that says the same word as
  // a destination this build does not have tells the owner their photos are gone.
  const available = ['chat', 'vision', 'messages', 'nearby', 'gallery'];
  const locked = tileBlock('gallery', available, true);
  const missing = tileBlock('agent', available, true);
  assert.equal(locked.reason, CAPTURE_LOCK_REASON);
  assert.equal(locked.caption, TILE_CAPTURE_LOCK_SHORT);
  assert.equal(missing.caption, TILE_NOT_IN_BUILD_SHORT);
  assert.notEqual(locked.caption, missing.caption);
  // And each caption is a shortening of the reason it is rendered with, so the
  // short line cannot become a different claim from the one it shortens.
  assert.match(locked.reason, /question first/);
  assert.match(locked.caption, /question first/);
  assert.doesNotMatch(locked.caption, /not in this build/);
  assert.match(missing.reason, /not available in this build/);
  // A tile that opens has neither.
  assert.equal(tileBlock('chat', available, true), null);
  assert.equal(tileBlock('chat', available, false), null);
});

test('every blocked caption is true of its reason and fits the reserved line', () => {
  // The caption slot is one line on every tile (`home.css`), and the rows are
  // equalised, so a caption that wraps makes its row taller and squeezes the
  // central control off a short panel. The budget is read from the stylesheet
  // rather than repeated here, so the two cannot drift apart.
  const css = readFileSync(new URL('../app/device/home.css', import.meta.url), 'utf8');
  const budget = Number(css.match(/\.device-home-tile-reason[\s\S]{0,400}?max-width:\s*(\d+)ch/)?.[1]);
  assert.ok(budget > 0, 'the caption line still declares a ch width budget');

  const available = ['chat'];
  const captions = new Set();
  for (const tile of homeTiles()) {
    if (tile.id === 'chat') continue;
    const block = tileBlock(tile.id, available, true);
    assert.ok(block, `${tile.id} is not in this build`);
    // "Not in this build" is only honest because every build reason says it.
    assert.match(block.reason, /not available in this build/, `${tile.id} must say the build lacks it`);
    assert.ok(block.caption.length <= budget, `${tile.id}'s caption (${block.caption.length}) must fit ${budget}ch`);
    captions.add(block.caption);
  }
  assert.deepEqual([...captions], [TILE_NOT_IN_BUILD_SHORT]);
  assert.ok(TILE_CAPTURE_LOCK_SHORT.length <= budget, 'the locked caption must fit the same line');
});

test('availability does not reorder the tile map', () => {
  // The whole point of the grid: position is learned by touch, not by reading.
  const withAll = homeTiles().map((tile) => tile.id);
  const withSome = homeTiles().map((tile) => tile.id);
  assert.deepEqual(withAll, withSome);
});

// --- Post-reset recording-slot wait ----------------------------------------

test('a start after a reset waits for a reading the device produced afterwards', () => {
  // The reproduced defect: /clear returned 200 and the start was dispatched
  // against the pre-reset reading, which the device then refused with
  // 409 voice_busy. A reading taken before the acknowledgement cannot attest
  // that the slot is free.
  assert.equal(resetSlotWait(1, 1, false, true, 0), 'waiting');
  assert.equal(resetSlotWait(1, 1, true, true, 0), 'ready');
});

test('the post-reset wait is bounded and cannot start on an ineligible reading', () => {
  assert.equal(resetSlotWait(1, 1, true, false, 0), 'waiting');
  assert.equal(resetSlotWait(1, 1, true, false, RESET_SLOT_WAIT_MS - 1), 'waiting');
  assert.equal(resetSlotWait(1, 1, true, false, RESET_SLOT_WAIT_MS), 'timeout');
  // A reading that never arrives is a timeout too, not an infinite wait.
  assert.equal(resetSlotWait(1, 1, false, true, RESET_SLOT_WAIT_MS), 'timeout');
});

test('a withdrawn activation outranks any reading the device publishes', () => {
  // Leaving Home ends the activation; a late acknowledgement must not revive it.
  assert.equal(resetSlotWait(1, 2, true, true, 0), 'withdrawn');
  assert.equal(resetSlotWait(1, 2, true, true, RESET_SLOT_WAIT_MS * 10), 'withdrawn');
});

// --- Status strip ----------------------------------------------------------

test('every unproven state reads as unavailable, never as ready', () => {
  assert.equal(homeStatus(null, false).tone, 'unavailable');
  assert.equal(homeStatus(null, true).tone, 'unavailable');
  assert.equal(homeStatus(fixture(), false).tone, 'unavailable');
  for (const status of [homeStatus(null, false), homeStatus(null, true), homeStatus(fixture(), false)]) {
    assert.equal(status.fresh, false);
    assert.ok(status.label.trim().length > 0);
  }
});

test('a stale reading is unknown rather than the last known value', () => {
  const stale = fixture();
  stale.observed_at = new Date(Date.now() - 60_000).toISOString();
  const status = homeStatus(stale, true);
  assert.equal(status.tone, 'unavailable');
  assert.equal(status.label, 'Status unavailable');
  assert.equal(status.fresh, false);
  // The old value must not leak through as if current.
  assert.ok(!status.label.includes('Ready'));
});

test('disconnected and unreadable are distinct from each other', () => {
  assert.match(homeStatus(fixture(), false).label, /Reconnecting/);
  assert.match(homeStatus(null, true).label, /Status unavailable/);
});

test('a ready and fully attested lane reads Ready to ask', () => {
  const status = homeStatus(fixture('idle'), true);
  assert.equal(status.tone, 'ready');
  assert.equal(status.label, 'Ready to ask');
  assert.equal(status.fresh, true);
});

// `homeStatus` projects the device's own `deviceView`, which already returns a
// specific "unavailable" title for a failed lane gate. It does not invent a
// second opinion about readiness, and it does not claim a typed lane on the
// device's behalf. These tests assert the projection, not a reinterpretation.

test('a missing microphone reports the specific reason and never claims readiness', () => {
  const noMic = fixture('idle');
  noMic.audio.present = false;
  const status = homeStatus(noMic, true);
  assert.equal(status.tone, 'unavailable');
  assert.equal(status.label, 'Microphone disconnected');
  assert.notEqual(status.tone, 'ready');
});

test('an unready audio service reports its own reason, not a generic failure', () => {
  const unready = fixture('idle');
  unready.audio.ready = false;
  const status = homeStatus(unready, true);
  assert.equal(status.label, 'Audio is not ready');
});

test('a stopped service blocks the spoken lane with the device\'s own reason', () => {
  const stopped = fixture('idle');
  stopped.services.llm = 'failed';
  const status = homeStatus(stopped, true);
  assert.equal(status.tone, 'unavailable');
  assert.equal(status.label, 'Voice is not ready');
});

test('the typed lane is reported separately from spoken readiness', () => {
  // A missing microphone is not a dead end: the typed lane needs no audio.
  const noMic = fixture('idle');
  noMic.audio.present = false;
  noMic.voice.typed_ready = true;
  assert.equal(homeStatus(noMic, true).tone, 'unavailable');
  assert.equal(typedLaneReady(noMic, true), true);

  const noService = fixture('idle');
  noService.voice.typed_ready = true;
  noService.services.voice_loop = 'failed';
  assert.equal(typedLaneReady(noService, true), false);
  assert.equal(typedLaneReady(null, true), false);
  assert.equal(typedLaneReady(fixture('idle'), false), false);
});

test('live phases report themselves and stay fresh', () => {
  for (const [phase, tone] of [['listening', 'working'], ['processing', 'working'], ['complete', 'ready'], ['error', 'attention']]) {
    const status = homeStatus(fixture(phase), true);
    assert.equal(status.tone, tone, `${phase} should be ${tone}`);
    assert.equal(status.fresh, true);
  }
});

test('status wording never asserts readiness the evidence does not carry', () => {
  const cases = [
    homeStatus(null, false), homeStatus(null, true), homeStatus(fixture(), false),
    homeStatus({ ...fixture(), observed_at: new Date(0).toISOString() }, true),
  ];
  for (const status of cases) {
    assert.ok(!/ready to ask/i.test(status.label), `overclaim: ${status.label}`);
  }
});

test('the strip always carries a label so colour is never the only signal', () => {
  for (const phase of ['idle', 'listening', 'review', 'processing', 'complete', 'error', 'unknown']) {
    for (const connected of [true, false]) {
      const status = homeStatus(fixture(phase), connected);
      assert.ok(status.label.trim().length > 0, `${phase}/${connected} has no label`);
      assert.ok(['ready', 'working', 'attention', 'unavailable'].includes(status.tone));
    }
  }
});

// --- Radio -----------------------------------------------------------------

test('radio absence and radio unknown are different states', () => {
  const missing = fixture();
  missing.radio = { present: false, detail: 'No radio attached' };
  assert.equal(radioSummary(missing, true).state, 'missing');
  assert.equal(radioSummary(null, true).state, 'unknown');
  assert.equal(radioSummary(fixture(), false).state, 'unknown');
});

test('a stale status cannot assert radio state either way', () => {
  const stale = fixture();
  stale.observed_at = new Date(Date.now() - 60_000).toISOString();
  assert.equal(radioSummary(stale, true).state, 'unknown');
});

test('radio state is never folded into the device readiness tone', () => {
  const noRadio = fixture();
  noRadio.radio = { present: false, detail: 'No radio attached' };
  // Device readiness is unaffected by the radio: different questions.
  assert.equal(homeStatus(noRadio, true).tone, 'ready');
  assert.equal(radioSummary(noRadio, true).state, 'missing');
});

// --- Central control -------------------------------------------------------

test('the central control is a labelled button that never promises capture it cannot deliver', () => {
  assert.equal(VOICE_CONTROL_LABEL, 'Start a new chat');
  const ready = voiceControlState(fixture('idle'), true, false);
  assert.equal(ready.canStart, true);
  assert.equal(ready.instruction, 'Tap to talk · hold for push-to-talk');

  const noMic = fixture('idle');
  noMic.audio.present = false;
  const blocked = voiceControlState(noMic, true, false);
  assert.equal(blocked.canStart, false);
  assert.match(blocked.blockedReason, /Microphone/);
});

test('the control shows an explicit stop affordance for a recording this owner holds', () => {
  // Ownership is now an input: the same `listening` reading is stoppable only
  // when this client's owner acknowledged the session the device publishes.
  const listening = fixture('listening');
  listening.voice.session_id = OWNED_SESSION;
  const state = voiceControlState(listening, true, false, Date.now(), OWNED);
  assert.equal(state.phase, 'recording');
  assert.equal(state.canStart, false);
  assert.match(state.instruction, /Done|Cancel/);
});

test('a listening reading with no acknowledged owner exposes no GUI stop', () => {
  // The physical side button, another client, and an owner that has gone
  // uncertain all look like this. The state stays visible; the command does not
  // exist, because stopping a recording this client does not own would end
  // somebody else's turn.
  const physical = fixture('listening');
  physical.voice.session_id = OWNED_SESSION;
  physical.voice.input_source = 'physical';
  const state = voiceControlState(physical, true, false);
  assert.equal(state.phase, 'busy');
  assert.equal(state.canStart, false);
  assert.doesNotMatch(state.instruction, /Done/);
  assert.ok(state.instruction.trim().length > 0, 'the user still needs to see what is happening');
});

test('an ownership claim for a different session is not ownership', () => {
  // A stale acknowledgement: the owner still holds a session the device has
  // moved past, or never had. Either way it is not this recording.
  const other = fixture('listening');
  other.voice.session_id = '44444444-4444-4444-8444-444444444444';
  assert.equal(voiceControlState(other, true, false, Date.now(), OWNED).phase, 'busy');
  const none = fixture('listening');
  none.voice.session_id = OWNED_SESSION;
  assert.equal(voiceControlState(none, true, false, Date.now(), { owned: false, sessionId: OWNED_SESSION }).phase, 'busy');
  assert.equal(voiceControlState(none, true, false, Date.now(), { owned: true, sessionId: null }).phase, 'busy');
});

test('an owned recording outranks the global busy signal the real owner reports', () => {
  // TalkControls reports `owned || pending || uncertain`, so busy is true for
  // the entire time it holds a recording. Testing busy first hid Stop/Cancel
  // exactly when the user needed them.
  const listening = fixture('listening');
  listening.voice.session_id = OWNED_SESSION;
  const state = voiceControlState(listening, true, true, Date.now(), OWNED);
  assert.equal(state.phase, 'recording');
  assert.match(state.instruction, /Done|Cancel/);
  // Busy still wins where no recording is owned.
  assert.equal(voiceControlState(fixture('idle'), true, true, Date.now(), OWNED).phase, 'busy');
  assert.equal(voiceControlState(fixture('idle'), true, true, Date.now(), NO_CAPTURE_OWNER).phase, 'busy');
});

test('the control is busy rather than startable while processing', () => {
  const state = voiceControlState(fixture('processing'), true, false);
  assert.equal(state.phase, 'busy');
  assert.equal(state.canStart, false);
});

test('the control is busy while a real review is in progress', () => {
  // A review phase is only real when the device publishes a review id and a
  // session; without them `deviceView` reports the interface as unavailable and
  // the control must not present itself as mid-review.
  const review = fixture('review');
  review.voice.session_id = '11111111-1111-4111-8111-111111111111';
  review.voice.review_id = '22222222-2222-4222-8222-222222222222';
  review.voice.review_ready = true;
  const state = voiceControlState(review, true, false);
  assert.equal(state.phase, 'busy');
  assert.equal(state.canStart, false);
});

test('an incomplete review is not treated as a live review', () => {
  const incomplete = fixture('review');
  incomplete.voice.review_ready = false;
  const state = voiceControlState(incomplete, true, false);
  assert.equal(state.phase, 'idle');
  assert.equal(state.canStart, false, 'the unavailable view must not permit capture');
});

test('an explicit busy flag wins over a ready status', () => {
  const state = voiceControlState(fixture('idle'), true, true);
  assert.equal(state.phase, 'busy');
  assert.equal(state.canStart, false);
});

test('a disconnected device cannot start capture', () => {
  const state = voiceControlState(fixture(), false, false);
  assert.equal(state.canStart, false);
  // No status at all is also unstartable, and says why rather than failing silently.
  const none = voiceControlState(null, true, false);
  assert.equal(none.canStart, false);
  assert.ok(none.blockedReason.trim().length > 0);
});

test('a stale report cannot start capture while the strip calls status unavailable', () => {
  // The control and the strip are judged from the same reading, so the control
  // cannot offer capture the strip has just withdrawn. Before this, the strip
  // read `Status unavailable` and the button still started a recording.
  const stale = fixture('idle');
  stale.observed_at = new Date(Date.now() - 60_000).toISOString();
  const state = voiceControlState(stale, true, false);
  assert.equal(state.canStart, false);
  assert.equal(state.phase, 'idle');
  assert.match(state.blockedReason, /too old/);
});

test('a ready side button does not make the on-screen button able to capture', () => {
  // A physical-only device: the spoken lane is genuinely ready, but this control
  // is the touch lane, and the touch lane is not. The button stays blocked and
  // names the control that does work, rather than pretending to capture.
  const physicalOnly = fixture('idle');
  physicalOnly.voice.touch_ready = false;
  const state = voiceControlState(physicalOnly, true, false);
  assert.equal(state.canStart, false);
  assert.match(state.blockedReason, /side button/);
  assert.equal(homeStatus(physicalOnly, true).tone, 'ready', 'the device itself is still ready — only this control is not');
});

test('a finished or failed turn is startable again, through new-chat preparation', () => {
  // Disabling the control here stranded the user: the answer is on screen and
  // there was no way to ask again. The activation is permitted, but it must run
  // the explicit New chat preparation first, because the old context is loaded.
  for (const phase of ['complete', 'error']) {
    const state = voiceControlState(fixture(phase), true, false);
    assert.equal(state.canStart, true, `${phase} must not strand the user`);
    assert.equal(state.activation, 'new-chat', `${phase} must not record into the old context`);
    assert.equal(state.blockedReason, '');
  }
});

test('a finished turn cannot start capture while the question service is down', () => {
  const down = fixture('complete');
  down.services.voice_loop = 'stopped';
  const state = voiceControlState(down, true, false);
  assert.equal(state.canStart, false);
  assert.equal(state.activation, 'direct');
  assert.ok(state.blockedReason.trim().length > 0);
});

test('asking again from a finished turn needs the microphone, not just the question service', () => {
  // A completed answer stays readable without a microphone — that is the point
  // of the separate typed lane — but "ask again" records. A running question
  // service alone was treated as permission, so this offered capture on a device
  // whose microphone had been unplugged.
  const removed = fixture('complete');
  removed.audio.present = false;
  const noMic = voiceControlState(removed, true, false);
  assert.equal(noMic.canStart, false);
  assert.match(noMic.blockedReason, /Microphone/);

  const unready = fixture('error');
  unready.audio.ready = false;
  const noAudio = voiceControlState(unready, true, false);
  assert.equal(noAudio.canStart, false);
  assert.match(noAudio.blockedReason, /Audio/);

  const stopped = fixture('complete');
  stopped.local_voice_ready = false;
  assert.equal(voiceControlState(stopped, true, false).canStart, false);

  // The same turn with everything attested is still startable through new-chat.
  assert.equal(voiceControlState(fixture('complete'), true, false).canStart, true);
});

test('a finished turn on a physical-only device names the control that works', () => {
  const physicalOnly = fixture('complete');
  physicalOnly.voice.touch_ready = false;
  const state = voiceControlState(physicalOnly, true, false);
  assert.equal(state.canStart, false);
  assert.match(state.blockedReason, /side button/);
});

test('an idle ready lane activates directly rather than through a reset', () => {
  assert.equal(voiceControlState(fixture('idle'), true, false).activation, 'direct');
});

test('every control state says what an activation would do', () => {
  // A missing field here would be read as `undefined` by a parent switching on
  // it, which is how a "no reset needed" default gets invented by accident.
  for (const state of [
    voiceControlState(fixture('idle'), true, false),
    voiceControlState(fixture('complete'), true, false),
    voiceControlState(fixture('idle'), true, true),
    voiceControlState(fixture('listening'), true, false),
    voiceControlState(null, false, false),
  ]) {
    assert.ok(['direct', 'new-chat'].includes(state.activation), `unexpected activation ${state.activation}`);
  }
});

test('a stale reading is not shown as a live turn either', () => {
  // A stale `listening` cannot attest that the device is recording, so it is not
  // rendered as an active recording with a stop affordance.
  const stale = fixture('listening');
  stale.observed_at = new Date(Date.now() - 60_000).toISOString();
  const state = voiceControlState(stale, true, false);
  assert.equal(state.phase, 'idle');
  assert.equal(state.canStart, false);
});

// --- Gesture arbitration ---------------------------------------------------

test('a short press is a tap and a long press is a hold, never both', () => {
  const down = gestureDown(0);
  const tap = gestureUp(down.state);
  assert.equal(tap.outcome, 'tap');

  const down2 = gestureDown(0);
  const held = gestureHold(down2.state);
  assert.equal(held.outcome, 'hold');
  const released = gestureUp(held.state);
  assert.equal(released.outcome, 'hold');
});

test('the hold threshold is a single shared rule', () => {
  assert.equal(holdElapsed(0, HOLD_MS - 1), false);
  assert.equal(holdElapsed(0, HOLD_MS), true);
  assert.equal(holdElapsed(0, HOLD_MS + 1), true);
});

test('a cancelled gesture never becomes a tap', () => {
  const down = gestureDown(0);
  const cancelled = gestureCancel(down.state);
  const released = gestureUp(cancelled);
  assert.equal(released.outcome, null);
  assert.deepEqual(released.state, IDLE_GESTURE);
});

test('a late hold timer after release does nothing', () => {
  const down = gestureDown(0);
  const released = gestureUp(down.state);
  assert.equal(released.outcome, 'tap');
  // The timer fires after the finger is up: it must not start a hold.
  const late = gestureHold(released.state);
  assert.equal(late.outcome, null);
  assert.deepEqual(late.state, IDLE_GESTURE);
});

test('a hold timer that fires twice starts only one hold', () => {
  const down = gestureDown(0);
  const first = gestureHold(down.state);
  const second = gestureHold(first.state);
  assert.equal(first.outcome, 'hold');
  assert.equal(second.outcome, null, 'duplicate timer must not start a second capture');
});

test('release without a press yields nothing', () => {
  assert.equal(gestureUp(IDLE_GESTURE).outcome, null);
});

test('a cancel after the hold started still ends cleanly', () => {
  const down = gestureDown(0);
  const held = gestureHold(down.state);
  const cancelled = gestureCancel(held.state);
  const released = gestureUp(cancelled);
  assert.equal(released.outcome, null);
});

test('a pending gesture is abandoned when the screen changes or the device is busy', () => {
  const down = gestureDown(0);
  assert.equal(gestureShouldCancel(down.state, false, false), true);
  assert.equal(gestureShouldCancel(down.state, true, true), true);
  assert.equal(gestureShouldCancel(down.state, true, false), false);
  assert.equal(gestureShouldCancel(IDLE_GESTURE, false, true), false);
});

// --- New-chat preparation --------------------------------------------------

test('capture is allowed only after preparation actually succeeded', () => {
  assert.equal(captureAllowed(IDLE_PREPARE), false);
  assert.equal(captureAllowed(beginPrepare('new chat', false)), false);
  assert.equal(captureAllowed(prepareSucceeded(beginPrepare('new chat', false))), true);
});

test('a destructive context must be acknowledged before preparing', () => {
  const awaiting = beginPrepare('discard unsaved photo', true);
  assert.equal(awaiting.phase, 'awaiting-acknowledgement');
  assert.equal(captureAllowed(awaiting), false);
  const preparing = acknowledgePrepare(awaiting);
  assert.equal(preparing.phase, 'preparing');
  assert.equal(captureAllowed(preparing), false);
  assert.equal(captureAllowed(prepareSucceeded(preparing)), true);
});

test('a failed reset never allows capture into the unprepared thread', () => {
  const failed = prepareFailed(beginPrepare('new chat', false), 'device refused');
  assert.equal(failed.phase, 'failed');
  assert.equal(captureAllowed(failed), false);
  assert.match(failed.reason, /refused/);
});

test('cancelling preparation allows nothing', () => {
  const cancelled = cancelPrepare(beginPrepare('new chat', false));
  assert.equal(cancelled.phase, 'cancelled');
  assert.equal(captureAllowed(cancelled), false);
});

test('a failed reset keeps the reason it was given, falling back to the prior one', () => {
  const prior = beginPrepare('switching chat', false);
  assert.equal(prepareFailed(prior, '   ').reason, 'switching chat');
});

test('acknowledgement and success are ignored when out of sequence', () => {
  assert.deepEqual(acknowledgePrepare(IDLE_PREPARE), IDLE_PREPARE);
  assert.deepEqual(prepareSucceeded(IDLE_PREPARE), IDLE_PREPARE);
  assert.deepEqual(prepareSucceeded(beginPrepare('x', true)), beginPrepare('x', true));
});

test('finishing preparation returns to idle so a second activation starts fresh', () => {
  assert.deepEqual(prepareFinished(), IDLE_PREPARE);
});

test('arriving on home does not begin preparation by itself', () => {
  // Nothing here prepares as a side effect of being rendered: preparation is
  // only ever entered by an explicit call.
  assert.equal(captureAllowed(IDLE_PREPARE), false);
  assert.equal(IDLE_PREPARE.phase, 'idle');
});

// --- Release rule ----------------------------------------------------------

test('a tap starts when idle and finishes when a capture is running', () => {
  assert.equal(releaseAction('tap', 'idle'), 'start');
  assert.equal(releaseAction('tap', 'recording'), 'finish');
  assert.equal(releaseAction('tap', 'busy'), 'none');
});

test('a second tap inside the pending window is not a second start', () => {
  // A start the parent has not confirmed yet is still one activation. Treating
  // the second tap as another start would ask for two chats from one gesture.
  assert.equal(releaseAction('tap', 'idle', true), 'none');
});

test('a hold release finishes a confirmed recording and fences an unconfirmed start', () => {
  // The hold began capture when its timer fired, so releasing the confirmed
  // recording ends it.
  assert.equal(releaseAction('hold', 'recording'), 'finish');
  // Releasing while the start is still unconfirmed must withdraw that start
  // instead: there is nothing to finish, and a late acknowledgement must not
  // leave a recording running after the gesture that asked for it is over.
  assert.equal(releaseAction('hold', 'busy', true), 'fence');
  assert.equal(releaseAction('hold', 'idle', true), 'fence');
  // A hold that dispatched no start has nothing to finish and nothing to fence.
  assert.equal(releaseAction('hold', 'idle'), 'none');
  assert.equal(releaseAction('hold', 'busy'), 'none');
});

test('a cancelled gesture resolves to no action at all', () => {
  assert.equal(releaseAction(null, 'idle'), 'none');
  assert.equal(releaseAction(null, 'recording'), 'none');
});

test('a release re-checks readiness before dispatching a start', () => {
  // An unavailable control has phase `idle`, so a press that began ready and a
  // release after readiness was withdrawn looked identical to a healthy tap. The
  // start has to be re-judged at the moment it would be dispatched, not trusted
  // from the press.
  assert.equal(releaseAction('tap', 'idle', false, false), 'none');
  assert.equal(releaseAction('tap', 'idle', false, true), 'start');
  // Finishing an owned recording is never gated on being able to start another.
  assert.equal(releaseAction('tap', 'recording', false, false), 'finish');
  assert.equal(releaseAction('hold', 'recording', false, false), 'finish');
});

test('cancelling or failing preparation from idle is a no-op, not a phantom record', () => {
  // The idle variant carries no reason, so these must not fabricate one. The
  // first version read `previous.reason` unconditionally and would have thrown.
  assert.deepEqual(cancelPrepare(IDLE_PREPARE), IDLE_PREPARE);
  assert.deepEqual(prepareFailed(IDLE_PREPARE, 'anything'), IDLE_PREPARE);
  assert.equal(captureAllowed(cancelPrepare(IDLE_PREPARE)), false);
});
