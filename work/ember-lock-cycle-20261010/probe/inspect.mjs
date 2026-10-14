/**
 * Print the real DOM around a control, so a selector can be derived from what
 * the app rendered rather than from what it was expected to render. Diagnostic,
 * not evidence.
 *
 *   node probe/inspect.mjs <fixture-url>
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const URL_UNDER_TEST = process.argv[2] ?? 'http://127.0.0.1:8796/probe/lock-fixture.html';
const CDP_PORT = Number(process.env.LOCK_CDP_PORT ?? 9388);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PIN = '13572468';

const chrome = spawn(CHROME, ['--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=/tmp/ember-lock-inspect-${process.pid}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--no-sandbox', '--disable-setuid-sandbox', 'about:blank'],
  { stdio: ['ignore', 'pipe', 'pipe'] });
chrome.stderr.on('data', () => {});

async function target() {
  for (let i = 0; i < 80; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json();
      const page = list.find((t) => t.type === 'page');
      if (page?.webSocketDebuggerUrl) return page;
    } catch {}
    await sleep(250);
  }
  throw new Error('no devtools target');
}

const ws = new WebSocket((await target()).webSocketDebuggerUrl);
let id = 0;
const pending = new Map();
await new Promise((resolve) => ws.addEventListener('open', resolve));
ws.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  const entry = pending.get(message.id);
  if (entry) { pending.delete(message.id); entry(message.result); }
});
const send = (method, params = {}) => new Promise((resolve) => {
  const next = ++id; pending.set(next, resolve);
  ws.send(JSON.stringify({ id: next, method, params }));
});
const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) throw new Error(result.exceptionDetails.text);
  return result.result.value;
};
const click = async (finder) => {
  const box = await evaluate(`(() => { const el = ${finder}; if (!el) return null; el.scrollIntoView({ block: 'center' }); const r = el.getBoundingClientRect(); return { x: r.x + r.width / 2, y: r.y + r.height / 2, w: r.width, h: r.height }; })()`);
  if (!box) throw new Error(`not found: ${finder}`);
  const point = { x: Math.round(box.x), y: Math.round(box.y) };
  await send('Input.dispatchMouseEvent', { type: 'mousePressed', button: 'left', clickCount: 1, ...point });
  await send('Input.dispatchMouseEvent', { type: 'mouseReleased', button: 'left', clickCount: 1, ...point });
  return box;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 1000, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: URL_UNDER_TEST });
await sleep(2500);
for (const digit of PIN) { await click(`[...document.querySelectorAll('.device-lock-keypad button')].find((b) => b.textContent.trim() === ${JSON.stringify(digit)})`); await sleep(60); }
await click(`document.querySelector('.device-lock-submit')`);
await sleep(2500);

console.log('=== buttons on Home ===');
console.log(JSON.stringify(await evaluate(`[...document.querySelectorAll('.device-screen button')].map((b) => { const r = b.getBoundingClientRect(); return { label: b.getAttribute('aria-label'), cls: b.className, text: b.textContent.trim().slice(0, 40), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; })`), null, 1));

// Open Settings through the real gear, then list every control it offers.
await click(`document.querySelector('.device-screen button[aria-label="Settings"]')`);
await sleep(1500);
console.log('=== after opening Settings: every element containing "Lock now" ===');
console.log(JSON.stringify(await evaluate(`[...document.querySelectorAll('#root *')].filter((el) => (el.textContent || '').includes('Lock now')).map((el) => { const r = el.getBoundingClientRect(); return { tag: el.tagName, cls: typeof el.className === 'string' ? el.className : '', own: [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).filter(Boolean).join('|'), x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) }; })`), null, 1));

chrome.kill('SIGKILL');
process.exit(0);
