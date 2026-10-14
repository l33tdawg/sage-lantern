/**
 * Ember — the lock cycle, driven through the real access boundary (disconnected).
 *
 * This renders the **real** `app/device/access.tsx`: the real setup/status read,
 * the real passcode keypad, the real `unlock` -> `activate` -> profile sequence,
 * the real `lock()` and its revocation, and the real `screen.tsx` beneath it.
 * esbuild bundles the submitted source, so what appears in the browser is the
 * reviewed integration rather than a mockup.
 *
 * Why this exists: the brief's acceptance list names "lock/epoch/sleep" as a
 * case the UI evidence must exercise, and every prior increment recorded the
 * lock case as covered *by construction* — the retained partial and the cached
 * threads are component state, and the device screen is rendered only while
 * unlocked, so a lock unmounts them. That is an argument about the code, not a
 * measurement of it. This fixture is the measurement: nothing here is asserted;
 * the driver locks the device through the real control and reads what is left.
 *
 * What is synthetic is the *device service*. `window.fetch` is replaced before
 * anything renders and answers the loopback endpoints the app talks to
 * (`127.0.0.1:3196/*`, `:3199/status`, `:3197/*`, `:3198/*`) from an in-page
 * state machine. **Every other origin is refused and the refusal is recorded**,
 * so this preview cannot reach a live service, a LAN peer or the public
 * internet even by accident. Nothing here claims a live device, a real
 * microphone, a model or a saved conversation.
 */
import { StrictMode, useCallback, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import DeviceAccessBoundary from '../../../apps/lantern-fleet-admin/app/device/access';
import DeviceSetupScreen from '../../../apps/lantern-fleet-admin/app/device/setup';
import { getDeviceToken } from '../../../apps/lantern-fleet-admin/lib/device-session';
import './preview-frame.css';

const PANEL_WIDTH = 720;
const PANEL_HEIGHT = 1560;
/** The passcode the synthetic broker accepts. Long enough for `validDevicePin`. */
const PIN = '13572468';
/** 43 URL-safe characters, which is exactly what `validDeviceToken` accepts. */
const TOKEN = 'z'.repeat(43);
const SESSION_ID = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
/**
 * A real 1x1 JPEG. It satisfies `parseHistoryPhoto` exactly — `/9j/` prefix, a
 * multiple of four, only the base64 alphabet — so a successful read is a read
 * the app actually accepts rather than one it rejects later.
 */
const PHOTO_B64 = '/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAP/////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////////CAAsIAAEAAQEBEQD/xAAUAAEAAAAAAAAAAAAAAAAAAAAD/9oACAEBAAA/AP/Z';

/**
 * The synthetic device service, installed before any app script runs.
 *
 * `window.__STUB__` is the handle the driver reads: `authCalls` is the broker
 * trail, and `state.epoch` is the access epoch. Rotating the epoch is a real
 * authority move as far as the app is concerned, because the app's own rule is
 * that `session_context` changes only when the epoch rotates or the token is
 * replaced.
 */
const STUB = `(() => {
  /**
   * FOR EDITORS - everything to the closing backtick is ONE template literal.
   *
   * Every backtick written inside it must be escaped as \` like this one, and any
   * \${ is a BUILD-TIME interpolation rather than text. A stray unescaped backtick
   * closes the string early and the damage is silent at build time: esbuild exits
   * 0, node --check passes, the driver starts, and only the page fails - with a
   * ReferenceError thrown from half-evaluated stub code. That happened once, from
   * a comment quoting an endpoint. Run probe/smoke.mjs after editing this file.
   */
  const realFetch = window.fetch.bind(window);
  const hex = (char, n) => char.repeat(n);
  const PHOTO = ${JSON.stringify(PHOTO_B64)};
  const state = {
    epoch: hex('a', 32),
    sessionRef: hex('b', 64),
    token: ${JSON.stringify(TOKEN)},
    phase: 'ready',
    /** Every 3196 action in order, so the driver can prove what was called. */
    authCalls: [],
    calls: [],
    blocked: [],
    /** How many leading \`history/photo\` reads fail before one succeeds. */
    photoFails: 0,
    photoCalls: 0,
  };
  window.__STUB__ = state;

  const json = (body, status = 200) =>
    new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

  /**
   * A 3197 reply carrying the authority stamp the client's own fence requires.
   *
   * Protected reads — saved history among them — are refused unless the reply
   * names the authority it was issued under, so a reply without a stamp is not
   * an answer the app will ever adopt.
   */
  const stamped = (body, status = 200) =>
    json({ ...body, session_context: { schema: 'sage.lantern.session-context.v1', epoch: state.epoch, session_ref: state.sessionRef } }, status);

  const context = () => ({ schema: 'sage.lantern.session-context.v1', epoch: state.epoch, session_ref: state.sessionRef });

  const status = () => ({
    schema: 'sage.lantern.local-status.v1',
    observed_at: new Date().toISOString(),
    local_voice_ready: true,
    audio: { present: true, detail: 'USB audio board', ptt_ready: true, ready: true, capture_verified: true },
    radio: { present: true, detail: 'USB online', name: 'Lantern C', frequency_mhz: 915.0, firmware: 'r7' },
    voice: {
      phase: state.phase,
      session_id: ${JSON.stringify(SESSION_ID)},
      transcript: state.phase === 'complete' ? 'What is on this panel?' : '',
      response: state.phase === 'complete' ? 'A retained answer that only an unlocked owner may read.' : '',
      detail: '', touch_ready: true, typed_ready: true, correction_ready: true,
      review_ready: true, photo_ready: true, has_photo: false,
      review_id: null, progress_stage: null, input_source: null,
    },
    session_context: context(),
    services: { llm: 'active', personal_sage: 'active' },
    network: { present: true, detail: 'Wired connection 2' },
    uptime_seconds: 3600,
    temperature_millicelsius: 45000,
    storage_free_bytes: 100000000000,
    storage_total_bytes: 200000000000,
  });

  const profile = () => ({
    version: 1, configured: true, setup_required: false,
    display_name: 'Lantern C', public_discoverable: false, revision: 3,
  });

  /**
   * The broker's answers. \`unlock\` hands out the token; \`activate\`, \`presence\`
   * and \`activity\` authorize it; \`lock\` and \`cancel\` revoke it. \`setup/status\`
   * reports a device that is already set up, so the boundary goes straight to
   * the passcode screen instead of the first-run flow.
   */
  const authReply = (action) => {
    switch (action) {
      case 'setup/status':
        return { enabled: true, recovery_backup_confirmed: true, has_existing_vault: true, can_initialize: false };
      case 'unlock':
        return { token: state.token };
      case 'activate':
      case 'presence':
      case 'activity':
        return { authorized: true };
      case 'profile/get':
      case 'profile/rename':
      case 'profile/setup':
        return profile();
      case 'lock':
        return { locked: true };
      case 'cancel':
        return { cancelled: true };
      case 'setup/enable':
      case 'setup/recovery':
        // The screen validates this with validRecoveryKey (44 characters, base64
        // shape) and refuses anything else, so a placeholder would stop the flow
        // before the backup phase and measure nothing. No template literal here:
        // this file's stub is one, and an unescaped backtick or an unintended
        // interpolation breaks the page while the build still exits 0.
        return { recovery_key: 'A'.repeat(43) + '=' };
      case 'setup/confirm':
        return { recovery_backup_confirmed: true };
      default:
        return {};
    }
  };

  window.fetch = async (input, init) => {
    const url = typeof input === 'string' ? input : (input && input.url) || String(input);
    state.calls.push(url);
    if (url.startsWith('http://127.0.0.1:3196/')) {
      const action = url.slice('http://127.0.0.1:3196/'.length);
      state.authCalls.push(action);
      return json(authReply(action));
    }
    if (url.startsWith('http://127.0.0.1:3199/status')) return json(status());
    /**
     * \`/voice\` is the reading that ESTABLISHES authority, not a second opinion.
     *
     * screen.tsx:377 reads this endpoint and feeds it to observeSessionContext,
     * while /status is telemetry that establishes nothing. This fixture was
     * written on 10 Oct, before the app made that call, so its allow-list refused
     * 3199/voice as a non-device origin: every run since then recorded four
     * blocked requests and failed four scenarios on them, which is a fixture
     * measuring its own staleness. One service answers both endpoints, so this is
     * the same reading under the schema the live-voice parser requires.
     */
    if (url.startsWith('http://127.0.0.1:3199/voice')) return json({ ...status(), schema: 'sage.lantern.voice-status.v1' });
    if (url.startsWith('http://127.0.0.1:3197/')) {
      const action = url.slice('http://127.0.0.1:3197/'.length);
      if (action === 'session/link') return json({ session_id: ${JSON.stringify(SESSION_ID)}, token: 'v'.repeat(24), lease_seconds: 4 });
      if (action === 'history/list') {
        return stamped({
          enabled: true, saving_off_keeps_items: true,
          photo_saving_supported: true, photos_enabled: true,
          items: [{
            session_id: '11111111-2222-3333-4444-555555555555',
            created_at: new Date(Date.now() - 60000).toISOString(),
            transcript: 'What is on this panel?',
            response: 'A retained photo from an earlier question.',
            has_photo: true,
          }],
        });
      }
      if (action === 'history/photo') {
        state.photoCalls += 1;
        if (state.photoCalls <= state.photoFails) return json({ detail: 'photo read failed' }, 503);
        return json({ image_base64: PHOTO });
      }
      return stamped({});
    }
    if (url.startsWith('http://127.0.0.1:3198/')) return new Response(new Uint8Array(0), { status: 200 });
    state.blocked.push(url);
    throw new Error('blocked non-device origin: ' + url);
  };

  // The app locks on \`document.hidden\`. A headless review page can be reported
  // hidden for reasons that have nothing to do with the scenario, which would
  // fire an unrequested lock in the middle of a measurement. Pinning the flag
  // visible keeps every lock in this run one the driver actually asked for.
  try {
    Object.defineProperty(document, 'hidden', { configurable: true, get: () => false });
    Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => 'visible' });
  } catch {}
})();`;

// Install the synthetic service before anything renders. This runs at module
// evaluation, so it precedes the app's first `setup/status` read — the stub has
// to be in place before the access boundary mounts, not after.
(0, eval)(STUB);

function useStubStatus() {
  const [status, setStatus] = useState(() => ({
    locked: true,
    panel: 'lock',
    token: false,
    authCalls: [] as string[],
    blocked: [] as string[],
  }));
  const read = useCallback(() => {
    const stub = (window as unknown as { __STUB__?: { authCalls: string[]; blocked: string[] } }).__STUB__;
    const root = document.getElementById('root');
    const has = (selector: string) => !!root?.querySelector(selector);
    setStatus({
      locked: !getDeviceToken(),
      panel: has('.device-lock') ? 'lock'
        : has('.device-screen.device-home-active') ? 'home'
          : has('.device-screen') ? 'device'
            : has('.device-setup') ? 'setup' : 'unknown',
      token: !!getDeviceToken(),
      authCalls: stub ? [...stub.authCalls] : [],
      blocked: stub ? [...stub.blocked] : [],
    });
  }, []);
  useEffect(() => {
    const interval = setInterval(read, 150);
    read();
    return () => clearInterval(interval);
  }, [read]);
  return status;
}

/**
 * Which real component to mount.
 *
 * `access.tsx` is the lock cycle. `?screen=setup` mounts `setup.tsx`, the
 * first-run screen, which is the ONLY place that passes `allowReveal` to the
 * keypad (its 'choose' and 'confirm' phases) - so it is the only place the
 * passcode reveal control renders, and therefore the only place its touch-target
 * rule can be measured at all. `enabled: false` selects the 'choose' phase.
 */
const SCREEN = new URLSearchParams(window.location.search).get('screen') ?? 'access';
/**
 * Which first-run state to mount.
 *
 * `check` is what a device that cannot initialize shows. `recover` is what an
 * already-enabled device shows, and it is the one first-run state where
 * `allowReveal` is FALSE — so mounting it is also the control proving the reveal's
 * presence comes from that prop and not from the stylesheet.
 */
const VARIANT = new URLSearchParams(window.location.search).get('variant') ?? 'choose';
const SETUP_STATUS = VARIANT === 'check'
  ? { enabled: false, recovery_backup_confirmed: false, has_existing_vault: false, can_initialize: false }
  : VARIANT === 'recover'
    ? { enabled: true, recovery_backup_confirmed: false, has_existing_vault: true, can_initialize: false }
    : { enabled: false, recovery_backup_confirmed: false, has_existing_vault: false, can_initialize: true };

function Fixture() {
  const status = useStubStatus();
  return <div className="preview-shell">
    <output className="preview-notice">
      <strong>SYNTHETIC PREVIEW</strong>
      <span>Lock cycle, driven through the real <code>access.tsx</code> · panel <code>{status.panel}</code> · device token <code>{status.token ? 'held' : 'none'}</code></span>
      <span>Device service simulated in-page; all non-loopback traffic refused.</span>
      <span>broker: <code>{status.authCalls.slice(-8).join(' · ') || 'none yet'}</code> · blocked: <code>{status.blocked.length ? status.blocked.join(', ') : 'none'}</code></span>
    </output>
    <div className="preview-stage">
      <div className="preview-panel">{SCREEN === 'setup'
        ? <DeviceSetupScreen status={SETUP_STATUS} refresh={() => {}} />
        : <DeviceAccessBoundary enabled />}</div>
    </div>
  </div>;
}

const root = document.getElementById('root');
root!.innerHTML = '';
createRoot(root!).render(<StrictMode><Fixture /></StrictMode>);
export { PIN };
