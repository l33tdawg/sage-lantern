/**
 * Drive the disconnected Home fixture in a real browser.
 *
 * Headless Chrome over the DevTools Protocol, with no test framework and no
 * dependencies: real layout, real trusted pointer and keyboard input, real
 * screenshots. The gesture scenarios assert the callback sequence the reviewed
 * component produces, including the pending-start race.
 *
 *   node drive-fixture.mjs [label]
 *
 * Writes screenshots/*.png and gesture-results.json beside this file.
 * A label redirects every output to `<name>-<label>.*` instead, so a later
 * revision's run is recorded beside the frozen one rather than over it:
 *
 *   node drive-fixture.mjs correction
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9333;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

/** Output names, so a labelled run cannot overwrite the frozen record. */
const LABEL = process.argv[2] ?? '';
const RESULTS_FILE = LABEL ? `gesture-${LABEL}-results.json` : 'gesture-results.json';
const shotFile = (name) => (LABEL ? name.replace(/^(home|gesture|preview)-/, `$1-${LABEL}-`) : name);

const fixtureUrl = (query) => `file://${join(here, 'home-fixture.html')}${query}`;

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
    if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.text} ${expression}`);
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

const profile = `/tmp/ember-home-fixture-${process.pid}`;
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text',
  // Chrome's own sandbox cannot initialise inside a restricted shell on this
  // host, so the renderer runs without it. The fixture only ever loads local
  // file:// pages built from this repository; no remote content is fetched.
  '--no-sandbox', '--disable-setuid-sandbox', '--disable-crashpad', '--disable-breakpad',
  'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
chrome.stderr.on('data', () => {});

try {
  const target = await devtoolsTarget();
  const cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  async function viewport(width, height) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile: false });
  }

  async function load(query) {
    await cdp.send('Page.navigate', { url: fixtureUrl(query) });
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.fixture').catch(() => false);
      if (ready) { await sleep(150); return; }
      await sleep(100);
    }
    throw new Error(`fixture never mounted for ${query}`);
  }

  async function shot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, shotFile(name)), Buffer.from(data, 'base64'));
    return `screenshots/${shotFile(name)}`;
  }

  async function state() {
    return cdp.evaluate('JSON.parse(JSON.stringify(window.fixture))');
  }

  async function robotBox() {
    return cdp.evaluate(`(() => {
      const el = document.querySelector('.device-home-robot');
      if (!el) return null;
      const box = el.getBoundingClientRect();
      return { x: Math.round(box.left + box.width / 2), y: Math.round(box.top + box.height / 2), disabled: el.disabled };
    })()`);
  }

  async function press(point) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  }
  async function release(point) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  }
  async function tap(point, gap = 40) {
    await press(point);
    await sleep(gap);
    await release(point);
  }

  // --- Gesture evidence ----------------------------------------------------

  const gestures = [];
  async function gesture(name, query, run, expect) {
    await viewport(720, 1560);
    await load(query);
    const box = await robotBox();
    if (!box) throw new Error(`${name}: the control was not rendered`);
    const detail = await run(box);
    const final = await state();
    const events = final.events;
    const pass = JSON.stringify(events) === JSON.stringify(expect.events)
      && (expect.phase === undefined || final.phase === expect.phase)
      && (expect.abandoned === undefined || final.abandoned === expect.abandoned);
    gestures.push({ name, query, expected: expect, actual: { events, phase: final.phase, busy: final.busy, abandoned: final.abandoned }, pass, detail });
    console.log(`${pass ? 'PASS' : 'FAIL'} ${name}: ${events.join(' → ') || '(no callbacks)'} · phase ${final.phase} · abandoned ${final.abandoned}`);
    return { final, detail };
  }

  await gesture('tap starts one chat', '?scenario=ready&log=1',
    async (box) => { await tap(box); await sleep(120); },
    { events: ['start'] });

  await gesture('second tap finishes the recording', '?scenario=listening&log=1',
    async (box) => { await tap(box); await sleep(120); },
    { events: ['finish'], phase: 'review' });

  await gesture('hold becomes push-to-talk and the release finishes it', '?scenario=ready&parent=confirm&log=1',
    async (box) => { await press(box); await sleep(620); await release(box); await sleep(160); },
    { events: ['start', 'finish'], phase: 'review' });

  const race = await gesture('release while the start is still pending fences it', '?scenario=ready&parent=slow&log=1',
    async (box) => {
      await press(box);
      await sleep(620);
      await release(box);
      await sleep(160);
      await shot('gesture-pending-race-720x1560.png');
      // The late acknowledgement lands here. It must not begin a recording.
      await sleep(1500);
      await shot('gesture-pending-race-settled-720x1560.png');
    },
    { events: ['start', 'cancel'], abandoned: true });

  await gesture('holding Enter produces exactly one activation', '?scenario=ready&log=1',
    async (box) => {
      await cdp.evaluate('document.querySelector(".device-home-robot").focus()');
      for (let repeat = 0; repeat < 3; repeat += 1) {
        await cdp.send('Input.dispatchKeyEvent', {
          type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, autoRepeat: true,
        });
        await sleep(30);
      }
      await cdp.send('Input.dispatchKeyEvent', {
        type: 'keyDown', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13, autoRepeat: false,
      });
      await cdp.send('Input.dispatchKeyEvent', {
        type: 'keyUp', key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13,
      });
      await sleep(150);
      void box;
    },
    { events: ['start'] });

  await gesture('an assistive click activates once', '?scenario=ready&log=1',
    async () => { await cdp.evaluate('document.querySelector(".device-home-robot").click()'); await sleep(150); },
    { events: ['start'] });

  await gesture('a cancelled pointer gesture does not become a tap', '?scenario=ready&parent=confirm&log=1',
    async (box) => {
      await press(box);
      await sleep(60);
      await cdp.evaluate(`document.querySelector('.device-home-robot').dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 }))`);
      await sleep(60);
      await release(box);
      await sleep(120);
    },
    { events: ['cancel'] });

  // --- Geometry, inside the real shell height ------------------------------

  const MEASURE = `(() => {
    const rect = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: +r.x.toFixed(2), y: +r.y.toFixed(2), w: +r.width.toFixed(2), h: +r.height.toFixed(2) };
    };
    return {
      home: rect('.device-home'), shellTop: rect('.fixture-shell-top'), shellFooter: rect('.fixture-shell-footer'),
      settings: rect('.device-home-settings'), status: rect('.device-home-status'), identity: rect('.device-home-identity'),
      stage: rect('.device-home-stage'), voice: rect('.device-home-voice'),
      robot: rect('.device-home-robot'), instruction: rect('.device-home-instruction'),
      feedback: rect('.device-home-feedback'), openChat: rect('.device-home-open-chat'), cancel: rect('.device-home-cancel'),
      tiles: rect('.device-home-tiles'),
      tileChat: rect('.device-home-tile[data-destination="chat"]'),
      tileVision: rect('.device-home-tile[data-destination="vision"]'),
      tileAgent: rect('.device-home-tile[data-destination="agent"]'),
      tileGallery: rect('.device-home-tile[data-destination="gallery"]'),
      viewport: { w: window.innerWidth, h: window.innerHeight },
      document: { w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight },
      stageScroll: (() => {
        const el = document.querySelector('.device-home-stage');
        if (!el) return null;
        const style = getComputedStyle(el);
        return { overflowY: style.overflowY, scrollHeight: Math.round(el.scrollHeight), clientHeight: Math.round(el.clientHeight) };
      })(),
      // The contract's short-panel fallback is a *reachable* scroller, so when
      // the region is shorter than the control block the control must still be
      // able to come fully into view. Measured rather than assumed: the stage is
      // scrolled to the top and the control's box compared with the region's.
      controlReachable: (() => {
        const stage = document.querySelector('.device-home-stage');
        const robot = document.querySelector('.device-home-robot');
        if (!stage || !robot) return null;
        const before = stage.scrollTop;
        stage.scrollTop = 0;
        const s = stage.getBoundingClientRect();
        const r = robot.getBoundingClientRect();
        const visible = r.top >= s.top - 1 && r.bottom <= s.bottom + 1 && r.left >= s.left - 1 && r.right <= s.right + 1;
        stage.scrollTop = before;
        return visible;
      })(),
    };
  })()`;

  // A box with no area paints nothing, so a region that has been clipped away to
  // nothing cannot overlap anything. Without this, content scrolled out of a
  // bounded region still "overlapped" whatever sat below it on the panel.
  const overlaps = (a, b) => Boolean(a && b && a.w > 0 && a.h > 0 && b.w > 0 && b.h > 0
    && a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h);

  // The control's declared size for a given panel, from home.css: 200px normally,
  // 160px under 520px wide, 128px on a short panel. Asserting the declared size
  // rather than one number is what catches a layout that *squashes* the control:
  // a smaller control by reflow is a decision, a squashed one is a defect.
  const declaredControl = (size) => (size.height <= 900 ? 112 : size.width <= 520 ? 160 : 200);

  function judge(name, size, measured) {
    const problems = [];
    const notes = [];
    const minimum = (label, box) => {
      if (!box) return;
      if (box.w < 72 || box.h < 72) problems.push(`${label} is ${box.w}x${box.h}, under the 72px minimum`);
    };
    // Boxes inside a scrolling region are clamped to what the region actually
    // paints. Content below the fold of a bounded scroller cannot overlap the
    // tile map on screen, and treating its unclipped geometry as an overlap
    // would fail the one layout the contract explicitly allows.
    const stageScrolls = !!measured.stageScroll && /auto|scroll/.test(measured.stageScroll.overflowY);
    const clip = (box) => (stageScrolls && box && measured.stage
      ? {
        x: Math.max(box.x, measured.stage.x), y: Math.max(box.y, measured.stage.y),
        w: Math.max(0, Math.min(box.x + box.w, measured.stage.x + measured.stage.w) - Math.max(box.x, measured.stage.x)),
        h: Math.max(0, Math.min(box.y + box.h, measured.stage.y + measured.stage.h) - Math.max(box.y, measured.stage.y)),
      }
      : box);
    const robot = clip(measured.robot);
    const feedback = clip(measured.feedback);
    if (overlaps(measured.settings, measured.status)) problems.push('Settings overlaps the status strip');
    if (overlaps(robot, measured.status)) problems.push('the control overlaps the status strip');
    if (overlaps(robot, measured.identity)) problems.push('the control overlaps the identity');
    if (overlaps(robot, feedback)) problems.push('the control overlaps the feedback panel');
    if (overlaps(feedback, measured.tiles)) problems.push('the feedback panel overlaps the tile map');
    if (overlaps(measured.status, measured.identity)) problems.push('the status strip overlaps the identity');
    const want = declaredControl(size);
    if (measured.robot && (Math.abs(measured.robot.w - want) > 1 || Math.abs(measured.robot.h - want) > 1)) {
      problems.push(`the control is ${measured.robot.w}x${measured.robot.h}, but this panel declares ${want}x${want} — the layout squashed it`);
    }
    // A bounded reader that is bounded to nothing is not a reader: when feedback
    // is on screen it must keep a usable height, and then scroll internally.
    if (measured.feedback && measured.feedback.h < 48) {
      problems.push(`the reader is squeezed to ${measured.feedback.h}px`);
    }
    // The contract allows a short panel to scroll the middle region rather than
    // shrink readable text or clip a control — but only a *reachable* scroller
    // counts. A region that is too short and cannot scroll has clipped the
    // control, and one that scrolls but never brings the control into view is
    // the same defect wearing a scrollbar.
    if (measured.stage && measured.voice && measured.stage.h + 1 < measured.voice.h) {
      const scroll = measured.stageScroll;
      const scrollable = !!scroll && /auto|scroll/.test(scroll.overflowY) && scroll.scrollHeight > scroll.clientHeight + 1;
      if (!scrollable) {
        problems.push(`the middle region is ${measured.stage.h}px but the control block needs ${measured.voice.h}px and does not scroll — the control is clipped`);
      } else if (measured.controlReachable !== true) {
        problems.push(`the middle region is ${measured.stage.h}px, shorter than the control block's ${measured.voice.h}px, and scrolling does not bring the control into view`);
      } else {
        notes.push(`the middle region scrolls (${measured.stage.h}px region, ${measured.voice.h}px control) and the control is reachable`);
      }
    }
    for (const [label, box] of [['Settings', measured.settings], ['the control', measured.robot],
      ['Chat', measured.tileChat], ['Vision', measured.tileVision], ['Agent', measured.tileAgent],
      ['Gallery', measured.tileGallery], ['Open chat', measured.openChat], ['Cancel', measured.cancel]]) minimum(label, box);
    if (measured.home && measured.home.h > (measured.shellFooter?.y ?? measured.viewport.h) - measured.home.y + 0.5) {
      problems.push('Home overflows its shell row');
    }
    if (measured.document.h > measured.viewport.h + 1 || measured.document.w > measured.viewport.w + 1) {
      problems.push(`the document overflows: ${measured.document.w}x${measured.document.h} in ${measured.viewport.w}x${measured.viewport.h}`);
    }
    return { name, size, pass: problems.length === 0, problems, notes, measured };
  }

  const geometry = [];
  for (const [name, width, height, query] of [
    ['720x1560 idle', 720, 1560, '?scenario=ready'],
    ['720x1560 long answer', 720, 1560, '?scenario=complete&feedback=long'],
    ['720x1560 recording', 720, 1560, '?scenario=listening'],
    ['720x1560 stale', 720, 1560, '?scenario=stale'],
    ['480x1040 idle', 480, 1040, '?scenario=ready'],
    ['480x1040 long answer', 480, 1040, '?scenario=complete&feedback=long'],
    ['360x800 idle', 360, 800, '?scenario=ready'],
    ['360x800 long answer', 360, 800, '?scenario=complete&feedback=long'],
  ]) {
    await viewport(width, height);
    await load(query);
    const measured = await cdp.evaluate(MEASURE);
    const result = judge(name, { width, height }, measured);
    geometry.push(result);
    console.log(`${result.pass ? 'PASS' : 'FAIL'} geometry ${name}: control ${measured.robot?.w}x${measured.robot?.h} at (${measured.robot?.x},${measured.robot?.y}) · tiles y=${measured.tiles?.y} h=${measured.tiles?.h}${result.problems.length ? ` — ${result.problems.join('; ')}` : ''}${result.notes?.length ? ` (${result.notes.join('; ')})` : ''}`);
  }

  // The tile map must not move when an answer appears: a reader that displaces
  // navigation moves targets under a finger that is already reaching for them.
  const idle = geometry.find((entry) => entry.name === '720x1560 idle');
  const long = geometry.find((entry) => entry.name === '720x1560 long answer');
  const navigationHeld = idle.measured.tiles.y === long.measured.tiles.y && idle.measured.tiles.h === long.measured.tiles.h;
  console.log(`${navigationHeld ? 'PASS' : 'FAIL'} the tile map keeps its position when an answer appears`);

  // --- Screen captures -----------------------------------------------------

  const screens = [
    // Portrait is the canonical screen. 720x1560 first, then the reflow checks.
    ['home-idle-720x1560.png', 720, 1560, '?scenario=ready'],
    ['home-recording-720x1560.png', 720, 1560, '?scenario=listening'],
    ['home-processing-720x1560.png', 720, 1560, '?scenario=processing'],
    ['home-answer-720x1560.png', 720, 1560, '?scenario=complete&feedback=long'],
    ['home-answer-streaming-720x1560.png', 720, 1560, '?scenario=complete&feedback=streaming'],
    ['home-answer-incomplete-720x1560.png', 720, 1560, '?scenario=complete&feedback=incomplete'],
    ['home-stale-720x1560.png', 720, 1560, '?scenario=stale'],
    ['home-physical-only-720x1560.png', 720, 1560, '?scenario=physical-only'],
    ['home-error-720x1560.png', 720, 1560, '?scenario=error'],
    ['home-no-mic-720x1560.png', 720, 1560, '?scenario=no-mic'],
    ['home-restricted-tiles-720x1560.png', 720, 1560, '?scenario=restricted'],
    ['home-offline-720x1560.png', 720, 1560, '?scenario=offline'],
    ['home-reference-480x800.png', 480, 800, '?scenario=ready'],
    ['home-idle-480x1040.png', 480, 1040, '?scenario=ready'],
    ['home-answer-480x1040.png', 480, 1040, '?scenario=complete&feedback=long'],
    ['home-idle-360x800.png', 360, 800, '?scenario=ready'],
    ['home-answer-360x800.png', 360, 800, '?scenario=complete&feedback=long'],
    // A desktop window shows a portrait device rather than a landscape app.
    ['preview-desktop-portrait-1440x1000.png', 1440, 1000, '?scenario=ready&frame=1'],
    ['preview-desktop-portrait-answer-1440x1000.png', 1440, 1000, '?scenario=complete&feedback=long&frame=1'],
    // Rotation fallback only: not an edition, kept so a rotated panel does not
    // overlap. The owner's direction is portrait.
    ['stress-landscape-1024x600.png', 1024, 600, '?scenario=ready'],
    ['stress-landscape-answer-1024x600.png', 1024, 600, '?scenario=complete&feedback=long'],
  ];

  const captures = [];
  for (const [name, width, height, query] of screens) {
    await viewport(width, height);
    await load(query);
    captures.push(await shot(name));
  }

  const results = {
    fixture: 'work/ember-lantern-home-20261002/home-fixture.html',
    browser: 'headless Chrome via DevTools Protocol, trusted input events',
    shell: 'real shell rows: 76px header | Home region | 94px footer (screen.css .device-touch, portrait.css)',
    gestures,
    geometry,
    navigation_held: navigationHeld,
    captures,
    gesture_failures: gestures.filter((entry) => !entry.pass).map((entry) => entry.name),
    geometry_failures: geometry.filter((entry) => !entry.pass).map((entry) => entry.name),
  };
  writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify(results, null, 2)}\n`);

  console.log(`\n${captures.length} screenshots written.`);
  const failures = [...results.gesture_failures, ...results.geometry_failures];
  if (!navigationHeld) failures.push('tile map moved');
  if (failures.length) {
    console.error(`Failures: ${failures.join(', ')}`);
    process.exitCode = 1;
  } else {
    console.log(`All ${gestures.length} gesture scenarios and ${geometry.length} geometry checks pass.`);
  }
} finally {
  chrome.kill('SIGTERM');
  await sleep(200);
}
