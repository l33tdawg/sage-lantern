/**
 * Ember — disconnected Home component fixture (2a review harness).
 *
 * This renders the **real** `app/device/home.tsx` against the **real**
 * `lib/home-navigation.ts` and `lib/device-state.ts`. It is not a mockup and not
 * a reimplementation: esbuild bundles the submitted source, so what appears in
 * the browser is the reviewed component.
 *
 * What is synthetic: the device status. There is no transport, no capture owner,
 * no microphone, no model and no reset. The harness *stands in* for the parent
 * that will own those, and it does so honestly:
 *
 *  - every callback the component makes is recorded, not faked into a screen;
 *  - `parent=slow` models an asynchronous New chat preparation, and when the
 *    component fences a pending start the harness *honours* it — the pending
 *    preparation is abandoned, and a late acknowledgement is then ignored
 *    instead of beginning a recording nobody asked for;
 *  - nothing here persists a task, a photo, a schedule or a detection.
 *
 * Query parameters:
 *   scenario  ready | stale | physical-only | complete | error | listening |
 *             processing | no-mic | restricted | offline
 *   parent    inert (default) | confirm | slow
 *   feedback  none (default) | short | long | streaming | incomplete
 *   log       1 shows the callback trail (used by the gesture evidence)
 *   shell     0 renders Home bare at the viewport size; default renders it in the
 *             real device shell rows — `76px | minmax(0,1fr) | 94px` from
 *             `screen.css` / `portrait.css` — because Home does not own the whole
 *             panel and must not be measured as if it did
 *   frame     1 frames the portrait device inside a desktop browser window
 *             instead of filling it: the panel is 720x1560 portrait and stays
 *             portrait regardless of the window's shape
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import Home from '../../apps/lantern-fleet-admin/app/device/home';
import type { DeviceStatus } from '../../apps/lantern-fleet-admin/lib/device-state';
import { NO_CAPTURE_OWNER, type CaptureOwnership, type HomeDestination } from '../../apps/lantern-fleet-admin/lib/home-navigation';
import './fixture-shell.css';

const PANEL_WIDTH = 720;
const PANEL_HEIGHT = 1560;
const FIXTURE_SESSION = 'fixture-session';

type Scenario =
  | 'ready' | 'stale' | 'physical-only' | 'complete' | 'error'
  | 'listening' | 'processing' | 'no-mic' | 'restricted' | 'offline';
type ParentMode = 'inert' | 'confirm' | 'slow';
type Feedback = 'none' | 'short' | 'long' | 'streaming' | 'incomplete';

const QUESTION = 'How long does stored drinking water keep?';
const SHORT_ANSWER = 'Sealed and cool, about six months; once opened, three days.';
const LONG_ANSWER = 'Sealed, cool and dark, treated water keeps for around six months. '
  + 'Once a container is opened, treat it as fresh food: keep it covered, keep it out of sunlight, '
  + 'and use it within about three days. If you are unsure, boiling is the reliable test — it does not '
  + 'fix every chemical problem, but it removes the biological ones. Store water away from fuel, paint '
  + 'and solvents, because plastic containers take up vapour over time. This paragraph is repeated '
  + 'synthetic fixture text so the panel can be checked with a realistic amount of it. ';

function buildStatus(scenario: Scenario, feedback: Feedback): DeviceStatus | null {
  if (scenario === 'offline') return null;

  const base: DeviceStatus = {
    schema: 'sage.lantern.local-status.v1',
    observed_at: new Date().toISOString(),
    local_voice_ready: true,
    audio: { present: true, detail: 'Synthetic fixture microphone', ready: true, capture_verified: false, ptt_ready: true },
    radio: { present: true, name: 'MeshCore USB', detail: 'Synthetic fixture radio' },
    voice: {
      phase: 'idle',
      transcript: QUESTION,
      response: feedback === 'none' ? '' : feedback === 'short' ? SHORT_ANSWER : LONG_ANSWER.repeat(feedback === 'long' ? 3 : 1),
      touch_ready: true,
    },
    services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
  };

  switch (scenario) {
    case 'stale':
      // The reading the strip will call "too old to trust".
      base.observed_at = new Date(Date.now() - 60_000).toISOString();
      return base;
    case 'physical-only':
      // The spoken lane is genuinely ready through the side button; the touch
      // lane is not. This is the case that must not silently enable the button.
      base.voice.touch_ready = false;
      return base;
    case 'complete':
      base.voice.phase = 'complete';
      base.voice.session_id = 'fixture-session';
      return base;
    case 'error':
      base.voice.phase = 'error';
      base.voice.detail = 'llm_timeout';
      return base;
    case 'listening':
      base.voice.phase = 'listening';
      base.voice.input_source = 'touch';
      base.voice.review_ready = true;
      base.voice.session_id = 'fixture-session';
      return base;
    case 'processing':
      base.voice.phase = 'processing';
      base.voice.progress_stage = 'generating';
      base.voice.session_id = 'fixture-session';
      return base;
    case 'no-mic':
      base.audio.present = false;
      base.audio.ready = false;
      return base;
    default:
      return base;
  }
}

function Fixture() {
  const params = useMemo(() => new URLSearchParams(location.search), []);
  const scenario = (params.get('scenario') ?? 'ready') as Scenario;
  const parentMode = (params.get('parent') ?? 'inert') as ParentMode;
  const feedback = (params.get('feedback') ?? 'none') as Feedback;
  const showLog = params.get('log') === '1';
  const shell = params.get('shell') !== '0';
  const frame = params.get('frame') === '1';
  const [scale, setScale] = useState(1);
  const device = useRef<HTMLDivElement | null>(null);

  const [phase, setPhase] = useState<string>(() => String(buildStatus(scenario, feedback)?.voice.phase ?? 'idle'));
  const [busy, setBusy] = useState(false);
  /**
   * Who holds the recording, as the parent would know it. The `listening`
   * scenario is one this harness's owner already started, so it holds the
   * session the status publishes; every other scenario starts with no owner,
   * which is what makes the component withhold a stop it cannot justify.
   */
  const [ownership, setOwnership] = useState<CaptureOwnership>(() =>
    String(buildStatus(scenario, feedback)?.voice.phase ?? 'idle') === 'listening'
      ? { owned: true, sessionId: FIXTURE_SESSION }
      : NO_CAPTURE_OWNER);
  const [events, setEvents] = useState<string[]>([]);
  const [startedAt] = useState(() => Date.now());

  const pending = useRef(false);
  const abandoned = useRef(false);
  const lateAck = useRef<ReturnType<typeof setTimeout> | null>(null);

  const record = useCallback((name: string) => {
    setEvents((previous) => [...previous, name]);
  }, []);

  // The harness reports its own state so a driver can assert what the parent was
  // left believing, which is where the pending-start race actually shows up.
  useEffect(() => {
    (window as unknown as Record<string, unknown>).fixture = {
      scenario, parent: parentMode, feedback, events, phase, busy,
      abandoned: abandoned.current,
      ownership,
      startedAt,
    };
  }, [scenario, parentMode, feedback, events, phase, busy, ownership, startedAt]);

  const status = useMemo(() => {
    // The harness owns the live turn: it re-derives the status from its own
    // phase so a confirmed capture is visible in the component's real inputs. A
    // live turn must also publish the session the owner was given — a `listening`
    // reading with no session is exactly the unowned case the component is now
    // required to treat as somebody else's recording rather than its own.
    const built = buildStatus(scenario, feedback);
    if (built && phase !== built.voice.phase) {
      built.voice.phase = phase;
      if (phase !== 'idle') built.voice.session_id = FIXTURE_SESSION;
    }
    return built;
  }, [scenario, feedback, phase]);

  const connected = scenario !== 'offline';
  const available: readonly HomeDestination[] = scenario === 'restricted'
    ? ['chat', 'vision']
    : ['chat', 'vision', 'agent', 'gallery'];

  const onStartNewChat = useCallback(() => {
    record('start');
    if (parentMode === 'confirm') {
      // A parent that confirms quickly: the hold becomes a real recording.
      setBusy(true);
      // The acknowledgement: the owner now holds the session the device publishes.
      setTimeout(() => { setPhase('listening'); setOwnership({ owned: true, sessionId: FIXTURE_SESSION }); setBusy(false); }, 60);
      return;
    }
    if (parentMode === 'slow') {
      // An asynchronous New chat preparation. The acknowledgement lands late; if
      // the component fenced the start, it must be ignored.
      pending.current = true;
      setBusy(true);
      lateAck.current = setTimeout(() => {
        lateAck.current = null;
        if (!abandoned.current) { setPhase('listening'); setOwnership({ owned: true, sessionId: FIXTURE_SESSION }); }
        pending.current = false;
        setBusy(false);
      }, 1200);
    }
  }, [parentMode, record]);

  const onFinishCapture = useCallback(() => {
    record('finish');
    // Finishing a recording lands in transcript review, per the brief — not in a
    // fictional saved conversation.
    setPhase((current) => (current === 'listening' ? 'review' : current));
    setOwnership(NO_CAPTURE_OWNER);
  }, [record]);

  const onCancelCapture = useCallback(() => {
    record('cancel');
    if (pending.current) {
      // Honouring the fence: the preparation this start asked for is withdrawn,
      // so the late acknowledgement above can no longer begin a recording.
      pending.current = false;
      abandoned.current = true;
      if (lateAck.current !== null) { clearTimeout(lateAck.current); lateAck.current = null; }
      setBusy(false);
      return;
    }
    setPhase((current) => (current === 'listening' ? 'idle' : current));
    setOwnership(NO_CAPTURE_OWNER);
  }, [record]);

  const answerText = feedback === 'none' ? '' : feedback === 'short' ? SHORT_ANSWER : LONG_ANSWER.repeat(feedback === 'long' ? 3 : 1);

  // Desktop framing: the panel is portrait and stays portrait, so a wide browser
  // window shows a portrait device rather than a landscape app. Scaling is
  // presentation only; at scale 1 the geometry is the real CSS geometry.
  useEffect(() => {
    if (!frame) return;
    const fit = () => {
      const next = Math.min(1,
        (window.innerHeight - 40) / PANEL_HEIGHT,
        (window.innerWidth - 40) / PANEL_WIDTH);
      setScale(next > 0 ? next : 0.1);
    };
    fit();
    window.addEventListener('resize', fit);
    return () => window.removeEventListener('resize', fit);
  }, [frame]);

  const home = <Home
    status={status}
    connected={connected}
    busy={busy}
    captureOwnership={ownership}
    activation={{ pending: busy }}
    available={available}
    answerText={answerText}
    questionText={feedback === 'none' ? '' : QUESTION}
    streaming={feedback === 'streaming'}
    incomplete={feedback === 'incomplete'}
    onStartNewChat={onStartNewChat}
    onFinishCapture={onFinishCapture}
    onCancelCapture={onCancelCapture}
    onOpenChat={() => record('chat')}
    onOpenSettings={() => record('settings')}
    onOpenTile={(destination) => record(`tile:${destination}`)}
  />;

  const log = showLog && <output className="fixture-log" data-events={events.join(',')}>
    <strong>fixture callbacks</strong>
    <span>{events.length === 0 ? 'none' : events.join(' → ')}</span>
    <span className="fixture-log-state">phase {phase} · busy {String(busy)} · abandoned {String(abandoned.current)}</span>
  </output>;

  // The real shell is `76px | minmax(0, 1fr) | 94px`. Home lives in the middle
  // row, so the shell rows are reserved here too: a preview that hands Home the
  // whole 1560px would measure a screen the device never shows.
  const screen = <div className="fixture-screen" ref={device}>
    <div className="fixture-shell-row fixture-shell-top">
      <span>shell header · 76px</span><small>existing app chrome, not part of Home 2a</small>
    </div>
    <div className="fixture-home-region">{home}{log}</div>
    <div className="fixture-shell-row fixture-shell-footer">
      <span>shell footer · 94px</span><small>existing app chrome, not part of Home 2a</small>
    </div>
  </div>;

  if (!frame) return shell ? screen : <>{home}{log}</>;

  return <div className="fixture-desktop">
    <div className="fixture-fit" style={{ width: PANEL_WIDTH * scale, height: PANEL_HEIGHT * scale }}>
      <div className="fixture-device" style={{ transform: `scale(${scale})` }}>{screen}</div>
    </div>
  </div>;
}

createRoot(document.getElementById('root')!).render(<Fixture />);
