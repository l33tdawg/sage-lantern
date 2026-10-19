'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { answerIsVisible, deviceView, parseDeviceStatus, statusIsFresh, waitingQuestion, type DeviceStatus } from '../../lib/device-state';
import { liveVoicePollDelay, mergeLiveVoiceStatus, mergeTelemetryStatus, parseLiveVoiceStatus } from '../../lib/live-voice';
import './screen.css';
import './community.css';
import './threads.css';
import { useDrawerNavigation } from './drawer-navigation';
import { ChatExchanges, useConversations } from './conversations';
import CameraScreen from './camera';
import HistoryScreen from './history';
import NearbyScreen from './nearby';
import AboutScreen from './about';
import SettingsScreen from './settings';
import MessagesScreen from './messages';
import GalleryScreen from './gallery';
import AgentScreen from './agent';
import HeartbeatScreen from './heartbeat';
import HardwareScreen from './hardware';
import Chat from './chat';
import ShareScreen from './share';
import type { ShareSource } from '../../lib/community';
import { voiceCommand } from '../../lib/voice-control';
import { deviceSessionHeaders, getDeviceToken, observeSessionContext, subscribeDeviceSession, establishedSessionContext, fenceProtectedReply } from '../../lib/device-session';
import type { DeviceProfile } from '../../lib/device-profile';
import { CameraIcon, SettingsIcon, HistoryIcon, InfoIcon, LockIcon, NetworkIcon, ShareIcon } from './icons';
import DeviceHeader, { type HeaderPage } from './device-header';
import TalkControls, { START_NOT_STARTED_NOTICE, type TalkController, type TalkOwnership } from './talk';
import AnswerWait from './answer-wait';
import QuestionTurn from './question-turn';
import { handoffQuestion, type QuestionHandoff } from '../../lib/question-flow';
import QuestionCorrection from './question-correction';
import QuestionReview from './question-review';
import SuggestedQuestion from './suggested-question';
import { CorrectionAttempts } from '../../lib/question-correction';
import Image from 'next/image';
import { savedHistory } from '../../lib/saved-history';
import { photoForSession, type PhotoAttachment, type SessionPhoto } from '../../lib/photo-preview';
import Home, { type HomeActivation } from './home';
import {
  NO_CAPTURE_OWNER, destinationKeepsCapture, resetSlotWait, voiceControlState,
  type CaptureOwnership, type HomeDestination, type HomeFocusTarget, type SlotWaitOutcome,
} from '../../lib/home-navigation';
import './portrait.css';

/** The activation this screen has not finished resolving yet. */
const IDLE_ACTIVATION: HomeActivation = Object.freeze({ pending: false });

/**
 * Every destination this screen can be showing.
 *
 * `main` is Chat. The rest are reachable from Home, from each other, or from
 * the header and menu.
 */
type Page = 'home' | 'main' | 'camera' | 'history' | 'status' | 'about' | 'settings' | 'messages' | 'sharing' | 'gallery' | 'agent' | 'heartbeat' | 'hardware';

/**
 * Where Back returns to, and what to focus when it gets there.
 *
 * A route opened from Home must go back to Home, not to Chat, and a viewer
 * opened from the camera must go back to the camera. Holding the origin here —
 * rather than inferring it from the page we are leaving — is what lets two
 * controls reach the same screen and still Back to different places.
 *
 * Settings is the one destination reachable from *every* screen (the menu is in
 * the shared header), so its origin is whatever screen the user was on, and it
 * is recorded by both entries. Without that, Settings was the only Back in the
 * app that could not find where it came from and fell through to Chat.
 *
 * `focusRef` names the control in the shared header that opened the route, and is
 * set only when that control outlives the journey. The header is mounted on every
 * screen, so its own entries — the menu button, Messages, Nearby — are still there
 * to return to. A route opened from a control *inside* the screen being left
 * leaves it unset, because that screen unmounts and its replacement already takes
 * focus on entry. Guessing the control from the origin's page instead is what put
 * focus on the Menu button for every return to a screen the header has no entry
 * for, so the second activation went to a control that had opened nothing.
 *
 * An origin may carry the origin *it* was opened over, because one Back is not
 * always the whole journey: Home → Settings → Hardware is two steps in and must
 * be two steps out. Settings' own origin is therefore kept as `parent` when a
 * subsection replaces the slot, and a Back that lands there puts it back. The
 * chain has no fixed depth: the shared menu can alternate Settings and About
 * repeatedly, so a journey of sixteen Back presses to Home is real rather than
 * bounded away. Each link keeps the origin it actually displaced and restores
 * it in turn rather than accumulating a duplicate, so no Back revisits the
 * screen it just came from.
 */
type BackOrigin = { page: Page; homeFocus?: HomeFocusTarget; focusRef?: HeaderFocus; parent?: BackOrigin };

/**
 * A control in the shared header, named so Back can return to it.
 *
 * A name rather than a stored element, because the element a control had before a
 * navigation is not the element that exists after it: only the header survives a
 * route change, and these re-resolve from this file's own refs on the way back.
 * `menu` is the button that opens the menu; `messages`, `nearby` and `camera` are
 * the header entries and the Chat camera shortcut; `history` and `share` are the
 * Chat shortcuts, which only the no-origin path below has to name.
 */
type HeaderFocus = 'menu' | 'messages' | 'nearby' | 'camera' | 'history' | 'share';

/**
 * Which Home destinations this build can actually open.
 *
 * Chat, Vision, Messages and Nearby are existing screens: opening one from Home
 * shows what it already shows, including its own unavailable state when its
 * transport is down. Gallery is 2b and Agent is 2c.
 *
 * Heartbeat and Hardware are reached from Settings and have no tile at all, which
 * is why they are absent here rather than listed as unavailable. A tile whose
 * screen does not exist is still rendered in its stable position and reports
 * itself unavailable, rather than being dropped or opening something else — a
 * tile map that reorders or disappears is worse than one that says "not yet".
 */
const HOME_AVAILABLE: readonly HomeDestination[] = Object.freeze(['chat', 'vision', 'messages', 'nearby', 'gallery', 'agent']);

export default function DeviceScreen({ onLock, profile, onProfileChange, homeEnabled = false }: { onLock?: () => void; profile?: DeviceProfile; onProfileChange?: (profile: DeviceProfile) => void; homeEnabled?: boolean } = {}) {
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const chats = useConversations(connected, status?.voice.phase === 'complete' ? status.voice.session_id : undefined);
  /**
   * The conversation reset, held in a ref.
   *
   * The epoch fence drops the thread it can no longer vouch for, but the poll
   * runs for the life of the screen and `useConversations` returns a new closure
   * every render — depending on it directly would rebuild the poll on every
   * render, which is a worse defect than the one being fixed.
   */
  const resetChats = useRef(chats.reset);
  useEffect(() => { resetChats.current = chats.reset; });
  const [showChats, setShowChats] = useState(false);
  /**
   * Whether the conversation list is pinned open on a portrait panel.
   *
   * Every portrait panel matches the narrow query, so there the list can only be
   * the off-canvas drawer. The owner asked for it to stay collapsible but be
   * pin-able: it still starts collapsed, and the pin keeps it open as a real
   * column beside the conversation. Deliberately in memory only - it is session
   * state, and no protected content is persisted anywhere in this app.
   */
  const [chatsPinned, setChatsPinned] = useState(false);
  const listOpen = showChats || chatsPinned;
  useEffect(() => { if (listOpen) void savedHistory.refresh(); }, [listOpen]);
  const chatDrawer = useRef<HTMLElement>(null);
  // Closing is the user saying they are done with the list, so it outranks a
  // pin: leaving the pane open after Close would make that control a lie.
  const closeChats = useCallback(() => {
    /**
     * Whether the keyboard is about to be destroyed with the list.
     *
     * Captured BEFORE the state change: by the time a frame callback runs, the
     * control that had focus is already hidden and focus has fallen to the body,
     * so asking afterwards `where is focus?` answers with the damage rather than
     * the cause. My first attempt did exactly that and restored nothing.
     */
    const list = chatDrawer.current;
    const heldFocus = !!list && !!document.activeElement && list.contains(document.activeElement);
    setShowChats(false);
    setChatsPinned(false);
    /**
     * Put the keyboard back on the control that opens the list.
     *
     * A pinned list is deliberately not a modal, so the drawer's own focus
     * restore never runs for it: dismissing one left focus on the document body,
     * measured, while the list it belonged to had just been hidden. Only when the
     * list actually held focus, so dismissing it from outside — a scrim press —
     * never moves the keyboard the user is already using.
     */
    if (!heldFocus) return;
    requestAnimationFrame(() => {
      const opener = document.querySelector<HTMLElement>('.device-chat-list-button, .device-history-shortcut');
      opener?.focus();
    });
  }, []);
  // A pinned list is a column, not a modal, so it takes no focus trap.
  const narrowChats = useDrawerNavigation(chatDrawer, showChats && !chatsPinned, closeChats);
  const [chatSearch, setChatSearch] = useState('');
  const edgeSwipe = useRef<{ x: number; y: number } | null>(null);
  const [page, setPage] = useState<Page>(homeEnabled ? 'home' : 'main');
  const [shareSource, setShareSource] = useState<ShareSource | undefined>(undefined);
  const [clearedSession, setClearedSession] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState('');
  const [photo, setPhoto] = useState<PhotoAttachment | undefined>(undefined);
  const [photoStart, setPhotoStart] = useState<string | undefined>(undefined);
  const [sessionPhoto, setSessionPhoto] = useState<SessionPhoto | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false);
  const [touchBusy, setTouchBusy] = useState(false);
  /**
   * What the single capture owner holds. Home reads this to decide whether the
   * listening state on screen is *its own* recording — the one it may offer to
   * stop — or somebody else's, which it may only report.
   */
  const [captureOwnership, setCaptureOwnership] = useState<TalkOwnership>({ owned: false, pending: false, uncertain: false, sessionId: null });
  const [activation, setActivation] = useState<HomeActivation>(IDLE_ACTIVATION);
  const captureController = useRef<TalkController | null>(null);
  /**
   * Monotonic activation token. Every start takes a new one; every withdrawal
   * bumps it, so an activation that is still awaiting a reset or a capture
   * acknowledgement can tell that the gesture which asked for it is over and
   * unwind instead of starting a recording nobody is waiting for.
   */
  const activationToken = useRef(0);
  /**
   * Status readings already accepted from the device, and the preparations
   * waiting for the next one.
   *
   * A preparation that has just reset the context cannot trust the reading it
   * already holds: `/clear` is acknowledged before the worker releases the
   * recording slot, so starting against the pre-reset reading is what produced
   * the reproduced "cleared but never started" defect. Counting readings is how
   * "the device has not answered yet" is told apart from "the device answered and
   * the slot is free".
   */
  const statusReadings = useRef(0);
  const readingWaiters = useRef<Array<() => void>>([]);
  const activationPending = useRef(false);
  /**
   * The inputs a start is judged against.
   *
   * `status` and `connected` are written by the poll **in the same tick it
   * counts the reading**, not mirrored into the ref by an effect. A preparation
   * woken by a reading must be judged against that reading; mirroring it one
   * render late let a waiting start read the pre-reset status, decide the
   * recording slot was free, and dispatch a start the device then refused with
   * 409 voice_busy. `blocked` and `ownership` are render-derived and are filled
   * in below, where the state they come from exists.
   */
  const liveInputs = useRef({ status, connected, blocked: false, pending: false, ownership: NO_CAPTURE_OWNER as CaptureOwnership });
  const [correction, setCorrection] = useState<{ sourceId: string; question: string; photo?: PhotoAttachment; historyPhoto?: boolean; fromHistory?: boolean } | null>(null);
  const [correctionAttempts] = useState(() => new CorrectionAttempts());
  const readingPosition = useRef(0);
  const followLatest = useRef(true);
  /**
   * The partial answer a normal interruption left behind, and the turn it belongs
   * to.
   *
   * The status contract publishes no interruption flag, so this is derived from
   * what the screen already sees: a reading that was streaming an answer, and a
   * later reading that has stopped processing without completing. The words are
   * the device's own — `Incomplete` is what says the answer never finished — so
   * retention is bound to the authority that produced it and is dropped by every
   * fence the screen already has: an epoch or owner change, a new voice session,
   * New chat, and the lock's unmount.
   *
   * It is state rather than a ref because what it holds is *rendered*: a value read
   * out of a ref would not re-render when the hold appeared or was dropped, so the
   * marker could lag the words it describes.
   */
  const [partialTurn, setPartialTurn] = useState<{ sessionId: string; question: string; text: string } | null>(null);
  const authorityRevision = useRef(0);
  /**
   * Retained answer text is protected content, so it is dropped through the one
   * authority seam every other protected surface already uses.
   *
   * The poll's own fence cannot cover this. A reading that cannot be compared —
   * a missing or malformed context — drops the authority inside
   * `observeSessionContext` and *then* reports the refusal, so the words this
   * screen is holding are already outliving the authority that produced them
   * before the poll's catch runs; the catch reports the device as unreachable
   * rather than clearing protected content. A replaced token and a refusal first
   * observed by another surface reach the same state from outside this poll
   * entirely. Subscribing here covers all three without inventing a second
   * invalidation channel, and restoring the old context cannot revive the words
   * because the hold is already gone.
   */
  useEffect(() => subscribeDeviceSession(() => { authorityRevision.current += 1; setPartialTurn(null); }), []);
  const [scrollRequest, setScrollRequest] = useState(0);
  const showLatest = () => {
    followLatest.current = true;
    setScrollRequest((request) => request + 1);
  };
  const menuButton = useRef<HTMLButtonElement>(null);
  const cameraButton = useRef<HTMLButtonElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const messagesButton = useRef<HTMLButtonElement>(null);
  const nearbyButton = useRef<HTMLButtonElement>(null);
  const shareButton = useRef<HTMLButtonElement>(null);
  const menuClose = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const reader = useRef<HTMLDivElement>(null);
  const session = useRef<string | undefined>(undefined);
  /** The session whose authoritative review has already been routed to Chat. */
  const reviewRouted = useRef<string | null>(null);
  const reviewRetry = useRef<{ sessionId: string; photo?: PhotoAttachment } | null>(null);
  const [questionHandoff, setQuestionHandoff] = useState<QuestionHandoff | null>(null);
  /**
   * Where an open typed review's keyboard renders.
   *
   * A callback ref rather than a plain one, because the composer is rendered by
   * the capture owner in the footer and the review is rendered here: the
   * element arrives after the first render, and the portal needs the re-render
   * that tells it where to go.
   */
  const [composerKeyboard, setComposerKeyboard] = useState<HTMLDivElement | null>(null);
  const [reviewActions, setReviewActions] = useState<HTMLDivElement | null>(null);
  const [suggestedDraft, setSuggestedDraft] = useState<{ sourceId: string; question: string } | null>(null);
  /**
   * Where a shortcut-opened route came from, so Back returns there.
   *
   * Every entry that opens a route records one — a Home tile, the gear, the
   * menu's own entries, the header's Messages and Nearby, and the camera's
   * Gallery shortcut. Without it, Back always landed in Chat, which is not where
   * the user left.
   */
  const backTo = useRef<BackOrigin | null>(null);
  /** The Home control a return should focus, and a counter so repeats still fire. */
  const [homeFocus, setHomeFocus] = useState<{ target: HomeFocusTarget; nonce: number } | null>(null);

  /**
   * Invalidate a pending activation without touching a recording that was
   * already accepted.
   *
   * Used where the *authority* changes — a new session, a disconnect, a status
   * that can no longer be trusted. A preparation that is still in flight must
   * not be allowed to finish, but an owned recording is reconciled by the
   * capture owner itself and must not be torn down by a poll.
   */
  /** Publish the activation and keep the ref the poll reads in step with it. */
  const applyActivation = useCallback((next: HomeActivation) => {
    activationPending.current = next.pending;
    setActivation(next);
  }, []);

  const fenceActivation = useCallback(() => {
    activationToken.current += 1;
    /**
     * Withdraw the capture owner's pending start as well.
     *
     * The parent's token and the owner's intent token are two different fences,
     * and bumping only this one left a command already awaiting its
     * acknowledgement free to arrive, be adopted and be kept alive — the
     * reproduced defect where a status disconnect, or an unrelated session
     * publication, during a delayed `/start` produced a recording nobody owned,
     * on a screen showing "Reconnecting" with no Cancel.
     *
     * Every authority and freshness invalidation of a pending activation
     * withdraws it here now, so navigation is no longer the only path that does.
     * A start landing after this is cancelled with the credentials it returned
     * rather than adopted. An *accepted* recording is untouched: `withdrawStart`
     * bumps the intent and nothing else, which is what lets an owned recording
     * survive purely viewing another surface.
     */
    captureController.current?.withdrawStart();
    applyActivation(IDLE_ACTIVATION);
  }, [applyActivation]);

  /**
   * Abandon the activation and everything it may have started. Used when this
   * screen stops being able to show what it asked for: hidden, unmounted, or
   * navigated away from Home. An unacknowledged start is withdrawn, and an
   * accepted session is cancelled with its own credentials.
   */
  const abandonActivation = useCallback(() => {
    fenceActivation();
    captureController.current?.cancel();
  }, [fenceActivation]);

  useEffect(() => {
    let disposed = false;
    let nextPoll: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    let telemetryTimer: ReturnType<typeof setTimeout>;
    let telemetryController: AbortController;
    let telemetryObservedAt: string | null = null;
    const poll = async () => {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 4000);
      // The auth session this request is issued under. A reply that outlives it
      // describes an authority that no longer exists.
      const issuedUnder = getDeviceToken();
      const issuedRevision = authorityRevision.current;
      let observedByThisRequest = false;
      try {
        const response = await fetch('http://127.0.0.1:3199/voice', { cache: 'no-store', signal: controller.signal, headers: deviceSessionHeaders() });
        if (!response.ok) throw new Error('Status unavailable');
        const live = parseLiveVoiceStatus(await response.json());
        const next = mergeLiveVoiceStatus(liveInputs.current.status, live, telemetryObservedAt);
        if (!statusIsFresh(next)) throw new Error('Status is stale');
        /**
         * A reply that outlived the surface or the auth session it was issued
         * under is evidence about nothing, so it is refused here — before the
         * shared observation seam and before any state.
         *
         * `observeSessionContext` is process-wide: it establishes the authority
         * every protected surface — chats, gallery, saved history — compares
         * against. Letting an obsolete reply establish it would install an
         * authority that is already gone, and the *next* current reading would
         * then look like a rotation and drop content that was never under the
         * dead authority at all. A replaced token has already reset the
         * observation, so such a reply can only ever roll it back.
         *
         * Refused rather than thrown: for a live screen this is a reply to skip,
         * not a reason to report the device as unreachable, and the loop
         * reschedules itself either way.
         */
        if (disposed || getDeviceToken() !== issuedUnder || authorityRevision.current !== issuedRevision) return;
        /**
         * The epoch fence.
         *
         * The context is derived from the access epoch *and* the device token, so
         * it survives every voice publication — the clear worker's idle state, a
         * start's recording, a finished turn — and changes only when the epoch
         * rotates or the token is replaced. That is the whole reason a new
         * `voice.session_id` is no longer treated as a change of owner: it is a
         * publication, not an authority, and the counter that used to tell the
         * two apart could both excuse a real rotation and fence a legitimate one.
         *
         * A reading that cannot be compared is refused outright. A reading that
         * disagrees with the observed context is a real rotation, so everything
         * protected is dropped before the new authority's reading is rendered.
         * Reporting it through `observeSessionContext` is also what tells the
         * other protected surfaces (chats, gallery, saved history) to drop what
         * they hold, without a second invalidation channel beside the seam.
         */
        observedByThisRequest = true;
        const observation = observeSessionContext(next.session_context);
        if (observation === 'refused') throw new Error('Status context unavailable');
        if (!disposed) {
          if (observation === 'changed') {
            fenceActivation();
            // A partial answer is protected content: an epoch or owner change
            // drops the words with everything else, and retention never outlives
            // the authority that produced it.
            setPartialTurn(null);
            setQuestionHandoff(null);
            setSuggestedDraft(null);
            setCorrection(null);
            setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined);
            resetChats.current();
            readingPosition.current = 0; followLatest.current = true;
            /**
             * And the device goes back to the surface a new authority starts on.
             *
             * Everything protected has just been dropped, so the screen the user
             * was looking at has been emptied underneath them. Leaving them on it
             * is a screen that silently discarded their work — a task form closed
             * with the draft that was in it a moment ago, a composed message gone —
             * and says nothing about why. The destination is the one a fresh mount
             * picks, so an unlock and a rotation cannot land on two different
             * "neutral" surfaces.
             *
             * This is the rotation only. A *publication* within one authority keeps
             * the user where they are, which is the half that used to be wrong, and
             * the branch below is where that is decided. The origin chain and any
             * pending Home focus belong to the authority that just ended, so neither
             * survives it.
             */
            backTo.current = null;
            setHomeFocus(null);
            setMenuOpen(false);
            setPage(homeEnabled ? 'home' : 'main');
          }
          if (next.voice.session_id !== session.current) {
            session.current = next.voice.session_id;
            // A new turn's words are not the previous turn's, so the held partial
            // goes with the session it belonged to.
            setPartialTurn(null);
            // A new voice session is a publication within the same authority, so
            // the per-turn presentation resets but the activation is not fenced:
            // admission is a correlation (the `admit` gate and the runtime's own
            // confirmation), not something a session id can decide on its own.
            setQuestionHandoff(null);
            setSuggestedDraft(null);
            setPhoto(undefined); setPhotoStart(undefined);
            // A new session returns the user to the question surface — but Home
            // *is* the question surface, so bouncing it to `main` here made every
            // visit to Home last one poll. Detached screens (messages, sharing)
            // are left where they are, as before. Agent is left too: its draft is
            // screen state that must not outlive its authority, and a publication
            // is not an authority change, so navigating away here would discard a
            // draft by a route the lock and owner-change rule does not cover.
            setPage((current) => current === 'messages' || current === 'sharing' || current === 'agent' || (homeEnabled && current === 'home') ? current : 'main');
            setMenuOpen(false);
            setSessionPhoto((current) => current?.sessionId === next.voice.session_id ? current : undefined);
            readingPosition.current = 0; followLatest.current = true;
          }
          // The review editor lives in the existing Chat screen. Home is the
          // preparation and listening surface, so the authoritative review is
          // routed there — once per session, and never by running the New chat
          // policy as a side effect of a poll.
          if (homeEnabled && next.voice.phase === 'review' && next.voice.session_id && reviewRouted.current !== next.voice.session_id) {
            reviewRouted.current = next.voice.session_id;
            setPage((current) => (current === 'home' ? 'main' : current));
          }
          // The ref is updated in the same tick as the counter, not later: a
          // preparation woken by this reading must see *this* reading.
          liveInputs.current.status = next;
          liveInputs.current.connected = true;
          /**
           * Keep the words that really arrived, so an interruption can retain
           * them rather than losing an answer that was half spoken.
           *
           * Judged from the reading that was accepted: a refused, stale or
           * superseded reply never reaches this point, so it can neither set a
           * hold nor clear one. A completed answer clears the hold because
           * completion is the thing that makes `Incomplete` untrue.
           */
          const acceptedView = deviceView(next, true);
          if (acceptedView.phase === 'processing' && next.voice.response.trim()) {
            setPartialTurn({
              sessionId: next.voice.session_id ?? '',
              question: next.voice.transcript,
              text: next.voice.response,
            });
          } else if (acceptedView.phase === 'complete') {
            setPartialTurn(null);
          }
          /**
           * The device is publishing a live session that is not the one our owner
           * holds: ours was superseded.
           *
           * This is what an *unexpected* voice-session publication looks like from
           * here, and it is the case the session-change fence above cannot judge
           * on its own. While a start is awaiting its acknowledgement the device
           * may legitimately publish the session it is about to hand us, so
           * fencing early would destroy the very recording the user asked for.
           * Once the owner has adopted one, the two are comparable — and keeping
           * a session the device has moved past alive would hold a recording
           * nobody owns. It is cancelled with its own credentials.
           *
           * Judged here rather than in an effect: this is the reading that
           * establishes it, it is already fresh-validated above, and the update
           * is not on the render path.
           */
          const held = liveInputs.current.ownership;
          if (held.owned && held.sessionId && next.voice.session_id && next.voice.session_id !== held.sessionId
            && ['listening', 'review', 'processing'].includes(next.voice.phase)) {
            fenceActivation();
            captureController.current?.cancel();
          }
          setStatus(next); setConnected(true);
          // A fresh, validated reading is what a preparation waiting on the
          // device is waiting for. Only readings the device actually produced
          // count; a failed poll is not evidence of anything.
          statusReadings.current += 1;
          const waiting = readingWaiters.current;
          readingWaiters.current = [];
          for (const wake of waiting) wake();
        }
      } catch { if (!disposed && getDeviceToken() === issuedUnder && (observedByThisRequest || authorityRevision.current === issuedRevision)) { liveInputs.current.connected = false; fenceActivation(); setConnected(false); setQuestionHandoff(null); setCorrection(null); setSuggestedDraft(null); } }
      finally { clearTimeout(deadline); if (!disposed) nextPoll = setTimeout(poll, liveVoicePollDelay(liveInputs.current.status, liveInputs.current.pending)); }
    };
    const pollTelemetry = async () => {
      telemetryController = new AbortController();
      const deadline = setTimeout(() => telemetryController.abort(), 4000);
      const issuedToken = getDeviceToken();
      const issuedAuthority = establishedSessionContext();
      try {
        const response = await fetch('http://127.0.0.1:3199/status', { cache: 'no-store', signal: telemetryController.signal, headers: deviceSessionHeaders() });
        if (!response.ok) throw new Error('Telemetry unavailable');
        const next = parseDeviceStatus(await response.json());
        if (!statusIsFresh(next)) throw new Error('Telemetry is stale');
        // This channel never observes or establishes authority. A reply issued
        // before a re-key (including A→B→A) cannot roll the live observer back.
        if (disposed || getDeviceToken() !== issuedToken || next.session_context === 'invalid'
          || fenceProtectedReply(issuedAuthority, next.session_context) !== 'ok') return;
        const current = liveInputs.current.status;
        if (!current) return;
        const merged = mergeTelemetryStatus(current, next);
        if (!merged) return;
        telemetryObservedAt = next.observed_at;
        liveInputs.current.status = merged;
        setStatus(merged);
      } catch {
        // A failed hardware refresh cannot grant readiness indefinitely. The
        // next live reading keeps the answer but discards unverified telemetry.
        const current = liveInputs.current.status;
        if (!disposed && getDeviceToken() === issuedToken && current && current.session_context !== 'invalid'
          && fenceProtectedReply(issuedAuthority, current.session_context) === 'ok') telemetryObservedAt = null;
      } finally {
        clearTimeout(deadline);
        if (!disposed) telemetryTimer = setTimeout(pollTelemetry, 700);
      }
    };
    void poll().then(() => { if (!disposed) void pollTelemetry(); });
    return () => {
      disposed = true;
      clearTimeout(nextPoll); clearTimeout(telemetryTimer);
      controller?.abort(); telemetryController?.abort();
    };
  }, [homeEnabled, fenceActivation]);

  useEffect(() => { if (menuOpen) menuClose.current?.focus({ preventScroll: true }); }, [menuOpen]);
  useEffect(() => {
    const hide = () => {
      if (!document.hidden) return;
      // A hidden screen cannot show what it asked for, so a start it is still
      // waiting on is withdrawn rather than left to arrive unseen.
      abandonActivation();
      setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined); setQuestionHandoff(null); setSuggestedDraft(null);
    };
    document.addEventListener('visibilitychange', hide);
    return () => {
      document.removeEventListener('visibilitychange', hide);
      // Unmounting also withdraws a pending activation. The owner cancels its own
      // session as it unmounts beside this screen; all the parent has to do is
      // stop treating the activation as live.
      activationToken.current += 1;
    };
  }, [abandonActivation]);
  const inCurrentChat = !chats.current || status?.voice.session_id === chats.current.active_session_id || chats.current.items.some((item) => item.session_id === status?.voice.session_id);
  // The voice poll leads the independent chat poll during a new turn. Keep the
  // in-flight question visible instead of flashing an empty/ready screen.
  const voiceInFlight = !!status && (['listening', 'review', 'processing'].includes(status.voice.phase)
    || (status.voice.phase === 'complete' && questionHandoff?.sessionId === status.voice.session_id));
  const chatStatus = status && !inCurrentChat && !voiceInFlight ? { ...status, voice: { ...status.voice, phase: 'idle', transcript: '', response: '' } } : status;
  const visibleStatus = chatStatus && clearedSession && chatStatus.voice.session_id === clearedSession ? { ...chatStatus, voice: { ...chatStatus.voice, phase: 'idle', transcript: '', response: '' } } : chatStatus;
  const view = deviceView(visibleStatus, connected);
  // Recording belongs to the current voice session even while the chat poll
  // still contains the previous active_session_id. It exposes no prior text.
  const recordingView = deviceView(status, connected);
  const recording = recordingView.phase === 'listening';
  const reviewing = recordingView.phase === 'review';
  const typedReview = reviewing && status?.voice.input_source === 'typed';
  const answerVisible = answerIsVisible(visibleStatus, connected);
  const toolbarWaiting = view.phase === 'processing' && !answerVisible;
  const question = waitingQuestion(visibleStatus, connected);
  /**
   * The turn an interruption left on screen.
   *
   * A partial answer is shown only while the hold still belongs to the session on
   * screen and was not cleared by New chat. An explicit failure is not an
   * interruption: the device said what went wrong, so the error stands on its own
   * rather than being dressed up as an unfinished answer.
   */
  const heldTurn = partialTurn && partialTurn.sessionId !== clearedSession
    && partialTurn.sessionId === (visibleStatus?.voice.session_id ?? '') ? partialTurn : null;
  const interrupted = !answerVisible && view.phase !== 'processing' && view.phase !== 'error' && !!heldTurn;
  const partialAnswer = interrupted && heldTurn ? heldTurn.text : '';
  const partialQuestion = interrupted && heldTurn ? heldTurn.question : '';
  const currentQuestion = handoffQuestion(visibleStatus, connected, questionHandoff) || question
    || (answerVisible ? visibleStatus?.voice.transcript : null) || (interrupted ? partialQuestion : null) || null;
  const turnVisible = recording || reviewing || view.phase === 'processing' || answerVisible || interrupted;
  const displayedPhoto = photo || (connected ? photoForSession(sessionPhoto, status?.voice.session_id) : undefined);
  const text = status?.voice.response || '';
  const draft = connected && view.phase === 'complete' && suggestedDraft?.sourceId === visibleStatus?.voice.session_id ? suggestedDraft : null;
  const suggestedQuestions = view.phase === 'complete' ? visibleStatus?.voice.follow_up_questions || [] : [];

  // Submit resumes following even if the user was reading an older exchange.
  // Apply after the new layout, before paint; passive scrolling can otherwise
  // show the old position or mistake layout changes for a scroll away.
  useLayoutEffect(() => {
    const element = reader.current;
    if (!element) return;
    if (followLatest.current) element.scrollTop = element.scrollHeight;
    else element.scrollTop = readingPosition.current;
  }, [scrollRequest, text, currentQuestion, page, correction, answerVisible, recording, reviewing, draft, status?.voice.phase, status?.voice.session_id, chats.current?.thread_id, chats.current?.items.length]);

  const closeMenu = () => { setMenuOpen(false); requestAnimationFrame(() => menuButton.current?.focus()); };

  /**
   * Leave for a route, remembering where Back should return to.
   *
   * `focusRef` returns focus to a header control on the origin screen; the Home
   * control is restored through `homeFocus` instead, because it only exists
   * while Home is mounted.
   *
   * The origin this entry displaced travels with it as the parent, exactly as it
   * does for a menu destination. Recording only the immediate origin made the
   * first Back correct and the second one fall through to Chat: the camera and
   * the gallery link to each other, so Home -> Gallery -> Camera, Back, Back
   * dropped the Home origin the tile had recorded and landed in Chat with focus
   * on the shared Menu button.
   */
  const openRoute = (destination: Page, origin: BackOrigin) => {
    const displaced = backTo.current;
    backTo.current = { ...origin, ...(displaced ? { parent: displaced } : {}) };
    setPage(destination);
  };

  /**
   * Focus a control in the shared header by name.
   *
   * The header is rendered by this file, so every one of these is a ref it owns
   * and can focus whichever screen is behind it.
   */
  const focusHeaderControl = (target: HeaderFocus) => {
    const element = target === 'messages' ? messagesButton.current
      : target === 'nearby' ? nearbyButton.current
        : target === 'camera' ? cameraButton.current
          : target === 'history' ? historyButton.current
            : target === 'share' ? shareButton.current
              : menuButton.current;
    element?.focus();
  };

  const goBack = () => {
    const origin = backTo.current;
    /**
     * Hand the slot back to the origin being returned to, not to nothing.
     *
     * Clearing it outright made the first Back the last one: Home → Settings →
     * Hardware returned to Settings correctly, but Settings then held no origin,
     * so the second Back fell through to Chat below. Settings' own origin was
     * overwritten when the subsection opened and had nowhere to survive. The
     * guard drops a parent that is the origin itself, which no path produces
     * today and which would otherwise make Back unable to leave that screen.
     */
    backTo.current = origin?.parent && origin.parent !== origin ? origin.parent : null;
    if (origin) {
      setPage(origin.page);
      if (origin.homeFocus) {
        const target = origin.homeFocus;
        setHomeFocus((current) => ({ target, nonce: (current?.nonce ?? 0) + 1 }));
      } else {
        // The control that opened the route, when one outlived it. A menu-only
        // destination opened from Home records `menu` here, because the menu item
        // itself is gone: the button that opened the menu is what is still there.
        const target = origin.focusRef;
        if (target) requestAnimationFrame(() => focusHeaderControl(target));
      }
      // Unset means nothing named a surviving control: the screen that just
      // mounted takes focus on entry (every route screen focuses its own Back, and
      // Home its requested target), and that stands. Naming the Menu button here
      // put focus on a control that had not opened anything.
      return;
    }
    setPage('main');
    // No recorded origin at all: nothing names the control that opened the screen
    // being left, so the header entry for it is the only honest answer, and the
    // page being left is the only evidence there is.
    requestAnimationFrame(() => focusHeaderControl(page === 'messages' ? 'messages'
      : page === 'sharing' && shareSource ? 'share'
        : page === 'history' ? 'history'
          : 'menu'));
  };
  const busy = clearing || chats.selecting || touchBusy || recording || reviewing || !!draft || view.phase === 'processing';
  /**
   * Why Chat's header controls are disabled, in visible text.
   *
   * The bar is not the only way off the screen - the header keeps its own Home
   * control - but a disabled control on a touch panel still has to say what is
   * stopping it instead of hiding the reason in a tooltip no finger can reach.
   */
  const chatHeaderNotice = !connected ? 'The device is offline.'
    : busy ? 'Finish or cancel what is in progress first.' : undefined;
  const canShareAnswer = connected && !busy && view.phase === 'complete' && !!status?.voice.session_id;
  const canAskAgain = connected && !!status?.voice.touch_ready && !busy && ['ready', 'complete', 'error'].includes(view.phase);
  useEffect(() => {
    const retry = reviewRetry.current;
    if (!retry || !connected || status?.voice.session_id !== retry.sessionId || status.voice.phase !== 'idle') return;
    const frame = requestAnimationFrame(() => {
      reviewRetry.current = null;
      setPhoto(retry.photo); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID()); setPage('main');
    });
    return () => cancelAnimationFrame(frame);
  }, [connected, status]);
  const editQuestion = !busy && view.phase === 'complete' && status?.voice.correction_ready === true && connected && visibleStatus?.voice.session_id && visibleStatus.voice.transcript.trim() && (!status.voice.has_photo || displayedPhoto)
    ? () => setCorrection({ sourceId: visibleStatus.voice.session_id!, question: visibleStatus.voice.transcript, photo: displayedPhoto }) : undefined;
  const askAgain = (attachment?: PhotoAttachment) => {
    if (!canAskAgain) return;
    setPhoto(attachment); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID());
    setPage('main'); setClearError('');
    if (status?.voice.session_id) setClearedSession(status.voice.session_id);
    readingPosition.current = 0;
  };
  /**
   * The existing New chat policy, with an outcome.
   *
   * It used to swallow its failure, which is fine for a button that only has to
   * look tidy but not for a caller that intends to record afterwards: a reset
   * that failed leaves the previous context loaded, and recording into it is the
   * wrong-thread bug the brief names. So the caller gets `success`, `failure` or
   * `refused` and capture follows `success` only.
   */
  const clearAnswer = async (): Promise<'success' | 'failure' | 'refused'> => {
    if (busy) return 'refused';
    const current = status?.voice.session_id;
    setClearing(true); setClearError('');
    /**
     * A clear changes saved history: the exchange it stops holding is retained
     * as a past conversation. Without this window the cache kept serving the
     * snapshot it took before the clear, and because the only forced read was
     * gated on a *completed* session, the conversation the user had just
     * cleared did not appear in Past chats for ~56s — through the 30s interval's
     * first tick, which the refresh guard treats as a no-op. `beginChange` is
     * the same invalidation a removal and a settings write already use: reads
     * are held while the change is in flight, and a fresh one is forced once it
     * settles, whether or not the list happens to be open.
     */
    const endHistoryChange = savedHistory.beginChange();
    let accepted = false;
    try {
      await voiceCommand('clear', {});
      accepted = true;
      chats.reset(); setShowChats(false);
      setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined);
      setQuestionHandoff(null);
      setSuggestedDraft(null);
      setClearedSession(current || null);
      // New chat is an explicit request to stop holding the last turn, so the
      // partial goes with it rather than reappearing as an unfinished answer.
      setPartialTurn(null);
      readingPosition.current = 0;
      return 'success';
    } catch { setClearError('Could not clear. Please try again.'); return 'failure'; }
    finally {
      setClearing(false);
      if (accepted) settleHistoryChange(current, endHistoryChange);
      else endHistoryChange();
    }
  };

  const headerPage: HeaderPage = correction ? 'correction' : page;
  const headerHome = () => {
    // The shared capture owner survives Home/Chat navigation, so an accepted
    // recording can be viewed from either surface. The question editor does
    // not: unmounting it cancels its review or discards a draft.
    if (homeEnabled) {
      if (reviewing || !!draft) return;
      if (correction && busy) return;
      if (correction) { setCorrection(null); return; }
      if (page !== 'home') { setShowChats(false); setPage('home'); }
      return;
    }
    if (busy) return;
    if (page === 'main' && !correction) void clearAnswer();
    else goBack();
  };

  /**
   * Readiness is re-derived at every awaited step from the *current* reading
   * rather than the one the gesture began with. An activation outlives several
   * status polls, and the lane can be withdrawn underneath it: a start is only
   * ever dispatched against evidence that is still current.
   *
   * The busy input deliberately excludes this screen's own `clearing` flag. The
   * reset this activation runs sets it, and reading it back as "something else is
   * busy" is the ordering bug the reviewer reproduced: `/clear` returned 200 and
   * no `/start` ever followed, because the reset's own completion looked like an
   * external operation still in progress.
   */
  const captureBlocked = chats.selecting || touchBusy || recording || reviewing || !!draft || view.phase === 'processing';
  useEffect(() => {
    // Only the render-derived half. The device reading is written by the poll,
    // which must be able to hand a waiting preparation the exact reading that
    // woke it rather than the one before it.
    liveInputs.current.blocked = captureBlocked;
    liveInputs.current.ownership = captureOwnership;
    liveInputs.current.pending = captureOwnership.pending;
  });
  const currentCapture = () => voiceControlState(
    liveInputs.current.status, liveInputs.current.connected, liveInputs.current.blocked,
    Date.now(), liveInputs.current.ownership,
  );

  /**
   * Resolve on the next validated reading, or after `ms` so a wait is always
   * bounded even when the device stops answering.
   */
  const waitForReading = (after: number, ms: number) => new Promise<void>((resolve) => {
    if (statusReadings.current > after) { resolve(); return; }
    let settled = false;
    const wake = () => { if (settled) return; settled = true; clearTimeout(timer); resolve(); };
    const timer = setTimeout(wake, ms);
    readingWaiters.current.push(wake);
  });

  /**
   * Close the saved-history window on a reading the device produced *after* the
   * clear was accepted, never on the acknowledgement itself.
   *
   * `/clear` is acknowledged before its worker releases the slot. A history read
   * taken inside that window returns the list from before the change — and it
   * also stamps the cache, which then suppresses the next read for
   * HISTORY_REFRESH_MS. Measured: the read landed 867ms after the press, and the
   * conversation the user had just cleared did not appear in Past chats until
   * ~56s. Bounded, so a device that stops answering cannot hold reads shut.
   */
  const settleHistoryChange = (cleared: string | undefined, end: () => void) => {
    const mark = statusReadings.current;
    const started = Date.now();
    const step = () => {
      const live = liveInputs.current.status;
      const movedOn = statusReadings.current > mark
        && (!live || !live.voice.session_id || live.voice.session_id !== cleared);
      if (movedOn || Date.now() - started >= 5000) { end(); return; }
      setTimeout(step, 200);
    };
    step();
  };

  /**
   * Home's intentional activation: prepare the context, then record.
   *
   * The order is the contract's, not this file's invention — check readiness, run
   * the existing New chat policy and *await its result*, check readiness again,
   * and only then ask the capture owner for a session. Every intentional Home
   * start runs that policy, including an idle one: `activation` describes how the
   * control is worded, not whether the context needs preparing, and a thread can
   * be loaded under an idle device. The token is compared after each await so a
   * gesture that has already ended cannot leave a recording behind it.
   */
  /**
   * Say the one thing that is true when a replaced authority invalidated an
   * activation while its start was still in flight.
   *
   * Silence is right when the gesture is simply over — the user navigated away,
   * or the surface unmounted — and wrong when the device token was replaced
   * underneath a user who is still standing there. Measured: a 409 arriving
   * after a token replacement told them nothing at all, nothing was recording,
   * and they believed it was. This writes to the activation surface without the
   * activation token's permission on purpose: it reports a refusal, it does not
   * resume the activation, and the next gesture replaces it.
   */
  const reportSupersededActivation = (issuedToken: string | null) => {
    if (getDeviceToken() === issuedToken) return;
    setActivation({ pending: false, error: START_NOT_STARTED_NOTICE });
  };

  const startNewChat = () => {
    const token = ++activationToken.current;
    // The authority this activation was issued under, so a replacement landing
    // mid-flight is distinguishable from the gesture simply being over.
    const issuedToken = getDeviceToken();
    applyActivation({ pending: true });
    void (async () => {
      const first = currentCapture();
      if (!first.canStart) {
        if (token === activationToken.current) {
          applyActivation({ pending: false, error: first.blockedReason || 'Recording is not available right now.' });
        }
        return;
      }
      const outcome = await clearAnswer();
      if (token !== activationToken.current) {
        reportSupersededActivation(issuedToken);
        return;
      }
      if (outcome !== 'success') {
        applyActivation({ pending: false, error: outcome === 'refused'
          ? 'The device is busy. Try again in a moment.'
          : 'Could not start a new chat, so nothing was recorded.' });
        return;
      }
      // The acknowledgement means the clear was accepted, not that the
      // recording slot is free: the worker releases it afterwards, and a start
      // issued inside that window is refused with 409 voice_busy. So the start
      // waits — boundedly, and cancellably — for a reading the device produced
      // *after* the acknowledgement that shows the slot free.
      const mark = statusReadings.current;
      const startedWaiting = Date.now();
      let slot: SlotWaitOutcome = 'waiting';
      while (slot === 'waiting') {
        slot = resetSlotWait(
          token, activationToken.current, statusReadings.current > mark,
          currentCapture().canStart, Date.now() - startedWaiting,
        );
        if (slot === 'waiting') await waitForReading(statusReadings.current, 250);
      }
      if (slot === 'withdrawn') return;
      if (slot !== 'ready') {
        applyActivation({ pending: false, error: 'The device is still finishing the last question. Try again in a moment.' });
        return;
      }
      const second = currentCapture();
      if (!second.canStart) {
        if (token === activationToken.current) {
          applyActivation({ pending: false, error: second.blockedReason || 'Recording is not available right now.' });
        }
        return;
      }
      /**
       * The session that was published when this start was dispatched.
       *
       * Admission is a *correlation*, not a counter: while the command is in
       * flight the freshest reading may still be this baseline (the device has
       * not caught up) or may positively name the session we are about to be
       * handed. Any third identity is somebody else's session, which is exactly
       * how a foreign session arriving before the acknowledgement used to be
       * adopted once an expectation was outstanding.
       */
      const startingFrom = liveInputs.current.status?.voice.session_id;
      const started = await captureController.current?.start((candidate) => {
        const current = liveInputs.current;
        return current.connected && !!current.status && statusIsFresh(current.status)
          && (current.status.voice.session_id === startingFrom
            || current.status.voice.session_id === candidate.session_id);
      });
      if (token !== activationToken.current) {
        reportSupersededActivation(issuedToken);
        return;
      }
      if (started === 'started' || started === 'withdrawn') { applyActivation(IDLE_ACTIVATION); return; }
      // The runtime refused an active start, or the context could not be
      // compared: say so rather than letting the tap look like nothing happened.
      if (started === 'not-started') { applyActivation({ pending: false, error: START_NOT_STARTED_NOTICE }); return; }
      applyActivation({ pending: false, error: started === 'refused'
        ? 'Already recording.'
        : 'Could not start recording. Try again.' });
    })();
  };

  /**
   * Leave Home for another surface.
   *
   * A *preparation* that Home started belongs to Home, so navigating away
   * withdraws it — and withdrawing it means telling the capture owner, whose
   * start may already be awaiting its acknowledgement. Fencing only this
   * screen's own token is not enough: the owner compares a different token, and
   * that was the reproduced defect where Chat adopted a recording the user had
   * already left Home during.
   *
   * An *accepted* recording is not withdrawn. There is one capture owner above
   * both surfaces, it survives the move, and stopping it because the user looked
   * at Chat would be the second-owner bug in a different costume.
   */
  const leaveHome = (destination: Page) => {
    if (page === 'home') {
      fenceActivation();
      captureController.current?.withdrawStart();
    }
    setPage(destination);
  };

  /**
   * The Home control a menu destination belongs to, when Home has one.
   *
   * Home exposes one tile per HomeDestination plus its Settings gear, so the
   * menu-only destinations — Past questions, Shared knowledge, About — have no
   * Home control at all. Naming one anyway would put focus on a control that
   * never opened the screen.
   */
  const HOME_TARGET_FOR_MENU: Partial<Record<Page, HomeFocusTarget>> = {
    settings: { kind: 'settings' },
    messages: { kind: 'tile', destination: 'messages' },
    status: { kind: 'tile', destination: 'nearby' },
  };

  /**
   * Open a destination from a control in the shared header.
   *
   * Two kinds of entry live in that header: the menu's own items, and the Messages
   * and Nearby buttons that stay reachable on every screen. Both open over whatever
   * screen the user is on, and that screen is the origin — not Chat. Only Settings
   * used to record one; the rest navigated without it, and Nearby, Messages and the
   * header cleared it outright, so Back fell through to Chat and lost the screen
   * the entry was opened over.
   *
   * Opening the menu from Home is a real origin too, and it is preserved rather
   * than flattened: its focus target is Home's own tile or gear, since the menu
   * itself is closed by then.
   *
   * `opener` names the header control the user activated, and is recorded only
   * when that control outlives the journey. The menu button, Messages and Nearby
   * all do. A destination opened from a control *inside* the screen being left —
   * the Settings entries, Agent's Heartbeat button — passes nothing, because that
   * screen unmounts and its replacement takes focus on entry; naming the shared
   * Menu button there would put focus on a control that opened nothing.
   *
   * Re-entering the destination already on screen is a no-op. Recording it as
   * its own origin would leave Back with nowhere to go, and the origin that
   * screen already holds is still the true one. `onOpen` runs only on a real
   * navigation, so a repeated entry cannot reset the screen either.
   *
   * The entry replaces the origin slot, so it takes the origin it displaced with
   * it as `parent`: Settings → a subsection is the only two-step journey in the
   * app, and without this the displaced origin was simply lost.
   *
   * Every entry leaves through `leaveHome`, so a pending Home activation is
   * fenced whichever destination is chosen, not only Settings.
   */
  const openHeaderRoute = (destination: Page, opener?: HeaderFocus, onOpen?: () => void) => {
    setMenuOpen(false);
    if (page === destination) return;
    onOpen?.();
    const homeFocus = HOME_TARGET_FOR_MENU[destination];
    const displaced = backTo.current;
    // This entry's origin replaces whatever was there — one assignment, not a
    // merge — and the origin it displaced travels with it as the parent, so a
    // second Back out of Settings can still find the screen Settings came from.
    backTo.current = {
      ...(page === 'home' && homeFocus ? { page: 'home' as const, homeFocus } : { page, ...(opener ? { focusRef: opener } : {}) }),
      ...(displaced ? { parent: displaced } : {}),
    };
    leaveHome(destination);
  };

  /** The Home tile a route belongs to, so Back can restore focus to it. */
  const TILE_FOR_ROUTE: Record<string, HomeDestination> = {
    messages: 'messages', status: 'nearby', gallery: 'gallery', agent: 'agent',
  };

  const openHomeTile = (destination: HomeDestination) => {
    // The same policy the tiles present. A disabled button is not a guard: a
    // stale render, a synthetic click or a keyboard activation can still reach
    // this handler, and the cost of being wrong is an accepted recording
    // cancelled by the capture owner's own unmount.
    if (busy && !destinationKeepsCapture(destination)) return;
    // Chat is a route like the others, and it used to be the one that recorded
    // no origin at all: Back out of it fell through to the header Home control,
    // which only changes the page and raises no focus request, so the user
    // landed on Home with focus on the document body rather than on the tile
    // they had just left.
    if (destination === 'chat') {
      leaveHome('main');
      backTo.current = { page: 'home', homeFocus: { kind: 'tile', destination: 'chat' } };
      return;
    }
    if (destination === 'vision') {
      leaveHome('camera');
      // The camera is a route like any other, and it used to be the one that
      // recorded no origin at all: Back out of it fell through to Chat and the
      // Vision tile the user left was lost. Home is the origin here, and the
      // tile is what regains focus.
      backTo.current = { page: 'home', homeFocus: { kind: 'tile', destination: 'vision' } };
      return;
    }
    // Messages, Nearby and Gallery are existing screens reached as routes. The
    // origin is recorded so Back returns to the tile the user left instead of
    // dropping them in Chat. Opening any of them sends no message, acknowledges
    // no receipt, announces nothing, pairs nothing, writes no radio and starts
    // no capture — and each may open while its own transport is unavailable,
    // showing its established unavailable state. Being able to open a screen is
    // separate from being able to use what is on it.
    const route: Page | null = destination === 'messages' ? 'messages'
      : destination === 'nearby' ? 'status'
        : destination === 'gallery' ? 'gallery'
          : destination === 'agent' ? 'agent' : null;
    if (!route) return;
    leaveHome(route);
    backTo.current = { page: 'home', homeFocus: { kind: 'tile', destination: TILE_FOR_ROUTE[route] } };
  };

  /**
   * Stage a photo for a question and go to Chat — without recording.
   *
   * Camera's own handoff deliberately starts the microphone; a photo chosen from
   * the gallery must not, because the user has not asked to speak. Staging sets
   * the attachment and leaves the existing voice and typed controls for the user
   * to choose between.
   */
  const stagePhotoInChat = (attachment: PhotoAttachment) => {
    backTo.current = null;
    setPhoto(attachment);
    setPhotoStart(undefined);
    setSessionPhoto(undefined);
    setClearError('');
    showLatest();
    setPage('main');
  };

  // Home shows the real owner's busy signal, so the case the contract calls out
  // — an owned recording with the global busy flag true — is what actually
  // renders on the device rather than a fixture-only simplification.
  const homeBusy = busy || activation.pending;
  const homeFeedback = answerVisible ? text : partialAnswer;
  const homeQuestion = answerVisible || view.phase === 'processing' || interrupted ? currentQuestion ?? '' : '';

  return <main className={`device-screen${page === 'main' ? ' device-chat-active' : ''}${page === 'main' && !correction ? ` device-${view.phase} device-thread-layout` : page === 'settings' ? ` device-${view.phase}` : ''}${page === 'home' && homeEnabled ? ' device-home-active device-thread-layout' : ''}${chatsPinned ? ' device-chats-pinned' : ''}${showChats ? ' device-show-chats' : ''}`} onTouchStart={(event) => { const touch = event.touches[0]; edgeSwipe.current = touch.clientX < 28 ? { x: touch.clientX, y: touch.clientY } : null; }} onTouchEnd={(event) => { const start = edgeSwipe.current; edgeSwipe.current = null; const touch = event.changedTouches[0]; if (start && !busy && page === 'main' && touch.clientX - start.x > 70 && Math.abs(touch.clientY - start.y) < 50) setShowChats(true); }}>
    <DeviceHeader busy={busy} clearing={clearing} page={headerPage} menuOpen={menuOpen} messagesRef={messagesButton} menuRef={menuButton}
      wordmarkStartsChat={!homeEnabled} wordmarkBlocked={homeEnabled && (reviewing || !!draft)}
      nearbyRef={nearbyButton}
      onHome={headerHome} onNearby={() => openHeaderRoute('status', 'nearby')} onMessages={() => openHeaderRoute('messages', 'messages')} onMenu={() => setMenuOpen(true)} />
    {correction && connected ? <QuestionCorrection key={correction.sourceId} {...correction} attempts={correctionAttempts}
      ready={status?.voice.correction_ready === true && !busy && (correction.fromHistory ? canAskAgain : view.phase === 'complete' && status.voice.session_id === correction.sourceId)}
      close={() => setCorrection(null)} submitted={(sessionId, attachment) => { showLatest(); setCorrection(null); setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(attachment ? { ...attachment, sessionId } : undefined); setPage('main'); }} />
      : page === 'settings' ? <SettingsScreen close={goBack} onOpen={(destination) => openHeaderRoute(destination)} profile={profile} onProfileChange={onProfileChange} onLock={onLock} />
      : page === 'messages' ? <MessagesScreen close={goBack} />
      : page === 'sharing' ? <ShareScreen source={shareSource} close={goBack} />
      : page === 'camera' ? <CameraScreen photoReady={connected && status?.voice.photo_ready} onAttach={(image) => { setPhoto(image); setPhotoStart(crypto.randomUUID()); backTo.current = null; setPage('main'); if (status?.voice.session_id) setClearedSession(status.voice.session_id); }} onOpenGallery={() => openRoute('gallery', { page: 'camera', focusRef: 'camera' })} close={goBack} />
      : page === 'history' ? <HistoryScreen close={goBack} canAsk={canAskAgain} onAskAgain={askAgain} onCorrect={connected && status?.voice.correction_ready === true ? (sourceId, question, attachment) => setCorrection({ sourceId, question, photo: attachment, historyPhoto: !!attachment, fromHistory: true }) : undefined} />
      : page === 'status' ? <NearbyScreen status={status} connected={connected} close={goBack} />
      : page === 'gallery' ? <GalleryScreen
        connected={connected}
        busy={busy}
        photoReady={connected && status?.voice.photo_ready === true}
        onOpenCamera={() => openRoute('camera', { page: 'gallery' })}
        onAskAboutPhoto={(attachment) => stagePhotoInChat(attachment)}
        close={goBack}
      />
      : page === 'agent' ? <AgentScreen close={goBack} onOpenHeartbeat={() => openHeaderRoute('heartbeat')} />
      : page === 'heartbeat' ? <HeartbeatScreen close={goBack} />
      : page === 'hardware' ? <HardwareScreen close={goBack} status={status} connected={connected} />
      : page === 'about' ? <AboutScreen close={goBack} />
      : <>
      {showChats && !chatsPinned && <button className="device-chat-scrim" aria-label="Close chats" onClick={closeChats} />}
      <aside id="device-chats" ref={chatDrawer} className="device-chat-list" aria-label="Conversations" inert={menuOpen}>
        <div className="device-drawer-heading"><h2>Conversations</h2><div className="device-drawer-actions"><button type="button" className="device-chats-pin" aria-pressed={chatsPinned} onClick={() => { const pinning = !chatsPinned; setChatsPinned(pinning); if (pinning) setShowChats(false); }}>{chatsPinned ? 'Unpin' : 'Keep open'}</button><button className="device-chats-back" onClick={closeChats}>Close</button></div></div>
        {homeEnabled && <button className="device-new-chat" disabled={busy || !connected || clearing} aria-busy={clearing} onClick={() => void clearAnswer()}>New chat</button>}
        <input className="device-thread-search" type="search" aria-label="Search conversations" placeholder="Search chats" value={chatSearch} onChange={(event) => setChatSearch(event.target.value)} />
        {chats.error && <p role="status">{chats.error} {chats.historyError && <button disabled={chats.refreshing} onClick={() => void savedHistory.refresh(true)}>Try again</button>}</p>}
        {!chats.history && !chats.error && !chats.threads.length && <p role="status">Loading conversations…</p>}
        {chats.current && chats.current.items.length > 4 && <p>For older details, you may need to remind Lantern.</p>}
        <nav aria-label="Past conversation threads">{chats.threads.filter((thread) => thread.items.some((item) => (item.transcript + ' ' + item.response).toLocaleLowerCase().includes(chatSearch.toLocaleLowerCase())) || !chatSearch).map((thread) => <button key={thread.id} disabled={busy || !connected} aria-current={thread.id === chats.current?.thread_id ? 'page' : undefined} onClick={() => {
          if (thread.id === chats.current?.thread_id) setShowChats(false);
          else void chats.select(thread.id).then((ok) => { if (ok) { setQuestionHandoff(null); setPhoto(undefined); setSessionPhoto(undefined); setShowChats(false); readingPosition.current = 0;
            /**
             * The inventory moved: the chat that was open is saved history now and
             * the one just opened is not. Without this read the drawer keeps
             * showing the list from before the switch, so the conversation you
             * left is missing from "Past conversation threads" until something
             * else refreshes it. Measured: after selecting the second of two
             * chats the list held one row - the one now open - and the other
             * thread was absent.
             *
             * Forced, because an unforced refresh returns the cached snapshot when
             * the last read is under HISTORY_REFRESH_MS old (30s) - and the drawer
             * has just read, so it always is. A non-forced call here is a no-op
             * that looks like a fix; measured, the list stayed stale with it.
             */
            void savedHistory.refresh(true); } });
        }}><strong>{thread.title}</strong><small>{thread.items.length} {thread.items.length === 1 ? 'exchange' : 'exchanges'}</small></button>)}</nav>
        {chats.history?.enabled === false && <p>Saving is off. This chat clears when you leave it or lock the device.</p>}
        <button disabled={busy} onClick={() => setPage('history')}>Manage saved history</button>
      </aside>
      {page === 'home' && homeEnabled ? <Home
        status={status}
        connected={connected}
        busy={homeBusy}
        available={HOME_AVAILABLE}
        locked={busy}
        answerText={homeFeedback}
        questionText={homeQuestion}
        streaming={view.phase === 'processing' && !!text}
        incomplete={interrupted}
        captureOwnership={captureOwnership}
        activation={activation}
        focusRequest={homeFocus}
        onStartNewChat={startNewChat}
        onFinishCapture={() => captureController.current?.finish('stop')}
        onCancelCapture={abandonActivation}
        onOpenChat={() => leaveHome('main')}
        onOpenSettings={() => openHeaderRoute('settings')}
        onOpenTile={openHomeTile}
      /> : <Chat
        threadTitle={chats.threads.find((thread) => thread.id === chats.current?.thread_id)?.title}
        onBack={homeEnabled ? () => { if (backTo.current) goBack(); else headerHome(); } : undefined}
        onOpenList={() => { if (chatsPinned) setChatsPinned(false); else setShowChats(true); }}
        listExpanded={listOpen || !narrowChats}
        listControls="device-chats"
        onNewChat={() => void clearAnswer()}
        busy={busy || !connected}
        notice={chatHeaderNotice}
      >
        {photo && <div className="device-photo-attached"><CameraIcon /><span>{photoStart ? 'Starting photo question…' : 'Photo attached · ask when ready'}</span><button disabled={busy} onClick={() => { setPhoto(undefined); setPhotoStart(undefined); }}>Remove</button></div>}
        {clearError && <p className="device-inline-error" role="alert">{clearError}</p>}
        {answerVisible && view.phase === 'complete' && status?.voice.detail && <p className="device-inline-error" role="status">{status.voice.detail}</p>}
        <div className="device-phase" role="status"><span className="device-indicator" aria-hidden="true" />{recording ? 'RECORDING' : reviewing || draft ? 'REVIEW YOUR QUESTION' : view.phase === 'complete' ? 'ANSWER · ON DEVICE' : view.phase === 'ready' ? 'READY · ON DEVICE' : view.phase === 'processing' ? answerVisible ? 'ANSWERING · ON DEVICE' : 'WORKING · ON DEVICE' : 'DEVICE STATUS'}</div>
        <div className={`device-content${displayedPhoto ? ' device-content-photo' : ''}`}>
        {displayedPhoto && <figure className="device-photo-preview"><Image src={`data:image/jpeg;base64,${displayedPhoto.preview}`} width={1280} height={720} unoptimized alt="Photo attached to this question" /><figcaption>Your photo</figcaption></figure>}
        <div id="device-answer" ref={reader} className="device-reader" tabIndex={0} aria-label="Your question and answer" onScroll={(event) => {
          const element = event.currentTarget;
          readingPosition.current = element.scrollTop;
          followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}><ChatExchanges items={(chats.current?.items || []).filter((item) => !turnVisible || item.session_id !== visibleStatus?.voice.session_id)} />
          {turnVisible ? <QuestionTurn key={status?.voice.session_id || 'active'} phase={recordingView.phase} question={currentQuestion}
            answer={answerVisible ? text : partialAnswer} incomplete={interrupted}
            reply={!!chats.current?.items.length} hint={recordingView.hint} edit={editQuestion}
            review={reviewing && status?.voice.session_id && status.voice.review_id ? <QuestionReview
              key={status.voice.review_id} embedded actionsContainer={reviewActions} sessionId={status.voice.session_id} reviewId={status.voice.review_id} question={status.voice.transcript}
              typed={typedReview} hasPhoto={status.voice.has_photo} canRecordAgain={!typedReview && (!status.voice.has_photo || !!displayedPhoto)}
              keyboardContainer={typedReview ? composerKeyboard : null}
              onSubmitAttempt={(submitted) => { showLatest(); setQuestionHandoff({ sessionId: status.voice.session_id!, question: submitted }); }}
              recordAgain={() => { reviewRetry.current = { sessionId: status.voice.session_id!, photo: displayedPhoto }; }} /> : undefined} />
            : (!chats.current?.items.length || view.phase === 'error' || view.phase === 'unavailable') && !partialAnswer && <div className="device-message" aria-live="polite"><h1>{view.title}</h1><p>{view.hint}</p></div>}
          {answerVisible && !draft && suggestedQuestions.length > 0 && <section className="device-suggested-questions" aria-label="Suggested follow-up questions">
            <h2>Explore a little more</h2>
            {suggestedQuestions.map((suggestion) => <button key={suggestion} disabled={!canAskAgain} onClick={() => {
              if (!canAskAgain || !visibleStatus?.voice.session_id) return;
              setSuggestedDraft({ sourceId: visibleStatus.voice.session_id, question: suggestion }); showLatest();
            }}>{suggestion}<span aria-hidden="true">↗</span></button>)}
          </section>}
          {draft && <SuggestedQuestion key={`suggestion-${draft.sourceId}`} {...draft} actionsContainer={reviewActions}
            close={() => setSuggestedDraft(null)}
            askSomethingElse={() => { setSuggestedDraft(null); setPhoto(undefined); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID()); showLatest(); }}
            submitted={(sessionId, submittedQuestion) => { setQuestionHandoff({ sessionId, question: submittedQuestion }); showLatest(); }} />}
        </div>
        </div>
        {view.phase === 'processing' && <AnswerWait key={status?.voice.session_id || 'observed-processing'} stage={status?.voice.progress_stage} expanded={toolbarWaiting} />}
      </Chat>}
      <footer className={`device-footer device-navigation-footer${page === 'main' ? ' device-chat-footer' : ''}${typedReview ? ' device-chat-typing' : ''}${toolbarWaiting ? ' device-toolbar-waiting' : ''}${suggestedQuestions.length ? ' device-has-suggestions' : ''}`} hidden={page === 'home'} inert={toolbarWaiting || menuOpen || (showChats && narrowChats)} aria-hidden={toolbarWaiting || undefined}>
        <div className="device-review-actions" ref={setReviewActions} hidden={!reviewing && !draft} />
        {page !== 'main' && <button ref={historyButton} className="device-history-shortcut" disabled={busy || !connected} onClick={() => setShowChats(true)} aria-controls="device-chats" aria-expanded={showChats || !narrowChats}>Chats</button>}
        <button ref={shareButton} className="device-share-answer" disabled={!canShareAnswer} onClick={() => { if (!canShareAnswer || !status?.voice.session_id) return; setShareSource({ source_id: status.voice.session_id, question: status.voice.transcript, answer: status.voice.response, has_photo: !!status.voice.has_photo }); setPage('sharing'); }}><ShareIcon />Share</button>
        <button ref={cameraButton} className="device-camera-shortcut" disabled={busy || !connected} onClick={() => openRoute('camera', { page: 'main', focusRef: 'camera' })} aria-label="Open camera"><CameraIcon /></button>
        <TalkControls status={status} photo={photo} autoStart={photoStart} onAutoStartHandled={() => setPhotoStart(undefined)} onStarted={(sessionId, attachment) => setSessionPhoto(attachment ? { sessionId, ...attachment } : undefined)} onCancelled={() => { setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined); }} onTypedSession={(sessionId, attachment) => { setQuestionHandoff(null); setPhotoStart(undefined); setSessionPhoto(attachment ? { sessionId, ...attachment } : undefined); setClearError(''); readingPosition.current = 0; followLatest.current = true; showLatest(); }} cancelLabel={displayedPhoto ? 'Back' : 'Cancel'} connected={connected && !clearing && !chats.selecting && !draft && (!photo || !!status?.voice.photo_ready)} compact composer={page === 'main'} onKeyboardContainer={setComposerKeyboard} onBusyChange={setTouchBusy} controllerRef={captureController} onOwnership={setCaptureOwnership} label={suggestedQuestions.length ? 'Ask something else' : chats.current?.items.length || turnVisible ? 'Follow up' : 'Ask'} />
      </footer>
    </>}
    <div className={`device-menu-backdrop${menuOpen ? ' device-menu-open' : ''}`} inert={!menuOpen} aria-hidden={!menuOpen} onClick={(event) => { if (event.target === event.currentTarget) closeMenu(); }}>
      <div ref={menu} role="dialog" aria-modal="true" aria-label="Device menu" className="device-menu" onKeyDown={(event) => {
        if (event.key === 'Escape') closeMenu();
        if (event.key === 'Tab') {
          const buttons = menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
          if (!buttons?.length) return;
          if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons[buttons.length - 1].focus(); }
          else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { event.preventDefault(); buttons[0].focus(); }
        }
      }}>
        <header><h1>Menu</h1><button ref={menuClose} onClick={closeMenu}>Close</button></header>
        <button onClick={() => openHeaderRoute('history', 'menu')}><HistoryIcon /> Past questions</button>
        <button onClick={() => openHeaderRoute('status', 'menu')}><NetworkIcon /> Nearby</button>
        <button onClick={() => openHeaderRoute('settings', 'menu')}><SettingsIcon /> Settings</button>
        <button onClick={() => openHeaderRoute('sharing', 'menu', () => setShareSource(undefined))}><ShareIcon /> Shared knowledge</button>
        <button onClick={() => openHeaderRoute('about', 'menu')}><InfoIcon /> About</button>
        {onLock && <button onClick={onLock}><LockIcon /> Lock</button>}
        <p>Saved questions and device details.</p>
      </div>
    </div>
  </main>;
}
