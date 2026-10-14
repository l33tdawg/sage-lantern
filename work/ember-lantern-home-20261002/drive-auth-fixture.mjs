/**
 * Drive the auth-context fence and the refused-start notice in the real preview.
 *
 * This is the increment Rook authorised after the Settings correction: capture
 * the broker's noncredential context at admission, compare it before adoption,
 * before a renewed lease, before a stopped capture is treated as ours, and
 * before a reading is rendered; and refuse a start the runtime declined instead
 * of letting the tap look like nothing happened.
 *
 *   node drive-auth-fixture.mjs [base-url] [label]
 *
 * Writes `auth-fence-results.json` and `screenshots/auth-*.png` beside this
 * file. A label prefixes every output with it instead, so a later run is
 * recorded beside this one. The frozen 42-scenario record and the Settings
 * records are never read or written.
 *
 * What the fixture models: `context=on` publishes the real contract on `/status`
 * (top-level context plus the voice block's own) and attaches the admission
 * context to every successful protected reply. `rotation=status` rotates the
 * epoch with the device token unchanged — a reachable runtime state.
 * `rotation=confirm|keepalive|stop` answers 200 carrying a context the client
 * never admitted under, which the real runtime would not do: those scenarios
 * exist to prove the *client* refuses, not that the server does.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8791/screen-fixture.html';
const LABEL = process.argv[3] ?? '';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9337;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

const RESULTS_FILE = LABEL ? `auth-fence-${LABEL}-results.json` : 'auth-fence-results.json';
const shotFile = (name) => (LABEL ? `auth-${LABEL}-${name}` : `auth-${name}`);

const results = [];
const consoleErrors = [];

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (event) => reject(new Error(`devtools socket error: ${event.message ?? 'unknown'}`)));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') {
        consoleErrors.push(String(message.params?.exceptionDetails?.exception?.description ?? message.params?.exceptionDetails?.text ?? 'exception'));
        return;
      }
      if (message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') {
        consoleErrors.push((message.params.args ?? []).map((arg) => String(arg.value ?? arg.description ?? '')).join(' '));
        return;
      }
      const entry = this.pending.get(message.id);
      if (!entry) return;
      this.pending.delete(message.id);
      if (message.error) entry.reject(new Error(`${message.error.message} (${entry.method})`));
      else entry.resolve(message.result);
    });
  }

  async send(method, params = {}) {
    await this.ready;
    const id = ++this.nextId;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.text} :: ${expression}`);
    return result.result.value;
  }
}

async function devtoolsTarget() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await response.json();
      const page = targets.find((target) => target.type === 'page');
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('Chrome DevTools endpoint never became available');
}

const profile = `/tmp/ember-auth-fixture-${process.pid}`;
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text',
  '--no-sandbox', '--disable-setuid-sandbox', '--disable-crashpad', '--disable-breakpad',
  'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
chrome.stderr.on('data', () => {});

let cdp;
try {
  const target = await devtoolsTarget();
  cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  async function load(query, settle = true) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 820, height: 1700, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Page.navigate', { url: `${BASE}${query}` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      // Either mount counts as ready: the integrated screen, or the capture owner
      // rendered on its own by `owner=1`.
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.preview && !!(document.querySelector(".device-screen") || document.querySelector(".owner-harness"))').catch(() => false);
      if (ready) {
        await cdp.send('Page.bringToFront').catch(() => {});
        await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
        await cdp.evaluate(`(() => { const el = document.querySelector('.preview-log'); if (el) el.style.display = 'none'; })()`).catch(() => {});
        if (settle) await sleep(1400);
        return;
      }
      await sleep(100);
    }
    throw new Error(`preview never mounted for ${query}`);
  }

  async function shot(name, selector = '.device-screen') {
    await cdp.evaluate(`(() => { const n = document.querySelector('.preview-notice'); if (n) n.style.display = 'none'; })()`);
    await sleep(120);
    const box = await cdp.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height), scale: 1 };
    })()`);
    let ok = false;
    if (box && box.width && box.height) {
      const { data } = await cdp.send('Page.captureScreenshot', { format: 'png', clip: box, captureBeyondViewport: true });
      writeFileSync(join(shots, shotFile(name)), Buffer.from(data, 'base64'));
      ok = true;
    }
    await cdp.evaluate(`(() => { const n = document.querySelector('.preview-notice'); if (n) n.style.display = ''; })()`);
    return ok ? `screenshots/${shotFile(name)}` : '';
  }

  const probe = () => cdp.evaluate('window.preview.probe()');
  const commands = (state) => state.log.join(' | ');

  /** What the user can read on Home or Chat, plus what holds focus. */
  const ui = () => cdp.evaluate(`(() => {
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    const root = document.querySelector('.device-screen');
    const active = document.activeElement;
    const notice = root && root.querySelector('.device-talk-status');
    return {
      home: !!document.querySelector('.device-home'),
      chat: !!document.querySelector('.device-main'),
      homeBlocked: [...document.querySelectorAll('.device-home-blocked')].map((el) => clean(el.textContent)),
      talkNotice: notice ? clean(notice.textContent) : null,
      phase: clean(document.querySelector('.device-phase')?.textContent),
      answer: clean(document.querySelector('.device-reader')?.textContent).slice(0, 160),
      homeAnswer: clean(document.querySelector('.device-home-answer')?.textContent).slice(0, 160),
      statusLabel: clean(document.querySelector('.device-home-status-label')?.textContent),
      statusDetail: clean(document.querySelector('.device-home-status-detail')?.textContent),
      answerVisible: !!document.querySelector('.device-home-answer, .device-chat-assistant'),
      recordingActions: !!document.querySelector('.device-recording-actions'),
      homeCancel: !!document.querySelector('.device-home-cancel'),
      active: active ? { tag: active.tagName, className: typeof active.className === 'string' ? active.className : '' } : null,
    };
  })()`);

  const robot = () => cdp.evaluate(`(() => {
    const el = document.querySelector('.device-home-robot') ?? document.querySelector('.device-talk-primary');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled, selector: el.className };
  })()`);

  const press = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  const release = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  const click = async (point) => { await press(point); await sleep(60); await release(point); await sleep(700); };
  const clickSelector = async (selector) => {
    const point = await cdp.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
    })()`);
    if (!point || point.disabled) return null;
    await click(point);
    return point;
  };

  function check(name, ok, detail = '') { return { name, ok: !!ok, detail: String(detail) }; }
  function record(name, checks, screenshot) {
    const labelled = screenshot ? screenshot.replace(/[^/]+$/, (file) => (file.startsWith(`auth-${LABEL}-`) ? file : shotFile(file.replace(/^auth-/, '')))) : screenshot;
    results.push({ scenario: name, screenshot: labelled, checks, passed: checks.every((entry) => entry.ok) });
  }

  /** One tap on the Home voice control, which is the real activation path. */
  async function tapHome() {
    const button = await robot();
    if (!button || button.disabled) return false;
    await click(button);
    return true;
  }

  /**
   * Wait, boundedly, for the device to reach a state the scenario asserts.
   *
   * A fixed sleep is what made an earlier run report "no rotation" for a
   * rotation that had simply not been published yet: the assertion has to wait
   * for the device, and still be bounded so a device that never gets there fails
   * rather than hangs.
   */
  async function waitFor(read, predicate, timeoutMs = 9000) {
    const deadline = Date.now() + timeoutMs;
    let last;
    while (Date.now() < deadline) {
      last = await read();
      if (predicate(last)) return last;
      await sleep(200);
    }
    return last;
  }

  // --- 1. The unrotated case still works ---------------------------------
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&log=1&frame=1');
    const before = await ui();
    const tapped = await tapHome();
    await sleep(900);
    const recording = await ui();
    const state = await probe();
    const shotPath = await shot('01-recording-under-its-own-context.png');
    record('a-context-device-records-normally', [
      check('the context device reaches its home surface', before.home, JSON.stringify(before)),
      check('a tap starts recording under the published context', tapped && recording.recordingActions, `tapped=${tapped} actions=${recording.recordingActions} phase=${recording.phase}`),
      check('the capture was confirmed and not cancelled', state.confirmed === true && !commands(state).includes('POST /cancel'), commands(state)),
      check('no context refusal is shown', !(recording.homeBlocked ?? []).some((line) => /did not start/.test(line)), (recording.homeBlocked ?? []).join(' | ')),
    ], shotPath);
  }

  // --- 2. The epoch rotates while the capture is held --------------------
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=status-held&log=1&frame=1');
    await tapHome();
    const state = await waitFor(probe, (next) => next.log.some((line) => line.includes('epoch rotated')) && next.log.some((line) => line.includes('POST /cancel')));
    const rotated = await ui();
    const shotPath = await shot('02-epoch-rotated-while-recording.png');
    record('a-same-token-epoch-rotation-is-fenced', [
      check('the rotation happened with the device token unchanged', commands(state).includes('epoch rotated'), commands(state)),
      check('the protected recording is no longer presented as ours', !rotated.recordingActions, `actions=${rotated.recordingActions} phase=${rotated.phase}`),
      // The strip stops presenting a usable voice lane. Its exact wording for a
      // fenced block is a copy decision recorded separately (O3), so the assertion
      // is the unambiguous half: the device is no longer advertised as ready.
      check('the surface stops presenting a usable voice lane', !/ready to ask/i.test(rotated.statusLabel), `${rotated.statusLabel} · ${rotated.statusDetail}`),
      check('the capture was cancelled with the credentials it returned', commands(state).includes('POST /cancel → 200'), commands(state)),
    ], shotPath);
  }

  // --- 3. A rotated context on a completed turn clears the protected text --
  {
    // Sampled before the surface settles: the rotation lands on the second
    // reading, so the "before" observation has to be taken as soon as the answer
    // is published rather than after a fixed wait.
    await load('?scenario=complete&mic=on&reset=ok&context=on&rotation=status&log=1&frame=1', false);
    const before = await waitFor(ui, (view) => /Sealed and cool/.test(view.homeAnswer), 6000);
    const state = await waitFor(probe, (next) => next.log.some((line) => line.includes('epoch rotated')));
    await sleep(600);
    const after = await ui();
    const shotPath = await shot('03-answer-cleared-by-rotation.png');
    record('a-rotation-clears-protected-content', [
      check('the answer is on screen under the first context', /Sealed and cool, about six months/.test(`${before.homeAnswer} ${before.answer}`), `${before.homeAnswer} ${before.answer}`),
      check('the rotation was published by status', commands(state).includes('epoch rotated'), commands(state)),
      check('the protected answer is gone afterwards', !/Sealed and cool, about six months/.test(`${after.homeAnswer} ${after.answer}`), `${after.homeAnswer} ${after.answer}`),
      check('the surface is not left presenting the old authority', !/ready to ask/i.test(after.statusLabel), `${after.statusLabel} · ${after.statusDetail}`),
    ], shotPath);
  }

  // --- 4. A reply the client never admitted under is refused -------------
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=confirm&log=1&frame=1');
    await tapHome();
    await sleep(1200);
    const refused = await ui();
    const state = await probe();
    const shotPath = await shot('04-confirm-reply-context-refused.png');
    record('a-confirmation-under-another-context-is-refused', [
      check('the reply arrived with a context the client never admitted under', commands(state).includes('POST /start/confirm → 200'), commands(state)),
      check('the returned credentials were cancelled anyway', commands(state).includes('POST /cancel → 200'), commands(state)),
      check('nothing was adopted', !refused.recordingActions && !state.ownedSession, `actions=${refused.recordingActions} owned=${state.ownedSession}`),
      check('no lease was renewed for it', !commands(state).includes('/keepalive'), commands(state)),
      check('an active activation is told the recording did not start', (refused.homeBlocked ?? []).some((line) => line === 'The recording did not start. Tap to try again.'), (refused.homeBlocked ?? []).join(' | ')),
    ], shotPath);
  }

  // --- 5. A renewed lease under another context stops the recording ------
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=keepalive&log=1&frame=1');
    await tapHome();
    await sleep(1400);
    const stopped = await ui();
    const state = await probe();
    const shotPath = await shot('05-keepalive-context-refused.png');
    record('a-released-lease-under-another-context-stops-the-capture', [
      check('the renewal was attempted under the admitted context', commands(state).includes('POST /keepalive → 200'), commands(state)),
      check('the capture stopped rather than being held', !stopped.recordingActions, `actions=${stopped.recordingActions} phase=${stopped.phase}`),
      check('it was cancelled with its own credentials', commands(state).includes('POST /cancel → 200'), commands(state)),
      check('the user is told the session changed', (stopped.homeBlocked ?? []).some((line) => /device session changed/.test(line)) || /device session changed/.test(stopped.talkNotice ?? ''), `${(stopped.homeBlocked ?? []).join(' | ')} · notice=${stopped.talkNotice}`),
    ], shotPath);
  }

  // --- 6. A refused start is loud when active, silent when withdrawn -----
  {
    // `session-change-silent` takes the device over before any status read can
    // see it, so the runtime refuses the confirmation with 409 while the user is
    // still waiting. Option (b): the active activation is told, and told without
    // a takeover claim.
    await load('?scenario=ready&mic=on&reset=ok&fault=session-change-silent&log=1&frame=1');
    await tapHome();
    await sleep(1400);
    const active = await ui();
    const activeState = await probe();
    const activeShot = await shot('06-refused-start-is-reported.png');
    record('an-active-refused-start-is-reported', [
      check('the runtime refused the confirmation', commands(activeState).includes('start/confirm → 409'), commands(activeState)),
      check('the returned credentials were cancelled', commands(activeState).includes('POST /cancel → 200'), commands(activeState)),
      check('nothing was adopted and no lease was renewed', !activeState.ownedSession && !commands(activeState).includes('/keepalive'), `owned=${activeState.ownedSession}`),
      check('the user is told the recording did not start', (active.homeBlocked ?? []).some((line) => line === 'The recording did not start. Tap to try again.'), (active.homeBlocked ?? []).join(' | ')),
      check('the message claims no takeover', !(active.homeBlocked ?? []).some((line) => /takeover|another|someone|took/i.test(line)), (active.homeBlocked ?? []).join(' | ')),
    ], activeShot);

    // The same refusal with the activation already withdrawn: the message must
    // not appear at all, because nobody is waiting for that recording.
    await load('?scenario=ready&mic=on&reset=ok&fault=session-change-silent&confirmDelay=2500&log=1&frame=1');
    await tapHome();
    // The case under test is a refusal that arrives for a start the user then
    // withdraws — not a withdrawal before the request was ever sent — so the
    // dispatch is awaited first.
    await waitFor(probe, (next) => next.log.some((line) => line.includes('POST /start → 200')), 6000);
    // The withdrawal has to be a real activation of Home's own Cancel control,
    // and it has to have happened before the refusal lands — both are asserted,
    // because a click that missed would make the silence mean nothing.
    const cancelled = await waitFor(() => clickSelector('.device-home-cancel'), (point) => point !== null, 3000);
    const withdrawnShot = await shot('07-withdrawn-refused-start-is-silent.png');
    const withdrawnState = await waitFor(probe, (next) => next.log.some((line) => line.includes('start/confirm → 409')), 9000);
    await sleep(800);
    const withdrawn = await ui();
    const afterShot = await shot('08-withdrawn-refusal-still-silent.png');
    record('a-withdrawn-refused-start-stays-silent', [
      check('Home offered its Cancel control and it was activated', !!cancelled, JSON.stringify(cancelled)),
      check('the refusal still arrived after the withdrawal', commands(withdrawnState).includes('start/confirm → 409'), commands(withdrawnState)),
      check('no refusal message is shown for a withdrawn activation', !(withdrawn.homeBlocked ?? []).some((line) => /did not start/.test(line)), (withdrawn.homeBlocked ?? []).join(' | ')),
      check('the screen is back on Home with nothing recording', withdrawn.home && !withdrawn.recordingActions, `home=${withdrawn.home} actions=${withdrawn.recordingActions}`),
    ], withdrawnShot || afterShot);
  }

  // --- 9 & 10. The capture owner's own fence, with no parent --------------
  //
  // `DeviceScreen` supplies an admission callback and its own epoch fence over
  // `/status`. Both are real, and both would refuse this start for their own
  // reasons — which is exactly why the integrated page cannot show whether the
  // *owner* refuses it. `owner=1` mounts `TalkControls` alone: the readings go to
  // it and nothing else does.
  const owner = () => cdp.evaluate('window.previewOwner && window.previewOwner.probe()');
  const ownerNotice = () => cdp.evaluate(`(() => {
    const el = document.querySelector('.device-talk-status');
    return el ? el.textContent.replace(/\\s+/g, ' ').trim() : null;
  })()`);
  const ROTATED_DURING_START = 'epoch rotated: the broker moved to a new context while /start was in flight';
  const ROTATED_PUBLISHED = 'owner harness published the rotated context while the start reply was still out';
  const ACK_DELIVERED = 'start acknowledgement delivered (not a dispatch)';
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=status-inflight&startDelay=1800&owner=1&log=1&frame=1');
    const tapped = await clickSelector('.device-talk-primary');
    await waitFor(owner, (next) => next.log.some((line) => line.includes(ROTATED_PUBLISHED)), 9000);
    await sleep(2800);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot('09-owner-refuses-a-start-issued-under-a-dead-authority.png', '.owner-harness');
    record('the-owner-refuses-a-start-issued-under-the-old-epoch', [
      check('the owner offered its capture control and it was activated', !!tapped, JSON.stringify(tapped)),
      check('the epoch rotated inside the pending start, on the same device token', after.log.some((line) => line.includes(ROTATED_DURING_START)), commands(after)),
      check('the rotated reading reached the owner before the delayed reply did',
        after.log.indexOf(ROTATED_PUBLISHED) >= 0 && after.log.indexOf(ROTATED_PUBLISHED) < after.log.indexOf(ACK_DELIVERED),
        `rotated@${after.log.indexOf(ROTATED_PUBLISHED)} ack@${after.log.indexOf(ACK_DELIVERED)}`),
      check('nothing was adopted: no ownership, no onStarted', after.ownership?.owned === false && after.started.length === 0,
        `owned=${after.ownership?.owned} started=${after.started.length}`),
      check('the credentials the reply carried were cancelled', after.log.some((line) => line.includes('POST /cancel')), commands(after)),
      check('no lease was renewed for a session that was never adopted', !after.log.some((line) => line.includes('POST /keepalive')), commands(after)),
      check('the activation was told the recording did not start', /did not start/.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
  {
    // The positive control: the identical query without the rotation. If this
    // one did not start, the refusal above would prove nothing about the fence.
    await load('?scenario=ready&mic=on&reset=ok&context=on&startDelay=1800&owner=1&log=1&frame=1');
    const tapped = await clickSelector('.device-talk-primary');
    await waitFor(owner, (next) => next.started.length > 0, 9000);
    await sleep(1600);
    const after = await owner();
    const shotPath = await shot('10-owner-admits-the-same-start-when-the-context-holds.png', '.owner-harness');
    record('the-owner-admits-the-same-start-when-nothing-rotated', [
      check('the same control was activated against the same delayed start', !!tapped, JSON.stringify(tapped)),
      check('the capture was adopted exactly once', after.started.length === 1 && after.ownership?.owned === true,
        `started=${after.started.length} owned=${after.ownership?.owned}`),
      check('the accepted capture was kept alive', after.log.some((line) => line.includes('POST /keepalive')), commands(after)),
      check('nothing was cancelled', !after.log.some((line) => line.includes('POST /cancel')), commands(after)),
    ], shotPath);
  }

  // --- 11 & 12. The poll's ordering at the shared session seam ------------
  //
  // `observeSessionContext` is process-wide: it establishes the authority every
  // protected surface compares against. A reply that outlived the auth session it
  // was issued under must not reach it, or the *next* current reading looks like a
  // rotation and drops content that was never under the dead authority.
  const notifications = () => cdp.evaluate('window.preview.notifications()');
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&statusDelay=1200&tokenSwapAfter=2&log=1&frame=1', false);
    const baseline = await notifications();
    await sleep(6500);
    const after = await probe();
    const notices = await notifications();
    const shotPath = await shot('11-an-obsolete-reply-does-not-move-the-shared-authority.png');
    record('an-obsolete-poll-reply-cannot-move-the-shared-authority', [
      check('the device token was replaced while a reply built under the old one was in flight',
        after.log.some((line) => line.includes('device token replaced')), commands(after)),
      check('the poll kept reading under the replacement authority', after.statusReads >= 3, `statusReads=${after.statusReads}`),
      check('exactly one authority change reached the subscribers: the replacement itself',
        baseline === 0 && notices === 1, `baseline=${baseline} after=${notices}`),
    ], shotPath);
  }
  {
    // The positive control: a rotation this client is actually entitled to see
    // must still notify, exactly once. Without it the count above could be
    // satisfied by a seam that had stopped reporting anything at all.
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=status&log=1&frame=1');
    const rotated = await waitFor(probe, (next) => next.log.some((line) => line.includes('epoch rotated')), 9000);
    await sleep(1600);
    const notices = await notifications();
    const shotPath = await shot('12-a-current-rotation-still-notifies-once.png');
    record('a-current-same-token-rotation-still-notifies-once', [
      check('the same-token rotation was published by status', commands(rotated).includes('epoch rotated'), commands(rotated)),
      check('exactly one authority-change notification reached the subscribers', notices === 1, String(notices)),
    ], shotPath);
  }

  // --- 13 & 14. The protected reads, not only the capture -------------------
  //
  // `owner=1` again: a typed review is opened by the owner itself, and the
  // parent's own fences would refuse it for their own reasons.
  const TYPED_ROTATED = 'epoch rotated: the broker moved to a new context while /question/start was in flight';
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&rotation=typed-inflight&typedDelay=1800&owner=1&log=1&frame=1');
    const tapped = await clickSelector('.device-question-button');
    await waitFor(owner, (next) => next.log.some((line) => line.includes(TYPED_ROTATED)), 9000);
    await sleep(2800);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot('13-owner-refuses-a-typed-review-issued-under-a-dead-authority.png', '.owner-harness');
    record('the-owner-refuses-a-typed-review-issued-under-the-old-epoch', [
      check('the owner offered its Type control and it was activated', !!tapped, JSON.stringify(tapped)),
      check('the epoch rotated inside the pending typed start', after.log.some((line) => line.includes(TYPED_ROTATED)), commands(after)),
      check('no typed session was adopted', after.typed.length === 0, `typed=${after.typed.length}`),
      check('the credentials the review reply carried were cancelled', after.log.some((line) => line.includes('POST /cancel')), commands(after)),
      check('the device session change is what the user is told', /device session changed/i.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
  {
    // Positive control: the same query without the rotation. A typed review the
    // device is entitled to open still opens.
    await load('?scenario=ready&mic=on&reset=ok&context=on&typedDelay=1800&owner=1&log=1&frame=1');
    const tapped = await clickSelector('.device-question-button');
    await waitFor(owner, (next) => next.typed.length > 0, 9000);
    const after = await owner();
    const shotPath = await shot('14-owner-opens-the-typed-review-when-the-context-holds.png', '.owner-harness');
    record('the-owner-opens-the-typed-review-when-nothing-rotated', [
      check('the same control was activated against the same delayed typed start', !!tapped, JSON.stringify(tapped)),
      check('the typed review was adopted exactly once', after.typed.length === 1, `typed=${after.typed.length}`),
      check('nothing was cancelled', !after.log.some((line) => line.includes('POST /cancel')), commands(after)),
    ], shotPath);
  }
  const STATE_ROTATED = 'epoch rotated on the device: the broker publishes a new context for the same device token';
  {
    await load('?scenario=ready&mic=on&reset=ok&context=on&stateDelay=1800&log=1&frame=1');
    await waitFor(probe, (next) => next.statusReads >= 1, 9000);
    // Dispatch the real read, move the epoch underneath it, wait for this
    // screen's own status poll to publish the new context, then let it land.
    await cdp.evaluate('window.preview.beginConversationProbe()');
    await cdp.evaluate('window.preview.rotateAuthority()');
    const rotated = await waitFor(probe, (next) => next.log.some((line) => line.includes(STATE_ROTATED)), 9000);
    await sleep(2600);
    const result = await cdp.evaluate('window.preview.conversationProbe()');
    const shotPath = await shot('15-a-conversation-reply-from-a-dead-authority-is-refused.png');
    record('a-conversation-reply-from-a-dead-authority-is-refused', [
      check('the epoch rotated while the conversation read was in flight', commands(rotated).includes(STATE_ROTATED), commands(rotated)),
      check('the real conversationRequest refused it instead of handing it to the parsers',
        result?.outcome === 'refused', JSON.stringify(result)),
    ], shotPath);
  }
  {
    // Positive control: the same read, nothing moving underneath it. The reply is
    // adopted, and the admission stamp does not travel on into the state.
    await load('?scenario=ready&mic=on&reset=ok&context=on&stateDelay=1800&log=1&frame=1');
    await waitFor(probe, (next) => next.statusReads >= 1, 9000);
    await cdp.evaluate('window.preview.beginConversationProbe()');
    await sleep(2600);
    const result = await cdp.evaluate('window.preview.conversationProbe()');
    const shotPath = await shot('16-a-conversation-reply-under-its-own-authority-is-adopted.png');
    record('a-conversation-reply-under-its-own-authority-is-adopted', [
      check('the real conversationRequest adopted it', result?.outcome === 'adopted', JSON.stringify(result)),
      check('the admission stamp is not handed on as conversation state',
        Array.isArray(result?.keys) && !result.keys.includes('session_context'), JSON.stringify(result?.keys)),
    ], shotPath);
  }

  // --- 17 to 23. The photo-saving policy is a claim of the reply's own ------
  //
  // `/history/list` is where the photo-saving policy comes from, and that read is
  // a protected reply like any other: it carries the policy of the epoch that
  // admitted it. `rotation=photo-inflight` is the reachable state in which the
  // broker has already moved — a lock, owner change or revocation landing before
  // the tap — and this client's next poll has not arrived, so the policy on offer
  // belongs to an authority the activation was never admitted under. `hold=1`
  // keeps the reading at the pre-rotation context: that window is the whole state
  // under test, and holding it is what makes the probe deterministic instead of a
  // race against the poll.
  const PHOTO_OWNER = '?scenario=ready&mic=on&reset=ok&context=on&photos=on&saving=on&owner=1&attach=1&log=1&frame=1';
  const PHOTO_HELD = `${PHOTO_OWNER}&hold=1`;
  const PHOTO_RACE = `${PHOTO_HELD}&rotation=photo-inflight`;
  const PHOTO_ROTATED = 'epoch rotated before /history/list was answered';

  /**
   * Load the capture owner holding a real photo and activate one of its controls.
   *
   * Waiting for the control to be *enabled* is not decoration: a tap taken before
   * the owner's first reading lands would be refused for `issuedUnder ===
   * undefined`, which is a different gate from the one under test, and the
   * scenario would pass without ever reaching it.
   */
  async function armPhotoOwner(query, selector = '.device-talk-primary') {
    await load(query);
    const armed = await waitFor(
      () => cdp.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return el ? { disabled: !!el.disabled } : null; })()`),
      (point) => point !== null && !point.disabled,
    );
    const pressed = await clickSelector(selector);
    return { armed: !!(armed && !armed.disabled), pressed: !!pressed };
  }

  {
    const { armed, pressed } = await armPhotoOwner(PHOTO_RACE);
    await waitFor(owner, (next) => next.historyReads >= 1, 9000);
    await sleep(1200);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot('17-photo-policy-from-another-authority-never-dispatches.png', '.owner-harness');
    record('a-photo-policy-from-another-authority-never-reaches-dispatch', [
      check('the owner offered its capture control and it was activated', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('the policy read was served at all, so the refusal is not an absent test', after.historyReads >= 1, `historyReads=${after.historyReads}`),
      check('the broker had already rotated when that read was answered', after.log.some((line) => line.includes(PHOTO_ROTATED)), commands(after)),
      check('the reading this client holds is still the pre-rotation one',
        after.heldEpoch !== null && after.heldEpoch !== after.brokerEpoch, `held=${after.heldEpoch} broker=${after.brokerEpoch}`),
      check('no start was dispatched on the strength of an unattributable policy', after.startDispatched === false, commands(after)),
      check('no original photo bytes left the device', after.lastStartHadOriginal === false, commands(after)),
      check('the activation is told the recording did not start', /did not start/.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
  {
    // The positive control, differing only in the rotation: the same read, the
    // same held reading, nothing moving underneath it. If this one did not carry
    // the original, the refusal above would prove nothing about the fence.
    const { armed, pressed } = await armPhotoOwner(PHOTO_HELD);
    await waitFor(owner, (next) => next.started.length > 0, 9000);
    await sleep(900);
    const after = await owner();
    const shotPath = await shot('18-photo-policy-under-its-own-authority-retains-the-original.png', '.owner-harness');
    record('a-photo-policy-under-its-own-authority-still-retains-the-original', [
      check('the same control was activated against the same held reading', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('the read was answered under the authority the activation holds',
        after.historyReads >= 1 && after.heldEpoch === after.brokerEpoch, `reads=${after.historyReads} held=${after.heldEpoch} broker=${after.brokerEpoch}`),
      check('the policy it published still let the original travel', after.lastStartHadOriginal === true, commands(after)),
      check('the capture was adopted exactly once', after.started.length === 1 && after.ownership?.owned === true,
        `started=${after.started.length} owned=${after.ownership?.owned}`),
    ], shotPath);
  }
  {
    const { armed, pressed } = await armPhotoOwner(PHOTO_RACE, '.device-question-button');
    await waitFor(owner, (next) => next.historyReads >= 1, 9000);
    await sleep(1200);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot('19-typed-photo-policy-from-another-authority-never-dispatches.png', '.owner-harness');
    record('a-typed-photo-policy-from-another-authority-never-reaches-dispatch', [
      check('the owner offered its Type control and it was activated', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('the policy read was served at all, so the refusal is not an absent test', after.historyReads >= 1, `historyReads=${after.historyReads}`),
      check('the broker had already rotated when that read was answered', after.log.some((line) => line.includes(PHOTO_ROTATED)), commands(after)),
      check('the reading this client holds is still the pre-rotation one',
        after.heldEpoch !== null && after.heldEpoch !== after.brokerEpoch, `held=${after.heldEpoch} broker=${after.brokerEpoch}`),
      check('no typed question was dispatched on the strength of an unattributable policy', after.questionStartDispatched === false, commands(after)),
      check('no original photo bytes left the device', after.lastTypedHadOriginal === false, commands(after)),
      check('the device session change is what the user is told', /device session changed/i.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
  {
    const { armed, pressed } = await armPhotoOwner(PHOTO_HELD, '.device-question-button');
    await waitFor(owner, (next) => next.typed.length > 0, 9000);
    await sleep(900);
    const after = await owner();
    const shotPath = await shot('20-typed-photo-policy-under-its-own-authority-retains-the-original.png', '.owner-harness');
    record('a-typed-photo-policy-under-its-own-authority-still-retains-the-original', [
      check('the same control was activated against the same held reading', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('the policy it published still let the original travel', after.lastTypedHadOriginal === true, commands(after)),
      check('the typed review was adopted exactly once', after.typed.length === 1, `typed=${after.typed.length}`),
    ], shotPath);
  }
  {
    // The declared legacy path: an unlocked device publishes no context at all, so
    // the reply carries none and `null` matches `null`. The policy still decides,
    // and the original still travels — a fence that refused here would have broken
    // every device that has not been updated.
    const { armed, pressed } = await armPhotoOwner('?scenario=ready&mic=on&reset=ok&photos=on&saving=on&owner=1&attach=1&log=1&frame=1');
    await waitFor(owner, (next) => next.started.length > 0, 9000);
    await sleep(900);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot('21-legacy-unlocked-device-still-retains-the-original.png', '.owner-harness');
    record('the-legacy-unlocked-device-still-retains-the-original', [
      check('the same control was activated on a device publishing no context', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('no context was published, so the legacy path is the one under test', after.heldEpoch === null, String(after.heldEpoch)),
      check('the policy still let the original travel', after.lastStartHadOriginal === true, commands(after)),
      check('the capture was adopted exactly once', after.started.length === 1, `started=${after.started.length}`),
      check('no context refusal is shown', !/device session changed|did not start/.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
  for (const stamp of ['missing', 'malformed']) {
    // Declared unreachable, like the `confirm`/`keepalive`/`stop` rotations: the
    // real runtime stamps every successful protected reply through `respond()` and
    // re-checks `context_current` before writing it, so a locked device answers
    // this read with a stamp or not at all. It exists to prove the *client*
    // refuses a policy it cannot attribute instead of trusting the server.
    const { armed, pressed } = await armPhotoOwner(`${PHOTO_OWNER}&historyStamp=${stamp}`);
    await waitFor(owner, (next) => next.historyReads >= 1, 9000);
    await sleep(1200);
    const after = await owner();
    const notice = await ownerNotice();
    const shotPath = await shot(`${stamp === 'missing' ? '22' : '23'}-${stamp}-photo-policy-stamp-is-refused.png`, '.owner-harness');
    record(`a-photo-policy-with-a-${stamp}-admission-stamp-is-refused`, [
      check('the owner offered its capture control and it was activated', armed && pressed, `armed=${armed} pressed=${pressed}`),
      check('the read was served at all, so the refusal is not an absent test', after.historyReads >= 1, `historyReads=${after.historyReads}`),
      check(`the reply really did carry a ${stamp} stamp`,
        after.log.some((line) => line.includes(`history/list → 200 with a ${stamp} admission stamp`)), commands(after)),
      check('no start was dispatched on the strength of it', after.startDispatched === false, commands(after)),
      check('no original photo bytes left the device', after.lastStartHadOriginal === false, commands(after)),
      check('the activation is told the recording did not start', /did not start/.test(notice ?? ''), String(notice)),
    ], shotPath);
  }
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* closing */ }
  chrome.kill('SIGTERM');
}

const failed = results.flatMap((entry) => entry.checks.filter((entry) => !entry.ok).map((entry) => `${entry.scenario}: ${entry.name} — ${entry.detail}`));
writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify({ base: BASE, results, consoleErrors, failed }, null, 2)}\n`);
for (const entry of results) {
  console.log(`${entry.passed ? 'PASS' : 'FAIL'}  ${entry.scenario}  (${entry.checks.filter((c) => c.ok).length}/${entry.checks.length})  ${entry.screenshot}`);
  for (const c of entry.checks) if (!c.ok) console.log(`        ✗ ${c.name} — ${c.detail}`);
}
console.log(`\n${results.filter((entry) => entry.passed).length}/${results.length} scenarios passed.`);
if (consoleErrors.length) console.log(`Console errors: ${consoleErrors.slice(0, 5).join(' | ')}`);
else console.log('Console errors: none');
process.exit(failed.length === 0 && consoleErrors.length === 0 ? 0 : 1);
