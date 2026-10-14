/**
 * Read the Chat bar and composer layout out of the real fixture.
 *
 * A one-off probe rather than a scenario: it measures the box model of the
 * frame and the composer at each viewport, so a wrapping bar or a squeezed
 * field is a reading instead of a guess from a screenshot.
 *
 *   node probe-chat-layout.mjs [base-url]
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8791/screen-fixture.html';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9336;
const here = dirname(fileURLToPath(import.meta.url));

/** [width, height, mobile, framed-panel query] */
const VIEWPORTS = [
  [820, 1700, false, '&frame=1'],
  [480, 1040, true, '&frame=0'],
  [420, 900, true, '&frame=0'],
];

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

const DUMP = `(() => {
  const facts = (selector) => {
    const el = document.querySelector(selector);
    if (!el) return null;
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return { rect: { x: Math.round(rect.left), y: Math.round(rect.top), w: Math.round(rect.width), h: Math.round(rect.height) },
      display: style.display, direction: style.flexDirection, wrap: style.flexWrap,
      text: (el.textContent || '').trim().slice(0, 32) };
  };
  const rowChildren = [...document.querySelectorAll('.device-chat-composer-row > *')].map((child) => {
    const rect = child.getBoundingClientRect();
    return { class: child.className, w: Math.round(rect.width), h: Math.round(rect.height), x: Math.round(rect.left), y: Math.round(rect.top) };
  });
  const screen = document.querySelector('.device-screen');
  const screenStyle = screen ? getComputedStyle(screen) : null;
  return {
    panel: screen ? { w: Math.round(screen.getBoundingClientRect().width), h: Math.round(screen.getBoundingClientRect().height) } : null,
    gridRows: screenStyle?.gridTemplateRows,
    gridChildren: screen ? [...screen.children].map((child) => {
      const style = getComputedStyle(child);
      const rect = child.getBoundingClientRect();
      return { tag: child.tagName.toLowerCase(), class: String(child.className).slice(0, 34), position: style.position,
        row: style.gridRowStart + '/' + style.gridRowEnd, rect: { y: Math.round(rect.top), h: Math.round(rect.height) } };
    }) : null,
    bar: facts('.device-chat-bar'),
    title: facts('.device-chat-bar .device-page-title h1'),
    back: facts('.device-chat-back'),
    footer: facts('.device-navigation-footer'),
    composer: facts('.device-chat-composer'),
    row: facts('.device-chat-composer-row'),
    field: facts('.device-chat-composer-field'),
    reason: facts('.device-chat-composer-reason'),
    rowChildren,
    overflowX: screen ? screen.scrollWidth > screen.clientWidth : null,
  };
})()`;

const profile = `/tmp/ember-chat-probe-${process.pid}`;
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
  for (const [width, height, mobile, frame] of VIEWPORTS) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
    await cdp.send('Page.navigate', { url: `${BASE}?scenario=complete&mic=on&reset=ok${frame}` });
    for (let attempt = 0; attempt < 100; attempt += 1) {
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.preview && !!document.querySelector(".device-screen")').catch(() => false);
      if (ready) break;
      await sleep(100);
    }
    await sleep(1200);
    const tile = await cdp.evaluate(`(() => {
      const tile = [...document.querySelectorAll('.device-home-tile')].find((entry) => entry.dataset.destination === 'chat');
      if (!tile) return null;
      const rect = tile.getBoundingClientRect();
      return { x: Math.round(rect.left + rect.width / 2), y: Math.round(rect.top + rect.height / 2) };
    })()`);
    if (!tile) { console.log(`${width}x${height}: the Chat tile was not on screen`); continue; }
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: tile.x, y: tile.y, button: 'left', buttons: 1, clickCount: 1 });
    await sleep(60);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: tile.x, y: tile.y, button: 'left', buttons: 0, clickCount: 1 });
    await sleep(900);
    console.log(`\n=== viewport ${width}x${height} (panel ${JSON.stringify((await cdp.evaluate(DUMP)).panel)}) ===`);
    console.log(JSON.stringify(await cdp.evaluate(DUMP), null, 1));
  }
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* already gone */ }
  chrome.kill('SIGKILL');
}
