'use client';

/**
 * Lantern device home: the menu and voice surface.
 *
 * Presentational by design. This component **owns no capture**. It reports what
 * the user did and renders what the parent tells it, because the brief requires
 * exactly one TalkControls/capture-ownership lifetime across Home and Chat, and
 * a second recording owner is the bug class that rule exists to prevent. The
 * parent decides what "start a new chat" means and drives the existing capture
 * owner; this file never touches the microphone.
 *
 * The status strip is derived by `homeStatus` from validated device evidence, so
 * it cannot report readiness the device has not attested.
 *
 * Gestures cross a pending window: a start may be dispatched while the parent is
 * still preparing (an asynchronous New chat reset). This component therefore
 * tracks whether *this* gesture has an unconfirmed start, so a release inside
 * that window withdraws it instead of finishing a recording that does not exist,
 * and so a cancel that arrives from elsewhere tells the parent to abandon it.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  HOLD_MS, NO_CAPTURE_OWNER, VOICE_CONTROL_LABEL, gestureCancel, gestureDown,
  gestureHold, gestureShouldCancel, gestureUp, homeStatus, homeTiles, radioSummary, releaseAction, routeFor,
  tileBlock, tileCommand, voiceControlState,
  type CaptureOwnership, type GestureState, type HomeDestination, type HomeFocusTarget,
} from '../../lib/home-navigation.ts';
import { IDLE_GESTURE } from '../../lib/home-navigation.ts';
import type { DeviceStatus } from '../../lib/device-state.ts';
import { CameraIcon, EnvelopeIcon, NetworkIcon } from './icons';
import './home.css';

/**
 * What the parent has done with the activation this screen asked for.
 *
 * The component owns the *gesture*; the parent owns the *activation* — the New
 * chat preparation, the reset outcome and the capture it may or may not have
 * started. Reporting it back is what lets a second press cleanly supersede a
 * finished attempt without ever superseding one that is still in flight.
 */
export type HomeActivation = {
  /** An activation is still being resolved; a new one must not replace it. */
  pending: boolean;
  /** Why the last activation ended without capture. Shown, not swallowed. */
  error?: string;
};

export type HomeProps = {
  status: DeviceStatus | null;
  connected: boolean;
  /** A question turn is live, or the device is otherwise occupied. */
  busy: boolean;
  /** Destinations this build can actually open. */
  available: readonly HomeDestination[];
  /** Short answer text for in-place feedback; empty when there is none. */
  answerText?: string;
  questionText?: string;
  /** True while response text is still arriving, so the UI can mark it live. */
  streaming?: boolean;
  /** A previous turn ended without completing, so the text is partial. */
  incomplete?: boolean;
  /** What the single capture owner holds, so a stop is only offered for our own. */
  captureOwnership?: CaptureOwnership;
  /**
   * True while opening a destination other than Chat would destroy the current
   * turn: every other destination unmounts the single capture owner, whose own
   * cleanup cancels the session it holds. Presentation only — the parent's
   * dispatch applies the same policy, because a disabled button is not a guard.
   */
  locked?: boolean;
  /** The parent's view of the activation it was asked to run. */
  activation?: HomeActivation;
  /**
   * Move focus back to the control the user left from, so Home→Settings→Home
   * does not drop the user at the top of the document. The parent bumps `nonce`
   * on every return, so coming back to the same control twice still moves focus.
   */
  focusRequest?: { target: HomeFocusTarget; nonce: number } | null;
  /** Explicit start of a new chat: the parent runs its New chat policy. */
  onStartNewChat: () => void;
  /** Explicit finish of an in-progress capture. */
  onFinishCapture: () => void;
  /** Abandon a cancelled gesture, or a start whose gesture is already over. */
  onCancelCapture: () => void;
  onOpenChat: () => void;
  onOpenSettings: () => void;
  onOpenTile: (destination: HomeDestination) => void;
  /** Injectable for tests and the fixture; defaults to Date.now. */
  now?: () => number;
};

export default function Home({
  status, connected, busy, available, answerText = '', questionText = '', streaming = false,
  incomplete = false, captureOwnership = NO_CAPTURE_OWNER, activation = { pending: false },
  locked = false, focusRequest = null,
  onStartNewChat, onFinishCapture, onCancelCapture, onOpenChat, onOpenSettings,
  onOpenTile, now = Date.now,
}: HomeProps) {
  const [, setGesture] = useState<GestureState>(IDLE_GESTURE);
  const holdTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const gestureRef = useRef<GestureState>(IDLE_GESTURE);
  /**
   * True when a gesture has asked the parent to start a chat and capture has not
   * been confirmed yet. Scoped to the gesture: cleared when a new press begins,
   * when capture is confirmed, and when the start is finished or withdrawn.
   */
  const startPending = useRef(false);
  /**
   * The pointer that owns the current gesture.
   *
   * A second finger landing on the control is ignored as a new activation, but
   * its *release* must be ignored too: without this, lifting the second finger
   * released the first gesture and finished a recording the user was still
   * holding. The contract calls this out as "active pointer identity".
   */
  const activePointer = useRef<number | null>(null);
  /** Tile buttons, so a return from a shortcut can restore focus where it left. */
  const tileRefs = useRef(new Map<HomeDestination, HTMLButtonElement>());
  /** The Settings gear, which is a focus target but not a tile. */
  const settingsRef = useRef<HTMLButtonElement>(null);

  const statusStrip = useMemo(() => homeStatus(status, connected, now()), [status, connected, now]);
  const radio = useMemo(() => radioSummary(status, connected, now()), [status, connected, now]);
  const control = useMemo(
    () => voiceControlState(status, connected, busy, now(), captureOwnership),
    [status, connected, busy, now, captureOwnership],
  );
  const tiles = homeTiles();

  // The hold timer and the release handlers run outside the render that created
  // them, so they read the committed control rather than a stale closure.
  const controlRef = useRef(control);
  const activationRef = useRef(activation);
  useEffect(() => {
    controlRef.current = control;
    activationRef.current = activation;
    // Capture is running, so nothing this component asked for is pending.
    if (control.phase === 'recording') startPending.current = false;
  });

  const clearHold = useCallback(() => {
    if (holdTimer.current !== null) { clearTimeout(holdTimer.current); holdTimer.current = null; }
  }, []);

  const apply = useCallback((next: GestureState) => {
    gestureRef.current = next;
    setGesture(next);
  }, []);

  // Back from a Home shortcut puts focus back on the control that opened it. The
  // parent drives this rather than Home tracking its own last press, because the
  // destination screen is what knows the user is returning.
  useEffect(() => {
    if (!focusRequest) return;
    const { target } = focusRequest;
    const element = target.kind === 'settings' ? settingsRef.current : tileRefs.current.get(target.destination);
    element?.focus({ preventScroll: true });
  }, [focusRequest]);

  const finishCapture = useCallback(() => { clearHold(); apply(IDLE_GESTURE); onFinishCapture(); }, [apply, clearHold, onFinishCapture]);
  const cancelCapture = useCallback(() => {
    clearHold();
    apply(IDLE_GESTURE);
    // A pending start and an accepted recording are both cancelled from here,
    // and neither may outlive the button press that ended them.
    startPending.current = false;
    onCancelCapture();
  }, [apply, clearHold, onCancelCapture]);

  // A pending gesture must not survive the device becoming busy: releasing
  // afterwards would otherwise act on a screen the user left.
  useEffect(() => {
    if (!gestureShouldCancel(gestureRef.current, true, busy)) return;
    // ...unless it already asked for a start. The parent reports busy *because*
    // it is starting the capture this gesture asked for, so erasing the gesture
    // here would strand a recording with no release left to stop it. The start
    // intent is preserved and its release decides: finish a confirmed recording,
    // or fence a start that never became one.
    if (startPending.current) return;
    clearHold();
    apply(gestureCancel(gestureRef.current));
  }, [busy, clearHold, apply]);

  useEffect(() => clearHold, [clearHold]);

  const settleRelease = useCallback((outcome: ReturnType<typeof gestureUp>['outcome']) => {
    // The start-or-finish rule is decided by the tested pure helper rather than
    // inline here, so a duplicate start cannot be introduced by an edit. The
    // current `canStart` is passed rather than the one the press began with:
    // readiness can be lost between a pointer going down and coming up, and an
    // unavailable control is still phase `idle`, so the press alone cannot tell
    // the two apart.
    const current = controlRef.current;
    switch (releaseAction(outcome, current.phase, startPending.current, current.canStart)) {
      case 'start':
        startPending.current = true;
        onStartNewChat();
        return;
      case 'finish':
        startPending.current = false;
        finishCapture();
        return;
      case 'fence':
        // Withdraw the start this gesture asked for: capture never began, so
        // there is nothing to finish.
        startPending.current = false;
        onCancelCapture();
        return;
      default:
        return;
    }
  }, [finishCapture, onCancelCapture, onStartNewChat]);

  const pointerDown = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    // One code path for mouse, touch and pen. Pointer capture keeps the release
    // coming to this button even if the finger leaves it, and a cancelled
    // gesture must not become a tap.
    //
    // Default handling is deliberately *not* prevented here: suppressing it also
    // suppresses focus, and the compatibility click it would suppress is
    // rejected in `click` by its device detail count instead.
    //
    // A press that arrives while a gesture is already down is a second pointer
    // (another finger, or a pen beside a finger), not a new activation. It must
    // not replace the intent already in flight.
    if (gestureRef.current.active) return;
    const current = controlRef.current;
    const recording = current.phase === 'recording';
    // A press is accepted while recording too — that is the second tap that
    // finishes capture. Gating this on `canStart` alone made the stop
    // affordance unreachable, because `canStart` is false *because* a capture
    // is running.
    if (!current.canStart && !recording) return;
    event.currentTarget.setPointerCapture?.(event.pointerId);
    activePointer.current = event.pointerId;
    // This gesture owns whatever start it dispatches — unless the parent is
    // still resolving the previous activation, in which case clearing the flag
    // here would let this release dispatch a second start behind it.
    if (!activationRef.current.pending) startPending.current = false;
    apply(gestureDown(now()).state);
    if (recording) return;
    holdTimer.current = setTimeout(() => {
      holdTimer.current = null;
      const latest = controlRef.current;
      // A press that began as a start must not become a second start if the
      // device moved on while the finger was still down.
      if (!latest.canStart || latest.phase === 'recording') return;
      const held = gestureHold(gestureRef.current);
      apply(held.state);
      if (held.outcome === 'hold') {
        startPending.current = true;
        onStartNewChat();
      }
    }, HOLD_MS);
  }, [apply, now, onStartNewChat]);

  const pointerUp = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    // Only the pointer that owns this gesture may end it. A second finger's
    // release is not this gesture's release.
    if (!gestureRef.current.active || event.pointerId !== activePointer.current) return;
    activePointer.current = null;
    const released = gestureUp(gestureRef.current);
    clearHold();
    apply(released.state);
    settleRelease(released.outcome);
  }, [apply, clearHold, settleRelease]);

  // Pointer capture lost while a press is still pending means the gesture was
  // cancelled. It must not become a tap.
  const pointerCancel = useCallback((event: React.PointerEvent<HTMLButtonElement>) => {
    if (!gestureRef.current.active || event.pointerId !== activePointer.current) return;
    activePointer.current = null;
    event.preventDefault();
    clearHold();
    apply(gestureCancel(gestureRef.current));
    /**
     * Withdraw a start this press asked for, and nothing else.
     *
     * The cancelled gesture must not become a tap, which the registration above
     * already prevents. It must also not tear down a recording an earlier,
     * completed gesture opened: a second press carries no start of its own —
     * `startPending` is false while a recording is live — yet this called
     * `onCancelCapture()` unconditionally, which cancels the accepted session.
     * Measured: a cancelled second press took the screen from `listening` to
     * `idle` and dropped the owned session, discarding audio the user had
     * already recorded. A recording that survives keeps its explicit stop and
     * cancel affordances, so this is the recoverable outcome.
     */
    if (!startPending.current) return;
    startPending.current = false;
    onCancelCapture();
  }, [apply, clearHold, onCancelCapture]);

  /** One activation, whatever produced it: tap, Enter/Space, or assistive click. */
  const activate = useCallback(() => {
    const current = controlRef.current;
    if (!current.canStart && current.phase !== 'recording') return;
    settleRelease('tap');
  }, [settleRelease]);

  const keyDown = useCallback((event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== 'Enter' && event.key !== ' ') return;
    // Preventing the default also suppresses the browser's own activation click.
    event.preventDefault();
    // Auto-repeat is not a hold gesture: holding the key down must not produce a
    // second activation, and a hold is only ever a pointer gesture.
    if (event.repeat) return;
    activate();
  }, [activate]);

  const click = useCallback((event: React.MouseEvent<HTMLButtonElement>) => {
    // A pointer press already acted on pointerdown/pointerup, and its
    // compatibility click carries a device detail count, so it must not become a
    // second activation. Assistive technology synthesises a click with no
    // pointer gesture behind it (detail 0): that one *is* the user's single
    // activation, and without it the control is unreachable by screen reader.
    if (event.detail !== 0) return;
    activate();
  }, [activate]);

  const recording = control.phase === 'recording';
  const controlLabel = recording ? 'Finish and review your question' : VOICE_CONTROL_LABEL;

  return <section className="device-home" aria-label="Lantern home">
    <button
      ref={settingsRef}
      type="button"
      className="device-home-settings"
      onClick={onOpenSettings}
      disabled={busy}
      aria-label="Settings"
      title="Settings"
    >
      <SettingsGlyph />
    </button>

    <div className={`device-home-status device-home-status-${statusStrip.tone}`} role="status">
      <span className="device-home-status-dot" aria-hidden="true" />
      <span className="device-home-status-label">{statusStrip.label}</span>
      {statusStrip.detail && <span className="device-home-status-detail">{statusStrip.detail}</span>}
      <span className="device-home-radio" data-state={radio.state}>{radio.label}</span>
    </div>

    <div className="device-home-identity">
      <h1 className="device-home-title">SAGE Lantern</h1>
    </div>

    <div className="device-home-stage">
      <div className="device-home-voice">
        <button
          type="button"
          className={`device-home-robot${recording ? ' device-home-robot-recording' : ''}`}
          data-activation={control.activation}
          aria-label={controlLabel}
          aria-pressed={recording}
          disabled={!control.canStart && !recording}
          onPointerDown={pointerDown}
          onPointerUp={pointerUp}
          onPointerCancel={pointerCancel}
          onLostPointerCapture={pointerCancel}
          onKeyDown={keyDown}
          onClick={click}
        >
          <RobotGlyph recording={recording} />
        </button>
        <p className="device-home-instruction">{control.instruction}</p>
        {activation.pending && !recording && <p className="device-home-blocked" role="status">Preparing a new chat…</p>}
        {control.blockedReason && <p className="device-home-blocked" role="status">{control.blockedReason}</p>}
        {activation.error && <p className="device-home-blocked" role="status">{activation.error}</p>}
        {/* Cancel is offered while a start is still being prepared as well as
            while a recording is running: during preparation there is nothing to
            stop, but there is a start to withdraw, and leaving the user with no
            way to withdraw it is what the integrated review reproduced. */}
        {(recording || activation.pending) && <button
          type="button"
          className="device-home-cancel"
          data-cancel-scope={recording ? 'recording' : 'preparing'}
          aria-label={recording ? 'Cancel this recording' : 'Cancel starting a new chat'}
          onClick={cancelCapture}
        >Cancel</button>}
      </div>

      {(answerText || questionText) && <div className="device-home-feedback" aria-live="polite">
        {questionText && <p className="device-home-question">You asked · “{questionText}”</p>}
        {answerText && <p className="device-home-answer">
          {answerText}
          {streaming && <span className="device-home-caret" aria-hidden="true">▍</span>}
          {incomplete && !streaming && <span className="device-home-incomplete">Incomplete</span>}
        </p>}
        <button type="button" className="device-home-open-chat" onClick={onOpenChat}>Open chat</button>
      </div>}
    </div>

    <nav className="device-home-tiles" aria-label="Home">
      {tiles.map((tile) => {
        const block = tileBlock(tile.id, available, locked);
        const command = tileCommand(tile.id, available, locked);
        const route = routeFor(tile.id);
        return <button
          key={tile.id}
          ref={(element) => {
            // React calls this with null on unmount; the map must not keep a
            // detached node, or returning to Home would focus nothing.
            if (element) tileRefs.current.set(tile.id, element);
            else tileRefs.current.delete(tile.id);
          }}
          type="button"
          className="device-home-tile"
          data-destination={tile.id}
          data-route={route.kind}
          disabled={command.kind === 'none'}
          aria-label={block ? `${tile.accessibleLabel}. ${block.reason}` : tile.accessibleLabel}
          title={block?.reason ?? tile.accessibleLabel}
          onClick={() => { if (command.kind === 'open') onOpenTile(command.destination); }}
        >
          <TileGlyph destination={tile.id} />
          <span className="device-home-tile-label">{tile.label}</span>
          {/* The caption slot exists on every tile, not only the unavailable
              ones. A reason that appeared on two tiles and not the others made
              those rows taller, and because the rows are equalised the whole map
              grew and squeezed the central control off a short panel. The caption
              is the shortened form of *this* tile's own reason, so a locked tile
              and a destination this build does not have cannot read alike. */}
          <span className="device-home-tile-reason">{block?.caption ?? ''}</span>
        </button>;
      })}
    </nav>
  </section>;
}

function SettingsGlyph() {
  return <svg viewBox="0 0 24 24" width="32" height="32" fill="none" stroke="currentColor" strokeWidth="1.8"
    strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <circle cx="12" cy="12" r="3.2" />
    <path d="M12 3v2.2M12 18.8V21M3 12h2.2M18.8 12H21M5.6 5.6l1.6 1.6M16.8 16.8l1.6 1.6M18.4 5.6l-1.6 1.6M7.2 16.8l-1.6 1.6" />
  </svg>;
}

function RobotGlyph({ recording }: { recording: boolean }) {
  // Lantern linework: strokes only, no sprite, no hard offset shadow, no arcade
  // type. Motion is a single pulse that respects reduced motion via CSS.
  return <svg className="device-home-robot-glyph" viewBox="0 0 96 96" width="96" height="96"
    fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="24" y="30" width="48" height="40" rx="12" />
    <circle cx="39" cy="48" r="4.5" fill="currentColor" stroke="none" />
    <circle cx="57" cy="48" r="4.5" fill="currentColor" stroke="none" />
    <path d="M42 60h12" />
    <path d="M48 30V22" />
    <circle cx="48" cy="18" r="4" />
    {recording
      ? <path d="M30 78h36" />
      : <path d="M36 78h24" />}
  </svg>;
}

function TileGlyph({ destination }: { destination: HomeDestination }) {
  if (destination === 'vision') return <CameraIcon />;
  if (destination === 'messages') return <EnvelopeIcon />;
  if (destination === 'nearby') return <NetworkIcon />;
  if (destination === 'chat') return <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 6h16v10H9l-5 4z" /></svg>;
  if (destination === 'agent') return <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 7h14M5 12h14M5 17h9" /><circle cx="18" cy="17" r="2" /></svg>;
  return <svg viewBox="0 0 24 24" width="34" height="34" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="5" width="18" height="14" rx="3" /><circle cx="9" cy="10" r="1.8" /><path d="M4 17l5-4 4 3 3-2 4 3" /></svg>;
}
