/**
 * One question, asked in the browser: what does the gallery viewer's action row
 * need at 1024x600 so that it is fully visible WITHOUT a pinned row covering the
 * Previous/Next row above it?
 *
 * Candidate styles are injected one at a time and the scroll overflow of the
 * viewer's own scroll region is re-measured for each, with the primary row put
 * back into normal flow so its un-pinned position is what is read.
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8907/screen-fixture.html';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9407;
const here = dirname(fileURLToPath(import.meta.url));

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url); this.nextId = 0; this.pending = new Map();
    this.ready = new Promise((resolve, reject) => { this.ws.addEventListener('open', () => resolve()); this.ws.addEventListener('error', (e) => reject(new Error(String(e.message)))); });
    this.ws.addEventListener('message', (event) => {
      const m = JSON.parse(event.data); const entry = this.pending.get(m.id); if (!entry) return;
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
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
    return r.result.value;
  }
}
async function target() {
  for (let i = 0; i < 80; i += 1) {
    try { const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json(); const page = list.find((t) => t.type === 'page'); if (page?.webSocketDebuggerUrl) return page; } catch { /* wait */ }
    await sleep(250);
  }
  throw new Error('no devtools target');
}
const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=/tmp/gallery-probe-${process.pid}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars', '--force-device-scale-factor=1',
  '--no-sandbox', '--disable-setuid-sandbox', '--disable-crashpad', '--disable-breakpad', 'about:blank'], { stdio: 'ignore' });
let cdp;
try {
  cdp = new CDP((await target()).webSocketDebuggerUrl);
  await cdp.send('Page.enable'); await cdp.send('Runtime.enable');
  await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1024, height: 600, deviceScaleFactor: 1, mobile: false });
  await cdp.send('Page.navigate', { url: `${BASE}?scenario=complete&mic=on&reset=ok&photos=three&frame=0` });
  for (let i = 0; i < 150; i += 1) { if (await cdp.evaluate('!!document.querySelector(".device-home-tile")').catch(() => false)) break; await sleep(100); }
  await cdp.evaluate(`(() => { const n = document.querySelector('.preview-notice'); if (n) n.style.display = 'none'; return true; })()`);
  await sleep(800);
  const click = async (selector) => {
    const p = await cdp.evaluate(`(() => { const el = document.querySelector(${JSON.stringify(selector)}); if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    if (!p) throw new Error(`nothing to click for ${selector}`);
    await sleep(200);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: p.x, y: p.y, button: 'left', clickCount: 1 });
    await sleep(40);
    await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: p.x, y: p.y, button: 'left', clickCount: 1 });
  };
  await click('.device-home-tile[data-destination="gallery"]');
  for (let i = 0; i < 100; i += 1) { if (await cdp.evaluate('!!document.querySelector(".device-gallery-thumb")').catch(() => false)) break; await sleep(100); }
  await sleep(400);
  await click('.device-gallery-thumb');
  await sleep(700);
  if (!(await cdp.evaluate('!!document.querySelector(".device-gallery-viewer")'))) throw new Error('the viewer never opened');

  await cdp.evaluate(`(() => {
    const style = document.createElement('style'); style.id = 'probe'; document.head.append(style);
    window.__probe = (css) => {
      document.querySelector('#probe').textContent = css;
      const a = (sel) => { const el = document.querySelector(sel); if (!el) return null; const r = el.getBoundingClientRect(); return { top: Math.round(r.top * 10) / 10, bottom: Math.round(r.bottom * 10) / 10, h: Math.round(r.height * 10) / 10 }; };
      const port = document.querySelector('.device-gallery-content');
      return {
        overflow: port.scrollHeight - port.clientHeight,
        port: { top: Math.round(port.getBoundingClientRect().top), bottom: Math.round(port.getBoundingClientRect().bottom), h: port.clientHeight, scrollH: port.scrollHeight },
        frame: a('.device-gallery-frame'),
        image: a('.device-gallery-frame img'),
        prevNext: a('.device-gallery-actions:not(.device-gallery-primary)'),
        primary: a('.device-gallery-primary'),
        question: a('.device-gallery-question'),
        position: a('.device-gallery-position'),
      };
    };
    return true;
  })()`);

  const candidates = [
    ['current (sticky only)', ''],
    ['img max-height 36dvh', '.device-gallery-frame img { max-height: 36dvh; }'],
    ['img 32dvh', '.device-gallery-frame img { max-height: 32dvh; }'],
    ['img 28dvh', '.device-gallery-frame img { max-height: 28dvh; }'],
    ['img 24dvh', '.device-gallery-frame img { max-height: 24dvh; }'],
    ['img 32dvh + viewer gap 8', '.device-gallery-frame img { max-height: 32dvh; } .device-gallery-viewer { gap: 8px; }'],
    ['img 28dvh + viewer gap 8', '.device-gallery-frame img { max-height: 28dvh; } .device-gallery-viewer { gap: 8px; }'],
    ['img 32dvh + gap 8 + frame padding 6', '.device-gallery-frame img { max-height: 32dvh; } .device-gallery-viewer { gap: 8px; } .device-gallery-frame { padding: 6px; }'],
    ['img 28dvh + gap 8 + frame padding 6', '.device-gallery-frame img { max-height: 28dvh; } .device-gallery-viewer { gap: 8px; } .device-gallery-frame { padding: 6px; }'],
    ['img 36dvh + gap 8 + frame padding 6 + question 20px', '.device-gallery-frame img { max-height: 36dvh; } .device-gallery-viewer { gap: 8px; } .device-gallery-frame { padding: 6px; } .device-gallery-question { font-size: 20px; }'],
    ['img 32dvh + gap 8 + frame padding 6 + question 20px', '.device-gallery-frame img { max-height: 32dvh; } .device-gallery-viewer { gap: 8px; } .device-gallery-frame { padding: 6px; } .device-gallery-question { font-size: 20px; }'],
  ];
  const pin = 'position: static !important; ';
  console.log('candidate | scroll overflow | img h | frame h | prevNext top..bottom | primary top..bottom (in flow)');
  for (const [name, css] of candidates) {
    const unpinned = await cdp.evaluate(`(() => { const el = document.querySelector('.device-gallery-primary'); el.style.setProperty('position', 'static', 'important'); const out = window.__probe(${JSON.stringify(css)}); el.style.removeProperty('position'); return out; })()`);
    console.log(`${name.padEnd(52)} | ${String(unpinned.overflow).padStart(4)} | ${unpinned.image?.h} | ${unpinned.frame?.h} | ${unpinned.prevNext?.top}..${unpinned.prevNext?.bottom} | ${unpinned.primary?.top}..${unpinned.primary?.bottom} | port ${unpinned.port.top}..${unpinned.port.bottom} (h ${unpinned.port.h}, scrollH ${unpinned.port.scrollH})`);
  }
  const pinned = await cdp.evaluate(`window.__probe('')`);
  console.log('\nwith the shipped sticky rule engaged:', JSON.stringify(pinned));
} finally {
  try { chrome.kill('SIGKILL'); } catch { /* gone */ }
}
