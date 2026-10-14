/**
 * Drive the Settings screen in the real integrated preview.
 *
 * This is the 2b remainder: `SECONDARY_SCREEN_DECISIONS.md` decides the Settings
 * contract (grouped control surface, existing bindings reused, no inferred
 * success before an authoritative reply, Home -> Settings -> Home navigation),
 * and none of the 42 scenarios in `drive-screen-fixture.mjs` touch Settings at
 * all. Running the frozen driver proves nothing about this screen.
 *
 * Additive by design, so the frozen 42-scenario record stays byte-identical and
 * each set can be accepted or rejected on its own:
 *
 *   node drive-settings-fixture.mjs [base-url] [label]
 *
 * Writes `settings-review-results.json` and `screenshots/settings-*.png` beside
 * this file. It does not read or write `screen-gesture-results.json`.
 *
 * A label redirects every output to `settings-<label>-*` instead, so a
 * corrected run can be recorded beside the pre-correction one without
 * overwriting it:
 *
 *   node drive-settings-fixture.mjs http://127.0.0.1:8791/screen-fixture.html correction
 *
 * What this fixture CAN exercise: the real `settings.tsx` inside the real
 * `screen.tsx`, with `history/list` served and every Settings *write* refused
 * (3197 answers 404 for `history/settings`; 3196 is not served at all, so the
 * power and lock panels take their real failure branch). A refused write is
 * exactly the "failed settings acknowledgement" case the decisions require, so
 * the fixture's limitations and the required evidence happen to coincide here.
 *
 * What it CANNOT exercise, and therefore does not claim: the name/profile panel
 * and "Lock now" need `profile`/`onProfileChange`/`onLock`, which the preview
 * does not pass to `DeviceScreen`; and no Settings write ever succeeds, so a
 * successful change is not measured anywhere in this file.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8791/screen-fixture.html';
const LABEL = process.argv[3] ?? '';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9336;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

/** Output names, so a labelled run cannot overwrite the unlabelled record. */
const RESULTS_FILE = LABEL ? `settings-${LABEL}-results.json` : 'settings-review-results.json';
const shotFile = (name) => (LABEL ? name.replace(/^settings-/, `settings-${LABEL}-`) : name);
/** Label a recorded screenshot path, once: scenarios pass literal paths. */
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

const profile = `/tmp/ember-settings-fixture-${process.pid}`;
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

  async function load(query) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 820, height: 1700, deviceScaleFactor: 1, mobile: false });
    await cdp.send('Page.navigate', { url: `${BASE}${query}` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.preview && !!document.querySelector(".device-screen")').catch(() => false);
      if (ready) {
        // Same reason as the integrated driver: a stale hidden flag persists
        // across navigations, and a hidden document changes what the app is
        // allowed to do.
        await cdp.send('Page.bringToFront').catch(() => {});
        await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
        // The transport log is position:fixed across the viewport bottom and
        // would swallow trusted clicks on the panel's last rows. Every
        // assertion reads the log through probe(), so hiding it costs nothing.
        await cdp.evaluate(`(() => { const el = document.querySelector('.preview-log'); if (el) el.style.display = 'none'; })()`).catch(() => {});
        await sleep(1400);
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

  /** What actually holds focus, and which surface it belongs to. */
  const focusInfo = () => cdp.evaluate(`(() => {
    const a = document.activeElement;
    if (!a) return null;
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    return {
      tag: a.tagName,
      className: typeof a.className === 'string' ? a.className : '',
      label: a.getAttribute('aria-label'),
      text: clean(a.textContent).slice(0, 40),
      isGear: a.classList.contains('device-home-settings'),
      isMenuTrigger: a.getAttribute('aria-haspopup') === 'dialog',
      isSwitch: a.getAttribute('role') === 'switch',
      inDialog: !!(a.closest && a.closest('[role="alertdialog"]')),
      inSettings: !!(a.closest && a.closest('section[aria-label="Settings"]')),
    };
  })()`);

  /** A real mouse click at the centre of a selector; null when it cannot be hit. */
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

  /** One entry of the device menu, found by its own visible text. */
  const menuEntry = (text) => cdp.evaluate(`(() => {
    const el = [...document.querySelectorAll('.device-menu button')].find((b) => (b.textContent || '').trim().endsWith(${JSON.stringify(text)}));
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled };
  })()`);

  /** The real Settings DOM, read the way a user or a screen reader sees it. */
  const settingsUI = () => cdp.evaluate(`(() => {
    const root = document.querySelector('section[aria-label="Settings"]');
    const rect = (el) => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled }; };
    const box = (selector) => { const el = root && root.querySelector(selector); return el ? rect(el) : null; };
    const switchOn = (label) => {
      const el = root && root.querySelector('button[role="switch"][aria-label="' + label + '"]');
      if (!el) return null;
      return { ...rect(el), checked: el.getAttribute('aria-checked'), text: (el.textContent || '').trim() };
    };
    const clean = (value) => (value || '').replace(/\\s+/g, ' ').trim();
    return {
      home: !!document.querySelector('.device-home'),
      chat: !!document.querySelector('.device-main'),
      settingsOpen: !!root,
      title: root ? clean(root.querySelector('.device-page-title h1')?.textContent) : null,
      back: box('.device-subheader button'),
      panelHeadings: root ? [...root.querySelectorAll('.device-panel h2')].map((h) => clean(h.textContent)) : [],
      sectionLabels: root ? [...root.querySelectorAll('.device-section-label')].map((s) => clean(s.textContent)) : [],
      panels: root ? [...root.querySelectorAll('.device-panel')].map((p) => ({ label: p.getAttribute('aria-label'), heading: clean(p.querySelector('h2')?.textContent), text: clean(p.textContent).slice(0, 240), selects: p.querySelectorAll('select').length })) : [],
      saveQuestions: switchOn('Save questions'),
      keepPhotos: switchOn('Keep attached photos'),
      dialog: root ? clean(root.querySelector('[role="alertdialog"]')?.textContent).slice(0, 200) : null,
      dialogHeading: root ? clean(root.querySelector('[role="alertdialog"] h2')?.textContent) : null,
      dialogPrimary: box('[role="alertdialog"] .device-action-primary'),
      dialogCancel: box('[role="alertdialog"] .device-actions button'),
      anyCheckedTrue: root ? [...root.querySelectorAll('[role="switch"]')].some((s) => s.getAttribute('aria-checked') === 'true') : false,
      alerts: root ? [...root.querySelectorAll('[role="alert"]')].map((a) => clean(a.textContent)) : [],
      destinations: root ? [...root.querySelectorAll('a,button')].map((e) => clean(e.textContent)).filter((t) => /^(Agent|Heartbeat|Hardware)\\b/.test(t)) : [],
      active: (() => { const a = document.activeElement; return a ? { tag: a.tagName, text: clean(a.textContent).slice(0, 30), insideSettings: !!(root && root.contains(a)) } : null; })(),
    };
  })()`);

  const press = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  const release = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  const click = async (point) => { await press(point); await sleep(60); await release(point); await sleep(700); };
  const escape = async () => {
    for (const type of ['keyDown', 'keyUp']) {
      await cdp.send('Input.dispatchKeyEvent', { type, key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
    }
    await sleep(500);
  };
  /**
   * Real keyboard input.
   *
   * Nothing in this file calls `element.focus()`: the correction requires the
   * keyboard path to be *proved*, and a programmatic focus call proves only that
   * the dialog can hold focus once somebody puts it there.
   */
  const pressKey = async (key, code, virtualKey, text) => {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey, ...(text ? { text, unmodifiedText: text } : {}) });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: virtualKey, nativeVirtualKeyCode: virtualKey });
    await sleep(450);
  };
  const tab = () => pressKey('Tab', 'Tab', 9);
  const enter = () => pressKey('Enter', 'Enter', 13, '\r');
  const space = () => pressKey(' ', 'Space', 32, ' ');

  /** Walk the real tab order until `selector` holds focus; steps, or -1. */
  async function tabUntil(selector, limit = 14) {
    const holds = () => cdp.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); return !!el && document.activeElement === el; })()`);
    for (let step = 0; step <= limit; step += 1) {
      if (await holds()) return step;
      if (step === limit) break;
      await tab();
    }
    return -1;
  }

  function check(name, ok, detail = '') { return { name, ok: !!ok, detail: String(detail) }; }
  function record(name, checks, screenshot) {
    results.push({ scenario: name, screenshot: labelShot(screenshot), checks, passed: checks.every((c) => c.ok) });
  }
  const commands = (state) => state.log.join(' | ');

  async function openSettings(query = '?scenario=ready&frame=1&log=1') {
    await load(query);
    const home = await settingsUI();
    const gear = await cdp.evaluate(`(() => { const el = document.querySelector('.device-home-settings'); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled }; })()`);
    if (!gear) return { gear: null, before: home };
    await click(gear);
    await sleep(600);
    return { gear, before: home, after: await settingsUI() };
  }

  // --- 1. Home -> Settings -> Back (decisions, "Navigation and acceptance") --
  {
    const opened = await openSettings();
    await shot('settings-01-open.png');
    await click((await settingsUI()).back);
    await sleep(700);
    const landed = await settingsUI();
    const landedFocus = await focusInfo();
    await shot('settings-02-after-back.png');
    record('home-to-settings-and-back-returns-home', [
      check('the Home gear is reachable', !!opened.gear && !opened.gear.disabled, JSON.stringify(opened.gear)),
      check('the gear opens the Settings screen', !!opened.after?.settingsOpen, JSON.stringify(opened.after?.title)),
      check('Settings has its own Back control and title', opened.after?.title === 'Settings' && !!opened.after?.back, `${opened.after?.title} · back=${!!opened.after?.back}`),
      // The decisions state this as a requirement, not a preference: "Home ->
      // Settings -> Home. A Back action restores the originating view and focus,
      // rather than always landing in Chat."
      check('Back returns to Home, not Chat', landed.home && !landed.chat, `home=${landed.home} chat=${landed.chat}`),
      // ...and "and focus" is the half that was missing: the gear that opened
      // Settings is where the user is put back.
      check('Back restores focus to the Home gear it was opened from', landedFocus?.isGear === true, JSON.stringify(landedFocus)),
    ], 'screenshots/settings-01-open.png');
  }

  // --- 6. The menu entry keeps the screen the menu was opened over --------
  {
    await load('?scenario=ready&frame=1&log=1');
    await clickSelector('.device-home-tile[data-destination="chat"]');
    await sleep(500);
    await clickSelector('button[aria-haspopup="dialog"]');
    await sleep(400);
    const menuClick = await menuEntry('Settings');
    if (menuClick) await click(menuClick);
    await sleep(600);
    const openedOverChat = await settingsUI();
    if (openedOverChat.back) await click(openedOverChat.back);
    await sleep(700);
    const backInChat = await settingsUI();
    const chatFocus = await focusInfo();
    await shot('settings-06-chat-menu-origin.png');

    // The same entry, over Home: the menu lives in the shared header, so this is
    // a real second origin and it must not be flattened into Chat.
    await clickSelector('.device-wordmark');
    await sleep(500);
    await clickSelector('button[aria-haspopup="dialog"]');
    await sleep(400);
    const homeMenuClick = await menuEntry('Settings');
    if (homeMenuClick) await click(homeMenuClick);
    await sleep(600);
    const openedOverHome = await settingsUI();
    if (openedOverHome.back) await click(openedOverHome.back);
    await sleep(700);
    const backHome = await settingsUI();
    const homeFocus = await focusInfo();
    await shot('settings-07-home-menu-origin.png');

    record('settings-back-returns-to-the-screen-it-was-opened-from', [
      check('the menu opens Settings over Chat', !!openedOverChat.settingsOpen && openedOverChat.title === 'Settings', `open=${openedOverChat.settingsOpen} title=${openedOverChat.title} entry=${JSON.stringify(menuClick)}`),
      check('Back returns to Chat, not Home', backInChat.chat && !backInChat.home, `chat=${backInChat.chat} home=${backInChat.home}`),
      check('Back restores focus to the Chat menu control', chatFocus?.isMenuTrigger === true, JSON.stringify(chatFocus)),
      check('the same entry over Home opens Settings', !!openedOverHome.settingsOpen, `open=${openedOverHome.settingsOpen} title=${openedOverHome.title}`),
      check('Back returns to Home, preserving the menu Home origin', backHome.home && !backHome.chat, `home=${backHome.home} chat=${backHome.chat}`),
      check('Back restores focus to the Home gear', homeFocus?.isGear === true, JSON.stringify(homeFocus)),
    ], 'screenshots/settings-06-chat-menu-origin.png');
  }

  // --- 2. The grouped surface, and the destinations 2c has not delivered ---
  {
    const { after } = await openSettings();
    const panels = (after?.panels ?? []).map((p) => p.heading);
    const bluetooth = (after?.panels ?? []).find((p) => p.heading === 'Bluetooth audio');
    record('settings-surface-is-grouped-and-honest', [
      check('the delivery panels are present', ['Automatic lock', 'Screen and sleep'].every((h) => panels.includes(h)), panels.join(', ')),
      check('the privacy group is present', panels.includes('Save questions'), panels.join(', ')),
      check('the saving switch reflects the served state', after?.saveQuestions?.checked === 'false', `aria-checked=${after?.saveQuestions?.checked}`),
      check('photo saving is offered only because the device attests it', !!after?.keepPhotos, JSON.stringify(after?.keepPhotos)),
      check('unsupported Bluetooth audio is stated, not implied available', !!bluetooth && /not available in Lantern yet/.test(bluetooth.text), bluetooth?.text ?? '(no panel)'),
      // 2c is undelivered, so the decisions require these entries to appear only
      // "as those screens are delivered".
      check('no entries to undelivered 2c destinations', (after?.destinations ?? []).length === 0, (after?.destinations ?? []).join(', ')),
    ], 'screenshots/settings-01-open.png');
  }

  // --- 3. A refused write must not be rendered as a successful change ------
  {
    const { after } = await openSettings();
    const before = after.saveQuestions;
    await click(before);
    const confirming = await settingsUI();
    await shot('settings-03-confirm.png');
    await click(confirming.dialogPrimary);
    await sleep(700);
    const state = await probe();
    const failed = await settingsUI();
    const failedFocus = await focusInfo();
    await shot('settings-04-failed-acknowledgement.png');
    const log = commands(state);
    record('failed-settings-acknowledgement-is-not-rendered-as-success', [
      check('turning saving on asks for confirmation first', confirming.dialogHeading === 'Save questions on this device?', confirming.dialogHeading ?? '(no dialog)'),
      check('the write was actually attempted', /POST \/history\/settings → not served by this preview \(404\)/.test(log), log),
      check('the refusal is stated to the user', (failed.alerts ?? []).some((a) => /Could not confirm the setting/.test(a)), (failed.alerts ?? []).join(' | ')),
      // A future optimistic implementation would keep the panel and show On;
      // this check is written to fail in that case.
      check('no switch claims On after a refused write', !(failed.saveQuestions && failed.saveQuestions.checked === 'true') && !failed.anyCheckedTrue, `switch=${JSON.stringify(failed.saveQuestions)} anyOn=${failed.anyCheckedTrue}`),
      check('no success wording is shown', !/Saved|Lock delay saved/.test((failed.alerts ?? []).join(' ')), (failed.alerts ?? []).join(' | ')),
      // The failure removes the panel the switch lived in, so there is no switch
      // left to return to: focus must still land on a real control rather than
      // falling to the document.
      check('a refused write leaves focus on a control, not the document body', failedFocus?.tag === 'BUTTON' && failedFocus.inSettings === true, JSON.stringify(failedFocus)),
    ], 'screenshots/settings-04-failed-acknowledgement.png');
  }

  // --- 4. Unavailable bindings say so; nothing is invented ---------------
  {
    const { after } = await openSettings();
    const lock = (after?.panels ?? []).find((p) => p.label === 'Automatic lock');
    const sleepPanel = (after?.panels ?? []).find((p) => p.label === 'Screen and sleep');
    record('unreachable-bindings-are-stated-not-invented', [
      check('the lock panel reports why it cannot load', !!lock && /Lock settings need a device update or a new unlocked session/.test(lock.text), lock?.text ?? '(no panel)'),
      check('the lock panel offers no control it cannot honour', (lock?.selects ?? 0) === 0, `selects=${lock?.selects}`),
      check('the sleep panel reports why it cannot load', !!sleepPanel && /Sleep settings need a device update or a new unlocked session/.test(sleepPanel.text), sleepPanel?.text ?? '(no panel)'),
      check('the sleep panel shows no invented delay value', (sleepPanel?.selects ?? 0) === 0, `selects=${sleepPanel?.selects}`),
    ], 'screenshots/settings-01-open.png');
  }

  // --- 5. Focus, and a cancelled edit that writes nothing ----------------
  {
    const { after } = await openSettings();
    const onOpen = after?.active;
    // Reachability is proved with the keyboard: Tab until the switch holds
    // focus, with no `element.focus()` anywhere in this scenario.
    const tabSteps = await tabUntil('button[role="switch"][aria-label="Save questions"]');
    const onSwitch = await focusInfo();
    await enter();
    const confirming = await settingsUI();
    const focusedInDialog = confirming.active;
    const dialogFocus = await focusInfo();
    await shot('settings-05-confirm.png');
    await escape();
    const afterEscape = await settingsUI();
    const escapedFocus = await focusInfo();
    // Cancel is the other exit, and it is taken from the keyboard too.
    await enter();
    const reopened = await settingsUI();
    const reopenedFocus = await focusInfo();
    await space();
    const cancelled = await settingsUI();
    const cancelledFocus = await focusInfo();
    const state = await probe();
    const log = commands(state);
    record('settings-focus-and-a-cancelled-edit', [
      check('opening Settings moves focus into the screen', !!onOpen?.insideSettings, JSON.stringify(onOpen)),
      // Reachable unaided: the walk above used real Tab presses and nothing else.
      check('the saving switch is reachable by tabbing, with no focus injection', tabSteps >= 0 && onSwitch?.isSwitch === true, `steps=${tabSteps} active=${JSON.stringify(onSwitch)}`),
      check('the switch opens the confirmation from the keyboard', confirming.dialogHeading === 'Save questions on this device?', confirming.dialogHeading ?? '(no dialog)'),
      // The dialog replaces the panel that held focus, so focus has to be moved
      // into it deliberately — otherwise it lands on the document body.
      check('the confirm step takes focus, and keeps it inside the screen', dialogFocus?.inDialog === true, `ui=${JSON.stringify(focusedInDialog)} focus=${JSON.stringify(dialogFocus)}`),
      // `dialog` reads as '' when the dialog is gone, so these are falsy tests
      // rather than null tests.
      check('Escape returns focus to the switch that opened it', !afterEscape.dialog && escapedFocus?.isSwitch === true, `dialog=${JSON.stringify(afterEscape.dialog)} focus=${JSON.stringify(escapedFocus)}`),
      check('the dialog reopens from the switch by keyboard', !!reopened.dialog && reopenedFocus?.inDialog === true, `dialog=${JSON.stringify(reopened.dialog)} focus=${JSON.stringify(reopenedFocus)}`),
      check('activating Cancel by keyboard closes it and returns focus to the switch', !cancelled.dialog && cancelledFocus?.isSwitch === true, `dialog=${JSON.stringify(cancelled.dialog)} focus=${JSON.stringify(cancelledFocus)}`),
      check('Cancel leaves the screen and the setting untouched', !!cancelled.settingsOpen && cancelled.saveQuestions?.checked === 'false', `open=${cancelled.settingsOpen} switch=${cancelled.saveQuestions?.checked}`),
      check('a cancelled edit writes nothing', !/POST \/history\/settings/.test(log), log),
    ], 'screenshots/settings-05-confirm.png');
  }
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* closing */ }
  chrome.kill('SIGTERM');
}

const failed = results.flatMap((entry) => entry.checks.filter((c) => !c.ok).map((c) => `${entry.scenario}: ${c.name} — ${c.detail}`));
writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify({ base: BASE, results, consoleErrors, failed }, null, 2)}\n`);
for (const entry of results) {
  console.log(`${entry.passed ? 'PASS' : 'FAIL'}  ${entry.scenario}  (${entry.checks.filter((c) => c.ok).length}/${entry.checks.length})  ${entry.screenshot}`);
  for (const c of entry.checks) if (!c.ok) console.log(`        ✗ ${c.name} — ${c.detail}`);
}
console.log(`\n${results.filter((entry) => entry.passed).length}/${results.length} scenarios passed.`);
if (consoleErrors.length) console.log(`Console errors: ${consoleErrors.slice(0, 5).join(' | ')}`);
else console.log('Console errors: none');
process.exit(failed.length === 0 && consoleErrors.length === 0 ? 0 : 1);
