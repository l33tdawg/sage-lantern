'use client';

import { useEffect, useEffectEvent, useImperativeHandle, useRef, useState, type Ref } from 'react';
import { confirmTouchSession, parseTouchSession, replySessionContext, voiceCommand, VoiceControlError, type TouchSession } from '../../lib/voice-control';
import { deviceView, typedLaneAvailable, voiceLaneNotice, type DeviceStatus } from '../../lib/device-state';
import ChatComposer from './chat-composer';
import { CancelIcon, KeyboardIcon, MicrophoneIcon, SendIcon } from './icons';
import type { PhotoAttachment } from '../../lib/photo-preview';
import { parseDeviceHistory } from '../../lib/device-history';
import {
  SESSION_CONTEXT_UNAVAILABLE, getDeviceToken, sessionContextEqual, type SessionContext,
} from '../../lib/device-session';

/**
 * What this capture owner is holding, reported to the parent that renders both
 * the Home and Chat presentations of it.
 *
 * `owned` is the only field that may be treated as permission to stop a
 * recording: it is set from an accepted session this owner still holds, and
 * cleared the moment the outcome of a command is unknown. `sessionId` is the
 * session that owner holds even while the outcome is being reconciled, so the
 * parent can match it against the session the device publishes.
 */
export type TalkOwnership = {
  owned: boolean;
  pending: boolean;
  uncertain: boolean;
  sessionId: string | null;
};

/** What a requested capture actually did. */
export type TalkStartOutcome = 'started' | 'failed' | 'refused' | 'withdrawn' | 'not-started';

/**
 * What an *active* activation is told when the runtime did not start its
 * recording.
 *
 * "Tap to try again" is the affordance the user actually has, on both Home and
 * Chat. The wording deliberately claims nothing about why: neither a 409 on the
 * confirmation nor a context mismatch is evidence that somebody else took the
 * device over, and saying so would be a diagnosis the UI cannot make.
 */
export const START_NOT_STARTED_NOTICE = 'The recording did not start. Tap to try again.';

/** A recording whose epoch moved underneath it was stopped, and says so. */
export const RECORDING_SESSION_CHANGED_NOTICE = 'The device session changed, so the recording stopped.';

/**
 * A typed question whose authority moved before it was admitted opens nothing.
 *
 * It claims no more than that: the review was not adopted, and the affordance the
 * user still has is the Type control.
 */
export const TYPED_SESSION_CHANGED_NOTICE = 'The device session changed. Tap Type to try again.';

/**
 * The explicit interface the parent drives the single capture owner through.
 *
 * Home renders a different control than Chat, but there is still exactly one
 * owner: both presentations call these methods rather than each growing their
 * own recording state. `cancel` withdraws an unacknowledged start as well as
 * cancelling an accepted one — a preparation that has already been abandoned
 * must not leave a recording running behind it.
 */
export type TalkController = {
  /**
   * Start a capture, optionally admitting the session the device returns.
   *
   * `admit` is the caller's chance to judge the session *before* this owner
   * publishes it as its own. It exists because the caller may hold evidence this
   * component cannot see — a status reading that names a different session — and
   * because the alternative is worse in a way that is easy to miss: adopting
   * first and cancelling on the next poll means the session really was owned, a
   * heartbeat really was scheduled, and the screen really did show a recording
   * for that window. Refusing admission cancels the returned session with its own
   * credentials and never takes ownership at all.
   */
  start: (admit?: (candidate: TouchSession) => boolean) => Promise<TalkStartOutcome>;
  finish: (action: 'stop' | 'cancel') => void;
  cancel: () => void;
  /**
   * Withdraw a start that has not been acknowledged yet, **without** touching a
   * recording that was already accepted.
   *
   * The two are different operations and the UI has to be able to tell them
   * apart: leaving Home during preparation must withdraw the start the user
   * walked away from, while an accepted recording must survive the user merely
   * looking at Chat. `cancel` does both; this does only the first.
   */
  withdrawStart: () => void;
};

export default function TalkControls({ status, connected, compact = false, composer = false, onBusyChange, label = 'Ask', photo, autoStart, onAutoStartHandled, onStarted, onCancelled, cancelLabel = 'Cancel', onTypedSession, controllerRef, onOwnership, onKeyboardContainer }: { status: DeviceStatus | null; connected: boolean; compact?: boolean; composer?: boolean; onBusyChange?: (busy: boolean) => void; label?: string; photo?: PhotoAttachment; autoStart?: string; onAutoStartHandled?: () => void; onStarted?: (sessionId: string, photo?: PhotoAttachment) => void; onCancelled?: () => void; cancelLabel?: string; onTypedSession?: (sessionId: string, photo?: PhotoAttachment) => void; controllerRef?: Ref<TalkController>; onOwnership?: (ownership: TalkOwnership) => void; onKeyboardContainer?: (element: HTMLDivElement | null) => void }) {
  const [owned, setOwned] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [uncertain, setUncertain] = useState(false);
  const [typedPending, setTypedPending] = useState(false);
  const [typedError, setTypedError] = useState('');
  const [ownedSession, setOwnedSession] = useState<string | null>(null);
  const session = useRef<TouchSession | null>(null);
  const heartbeat = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const mounted = useRef(false);
  const busy = useRef(false);
  const recordingActive = useRef(false);
  const consumedStart = useRef<string | undefined>(undefined);
  /**
   * The latest reading, and the authority the current capture was issued under.
   *
   * The authority is captured before the first protected await and compared at
   * every later protected step — admission, renewal, the stopped capture, and the
   * rendering that follows. It is epoch- and token-bound, so it survives every
   * voice publication and changes only on a real rotation.
   */
  const statusRef = useRef<DeviceStatus | null>(status);
  const admittedContext = useRef<SessionContext | null>(null);
  /**
   * Activation token. Bumped by every requested start and by every cancel, so a
   * start that is still in flight when its activation ends can tell that it has
   * been withdrawn and cancel the session it is about to be handed rather than
   * adopting it.
   */
  const intent = useRef(0);

  // The async paths that outlive a render read the reading through this ref.
  useEffect(() => { statusRef.current = status; });
  useEffect(() => {
    onBusyChange?.(owned || pending || uncertain);
    return () => onBusyChange?.(false);
  }, [owned, pending, uncertain, onBusyChange]);
  useEffect(() => {
    onOwnership?.({ owned, pending, uncertain, sessionId: ownedSession });
  }, [owned, pending, uncertain, ownedSession, onOwnership]);
  useEffect(() => {
    if (!connected || !session.current || status?.voice.session_id !== session.current.session_id || !['review', 'processing', 'complete', 'idle', 'error'].includes(status.voice.phase)) return;
    session.current = null;
    recordingActive.current = false;
    clearTimeout(heartbeat.current);
    setOwned(false); setUncertain(false); setOwnedSession(null);
  }, [status, connected]);
  useEffect(() => {
    if (!uncertain) return;
    const expiry = setTimeout(() => { session.current = null; setUncertain(false); setOwnedSession(null); }, 6000);
    return () => clearTimeout(expiry);
  }, [uncertain]);

  useEffect(() => {
    mounted.current = true;
    const abandon = () => {
      // Withdraw a start that has not been acknowledged yet. Without this, a
      // hide/show that lands before the acknowledgement would let the late
      // session be adopted by an owner the user has already left.
      intent.current += 1;
      recordingActive.current = false;
      const current = session.current;
      session.current = null;
      admittedContext.current = null;
      clearTimeout(heartbeat.current);
      if (current) void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
    };
    const hide = () => {
      if (document.hidden) { abandon(); setOwned(false); setOwnedSession(null); }
    };
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', hide); abandon(); };
  }, []);

  /**
   * What the device is publishing as its context right now.
   *
   * `undefined` means it cannot be verified — no reading yet, or a malformed one
   * — and every comparison refuses. `null` is a device publishing none at all,
   * the legacy unlocked mode, and only another `null` matches it.
   */
  const publishedContext = (): SessionContext | null | undefined => {
    const reading = statusRef.current;
    if (!reading) return undefined;
    return reading.session_context === 'invalid' ? undefined : (reading.session_context ?? null);
  };

  /** A reply is ours only when it agrees with the device's current context. */
  const contextMatches = (fromReply: SessionContext | null): boolean => {
    const published = publishedContext();
    return published !== undefined && sessionContextEqual(published, fromReply);
  };

  const keepRecording = (current: TouchSession, delay = 1000) => {
    heartbeat.current = setTimeout(async () => {
      if (session.current !== current || !recordingActive.current) return;
      try {
        const kept = await voiceCommand('keepalive', { session_id: current.session_id, token: current.token });
        // A renewed lease is a protected reply: it has to come back under the
        // context this capture was admitted under, and that context has to still
        // be the one the device publishes. Either disagreement means the epoch
        // moved, and holding the session would be holding something void.
        const renewedUnder = replySessionContext(kept);
        if (!sessionContextEqual(renewedUnder, admittedContext.current) || !contextMatches(renewedUnder)) {
          throw new Error(SESSION_CONTEXT_UNAVAILABLE);
        }
        if (session.current === current && recordingActive.current) keepRecording(current);
      } catch (failure) {
        if (session.current !== current || !recordingActive.current) return;
        recordingActive.current = false;
        void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
        const epochMoved = failure instanceof Error && failure.message === SESSION_CONTEXT_UNAVAILABLE;
        if (!mounted.current) { session.current = null; return; }
        setOwned(false); setOwnedSession(null);
        if (epochMoved) { session.current = null; setUncertain(false); setError(RECORDING_SESSION_CHANGED_NOTICE); }
        else { setUncertain(true); setError('Connection lost. Checking recording status.'); }
      }
    }, delay);
  };

  const start = async (admit?: (candidate: TouchSession) => boolean): Promise<TalkStartOutcome> => {
    if (busy.current || session.current) return 'refused';
    const token = ++intent.current;
    const deviceToken = getDeviceToken();
    const active = () => mounted.current && !document.hidden && token === intent.current && getDeviceToken() === deviceToken;
    /**
     * Refuse a start the runtime did not admit.
     *
     * A withdrawn activation stays silent: the user has already left, and a
     * message about a recording nobody is waiting for is noise. An active one is
     * told the recording did not start, because a silent refusal is what makes a
     * tap look like nothing happened.
     *
     * "Already left" is narrower than `active()` being false. `active()` also
     * compares the device token it captured, so a re-key underneath a user who
     * is still standing there is indistinguishable from an abandoned gesture.
     * Left as a silent withdrawal, a capture whose confirmation came back 409
     * after the authority moved told the user nothing at all - no notice and no
     * stop affordance - while they believed they were recording. When the only
     * thing that moved is the authority, the surface says the one thing that is
     * true either way: the recording did not start.
     */
    const refuseStart = (): TalkStartOutcome => {
      if (!active()) {
        const abandoned = !mounted.current || token !== intent.current;
        const authorityMoved = mounted.current && !abandoned && getDeviceToken() !== deviceToken;
        if (!authorityMoved) return 'withdrawn';
        setError(START_NOT_STARTED_NOTICE);
        return 'not-started';
      }
      if (mounted.current) setError(START_NOT_STARTED_NOTICE);
      return 'not-started';
    };
    busy.current = true;
    setPending(true);
    setError('');
    /**
     * The authority this activation is issued under, captured before the first
     * protected await.
     *
     * It is deliberately *not* read out of the reply that answers the command. A
     * rotation that lands while the command is in flight is admitted by the
     * runtime under the new epoch, so the delayed reply carries it and so does
     * every reading after it: reply and reading agree with each other and
     * disagree only with the authority the user actually activated. Taking the
     * reply's own context as the expectation would make the reply its own
     * witness, and the fence would rest on some other object noticing the
     * rotation first.
     */
    const issuedUnder = publishedContext();
    try {
      // An authority that cannot be compared is not the same thing as no
      // authority, and it opens no recording.
      if (issuedUnder === undefined) return refuseStart();
      /**
       * Whether the original photo may travel with this question.
       *
       * The policy behind that decision is read out of a protected reply, so the
       * reply is judged before any of it is read. Comparing only the *current*
       * reading is not enough: a read admitted under a newer epoch answers with
       * that epoch's own policy, so a rotation this client's status poll has not
       * caught up with would hand this activation a policy it was never admitted
       * under — and the original bytes would already be on the wire before the
       * start reply was refused for carrying the same new context.
       *
       * The read can also outlive the authority it was issued under, which is why
       * the reply has to agree with the remembered expectation *and* with what the
       * device publishes now.
       */
      let retainOriginal = false;
      if (photo) {
        const listing = await voiceCommand('history/list', {});
        // The third door of the same class, on the photo path: the read can
        // outlive the authority it was issued under, and a bare 'withdrawn' here
        // told a user who is still there nothing at all. Its neighbours below
        // already refuse through `refuseStart`, which is silent only when the
        // activation is genuinely over.
        if (!active()) return refuseStart();
        let listedUnder: SessionContext | null;
        try { listedUnder = replySessionContext(listing); }
        catch { return refuseStart(); }
        if (!sessionContextEqual(listedUnder, issuedUnder) || !contextMatches(listedUnder)) return refuseStart();
        const history = parseDeviceHistory(listing);
        retainOriginal = history.photo_saving_supported === true && history.enabled && history.photos_enabled === true;
      }
      const requestId = crypto.randomUUID();
      const started = await voiceCommand('start', { request_id: requestId, ...(photo ? { image_base64: photo.image, ...(retainOriginal ? { original_base64: photo.preview } : {}) } : {}) });
      const current = parseTouchSession(started);
      const cancelReturned = () => { void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {}); };
      /**
       * The reply is judged against the authority captured before dispatch, and
       * that authority must still be the one the device publishes.
       *
       * Both halves are required and neither is inferable from the other: the
       * first catches a rotation that happened while this command was in flight —
       * where the reply agreeing with the latest reading is precisely the trap,
       * because both of them moved together — and the second catches one the
       * status poll has already seen. A reply whose context cannot be read opens
       * no recording either: the credentials it returned are cancelled with the
       * session's own token, which is the difference between refusing a start and
       * adopting one and unwinding later.
       */
      let repliedUnder: SessionContext | null;
      try { repliedUnder = replySessionContext(started); }
      catch { cancelReturned(); return refuseStart(); }
      if (!sessionContextEqual(repliedUnder, issuedUnder) || !contextMatches(repliedUnder)) { cancelReturned(); return refuseStart(); }
      // The caller may have observed a different session while this command was
      // pending. Judge that here — before ownership, before the heartbeat, before
      // `onStarted` — rather than adopting and unwinding on the next poll.
      const admitted = () => {
        if (!active()) return false;
        try { return admit?.(current) ?? true; } catch { return false; }
      };
      if (!admitted()) {
        // The activation that asked for this capture is over — withdrawn,
        // navigated away from, hidden, or superseded by a session the caller
        // refuses. The device has already opened the session, so it is cancelled
        // with the credentials it just returned rather than adopted by an owner
        // nobody is waiting for.
        //
        // The same judgement the two refusal paths above make, for the door that
        // skips them. `admitted()` calls `active()`, so a token replaced a moment
        // before this point refuses HERE rather than in the confirmation's catch,
        // and the plain 'withdrawn' this used to return is silent. Measured: the
        // log shows POST /cancel → 200 and no /start/confirm at all, so the
        // catch below is never reached and cannot report it — a user still
        // standing there was told nothing while believing they were recording.
        cancelReturned();
        return refuseStart();
      }
      // A takeover may be newer than our last status poll. Confirm the exact
      // request and returned credentials against the runtime's current owner.
      let confirmedUnder: SessionContext | null;
      try { confirmedUnder = await confirmTouchSession(current, requestId, deviceToken); }
      catch (failure) {
        cancelReturned();
        // A 409 is the runtime refusing this exact confirmation. It is not proof
        // that somebody else took the device, so an active activation is told the
        // recording did not start instead of being told about a takeover.
        if (failure instanceof VoiceControlError && failure.status === 409) return refuseStart();
        /**
         * The same decision the 409 branch makes, for the two doors that reach
         * here instead of it.
         *
         * `confirmTouchSession` refuses a replaced authority with a plain Error
         * rather than a VoiceControlError, and a confirmation that outlives
         * `voiceCommand`'s 2500ms abort arrives as an abort rather than a reply.
         * Both used to fall straight to a silent withdrawal, so a re-key that
         * landed a moment earlier — or a confirm that answered just too slowly —
         * told the user nothing while nothing was recording. `refuseStart` makes
         * exactly the judgement wanted here: silent only when the activation is
         * genuinely gone, and the one true sentence when the authority moved.
         */
        if (!active()) return refuseStart();
        throw failure;
      }
      /**
       * The confirmation is the runtime's answer about *this* request, so it must
       * come back under the authority the start was issued under — and that
       * authority must still be the one the device publishes.
       *
       * Both halves are required: the first catches a rotation between admission
       * and confirmation, the second one the status poll has already seen.
       * Neither is inferable from `voice.session_id`.
       */
      if (!sessionContextEqual(confirmedUnder, issuedUnder) || !contextMatches(confirmedUnder)) {
        cancelReturned();
        return refuseStart();
      }
      /**
       * Cancellation, navigation or auth rotation can occur during confirmation.
       *
       * This is the second door that reaches a bare `'withdrawn'`, and its own
       * comment names the rotation. The same judgement as the refusals above
       * applies: an activation that is genuinely over — cancelled, navigated
       * away, hidden — stays silent, while a replaced authority under a user who
       * is still standing there says the one thing that is true, because
       * otherwise they are left believing they are recording.
       */
      if (!admitted()) { cancelReturned(); return refuseStart(); }
      session.current = current;
      admittedContext.current = issuedUnder;
      recordingActive.current = true;
      setOwned(true);
      setOwnedSession(current.session_id);
      // Confirmation has used part of the initial lease. Renew immediately
      // after admission, then use the normal interval for subsequent heartbeats.
      keepRecording(current, 0);
      onStarted?.(current.session_id, photo);
      return 'started';
    } catch (failure) {
      if (mounted.current) setError(failure instanceof Error && failure.name === 'Error' ? failure.message : 'Could not start. Try again.');
      return 'failed';
    } finally { busy.current = false; if (mounted.current) setPending(false); }
  };

  const finish = async (action: 'stop' | 'cancel') => {
    const current = session.current;
    if (!current || busy.current) return;
    busy.current = true;
    setPending(true);
    recordingActive.current = false;
    clearTimeout(heartbeat.current);
    try {
      const sent = await voiceCommand(action, { session_id: current.session_id, token: current.token });
      /**
       * The command is only this owner's if it came back under the context the
       * capture was admitted under. Otherwise the epoch moved while it was in
       * flight: the session is void, so nothing here is treated as this
       * capture's outcome — no cancel callback, no cleared error, no rendering.
       */
      const stoppedUnder = replySessionContext(sent);
      if (!sessionContextEqual(stoppedUnder, admittedContext.current) || !contextMatches(stoppedUnder)) {
        session.current = null;
        admittedContext.current = null;
        if (mounted.current) { setOwned(false); setUncertain(false); setOwnedSession(null); setError(RECORDING_SESSION_CHANGED_NOTICE); }
        return;
      }
      session.current = null;
      admittedContext.current = null;
      if (action === 'cancel' && mounted.current) onCancelled?.();
      if (mounted.current) setError('');
    } catch {
      if (mounted.current) { setUncertain(true); setError('Connection lost. Check recording status.'); }
    } finally {
      busy.current = false;
      if (mounted.current) { setOwned(false); setPending(false); setOwnedSession(null); }
    }
  };

  /**
   * Withdraw an activation and stop whatever it left behind.
   *
   * Bumping the token first is what makes this safe against a start that is
   * still in flight: that start will see the token change after its await and
   * cancel the session it was handed. An already-accepted session is cancelled
   * here, with its own credentials.
   */
  const cancel = () => {
    intent.current += 1;
    if (session.current) { void finish('cancel'); return; }
    recordingActive.current = false;
    admittedContext.current = null;
    clearTimeout(heartbeat.current);
  };

  /**
   * Withdraw a pending start only.
   *
   * Bumping the token is the whole operation: a `start` still awaiting its
   * acknowledgement compares the token after that await and cancels the session
   * it was handed with the credentials that session returned, instead of
   * adopting a recording nobody is waiting for. An accepted session is left
   * alone here — `cancel` is the operation that stops one, and navigating
   * between Home and Chat is not a reason to stop recording.
   */
  const withdrawStart = () => { intent.current += 1; };

  useImperativeHandle(controllerRef, () => ({
    start,
    finish: (action: 'stop' | 'cancel') => { void finish(action); },
    cancel,
    withdrawStart,
  }));

  const openTyped = async () => {
    if (busy.current || session.current || typedPending) return;
    busy.current = true;
    setTypedPending(true);
    setTypedError('');
    // The authority this typed question is opened under, captured before the
    // first protected await. The review it opens is a session the device holds
    // for this client, so it is admitted under exactly the same expectation the
    // capture path uses rather than one read back out of the reply.
    const issuedUnder = publishedContext();
    try {
      // An authority that cannot be compared opens no review.
      if (issuedUnder === undefined) { setTypedError(TYPED_SESSION_CHANGED_NOTICE); return; }
      // A typed question needs no microphone and no capture: the runtime opens
      // the same review/submit path the spoken lane uses. An attached photo
      // travels with it exactly as it does for a spoken question.
      //
      // Retention is the spoken lane's decision and is fenced the same way: the
      // policy comes out of a protected reply, so that reply is judged against
      // the authority this review is opened under — and against the one the
      // device publishes now — before a single flag is read out of it. A reply
      // admitted under an epoch this client has not caught up with would
      // otherwise decide how many original bytes leave the device.
      let retainOriginal = false;
      if (photo) {
        const listing = await voiceCommand('history/list', {});
        if (!mounted.current || document.hidden) return;
        let listedUnder: SessionContext | null;
        try { listedUnder = replySessionContext(listing); }
        catch { setTypedError(TYPED_SESSION_CHANGED_NOTICE); return; }
        if (!sessionContextEqual(listedUnder, issuedUnder) || !contextMatches(listedUnder)) { setTypedError(TYPED_SESSION_CHANGED_NOTICE); return; }
        const history = parseDeviceHistory(listing);
        retainOriginal = history.photo_saving_supported === true && history.enabled && history.photos_enabled === true;
      }
      const reply = await voiceCommand('question/start', {
        request_id: crypto.randomUUID(),
        ...(photo ? { image_base64: photo.image, ...(retainOriginal ? { original_base64: photo.preview } : {}) } : {}),
      });
      const opened = parseTouchSession(reply);
      const cancelOpened = () => { void voiceCommand('cancel', { session_id: opened.session_id, token: opened.token }, true).catch(() => {}); };
      /**
       * The review is this activation's only when it was opened under the
       * authority the command was issued under, and that authority is still the
       * one the device publishes.
       *
       * The reply's own context cannot stand in for the expectation: a rotation
       * landing while the command is in flight is admitted under the new epoch,
       * so the reply and the latest reading agree with each other and disagree
       * only with the activation the user actually began. Refusing cancels the
       * credentials the reply carried, so a review nobody is waiting for does not
       * stay open on the device.
       */
      let openedUnder: SessionContext | null;
      try { openedUnder = replySessionContext(reply); }
      catch { cancelOpened(); setTypedError(TYPED_SESSION_CHANGED_NOTICE); return; }
      if (!sessionContextEqual(openedUnder, issuedUnder) || !contextMatches(openedUnder)) {
        cancelOpened();
        setTypedError(TYPED_SESSION_CHANGED_NOTICE);
        return;
      }
      // The review stays open if the screen hides, exactly like a spoken
      // review: the next status poll reopens the keyboard for the same session.
      if (mounted.current) onTypedSession?.(opened.session_id, photo);
    } catch (failure) {
      if (mounted.current) {
        setTypedError(failure instanceof Error && failure.message === 'Voice is busy. Please wait.'
          ? 'Please wait for the current question to finish.'
          : 'Could not open the keyboard. Ask the operator to check the question service.');
      }
    } finally {
      busy.current = false;
      if (mounted.current) setTypedPending(false);
    }
  };

  const view = deviceView(status, connected);
  const canStart = !uncertain && !!status?.voice.touch_ready && connected && ['ready', 'complete', 'error'].includes(view.phase);
  const photoForType = !photo || status?.voice.photo_ready === true;
  const canType = !uncertain && connected && typedLaneAvailable(status, connected) && !typedPending && photoForType
    && ['ready', 'complete', 'error', 'unavailable'].includes(view.phase);
  const askNotice = connected && status && !owned && !canStart ? voiceLaneNotice(status) : null;
  const typedNotice = connected && status
    ? (!typedLaneAvailable(status, connected) ? 'Typed questions need the local question service.'
      : (!photoForType ? 'This device cannot attach a photo to a typed question yet.' : null))
    : null;
  const notice = error || typedError || askNotice || typedNotice || (!compact && canStart && !owned ? 'Tap to speak' : '');
  const startPhotoQuestion = useEffectEvent(() => {
    onAutoStartHandled?.();
    if (canStart && !document.hidden) void start();
    else setError('Could not start recording. Tap Ask to try again.');
  });
  useEffect(() => {
    if (!autoStart || consumedStart.current === autoStart) return;
    const frame = requestAnimationFrame(() => {
      if (consumedStart.current === autoStart) return;
      consumedStart.current = autoStart;
      startPhotoQuestion();
    });
    return () => cancelAnimationFrame(frame);
  }, [autoStart]);
  const recordingActions = owned && <div className="device-recording-actions"><button className="device-talk-cancel" disabled={pending} onClick={() => void finish('cancel')}><CancelIcon />{cancelLabel}</button><button className="device-action-primary" disabled={pending} onClick={() => void finish('stop')}><SendIcon />{status?.voice.review_ready ? 'Done' : 'Send'}</button></div>;
  const microphone = <button className="device-talk-primary" disabled={!canStart || pending || owned} aria-busy={pending} onClick={() => void start()}><MicrophoneIcon />{label}</button>;
  /**
   * In Chat the typed lane *is* the composer, not a second button beside it, so
   * the separate Type control is not rendered twice.
   *
   * The field is hidden while a review is open: the review already carries the
   * only two things the user can do with it, and a disabled field that still
   * said "Type a message" would be an offer that is not on.
   */
  const reviewOpen = status?.voice.phase === 'review';
  /**
   * A disabled field has to say why on a touch panel, and the lane's own notice
   * only covers admission faults: during a recording, a review or a lost
   * connection the field is unavailable for a reason no status fault reports.
   */
  const composerReason = notice || (canType || reviewOpen ? '' : !connected ? 'The device is offline.'
    : owned ? 'Finish or cancel this recording first.'
      : typedPending ? 'Opening the keyboard...'
        : pending ? 'Please wait for this to finish.'
          : uncertain ? 'Confirm or retry the last submission first.'
            : 'Typed questions are not available yet.');
  if (composer) return <ChatComposer
    fieldLabel={reviewOpen ? null : 'Type a message'}
    pending={typedPending}
    reason={composerReason}
    canOpen={canType}
    onOpen={() => void openTyped()}
    keyboardRef={onKeyboardContainer}
  >{recordingActions}{microphone}</ChatComposer>;
  return <div className={`device-talk-controls${compact ? ' device-talk-compact' : ''}`}>
    {recordingActions}
    {notice && <span className="device-talk-status" role="status">{notice}</span>}
    {microphone}
    <button type="button" className="device-question-button" disabled={!canType} aria-busy={typedPending} onClick={() => void openTyped()}><KeyboardIcon />Type</button>
  </div>;
}
