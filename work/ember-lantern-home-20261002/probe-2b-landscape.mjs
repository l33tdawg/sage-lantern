/**
 * Probe the 1024x600 landscape Home layout.
 *
 * Written to answer one question the driver's 43-scenario run could not: when the
 * new "nothing in the stage paints outside the panel" check fires at 1024x600, is
 * it a real layout defect or a bad selector in the check? The check used
 * `document.querySelector('.device-home')`, which returns the FIRST match — if
 * more than one exists, the panel rect it compares against is not necessarily the
 * visible one, and the finding would be an artefact.
 *
 *   node probe-2b-landscape.mjs [width] [height]
 *
 * Read-only: launches its own headless Chrome on its own port and profile, loads
 * the local fixture, measures, and exits. It writes no fixture output.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';

const WIDTH = Number(process.argv[2] ?? 1024);
const HEIGHT = Number(process.argv[3] ?? 600);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9346;
const BASE = process.env.PROBE_BASE ?? 'http://127.0.0.1:8791/screen-fixture.html';

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=/tmp/ember-probe-${process.pid}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text', '--no-sandbox', 'about:blank',
], { stdio: 'ignore' });

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url); this.nextId = 0; this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      this.ws.addEventListener('open', () => resolve());
      this.ws.addEventListener('error', (e) => reject(new Error(String(e.message))));
    });
    this.ws.addEventListener('message', (event) => {
      const m = JSON.parse(event.data);
      const entry = this.pending.get(m.id); if (!entry) return;
      this.pending.delete(m.id);
      if (m.error) entry.reject(new Error(m.error.message)); else entry.resolve(m.result);
    });
  }
  async send(method, params = {}) {
    await this.ready; const id = ++this.nextId;
    return new Promise((resolve, reject) => { this.pending.set(id, { resolve, reject }); this.ws.send(JSON.stringify({ id, method, params })); });
  }
  async evaluate(expression) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.text);
    return r.result.value;
  }
}

let target = null;
for (let i = 0; i < 80 && !target; i += 1) {
  try {
    const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json();
    target = list.find((t) => t.type === 'page');
  } catch { /* not up yet */ }
  if (!target) await sleep(250);
}
if (!target) { chrome.kill(); throw new Error('no devtools target'); }

const cdp = new CDP(target.webSocketDebuggerUrl);
await cdp.send('Runtime.enable');
await cdp.send('Emulation.setDeviceMetricsOverride', { width: WIDTH, height: HEIGHT, deviceScaleFactor: 1, mobile: false });
await cdp.send('Page.navigate', { url: `${BASE}?scenario=complete&mic=on&reset=ok&frame=0` });
for (let i = 0; i < 60; i += 1) {
  if (await cdp.evaluate('!!document.querySelector(".device-home-tile")').catch(() => false)) break;
  await sleep(100);
}
await sleep(900);

// The harness banner is in normal flow above the panel, so leaving it up makes
// every absolute `t` a statement about the harness rather than the device. The
// driver hides it for the moment of capture; hiding it for the measurement too is
// what makes these numbers device geometry. Relative findings are unaffected
// either way, which is why the earlier run was still able to see the overflow.
const banner = await cdp.evaluate(`(() => {
  const n = document.querySelector('.preview-notice');
  if (!n) return false;
  n.style.display = 'none';
  return true;
})()`);
await sleep(200);

const report = await cdp.evaluate(`(() => {
  const rect = (el) => { const r = el.getBoundingClientRect(); return { l: Math.round(r.left), r: Math.round(r.right), t: Math.round(r.top), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) }; };
  const homes = [...document.querySelectorAll('.device-home')].map((el, i) => ({
    i, cls: String(el.className), rect: rect(el),
    visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
    display: getComputedStyle(el).display,
    ancestorHidden: !!el.closest('[aria-hidden="true"], [hidden]'),
    parent: String(el.parentElement?.className || el.parentElement?.tagName || ''),
  }));
  const stage = document.querySelector('.device-home-stage');
  const feedback = document.querySelector('.device-home-feedback');
  const strip = document.querySelector('.device-home-status');
  const tiles = document.querySelector('.device-home-tiles');
  const answer = document.querySelector('.device-home-answer');
  return {
    viewport: { w: innerWidth, h: innerHeight },
    homes,
    stage: stage ? rect(stage) : null,
    feedback: feedback ? rect(feedback) : null,
    feedbackScroll: feedback ? { scrollH: feedback.scrollHeight, clientH: feedback.clientHeight } : null,
    strip: strip ? rect(strip) : null,
    tiles: tiles ? rect(tiles) : null,
    answer: answer ? { rect: rect(answer), text: (answer.textContent || '').slice(0, 60) } : null,
    stageStyle: stage ? { alignItems: getComputedStyle(stage).alignItems, flexDirection: getComputedStyle(stage).flexDirection } : null,
    feedbackStyle: feedback ? { flex: getComputedStyle(feedback).flex, minHeight: getComputedStyle(feedback).minHeight, overflow: getComputedStyle(feedback).overflow, alignSelf: getComputedStyle(feedback).alignSelf } : null,
    bodyScroll: { w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight },
  };
})()`);

console.log(JSON.stringify(report, null, 2));

// Capture the same viewport the driver screenshots, so the measured rects can be
// checked against what is actually painted rather than against an eyeball estimate.
const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
const { writeFileSync } = await import('node:fs');
const out = `screenshots/probe-${WIDTH}x${HEIGHT}.png`;
writeFileSync(out, Buffer.from(shot.data, 'base64'));
console.log(`screenshot: ${out}`);
chrome.kill();
