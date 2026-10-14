/**
 * Measure Lantern's stylesheet defects in a real browser.
 *
 * Everything here is a measurement of the running app in headless Chrome: real
 * layout, real computed styles, trusted pointer input, real screenshots. No CSS
 * is read as evidence.
 *
 * Standard method borrowed from work/ember-ui-verify-20261014/:
 *   - device metrics are set BEFORE the navigation, because a viewport applied
 *     after boot leaves the fixture frame sized for the old one and a portrait
 *     box is then measured against a landscape media query;
 *   - `?frame=0` makes the panel the viewport, and the panel's own bounding rect
 *     is asserted to equal the requested viewport before anything is measured;
 *   - visibility is always a rectangle intersection with the panel, never
 *     `innerText` (React leaves stray text nodes painted nowhere);
 *   - every predicate carries a positive AND a negative control.
 *
 *   node audit.mjs [base-url] [label]
 *
 * Writes `screenshots/<label>-<view>-<screen>.png` and
 * `styles-<label>-results.json` beside this file.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8907/screen-fixture.html';
const LABEL = process.argv[3] ?? 'baseline';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9407;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

const VIEWPORTS = [
  { w: 720, h: 1560, view: 'portrait-720x1560', mobile: false },
  { w: 480, h: 1040, view: 'portrait-480x1040', mobile: true },
  { w: 360, h: 800, view: 'portrait-360x800', mobile: true },
  { w: 1024, h: 600, view: 'landscape-1024x600', mobile: false },
];

const SCENARIO = '?scenario=complete&mic=on&reset=ok&photos=three&frame=0';

/** How to reach each screen from a fresh boot. */
const VISITS = [
  { name: 'home', steps: [], expect: '.device-home-tile' },
  { name: 'chat', steps: [['tile', 'chat']], expect: '.device-chat-bar' },
  { name: 'chat-keyboard', steps: [['tile', 'chat'], ['click', '.device-chat-composer-field']], expect: '.device-chat-composer-keyboard .question-key-row button' },
  { name: 'gallery', steps: [['tile', 'gallery'], ['wait', '.device-gallery-thumb']], expect: '.device-gallery' },
  { name: 'gallery-viewer', steps: [['tile', 'gallery'], ['wait', '.device-gallery-thumb'], ['click', '.device-gallery-thumb']], expect: '.device-gallery-viewer' },
  { name: 'agent', steps: [['tile', 'agent']], expect: '.device-subscreen[aria-label="Agent"]' },
  { name: 'agent-keyboard', steps: [['tile', 'agent'], ['click', '.device-page-bar-action'], ['wait', '#agent-task-instruction'], ['click', '#agent-task-instruction']], expect: '.device-task-form .question-key-row button' },
  { name: 'vision', steps: [['tile', 'vision']], expect: '.device-camera' },
  { name: 'messages', steps: [['tile', 'messages']], expect: '.device-subscreen[aria-label="Messages"]' },
  { name: 'nearby', steps: [['tile', 'nearby']], expect: '.device-subscreen[aria-label="Nearby"]' },
  { name: 'settings', steps: [['menu', 'Settings']], expect: '.device-subscreen[aria-label="Settings"]' },
  { name: 'history', steps: [['menu', 'Past questions']], expect: '.device-subscreen[aria-label="Question history"]' },
  { name: 'status', steps: [['menu', 'Nearby']], expect: '.device-subscreen[aria-label="Nearby"]' },
  { name: 'sharing', steps: [['menu', 'Shared knowledge']], expect: '.device-subscreen[aria-label="Shared knowledge"]' },
  { name: 'about', steps: [['menu', 'About']], expect: '.device-subscreen[aria-label="About Lantern"]' },
  { name: 'about-credits', steps: [['menu', 'About'], ['wait', '.device-subcontent details'], ['click', '.device-subcontent details summary']], expect: '.device-subcontent details[open] a' },
];

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url);
    this.nextId = 0;
    this.pending = new Map();
    this.consoleErrors = [];
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (event) => reject(new Error(`devtools socket error: ${event.message ?? 'unknown'}`)));
    });
    this.ws.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.method === 'Runtime.exceptionThrown') {
        this.consoleErrors.push(String(message.params?.exceptionDetails?.exception?.description ?? message.params?.exceptionDetails?.text ?? 'exception'));
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
    if (result.exceptionDetails) {
      const detail = result.exceptionDetails.exception?.description ?? result.exceptionDetails.text;
      throw new Error(`evaluate failed: ${detail} :: ${expression.slice(0, 200)}`);
    }
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

/**
 * The in-page measurement library.
 *
 * Installed once per document and used for every number this run reports, so a
 * predicate that cannot fail cannot pass either: the same functions are run
 * against injected positive and negative controls before any screen is read.
 */
const HELPERS = String.raw`(() => {
  const nums = (c) => (String(c || '').match(/[\d.]+/g) || []).map(Number);
  const color = (c) => { const n = nums(c); return n.length >= 3 ? { r: n[0], g: n[1], b: n[2], a: n.length > 3 ? n[3] : 1 } : null; };
  const round = (n) => Math.round(n * 10) / 10;
  const chan = (v) => { const x = v / 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); };
  const lum = (c) => 0.2126 * chan(c.r) + 0.7152 * chan(c.g) + 0.0722 * chan(c.b);
  const ratio = (a, b) => { const la = lum(a); const lb = lum(b); const hi = Math.max(la, lb); const lo = Math.min(la, lb); return (hi + 0.05) / (lo + 0.05); };
  const over = (top, bottom) => ({
    r: top.r * top.a + bottom.r * (1 - top.a),
    g: top.g * top.a + bottom.g * (1 - top.a),
    b: top.b * top.a + bottom.b * (1 - top.a),
    a: 1,
  });
  const rgbText = (c) => 'rgb(' + [c.r, c.g, c.b].map((v) => Math.round(v)).join(', ') + ')';
  const background = (el) => {
    const chain = [];
    for (let node = el; node; node = node.parentElement) {
      const c = color(getComputedStyle(node).backgroundColor);
      if (c && c.a > 0) chain.push(c);
      if (c && c.a >= 1) break;
    }
    let acc = chain.length ? chain[chain.length - 1] : { r: 11, g: 24, b: 32, a: 1 };
    if (acc.a < 1) acc = over(acc, { r: 11, g: 24, b: 32, a: 1 });
    for (let i = chain.length - 2; i >= 0; i -= 1) acc = over(chain[i], acc);
    return acc;
  };
  const panel = () => document.querySelector('.device-screen');
  const panelRect = () => panel().getBoundingClientRect();
  const shown = (el) => {
    const cs = getComputedStyle(el);
    return cs.display !== 'none' && cs.visibility !== 'hidden' && Number(cs.opacity) > 0.01;
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    const p = panelRect();
    const ix = Math.max(0, Math.min(r.right, p.right) - Math.max(r.left, p.left));
    const iy = Math.max(0, Math.min(r.bottom, p.bottom) - Math.max(r.top, p.top));
    const area = r.width * r.height;
    return {
      w: round(r.width), h: round(r.height),
      left: round(r.left), top: round(r.top), right: round(r.right), bottom: round(r.bottom),
      insidePanel: r.left >= p.left - 0.5 && r.right <= p.right + 0.5,
      visible: area > 0 ? round((ix * iy) / area) : 0,
    };
  };
  const name = (el) => (el.getAttribute('aria-label') || el.textContent || el.tagName).trim().replace(/\s+/g, ' ').slice(0, 30);
  const targets = (selector, root = panel()) => [...root.querySelectorAll(selector)].filter(shown).map((el) => {
    const b = box(el);
    return { label: name(el), cls: typeof el.className === 'string' ? el.className.slice(0, 60) : '', ...b, underFloor: b.w < 72 || b.h < 72 };
  });
  const rootsOf = (arg) => (typeof arg === 'string' ? [...document.querySelectorAll(arg)] : [arg ?? panel()]);
  const texts = (arg = panel()) => {
    const out = [];
    const consider = (el) => {
      if (!shown(el)) return;
      const own = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join(' ').trim();
      if (!own) return;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return;
      out.push({ el, text: own.slice(0, 40), color: color(getComputedStyle(el).color), rgb: getComputedStyle(el).color, cls: typeof el.className === 'string' ? el.className.slice(0, 60) : '' });
    };
    for (const root of rootsOf(arg)) {
      if (root && root.nodeType === 1) consider(root);
      for (const el of (root?.querySelectorAll?.('*') ?? [])) consider(el);
    }
    return out;
  };
  const whiteText = (arg = panel()) => texts(arg).filter((t) => t.rgb === 'rgb(255, 255, 255)')
    .map((t) => ({ text: t.text, cls: t.cls, ...box(t.el) }));
  const contrastPairs = (arg = panel()) => texts(arg).map((t) => {
    const bg = background(t.el);
    const fg = t.color.a < 1 ? over(t.color, bg) : t.color;
    return { text: t.text, cls: t.cls, color: t.rgb, background: rgbText(bg), ratio: round(ratio(fg, bg)), ...box(t.el) };
  });
  const overflowing = (root = panel()) => {
    const p = panelRect();
    return [...root.querySelectorAll('*')].filter((el) => {
      if (!shown(el)) return false;
      const r = el.getBoundingClientRect();
      if (r.width < 1 || r.height < 1) return false;
      return r.right > p.right + 0.5 || r.left < p.left - 0.5;
    }).map((el) => ({ tag: el.tagName, cls: typeof el.className === 'string' ? el.className.slice(0, 60) : '', ...box(el), scrollW: el.scrollWidth, clientW: el.clientWidth }));
  };
  const hScroll = (root = panel()) => [...root.querySelectorAll('*')].filter((el) => el.scrollWidth > el.clientWidth + 1)
    .map((el) => ({ tag: el.tagName, cls: typeof el.className === 'string' ? el.className.slice(0, 60) : '', scrollW: el.scrollWidth, clientW: el.clientWidth, ...box(el) }));
  window.__audit = { nums, color, round, lum, ratio, over, background, panel, panelRect, shown, box, name, targets, texts, whiteText, contrastPairs, overflowing, hScroll, rgbText };
  return {
    installed: true,
    panel: (() => { const r = panelRect(); return { left: round(r.left), top: round(r.top), width: round(r.width), height: round(r.height) }; })(),
    viewport: { w: innerWidth, h: innerHeight, scrollTop: document.scrollingElement.scrollTop, scrollHeight: document.scrollingElement.scrollHeight },
  };
})()`;

const results = { label: LABEL, base: BASE, startedAt: new Date().toISOString(), viewports: {}, consoleErrors: [], notes: [] };
const problems = [];

const profile = `/tmp/lantern-style-audit-${process.pid}`;
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

  const waitFor = async (selector, timeoutMs = 8000) => {
    for (let attempt = 0; attempt < Math.ceil(timeoutMs / 100); attempt += 1) {
      const found = await cdp.evaluate(`!!document.querySelector(${JSON.stringify(selector)})`).catch(() => false);
      if (found) return true;
      await sleep(100);
    }
    return false;
  };

  async function clickPoint(x, y) {
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x, y, button: 'left', clickCount: 1 });
    await sleep(40);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x, y, button: 'left', clickCount: 1 });
  }

  /** Press an element by its own rectangle: scroll it in, re-measure, then click. */
  async function press(selector, nth = 0) {
    const pick = `([...document.querySelectorAll(${JSON.stringify(selector)})].filter((el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0; })[${nth}])`;
    const found = await cdp.evaluate(`(() => { const el = ${pick}; if (!el) return false; el.scrollIntoView({ block: 'center', inline: 'nearest' }); return true; })()`);
    if (!found) throw new Error(`nothing to press for ${selector}`);
    await sleep(220);
    const point = await cdp.evaluate(`(() => {
      const el = ${pick};
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return {
        x: Math.round(Math.min(Math.max(r.left + r.width / 2, 2), innerWidth - 2)),
        y: Math.round(Math.min(Math.max(r.top + r.height / 2, 2), innerHeight - 2)),
        w: Math.round(r.width), h: Math.round(r.height),
        fullyInside: r.left >= 0 && r.top >= 0 && r.right <= innerWidth && r.bottom <= innerHeight,
      };
    })()`);
    if (!point) throw new Error(`nothing to press for ${selector}`);
    await clickPoint(point.x, point.y);
    return point;
  }

  async function pressMenu(label) {
    await press('.device-top-actions button:last-child');
    if (!(await waitFor('.device-menu-open', 3000))) throw new Error('the menu never opened');
    await sleep(300);
    const index = await cdp.evaluate(`[...document.querySelectorAll('.device-menu button')].findIndex((b) => b.textContent.includes(${JSON.stringify(label)}))`);
    if (index < 0) throw new Error(`no menu entry for ${label}`);
    await press('.device-menu button', index);
  }

  async function shot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, `${name}.png`), Buffer.from(data, 'base64'));
    return `screenshots/${name}.png`;
  }

  for (const vp of VIEWPORTS) {
    const tag = `${LABEL}-${vp.view}`;
    const record = { viewport: `${vp.w}x${vp.h}`, screens: {} };
    results.viewports[vp.view] = record;

    await cdp.send('Emulation.setDeviceMetricsOverride', { width: vp.w, height: vp.h, deviceScaleFactor: 1, mobile: vp.mobile });
    await cdp.send('Page.navigate', { url: `${BASE}${SCENARIO}` });
    if (!(await waitFor('.device-home-tile', 15000))) throw new Error(`preview never mounted at ${vp.w}x${vp.h} (${vp.view})`);
    await cdp.send('Page.bringToFront').catch(() => {});
    await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
    // The synthetic-preview banner is harness chrome inside the stage the panel
    // is centred in. With `frame=0` the panel IS the viewport, so hiding it is
    // what makes the panel's own rect equal the requested viewport.
    await cdp.evaluate(`(() => { const n = document.querySelector('.preview-notice'); if (n) n.style.display = 'none'; return true; })()`);
    await sleep(900);

    const installed = await cdp.evaluate(HELPERS);
    record.panel = installed.panel;
    record.viewport = installed.viewport;
    const exact = installed.panel.left === 0 && installed.panel.top === 0 && installed.panel.width === vp.w && installed.panel.height === vp.h;
    record.panelMatchesViewport = exact;
    if (!exact) problems.push(`${vp.view}: panel ${JSON.stringify(installed.panel)} != requested ${vp.w}x${vp.h}`);

    record.controls = await cdp.evaluate(`(() => {
      const a = window.__audit;
      const host = document.querySelector('.device-screen');
      const place = (el) => { el.style.cssText += ';position:fixed;left:-500px;top:-500px;display:block'; host.append(el); return el; };
      const field = (id, size) => {
        const el = document.createElement('input');
        el.id = id;
        el.type = 'text';
        el.value = 'x';
        el.style.cssText = 'box-sizing:border-box;min-width:0;min-height:0;width:' + size + 'px;height:' + size + 'px';
        return place(el);
      };
      const para = (id, colour) => { const el = document.createElement('p'); el.id = id; el.textContent = 'probe'; el.style.cssText = 'margin:0;padding:2px;width:200px;background-color:#122935;color:' + colour; return place(el); };
      const negative = field('audit-negative-target', 20);
      const positive = field('audit-positive-target', 80);
      const negativeText = para('audit-negative-contrast', '#0000ee');
      const positiveText = para('audit-positive-contrast', '#f6fafc');
      const bad = a.targets('#audit-negative-target', host)[0];
      const good = a.targets('#audit-positive-target', host)[0];
      const badPair = a.contrastPairs('#audit-negative-contrast')[0];
      const goodPair = a.contrastPairs('#audit-positive-contrast')[0];
      negative.remove(); positive.remove(); negativeText.remove(); positiveText.remove();
      return {
        negativeTarget: bad ? { w: bad.w, h: bad.h, underFloor: bad.underFloor } : null,
        positiveTarget: good ? { w: good.w, h: good.h, underFloor: good.underFloor } : null,
        negativeTargetFailsFloor: !!bad && bad.w === 20 && bad.h === 20 && bad.underFloor === true,
        positiveTargetPassesFloor: !!good && good.w === 80 && good.h === 80 && good.underFloor === false,
        negativeContrastFails: (badPair?.ratio ?? 99) < 4.5,
        negativeContrast: badPair ? { ratio: badPair.ratio, color: badPair.color, background: badPair.background } : null,
        positiveContrastPasses: (goodPair?.ratio ?? 0) >= 4.5,
        positiveContrast: goodPair ? { ratio: goodPair.ratio, color: goodPair.color, background: goodPair.background } : null,
      };
    })()`);
    const control = record.controls;
    if (!(control.negativeTargetFailsFloor && control.positiveTargetPassesFloor)) problems.push(`${vp.view}: the target-size predicate does not discriminate ${JSON.stringify(control)}`);
    if (!(control.negativeContrastFails && control.positiveContrastPasses)) problems.push(`${vp.view}: the contrast predicate does not discriminate ${JSON.stringify(control)}`);

    for (const visit of VISITS) {
      await cdp.send('Page.navigate', { url: `${BASE}${SCENARIO}` });
      if (!(await waitFor('.device-home-tile', 15000))) throw new Error(`preview never mounted for ${visit.name} at ${vp.view}`);
      await cdp.evaluate(`(() => { const n = document.querySelector('.preview-notice'); if (n) n.style.display = 'none'; return true; })()`);
      await cdp.evaluate(HELPERS);
      await sleep(700);
      for (const [kind, arg] of visit.steps) {
        try {
          if (kind === 'tile') await press(`.device-home-tile[data-destination="${arg}"]`);
          else if (kind === 'click') await press(arg);
          else if (kind === 'menu') await pressMenu(arg);
          else if (kind === 'wait' && !(await waitFor(arg, 10000))) throw new Error(`wait timed out for ${arg}`);
        } catch (failure) {
          problems.push(`${vp.view}/${visit.name}: step ${kind} ${arg} failed: ${failure.message}`);
        }
        await sleep(500);
      }
      if (!(await waitFor(visit.expect, 10000))) problems.push(`${vp.view}/${visit.name}: expected selector ${visit.expect} never appeared`);
      await sleep(600);
      const panel = await cdp.evaluate(`(() => { const r = window.__audit.panelRect(); return { left: Math.round(r.left), top: Math.round(r.top), width: Math.round(r.width), height: Math.round(r.height) }; })()`);
      const screen = await cdp.evaluate(`(() => {
        const a = window.__audit;
        const root = a.panel();
        const bar = root.querySelector('.device-top');
        const rects = (el) => { const r = el.getBoundingClientRect(); return { left: a.round(r.left), top: a.round(r.top), right: a.round(r.right), bottom: a.round(r.bottom), w: a.round(r.width), h: a.round(r.height) }; };
        return {
          open: !!document.querySelector(${JSON.stringify(visit.expect)}),
          panelScroll: { scrollW: root.scrollWidth, clientW: root.clientWidth, scrollH: root.scrollHeight, clientH: root.clientHeight },
          header: bar ? { ...rects(bar), scrollW: bar.scrollWidth, clientW: bar.clientWidth } : null,
          headerControls: a.targets('.device-top button'),
          allTargets: a.targets('button, a[href], input, textarea, select'),
          underFloor: a.targets('button, a[href], input, textarea, select').filter((t) => t.underFloor),
          horizontalScroll: a.hScroll(),
          overflowingPanel: a.overflowing(),
          whiteText: a.whiteText(),
          contrast: a.contrastPairs().filter((p) => p.ratio < 4.5),
          contrastCount: a.contrastPairs().length,
          worstContrast: a.contrastPairs().reduce((worst, p) => (!worst || p.ratio < worst.ratio ? p : worst), null),
          distinctTextColors: [...new Set(a.texts().map((t) => t.rgb))].sort(),
          keys: a.targets('.question-key-row button'),
          keyRows: [...root.querySelectorAll('.question-key-row')].map((row) => ({ ...rects(row), scrollW: row.scrollWidth, clientW: row.clientWidth, overflowX: getComputedStyle(row).overflowX, keys: row.querySelectorAll('button').length })),
          chatBack: a.targets('.device-chat-back')[0] ?? null,
          subscreenBack: a.targets('.device-subheader > button')[0] ?? null,
          chatBar: (() => { const el = root.querySelector('.device-chat-bar'); if (!el) return null; return { ...rects(el), scrollW: el.scrollWidth, clientW: el.clientWidth, children: [...el.children].map((c) => ({ cls: typeof c.className === 'string' ? c.className.slice(0, 40) : '', ...rects(c) })) }; })(),
          galleryHeader: (() => { const el = root.querySelector('.device-gallery .device-subheader'); if (!el) return null; return { ...rects(el), wrap: getComputedStyle(el).flexWrap, scrollW: el.scrollWidth, clientW: el.clientWidth, children: [...el.children].map((c) => ({ cls: typeof c.className === 'string' ? c.className.slice(0, 40) : '', ...rects(c), title: c.textContent.trim().slice(0, 20) })) }; })(),
          keyRowScroll: [...root.querySelectorAll('.question-key-row')].map((row) => {
            const keys = [...row.querySelectorAll('button')];
            row.scrollLeft = row.scrollWidth;
            const last = keys[keys.length - 1];
            const reachable = last ? rects(last).right <= rects(row).right + 1 : null;
            row.scrollLeft = 0;
            const first = keys[0];
            const firstReachable = first ? rects(first).left >= rects(row).left - 1 : null;
            return { keys: keys.length, firstKey: first ? rects(first) : null, lastKey: last ? rects(last) : null, row: rects(row), justify: getComputedStyle(row).justifyContent, overflowX: getComputedStyle(row).overflowX, scrollW: row.scrollWidth, clientW: row.clientWidth, scrollable: row.scrollWidth > row.clientWidth, lastKeyReachable: reachable, firstKeyReachable: firstReachable };
          }),
          galleryActionRows: [...root.querySelectorAll('.device-gallery-actions')].map((el) => ({ cls: el.className, ...rects(el), visible: a.box(el).visible })),
          galleryPrimary: (() => { const el = root.querySelector('.device-gallery-primary'); return el ? { ...rects(el), visible: a.box(el).visible, buttons: [...el.querySelectorAll('button')].map((b) => a.name(b)) } : null; })(),
          /* The pinned row must not be sitting on top of the Previous/Next row:
             a tap in the covered band would hit the wrong control. */
          galleryPrevNext: (() => { const el = root.querySelector('.device-gallery-actions:not(.device-gallery-primary)'); return el ? { ...rects(el), visible: a.box(el).visible } : null; })(),
          galleryRowOverlap: (() => {
            const one = root.querySelector('.device-gallery-actions:not(.device-gallery-primary)');
            const two = root.querySelector('.device-gallery-primary');
            if (!one || !two) return null;
            const a1 = one.getBoundingClientRect(); const b1 = two.getBoundingClientRect();
            const ix = Math.max(0, Math.min(a1.right, b1.right) - Math.max(a1.left, b1.left));
            const iy = Math.max(0, Math.min(a1.bottom, b1.bottom) - Math.max(a1.top, b1.top));
            const top = document.elementFromPoint(Math.round((Math.max(a1.left, b1.left) + Math.min(a1.right, b1.right)) / 2), Math.round(Math.min(a1.bottom, b1.bottom) - 2));
            return { area: Math.round(ix * iy), vertical: Math.round(iy), hitTest: top ? a.name(top) : null, pinned: getComputedStyle(two).position };
          })(),
          galleryCamera: (() => { const el = root.querySelector('.device-gallery-camera'); return el ? { ...rects(el), visible: a.box(el).visible } : null; })(),
          galleryContent: (() => { const el = root.querySelector('.device-gallery-content'); return el ? { ...rects(el), scrollW: el.scrollWidth, clientW: el.clientWidth } : null; })(),
          aboutLinks: [...root.querySelectorAll('.device-subcontent a[href]')].map((el) => {
            const bg = a.background(el); const fg = a.color(getComputedStyle(el).color);
            return { href: el.getAttribute('href').slice(0, 40), text: el.textContent.trim().slice(0, 24), color: getComputedStyle(el).color, background: a.rgbText(bg), ratio: a.round(a.ratio(fg.a < 1 ? a.over(fg, bg) : fg, bg)), ...rects(el) };
          }),
          aboutParagraph: (() => { const el = root.querySelector('.device-subcontent details[open] p'); return el ? { text: el.textContent.trim().slice(0, 60), lines: el.getClientRects().length, ...rects(el) } : null; })(),
          /* A/B in one page load: the same anchors with the stylesheet rule
             neutralised by a later, more specific author rule, which is what
             they looked like before this pass. The paragraph's own rect and
             the overlap with its neighbouring text are what "the sentence flow
             did not move" has to mean in rectangles. */
          aboutAnchorAb: (() => {
            const links = [...root.querySelectorAll('.device-subcontent a[href]')];
            if (!links.length) return null;
            const owner = links[0].parentElement;
            const siblings = [...owner.parentElement.children].filter((el) => el !== owner);
            const measure = () => ({
              links: links.map((el) => ({ w: rects(el).w, h: rects(el).h, top: rects(el).top, left: rects(el).left, right: rects(el).right })),
              paragraph: rects(owner),
              overlapWithSiblings: links.map((el) => siblings.reduce((worst, sib) => {
                const a = el.getBoundingClientRect(); const b = sib.getBoundingClientRect();
                const ix = Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left));
                const iy = Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
                return Math.max(worst, Math.round(ix * iy));
              }, 0)),
            });
            const after = measure();
            const style = document.createElement('style');
            style.id = 'audit-before-anchors';
            style.textContent = 'body .device-screen .device-subcontent a[href] { display: inline; min-width: 0; min-height: 0; padding: 0; margin: 0; color: #0000ee; text-decoration: underline; }';
            document.head.append(style);
            const before = measure();
            style.remove();
            const restored = measure();
            return { before, after, restored, neutralised: restored.links[0].h === after.links[0].h };
          })(),
          keyboardTextarea: (() => { const el = root.querySelector('.question-keyboard textarea'); return el ? { color: getComputedStyle(el).color, background: getComputedStyle(el).backgroundColor, deviceTextVar: getComputedStyle(el).getPropertyValue('--device-text').trim(), deviceInkVar: getComputedStyle(el).getPropertyValue('--device-ink').trim() } : null; })(),
          deviceTextOnScreen: getComputedStyle(root).getPropertyValue('--device-text').trim(),
          headerFit: (() => {
            if (!bar) return null;
            const buttons = [...bar.querySelectorAll('button')];
            const p = a.panelRect();
            const r = bar.getBoundingClientRect();
            return {
              rowHeight: a.round(r.height),
              buttonsInsideRow: buttons.every((b) => { const q = b.getBoundingClientRect(); return q.top >= r.top - 0.5 && q.bottom <= r.bottom + 0.5; }),
              contentOverflow: bar.scrollWidth - bar.clientWidth,
              minButtonW: buttons.length ? Math.min(...buttons.map((b) => a.round(b.getBoundingClientRect().width))) : null,
              minButtonH: buttons.length ? Math.min(...buttons.map((b) => a.round(b.getBoundingClientRect().height))) : null,
              lastButtonRight: buttons.length ? a.round(Math.max(...buttons.map((b) => b.getBoundingClientRect().right))) : null,
              panelRight: a.round(p.right),
            };
          })(),
        };
      })()`);
      screen.viewport = `${vp.w}x${vp.h}`;
      screen.panelExact = panel.left === 0 && panel.top === 0 && panel.width === vp.w && panel.height === vp.h;
      if (!screen.panelExact) problems.push(`${vp.view}/${visit.name}: panel moved to ${JSON.stringify(panel)} after navigation`);
      screen.screenshot = await shot(`${tag}-${visit.name}`);
      record.screens[visit.name] = screen;
    }
    console.log(`measured ${vp.view}`);
  }

  results.consoleErrors = cdp.consoleErrors;
} finally {
  try { chrome.kill('SIGKILL'); } catch { /* already gone */ }
}

results.problems = problems;
results.finishedAt = new Date().toISOString();
writeFileSync(join(here, `styles-${LABEL}-results.json`), `${JSON.stringify(results, null, 2)}\n`);

const pad = (v, n) => String(v).padEnd(n);
console.log(`\n=== ${LABEL} ===`);
for (const [view, record] of Object.entries(results.viewports)) {
  console.log(`\n--- ${view} panel=${JSON.stringify(record.panel)} exact=${record.panelMatchesViewport} ---`);
  console.log(`  controls: ${JSON.stringify(record.controls)}`);
  for (const [name, s] of Object.entries(record.screens)) {
    const keys = s.keys.length ? `keys n=${s.keys.length} min ${Math.min(...s.keys.map((k) => k.w))}x${Math.min(...s.keys.map((k) => k.h))} max ${Math.max(...s.keys.map((k) => k.w))}x${Math.max(...s.keys.map((k) => k.h))}` : 'no keys';
    console.log(`  ${pad(name, 15)} open=${s.open} targets=${pad(s.allTargets.length, 3)} under72=${pad(s.underFloor.length, 3)} whiteText=${pad(s.whiteText.length, 3)} worst=${pad(s.worstContrast?.ratio, 6)} "${(s.worstContrast?.text ?? '').slice(0, 22)}" ${keys}`);
    if (s.headerFit) console.log(`      header row=${s.headerFit.rowHeight} inside=${s.headerFit.buttonsInsideRow} overflow=${s.headerFit.contentOverflow} minBtn=${s.headerFit.minButtonW}x${s.headerFit.minButtonH} lastRight=${s.headerFit.lastButtonRight}/${s.headerFit.panelRight} :: ${s.headerControls.map((t) => `${t.label.slice(0, 10)}=${t.w}x${t.h}@${t.left}`).join(' ')}`);
    if (s.chatBack) console.log(`      chatBack ${s.chatBack.w}x${s.chatBack.h} at ${s.chatBack.left},${s.chatBack.top}`);
    if (s.galleryPrimary) console.log(`      galleryPrimary top=${s.galleryPrimary.top} bottom=${s.galleryPrimary.bottom} h=${s.galleryPrimary.h} visible=${s.galleryPrimary.visible} ${JSON.stringify(s.galleryPrimary.buttons)}`);
    if (s.galleryPrevNext) console.log(`      galleryPrevNext top=${s.galleryPrevNext.top} bottom=${s.galleryPrevNext.bottom} visible=${s.galleryPrevNext.visible} overlap=${JSON.stringify(s.galleryRowOverlap)}`);
    if (s.galleryCamera) console.log(`      galleryCamera visible=${s.galleryCamera.visible} ${s.galleryCamera.w}x${s.galleryCamera.h} right=${s.galleryCamera.right}`);
    if (s.galleryContent) console.log(`      galleryContent scrollW=${s.galleryContent.scrollW} clientW=${s.galleryContent.clientW} w=${s.galleryContent.w}`);
    if (s.aboutLinks.length) console.log(`      aboutLinks ${s.aboutLinks.map((l) => `${l.text}:${l.w}x${l.h}@${l.ratio}`).join(' | ')}`);
    if (s.aboutAnchorAb) { const ab = s.aboutAnchorAb; console.log(`      anchorA/B before ${JSON.stringify(ab.before.links)} paragraph ${ab.before.paragraph.h}`); console.log(`      anchorA/B after  ${JSON.stringify(ab.after.links)} paragraph ${ab.after.paragraph.h} overlapWithSiblings ${JSON.stringify(ab.after.overlapWithSiblings)} restored=${ab.neutralised}`); }
    if (s.underFloor.length) console.log(`      underFloor ${JSON.stringify(s.underFloor.slice(0, 8).map((t) => `${t.label.slice(0, 18)}=${t.w}x${t.h}`))}`);
    if (s.horizontalScroll.length) console.log(`      hScroll ${JSON.stringify(s.horizontalScroll.slice(0, 5).map((e) => `${e.cls || e.tag}:${e.scrollW}/${e.clientW}`))}`);
    if (s.overflowingPanel.length) console.log(`      overflowingPanel ${JSON.stringify(s.overflowingPanel.slice(0, 5).map((e) => `${e.cls || e.tag}:${e.left}..${e.right}`))}`);
    if (s.contrast.length) console.log(`      contrastFail ${JSON.stringify(s.contrast.slice(0, 5).map((p) => `${p.text}@${p.ratio}`))}`);
    if (s.distinctTextColors.some((c) => c === 'rgb(255, 255, 255)')) console.log(`      textColors ${JSON.stringify(s.distinctTextColors)}`);
  }
}
console.log(`\nconsole errors: ${results.consoleErrors.length}`);
for (const error of results.consoleErrors.slice(0, 5)) console.log(`  ${error}`);
console.log(`problems: ${problems.length}`);
for (const problem of problems) console.log(`  ! ${problem}`);
console.log(`\nwrote styles-${LABEL}-results.json`);
