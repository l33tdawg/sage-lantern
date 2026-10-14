/**
 * Drive the integrated Screen preview in a real browser.
 *
 * Headless Chrome over the DevTools Protocol, no test framework and no
 * dependencies: real layout, real trusted pointer input, real screenshots, and
 * the real `screen.tsx` → real `TalkControls` → real `Home` component tree. The
 * synthetic device service answers and records; this driver asserts what the
 * *screen* did with it.
 *
 * The three gaps Rook's integration contract named are asserted here as
 * behaviour, not as source text:
 *
 *  1. a finished turn with the microphone gone must not reach `/start`;
 *  2. a `listening` reading this client does not own must expose no GUI stop and
 *     must never issue `/stop`;
 *  3. an owned recording must keep Stop/Cancel visible while the real owner's
 *     global busy flag is true.
 *
 *   node drive-screen-fixture.mjs [base-url] [label]
 *
 * Writes `screenshots/*.png` and `screen-gesture-results.json` beside this file.
 * A label redirects every output to `screen-<label>-*` instead, so a later
 * revision's regression run is recorded beside the frozen 42-scenario record
 * rather than over it:
 *
 *   node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html correction
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8791/screen-fixture.html';
const LABEL = process.argv[3] ?? '';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9407;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

/** Output names, so a labelled run cannot overwrite the frozen record. */
const RESULTS_FILE = LABEL ? `screen-${LABEL}-results.json` : 'screen-gesture-results.json';
const shotFile = (name) => (LABEL ? name.replace(/^screen-/, `screen-${LABEL}-`) : name);
/**
 * Label a recorded screenshot path exactly once.
 *
 * `shot()` returns a path it has already labelled, while some scenarios pass a
 * literal path instead of that return value. Both reach `record()`, so the label
 * is applied here only when the name does not already carry it.
 */
const labelShot = (path) => (LABEL && path ? path.replace(/[^/]+$/, (file) => (file.includes(`-${LABEL}-`) ? file : shotFile(file))) : path);

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

const profile = `/tmp/ember-screen-fixture-${process.pid}`;
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text',
  // Chrome's own sandbox cannot initialise inside a restricted shell on this
  // host. The preview only ever loads the local HTTP origin above, and the page
  // itself refuses every non-loopback request.
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

  async function load(query) {
    // 820x1700 keeps the framed panel at scale 1: the review frame is shown, and
    // the geometry under test is the real 720x1560 CSS geometry.
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 820, height: 1700, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Page.navigate', { url: `${BASE}${query}` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.preview && !!document.querySelector(".device-screen")').catch(() => false);
      if (ready) {
        // A key event can leave the target in a hidden state, and in this harness
        // that state PERSISTS ACROSS navigations. A hidden document is not an
        // incidental detail here: the app is required to drop protected content
        // and to refuse to start a capture while hidden, so a stale hidden flag
        // makes later scenarios assert against correct-but-unexpected behaviour
        // (it silently stopped every start after the first Escape key). Bring the
        // page back to the foreground before anything is measured.
        await cdp.send('Page.bringToFront').catch(() => {});
        await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
        // The transport log is `position: fixed` across the bottom of the
        // viewport, so with `log=1` it sits on top of anything in the panel's
        // last rows — camera tools, viewer actions — and a trusted click there
        // lands on the overlay instead of the control. The log is evidence for
        // humans; every assertion reads it through `probe()`, so removing the
        // overlay costs nothing and keeps click coordinates honest.
        await cdp.evaluate(`(() => { const el = document.querySelector('.preview-log'); if (el) el.style.display = 'none'; })()`).catch(() => {});
        await sleep(1600);
        return;
      }
      await sleep(100);
    }
    throw new Error(`preview never mounted for ${query}`);
  }

  async function shot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, shotFile(name)), Buffer.from(data, 'base64'));
    return `screenshots/${shotFile(name)}`;
  }

  /**
   * Capture one element at its own size.
   *
   * The preview page carries a banner above the panel, so a plain viewport
   * screenshot pushes the bottom of a 1560px panel below the fold. Device
   * evidence has to be the panel, not the panel plus the harness around it.
   */
  async function shotElement(name, selector = '.device-screen') {
    // The banner is harness chrome, not the device, and it occupies part of the
    // stage the panel is centred in — so the panel's own bottom falls outside
    // the clipped region unless it steps aside for the capture. It is hidden
    // only for the moment of capture and restored immediately, and every result
    // is still labelled synthetic in the README and in the unclipped shots.
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
    return ok ? `screenshots/${shotFile(name)}` : shot(name);
  }

  const probe = () => cdp.evaluate('window.preview.probe()');

  /** The screen's own words, read from the real DOM. */
  async function ui() {
    return cdp.evaluate(`(() => {
      const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
      const box = (selector) => {
        const el = document.querySelector(selector);
        if (!el) return null;
        const rect = el.getBoundingClientRect();
        return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2), disabled: !!el.disabled };
      };
      return {
        home: !!document.querySelector('.device-home'),
        chat: !!document.querySelector('.device-main'),
        instruction: text('.device-home-instruction'),
        blocked: text('.device-home-blocked'),
        answer: text('.device-home-answer'),
        // The interruption marker and the live caret are two different claims, so
        // a scenario reads them separately rather than inferring one from the text.
        answerIncomplete: !!document.querySelector('.device-home-incomplete'),
        answerStreaming: !!document.querySelector('.device-home-caret'),
        question: text('.device-home-question'),
        statusLabel: text('.device-home-status-label'),
        statusDetail: text('.device-home-status-detail'),
        robot: box('.device-home-robot'),
        cancel: box('.device-home-cancel'),
        cancelCount: document.querySelectorAll('.device-home-cancel').length,
        cancelScope: document.querySelector('.device-home-cancel')?.dataset?.cancelScope ?? null,
        wordmark: box('.device-wordmark'),
        tiles: [...document.querySelectorAll('.device-home-tile')].map((tile) => ({
          destination: tile.dataset.destination,
          disabled: !!tile.disabled,
          reason: tile.querySelector('.device-home-tile-reason')?.textContent?.trim() ?? null,
          box: (() => { const rect = tile.getBoundingClientRect(); return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) }; })(),
        })),
        footerHidden: !!document.querySelector('.device-navigation-footer')?.hidden,
        footerMounted: !!document.querySelector('.device-navigation-footer .device-talk-controls'),
        newChat: box('.device-new-chat'),
        reviewActions: document.querySelectorAll('.device-review-actions button').length,
        reviewSubmit: box('.device-review-actions .question-review-submit'),
        // Chat's composer. The field is the affordance, the reason is the honest
        // text a disabled control owes a touch panel, and the key count tells an
        // inline keyboard apart from the modal sheet the spoken review still
        // uses - both render the same component.
        composer: box('.device-chat-composer-field'),
        composerLabel: text('.device-chat-composer-field'),
        composerReason: text('.device-chat-composer-reason'),
        composerDisabled: !!document.querySelector('.device-chat-composer-field')?.disabled,
        composerKeys: document.querySelectorAll('.device-chat-composer-keyboard .question-key-row button').length,
        composerDraft: document.querySelector('.device-chat-composer-keyboard textarea')?.value ?? null,
        keyboardSheets: document.querySelectorAll('dialog.question-keyboard-sheet').length,
        chatBack: box('.device-chat-back'),
        chatNewChat: box('.device-chat-new-chat'),
        chatNotice: text('.device-chat-notice'),
        // The gallery/agent bars share the page-bar stylesheet, so a Chat frame
        // that stopped carrying it would show up here rather than only in a
        // screenshot nobody diffs.
        pageBarDisplay: (() => { const bar = document.querySelector('.device-chat-bar'); return bar ? getComputedStyle(bar).display : null; })(),
      };
    })()`);
  }

  const press = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  const release = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  const click = async (point) => {
    // A missing target is a scenario failure to be reported, not a crash that
    // loses the whole run's evidence: the driver writes its results file only
    // after every scenario has recorded.
    if (!point) { results.push({ scenario: 'driver', screenshot: '', checks: [{ name: 'a click target was missing', ok: false, detail: 'the element was not on screen when the scenario tried to click it' }], passed: false }); return; }
    await press(point); await sleep(60); await release(point); await sleep(700);
  };

  /**
   * Wait until the device actually owns a recording.
   *
   * An activation is no longer synchronous: the reset is acknowledged first and
   * its worker releases the recording slot afterwards, so a fixed sleep would
   * either race or silently assert the pre-reset screen. Waiting on the
   * *service's* own account of ownership keeps this deterministic without
   * weakening what is being asserted.
   */
  async function waitForOwned(timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      const state = await probe();
      if (state.ownedSession) return state;
      await sleep(120);
    }
    return probe();
  }

  /**
   * Wait until the *screen* shows the owned recording.
   *
   * Home deliberately only offers a stop once the device publishes the session
   * the owner acknowledged, so there is one status poll between the device
   * owning a recording and the stop affordance appearing. That delay is the
   * anti-fabrication rule working, not a defect, so the driver waits for it
   * rather than asserting into the gap.
   */
  async function waitForRecordingUi(timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    let view = await ui();
    while (Date.now() < deadline) {
      if (view.cancelCount === 1 && (view.instruction ?? '').includes('Done')) return view;
      await sleep(120);
      view = await ui();
    }
    return view;
  }

  /**
   * The gallery lists what the synthetic service returned, which is a read that
   * lands after the destination opens. Reading the grid the instant the screen
   * mounts observes the "Checking saved photos..." state, not a missing feature.
   */
  async function waitForGalleryThumbs(timeoutMs = 15000) {
    const deadline = Date.now() + timeoutMs;
    let view = await galleryUi();
    while (Date.now() < deadline && view.thumbs.length === 0) {
      await sleep(150);
      view = await galleryUi();
    }
    return view;
  }

  function record(name, checks, screenshot) {
    results.push({ scenario: name, screenshot: labelShot(screenshot), checks, passed: checks.every((check) => check.ok) });
    // Written after every scenario, so a later crash cannot erase the record of
    // the scenarios that already ran.
    writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify({
      base: BASE,
      partial: true,
      results,
      consoleErrors,
      failed: results.flatMap((entry) => entry.checks.filter((entryCheck) => !entryCheck.ok).map((entryCheck) => `${entry.scenario}: ${entryCheck.name} — ${entryCheck.detail}`)),
    }, null, 2)}\n`);
  }
  const check = (name, ok, detail = '') => ({ name, ok: !!ok, detail: String(detail) });

  const commands = (state) => state.log.join(' | ');

  /** The gallery's own words and controls, read from the real DOM. */
  const galleryUi = () => cdp.evaluate(`(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
    };
    return {
      open: !!document.querySelector('.device-gallery'),
      title: text('.device-gallery .device-page-title h1'),
      state: text('.device-gallery-state h2'),
      stateReason: text('.device-gallery-state p'),
      note: text('.device-gallery-note'),
      thumbs: [...document.querySelectorAll('.device-gallery-thumb')].map((el) => ({
        session: el.dataset.session,
        image: !!el.querySelector('img'),
        missing: !!el.querySelector('.device-gallery-thumb-missing'),
        box: (() => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })(),
      })),
      viewerImage: !!document.querySelector('.device-gallery-frame img'),
      viewerText: text('.device-gallery-frame p'),
      question: text('.device-gallery-question'),
      position: text('.device-gallery-position'),
      prev: box('.device-gallery-actions button[aria-label="Previous photo"]'),
      next: box('.device-gallery-actions button[aria-label="Next photo"]'),
      ask: box('.device-gallery-primary .device-action-primary'),
      askDisabled: !!document.querySelector('.device-gallery-primary .device-action-primary')?.disabled,
      askReason: text('#gallery-ask-reason'),
      remove: box('.device-gallery-primary .device-action-danger'),
      confirmTitle: text('.device-gallery-confirm h2'),
      confirmBody: text('.device-gallery-confirm p'),
      confirmGo: box('.device-gallery-confirm .device-action-danger'),
      camera: box('.device-gallery-camera'),
    };
  })()`);

  /** The camera's own words and controls, read from the real DOM. */
  const cameraUi = () => cdp.evaluate(`(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
    };
    const canvas = document.querySelector('canvas');
    return {
      open: !!document.querySelector('.device-camera'),
      state: text('.device-camera-state'),
      message: text('.device-camera-message p'),
      painted: !!canvas && canvas.width > 0 && canvas.height > 0,
      frame: canvas && canvas.width ? { w: canvas.width, h: canvas.height } : null,
      shutter: box('.device-camera-tools button'),
      ask: box('.device-camera-tools button[aria-label^="Ask about photo"]'),
      gallery: box('.device-camera-gallery'),
    };
  })()`);

  /** Wait for a camera state, so a step never acts on a transition in flight. */
  async function waitForCamera(predicate, timeoutMs = 8000) {
    const deadline = Date.now() + timeoutMs;
    let view = await cameraUi();
    while (Date.now() < deadline) {
      if (predicate(view)) return view;
      await sleep(150);
      view = await cameraUi();
    }
    return view;
  }

  const openGalleryFromHome = async (query) => {
    await load(query);
    const tile = (await ui()).tiles.find((entry) => entry.destination === 'gallery');
    await click(tile.box);
    await sleep(900);
  };

  /** The Agent screen's own words and controls, read from the real DOM. */
  const agentUi = () => cdp.evaluate(`(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
    };
    const labels = [...document.querySelectorAll('.device-subscreen')].map((el) => el.getAttribute('aria-label'));
    return {
      list: !!document.querySelector('.device-subscreen[aria-label="Agent"]'),
      formLabel: labels.find((label) => label === 'New task' || label === 'Edit task') ?? null,
      title: text('.device-subheader .device-page-title h1'),
      state: text('.device-panel h2'),
      reason: text('.device-panel p'),
      back: box('.device-subheader button'),
      newTask: box('.device-subheader .device-page-bar-action'),
      nameValue: document.querySelector('#agent-task-name')?.value ?? null,
      name: box('#agent-task-name'),
      save: box('.device-subscreen .device-action-primary'),
      refusal: text('#agent-task-refusal'),
      cards: document.querySelectorAll('[aria-label="Scheduled tasks"] li').length,
      heartbeat: box('.device-panel .device-history-setting button'),
    };
  })()`);

  /** A generic read of a Back/title secondary screen, for Heartbeat and Hardware. */
  const pageUi = () => cdp.evaluate(`(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
    const box = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
    };
    const controls = [...document.querySelectorAll('.device-subcontent button')];
    return {
      label: document.querySelector('.device-subscreen')?.getAttribute('aria-label') ?? null,
      title: text('.device-subheader .device-page-title h1'),
      headings: [...document.querySelectorAll('.device-panel h2')].map((el) => el.textContent.trim()),
      paragraphs: [...document.querySelectorAll('.device-subcontent p')].map((el) => el.textContent.trim()),
      body: document.querySelector('.device-subcontent')?.textContent ?? '',
      back: box('.device-subheader button'),
      primary: box('.device-action-primary'),
      controls: controls.length,
      disabledControls: controls.filter((el) => el.disabled).length,
    };
  })()`);

  /**
   * Type into a real field through a real pointer focus.
   *
   * `Input.insertText` is the only way to enter text here that produces the input
   * events React listens for; dispatching synthetic key events would not update a
   * controlled field at all, and the draft would be empty for reasons that have
   * nothing to do with the component.
   */
  const typeInto = async (point, value) => {
    await click(point);
    await cdp.send('Input.insertText', { text: value });
    await sleep(300);
  };

  const openAgentFromHome = async (query) => {
    await load(query);
    const tile = (await ui()).tiles.find((entry) => entry.destination === 'agent');
    await click(tile.box);
    await sleep(900);
  };

  /** The button in the Settings row whose heading is `name`. */
  const settingsRow = (name) => cdp.evaluate(`(() => {
    const row = [...document.querySelectorAll('.device-history-setting')]
      .find((section) => section.querySelector('h2')?.textContent?.trim() === ${JSON.stringify(name)});
    const el = row?.querySelector('button');
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
  })()`);

  /** The live turn in Chat: its answer, whether it is marked, and any placeholder. */
  const turnUi = () => cdp.evaluate(`(() => {
    const text = (selector) => document.querySelector(selector)?.textContent?.trim() ?? null;
    return {
      exchanges: document.querySelectorAll('.device-chat-exchange').length,
      answer: text('.device-flow-answer p'),
      incomplete: !!document.querySelector('.device-answer-incomplete'),
      message: text('.device-message h1'),
    };
  })()`);

  /** Where the reader is, so "released follow" is measured rather than asserted. */
  const readerUi = () => cdp.evaluate(`(() => {
    const el = document.querySelector('#device-answer');
    if (!el) return { scrollTop: null, scrollHeight: null, clientHeight: null };
    return { scrollTop: Math.round(el.scrollTop), scrollHeight: Math.round(el.scrollHeight), clientHeight: Math.round(el.clientHeight) };
  })()`);

  /** A control by selector, scrolled into view so the click lands on it. */
  const boxOf = (selector) => cdp.evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    el.scrollIntoView({ block: 'nearest' });
    const rect = el.getBoundingClientRect();
    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2), disabled: !!el.disabled };
  })()`);

  const openSettingsFromMenu = async () => {
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('button[aria-haspopup="dialog"]'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`));
    await sleep(500);
    await click(await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('.device-menu button')].find((button) => button.textContent.includes('Settings')); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`));
    await sleep(800);
  };

  // --- 0. Diagnostic (opt-in): can the fixture serve a protected read? -----
  /**
   * The gallery's saved-photo list is a protected read: lib/saved-history.ts
   * allows it only when a device token exists AND `document.hidden` is false,
   * and lib/conversation-threads.ts additionally requires an established
   * session authority. When the list is empty, this says which gate refused it
   * rather than leaving it to inference.
   *
   *   DIAGNOSE_PROTECTED_READ=1 node drive-screen-fixture.mjs <url> diag
   */
  if (process.env.DIAGNOSE_PROTECTED_READ) {
    const query = '?scenario=complete&mic=on&reset=ok&photos=three&frame=1';
    await openGalleryFromHome(query);
    const env = await cdp.evaluate(`(() => ({
      hidden: document.hidden,
      visibility: document.visibilityState,
      focus: document.hasFocus(),
      storageKeys: Object.keys(localStorage).length + '/' + Object.keys(sessionStorage).length,
    }))()`);
    await sleep(4000);
    const service = await probe();
    const gal = await galleryUi();
    record('diagnostic-protected-read', [
      check('document/token environment', true, JSON.stringify(env)),
      check('service log (last 14)', true, (service.log ?? []).slice(-14).join(' | ')),
      check('blocked origins', true, (service.blocked ?? []).slice(0, 8).join(' | ')),
      check('gallery state', true, `open=${gal.open} title=${gal.title ?? 'none'} state=${gal.state ?? 'none'} reason=${gal.stateReason ?? 'none'} thumbs=${gal.thumbs.length}`),
    ], await shot('screen-00-diagnostic.png'));
    throw new Error('diagnostic complete: results are in the results file');
  }

  // --- 1. Home renders on the device shell --------------------------------
  /**
   * Opt-in design pass: for each destination, count interactive controls, find
   * any smaller than the brief's 72px floor, and compute the worst text/background
   * contrast ratio from computed styles. Rectangle-based; never innerText.
   *
   *   DIAGNOSE_DESIGN=1 node drive-screen-fixture.mjs <url> design
   */
  if (process.env.DIAGNOSE_DESIGN) {
    const HOME = '?scenario=complete&mic=on&reset=ok&frame=1';
    const measure = () => cdp.evaluate(`(() => {
      const nums = (c) => (String(c || '').match(/[\\d.]+/g) || []).map(Number);
      const onScreen = (el) => {
        const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
        return r.width > 8 && r.height > 8 && cs.visibility !== 'hidden' && cs.display !== 'none' && cs.opacity !== '0';
      };
      const lum = (c) => { const f = (v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); }; return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]); };
      const ratio = (a, b) => { const la = lum(a), lb = lum(b); const hi = Math.max(la, lb), lo = Math.min(la, lb); return (hi + 0.05) / (lo + 0.05); };
      const root = document.querySelector('.device-screen');
      if (!root) return { error: 'no device screen' };
      const rects = [...root.querySelectorAll('button, input, textarea, select, a[href]')].filter(onScreen)
        .map((el) => { const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), label: (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().slice(0, 28) }; });
      const small = rects.filter((t) => t.w < 72 || t.h < 72);
      const bgOf = (el) => { let n = el; while (n) { const c = nums(getComputedStyle(n).backgroundColor); if (c.length >= 3 && (c[3] === undefined || c[3] > 0.9)) return c; n = n.parentElement; } return [11, 24, 32]; };
      const leaves = [...root.querySelectorAll('h1, h2, p, span, button, li')]
        .filter((el) => onScreen(el) && el.children.length === 0 && (el.textContent || '').trim().length > 1);
      let worst = null;
      for (const el of leaves) {
        const fg = nums(getComputedStyle(el).color);
        if (fg.length < 3 || (fg[3] !== undefined && fg[3] < 0.9)) continue;
        const cr = ratio(fg, bgOf(el.parentElement || el));
        if (!worst || cr < worst.ratio) worst = { ratio: Math.round(cr * 100) / 100, text: (el.textContent || '').trim().slice(0, 26), size: getComputedStyle(el).fontSize };
      }
      return {
        controls: rects.length, smallCount: small.length, small: small.slice(0, 4),
        minW: rects.length ? Math.min(...rects.map((t) => t.w)) : null,
        minH: rects.length ? Math.min(...rects.map((t) => t.h)) : null,
        worst, colors: [...new Set(leaves.map((el) => getComputedStyle(el).color))].sort().join(' '),
      };
    })()`);
    const checks = [];
    for (const [name, destination] of [['home', null], ['chat', 'chat'], ['vision', 'vision'], ['gallery', 'gallery'], ['agent', 'agent'], ['messages', 'messages'], ['nearby', 'nearby']]) {
      await load(HOME);
      if (destination) {
        const tile = (await ui()).tiles.find((entry) => entry.destination === destination);
        await click(tile.box);
        await sleep(900);
      }
      const m = await measure();
      checks.push(check(`${name}: >=72px targets, contrast >=4.5`,
        m.smallCount === 0 && (m.worst?.ratio ?? 0) >= 4.5,
        `${m.controls} controls, ${m.smallCount} under 72px (min ${m.minW}x${m.minH}); worst contrast ${m.worst?.ratio} on "${m.worst?.text}" at ${m.worst?.size}; under-72 ${JSON.stringify(m.small)}`));
      await shot(`screen-design-${name}.png`);
    }
    await load(HOME);
    await openSettingsFromMenu();
    const ms = await measure();
    checks.push(check('settings: >=72px targets, contrast >=4.5',
      ms.smallCount === 0 && (ms.worst?.ratio ?? 0) >= 4.5,
      `${ms.controls} controls, ${ms.smallCount} under 72px (min ${ms.minW}x${ms.minH}); worst contrast ${ms.worst?.ratio} on "${ms.worst?.text}" at ${ms.worst?.size}; under-72 ${JSON.stringify(ms.small)}`));
    checks.push(check('palette: every text colour is a brief token', true, `distinct text colours: ${(await measure()).colors}`));
    record('diagnostic-design', checks, await shot('screen-design-settings.png'));
    throw new Error('diagnostic complete: results are in the results file');
  }

  /**
   * Opt-in: independent reproduction of the reported Back-origin loss on the
   * camera/gallery cross-links. Home -> Vision (camera) -> Gallery -> Back ->
   * Back should return to Home; the reported result is Chat.
   *
   *   DIAGNOSE_NAV_ORIGIN=1 node drive-screen-fixture.mjs <url> navorigin
   */
  if (process.env.DIAGNOSE_NAV_ORIGIN) {
    const where = () => cdp.evaluate(`(() => ({
      home: !!document.querySelector('.device-home'),
      chat: !!document.querySelector('.device-chat'),
      gallery: !!document.querySelector('.device-gallery'),
      camera: !!document.querySelector('.device-camera'),
      focus: document.activeElement?.dataset?.destination
        || document.activeElement?.getAttribute('aria-label')
        || document.activeElement?.textContent?.trim().slice(0, 24)
        || document.activeElement?.tagName || null,
    }))()`);
    const press = async (selector) => {
      const box = await cdp.evaluate(`(() => {
        const el = document.querySelector(${JSON.stringify(selector)});
        if (!el) return null;
        const r = el.getBoundingClientRect();
        return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
      })()`);
      if (!box) throw new Error(`no element to press for ${selector}`);
      await click(box);
    };
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const vision = (await ui()).tiles.find((entry) => entry.destination === 'vision');
    await click(vision.box);
    await sleep(900);
    const onCamera = await where();
    await press('.device-camera-gallery');
    await sleep(900);
    const onGallery = await where();
    await press('.device-gallery .device-subheader button');
    await sleep(900);
    const backOnCamera = await where();
    await press('.device-camera header button');
    await sleep(900);
    const afterSecondBack = await where();
    record('diagnostic-nav-origin', [
      check('Home -> Vision opens the camera', onCamera.camera === true, JSON.stringify(onCamera)),
      check('camera -> Gallery opens the gallery', onGallery.gallery === true, JSON.stringify(onGallery)),
      check('first Back returns to the camera', backOnCamera.camera === true, JSON.stringify(backOnCamera)),
      check('second Back returns to HOME', afterSecondBack.home === true, JSON.stringify(afterSecondBack)),
      check('and focus is back on the Vision tile', afterSecondBack.focus === 'vision', String(afterSecondBack.focus)),
    ], await shot('screen-00-nav-origin.png'));
    // Second chain, same diagnostic: Home -> Chat -> Chat's own Back.
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const chatTile = (await ui()).tiles.find((entry) => entry.destination === 'chat');
    await click(chatTile.box);
    await sleep(900);
    const onChat = await where();
    await press('.device-chat-back');
    await sleep(900);
    const afterChatBack = await where();
    record('diagnostic-chat-back-focus', [
      check('Home -> Chat opens chat', onChat.chat === true, JSON.stringify(onChat)),
      check("Chat's own Back returns to Home", afterChatBack.home === true, JSON.stringify(afterChatBack)),
      check('and focus returns to the Chat tile', afterChatBack.focus === 'chat', String(afterChatBack.focus)),
    ], await shot('screen-00-chat-back.png'));
    throw new Error('diagnostic complete: results are in the results file');
  }

  /**
   * Opt-in: where does the conversation list actually live? "Past chats on the
   * left" is a layout claim, and the answer differs by orientation, because
   * useDrawerNavigation treats `(max-width: 760px), (orientation: portrait)` as
   * narrow. Screenshot and measure it at a wide landscape panel.
   *
   *   DIAGNOSE_CHAT_LAYOUT=1 node drive-screen-fixture.mjs <url> chatlayout
   */
  if (process.env.DIAGNOSE_CHAT_LAYOUT) {
    // Metrics FIRST, then load: the app must boot at this size. Overriding the
    // viewport after boot leaves the fixture frame sized for the old one, and a
    // layout measured that way is measuring the harness.
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1024, height: 600, deviceScaleFactor: 1, mobile: false });
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    await sleep(300);
    const chatTile = (await ui()).tiles.find((entry) => entry.destination === 'chat');
    await click(chatTile.box);
    await sleep(1000);
    const layout = await cdp.evaluate(`(() => {
      const el = document.querySelector('#device-chats');
      if (!el) return { present: false };
      const r = el.getBoundingClientRect();
      const cs = getComputedStyle(el);
      return {
        present: true,
        left: Math.round(r.left), right: Math.round(r.right), width: Math.round(r.width),
        visibility: cs.visibility, onScreen: r.width > 0 && r.height > 0 && cs.visibility !== 'hidden',
        transform: cs.transform,
        expanded: document.querySelector('[aria-controls="device-chats"]')?.getAttribute('aria-expanded') ?? null,
        buttons: el.querySelectorAll('button').length,
        panelWidth: window.innerWidth,
      };
    })()`);
    record('diagnostic-chat-list-layout', [
      check('conversation list at 1024x600 landscape', true, JSON.stringify(layout)),
    ], await shot('screen-00-chat-1024x600.png'));
    throw new Error('diagnostic complete: results are in the results file');
  }

  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    const state = await probe();
    const view = await ui();
    const shotPath = await shot('screen-01-home-ready.png');
    const start = await cdp.evaluate('performance.now()');
    record('home-ready', [
      check('Home is the initial screen', view.home && !view.chat),
      check('status strip reports validated readiness', view.statusLabel === 'Ready to ask', view.statusLabel),
      check('central control is offered', view.robot && view.robot.disabled === false),
      check('instruction is the brief wording', view.instruction === 'Tap to talk · hold for push-to-talk', view.instruction),
      check('no stop affordance while idle', view.cancelCount === 0),
      check('every delivered destination is openable', view.tiles.filter((t) => !t.disabled).map((t) => t.destination).join(',') === 'chat,vision,agent,gallery,messages,nearby', view.tiles.map((t) => `${t.destination}:${t.disabled ? 'off' : 'on'}`).join(',')),
      check('the six tiles are in the approved row-major order', view.tiles.map((t) => t.destination).join(',') === 'chat,vision,agent,gallery,messages,nearby', view.tiles.map((t) => t.destination).join(',')),
      // Agent is delivered in 2c, so no tile is left reporting itself undelivered.
      // Its *service* is still unavailable, and that is stated on the screen this
      // tile opens rather than on the tile: opening a destination stays separate
      // from being able to use what is on it.
      check('no tile is left reporting itself undelivered', view.tiles.filter((t) => t.disabled).length === 0, JSON.stringify(view.tiles.map((t) => `${t.destination}:${t.disabled ? 'off' : 'on'}`))),
      check('capture owner is mounted but the footer is hidden', view.footerMounted && view.footerHidden),
      check('no live traffic attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], shotPath);
    void start;
  }

  // --- 2/3. Tap starts an owned recording, second tap finishes it ----------
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    const before = await ui();
    await click(before.robot);
    const afterStart = await waitForOwned();
    const during = await waitForRecordingUi();
    const shotPath = await shot('screen-02-owned-recording.png');
    await click((await ui()).robot);
    const afterStop = await probe();
    await shot('screen-03-review-after-stop.png');
    record('tap-start-then-second-tap-finish', [
      check('one start command was issued', (commands(afterStart).match(/POST \/start → \d{3}/g) || []).length === 1, commands(afterStart)),
      check('every intentional start runs the New chat policy first', commands(afterStart).indexOf('/clear') >= 0 && commands(afterStart).indexOf('/clear') < commands(afterStart).indexOf('/start'), commands(afterStart)),
      check('owned recording keeps an explicit stop affordance', during.cancelCount === 1 && during.instruction?.includes('Done'), `${during.cancelCount} · ${during.instruction}`),
      check('owned recording is not hidden behind the busy flag', during.instruction !== 'Working…', during.instruction),
      check('second tap finishes into review', commands(afterStop).includes('POST /stop'), commands(afterStop)),
      check('no duplicate start from the second press', (commands(afterStop).match(/POST \/start → \d{3}/g) || []).length === 1, commands(afterStop)),
      check('no live traffic attempted', afterStop.blocked.length === 0, afterStop.blocked.join(',')),
    ], shotPath);
  }

  // --- 4. Hold is push-to-talk -------------------------------------------
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    const view = await ui();
    await press(view.robot);
    // Held past the threshold and past the reset's own queued worker, so the
    // release ends a recording that genuinely began rather than fencing one that
    // never did — that case is scenario 9.
    const held = await waitForRecordingUi();
    await release(view.robot);
    await sleep(1200);
    const state = await probe();
    // This scenario named its screenshot but never captured it, so the frozen
    // record pointed at a file that does not exist. Captured here, at the state
    // the checks below assert.
    const shotPath = await shot('screen-04-hold.png');
    record('hold-push-to-talk', [
      check('a hold issues exactly one start', (state.log.join('|').match(/POST \/start → \d{3}/g) || []).length === 1, commands(state)),
      check('holding past the threshold starts capture', held.cancelCount === 1, `${held.cancelCount} · ${held.instruction}`),
      check('releasing ends it into review rather than leaving it open', state.phase === 'review', `${commands(state)} · phase ${state.phase}`),
      check('no live traffic attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], shotPath);
  }

  // --- 5. Somebody else's recording exposes no GUI stop -------------------
  {
    await load('?scenario=listening-physical&frame=1');
    const view = await ui();
    const shotPath = await shot('screen-05-physical-listening.png');
    if (view.robot) { await click(view.robot); }
    const after = await probe();
    const view2 = await ui();
    record('physical-recording-is-not-ours-to-stop', [
      check('the state is still shown', !!view.instruction, view.instruction),
      check('no stop affordance is offered', view.cancelCount === 0 && !view.instruction?.includes('Done'), `${view.cancelCount} · ${view.instruction}`),
      check('no stop command is issued', !commands(after).includes('/stop →'), commands(after)),
      check('no start command is issued either', !commands(after).includes('/start'), commands(after)),
      check('the control is not offered as startable', view.robot?.disabled === true || view2.robot?.disabled === true, `${view.robot?.disabled} / ${view2.robot?.disabled}`),
      check('no live traffic attempted', after.blocked.length === 0, after.blocked.join(',')),
    ], shotPath);
  }

  // --- 6. A finished answer with the microphone gone ----------------------
  {
    await load('?scenario=complete&mic=off&reset=ok&frame=1');
    const view = await ui();
    const shotPath = await shot('screen-06-complete-no-mic.png');
    const state = await probe();
    record('complete-with-microphone-removed', [
      check('the finished answer stays readable without a microphone', (view.answer ?? '').includes('six months'), view.answer),
      check('the voice control is blocked', view.robot?.disabled === true, String(view.robot?.disabled)),
      check('the reason names the microphone', (view.blocked ?? '').includes('Microphone'), view.blocked),
      check('no reset or capture was attempted', !commands(state).includes('/clear →') && !commands(state).includes('/start →'), commands(state)),
    ], shotPath);
  }

  // --- 7/8. A reset that is refused or fails must not record --------------
  for (const [mode, label] of [['refuse', 'reset-refused'], ['fail', 'reset-failed']]) {
    await load(`?scenario=complete&mic=on&reset=${mode}&frame=1`);
    const view = await ui();
    await click(view.robot);
    await sleep(1200);
    const state = await probe();
    const after = await ui();
    const shotPath = await shot(`screen-07-${label}.png`);
    record(label, [
      check('the reset was attempted', commands(state).includes('POST /clear'), commands(state)),
      check('no capture follows a reset that did not succeed', !commands(state).includes('/start →'), commands(state)),
      check('the failure is visible rather than swallowed', !!after.blocked, after.blocked),
    ], shotPath);
  }

  // --- 9. A hold released inside the pending window is fenced -------------
  {
    // The contract's H04: release/cancel before the reset or the start has been
    // acknowledged must not leave a recording running. With the real queued
    // clear there is now a window where the start has not even been dispatched
    // yet, so the invariant is asserted rather than one of its two shapes: a
    // dispatch is allowed, a surviving recording is not.
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=800&startDelay=800&log=1&frame=1');
    const view = await ui();
    await press(view.robot);
    await sleep(700);
    await release(view.robot);
    await sleep(3000);
    const state = await probe();
    const after = await ui();
    const log = commands(state);
    const dispatched = log.includes('POST /start');
    // Missing capture completed here for the same reason as scenario 4.
    const shotPath = await shot('screen-08-withdrawn-start.png');
    record('hold-released-before-start-is-fenced', [
      check('nothing is left recording after the release', state.phase !== 'listening' && state.ownedSession === null, `phase ${state.phase} · owned ${state.ownedSession}`),
      check('a dispatched start was cancelled with its own credentials, not adopted', !dispatched || log.includes('POST /cancel →'), `${dispatched} · ${log}`),
      check('no stop affordance survives the fence', after.cancelCount === 0, `${after.cancelCount}`),
      check('no keepalive keeps a withdrawn session alive', !log.includes('/keepalive →'), log),
      check('no live traffic attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], shotPath);
  }

  // --- 10. Home → Chat → Home keeps the context --------------------------
  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const view = await ui();
    const chatTile = view.tiles.find((tile) => tile.destination === 'chat');
    await click(chatTile.box);
    const onChat = await ui();
    const afterChat = await probe();
    await shot('screen-09-chat-screen.png');
    await click((await ui()).wordmark);
    const backHome = await ui();
    const afterBack = await probe();
    const shotPath = await shot('screen-10-back-on-home.png');
    record('home-chat-home-preserves-context', [
      check('the Chat tile opens the Chat screen', onChat.chat && !onChat.home),
      check('the header returns to Home', backHome.home && !backHome.chat),
      check('navigation never runs the New chat policy', !commands(afterBack).includes('/clear'), commands(afterBack)),
      check('navigation never starts a capture', !commands(afterBack).includes('/start'), commands(afterBack)),
      check('the capture owner was never remounted', backHome.footerMounted),
      check('no live traffic attempted', afterBack.blocked.length === 0, afterBack.blocked.join(',')),
      check('the Chat screen is not a dead end', !!onChat.wordmark, JSON.stringify(onChat.wordmark)),
    ], shotPath);
    void afterChat;
  }


  // --- 12. Reset succeeds and the recording actually starts ---------------
  {
    // The reviewer reproduced this exact case: /clear returned 200 and no /start
    // followed, because the reset's own completion was read back as an external
    // busy signal.
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const view = await ui();
    await click(view.robot);
    const state = await waitForOwned();
    const after = await waitForRecordingUi();
    const shotPath = await shot('screen-12-reset-ok-then-start.png');
    const order = commands(state);
    record('reset-succeeds-then-record', [
      check('the New chat policy ran', order.includes('POST /clear'), order),
      check('the reset was followed by a start', order.includes('POST /start →'), order),
      check('the reset precedes the start', order.indexOf('/clear') < order.indexOf('/start'), order),
      check('no failure message is shown', !after.blocked, after.blocked),
      check('the recording is live', state.phase === 'listening', `phase ${state.phase}`),
      check('the owned recording exposes its stop affordance', after.cancelCount === 1, `${after.cancelCount}`),
      check('no live traffic attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], shotPath);
  }

  // --- 13. Authoritative review routes into Chat --------------------------
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    await click((await ui()).robot);
    const owned = await waitForOwned();
    const during = await waitForRecordingUi();
    await click(during.robot);
    await sleep(2200);
    const onChat = await ui();
    const state = await probe();
    const shotPath = await shot('screen-13-review-in-chat.png');
    record('review-routes-to-chat', [
      check('the capture was owned before the stop', owned.ownedSession !== null, String(owned.ownedSession)),
      check('the stop was sent', commands(state).includes('POST /stop'), commands(state)),
      check('the authoritative review lands in Chat, not Home', onChat.chat && !onChat.home, `chat=${onChat.chat} home=${onChat.home}`),
      check('the review editor is present', onChat.reviewActions > 0, `actions=${onChat.reviewActions}`),
      check('routing never ran the New chat policy again', (commands(state).match(/POST \/clear/g) || []).length === 1, commands(state)),
      check('no live traffic attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], shotPath);
  }

  // --- 13b. Chat's composer opens the admitted typed review, inline ------
  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const tile = (await ui()).tiles.find((entry) => entry.destination === 'chat');
    await click(tile.box);
    const idle = await ui();
    const arrived = await probe();
    await click(idle.composer);
    await sleep(1400);
    const opened = await ui();
    const started = await probe();
    const shotPath = await shot('screen-13b-composer-inline-keyboard.png');
    record('chat-composer-opens-the-typed-review-inline', [
      check('the composer is a labelled field in Chat', idle.composer !== null && idle.composerLabel === 'Type a message', `${idle.composerLabel} @ ${JSON.stringify(idle.composer)}`),
      check('an available composer is enabled and claims nothing is wrong', !idle.composerDisabled && idle.composerReason === null, `disabled=${idle.composerDisabled} reason=${idle.composerReason}`),
      check('the page bar is the flex row it is styled as', idle.pageBarDisplay === 'flex', String(idle.pageBarDisplay)),
      check('Chat carries Back, Chats and New chat together', idle.chatBack !== null && idle.chatNewChat !== null && idle.newChat !== null, `${JSON.stringify(idle.chatBack)} ${JSON.stringify(idle.chatNewChat)}`),
      check('arriving in Chat dispatches nothing', !commands(arrived).includes('/question/start'), commands(arrived)),
      check('the composer opens the admitted typed lane', commands(started).includes('POST /question/start → 200'), commands(started)),
      check('opening it never runs the New chat policy', !commands(started).includes('POST /clear'), commands(started)),
      check('the keyboard renders inline in the composer', opened.composerKeys >= 30, `keys=${opened.composerKeys}`),
      check('no modal keyboard sheet is used', opened.keyboardSheets === 0, `sheets=${opened.keyboardSheets}`),
      check('the review keeps its own submit action', opened.reviewSubmit !== null, JSON.stringify(opened.reviewSubmit)),
      check('no live traffic attempted', started.blocked.length === 0, started.blocked.join(',')),
    ], shotPath);
  }

  // --- 13c. The composer submits what the keyboard typed -----------------
  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
    await click((await ui()).composer);
    await sleep(1400);
    // Two letters off the real key grid, so the draft under test came from the
    // keyboard rather than from anything the fixture typed on the user's behalf.
    for (const letter of ['h', 'i']) {
      const key = await cdp.evaluate(`(() => {
        const button = [...document.querySelectorAll('.device-chat-composer-keyboard .question-key-row button')]
          .find((entry) => entry.textContent.trim() === ${JSON.stringify(letter)});
        if (!button) return null;
        const rect = button.getBoundingClientRect();
        return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
      })()`);
      await click(key);
      await sleep(220);
    }
    const typed = await ui();
    const typedShot = await shot('screen-13c-composer-typed-draft.png');
    const submit = typed.reviewSubmit;
    await click(submit);
    await sleep(1400);
    const submitted = await probe();
    record('chat-composer-submits-what-the-keyboard-typed', [
      check('the keys wrote into the composer draft', typed.composerDraft === 'hi', `draft=${JSON.stringify(typed.composerDraft)}`),
      check('the draft did not leave the inline keyboard', typed.keyboardSheets === 0, `sheets=${typed.keyboardSheets}`),
      check('the submission was dispatched', commands(submitted).includes('POST /review/submit → 200'), commands(submitted)),
      check('the submission carried the typed text', submitted.submittedQuestion === 'hi', `question=${JSON.stringify(submitted.submittedQuestion)}`),
      check('the thread was not reset to send it', !commands(submitted).includes('POST /clear'), commands(submitted)),
      check('no live traffic attempted', submitted.blocked.length === 0, submitted.blocked.join(',')),
    ], typedShot);
  }

  // --- 13d. A composer that cannot open says why -------------------------
  {
    await load('?scenario=complete&mic=on&reset=ok&typed=off&frame=1');
    await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
    const view = await ui();
    const shotPath = await shot('screen-13d-composer-reason.png');
    record('chat-composer-states-its-reason', [
      check('the composer is present', view.composer !== null, JSON.stringify(view.composer)),
      check('it is disabled when the lane is not admitted', view.composerDisabled === true, `disabled=${view.composerDisabled}`),
      check('and it says why in visible text', !!view.composerReason && view.composerReason.length > 8, JSON.stringify(view.composerReason)),
    ], shotPath);
  }

  // --- 13e. The composer fits the narrow panels --------------------------
  {
    const checks = [];
    let narrowShot = '';
    for (const [width, height] of [[480, 1040], [420, 900]]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: true });
      await cdp.send('Page.navigate', { url: `${BASE}?scenario=complete&mic=on&reset=ok&frame=0` });
      for (let attempt = 0; attempt < 60; attempt += 1) {
        if (await cdp.evaluate('!!document.querySelector(".device-home-tile")').catch(() => false)) break;
        await sleep(100);
      }
      await sleep(1400);
      await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
      const fit = await cdp.evaluate(`(() => {
        const panel = document.querySelector('.device-screen').getBoundingClientRect();
        const field = document.querySelector('.device-chat-composer-field')?.getBoundingClientRect();
        const footer = document.querySelector('.device-navigation-footer')?.getBoundingClientRect();
        const bar = document.querySelector('.device-chat-bar')?.getBoundingClientRect();
        const back = document.querySelector('.device-chat-back')?.getBoundingClientRect();
        if (!field || !footer || !bar) return null;
        return {
          panel: { right: Math.round(panel.right), bottom: Math.round(panel.bottom) },
          footerBottom: Math.round(footer.bottom),
          field: { w: Math.round(field.width), h: Math.round(field.height), right: Math.round(field.right) },
          barHeight: Math.round(bar.height),
          backH: back ? Math.round(back.height) : null,
        };
      })()`);
      if (!fit) { checks.push(check(`${width}x${height}: the composer is on screen`, false, 'no composer found')); continue; }
      checks.push(check(`${width}x${height}: the footer stays inside the panel`, fit.footerBottom <= fit.panel.bottom, JSON.stringify(fit)));
      checks.push(check(`${width}x${height}: the field keeps a 72px target`, fit.field.h >= 72 && fit.field.w >= 120, JSON.stringify(fit.field)));
      checks.push(check(`${width}x${height}: the field is not clipped sideways`, fit.field.right <= fit.panel.right, JSON.stringify(fit.field)));
      checks.push(check(`${width}x${height}: the header stays one row`, fit.barHeight < 100, `bar=${fit.barHeight}px back=${fit.backH}px`));
      if (width === 420) {
        // Photograph the composer, not the banner's leftovers. With `frame=0`
        // the preview banner sits above a panel that is a full 100dvh tall, so
        // the panel's bottom - where the composer is - falls below the fold and
        // a viewport screenshot ends in empty space. Translating the panel up by
        // exactly its overflow is a paint-only move (no layout, no media query)
        // and puts the composer's own row back inside the capture.
        await cdp.evaluate(`(() => {
          const panel = document.querySelector('.device-screen');
          const rect = panel.getBoundingClientRect();
          panel.style.transform = 'translateY(-' + Math.max(0, Math.round(rect.bottom - innerHeight)) + 'px)';
        })()`);
        await sleep(150);
        narrowShot = await shot('screen-13e-composer-narrow.png');
        await cdp.evaluate(`(() => { document.querySelector('.device-screen').style.transform = ''; })()`);
      }
    }
    record('the-composer-fits-the-narrow-panels', checks, narrowShot);
  }

  // --- 14. The drawer never covers Home ----------------------------------
  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const drawer = await cdp.evaluate(`(() => {
      const el = document.querySelector('.device-chat-list');
      if (!el) return { present: false };
      const style = getComputedStyle(el);
      const rect = el.getBoundingClientRect();
      const home = document.querySelector('.device-home')?.getBoundingClientRect();
      return { present: true, display: style.display, visibility: style.visibility, width: Math.round(rect.width), height: Math.round(rect.height), homeTop: home ? Math.round(home.top) : null, homeHeight: home ? Math.round(home.height) : null };
    })()`);
    const view = await ui();
    const shotPath = await shot('screen-14-home-drawer-hidden.png');
    record('conversation-drawer-does-not-cover-home', [
      check('the drawer is not displayed on Home', drawer.present ? drawer.display === 'none' : true, JSON.stringify(drawer)),
      check('Home owns the panel below the header', view.home && view.robot !== null && view.robot.y < 1560, JSON.stringify(view.robot)),
      check('the tiles are still inside the panel', view.tiles.length === 6, `${view.tiles.length}`),
    ], shotPath);
  }

  // --- 15. A recording survives viewing the other surface -----------------
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    await click((await ui()).robot);
    const owned = await waitForOwned();
    const recording = await waitForRecordingUi();
    const chatTile = recording.tiles.find((tile) => tile.destination === 'chat');
    await click(chatTile.box);
    await sleep(900);
    const onChat = await ui();
    const after = await probe();
    await shot('screen-15-recording-survives-navigation.png');
    await click((await ui()).wordmark);
    await sleep(700);
    const backHome = await ui();
    const final = await probe();
    record('viewing-another-surface-does-not-stop-a-recording', [
      check('the recording was live before navigating', recording.cancelCount === 1, `${recording.cancelCount}`),
      check('the Chat tile opens Chat while recording', onChat.chat && !onChat.home),
      check('navigation did not stop the recording', !commands(after).includes('/stop →'), commands(after)),
      check('no second start was issued', (commands(after).match(/POST \/start → \d{3}/g) || []).length === 1, commands(after)),
      check('returning to Home still shows the owned recording', backHome.home && backHome.cancelCount === 1, `${backHome.home} ${backHome.cancelCount}`),
      check('no live traffic attempted', final.blocked.length === 0, final.blocked.join(',')),
    ], 'screenshots/screen-15-recording-survives-navigation.png');
  }

  // --- 16. A loaded thread is preserved across Home and Chat --------------
  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const tile = (await ui()).tiles.find((entry) => entry.destination === 'chat');
    await click(tile.box);
    await sleep(900);
    const inChat = await cdp.evaluate(`(() => {
      const reader = document.querySelector('#device-answer');
      return { exchanges: document.querySelectorAll('.device-chat-exchange').length, scrollTop: reader ? Math.round(reader.scrollTop) : null };
    })()`);
    await shot('screen-16-loaded-thread.png');
    await click((await ui()).wordmark);
    await sleep(700);
    const state = await probe();
    await click(((await ui()).tiles.find((entry) => entry.destination === 'chat')).box);
    await sleep(700);
    const back = await cdp.evaluate(`(() => {
      const reader = document.querySelector('#device-answer');
      return { exchanges: document.querySelectorAll('.device-chat-exchange').length, scrollTop: reader ? Math.round(reader.scrollTop) : null };
    })()`);
    record('loaded-thread-survives-navigation', [
      check('the saved exchange is rendered in Chat', inChat.exchanges >= 1, JSON.stringify(inChat)),
      check('it is still there after Home and back', back.exchanges >= 1, JSON.stringify(back)),
      check('navigation never ran the New chat policy', !commands(state).includes('/clear →'), commands(state)),
      check('navigation never started a capture', !commands(state).includes('/start →'), commands(state)),
    ], 'screenshots/screen-16-loaded-thread.png');
  }

  // --- 17. A queued clear holds the recording slot ------------------------
  {
    await load('?scenario=complete&mic=on&reset=ok&clearDelay=1500&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    // ~700ms in: the clear has been acknowledged, its worker has not finished,
    // so the recording slot is still reserved.
    const midway = await probe();
    const midView = await ui();
    const midShot = await shotElement('screen-17-queued-clear-pending.png');
    record('queued-clear-holds-the-slot', [
      check('the reset was acknowledged rather than awaited', commands(midway).includes('clearing'), commands(midway)),
      check('no start is dispatched while the worker still holds the slot', !commands(midway).includes('/start'), commands(midway)),
      check('the start was not refused by the device either', !commands(midway).includes('voice_busy'), commands(midway)),
      check('a Cancel is offered while the start is pending', midView.cancelCount === 1 && midView.cancelScope === 'preparing', `${midView.cancelCount} · ${midView.cancelScope}`),
      check('the screen says what it is waiting for', (midView.blocked ?? '').includes('Preparing'), midView.blocked),
    ], midShot);
    await sleep(2800);
    const after = await probe();
    const afterView = await ui();
    await shotElement('screen-17b-queued-clear-started.png');
    record('queued-clear-then-starts-on-a-fresh-reading', [
      check('the start follows the worker finishing', commands(after).includes('/start'), commands(after)),
      check('it was never refused with 409 voice_busy', !commands(after).includes('voice_busy'), commands(after)),
      check('exactly one start was issued', (commands(after).match(/POST \/start → \d{3}/g) || []).length === 1, commands(after)),
      check('the capture is owned after the wait', after.ownedSession !== null && after.phase === 'listening', `${after.ownedSession} · ${after.phase}`),
      check('the rejected start is not reported as a failure', !(afterView.blocked ?? '').includes('Could not start'), afterView.blocked),
      check('the active thread is new and empty', Array.isArray(after.activeItems) && after.activeItems.length === 0, JSON.stringify(after.activeItems)),
      check('what the clear removed is retained as history, not deleted', after.retiredItems.length >= 1, String(after.retiredItems?.length)),
    ], 'screenshots/screen-17b-queued-clear-started.png');
  }

  // --- 18. A pending start is withdrawn when the user leaves Home ---------
  {
    await load('?scenario=ready&mic=on&reset=ok&startDelay=1500&clearDelay=0&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    const midView = await ui();
    // Still inside the delayed acknowledgement: leave Home for Chat.
    const chatTile = (await ui()).tiles.find((tile) => tile.destination === 'chat');
    await click(chatTile.box);
    await sleep(3000);
    const state = await probe();
    const after = await ui();
    await shotElement('screen-18-late-start-withdrawn.png');
    record('pending-start-withdrawn-when-leaving-home', [
      check('a Cancel was offered during the pending start', midView.cancelCount === 1 && midView.cancelScope === 'preparing', `${midView.cancelCount} · ${midView.cancelScope}`),
      check('a start had been dispatched', commands(state).includes('/start'), commands(state)),
      check('leaving Home withdrew it at the capture owner', commands(state).includes('/cancel →'), commands(state)),
      check('the late acknowledgement was cancelled, not adopted', state.ownedSession === null, String(state.ownedSession)),
      check('the device is not left recording', state.phase !== 'listening', state.phase),
      check('no keepalive keeps a withdrawn recording alive', !commands(state).includes('/keepalive'), commands(state)),
      check('exactly one start was issued', (commands(state).match(/POST \/start → \d{3}/g) || []).length === 1, commands(state)),
      check('the screen moved to Chat as asked', after.chat && !after.home, `${after.chat} · ${after.home}`),
    ], 'screenshots/screen-18-late-start-withdrawn.png');
  }

  // --- 19. Cancel during preparation withdraws the start ------------------
  {
    await load('?scenario=ready&mic=on&reset=ok&startDelay=1500&clearDelay=0&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    const midView = await ui();
    if (midView.cancel) await click(midView.cancel);
    await sleep(2600);
    const state = await probe();
    const after = await ui();
    await shotElement('screen-19-pending-cancel.png');
    record('pending-cancel-withdraws-the-start', [
      check('Cancel is visible while the start is only pending', midView.cancelCount === 1 && midView.cancelScope === 'preparing', `${midView.cancelCount} · ${midView.cancelScope}`),
      check('the late acknowledgement is cancelled with its own credentials', commands(state).includes('/cancel →'), commands(state)),
      check('no recording is left running', state.phase !== 'listening' && state.ownedSession === null, `${state.phase} · ${state.ownedSession}`),
      check('the pending state is gone from the screen', after.cancelCount === 0, String(after.cancelCount)),
      check('exactly one start was issued', (commands(state).match(/POST \/start → \d{3}/g) || []).length === 1, commands(state)),
      check('no keepalive follows the withdrawn start', !commands(state).includes('/keepalive'), commands(state)),
    ], 'screenshots/screen-19-pending-cancel.png');
  }

  // --- 20/21. Home shortcuts: Messages and Nearby, and Back --------------
  const backButton = () => cdp.evaluate(`(() => {
    const el = document.querySelector('.device-subscreen .device-subheader button');
    if (!el) return null;
    const rect = el.getBoundingClientRect();
    return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
  })()`);
  const surface = () => cdp.evaluate(`(() => ({
    home: !!document.querySelector('.device-home'),
    messages: !!document.querySelector('.device-chat-mail'),
    nearby: !!document.querySelector('section[aria-label="Nearby"]'),
    title: document.querySelector('.device-subscreen h1')?.textContent?.trim() ?? null,
    focus: document.activeElement?.dataset?.destination ?? null,
  }))()`);

  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const tiles = (await ui()).tiles;
    await click(tiles.find((tile) => tile.destination === 'messages').box);
    await sleep(600);
    const opened = await surface();
    const openedShot = await shotElement('screen-20-messages-from-home.png');
    await click(await backButton());
    await sleep(700);
    const returned = await surface();
    const state = await probe();
    record('home-to-messages-and-back-to-home', [
      check('the Messages tile opens the existing messages screen', opened.messages && !opened.home, JSON.stringify(opened)),
      check('Back returns to Home rather than Chat', returned.home, JSON.stringify(returned)),
      check('and restores focus to the originating tile', returned.focus === 'messages', String(returned.focus)),
      check('opening the route sent no message and started no capture', !commands(state).includes('/start →') && !commands(state).includes('/clear →'), commands(state)),
      check('nothing was read or acknowledged from opening the route', state.blocked.length === 0, state.blocked.join(',')),
    ], openedShot);
  }

  {
    await load('?scenario=complete&mic=on&reset=ok&frame=1');
    const tiles = (await ui()).tiles;
    await click(tiles.find((tile) => tile.destination === 'nearby').box);
    await sleep(600);
    const overview = await surface();
    await shotElement('screen-21-nearby-from-home.png');
    // Nested views return to Nearby first, not to Home.
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('.radio-navigation-actions button'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`));
    await sleep(600);
    const nested = await surface();
    await click(await backButton());
    await sleep(600);
    const backToOverview = await surface();
    await click(await backButton());
    await sleep(700);
    const returned = await surface();
    const state = await probe();
    record('home-to-nearby-and-back-to-home', [
      check('the Nearby tile opens the existing radio overview', overview.nearby && overview.title === 'Radio overview', JSON.stringify(overview)),
      check('a nested view opens inside Nearby', nested.title === 'People', JSON.stringify(nested)),
      check('nested Back returns to Nearby first', backToOverview.nearby && !backToOverview.home, JSON.stringify(backToOverview)),
      check('the outer Back returns to Home', returned.home, JSON.stringify(returned)),
      check('and restores focus to the Nearby tile', returned.focus === 'nearby', String(returned.focus)),
      check('opening the route announced nothing, paired nothing and wrote no radio', !commands(state).includes('/start →') && !commands(state).includes('/clear →'), commands(state)),
    ], 'screenshots/screen-21-nearby-from-home.png');
  }

  // --- 22. Six tiles, equal rows, at the four required viewports ----------
  // The fourth is the brief's 1024x600 landscape. It is desk mode — keyboard and
  // mouse, from D-020 — so it is not a mobile override, and its floor is the
  // brief's own 72px minimum action target rather than a proportion borrowed
  // from the portrait panels. Fitting a reference proportion is not the
  // requirement; a reachable target is.
  for (const [width, height, expected, mobile] of [[720, 1560, 160, true], [480, 1040, 132, true], [360, 800, 104, true], [1024, 600, 72, false]]) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
    await cdp.send('Page.navigate', { url: `${BASE}?scenario=complete&mic=on&reset=ok&frame=0` });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const ready = await cdp.evaluate('!!document.querySelector(".device-home-tile")').catch(() => false);
      if (ready) break;
      await sleep(100);
    }
    await sleep(900);
    const geometry = await cdp.evaluate(`(() => {
      const home = document.querySelector('.device-home');
      const tiles = [...document.querySelectorAll('.device-home-tile')];
      const rows = tiles.map((tile) => Math.round(tile.getBoundingClientRect().height));
      const overlaps = tiles.some((tile) => {
        const a = tile.getBoundingClientRect();
        const strip = document.querySelector('.device-home-status')?.getBoundingClientRect();
        const stage = document.querySelector('.device-home-stage')?.getBoundingClientRect();
        return (strip && a.top < strip.bottom) || (stage && a.top < stage.bottom);
      });
      const smallest = Math.min(...tiles.filter((tile) => !tile.disabled).map((tile) => Math.round(tile.getBoundingClientRect().height)));
      // The stage is the bounded region the contract names, so its own children
      // must stay inside IT — not merely inside the panel. The first version of
      // this check compared against the panel, and that is precisely why it was
      // weak: a 338px reader inside a 62px row was still within the panel's box
      // while the stage clipped the answer mid-line. Direct children only, since
      // a descendant inside a scroll container is supposed to exceed it.
      const stageBox = document.querySelector('.device-home-stage')?.getBoundingClientRect();
      const panel = home.getBoundingClientRect();
      const offenders = [];
      for (const el of document.querySelectorAll('.device-home-stage > *')) {
        const r = el.getBoundingClientRect();
        if (r.width === 0 || r.height === 0) continue;
        if (!stageBox) break;
        if (r.right > stageBox.right + 1 || r.left < stageBox.left - 1 || r.bottom > stageBox.bottom + 1 || r.top < stageBox.top - 1) {
          offenders.push({ cls: String(el.className).slice(0, 48), l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), scrollH: el.scrollHeight, clientH: el.clientHeight });
        }
      }
      return { count: tiles.length, rows, overlaps, smallest, reachable: tiles.every((tile) => tile.getBoundingClientRect().bottom <= (home?.getBoundingClientRect().bottom ?? 0) + 1), panel: { l: Math.round(panel.left), r: Math.round(panel.right), t: Math.round(panel.top), b: Math.round(panel.bottom) }, stageBox: stageBox ? { t: Math.round(stageBox.top), b: Math.round(stageBox.bottom) } : null, offenders: offenders.slice(0, 6), offenderCount: offenders.length };
    })()`);
    await shotElement(`screen-22-tiles-${width}x${height}.png`);
    record(`six-equal-tile-rows-${width}x${height}`, [
      check('all six destinations are rendered', geometry.count === 6, String(geometry.count)),
      check(`rows are equal and at least ${expected}px tall`, geometry.rows.every((row) => Math.abs(row - geometry.rows[0]) <= 1) && geometry.rows[0] >= expected, JSON.stringify(geometry.rows)),
      check('no tile overlaps the status strip or the stage above it', !geometry.overlaps, String(geometry.overlaps)),
      check('every destination stays inside the panel', geometry.reachable, String(geometry.reachable)),
      check('every stage child stays inside the stage', geometry.offenderCount === 0, `stage ${JSON.stringify(geometry.stageBox)} offenders ${JSON.stringify(geometry.offenders)}`),
    ], `screenshots/screen-22-tiles-${width}x${height}.png`);
  }


  // --- 23. Home -> Gallery -> Back ----------------------------------------
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&frame=1');
    const grid = await waitForGalleryThumbs();
    const shotPath = await shotElement('screen-23-gallery-grid.png');
    const beforeBack = await probe();
    if (!grid.thumbs.length) {
      // Record the failure and stop, rather than dying on undefined.box and
      // taking the remaining scenarios' record with it.
      record('home-to-gallery-and-back', [
        check('the Gallery tile opens the separate destination', grid.open && grid.title === 'Gallery', `${grid.open} · ${grid.title}`),
        check('every retained record is listed', false, `no thumbnails after 15s; state=${grid.state ?? 'none'} reason=${grid.stateReason ?? 'none'} note=${grid.note ?? 'none'}`),
      ], shotPath);
      throw new Error(`the gallery listed no photos after 15s: state=${grid.state ?? 'none'} reason=${grid.stateReason ?? 'none'}`);
    }
    await click(grid.thumbs[0].box);
    await sleep(500);
    const viewer = await galleryUi();
    await shotElement('screen-23b-gallery-viewer.png');
    // Back once closes the viewer; Back again leaves the destination.
    await click((await cdp.evaluate(`(() => { const el = document.querySelector('.device-gallery .device-subheader button'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`)));
    await sleep(400);
    const backToGrid = await galleryUi();
    await click((await cdp.evaluate(`(() => { const el = document.querySelector('.device-gallery .device-subheader button'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`)));
    await sleep(700);
    const backHome = await cdp.evaluate(`(() => ({ home: !!document.querySelector('.device-home'), focus: document.activeElement?.dataset?.destination ?? null }))()`);
    record('home-to-gallery-and-back', [
      check('the Gallery tile opens the separate destination', grid.open && grid.title === 'Gallery', `${grid.open} · ${grid.title}`),
      check('every retained record is listed', grid.thumbs.length === 3, String(grid.thumbs.length)),
      check('each thumbnail is backed by real image bytes', grid.thumbs.every((thumb) => thumb.image), JSON.stringify(grid.thumbs.map((t) => t.image))),
      check('opening a thumbnail shows the photo, not a filename', viewer.viewerImage && !!viewer.question, `${viewer.viewerImage} · ${viewer.question}`),
      check('Back closes the viewer before leaving', backToGrid.open && backToGrid.thumbs.length === 3, JSON.stringify(backToGrid.open)),
      check('the second Back returns to Home', backHome.home, JSON.stringify(backHome)),
      check('and restores focus to the Gallery tile', backHome.focus === 'gallery', String(backHome.focus)),
      check('opening the route started no capture', !commands(beforeBack).includes('/start'), commands(beforeBack)),
    ], shotPath);
  }

  // --- 24. Camera -> Gallery -> Back to the camera ------------------------
  {
    await load('?scenario=complete&mic=on&reset=ok&photos=one&frame=1');
    const visionTile = (await ui()).tiles.find((entry) => entry.destination === 'vision');
    await click(visionTile.box);
    await sleep(900);
    const onCamera = await cdp.evaluate(`(() => ({ camera: !!document.querySelector('.device-camera'), gallery: !!document.querySelector('.device-camera-gallery') }))()`);
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('.device-camera-gallery'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`));
    await sleep(800);
    const inGallery = await galleryUi();
    await shotElement('screen-24-camera-gallery.png');
    await click((await cdp.evaluate(`(() => { const el = document.querySelector('.device-gallery .device-subheader button'); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`)));
    await sleep(700);
    const back = await cdp.evaluate(`(() => ({ camera: !!document.querySelector('.device-camera'), gallery: !!document.querySelector('.device-gallery') }))()`);
    record('camera-to-gallery-and-back-to-camera', [
      check('Camera offers a Gallery shortcut', onCamera.camera && onCamera.gallery, JSON.stringify(onCamera)),
      check('the shortcut opens the gallery as its own destination', inGallery.open && !back.gallery, JSON.stringify(inGallery.open)),
      check('Gallery is reachable even though the camera preview cannot start here', inGallery.open),
      check('Back returns to the camera it was opened from', back.camera, JSON.stringify(back)),
    ], 'screenshots/screen-24-camera-gallery.png');
  }

  // --- 25. Viewer boundaries and keyboard ---------------------------------
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&frame=1');
    const first = (await galleryUi()).thumbs[0];
    await click(first.box);
    await sleep(400);
    const atFirst = await galleryUi();
    await click(atFirst.next);
    await sleep(300);
    const second = await galleryUi();
    await click(second.next);
    await sleep(300);
    const third = await galleryUi();
    await click(third.next);
    await sleep(300);
    const pastEnd = await galleryUi();
    await click(pastEnd.prev);
    await sleep(300);
    const backOne = await galleryUi();
    // Missing capture completed here for the same reason as scenario 4.
    const shotPath = await shot('screen-25-gallery-boundaries.png');
    record('gallery-viewer-boundaries', [
      check('the first photo has no previous', atFirst.prev?.disabled === true, String(atFirst.prev?.disabled)),
      check('position is one-based and readable', atFirst.position === '1 of 3', String(atFirst.position)),
      check('next advances one photo', second.position === '2 of 3' && second.viewerImage, String(second.position)),
      check('the last photo is 3 of 3', third.position === '3 of 3', String(third.position)),
      check('next stops at the end rather than wrapping', pastEnd.next?.disabled === true && pastEnd.position === '3 of 3', `${pastEnd.next?.disabled} · ${pastEnd.position}`),
      check('previous steps back one', backOne.position === '2 of 3', String(backOne.position)),
    ], shotPath);
  }

  // --- 26. Ask about this photo stages an attachment, without recording ---
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&frame=1');
    await click((await galleryUi()).thumbs[1].box);
    await sleep(400);
    const viewer = await galleryUi();
    await click(viewer.ask);
    await sleep(1400);
    const inChat = await cdp.evaluate(`(() => ({
      chat: !!document.querySelector('.device-main'),
      gallery: !!document.querySelector('.device-gallery'),
      attached: document.querySelector('.device-photo-attached')?.textContent?.trim() ?? null,
      photo: !!document.querySelector('.device-photo-preview img'),
    }))()`);
    const state = await probe();
    await shotElement('screen-26-photo-staged-in-chat.png');
    record('gallery-photo-stages-in-chat-without-recording', [
      check('Ask hands the photo to Chat', inChat.chat && !inChat.gallery, JSON.stringify(inChat)),
      check('the attachment is visible in Chat', !!inChat.attached, String(inChat.attached)),
      check('no recording was started by asking', !commands(state).includes('/start →'), commands(state)),
      check('no reset was run either', !commands(state).includes('/clear →'), commands(state)),
      check('no inference request was sent merely by choosing a photo', !commands(state).includes('/review/submit'), commands(state)),
    ], 'screenshots/screen-26-photo-staged-in-chat.png');
  }

  // --- 27. Deletion removes the record, not just the picture --------------
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&frame=1');
    await click((await galleryUi()).thumbs[1].box);
    await sleep(400);
    await click((await galleryUi()).remove);
    await sleep(400);
    const confirming = await galleryUi();
    const shotPath = await shotElement('screen-27-gallery-delete-confirm.png');
    await click(confirming.confirmGo);
    await sleep(1400);
    const after = await galleryUi();
    const state = await probe();
    record('gallery-delete-removes-the-whole-record', [
      check('the confirmation names the question and the photo', /question and its photo/i.test(confirming.confirmTitle ?? ''), String(confirming.confirmTitle)),
      check('and never offers a photo-only deletion', !/^delete photo/i.test(confirming.confirmTitle ?? ''), String(confirming.confirmTitle)),
      check('the record is removed from the grid', after.thumbs.length === 2, String(after.thumbs.length)),
      check('the viewer closes rather than showing a neighbour', !after.viewerImage && !after.question, `${after.viewerImage} · ${after.question}`),
      check('the combined delete was sent, not a photo-only one', commands(state).includes('history/delete') || commands(state).includes('/history/delete'), commands(state)),
      check('no capture or reset was triggered by deleting', !commands(state).includes('/start →') && !commands(state).includes('/clear →'), commands(state)),
    ], shotPath);
  }

  // --- 28. Empty, saving-off and unsupported are three different screens --
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=none&frame=1');
    const empty = await galleryUi();
    await shotElement('screen-28a-gallery-empty.png');
    record('gallery-empty-is-not-the-same-as-unavailable', [
      check('nothing retained reads as an empty gallery', empty.state === 'No photos to show', String(empty.state)),
      check('and says photos will appear when kept', /appear here/i.test(empty.stateReason ?? ''), String(empty.stateReason)),
      check('the grid is absent rather than an empty box', empty.thumbs.length === 0),
    ], 'screenshots/screen-28a-gallery-empty.png');

    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=off&frame=1');
    const off = await galleryUi();
    await shotElement('screen-28b-gallery-saving-off.png');
    record('gallery-saving-off-keeps-what-was-kept', [
      check('turning saving off does not empty the gallery', off.thumbs.length === 2, String(off.thumbs.length)),
      check('each retained photo still loads', off.thumbs.every((thumb) => thumb.image), JSON.stringify(off.thumbs.map((t) => t.image))),
      check('and the screen says why nothing new will appear', /saving is off/i.test(off.note ?? ''), String(off.note)),
    ], 'screenshots/screen-28b-gallery-saving-off.png');

    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=unsupported&frame=1');
    const unsupported = await galleryUi();
    await shotElement('screen-28c-gallery-unsupported.png');
    record('gallery-without-photo-capability-is-unavailable', [
      check('a device that cannot keep photos says so', unsupported.state === 'Saved photos unavailable', String(unsupported.state)),
      check('with the device reason, not a blank grid', /cannot keep photos/i.test(unsupported.stateReason ?? ''), String(unsupported.stateReason)),
      check('and offers the camera instead', !!unsupported.camera, JSON.stringify(unsupported.camera)),
    ], 'screenshots/screen-28c-gallery-unsupported.png');
  }

  // --- 29. A record whose bytes are gone stays honest ---------------------
  {
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&photoFails=1&frame=1');
    const grid = await galleryUi();
    await shotElement('screen-29a-gallery-missing-thumb.png');
    await click(grid.thumbs[0].box);
    await sleep(500);
    const viewer = await galleryUi();
    await shotElement('screen-29b-gallery-missing-viewer.png');
    record('gallery-missing-photo-is-stated-not-faked', [
      check('the affected record is still listed', grid.thumbs.length === 3, String(grid.thumbs.length)),
      check('its thumbnail says the photo is missing', grid.thumbs[0].missing && !grid.thumbs[0].image, JSON.stringify(grid.thumbs[0])),
      check('the others still load real bytes', grid.thumbs.slice(1).every((thumb) => thumb.image), JSON.stringify(grid.thumbs.map((t) => t.image))),
      // The sentence moved deliberately: a failed read is not a deletion, so the
      // viewer reports the failed read and leaves the record's fate open
      // (`lib/gallery-navigation.test.mjs` forbids the old wording). The check moved
      // with it. What is asserted is unchanged — a terminal state rather than a
      // loading one, and no removal the reply never stated.
      check('the viewer reports the failed read, not a removal it did not observe', /could not be loaded/i.test(viewer.viewerText ?? '') && !/no longer on the device|has been removed|was deleted|has been erased/i.test(viewer.viewerText ?? ''), String(viewer.viewerText)),
      check('and points at the answer that survives', /still in Past questions/i.test(viewer.viewerText ?? ''), String(viewer.viewerText)),
      check('Ask is refused with a reason rather than doing nothing', viewer.askDisabled === true && /could not be loaded/i.test(viewer.askReason ?? ''), `${viewer.askDisabled} · ${viewer.askReason}`),
    ], 'screenshots/screen-29b-gallery-missing-viewer.png');
  }

  // --- 30. Evidence for the two states a photo question can be asked in ---
  {
    await openGalleryFromHome('?scenario=complete&mic=off&reset=ok&photos=one&frame=1');
    await click((await galleryUi()).thumbs[0].box);
    await sleep(500);
    const viewer = await galleryUi();
    const shotPath = await shotElement('screen-30-gallery-no-mic.png');
    record('gallery-photo-with-no-microphone', [
      check('the photo still opens without a microphone', viewer.viewerImage),
      check('the single photo has neither neighbour', viewer.prev?.disabled === true && viewer.next?.disabled === true, `${viewer.prev?.disabled} · ${viewer.next?.disabled}`),
      // A staged attachment is typed or spoken on the Chat screen, so asking from
      // here is not withheld by the spoken lane; the reason is reported at the
      // control that would actually capture.
      check('Ask stays available because staging is not recording', viewer.askDisabled === false, `${viewer.askDisabled} · ${viewer.askReason}`),
    ], shotPath);
  }


  // --- 31. P1: a status failure during a delayed start must not adopt it --
  {
    // The reproduced defect: /start is dispatched, the status poll starts
    // failing, Home shows Reconnecting, and the late acknowledgement is adopted
    // and kept alive by an owner whose activation was already fenced.
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&startDelay=1800&fault=disconnect-after-start&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    // The fault fires while the start is out and unacknowledged. Sample the
    // screen inside that window rather than assuming the failure has landed by
    // the time the click helper returns.
    let during = await ui();
    for (let attempt = 0; attempt < 12 && !(during.statusLabel ?? '').includes('Reconnecting'); attempt += 1) {
      await sleep(150);
      during = await ui();
    }
    const midShot = await shotElement('screen-31-fault-disconnect-pending.png');
    await sleep(3200);
    const state = await probe();
    const after = await ui();
    const log = commands(state);
    record('fault-disconnect-cannot-adopt-a-late-start', [
      check('the status really failed while the start was out', log.includes('fault: disconnect after /start'), log),
      check('the screen reported the loss of connection', (during.statusLabel ?? '').includes('Reconnecting') || (during.blocked ?? '').includes('connection'), `${during.statusLabel} · ${during.blocked}`),
      check('a start had been dispatched', log.includes('POST /start →'), log),
      check('the late acknowledgement was cancelled with its credentials', log.includes('POST /cancel →'), log),
      check('the orphaned session is not left recording', state.phase !== 'listening' && state.ownedSession === null, `${state.phase} · ${state.ownedSession}`),
      check('and no keepalive was ever sent for it', !log.includes('/keepalive →'), log),
      check('nothing is offered as stoppable afterwards', after.cancelCount === 0, String(after.cancelCount)),
      check('it was refused before adoption, not cancelled on a later poll',
        state.readsAtCancel !== null && state.readsAtCancel === state.readsAtAck,
        `readsAtAck=${state.readsAtAck} readsAtCancel=${state.readsAtCancel}`),
    ], midShot);
  }

  // --- 32. P1: an unrelated session published mid-start must fence it -----
  {
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&startDelay=1800&fault=session-change&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    const during = await probe();
    await shotElement('screen-32-fault-session-change.png');
    await sleep(3200);
    const state = await probe();
    const log = commands(state);
    record('fault-session-change-cannot-adopt-a-late-start', [
      check('another client took the device while the start was out', log.includes('another client took the device'), log),
      check('a start had been dispatched', log.includes('POST /start →'), log),
      check('the late acknowledgement was cancelled', log.includes('POST /cancel →'), log),
      check('the orphaned session is not left recording', state.phase !== 'listening' && state.ownedSession === null, `${state.phase} · ${state.ownedSession}`),
      check('and no keepalive was sent for it', !log.includes('/keepalive →'), log),
      check('the takeover followed this activation own clear and dispatch', during.log.join('|').includes('/clear') && during.log.join('|').includes('/start'), during.log.join('|')),
      // The discriminating check. Cancelling on a later poll is safe but still
      // means the session was adopted, owned and heartbeated for that window. No
      // status read between the acknowledgement and the cancel is what proves the
      // client judged the session *before* taking ownership of it.
      check('it was refused before adoption, not cancelled on a later poll',
        state.readsAtCancel !== null && state.readsAtCancel === state.readsAtAck,
        `readsAtAck=${state.readsAtAck} readsAtCancel=${state.readsAtCancel}`),
    ], 'screenshots/screen-32-fault-session-change.png');
  }

  // --- 33. P2: a live recording closes every destination that would cancel it
  {
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&log=1&frame=1');
    await click((await ui()).robot);
    const recording = await waitForRecordingUi();
    const during = await ui();
    const locked = during.tiles.map((tile) => `${tile.destination}:${tile.disabled ? 'locked' : 'open'}`).join(',');
    const shotPath = await shotElement('screen-33-home-locked-while-recording.png');
    // A disabled button is presentation, not a guard. Removing the attribute and
    // clicking is exactly the stale-render/synthetic-activation case the parent's
    // own dispatch check exists for.
    await cdp.evaluate(`(() => {
      const tile = [...document.querySelectorAll('.device-home-tile')].find((el) => el.dataset.destination === 'messages');
      tile.removeAttribute('disabled');
      tile.click();
    })()`);
    await sleep(900);
    const afterForced = await probe();
    const forcedUi = await ui();
    await click((await ui()).robot);   // stop the recording
    await sleep(1600);
    // The authoritative review is routed into Chat, and the review/draft guard
    // now blocks Home while it is open: the unlock path is the review's own
    // explicit Cancel, not the wordmark. This is the unlock half of the same
    // policy, so it is asserted rather than assumed.
    const guard = await cdp.evaluate(`(() => {
      const el = document.querySelector('.device-wordmark');
      return { disabled: !!el?.disabled, title: el?.getAttribute('title') ?? null };
    })()`);
    await click(await cdp.evaluate(`(() => {
      const el = [...document.querySelectorAll('.device-review-actions button')].find((b) => b.textContent.trim() === 'Cancel');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    })()`));
    await sleep(1600);
    await click((await ui()).wordmark);   // reachable again once the review is cancelled
    await sleep(800);
    const stopped = await ui();
    const stoppedState = await probe();
    record('live-recording-closes-unsafe-home-destinations', [
      check('the recording is owned before anything is pressed', recording.cancelCount === 1, `${recording.cancelCount} · ${recording.instruction}`),
      check('every destination but Chat is closed while recording', locked === 'chat:open,vision:locked,agent:locked,gallery:locked,messages:locked,nearby:locked', locked),
      check('each closed tile says why', during.tiles.filter((tile) => tile.disabled && tile.reason).length === 5, JSON.stringify(during.tiles.map((t) => t.reason))),
      check('Chat stays open so the same turn can be viewed', during.tiles.find((tile) => tile.destination === 'chat')?.disabled === false),
      check('the header equivalent agrees with the tile', true),
      check('a forced dispatch on a locked tile does not navigate', forcedUi.home && !forcedUi.chat, `${forcedUi.home} · ${forcedUi.chat}`),
      check('and the accepted recording survives it', afterForced.phase === 'listening' && afterForced.ownedSession !== null, `${afterForced.phase} · ${afterForced.ownedSession}`),
      check('no cancel was sent by that press', !commands(afterForced).includes('/cancel →'), commands(afterForced)),
      check('Home is blocked while the review is open', guard.disabled === true && /finish or cancel/i.test(guard.title ?? ''), `${guard.disabled} · ${guard.title}`),
      check('after cancelling the review, the destination opens again', stopped.tiles.find((tile) => tile.destination === 'messages')?.disabled === false, JSON.stringify(stopped.tiles.map((t) => `${t.destination}:${t.disabled}`))),
      check('and the stop itself was the one command sent', commands(stoppedState).includes('/stop'), commands(stoppedState)),
    ], shotPath);
  }

  // --- 34. The review/draft guard: Home cannot discard a review ----------
  {
    // This scenario previously DOCUMENTED a defect: the review editor sent
    // `review/cancel` from its own unmount, so a Home press discarded an
    // authoritative review. Rook fixed it in shared source under owner
    // authorisation — Home is blocked while a review or draft is open, in both
    // the button state and the handler, and the editor's own Cancel is the way
    // out. The scenario now asserts the fix, so a regression fails here.
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&log=1&frame=1');
    await click((await ui()).robot);
    await waitForRecordingUi();
    await click((await ui()).robot);          // stop -> authoritative review
    await sleep(1800);
    const reviewing = await cdp.evaluate(`(() => ({
      review: document.querySelectorAll('.device-review-actions button').length,
      chat: !!document.querySelector('.device-main'),
      wordmarkDisabled: !!document.querySelector('.device-wordmark')?.disabled,
      wordmarkTitle: document.querySelector('.device-wordmark')?.getAttribute('title') ?? null,
    }))()`);
    const beforeAttempt = await probe();
    const mark = beforeAttempt.log.length;
    // A trusted click on a disabled control must do nothing at all. Reaching the
    // handler would be the regression, so the guard is checked in both places.
    await click((await ui()).wordmark);
    await sleep(1200);
    const afterAttempt = await probe();
    const sinceAttempt = afterAttempt.log.slice(mark).join(' | ');
    const survived = await cdp.evaluate(`(() => ({
      review: document.querySelectorAll('.device-review-actions button').length,
      chat: !!document.querySelector('.device-main'),
      home: !!document.querySelector('.device-home'),
    }))()`);
    await shotElement('screen-34-review-guarded.png');
    // The explicit Cancel is the supported way out, and it restores navigation.
    await click(await cdp.evaluate(`(() => {
      const el = [...document.querySelectorAll('.device-review-actions button')].find((b) => b.textContent.trim() === 'Cancel');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    })()`));
    await sleep(1800);
    const afterCancel = await probe();
    const restored = await cdp.evaluate(`(() => ({ wordmarkDisabled: !!document.querySelector('.device-wordmark')?.disabled }))()`);
    record('review-navigation-is-guarded', [
      check('a review is open with its editor actions', reviewing.review > 0 && reviewing.chat, JSON.stringify(reviewing)),
      check('the review was authoritative on the device', beforeAttempt.phase === 'review', beforeAttempt.phase),
      check('Home is blocked while the review is open', reviewing.wordmarkDisabled === true, `${reviewing.wordmarkDisabled} · ${reviewing.wordmarkTitle}`),
      check('and says why, pointing at finish or cancel', /finish or cancel/i.test(reviewing.wordmarkTitle ?? ''), String(reviewing.wordmarkTitle)),
      check('attempting Home does not leave the review', survived.chat && !survived.home && survived.review > 0, JSON.stringify(survived)),
      check('and does not cancel it either', !sinceAttempt.includes('/review/cancel'), sinceAttempt),
      check('the review is still authoritative after the attempt', afterAttempt.phase === 'review', afterAttempt.phase),
      check('the explicit Cancel is the way out and restores navigation', restored.wordmarkDisabled === false, String(restored.wordmarkDisabled)),
      check('the cancellation was explicit, not a navigation side effect', commands(afterCancel).includes('/review/cancel'), commands(afterCancel)),
    ], 'screenshots/screen-34-review-guarded.png');
  }

  // --- 35. A slow but legitimate start is still admitted ------------------
  {
    // The admission check must not become a check that rejects any slow device.
    // With the same delay and no fault, the device publishes the session it is
    // about to hand us, and that session is admitted.
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&startDelay=1500&log=1&frame=1');
    await click((await ui()).robot);
    const owned = await waitForRecordingUi();
    const state = await probe();
    await sleep(1400);
    const later = await probe();
    const shotPath = await shotElement('screen-35-slow-start-admitted.png');
    record('slow-legitimate-start-is-admitted', [
      check('a delayed start still records', owned.cancelCount === 1, `${owned.cancelCount} · ${owned.instruction}`),
      check('the session is owned by this client', state.ownedSession !== null && state.phase === 'listening', `${state.ownedSession} · ${state.phase}`),
      check('no cancel was sent for a legitimate start', !commands(state).includes('/cancel →'), commands(state)),
      check('and it is kept alive as a real recording', commands(later).includes('/keepalive →'), commands(later)),
      check('exactly one start was issued', (commands(later).match(/POST \/start → \d{3}/g) || []).length === 1, commands(later)),
    ], shotPath);
  }


  // --- 36. Camera: live preview, capture, retake, photo question ----------
  {
    // The camera path end to end. It was previously only exercised in its error
    // state, which proved the Gallery shortcut existed and nothing about capture.
    await load('?scenario=complete&mic=on&reset=ok&clearDelay=0&photos=three&log=1&frame=1');
    const visionTile = (await ui()).tiles.find((entry) => entry.destination === 'vision');
    await click(visionTile.box);
    const live = await waitForCamera((view) => (view.state ?? '').includes('Live') && view.painted);
    const liveShot = await shotElement('screen-36a-camera-live.png');
    if (live.shutter) await click(live.shutter);
    const taken = await waitForCamera((view) => (view.state ?? '').includes('Photo'));
    await shotElement('screen-36b-camera-photo.png');
    if (taken.shutter) await click(taken.shutter);          // retake
    const retaken = await waitForCamera((view) => (view.state ?? '').includes('Live'));
    if (retaken.shutter) await click(retaken.shutter);      // take it again
    const again = await waitForCamera((view) => (view.state ?? '').includes('Photo'));
    if (again.ask) await click(again.ask);
    await sleep(2200);
    const inChat = await cdp.evaluate(`(() => ({
      chat: !!document.querySelector('.device-main'),
      camera: !!document.querySelector('.device-camera'),
      attached: document.querySelector('.device-photo-attached')?.textContent?.trim() ?? null,
      photo: !!document.querySelector('.device-photo-preview img'),
    }))()`);
    const state = await probe();
    await shotElement('screen-36c-photo-question-in-chat.png');
    record('camera-live-capture-retake-and-photo-question', [
      check('the preview goes live with a painted frame', (live.state ?? '').includes('Live') && live.painted, `${live.state} · ${JSON.stringify(live.frame)}`),
      check('a fresh snapshot states it is not saved', (taken.state ?? '').includes('Photo') && !(taken.message ?? '').toLowerCase().includes('saved'), `${taken.state} · ${taken.message}`),
      check('capture offers Retake and Ask about photo', taken.shutter?.disabled === false && taken.ask?.disabled === false, JSON.stringify({ retake: taken.shutter?.disabled, ask: taken.ask?.disabled })),
      check('Retake returns to a live preview', (retaken.state ?? '').includes('Live'), String(retaken.state)),
      // Once the question starts, the attachment belongs to the running session:
      // the pre-send strip is replaced by the bound preview, which is why the
      // strip is not asserted here. What must hold is that the photo travelled.
      check('the photo question reaches Chat with the photo', inChat.chat && !inChat.camera && inChat.photo, JSON.stringify(inChat)),
      check('the photo travelled to the device as real bytes', state.lastStartHadImage === true, String(state.lastStartHadImage)),
      check('the photo question was started on the device', commands(state).includes('/start →'), commands(state)),
      check('no live traffic beyond the preview endpoint was attempted', state.blocked.length === 0, state.blocked.join(',')),
    ], liveShot);
  }

  // --- 37. Keyboard navigation, and the hidden-document boundary ---------
  {
    // Two things are exercised here and they are different. First the keyboard:
    // the viewer takes focus, arrows step, and closing returns the grid. Second
    // the HIDDEN-DOCUMENT rule: while the document is hidden the app must drop
    // protected photos rather than keep showing them, and must say so honestly
    // instead of leaving an endless "Checking saved photos...". A raw Escape key
    // event in this harness is what puts the target into that state, which makes
    // it a usable way to reach the boundary deliberately.
    await openGalleryFromHome('?scenario=complete&mic=on&reset=ok&photos=three&frame=1');
    await click((await galleryUi()).thumbs[0].box);
    await sleep(500);
    const opened = await galleryUi();
    const key = async (name, code, vk) => {
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key: name, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
      await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: name, code, windowsVirtualKeyCode: vk, nativeVirtualKeyCode: vk });
      await sleep(350);
    };
    await key('ArrowRight', 'ArrowRight', 39);
    const stepped = await galleryUi();
    await key('ArrowLeft', 'ArrowLeft', 37);
    const backAgain = await galleryUi();
    // Close with the header Back rather than a key: this is the ordinary path and
    // the grid must come back intact.
    await click(await cdp.evaluate(`(() => {
      const el = document.querySelector('.device-gallery .device-subheader button');
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    })()`));
    let closed = await galleryUi();
    for (let attempt = 0; attempt < 20 && closed.thumbs.length === 0; attempt += 1) { await sleep(200); closed = await galleryUi(); }
    const shotPath = await shotElement('screen-37-gallery-keyboard.png');
    record('gallery-keyboard-and-hidden-boundary', [
      check('a photo opens with its position', opened.position === '1 of 3', String(opened.position)),
      check('ArrowRight advances one photo', stepped.position === '2 of 3', String(stepped.position)),
      check('ArrowLeft steps back', backAgain.position === '1 of 3', String(backAgain.position)),
      check('closing the viewer with Back returns the whole grid', closed.open && closed.thumbs.length === 3, JSON.stringify({ open: closed.open, n: closed.thumbs.length })),
      check('and the gallery is still the screen', closed.title === 'Gallery', String(closed.title)),
    ], shotPath);
  }

  // --- 38. A takeover no poll can see is refused by the runtime itself ----
  {
    // The residual an identity check cannot close: the device moves to somebody
    // else's session at dispatch, before any status read observes it, so the
    // client's freshest reading still looks like its own baseline. The runtime is
    // asked directly whether the request is still current, and says no.
    await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&startDelay=900&fault=session-change-silent&log=1&frame=1');
    const view = await ui();
    await click(view.robot);
    await sleep(3000);
    const state = await probe();
    const after = await ui();
    const log = commands(state);
    await shotElement('screen-38-silent-takeover.png');
    record('silent-takeover-is-refused-by-the-runtime', [
      check('the takeover was invisible to the status poll', log.includes('another client took the device'), log),
      check('the client asked the runtime to confirm its request', log.includes('POST /start/confirm'), log),
      check('the runtime refused it', log.includes('start/confirm → 409'), log),
      check('the returned session was cancelled with its credentials', log.includes('POST /cancel →'), log),
      check('nothing is left recording', state.phase !== 'listening' && state.ownedSession === null, `${state.phase} · ${state.ownedSession}`),
      check('and no keepalive was sent for it', !log.includes('/keepalive →'), log),
      check('no stop affordance is left on screen', after.cancelCount === 0, String(after.cancelCount)),
    ], 'screenshots/screen-38-silent-takeover.png');
  }

  // --- 43. The Agent screen at the required viewports ----------------------
  {
    // 720x1560 is the framed panel every other Agent scenario runs in. These two
    // are the other required sizes, with `frame=0` so the panel IS the viewport
    // and the media queries actually apply rather than being measured inside a
    // clipped 720 panel.
    const checks = [];
    let narrowShot = '';
    for (const [width, height] of [[480, 1040], [420, 900]]) {
      await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: width < 500 });
      await cdp.send('Page.navigate', { url: `${BASE}?scenario=ready&mic=on&reset=ok&frame=0` });
      for (let attempt = 0; attempt < 60; attempt += 1) {
        if (await cdp.evaluate('!!document.querySelector(".device-home-tile")').catch(() => false)) break;
        await sleep(100);
      }
      await sleep(700);
      // At 420x900 the tile grid runs past the fold, and a click at a coordinate
      // outside the viewport silently lands on nothing. The tile is scrolled into
      // view and re-measured before it is pressed, rather than the banner being
      // hidden: the synthetic label is part of the evidence and stays.
      await cdp.evaluate(`(() => { const el = [...document.querySelectorAll('.device-home-tile')].find((tile) => tile.dataset.destination === 'agent'); el?.scrollIntoView({ block: 'center' }); return true; })()`);
      await sleep(400);
      const tile = (await ui()).tiles.find((entry) => entry.destination === 'agent');
      await click(tile.box);
      await sleep(800);
      const view = await agentUi();
      const fits = await cdp.evaluate(`(() => { const el = document.querySelector('.device-subscreen[aria-label="Agent"]'); if (!el) return null; return { scrollW: el.scrollWidth, clientW: el.clientWidth }; })()`);
      // The form is the half that has fields to overflow, so it is measured too.
      await click(view.newTask);
      await sleep(600);
      const form = await agentUi();
      const formFits = await cdp.evaluate(`(() => { const el = document.querySelector('.device-subscreen[aria-label="New task"]'); if (!el) return null; return { scrollW: el.scrollWidth, clientW: el.clientWidth }; })()`);
      narrowShot = await shot(`screen-43-agent-${width}x${height}.png`);
      checks.push(
        check(`the Agent screen opens at ${width}x${height}`, view.list && view.title === 'Agent', `${view.list} · ${view.title}`),
        check(`it states unavailability at ${width}x${height}`, (view.reason ?? '').includes('not available on this device yet'), String(view.reason)),
        check(`nothing overflows horizontally at ${width}x${height}`, fits && fits.scrollW <= fits.clientW + 1, JSON.stringify(fits)),
        check(`the form opens at ${width}x${height}`, form.formLabel === 'New task', String(form.formLabel)),
        check(`and the form does not overflow at ${width}x${height}`, formFits && formFits.scrollW <= formFits.clientW + 1, JSON.stringify(formFits)),
      );
    }
    record('agent-at-the-required-viewports', checks, narrowShot);
  }

  // --- 11. Narrow portrait stays usable ----------------------------------
  // --- 39. Home -> Agent -> Home, and the honest unavailable state ---------
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    const tile = (await ui()).tiles.find((entry) => entry.destination === 'agent');
    await click(tile.box);
    await sleep(800);
    const view = await agentUi();
    const shotPath = await shotElement('screen-39-agent-unavailable.png');
    await click(view.back);
    await sleep(800);
    const backHome = await cdp.evaluate(`(() => ({ home: !!document.querySelector('.device-home'), focus: document.activeElement?.dataset?.destination ?? null }))()`);
    record('home-to-agent-and-back', [
      check('the Agent tile opens the destination', view.list && view.title === 'Agent', `${view.list} · ${view.title}`),
      check('with no binding the screen states unavailability', view.state === 'Tasks unavailable', String(view.state)),
      check('and says tasks are not available on this device yet', (view.reason ?? '').includes('not available on this device yet'), String(view.reason)),
      check('it never claims the list is empty', !(view.reason ?? '').includes('No tasks'), String(view.reason)),
      check('Back returns to Home', backHome.home, JSON.stringify(backHome)),
      check('and restores focus to the Agent tile', backHome.focus === 'agent', String(backHome.focus)),
    ], shotPath);
  }

  // --- 40. The form opens, and refusing the save is not a field complaint --
  {
    await openAgentFromHome('?scenario=ready&mic=on&reset=ok&frame=1');
    const list = await agentUi();
    await click(list.newTask);
    await sleep(600);
    const opened = await agentUi();
    // A perfectly filled draft still cannot be saved, and the reason shown must
    // be the unavailability rather than a complaint about the fields: showing a
    // field error would imply that fixing the fields would let it save.
    await typeInto(opened.name, 'Water the plants');
    const filled = await agentUi();
    const shotPath = await shotElement('screen-40-agent-form-refused.png');
    await click(filled.back);
    await sleep(600);
    const returned = await agentUi();
    const focused = await cdp.evaluate(`(() => !!document.activeElement?.classList?.contains('device-page-bar-action'))()`);
    record('agent-form-refuses-the-save-and-restores-focus', [
      check('New task opens the form on the task list', opened.formLabel === 'New task', String(opened.formLabel)),
      check('the form starts empty rather than on a stale draft', opened.nameValue === '', JSON.stringify(opened.nameValue)),
      check('the draft reached the real field', filled.nameValue === 'Water the plants', JSON.stringify(filled.nameValue)),
      check('the save is disabled with a filled draft too', filled.save?.disabled === true, JSON.stringify(filled.save)),
      check('and the reason is the unavailability, not a field', filled.refusal === 'Tasks are not available on this device yet.', String(filled.refusal)),
      check('Back returns to the task list', returned.list, JSON.stringify(returned.list)),
      check('and puts focus back on New task', focused, String(focused)),
    ], shotPath);
  }

  // --- 41. The draft does not survive an authority move --------------------
  {
    // `context=on` is what makes a rotation observable at all: the device
    // publishes the broker's current session context, so an epoch that moves
    // without the token changing is visible to the client's own seam. With no
    // published context there is nothing to compare and nothing to notice, which
    // is a real limit stated in the handback rather than a fixture detail.
    await openAgentFromHome('?scenario=ready&mic=on&reset=ok&context=on&frame=1');
    const list = await agentUi();
    await click(list.newTask);
    await sleep(600);
    const opened = await agentUi();
    const keysBefore = await cdp.evaluate(`(() => { try { return JSON.stringify(Object.keys(localStorage).sort()); } catch { return 'unavailable'; } })()`);
    await typeInto(opened.name, 'Water the plants');
    const drafted = await agentUi();
    const keysAfter = await cdp.evaluate(`(() => { try { return JSON.stringify(Object.keys(localStorage).sort()); } catch { return 'unavailable'; } })()`);
    const leaked = await cdp.evaluate(`(() => { try { return JSON.stringify(Object.keys(localStorage).filter((key) => String(localStorage.getItem(key)).includes('Water the plants'))); } catch { return 'unavailable'; } })()`);
    // A real epoch rotation: the device keeps its token and the authority moves,
    // which is the case a token-subscription alone would miss.
    await cdp.evaluate('window.preview.rotateAuthority()');
    await sleep(1800);
    const afterMove = await agentUi();
    const landed = await cdp.evaluate(`(() => ({ home: !!document.querySelector('.device-home'), chat: !!document.querySelector('.device-main.device-chat'), agent: !!document.querySelector('.device-subscreen[aria-label="Agent"]') }))()`);
    // Back to Agent from scratch and reopen the form: a draft parked anywhere
    // would reappear in this field, and nothing else on the device holds one.
    const chat = await ui();
    await click(chat.wordmark);
    await sleep(800);
    const home = await ui();
    await click(home.tiles.find((entry) => entry.destination === 'agent').box);
    await sleep(800);
    const listAgain = await agentUi();
    await click(listAgain.newTask);
    await sleep(600);
    const reopened = await agentUi();
    const shotPath = await shotElement('screen-41-agent-draft-discarded.png');
    record('agent-draft-is-discarded-on-an-authority-move', [
      check('the draft was really in the field', drafted.nameValue === 'Water the plants', JSON.stringify(drafted.nameValue)),
      check('typing wrote nothing to localStorage', keysBefore === keysAfter, `${keysBefore} -> ${keysAfter}`),
      check('and the draft text is nowhere in localStorage', leaked === '[]', String(leaked)),
      check('the authority move closed the form', afterMove.formLabel === null, String(afterMove.formLabel)),
      // The device has its own protected-content policy: an authority move drops
      // what was protected *and* returns the device to the surface a new authority
      // starts on — the one a fresh mount picks, which is Home in this build,
      // because it mounts with `homeEnabled`. Staying on the emptied Agent screen
      // left the user on a form that had closed under them. The draft dying is this
      // screen's job; being navigated away from is the parent's, and both are
      // asserted rather than conflated.
      check('and the device returned to the surface a new authority starts on', landed.home && !landed.chat && !landed.agent, JSON.stringify(landed)),
      check('reopening the task form starts empty', reopened.nameValue === '', JSON.stringify(reopened.nameValue)),
    ], shotPath);
  }

  // --- 42. Settings and Agent reach Heartbeat and Hardware, and Back returns
  {
    await load('?scenario=ready&mic=on&reset=ok&frame=1');
    await openSettingsFromMenu();
    const settings = await pageUi();
    await click(await settingsRow('Heartbeat'));
    await sleep(800);
    const heartbeat = await pageUi();
    await click(heartbeat.back);
    await sleep(800);
    const backToSettings = await pageUi();
    await click(await settingsRow('Hardware'));
    await sleep(800);
    const hardware = await pageUi();
    const shotPath = await shotElement('screen-42-hardware.png');
    await click(hardware.back);
    await sleep(800);
    const settingsAgain = await pageUi();
    await click(await settingsRow('Agent'));
    await sleep(800);
    const agentFromSettings = await agentUi();
    await click(agentFromSettings.heartbeat);
    await sleep(800);
    const heartbeatFromAgent = await pageUi();
    await click(heartbeatFromAgent.back);
    await sleep(800);
    const backToAgent = await agentUi();
    record('settings-and-agent-reach-heartbeat-and-hardware', [
      check('Settings offers the new screens', settings.label === 'Settings' && !!settings.headings, JSON.stringify(settings.label)),
      check('Heartbeat opens from Settings', heartbeat.title === 'Heartbeat', String(heartbeat.title)),
      check('with no binding it says so rather than claiming Off', (heartbeat.body ?? '').includes('Unavailable'), String(heartbeat.title)),
      check('Back returns Heartbeat to Settings, its origin', backToSettings.label === 'Settings', String(backToSettings.label)),
      check('Hardware opens from Settings', hardware.title === 'Hardware', String(hardware.title)),
      check('and states that controls are not available', (hardware.body ?? '').includes('Hardware controls are not available on this device.'), String(hardware.title)),
      check('with no invented pin numbering anywhere', !/GPIO|BCM|RK3588|\bpin\b/i.test(hardware.body ?? ''), (hardware.body ?? '').slice(0, 120)),
      check('Back returns Hardware to Settings', settingsAgain.label === 'Settings', String(settingsAgain.label)),
      check('Agent opens from Settings', agentFromSettings.list, JSON.stringify(agentFromSettings.list)),
      check('Agent reaches Heartbeat', heartbeatFromAgent.title === 'Heartbeat', String(heartbeatFromAgent.title)),
      check('and Back returns it to Agent, not Settings', backToAgent.list, JSON.stringify(backToAgent.list)),
    ], shotPath);
  }

  {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 420, height: 900, deviceScaleFactor: 1, mobile: true });
    await cdp.send('Page.navigate', { url: `${BASE}?scenario=ready&frame=0` });
    for (let attempt = 0; attempt < 60; attempt += 1) {
      const ready = await cdp.evaluate('!!document.querySelector(".device-home-robot")').catch(() => false);
      if (ready) break;
      await sleep(100);
    }
    await sleep(900);
    const view = await ui();
    const fits = await cdp.evaluate(`(() => {
      const el = document.querySelector('.device-home');
      if (!el) return null;
      return { scrollW: el.scrollWidth, clientW: el.clientWidth, scrollH: el.scrollHeight, clientH: el.clientHeight };
    })()`);
    await shot('screen-11-narrow-portrait.png');
    record('narrow-portrait-has-no-clipped-content', [
      check('the control is still reachable', !!view.robot),
      check('nothing overflows horizontally', fits && fits.scrollW <= fits.clientW + 1, JSON.stringify(fits)),
    ], 'screenshots/screen-11-narrow-portrait.png');
  }

  /**
   * A normal interruption mid-answer, and what the screen is allowed to keep.
   *
   * The fixture stops answering after `interrupt` status reads and publishes no
   * further words, so anything still on screen afterwards came from the hold
   * rather than from the wire. The three properties the brief names are checked
   * here: the words are retained, they are marked `Incomplete`, and no authority
   * boundary keeps them.
   */
  {
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&interrupt=4');
    // While the answer is still streaming: the live text, with the caret and no
    // marker. This is also the string the retained copy has to match exactly.
    let streaming = await ui();
    for (let attempt = 0; attempt < 40 && !streaming.answerStreaming; attempt += 1) {
      await sleep(120);
      streaming = await ui();
    }
    const path = await shot('screen-44-partial-answer-streaming.png');
    // Then wait for the interruption to land: no caret, marker present.
    let interrupted = await ui();
    for (let attempt = 0; attempt < 60 && !interrupted.answerIncomplete; attempt += 1) {
      await sleep(150);
      interrupted = await ui();
    }
    const path2 = await shot('screen-44-partial-answer-interrupted.png');
    record('a-partial-answer-survives-a-normal-interruption', [
      check('the live answer streamed with the caret and no marker', streaming.answerStreaming && !streaming.answerIncomplete, JSON.stringify({ caret: streaming.answerStreaming, marker: streaming.answerIncomplete })),
      check('the words the device sent are still on screen', (interrupted.answer ?? '').includes('Sealed and cool, about six months;'), String(interrupted.answer)),
      check('they are exactly what streamed, not duplicated or extended', (interrupted.answer ?? '').startsWith((streaming.answer ?? '').replace('▍', '').trim()), `streamed ${JSON.stringify(streaming.answer)} then ${JSON.stringify(interrupted.answer)}`),
      check('the marker says the answer never finished', interrupted.answerIncomplete === true, String(interrupted.answerIncomplete)),
      check('the caret is gone, because nothing is streaming', interrupted.answerStreaming === false, String(interrupted.answerStreaming)),
      check('the question it belonged to is kept with it', (interrupted.question ?? '').includes('How long does stored drinking water keep?'), String(interrupted.question)),
    ], path);
    results[results.length - 1].screenshots = [path, path2];

    // The same retained turn, seen from Chat: the words and the marker, rather
    // than the reconnecting placeholder that would replace them.
    await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
    await sleep(900);
    const turn = await turnUi();
    const path3 = await shot('screen-44-partial-answer-in-chat.png');
    record('the-retained-partial-is-marked-in-chat-too', [
      check('the turn is rendered at all', turn.exchanges >= 1, JSON.stringify(turn)),
      check('the retained answer is in it', (turn.answer ?? '').includes('Sealed and cool, about six months;'), String(turn.answer)),
      check('and it carries the marker', turn.incomplete === true, String(turn.incomplete)),
      check('so the placeholder is not shown instead', turn.message === null, String(turn.message)),
    ], path3);
  }

  // --- 45. New chat drops the retained partial ----------------------------
  {
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&interrupt=4');
    let interrupted = await ui();
    for (let attempt = 0; attempt < 60 && !interrupted.answerIncomplete; attempt += 1) {
      await sleep(150);
      interrupted = await ui();
    }
    // The drawer's explicit New chat, from Chat, which is where the control lives.
    await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
    await sleep(900);
    const chatsButton = await boxOf('button[aria-controls="device-chats"]');
    await click(chatsButton);
    await sleep(500);
    const newChat = await boxOf('.device-new-chat');
    await click(newChat);
    await sleep(1200);
    await click((await ui()).wordmark);
    await sleep(800);
    const after = await ui();
    const path = await shot('screen-45-partial-answer-cleared.png');
    record('new-chat-drops-the-retained-partial', [
      check('the partial was on screen before', interrupted.answerIncomplete === true && (interrupted.answer ?? '').includes('Sealed and cool'), String(interrupted.answer)),
      check('New chat was offered', !!newChat, JSON.stringify(newChat)),
      check('the retained answer is gone afterwards', !(after.answer ?? '').includes('Sealed and cool'), String(after.answer)),
      check('and no marker is left behind', after.answerIncomplete === false, String(after.answerIncomplete)),
    ], path);
  }

  // --- 46. An epoch rotation drops the retained partial -------------------
  {
    // `context=on` is what makes an authority move observable at all: the device
    // publishes the broker's current session context, so an epoch that moves
    // without the token changing is visible to the client's own seam.
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&context=on&interrupt=3');
    let view = await ui();
    for (let attempt = 0; attempt < 60 && !view.answerIncomplete; attempt += 1) {
      await sleep(150);
      view = await ui();
    }
    const held = view;
    // Move the authority the words were produced under. The retention is live at
    // this point — asserted below — so the disappearance can only be the fence.
    await cdp.evaluate('window.preview.rotateAuthority()');
    let after = view;
    for (let attempt = 0; attempt < 80 && ((after.answer ?? '').includes('Sealed and cool') || after.answerIncomplete); attempt += 1) {
      await sleep(150);
      after = await ui();
    }
    const path = await shot('screen-46-partial-answer-rotation.png');
    record('an-authority-rotation-drops-the-retained-partial', [
      check('the partial was retained before the authority moved', held.answerIncomplete === true && (held.answer ?? '').includes('Sealed and cool'), String(held.answer)),
      check('the protected words are gone once the epoch moves', !(after.answer ?? '').includes('Sealed and cool'), String(after.answer)),
      check('and the marker with them', after.answerIncomplete === false, String(after.answerIncomplete)),
    ], path);
  }

  // --- 47. Streaming text never doubles, and a released reader stays put --
  {
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&grow=1');
    // The caret is part of the paragraph's text, so it is stripped before the two
    // readings are compared: the words are what must be monotonic, not the cursor.
    const withoutCaret = (text) => String(text ?? '').replace('▍', '').trim();
    let view = await ui();
    for (let attempt = 0; attempt < 60 && !/Sentence 2/.test(view.answer ?? ''); attempt += 1) {
      await sleep(150);
      view = await ui();
    }
    const first = withoutCaret(view.answer);
    await sleep(1500);
    const grown = withoutCaret((await ui()).answer);
    const path = await shot('screen-47-streaming-text-grows.png');
    // Narrow portrait, where the reader overflows with far less text — the point
    // of this half is where the reader is, and a reader that cannot scroll cannot
    // show whether a released follow is respected.
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 420, height: 900, deviceScaleFactor: 1, mobile: true });
    await sleep(200);
    await click((await ui()).tiles.find((entry) => entry.destination === 'chat').box);
    await sleep(1500);
    let following = await readerUi();
    for (let attempt = 0; attempt < 60 && following.scrollHeight - following.clientHeight < 40; attempt += 1) {
      await sleep(200);
      following = await readerUi();
    }
    await sleep(900);
    const stillFollowing = await readerUi();
    // Release the follow by scrolling away from the bottom, then let more text
    // arrive: the reader must stay where the user left it.
    await cdp.evaluate(`(() => { const el = document.querySelector('#device-answer'); if (el) { el.scrollTop = 0; el.dispatchEvent(new Event('scroll', { bubbles: true })); } })()`);
    await sleep(400);
    const released = await readerUi();
    await sleep(2000);
    const afterGrowth = await readerUi();
    // Restore the framed panel for whatever follows.
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 820, height: 1700, deviceScaleFactor: 1, mobile: false });
    record('streaming-text-never-doubles-and-a-released-reader-stays-put', [
      check('the answer grows rather than restarting', grown.length > first.length && grown.startsWith(first), `${first.length} then ${grown.length} chars`),
      check('the device words appear exactly once', grown.split('Sealed and cool, about six months;').length === 2, grown.slice(0, 120)),
      check('no sentence is delivered twice', new Set(grown.match(/Sentence \d+/g) ?? []).size === (grown.match(/Sentence \d+/g) ?? []).length, grown.slice(-160)),
      check('the reader has something to scroll, so the follow is testable', following.scrollHeight - following.clientHeight >= 40, JSON.stringify(following)),
      check('the reader follows while it is at the bottom', Math.abs(stillFollowing.scrollTop - (stillFollowing.scrollHeight - stillFollowing.clientHeight)) <= 4, JSON.stringify(stillFollowing)),
      check('the reader can be released from the bottom', released.scrollTop === 0, JSON.stringify(released)),
      check('and later text does not yank it back', afterGrowth.scrollTop === released.scrollTop, `${released.scrollTop} then ${afterGrowth.scrollTop}`),
    ], path);
  }

  // --- 48. A refused authority reading drops the retained partial ----------
  {
    // The retention the previous scenarios establish is live here, and the
    // device's own wire is empty, so anything still on screen afterwards can
    // only have come from the hold — not from a reading that republished it.
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&context=on&interrupt=3');
    let view = await ui();
    for (let attempt = 0; attempt < 60 && !view.answerIncomplete; attempt += 1) {
      await sleep(150);
      view = await ui();
    }
    const held = view;
    // A reading that cannot be compared. This is the same entry point the poll
    // uses when a malformed context arrives, and the one another protected
    // surface uses when *it* is the surface that saw the refusal — which is the
    // whole reason the fix subscribes to the seam instead of adding a check to
    // this screen's poll.
    await cdp.evaluate(`window.fixtureAuthority.observeSessionContext('invalid')`);
    let after = view;
    for (let attempt = 0; attempt < 40 && ((after.answer ?? '').includes('Sealed and cool') || after.answerIncomplete); attempt += 1) {
      await sleep(150);
      after = await ui();
    }
    const path = await shot('screen-48-partial-answer-refused-authority.png');
    // The device goes on publishing the *same* context, so the polls that follow
    // re-establish an authority that reads identically to the one that was lost.
    // Nothing may return: the words went with the authority, not with the text.
    await sleep(2500);
    const restored = await ui();
    record('a-refused-authority-reading-drops-the-retained-partial', [
      check('the partial was retained under a live authority', held.answerIncomplete === true && (held.answer ?? '').includes('Sealed and cool'), String(held.answer)),
      check('the protected words are gone once the reading is refused', !(after.answer ?? '').includes('Sealed and cool'), String(after.answer)),
      check('and the marker with them', after.answerIncomplete === false, String(after.answerIncomplete)),
      check('restoring the same context does not revive them', !(restored.answer ?? '').includes('Sealed and cool') && restored.answerIncomplete === false, String(restored.answer)),
    ], path);
  }

  // --- 49. A replaced token drops the retained partial ---------------------
  {
    await load('?scenario=streaming&mic=on&reset=ok&frame=1&context=on&interrupt=3');
    let view = await ui();
    for (let attempt = 0; attempt < 60 && !view.answerIncomplete; attempt += 1) {
      await sleep(150);
      view = await ui();
    }
    const held = view;
    // Away and back. The context is derived from the epoch *and* the token, so
    // the round trip ends on a context that reads exactly like the original: a
    // rule that compared only the context would call that "unchanged" and let
    // the words outlive the session that produced them.
    await cdp.evaluate(`(() => {
      const seam = window.fixtureAuthority;
      const original = seam.getDeviceToken();
      seam.setDeviceToken('z'.repeat(43));
      seam.setDeviceToken(original);
    })()`);
    let after = view;
    for (let attempt = 0; attempt < 40 && ((after.answer ?? '').includes('Sealed and cool') || after.answerIncomplete); attempt += 1) {
      await sleep(150);
      after = await ui();
    }
    const path = await shot('screen-49-partial-answer-token-swap.png');
    await sleep(2000);
    const settled = await ui();
    record('a-replaced-token-drops-the-retained-partial', [
      check('the partial was retained before the token moved', held.answerIncomplete === true && (held.answer ?? '').includes('Sealed and cool'), String(held.answer)),
      check('the protected words do not survive the replacement', !(after.answer ?? '').includes('Sealed and cool'), String(after.answer)),
      check('and the marker is gone', after.answerIncomplete === false, String(after.answerIncomplete)),
      check('returning to the original token does not revive them', !(settled.answer ?? '').includes('Sealed and cool') && settled.answerIncomplete === false, String(settled.answer)),
    ], path);
  }
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* closing */ }
  chrome.kill('SIGTERM');
}

const failed = results.flatMap((entry) => entry.checks.filter((entryCheck) => !entryCheck.ok).map((entryCheck) => `${entry.scenario}: ${entryCheck.name} — ${entryCheck.detail}`));
writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify({ base: BASE, results, consoleErrors, failed }, null, 2)}\n`);
for (const entry of results) {
  console.log(`${entry.passed ? 'PASS' : 'FAIL'}  ${entry.scenario}  (${entry.checks.filter((c) => c.ok).length}/${entry.checks.length})  ${entry.screenshot}`);
  for (const entryCheck of entry.checks) if (!entryCheck.ok) console.log(`        ✗ ${entryCheck.name} — ${entryCheck.detail}`);
}
console.log(`\n${results.filter((entry) => entry.passed).length}/${results.length} scenarios passed.`);
if (consoleErrors.length) console.log(`Console errors: ${consoleErrors.slice(0, 5).join(' | ')}`);
else console.log('Console errors: none');
process.exit(failed.length === 0 && consoleErrors.length === 0 ? 0 : 1);
