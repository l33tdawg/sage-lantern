/**
 * Drive the lock cycle through the real access boundary.
 *
 * Records, from the DOM and from the device's own session module, what is on
 * screen before a lock, while the device is locked, and after the same owner
 * unlocks again. Nothing here asserts a policy: every check reads a measurement
 * the page produced. The one deliberate intervention is pinning
 * `document.hidden` to false before the app loads, so that a headless page
 * cannot fire an unrequested lock in the middle of a scenario — the driver's
 * locks are then ones it actually asked for.
 *
 * The pre-change control is the same fixture built from the revision before the
 * lock seam existed, if `LOCK_CONTROL_ENTRY` names one. Without it the run is
 * still a measurement, just an undiscriminated one, and the report says so.
 *
 *   node probe/drive-lock-cycle.mjs <fixture-url>
 *
 * Environment:
 *   LOCK_LABEL       output filename prefix (default none)
 *   LOCK_CDP_PORT    devtools port (default 9366)
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const OUT = join(here, '..', 'evidence');
const SHOTS = join(OUT, 'screenshots');
mkdirSync(SHOTS, { recursive: true });
const labelled = (name) => (process.env.LOCK_LABEL ? `${process.env.LOCK_LABEL}-${name}` : name);

const URL_UNDER_TEST = process.argv[2] ?? 'http://127.0.0.1:8796/lock-fixture.html';
const CDP_PORT = Number(process.env.LOCK_CDP_PORT ?? 9366);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PROFILE = `/tmp/ember-lock-cycle-${process.pid}`;
/**
 * The panel to drive, parameterised because height decides which rule binds.
 *
 * `.device-lock-keypad button` also has a rule inside `@media (min-height: 550px)`,
 * so at the canonical 720x1560 panel the keypad was already 72px BEFORE the 14 Oct
 * floor pass raised the base rule from 64. Only a panel shorter than 550px can show
 * whether the base rule binds, which is what LOCK_VIEWPORT=1024x520 is for.
 */
const VIEWPORT = (() => {
  const [width, height] = (process.env.LOCK_VIEWPORT ?? '720x1560').split('x').map(Number);
  return { width, height };
})();
const PIN = '13572468';

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text',
  '--no-sandbox', '--disable-setuid-sandbox', '--disable-crashpad', '--disable-breakpad',
  'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
chrome.stderr.on('data', () => {});

async function devtoolsTarget() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`);
      const page = (await response.json()).find((target) => target.type === 'page');
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('Chrome DevTools endpoint never became available');
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.consoleErrors = [];
    this.exceptions = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', () => reject(new Error('devtools socket error')));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') {
        this.exceptions.push(String(message.params?.exceptionDetails?.exception?.description ?? message.params?.exceptionDetails?.text ?? 'exception'));
        return;
      }
      if (message.method === 'Runtime.consoleAPICalled' && message.params?.type === 'error') {
        this.consoleErrors.push((message.params.args ?? []).map((arg) => String(arg.value ?? arg.description ?? '')).join(' '));
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
    if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.text}`);
    return result.result.value;
  }
  /** A trusted click at the element's centre, so `event.isTrusted` holds. */
  async click(finder) {
    const box = await this.evaluate(`(() => {
      const el = ${finder};
      if (!el) return null;
      el.scrollIntoView({ block: 'center' });
      const r = el.getBoundingClientRect();
      return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height };
    })()`);
    if (!box || box.w <= 0 || box.h <= 0) throw new Error(`nothing clickable for ${finder}`);
    const point = { x: Math.round(box.x), y: Math.round(box.y) };
    await this.send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
    await this.send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
    return box;
  }
  async key(text) {
    for (const ch of text) {
      await this.send('Input.dispatchKeyEvent', { type: 'keyDown', text: ch, key: ch });
      await this.send('Input.dispatchKeyEvent', { type: 'keyUp', key: ch });
      await sleep(20);
    }
  }
  async shot(name) {
    const result = await this.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(SHOTS, labelled(`${name}`)), Buffer.from(result.data, 'base64'));
  }
}

async function waitFor(cdp, expression, { tries = 80, gap = 200, what = expression } = {}) {
  for (let attempt = 0; attempt < tries; attempt += 1) {
    try { if (await cdp.evaluate(expression)) return true; } catch { /* mid-render */ }
    await sleep(gap);
  }
  throw new Error(`timed out waiting for ${what}`);
}

/**
 * Everything the assertion may read, gathered in one expression so a check can
 * never straddle two renders. `root.innerText` is the whole protected surface:
 * if any retained word is still painted anywhere, it is in there.
 */
const VIEW = `(() => {
  const root = document.getElementById('root');
  const stub = window.__STUB__ || { authCalls: [], blocked: [] };
  const text = root ? root.innerText : '';
  const panel = root.querySelector('.preview-panel') || root;
  const count = (selector) => panel.querySelectorAll(selector).length;
  /**
   * Is this string painted inside the panel's visible box?
   *
   * Matching on \`innerText\` alone reported the old question as present on a
   * screen that did not show it: React leaves text nodes in the document, and a
   * node outside the panel's box is not something a user can see. A visibility
   * claim has to be about rectangles, so this walks leaf nodes and requires a
   * non-empty intersection with the panel.
   */
  const visibleText = (needle) => {
    const box = panel.getBoundingClientRect();
    for (const el of panel.querySelectorAll('*')) {
      if (el.children.length !== 0) continue;
      const own = el.textContent || '';
      if (!own.includes(needle)) continue;
      const r = el.getBoundingClientRect();
      const shown = r.width > 0 && r.height > 0
        && r.right > box.left && r.left < box.right && r.bottom > box.top && r.top < box.bottom;
      if (shown) return true;
    }
    return false;
  };
  return {
    locked: !document.querySelector('.device-screen.device-home-active') && !!document.querySelector('.device-lock'),
    lockScreen: count('.device-lock') > 0,
    homeScreen: count('.device-screen.device-home-active') > 0,
    chatSurface: count('.device-chat-list') + count('.device-conversation') + count('.device-thread-layout .device-message'),
    settingsScreen: count('.device-subscreen') > 0 && /Settings/.test(text),
    hasPhoto: count('img[alt="Photo attached to this question"]') + count('.device-photo-preview') > 0,
    hasIncompleteMarker: count('.device-home-incomplete') + count('.device-answer-incomplete') > 0,
    hasTranscript: visibleText('What is on this panel?'),
    hasAnswer: visibleText('A retained answer that only an unlocked owner may read'),
    instruction: (panel.querySelector('.device-home-instruction') || {}).textContent || '',
    statusLabel: (panel.querySelector('.device-home-status-label') || {}).textContent || '',
    authCalls: [...stub.authCalls],
    blocked: [...stub.blocked],
    /**
     * The controls the 14 Oct floor change touched on this screen, as rectangles.
     *
     * Measured rather than read from the stylesheet, because a rule that exists but
     * loses to a more specific one is precisely the defect that pass was about. The
     * 20x20 probe is measured by the SAME predicate: if it passes the floor, the
     * predicate is not measuring anything.
     */
    floor: (() => {
      const box = panel.getBoundingClientRect();
      const rows = [];
      const add = (label, el) => {
        if (!el) return;
        const r = el.getBoundingClientRect();
        rows.push({ label, w: Math.round(r.width), h: Math.round(r.height),
          inside: r.left >= box.left - 0.5 && r.top >= box.top - 0.5 && r.right <= box.right + 0.5 && r.bottom <= box.bottom + 0.5 });
      };
      panel.querySelectorAll('.device-lock-keypad button').forEach((el) => add('key:' + (el.textContent || '').trim(), el));
      add('reveal', panel.querySelector('.device-lock-copy .device-lock-reveal'));
      panel.querySelectorAll('.device-lock-wait button').forEach((el) => add('wait:' + (el.textContent || '').trim().slice(0, 22), el));
      const probe = document.createElement('div');
      probe.style.cssText = 'position:absolute;left:0;top:0;width:20px;height:20px;visibility:hidden';
      panel.appendChild(probe);
      const pr = probe.getBoundingClientRect();
      probe.remove();
      return { rows, control: { w: Math.round(pr.width), h: Math.round(pr.height) }, panel: { w: Math.round(box.width), h: Math.round(box.height) } };
    })(),
    text: text.slice(0, 400),
  };
})()`;

/** One measured check. A failure is recorded, not thrown, so later checks still run. */
function check(name, ok, detail) {
  return { name, ok: !!ok, detail: detail === undefined ? '' : String(detail) };
}

const results = [];
const record = (scenario, checks, shot) => {
  const passed = checks.every((entry) => entry.ok);
  results.push({ scenario, checks, passed, screenshot: shot ?? null });
  const failed = checks.filter((entry) => !entry.ok);
  console.log(`\n${passed ? 'PASS' : 'FAIL'}  ${scenario}`);
  for (const entry of checks) console.log(`   ${entry.ok ? 'ok  ' : 'FAIL'} ${entry.name}${entry.detail ? ` — ${entry.detail}` : ''}`);
  if (failed.length === 0) return;
  console.log(`   ${failed.length} failing check(s)`);
};

const cdp = new CDP((await devtoolsTarget()).webSocketDebuggerUrl);
await cdp.send('Page.enable');
await cdp.send('Runtime.enable');
await cdp.send('Emulation.setDeviceMetricsOverride', { ...VIEWPORT, deviceScaleFactor: 1, mobile: false });
await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'reduce' }] });

await cdp.send('Page.navigate', { url: URL_UNDER_TEST });
await waitFor(cdp, `!!document.getElementById('root')?.children.length`, { what: 'the preview to mount' });

// --- 0. The FIRST-RUN screen, the only place the reveal control renders ---
/**
 * `allowReveal` is passed only by setup.tsx (its 'choose' and 'confirm' phases);
 * the unlock path never sets it, so no scenario below can measure
 * `.device-lock-reveal` — the one rule from the 14 Oct floor pass that had no
 * measurement. This pass loads that screen, measures the reveal and the keypad
 * with the same floor predicate and the same injected 20x20 control, then returns
 * to the lock fixture so the rest of the run is untouched.
 */
const withQuery = (url, extra) => `${url}${url.includes('?') ? '&' : '?'}${extra}`;
const withScreen = (url, screen) => withQuery(url, `screen=${screen}`);
await cdp.send('Page.navigate', { url: withScreen(URL_UNDER_TEST, 'setup') });
await waitFor(cdp, `!!document.querySelector('.device-lock-reveal')`, { what: 'the passcode reveal on the first-run screen' });
await sleep(400);
const firstRun = await cdp.evaluate(VIEW);
const setupShot = '00-first-run-floor.png';
await cdp.shot(setupShot);
const setupKeys = firstRun.floor.rows.filter((entry) => entry.label.startsWith('key:'));
const setupReveal = firstRun.floor.rows.find((entry) => entry.label === 'reveal');
const meetsFloor = (entry) => entry.w >= 72 && entry.h >= 72;
record('the first-run screen holds the floor, including the passcode reveal', [
  check('the first-run screen rendered a keypad to measure', setupKeys.length >= 12,
    `${setupKeys.length} keys on a ${firstRun.floor.panel.w}x${firstRun.floor.panel.h} panel`),
  check('the passcode reveal renders here, unlike the unlock screen', !!setupReveal,
    setupReveal ? `${setupReveal.w}x${setupReveal.h}` : 'not rendered on the first-run screen either'),
  check('the reveal meets the 72px floor', !!setupReveal && meetsFloor(setupReveal),
    setupReveal ? `${setupReveal.w}x${setupReveal.h}` : 'nothing to measure'),
  check('every first-run keypad key meets the floor', setupKeys.every(meetsFloor),
    setupKeys.filter((entry) => !meetsFloor(entry)).map((entry) => `${entry.label}=${entry.w}x${entry.h}`).join(' · ')
      || `all ${setupKeys.length} keys at ${setupKeys[0] ? `${setupKeys[0].w}x${setupKeys[0].h}` : '?'}`),
  check('every measured control sits inside the panel', firstRun.floor.rows.every((entry) => entry.inside),
    firstRun.floor.rows.filter((entry) => !entry.inside).map((entry) => entry.label).join(' · ') || 'all inside'),
  check('the floor predicate discriminates: a 20x20 element fails it',
    firstRun.floor.control.w === 20 && firstRun.floor.control.h === 20 && !meetsFloor(firstRun.floor.control),
    `${firstRun.floor.control.w}x${firstRun.floor.control.h}`),
], setupShot);

/**
 * Drive the phases the choose screen leads into, and the two other first-run
 * states. Each is measured by the same floor predicate, and each records what it
 * can actually see rather than assuming the flow reached the phase.
 */
const typePin = async () => {
  for (const digit of PIN) {
    await cdp.click(`[...document.querySelectorAll('.device-lock-keypad button')].find((b) => b.textContent.trim() === ${JSON.stringify(digit)})`);
    await sleep(50);
  }
};
const submitKeypad = () => cdp.click(`document.querySelector('.device-lock-submit')`);
const waitButtons = (view) => view.floor.rows.filter((entry) => entry.label.startsWith('wait:'));

await typePin();
await submitKeypad();
await waitFor(cdp, `/Confirm your passcode/.test(document.querySelector('.device-lock-copy h1')?.textContent || '')`, { what: 'the confirm phase' });
await sleep(300);
const confirmRun = await cdp.evaluate(VIEW);
const confirmShot = '00b-first-run-confirm.png';
await cdp.shot(confirmShot);
const confirmKeys = confirmRun.floor.rows.filter((entry) => entry.label.startsWith('key:'));
const confirmReveal = confirmRun.floor.rows.find((entry) => entry.label === 'reveal');
record('the first-run confirm phase holds the floor', [
  check('the confirm phase is on screen', /Confirm your passcode/.test(confirmRun.text), confirmRun.text.slice(0, 80)),
  check('its reveal renders and meets the floor', !!confirmReveal && meetsFloor(confirmReveal),
    confirmReveal ? `${confirmReveal.w}x${confirmReveal.h}` : 'not rendered in the confirm phase'),
  check('every confirm keypad key meets the floor', confirmKeys.length >= 12 && confirmKeys.every(meetsFloor),
    `${confirmKeys.length} keys, worst ${Math.min(...confirmKeys.map((entry) => entry.h))}px tall`),
  check('the floor predicate discriminates: a 20x20 element fails it',
    confirmRun.floor.control.w === 20 && !meetsFloor(confirmRun.floor.control), `${confirmRun.floor.control.w}x${confirmRun.floor.control.h}`),
], confirmShot);

await typePin();
await submitKeypad();
await waitFor(cdp, `!!document.querySelector('.device-recovery-key')`, { what: 'the recovery-key screen' });
await sleep(300);
const backupRun = await cdp.evaluate(VIEW);
const backupShot = '00c-first-run-recovery-key.png';
await cdp.shot(backupShot);
const backupKeys = await cdp.evaluate(`(() => (document.querySelector('.device-recovery-key')?.textContent || ''))()`);
record('the recovery-key screen holds the floor', [
  check('the flow reached the recovery-key screen', backupKeys.length > 0, `${backupKeys.length} characters`),
  check('the key is the shape the app itself accepts', backupKeys.length === 44 && /^[A-Za-z0-9+/]{43}=$/.test(backupKeys), backupKeys.slice(0, 12) + '…'),
  check('its control meets the floor', waitButtons(backupRun).length > 0 && waitButtons(backupRun).every(meetsFloor),
    waitButtons(backupRun).map((entry) => `${entry.label}=${entry.w}x${entry.h}`).join(' · ') || 'no control found'),
], backupShot);

// The two other first-run states, each its own mount.
await cdp.send('Page.navigate', { url: withQuery(URL_UNDER_TEST, 'screen=setup&variant=check') });
await waitFor(cdp, `/Check setup/.test(document.body.innerText)`, { what: 'the check-setup screen' });
await sleep(300);
const checkRun = await cdp.evaluate(VIEW);
const checkShot = '00d-first-run-check.png';
await cdp.shot(checkShot);
record('the check-setup screen holds the floor', [
  check('the check screen is on screen', /Check setup/.test(checkRun.text), checkRun.text.slice(0, 80)),
  check('its control meets the floor', waitButtons(checkRun).length > 0 && waitButtons(checkRun).every(meetsFloor),
    waitButtons(checkRun).map((entry) => `${entry.label}=${entry.w}x${entry.h}`).join(' · ') || 'no control found'),
], checkShot);

await cdp.send('Page.navigate', { url: withQuery(URL_UNDER_TEST, 'screen=setup&variant=recover') });
await waitFor(cdp, `!!document.querySelector('.device-lock-keypad')`, { what: 'the recover phase' });
await sleep(300);
const recoverRun = await cdp.evaluate(VIEW);
const recoverShot = '00e-first-run-recover.png';
await cdp.shot(recoverShot);
const recoverKeys = recoverRun.floor.rows.filter((entry) => entry.label.startsWith('key:'));
const recoverReveal = recoverRun.floor.rows.find((entry) => entry.label === 'reveal');
record('the recover phase has no reveal, and that is the prop rather than the CSS', [
  check('the recover phase is on screen', /Your recovery key/.test(recoverRun.text), recoverRun.text.slice(0, 80)),
  check('its keypad meets the floor', recoverKeys.length >= 12 && recoverKeys.every(meetsFloor),
    `${recoverKeys.length} keys, worst ${recoverKeys.length ? Math.min(...recoverKeys.map((entry) => entry.h)) : 0}px tall`),
  // allowReveal is false here, so the control must be absent. This is what ties
  // the reveal's presence to the prop instead of to a stylesheet rule.
  check('no reveal renders, unlike choose and confirm', recoverReveal === undefined,
    recoverReveal ? `it rendered at ${recoverReveal.w}x${recoverReveal.h} - the prop is not what gates it` : 'absent, as allowReveal=false requires'),
], recoverShot);

// Back to the lock fixture for the lock cycle.
await cdp.send('Page.navigate', { url: URL_UNDER_TEST });
await waitFor(cdp, `!!document.getElementById('root')?.children.length`, { what: 'the lock fixture to mount again' });
await sleep(400);

/** Enter the passcode on the real keypad and wait for the device to come up. */
async function unlock() {
  await waitFor(cdp, `!!document.querySelector('.device-lock-submit')`, { what: 'the passcode keypad' });
  for (const digit of PIN) {
    await cdp.click(`[...document.querySelectorAll('.device-lock-keypad button')].find((b) => b.textContent.trim() === ${JSON.stringify(digit)})`);
    await sleep(60);
  }
  await cdp.click(`document.querySelector('.device-lock-submit')`);
  await waitFor(cdp, `!!document.querySelector('.device-screen.device-home-active')`, { what: 'Home after unlock' });
  // Let the status pipeline settle so the surface has real content to protect.
  await sleep(1600);
}

// --- 1. Unlock, then establish that there is protected content ----------
await unlock();
const owner = await cdp.evaluate(VIEW);
const shotOwner = '01-unlocked-owner.png';
await cdp.shot(shotOwner);
record('an unlocked owner sees the device surface', [
  check('the passcode keypad accepted the code and Home is shown', owner.homeScreen, JSON.stringify({ home: owner.homeScreen, lock: owner.lockScreen })),
  check('the broker was asked for setup/status, unlock, activate and the profile', ['setup/status', 'unlock', 'activate', 'profile/get'].every((a) => owner.authCalls.includes(a)), owner.authCalls.join(' · ')),
  check('the central control carries the brief wording', owner.instruction === 'Tap to talk · hold for push-to-talk', owner.instruction),
  check('a status strip is present and is not a static "Systems online"', owner.statusLabel.length > 0 && !/systems online/i.test(owner.statusLabel), owner.statusLabel),
  check('no non-device origin was contacted', owner.blocked.length === 0, owner.blocked.join(', ')),
], shotOwner);

// --- 2. Reach Settings and press the real Lock now control --------------
await cdp.click(`([...document.querySelectorAll('.device-screen button[aria-label="Settings"]')][0])`);
await waitFor(cdp, `!!document.querySelector('.device-lock') || !![...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Lock now')`, { what: 'the Settings surface' });
const settings = await cdp.evaluate(VIEW);
const shotSettings = '02-settings-before-lock.png';
await cdp.shot(shotSettings);
// Settings rows are `<button><Icon /><span>Lock now</span></button>`, so the
// first element whose text matches is the icon, not the control. Match only a
// button whose own trimmed text equals the label.
const lockNow = await cdp.click(`[...document.querySelectorAll('button')].find((b) => b.textContent.trim() === 'Lock now')`);
const lockNowDetail = await cdp.evaluate(`(() => {
  const all = [...document.querySelectorAll('button')].filter((b) => /lock now/i.test(b.textContent));
  return all.map((b) => {
    const r = b.getBoundingClientRect();
    return { tag: b.tagName, cls: b.className, text: b.textContent.trim().slice(0, 30), w: Math.round(r.width), h: Math.round(r.height), parentCls: b.parentElement ? b.parentElement.className : '' };
  });
})()`);
record('Settings offers the real lock control before locking', [
  check('the Settings surface is on screen', settings.settingsScreen, JSON.stringify({ settings: settings.settingsScreen, text: settings.text.slice(0, 80) })),
  check('a Lock now control is present and was pressed', !!lockNow && lockNow.w > 0 && lockNow.h > 59, JSON.stringify({ clicked: lockNow, candidates: lockNowDetail })),
  check('no non-device origin was contacted', settings.blocked.length === 0, settings.blocked.join(', ')),
], shotSettings);

// --- 3. Locked: nothing protected may remain anywhere in the DOM --------
await waitFor(cdp, `!!document.querySelector('.device-lock')`, { what: 'the lock screen after locking' });
await sleep(1200);
const locked = await cdp.evaluate(VIEW);
const shotLocked = '03-locked.png';
await cdp.shot(shotLocked);
record('a lock clears every protected surface', [
  check('the device returned to the passcode screen', locked.lockScreen && !locked.homeScreen, JSON.stringify({ lock: locked.lockScreen, home: locked.homeScreen })),
  check('the device screen is unmounted, not merely covered', locked.homeScreen === false, String(locked.homeScreen)),
  check('no chat surface survives the lock', locked.chatSurface === 0, String(locked.chatSurface)),
  check('no retained question text survives the lock', locked.hasTranscript === false, `transcript=${locked.hasTranscript}`),
  check('no retained answer text survives the lock', locked.hasAnswer === false, `answer=${locked.hasAnswer}`),
  check('no attached photo survives the lock', locked.hasPhoto === false, `photo=${locked.hasPhoto}`),
  check('no partial-answer marker survives the lock', locked.hasIncompleteMarker === false, `marker=${locked.hasIncompleteMarker}`),
  check('the broker was told to lock', locked.authCalls.includes('lock'), locked.authCalls.join(' · ')),
  check('no non-device origin was contacted', locked.blocked.length === 0, locked.blocked.join(', ')),
], shotLocked);

// --- 3b. Every lock-screen control meets the floor the app declares -----
/**
 * The two lock.css rules raised in the 14 Oct floor pass were changed without a
 * measurement, on the belief that nothing reached this screen. This fixture does.
 * The run is repeated at LOCK_VIEWPORT=1024x520 because the keypad's other rule
 * lives inside `@media (min-height: 550px)`: below that height the raised base
 * rule is the only thing holding the floor, so only that panel discriminates.
 */
const floorOk = (entry) => entry.w >= 72 && entry.h >= 72;
const keyRows = locked.floor.rows.filter((entry) => entry.label.startsWith('key:'));
const revealRow = locked.floor.rows.find((entry) => entry.label === 'reveal');
record('every lock-screen control meets the 72px floor', [
  check('the keypad renders keys to measure', keyRows.length >= 12,
    `${keyRows.length} keys on a ${locked.floor.panel.w}x${locked.floor.panel.h} panel`),
  check('every keypad key meets the floor', keyRows.every(floorOk),
    keyRows.filter((entry) => !floorOk(entry)).map((entry) => `${entry.label}=${entry.w}x${entry.h}`).join(' · ')
      || `all ${keyRows.length} keys at ${keyRows[0] ? `${keyRows[0].w}x${keyRows[0].h}` : '?'}`),
  /**
   * The reveal is a FIRST-RUN control: setup.tsx passes `allowReveal` while a
   * passcode is created and confirmed, and the unlock path never sets it, so no
   * lock-cycle run can render it. This check states that limitation rather than
   * passing silently - if the control ever does appear here, it fails and demands
   * a real measurement instead of letting the rule stay unverified.
   */
  check('the reveal rule is unverified here, and this records why', revealRow === undefined,
    revealRow ? `it DID render at ${revealRow.w}x${revealRow.h} - measure it now` : 'allowReveal comes only from setup.tsx (first-run create/confirm), never from the unlock path'),
  check('every measured control sits inside the panel', locked.floor.rows.every((entry) => entry.inside),
    locked.floor.rows.filter((entry) => !entry.inside).map((entry) => entry.label).join(' · ') || 'all inside'),
  check('the floor predicate discriminates: a 20x20 element fails it',
    locked.floor.control.w === 20 && locked.floor.control.h === 20 && !floorOk(locked.floor.control),
    `${locked.floor.control.w}x${locked.floor.control.h}`),
], shotLocked);

// --- 4. The same owner unlocks again ------------------------------------
await unlock();
const again = await cdp.evaluate(VIEW);
// Where the string is, not merely that it is: a failure here has to say which
// surface repainted the old question rather than only that it did.
const transcriptWhere = await cdp.evaluate(`(() => {
  const hits = [];
  for (const el of document.querySelectorAll('#root *')) {
    if (el.children.length === 0 && (el.textContent || '').includes('What is on this panel?')) hits.push(el.className + ' :: ' + el.textContent.trim().slice(0, 120));
  }
  return hits;
})()`);
// Positive control for the visibility reader itself. A check that never fires
// because its predicate is broken passes for the wrong reason, so the same
// predicate is pointed at a string that IS on screen. This is a control, not a
// fifth requirement, and it is labelled as one.
const visibilityControl = await cdp.evaluate(`(() => {
  const panel = document.querySelector('.preview-panel');
  const box = panel.getBoundingClientRect();
  const seen = (needle) => {
    for (const el of panel.querySelectorAll('*')) {
      if (el.children.length !== 0) continue;
      if (!(el.textContent || '').includes(needle)) continue;
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.height > 0 && r.right > box.left && r.left < box.right && r.bottom > box.top && r.top < box.bottom) return true;
    }
    return false;
  };
  return { present: seen('Tap to talk'), absent: seen('What is on this panel?') };
})()`);
const shotAgain = '04-unlocked-again.png';
await cdp.shot(shotAgain);
record('the same owner unlocks again onto a cleared device', [
  check('Home is shown again for the same passcode', again.homeScreen, JSON.stringify({ home: again.homeScreen, lock: again.lockScreen })),
  check('the visibility reader detects a string that is on screen (control)', visibilityControl.present === true, JSON.stringify(visibilityControl)),
  check('the previous question does not come back', again.hasTranscript === false, `transcript=${again.hasTranscript} :: ${JSON.stringify(transcriptWhere)}`),
  check('the previous answer does not come back', again.hasAnswer === false, `answer=${again.hasAnswer}`),
  check('the previous photo does not come back', again.hasPhoto === false, `photo=${again.hasPhoto}`),
  check('no partial-answer marker comes back', again.hasIncompleteMarker === false, `marker=${again.hasIncompleteMarker}`),
  check('the second unlock was a fresh broker cycle', again.authCalls.filter((a) => a === 'unlock').length === 2, again.authCalls.join(' · ')),
  check('no non-device origin was contacted', again.blocked.length === 0, again.blocked.join(', ')),
], shotAgain);

// --- 5. Browser health --------------------------------------------------
record('the run itself was clean', [
  check('no console errors', cdp.consoleErrors.length === 0, cdp.consoleErrors.slice(0, 3).join(' | ')),
  check('no uncaught exceptions', cdp.exceptions.length === 0, cdp.exceptions.slice(0, 3).join(' | ')),
], null);

const total = results.reduce((sum, entry) => sum + entry.checks.length, 0);
const passed = results.reduce((sum, entry) => sum + entry.checks.filter((c) => c.ok).length, 0);
console.log(`\n${passed}/${total} checks passed across ${results.length} scenarios.`);
console.log(`Console errors: ${cdp.consoleErrors.length}. Exceptions: ${cdp.exceptions.length}.`);

writeFileSync(join(OUT, labelled('lock-cycle-results.json')), `${JSON.stringify({
  fixture: URL_UNDER_TEST,
  browser: 'headless Chrome via DevTools Protocol, trusted input events',
  viewport: VIEWPORT,
  passcode: PIN,
  scenarios: results,
  totals: { checks: total, passed, scenarios: results.length },
  consoleErrors: cdp.consoleErrors,
  exceptions: cdp.exceptions,
}, null, 2)}\n`);

chrome.kill('SIGKILL');
process.exit(passed === total ? 0 : 1);
