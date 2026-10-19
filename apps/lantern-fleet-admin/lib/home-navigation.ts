/**
 * Home navigation and status derivation for the Lantern device screen.
 *
 * Pure functions only: no React, no fetch, no timers, no device access. The home
 * component renders what these return, so tile order, destination mapping and
 * status wording can be tested without a browser and argued about without a
 * device.
 *
 * Two rules drive everything here, both from the implementation brief:
 *
 *  1. Status comes from **validated evidence**. A tile, a fresh HTTP response or
 *     a static string cannot establish readiness. A reading older than the
 *     freshness window is *unknown*, not *ready*, and missing hardware is a
 *     different state from unknown hardware.
 *  2. Feature positions are **stable**. The tile map does not reorder when
 *     readiness changes, because a user who cannot read navigates by position.
 */
import {
  deviceView, questionServiceRunning, statusIsFresh, voiceLaneNotice,
  type DeviceStatus, type DeviceView,
} from './device-state.ts';

export type HomeDestination = 'chat' | 'vision' | 'agent' | 'gallery' | 'messages' | 'nearby';

/**
 * A Home control that can be focused when the user returns to Home.
 *
 * The Settings gear is a Home control but not a Home *destination*: it has no
 * tile, `routeFor` gives it no route, and `tileCommand` has nothing to say about
 * it. Adding it to `HomeDestination` would widen every tile-only path with a
 * value those paths cannot render, so it is a separate variant here and the gear
 * is focused through its own ref.
 */
export type HomeFocusTarget = { kind: 'tile'; destination: HomeDestination } | { kind: 'settings' };

export type HomeTile = {
  /** Stable key; also the row-major position. */
  id: HomeDestination;
  /** Visible label. Short, and always present — never an icon alone. */
  label: string;
  /** Spoken/assistive label, longer than the visible one. */
  accessibleLabel: string;
};

/**
 * Row-major tile order, fixed: two columns, three rows —
 *
 *   Chat    | Vision
 *   Agent   | Gallery
 *   Messages| Nearby
 *
 * The first four keep the reference positions, so the positional map the user
 * already learned does not move when the owner's later expansion added the last
 * two. Exported as a constant rather than derived so a reorder is a visible
 * one-line diff in review rather than an accident of a sort.
 */
export const HOME_TILES: readonly HomeTile[] = Object.freeze([
  { id: 'chat', label: 'Chat', accessibleLabel: 'Chat — open your conversations' },
  { id: 'vision', label: 'Vision', accessibleLabel: 'Vision — open the camera' },
  { id: 'agent', label: 'Agent', accessibleLabel: 'Agent — open tasks and schedules' },
  { id: 'gallery', label: 'Gallery', accessibleLabel: 'Gallery — open your saved photos' },
  { id: 'messages', label: 'Messages', accessibleLabel: 'Messages — open conversations with people' },
  { id: 'nearby', label: 'Nearby', accessibleLabel: 'Nearby — open the radio overview' },
]);

export function homeTiles(): readonly HomeTile[] {
  return HOME_TILES;
}

export function tileFor(id: HomeDestination): HomeTile {
  const tile = HOME_TILES.find((entry) => entry.id === id);
  if (!tile) throw new Error(`unknown home destination: ${id}`);
  return tile;
}

/** The screen a tile opens. Kept beside the tiles so the two cannot drift. */
export type HomeRoute = { kind: 'existing'; page: string } | { kind: 'candidate'; component: string };

const ROUTES: Record<HomeDestination, HomeRoute> = Object.freeze({
  chat: { kind: 'existing', page: 'chat' },
  vision: { kind: 'existing', page: 'camera' },
  // Messages and Nearby are existing screens reached from Home by the owner's
  // expansion: the existing inbox/conversations page and the existing radio
  // overview. They are *routes*, not new surfaces — opening one sends no
  // message, starts no dictation, acknowledges no receipt and writes no radio.
  messages: { kind: 'existing', page: 'messages' },
  nearby: { kind: 'existing', page: 'status' },
  // Gallery is delivered in this increment: the retained-photo destination, kept
  // separate from Camera, which shows a live view and says a fresh snapshot is
  // not saved.
  gallery: { kind: 'existing', page: 'gallery' },
  // `agent` is a 2c deliverable. Until it exists the tile is still rendered in
  // its stable position and reports itself unavailable rather than being dropped
  // or silently opening something else.
  agent: { kind: 'candidate', component: 'agent' },
});

export function routeFor(id: HomeDestination): HomeRoute {
  return ROUTES[id];
}

/**
 * Which tiles can actually be opened in this build.
 *
 * A tile absent from `available` is rendered **disabled with a visible reason**,
 * never hidden: hiding it would move the other tiles and break the positional
 * map that is the whole point of the grid.
 */
/**
 * Short visible wording for a tile this build cannot open.
 *
 * The brief allows short wording on the tile itself **provided** the specific
 * reason still travels with it, so the tile's accessible label and title carry
 * `unavailableReason` verbatim and only the visible line is shortened. With six
 * tiles in three rows, a full sentence on one tile makes that row taller than
 * the others and pushes the central control off the panel; a vague badge with
 * the detail dropped would be the dishonest version of the same fix.
 */
export const TILE_NOT_IN_BUILD_SHORT = 'Not in this build';

/** Shown on a destination that exists but is unsafe to open right now. */
export const CAPTURE_LOCK_REASON = 'Finish the current question first.';

/**
 * The visible line for a locked tile: a shortening of *that* reason.
 *
 * Two blocked tiles must not read the same when they are blocked for different
 * reasons. "Unavailable" on a locked Gallery reads as "photos are not on this
 * device", which is the opposite of what is true and contradicts the reason the
 * same control carries. Both captions stay inside the tile's one reserved line
 * (`home.css` gives it `max-width: 24ch`), because a caption that wraps makes its
 * row taller and the rows are equalised.
 */
export const TILE_CAPTURE_LOCK_SHORT = 'Finish question first';

/**
 * Whether leaving Home for this destination can destroy the current turn.
 *
 * Every destination except Chat is rendered *outside* the subtree that holds the
 * single `TalkControls` owner, so opening one unmounts that owner — and its own
 * unmount cleanup cancels the session it holds. Chat is the one destination that
 * keeps the owner mounted, which is exactly why Home↔Chat viewing preserves an
 * accepted recording and nothing else does.
 *
 * This is a property of the composition, not a policy preference, so it lives
 * here where it can be tested rather than being an implicit consequence of which
 * JSX branch a destination happens to use.
 */
export function destinationKeepsCapture(id: HomeDestination): boolean {
  return id === 'chat';
}

/** Why this destination is not in this build. Every one of these says "in this build". */
function buildReason(id: HomeDestination): string {
  if (id === 'agent') return 'Tasks and schedules are not available in this build.';
  if (id === 'gallery') return 'Saved photos are not available in this build.';
  if (id === 'messages') return 'Messages are not available in this build.';
  if (id === 'nearby') return 'The radio overview is not available in this build.';
  return 'This screen is not available in this build.';
}

/**
 * A blocked tile's claim: the full reason, and the shortened form of that same
 * claim the tile shows.
 *
 * They are produced together so a tile cannot render the short line for a reason
 * it does not have. `reason` is what the accessible name and the tooltip carry;
 * `caption` is what a touch panel can actually read, since a `title` needs a
 * pointer and this shell has none. `null` means the tile opens.
 */
export type TileBlock = { reason: string; caption: string };

export function tileBlock(id: HomeDestination, available: readonly HomeDestination[], locked = false): TileBlock | null {
  if (!available.includes(id)) return { reason: buildReason(id), caption: TILE_NOT_IN_BUILD_SHORT };
  // Route availability and "safe to open right now" are different questions: an
  // idle, disconnected Messages route is usable, one openable in the middle of a
  // recording is not.
  if (locked && !destinationKeepsCapture(id)) return { reason: CAPTURE_LOCK_REASON, caption: TILE_CAPTURE_LOCK_SHORT };
  return null;
}

export function unavailableReason(id: HomeDestination, available: readonly HomeDestination[], locked = false): string | null {
  return tileBlock(id, available, locked)?.reason ?? null;
}

// --- Status strip ----------------------------------------------------------

export type HomeStatusTone = 'ready' | 'working' | 'attention' | 'unavailable';

export type HomeStatus = {
  tone: HomeStatusTone;
  /** Short, always visible, never colour-only. */
  label: string;
  /** One supporting line. Empty when the label is enough. */
  detail: string;
  /** True only when a reading was validated as current. */
  fresh: boolean;
};

/**
 * Derive the status strip from validated evidence only.
 *
 * This is a **projection of `deviceView`**, which is the device's own
 * authoritative readiness model: it already distinguishes a live session from an
 * idle one, requires the spoken lane's gate on the idle path, and reports a
 * specific reason rather than a generic failure. Rewriting that judgement here
 * would create a second, weaker opinion about readiness — exactly what the brief
 * forbids — so the only things this function adds are:
 *
 *  - the connection and freshness gates, which `deviceView` cannot see; and
 *  - the wording the strip needs.
 *
 * Anything unproven reads as `unavailable`, never as `ready`.
 */
export function homeStatus(status: DeviceStatus | null, connected: boolean, now = Date.now()): HomeStatus {
  if (!connected) {
    return { tone: 'unavailable', label: 'Reconnecting…', detail: 'No connection to the device service.', fresh: false };
  }
  if (!status) {
    return { tone: 'unavailable', label: 'Status unavailable', detail: 'Waiting for the device to report.', fresh: false };
  }
  if (!statusIsFresh(status, now)) {
    // A stale reading is exactly the false-readiness case the brief forbids:
    // the last known value is not shown as if it were current.
    return { tone: 'unavailable', label: 'Status unavailable', detail: 'The last report is too old to trust.', fresh: false };
  }

  const view: DeviceView = deviceView(status, connected);

  switch (view.phase) {
    // `deviceView` returns `ready` only when the idle path's own gate passed,
    // so this needs no second opinion. It is still checked rather than assumed,
    // because the gate is the device's and could change independently.
    case 'ready':
      return { tone: 'ready', label: 'Ready to ask', detail: '', fresh: true };
    case 'complete':
      return { tone: 'ready', label: view.title, detail: view.hint, fresh: true };
    case 'listening':
    case 'review':
    case 'processing':
      return { tone: 'working', label: view.title, detail: view.hint, fresh: true };
    case 'error':
      return { tone: 'attention', label: view.title, detail: view.hint, fresh: true };
    default:
      // Includes the specific "unavailable" reasons the device reports, such as
      // a disconnected microphone or a service that is not running. They are
      // shown as the reason, not smoothed into one grey state.
      return { tone: 'unavailable', label: view.title, detail: view.hint, fresh: true };
  }
}

/**
 * The typed lane is offered by the device independently of the microphone, so a
 * missing microphone is not a dead end. Reported separately from readiness
 * rather than folded into it.
 */
export function typedLaneReady(status: DeviceStatus | null, connected: boolean, now = Date.now()): boolean {
  if (!connected || !status || !statusIsFresh(status, now)) return false;
  return status.voice.typed_ready === true && status.services.voice_loop === 'active';
}

/**
 * Radio state, kept **separate** from device readiness.
 *
 * The brief requires the two not to be conflated, and requires missing hardware
 * and unknown hardware to be different states rather than one grey bucket.
 */
export type RadioSummary = { state: 'present' | 'missing' | 'unknown'; label: string; detail: string };

export function radioSummary(status: DeviceStatus | null, connected: boolean, now = Date.now()): RadioSummary {
  if (!connected || !status || !statusIsFresh(status, now)) {
    return { state: 'unknown', label: 'Radio unknown', detail: 'Radio state cannot be confirmed.' };
  }
  if (!status.radio.present) {
    return { state: 'missing', label: 'No radio', detail: status.radio.detail || 'No radio hardware is attached.' };
  }
  const name = status.radio.name?.trim();
  return { state: 'present', label: name || 'Radio', detail: status.radio.detail || '' };
}

// --- Central voice control -------------------------------------------------

/**
 * The central control's visual state.
 *
 * `voiceLaneNotice` decides whether the microphone may start; the control never
 * promises capture the lane cannot deliver.
 */
export type VoiceControlState = {
  /** `idle` shows the robot; `recording` shows an explicit stop affordance. */
  phase: 'idle' | 'recording' | 'busy';
  /** Visible instruction under the control. */
  instruction: string;
  /** True when a spoken turn may be started at all. */
  canStart: boolean;
  /** What an activation must do before capture may begin. */
  activation: VoiceActivation;
  /** Why not, when `canStart` is false. Shown, not hidden. */
  blockedReason: string;
};

/**
 * How a permitted activation reaches capture.
 *
 * `direct` — the idle lane is ready and the parent may start capture.
 * `new-chat` — a finished or failed turn is still loaded. The activation is
 * *permitted*, but only through an explicit, successful New chat preparation:
 * recording into a thread whose context has not reset is the wrong-thread bug
 * the brief names. Disabling the control instead would strand the user with no
 * way to ask again from this screen.
 */
export type VoiceActivation = 'direct' | 'new-chat';

export const VOICE_CONTROL_LABEL = 'Start a new chat';
export const VOICE_CONTROL_INSTRUCTION = 'Tap to talk \u00b7 hold for push-to-talk';

/** The one reason used when only the physical side button can capture. */
const PHYSICAL_ONLY_REASON = 'Hold the side button to talk.';

/**
 * What the local capture owner is holding, as reported by that owner.
 *
 * The contract requires ownership to be *established*, not inferred: a status
 * report saying `listening` — even with `input_source=touch` — is not evidence
 * that this client may stop the recording. A physical side-button recording, a
 * recording from another client, and an owner that has gone uncertain all look
 * identical from a status poll, and issuing stop/cancel into one of them would
 * end somebody else's turn. So the parent passes what its own owner acknowledged,
 * and `voiceControlState` additionally requires the device to publish that same
 * session before it will offer a stop affordance.
 */
export type CaptureOwnership = {
  /** The local owner accepted a session and has not released it. */
  owned: boolean;
  /** The session that owner accepted, or null when it holds none. */
  sessionId: string | null;
};

/** No owner has acknowledged a session; nothing may be stopped from here. */
export const NO_CAPTURE_OWNER: CaptureOwnership = Object.freeze({ owned: false, sessionId: null });

/**
 * True when the local owner holds the session the device is currently
 * publishing. Both halves are required: an acknowledgement the device has moved
 * past is a stale owner, and a published session no owner acknowledged belongs
 * to somebody else.
 */
function ownsPublishedSession(ownership: CaptureOwnership, status: DeviceStatus, connected: boolean): boolean {
  return connected && ownership.owned === true && !!ownership.sessionId
    && status.voice.session_id === ownership.sessionId;
}

/** True when this button — the touch lane — is the control that can capture. */
function touchLaneCanCapture(status: DeviceStatus): boolean {
  return status.voice.touch_ready === true && voiceLaneNotice(status) === null;
}

/**
 * Why the touch lane cannot capture, in the device's own words when it has them.
 *
 * A clear lane notice means *some* control can capture. When that control is the
 * physical side button rather than this one, naming it is more useful than
 * reporting the lane as broken.
 */
function touchLaneBlockedReason(status: DeviceStatus, laneReason: string): string {
  if (status.voice.touch_ready === true) return laneReason;
  return voiceLaneNotice(status) === null ? PHYSICAL_ONLY_REASON : laneReason;
}

/**
 * Derive the central control from validated evidence only.
 *
 * Four gates, all of them fail-closed:
 *
 *  1. **Ownership before busy.** The real owner reports busy for the whole time
 *     it holds a recording (`owned || pending || uncertain`), so testing `busy`
 *     first hid the Stop/Cancel affordance exactly when it was needed. A
 *     confirmed owned recording now outranks the busy flag; everything else that
 *     is busy stays busy.
 *  2. **Ownership is established, not inferred.** `listening` with a matching
 *     owned session shows the stop affordance. `listening` without one is shown
 *     as busy with no GUI stop: it may be the physical side button, another
 *     client, or an owner that has gone uncertain, and none of those can be
 *     stopped from this control.
 *  3. **Freshness.** A reading older than the freshness window is unknown, not
 *     ready, and a stale `listening` is not rendered as a live recording either.
 *  4. **The touch lane's own readiness, at the moment of activation.** This
 *     button *is* the touch lane, so a ready physical side button does not make
 *     it able to capture, and neither does a running question service: replaying
 *     a finished answer needs the microphone too, which is why `complete`/`error`
 *     no longer gate on `questionServiceRunning` alone.
 */
export function voiceControlState(
  status: DeviceStatus | null, connected: boolean, busy: boolean, now = Date.now(),
  ownership: CaptureOwnership = NO_CAPTURE_OWNER,
): VoiceControlState {
  // A live turn is shown from a current reading only. A stale one cannot attest
  // that the device is mid-turn.
  const live = connected && status && statusIsFresh(status, now) ? deviceView(status, connected) : null;

  if (live?.phase === 'listening') {
    if (status && ownsPublishedSession(ownership, status, connected)) {
      return { phase: 'recording', instruction: 'Tap Done when finished, or Cancel.', canStart: false, activation: 'direct', blockedReason: '' };
    }
    return { phase: 'busy', instruction: live.hint || live.title, canStart: false, activation: 'direct', blockedReason: '' };
  }

  if (busy) {
    return { phase: 'busy', instruction: 'Working…', canStart: false, activation: 'direct', blockedReason: '' };
  }

  if (live && (live.phase === 'review' || live.phase === 'processing')) {
    return { phase: 'busy', instruction: live.hint || live.title, canStart: false, activation: 'direct', blockedReason: '' };
  }

  const blocked = (reason: string): VoiceControlState => ({
    phase: 'idle', instruction: VOICE_CONTROL_INSTRUCTION, canStart: false, activation: 'direct', blockedReason: reason,
  });

  if (!connected) return blocked('No connection to the device service.');
  if (!status) return blocked('Waiting for the device to report.');
  if (!statusIsFresh(status, now)) return blocked('The last device report is too old to trust.');

  const view = deviceView(status, connected);
  const laneReason = voiceLaneNotice(status) ?? view.hint ?? view.title;

  // A finished or failed turn is a legitimate place to ask again, and the local
  // question service is what has to reset the context first. That service
  // running is *not* enough on its own: asking again records, so the microphone
  // and the touch lane have to be ready too. The finished answer stays on screen
  // either way — it does not depend on microphone health.
  if (view.phase === 'complete' || view.phase === 'error') {
    if (!questionServiceRunning(status)) return blocked(laneReason);
    if (!touchLaneCanCapture(status)) return blocked(touchLaneBlockedReason(status, laneReason));
    return { phase: 'idle', instruction: VOICE_CONTROL_INSTRUCTION, canStart: true, activation: 'new-chat', blockedReason: '' };
  }

  if (view.phase !== 'ready') return blocked(laneReason);
  if (!touchLaneCanCapture(status)) return blocked(touchLaneBlockedReason(status, laneReason));
  return { phase: 'idle', instruction: VOICE_CONTROL_INSTRUCTION, canStart: true, activation: 'direct', blockedReason: '' };
}

// --- Gesture arbitration ---------------------------------------------------

/**
 * A press resolves to a tap or a hold, never both.
 *
 * Pure so the arbitration can be tested: the component supplies timestamps and
 * applies the answer. `HOLD_MS` is the threshold at which a press becomes
 * push-to-talk.
 */
export const HOLD_MS = 400;

export type GestureOutcome = 'tap' | 'hold';

export type GestureState = {
  /** Pressure is down. */
  active: boolean;
  /** The hold threshold already fired for this press. */
  holdFired: boolean;
  /** Set when the gesture was cancelled and must not become a tap. */
  cancelled: boolean;
};

export const IDLE_GESTURE: GestureState = Object.freeze({ active: false, holdFired: false, cancelled: false });

export function gestureDown(now: number): { state: GestureState; at: number } {
  return { state: { active: true, holdFired: false, cancelled: false }, at: now };
}

/**
 * Called when the hold timer fires. Returns the new state and whether the press
 * has become a hold. A cancel or a release always wins over a late timer, which
 * is how a cancelled gesture is prevented from becoming a tap.
 */
export function gestureHold(state: GestureState): { state: GestureState; outcome: GestureOutcome | null } {
  if (!state.active || state.cancelled || state.holdFired) return { state, outcome: null };
  return { state: { ...state, holdFired: true }, outcome: 'hold' };
}

export function gestureCancel(state: GestureState): GestureState {
  return { ...state, active: false, cancelled: true };
}

/**
 * Release. A cancelled press yields nothing; a press past the threshold yields
 * `hold` (the hold already fired, so this ends capture); an untouched press
 * yields `tap`.
 */
export function gestureUp(state: GestureState): { state: GestureState; outcome: GestureOutcome | null } {
  if (!state.active) return { state: IDLE_GESTURE, outcome: null };
  if (state.cancelled) return { state: IDLE_GESTURE, outcome: null };
  if (state.holdFired) return { state: IDLE_GESTURE, outcome: 'hold' };
  return { state: IDLE_GESTURE, outcome: 'tap' };
}

/**
 * True when a hold should be started for a press that has been down this long.
 * Kept as a function so the component's timer and the tests agree on the rule
 * rather than duplicating the comparison.
 */
export function holdElapsed(startedAt: number, now: number): boolean {
  return now - startedAt >= HOLD_MS;
}

// --- New-chat preparation --------------------------------------------------

/**
 * Starting a new chat is an *intentional* activation that may need a
 * destructive-context acknowledgement first. The brief is explicit that a
 * failed reset must not record into the wrong thread, so the sequence is
 * modelled rather than left to the component's control flow.
 */
export type PrepareState =
  | { phase: 'idle' }
  | { phase: 'awaiting-acknowledgement'; reason: string }
  | { phase: 'preparing'; reason: string }
  | { phase: 'ready'; reason: string }
  | { phase: 'failed'; reason: string }
  | { phase: 'cancelled'; reason: string };

export const IDLE_PREPARE: PrepareState = Object.freeze({ phase: 'idle' });

/** Whether the activation may begin capture. Only `ready` may. */
export function captureAllowed(prepare: PrepareState): boolean {
  return prepare.phase === 'ready';
}

export function beginPrepare(reason: string, needsAcknowledgement: boolean): PrepareState {
  return needsAcknowledgement
    ? { phase: 'awaiting-acknowledgement', reason }
    : { phase: 'preparing', reason };
}

export function acknowledgePrepare(previous: PrepareState): PrepareState {
  if (previous.phase !== 'awaiting-acknowledgement') return previous;
  return { phase: 'preparing', reason: previous.reason };
}

export function prepareSucceeded(previous: PrepareState): PrepareState {
  if (previous.phase !== 'preparing') return previous;
  return { phase: 'ready', reason: previous.reason };
}

/**
 * The reset failed. Capture stays disallowed: recording into a thread whose
 * context did not reset is the wrong-thread bug the brief names.
 */
export function prepareFailed(previous: PrepareState, detail: string): PrepareState {
  // `idle` carries no reason, and a failure that never began is not a failure.
  if (previous.phase === 'idle') return IDLE_PREPARE;
  const reason = detail.trim() || previous.reason;
  return { phase: 'failed', reason };
}

export function cancelPrepare(previous: PrepareState): PrepareState {
  // Cancelling something that never started is a no-op rather than a synthetic
  // "cancelled" record the UI would then have to explain.
  if (previous.phase === 'idle') return IDLE_PREPARE;
  return { phase: 'cancelled', reason: previous.reason };
}

/** After capture has been handed to the capture owner, the sequence is done. */
export function prepareFinished(): PrepareState {
  return IDLE_PREPARE;
}

// --- Post-reset recording-slot wait ----------------------------------------

/**
 * How long a preparation waits for the device to publish a reading that shows
 * the recording slot free again.
 *
 * This is a **bounded** wait, not a retry loop: the device acknowledges `/clear`
 * with `{"status": "clearing"}` and only releases the recording slot when its
 * worker finishes, so the HTTP acknowledgement is not proof that a new capture
 * may begin. Several status polls fit inside this budget; past it the
 * preparation gives up and says so rather than starting blindly.
 */
export const RESET_SLOT_WAIT_MS = 4000;

/** What a preparation may conclude about the post-reset recording slot. */
export type SlotWaitOutcome = 'ready' | 'waiting' | 'timeout' | 'withdrawn';

/**
 * Decide whether a preparation may start capture after its own reset.
 *
 * Kept pure so the ordering rule can be tested without a device: the request is
 * refused until a reading the device produced **after** the reset was
 * acknowledged says the slot is free. A reading that predates the
 * acknowledgement cannot attest to it — that was the reproduced defect where
 * `/clear` returned 200 and the start was dispatched against the pre-reset
 * screen.
 *
 * `withdrawn` outranks everything: once the activation token has been bumped,
 * the gesture that asked for this capture is over and no reading can revive it.
 */
export function resetSlotWait(
  token: number, currentToken: number, readingAfterReset: boolean, canStart: boolean,
  elapsedMs: number, budgetMs: number = RESET_SLOT_WAIT_MS,
): SlotWaitOutcome {
  if (token !== currentToken) return 'withdrawn';
  if (readingAfterReset && canStart) return 'ready';
  if (elapsedMs >= budgetMs) return 'timeout';
  return 'waiting';
}

// --- Nav ------------------------------------------------------------------

export type NavCommand =
  | { kind: 'open'; destination: HomeDestination }
  | { kind: 'open-chat' }
  | { kind: 'settings' }
  | { kind: 'none' };

/**
 * Resolve a tile press into a navigation command.
 *
 * An unavailable tile resolves to `none` rather than to a fallback screen: the
 * brief forbids substituting one destination for another. A destination that is
 * *available but unsafe right now* resolves to `none` for the same reason — the
 * press must not reach the parent's dispatch and unmount the capture owner.
 *
 * The check exists here as well as in the component because a guard that only
 * disables a button is not a guard: a synthetic click, a stale render or a
 * keyboard activation can all still call the handler.
 */
export function tileCommand(id: HomeDestination, available: readonly HomeDestination[], locked = false): NavCommand {
  if (!available.includes(id)) return { kind: 'none' };
  if (locked && !destinationKeepsCapture(id)) return { kind: 'none' };
  return { kind: 'open', destination: id };
}

/**
 * What a release should do, given whether a capture is already running.
 *
 * Kept pure and tested because getting it wrong is a double-start: a hold that
 * already began capture, followed by a release that begins another one.
 */
export type ReleaseAction = 'start' | 'finish' | 'fence' | 'none';

/**
 * What a release should do, given whether a capture is already running,
 * whether this gesture has a start still in flight, and whether capture is
 * *still* permitted.
 *
 * `startPending` is true once the gesture has asked the parent to start a chat
 * and capture has not been confirmed (the parent may still be running an
 * asynchronous New chat preparation). A release inside that window must `fence`
 * — withdraw the pending start — rather than `finish`: there is no recording to
 * stop yet, and a late acknowledgement must not leave one running after the
 * gesture that asked for it is over.
 *
 * `canStart` is re-read at release rather than trusted from the press. Readiness
 * can be lost between a pointer going down and coming up, and an unavailable
 * control has phase `idle`, so a press that began ready would otherwise dispatch
 * a start into a microphone the device has since withdrawn.
 */
export function releaseAction(
  outcome: GestureOutcome | null, phase: 'idle' | 'recording' | 'busy', startPending = false, canStart = true,
): ReleaseAction {
  if (outcome === null) return 'none';
  if (outcome === 'hold') {
    // The hold began capture when its timer fired; releasing the confirmed
    // recording ends it.
    if (phase === 'recording') return 'finish';
    return startPending ? 'fence' : 'none';
  }
  // A tap starts a capture when idle, and finishes the one that is running.
  if (phase === 'recording') return 'finish';
  // A second tap inside the pending window is not a second start: one activation,
  // one start, until the parent resolves it.
  if (phase === 'idle') return startPending ? 'none' : (canStart ? 'start' : 'none');
  return 'none';
}

/** True when a pending gesture must be abandoned because the screen changed. */
export function gestureShouldCancel(state: GestureState, stillOnHome: boolean, busy: boolean): boolean {
  return state.active && (!stillOnHome || busy);
}
