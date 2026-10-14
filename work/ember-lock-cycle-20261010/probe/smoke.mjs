/**
 * Reconnaissance for the lock fixture: load the page, let it settle, and print
 * what actually rendered plus the request trail. This is a diagnostic, not
 * evidence — it exists to answer "why is nothing on screen" without guessing.
 *
 *   node probe/smoke.mjs <fixture-url>
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const URL_UNDER_TEST = process.argv[2] ?? 'http://127.0.0.1:8796/probe/lock-fixture.html';
const CDP_PORT = Number(process.env.LOCK_CDP_PORT ?? 9377);
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PROFILE = `/tmp/ember-lock-smoke-${process.pid}`;

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${CDP_PORT}`, `--user-data-dir=${PROFILE}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--no-sandbox', '--disable-setuid-sandbox', 'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
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
const consoleMessages = [];
const requests = [];
await new Promise((resolve) => ws.addEventListener('open', resolve));
ws.addEventListener('message', (event) => {
  const message = JSON.parse(event.data);
  if (message.method === 'Runtime.consoleAPICalled') {
    consoleMessages.push(`${message.params.type}: ${(message.params.args ?? []).map((a) => String(a.value ?? a.description ?? '')).join(' ')}`);
  }
  if (message.method === 'Runtime.exceptionThrown') {
    consoleMessages.push(`EXCEPTION: ${message.params?.exceptionDetails?.exception?.description ?? message.params?.exceptionDetails?.text}`);
  }
  if (message.method === 'Network.requestWillBeSent') requests.push(message.params.request.url);
  const entry = pending.get(message.id);
  if (entry) { pending.delete(message.id); entry(message.result); }
});
const send = (method, params = {}) => new Promise((resolve) => {
  const next = ++id;
  pending.set(next, resolve);
  ws.send(JSON.stringify({ id: next, method, params }));
});
const evaluate = async (expression) => {
  const result = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true });
  if (result.exceptionDetails) return { error: result.exceptionDetails.text };
  return result.result.value;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Network.enable');
await send('Emulation.setDeviceMetricsOverride', { width: 1100, height: 1000, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: URL_UNDER_TEST });
await sleep(6000);

console.log('--- requests ---');
for (const url of requests) console.log(`  ${url}`);
console.log('--- console ---');
for (const line of consoleMessages) console.log(`  ${line}`);
console.log('--- page ---');
console.log(JSON.stringify(await evaluate(`(() => {
  const root = document.getElementById('root');
  const stub = window.__STUB__;
  return {
    stubPresent: !!stub,
    authCalls: stub ? stub.authCalls : null,
    blocked: stub ? stub.blocked : null,
    rootChildren: root ? root.children.length : -1,
    innerText: root ? root.innerText.slice(0, 500) : '',
    classes: root ? [...root.querySelectorAll('*')].slice(0, 25).map((el) => el.className).filter((c) => typeof c === 'string' && c) : [],
  };
})()`), null, 2));

const shot = await send('Page.captureScreenshot', { format: 'png' });
writeFileSync(join(here, '..', 'evidence', 'smoke.png'), Buffer.from(shot.data, 'base64'));
console.log('screenshot: evidence/smoke.png');
chrome.kill('SIGKILL');
process.exit(0);
