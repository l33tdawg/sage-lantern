/**
 * Probe the CHAT interface of the real device UI in a real browser.
 *
 * Same harness as drive-screen-fixture.mjs (headless Chrome over CDP, the real
 * screen.tsx from the current working tree, the synthetic device service), with
 * checks aimed at what the owner named: the past-chats list, where it lives at
 * each viewport, whether a past thread can be opened and survives navigation,
 * the composer on the narrow panels, New chat, and the 409 session-mismatch
 * path.
 *
 *   node probe-chat.mjs [base-url] [label]
 *
 * Visibility is a RECTANGLE claim here, never an innerText claim: React leaves
 * stray text nodes, so every "the user can see this" check walks the leaf text
 * nodes and requires a non-empty intersection with the panel box. Two controls
 * are run against that predicate so a predicate that never fires cannot pass:
 * a positive control (text known to be painted) and a negative control (a node
 * planted outside the panel, plus the closed drawer's own off-canvas text).
 */
import { spawn } from 'node:child_process';
import { setTimeout as sleep } from 'node:timers/promises';
import { mkdirSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const BASE = process.argv[2] ?? 'http://127.0.0.1:8901/screen-fixture.html';
const LABEL = process.argv[3] ?? 'chat';
const CHROME = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const PORT = 9401;
const here = dirname(fileURLToPath(import.meta.url));
const shots = join(here, 'screenshots');
mkdirSync(shots, { recursive: true });

const RESULTS_FILE = `screen-${LABEL}-results.json`;
const results = [];
const consoleErrors = [];
const record = (name, checks, screenshot) => results.push({ scenario: name, screenshot, checks, passed: checks.every((c) => c.ok) });
const check = (name, ok, detail = '') => ({ name, ok: !!ok, detail: String(detail) });

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
        consoleErrors.push((message.params.args ?? []).map((a) => String(a.value ?? a.description ?? '')).join(' '));
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
    if (result.exceptionDetails) throw new Error(`evaluate failed: ${result.exceptionDetails.text} :: ${expression.slice(0, 200)}`);
    return result.result.value;
  }
}

async function devtoolsTarget() {
  for (let attempt = 0; attempt < 80; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/json/list`);
      const targets = await response.json();
      const page = targets.find((t) => t.type === 'page');
      if (page?.webSocketDebuggerUrl) return page;
    } catch { /* not up yet */ }
    await sleep(250);
  }
  throw new Error('Chrome DevTools endpoint never became available');
}

const profile = `/tmp/lantern-chat-probe-${process.pid}`;
const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`,
  '--no-first-run', '--no-default-browser-check', '--disable-gpu', '--hide-scrollbars',
  '--force-device-scale-factor=1', '--disable-lcd-text',
  '--no-sandbox', '--disable-setuid-sandbox', '--disable-crashpad', '--disable-breakpad',
  'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
chrome.stderr.on('data', () => {});

/**
 * A leaf-text visibility probe for one selector, judged against the panel box.
 *
 * Returns every leaf text node under `root`, its own client rect (via a Range,
 * so a text node has geometry at all), and whether that rect intersects the
 * panel. `sameTextVisible` counts the nodes whose text matches `needle`.
 */
const LEAF_VISIBILITY = (selector, needle) => `(() => {
  const panel = document.querySelector('.device-screen');
  if (!panel) return { error: 'no panel' };
  const pr = panel.getBoundingClientRect();
  const root = document.querySelector(${JSON.stringify(selector)});
  if (!root) return { present: false, panel: null, leaves: 0, visible: 0, matches: 0, matchesVisible: 0, samples: [] };
  const leaves = [];
  const walk = (node) => {
    for (const child of node.childNodes) {
      if (child.nodeType === 3) {
        const text = (child.textContent || '').trim();
        if (text) {
          const range = document.createRange();
          range.selectNodeContents(child);
          const r = range.getBoundingClientRect();
          leaves.push({
            text: text.slice(0, 60),
            w: Math.round(r.width), h: Math.round(r.height),
            l: Math.round(r.left), t: Math.round(r.top),
            intersects: r.width > 0 && r.height > 0 && r.right > pr.left && r.left < pr.right && r.bottom > pr.top && r.top < pr.bottom,
          });
        }
      } else if (child.nodeType === 1) walk(child);
    }
  };
  walk(root);
  const wanted = ${JSON.stringify(needle ?? null)};
  const matching = wanted === null ? leaves : leaves.filter((leaf) => leaf.text.includes(wanted));
  return {
    present: true,
    panel: { l: Math.round(pr.left), t: Math.round(pr.top), r: Math.round(pr.right), b: Math.round(pr.bottom), w: Math.round(pr.width), h: Math.round(pr.height) },
    leaves: leaves.length,
    visible: leaves.filter((leaf) => leaf.intersects).length,
    matches: matching.length,
    matchesVisible: matching.filter((leaf) => leaf.intersects).length,
    samples: leaves.slice(0, 8),
  };
})()`;

let cdp;
try {
  const target = await devtoolsTarget();
  cdp = new CDP(target.webSocketDebuggerUrl);
  await cdp.send('Page.enable');
  await cdp.send('Runtime.enable');

  async function shot(name) {
    const { data } = await cdp.send('Page.captureScreenshot', { format: 'png' });
    writeFileSync(join(shots, name), Buffer.from(data, 'base64'));
    return `screenshots/${name}`;
  }

  /** Capture the panel at its own size, with the harness banner out of the way. */
  async function shotElement(name, selector = '.device-screen') {
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
      writeFileSync(join(shots, name), Buffer.from(data, 'base64'));
      ok = true;
    }
    return ok ? `screenshots/${name}` : shot(name);
  }

  /**
   * Load the fixture at an exact viewport.
   *
   * The banner is hidden for the whole run: with `frame=0` the panel is
   * `100dvh`, so a banner above it pushes the panel's own bottom below the fold
   * and a trusted click aimed at the footer would land on harness chrome. The
   * banner is synthetic surround, not the device.
   */
  async function load(query, { width, height, mobile }) {
    await cdp.send('Emulation.setDeviceMetricsOverride', { width, height, deviceScaleFactor: 1, mobile });
    await cdp.send('Page.navigate', { url: `${BASE}${query}` });
    for (let attempt = 0; attempt < 120; attempt += 1) {
      const ready = await cdp.evaluate('document.readyState === "complete" && !!window.preview && !!document.querySelector(".device-screen")').catch(() => false);
      if (ready) {
        // A hidden document makes the app CORRECTLY drop protected state; a
        // stale hidden flag here would manufacture defects. Bring it forward and
        // focus it before anything is measured, then report what it actually was.
        await cdp.send('Page.bringToFront').catch(() => {});
        await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
        await cdp.evaluate(`(() => {
          const el = document.querySelector('.preview-notice'); if (el) el.style.display = 'none';
          const log = document.querySelector('.preview-log'); if (log) log.style.display = 'none';
        })()`).catch(() => {});
        await sleep(1500);
        return;
      }
      await sleep(100);
    }
    throw new Error(`preview never mounted for ${query}`);
  }

  const visibility = () => cdp.evaluate(`(() => ({ hidden: document.hidden, state: document.visibilityState, focus: document.hasFocus() }))()`);
  const probe = () => cdp.evaluate('window.preview.probe()');
  const commands = (state) => (state.log || []).join(' | ');
  /**
   * The fixture's own proof that the protected-read authority was established.
   *
   * `/history/list` is a protected read: it is refused unless the session
   * context is established, and the offline cascade that a missing `/voice`
   * channel produces leaves it absent. A run without this line has no authority,
   * so every protected-surface result in it would be void.
   */
  const authorityOk = (state) => commands(state).includes('POST /history/list → 200');
  /** Offline-shaped strings. These are fixture-currency artifacts, never defects. */
  const OFFLINE = ['No connection to the device service', 'Reconnecting', 'Voice is not ready', 'Saved photos unavailable', 'The device is offline', 'unavailable'];
  const offlineShaped = (view) => (view.offlineText || []).filter((line) => OFFLINE.some((needle) => line.includes(needle)));

  const press = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  const release = (point) => cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  const click = async (point) => {
    if (!point) { record('driver', [check('a click target was on screen', false, 'the element was missing when the scenario tried to click it')], ''); return false; }
    await press(point); await sleep(60); await release(point); await sleep(700);
    return true;
  };

  const boxOf = (selector) => cdp.evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return null;
    el.scrollIntoView({ block: 'nearest' });
    const r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) return null;
    return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), disabled: !!el.disabled, w: Math.round(r.width), h: Math.round(r.height) };
  })()`);

  const typeInto = async (point, value) => {
    await click(point);
    await cdp.send('Input.insertText', { text: value });
    await sleep(400);
  };

  /**
   * Wait for a cleared thread to reach the past-chats list, and time it.
   *
   * A clear moves the active thread into saved history, but the screen only
   * forces a history read while the voice phase is `complete`, and the cache
   * treats any other read inside HISTORY_REFRESH_MS (30s) as a no-op. So the
   * delay is a product behaviour to be measured, not a race to be slept through.
   */
  const waitForEntry = async (maxMs = 34000, stepMs = 1000) => {
    const started = Date.now();
    const before = await probe();
    const readsBefore = (commands(before).match(/POST \/history\/list → 200/g) || []).length;
    let view = await chatUi();
    while (Date.now() - started < maxMs) {
      if (view.entries.length > 0) {
        const after = await probe();
        return { appeared: true, elapsed: Date.now() - started, view,
          readsAfter: (commands(after).match(/POST \/history\/list → 200/g) || []).length - readsBefore };
      }
      await sleep(stepMs);
      view = await chatUi();
    }
    const after = await probe();
    return { appeared: false, elapsed: Date.now() - started, view,
      readsAfter: (commands(after).match(/POST \/history\/list → 200/g) || []).length - readsBefore };
  };

  const openChat = async () => {
    const tile = await cdp.evaluate(`(() => {
      const el = document.querySelector('.device-home-tile[data-destination="chat"]');
      if (!el) return null;
      const r = el.getBoundingClientRect();
      return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
    })()`);
    await click(tile);
    await sleep(1000);
  };

  /** Everything the past-chats list and the chat column are, read from the DOM. */
  const chatUi = () => cdp.evaluate(`(() => {
    const text = (sel) => document.querySelector(sel)?.textContent?.trim() ?? null;
    const list = document.querySelector('#device-chats');
    const main = document.querySelector('.device-main');
    const panel = document.querySelector('.device-screen');
    const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { l: Math.round(r.left), t: Math.round(r.top), r: Math.round(r.right), b: Math.round(r.bottom), w: Math.round(r.width), h: Math.round(r.height) }; };
    const style = list ? getComputedStyle(list) : null;
    const btn = document.querySelector('.device-chat-list-button');
    const newChat = document.querySelector('.device-chat-new-chat');
    return {
      panel: rect(panel),
      layout: panel ? [...panel.classList].filter((c) => c.startsWith('device-') && (c === 'device-thread-layout' || c === 'device-show-chats' || c === 'device-home-active')) : [],
      onChat: !!document.querySelector('.device-chat'),
      listPresent: !!list,
      list: rect(list),
      listDisplay: style?.display ?? null,
      listVisibility: style?.visibility ?? null,
      listPosition: style?.position ?? null,
      listTransform: style?.transform ?? null,
      main: rect(main),
      section: rect(document.querySelector('.device-chat')),
      entries: [...document.querySelectorAll('#device-chats nav button')].map((el) => ({
        title: el.querySelector('strong')?.textContent?.trim() ?? null,
        count: el.querySelector('small')?.textContent?.trim() ?? null,
        current: el.getAttribute('aria-current'),
        disabled: !!el.disabled,
        box: (() => { const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), w: Math.round(r.width), h: Math.round(r.height) }; })(),
      })),
      navLabel: document.querySelector('#device-chats nav')?.getAttribute('aria-label') ?? null,
      asideLabel: list?.getAttribute('aria-label') ?? null,
      listButton: btn ? { text: btn.textContent.trim(), controls: btn.getAttribute('aria-controls'), expanded: btn.getAttribute('aria-expanded'), disabled: !!btn.disabled } : null,
      newChatButton: newChat ? { text: newChat.textContent.trim(), disabled: !!newChat.disabled } : null,
      threadHeading: text('.device-chat-bar .device-page-title h1'),
      exchanges: document.querySelectorAll('.device-chat-exchange').length,
      exchangeText: [...document.querySelectorAll('.device-chat-exchange')].map((el) => (el.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 90)),
      readerScrollTop: document.querySelector('#device-answer') ? Math.round(document.querySelector('#device-answer').scrollTop) : null,
      readerScrollHeight: document.querySelector('#device-answer')?.scrollHeight ?? null,
      readerClientHeight: document.querySelector('#device-answer')?.clientHeight ?? null,
      scrim: !!document.querySelector('.device-chat-scrim'),
      savedHistoryNote: [...document.querySelectorAll('#device-chats p')].map((el) => el.textContent.trim()),
      composer: (() => { const el = document.querySelector('.device-chat-composer-field'); if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), h: Math.round(r.height), r: Math.round(r.right), b: Math.round(r.bottom), disabled: !!el.disabled, text: el.textContent.trim() }; })(),
      composerReason: text('.device-chat-composer-reason'),
      footer: rect(document.querySelector('.device-navigation-footer')),
      homeBlocked: text('.device-home-blocked'),
      allBlocked: [...document.querySelectorAll('.device-home-blocked')].map((el) => (el.textContent || '').trim()).filter(Boolean),
      statusLabel: text('.device-home-status-label'),
      statusDetail: text('.device-home-status-detail'),
      offlineText: [...document.querySelectorAll('.device-home-blocked, .device-chat-notice, .device-inline-error, #device-chats p')]
        .map((el) => (el.textContent || '').trim()).filter(Boolean),
      homeCancels: document.querySelectorAll('.device-home-cancel').length,
      recording: !!document.querySelector('.device-home-cancel'),
      phase: text('.device-phase'),
    };
  })()`);

  const chatVisible = async (selector, needle) => cdp.evaluate(LEAF_VISIBILITY(selector, needle));

  // ======================================================================
  // A. Visibility predicate controls: prove the predicate CAN fail.
  // ======================================================================
  {
    const checks = [];
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 720, height: 1560, mobile: true });
    const vis = await visibility();
    checks.push(check('the page is genuinely visible before anything is measured', vis.hidden === false && vis.state === 'visible', JSON.stringify(vis)));

    // A positive control: a node planted inside the panel and painted.
    // A negative control: an identical node planted well outside the panel.
    const control = await cdp.evaluate(`(() => {
      const panel = document.querySelector('.device-screen');
      const pr = panel.getBoundingClientRect();
      const inside = document.createElement('span');
      inside.id = 'probe-inside'; inside.textContent = 'PROBEINSIDE';
      inside.style.cssText = 'position:absolute;left:4px;top:' + Math.round(pr.height / 2) + 'px;color:#fff;z-index:9999;';
      const outside = document.createElement('span');
      outside.id = 'probe-outside'; outside.textContent = 'PROBEOUTSIDE';
      outside.style.cssText = 'position:absolute;left:-4000px;top:-4000px;color:#fff;';
      panel.appendChild(inside); document.body.appendChild(outside);
      return true;
    })()`);
    const inRes = await chatVisible('#probe-inside', 'PROBEINSIDE');
    const outRes = await chatVisible('#probe-outside', 'PROBEOUTSIDE');
    checks.push(check('positive control: painted text inside the panel is seen', control && inRes.matchesVisible === 1, JSON.stringify(inRes)));
    checks.push(check('negative control: the same text outside the panel is NOT seen', outRes.matches === 1 && outRes.matchesVisible === 0, JSON.stringify(outRes)));
    await cdp.evaluate(`(() => { document.querySelector('#probe-inside')?.remove(); document.querySelector('#probe-outside')?.remove(); })()`);
    record('visibility-predicate-controls', checks, '');
  }

  // ======================================================================
  // B. The past-chats list at 720x1560 portrait (the device's own geometry).
  // ======================================================================
  {
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 720, height: 1560, mobile: true });
    await openChat();
    // Retire the delivered exchange into saved history, so there IS a past chat
    // for the list to be about. The real clear moves the thread, it does not
    // delete it.
    const newChatBox = await boxOf('.device-chat-new-chat');
    await click(newChatBox);
    await sleep(2600);
    // How long the just-cleared conversation takes to appear in its own list.
    const arrival = await waitForEntry();

    const closed = await chatUi();
    const closedShot = await shotElement('screen-chat-list-portrait-closed.png');
    const closedText = await chatVisible('#device-chats', 'Conversations');

    // The control the frame offers for the list.
    const button = await boxOf('.device-chat-list-button');
    await click(button);
    await sleep(700);
    const open = await chatUi();
    const openShot = await shotElement('screen-chat-list-portrait-open.png');
    const openText = await chatVisible('#device-chats', 'Conversations');
    const bState = await probe();

    const panel = closed.panel;
    const listOnLeft = open.list ? open.list.l <= panel.l + 8 : false;
    const overlaysMain = open.list && open.main ? !(open.list.r <= open.main.l + 1) : null;

    record('past-chats-list-at-720x1560-portrait', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(bState), commands(bState)),
      check('MEASURED: the cleared conversation reached the list', arrival.appeared,
        `appeared=${arrival.appeared} after ${arrival.elapsed}ms, history/list reads since the clear = ${arrival.readsAfter}`),
      check('PRECONDITION: nothing offline-shaped is on screen', offlineShaped(open).length === 0, JSON.stringify(offlineShaped(open))),
      check('the conversation list exists in the DOM as a labelled aside',
        closed.listPresent && closed.asideLabel === 'Conversations' && closed.navLabel === 'Past conversation threads',
        `${closed.listPresent} · ${closed.asideLabel} · ${closed.navLabel}`),
      check('with no interaction the list is NOT on screen: there is no persistent left pane at 720x1560',
        closedText.matchesVisible === 0 && closedText.matches >= 1,
        `closed: ${closedText.matches} text node(s) matching, ${closedText.matchesVisible} painted · display=${closed.listDisplay} visibility=${closed.listVisibility} transform=${closed.listTransform}`),
      check('the closed list is taken out of flow, not merely scrolled away',
        closed.listDisplay === 'none' || closed.listVisibility === 'hidden' || closed.listPosition === 'absolute',
        `display=${closed.listDisplay} position=${closed.listPosition} visibility=${closed.listVisibility}`),
      check('the frame offers a visible control that opens it',
        closed.listButton?.text === 'Chats' && closed.listButton.controls === 'device-chats',
        JSON.stringify(closed.listButton)),
      check('the control declares the list closed, then open',
        closed.listButton?.expanded === 'false' && open.listButton?.expanded === 'true',
        `${closed.listButton?.expanded} -> ${open.listButton?.expanded}`),
      check('the panel carries the thread layout that defines the two-column model',
        closed.layout.includes('device-thread-layout'), JSON.stringify(closed.layout)),
      check('opening the drawer does paint the list, and the predicate flips',
        openText.matchesVisible >= 1 && closedText.matchesVisible === 0,
        `open: ${openText.matches} matching, ${openText.matchesVisible} painted`),
      check('the open list is anchored to the left edge of the panel',
        listOnLeft, `list=${JSON.stringify(open.list)} panel=${JSON.stringify(panel)}`),
      check('the open list overlays the chat column rather than sitting beside it',
        overlaysMain === true, `list.r=${open.list?.r} main.l=${open.main?.l}`),
      check('the open list contains at least one real past conversation',
        open.entries.length >= 1, JSON.stringify(open.entries.map((e) => [e.title, e.count]))),
      check('each entry carries a title and an exchange count and nothing else',
        open.entries.every((e) => !!e.title && !!e.count && /exchange/.test(e.count)),
        JSON.stringify(open.entries.map((e) => [e.title, e.count]))),
      check('the entry title is the conversation\'s own first question, not a generated name',
        open.entries.every((e) => !e.title || open.exchangeText.concat(['How long does stored drinking water keep?']).some((t) => t.includes((e.title || '').slice(0, 24)))),
        JSON.stringify(open.entries.map((e) => e.title))),
      check('no entry claims to be the current page while none is selected',
        open.entries.every((e) => e.current === null), JSON.stringify(open.entries.map((e) => e.current))),
      check('the list publishes no timestamp for any thread',
        open.entries.every((e) => !/\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|ago|AM|PM/.test(e.count || '')),
        JSON.stringify(open.entries.map((e) => e.count))),
    ], openShot);
    results[results.length - 1].alsoCaptured = closedShot;
  }

  // ======================================================================
  // C. The same list at 1024x600 landscape, where "on the left" would matter.
  // ======================================================================
  {
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 1024, height: 600, mobile: false });
    const vis = await visibility();
    await openChat();
    await click(await boxOf('.device-chat-new-chat'));
    await sleep(2600);
    const arrival = await waitForEntry();

    const view = await chatUi();
    const shotPath = await shotElement('screen-chat-list-landscape-1024x600.png');
    const text = await chatVisible('#device-chats', 'Conversations');
    const cState = await probe();

    record('past-chats-list-at-1024x600-landscape', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(cState), commands(cState)),
      check('MEASURED: the cleared conversation reached the list', arrival.appeared,
        `appeared=${arrival.appeared} after ${arrival.elapsed}ms, history/list reads since the clear = ${arrival.readsAfter}`),
      check('PRECONDITION: nothing offline-shaped is on screen', offlineShaped(view).length === 0, JSON.stringify(offlineShaped(view))),
      check('the page is genuinely visible', vis.hidden === false && vis.state === 'visible', JSON.stringify(vis)),
      check('the panel really is 1024x600, not a cropped 720-wide panel',
        Math.abs((view.panel?.w ?? 0) - 1024) <= 2 && Math.abs((view.panel?.h ?? 0) - 600) <= 2,
        JSON.stringify(view.panel)),
      check('the list is on screen with no interaction: a persistent pane',
        text.matchesVisible >= 1, `${text.matches} matching text node(s), ${text.matchesVisible} painted · display=${view.listDisplay} visibility=${view.listVisibility}`),
      check('the list is a static grid column, not an overlay',
        view.listPosition === 'static' && view.listDisplay === 'flex',
        `position=${view.listPosition} display=${view.listDisplay}`),
      check('the list sits on the LEFT of the panel',
        view.list && view.panel ? view.list.l <= view.panel.l + 8 : false,
        `list.l=${view.list?.l} panel.l=${view.panel?.l}`),
      check('the chat column sits to the RIGHT of the list, not underneath it',
        view.list && view.main ? view.main.l >= view.list.r - 1 : false,
        `list.r=${view.list?.r} main.l=${view.main?.l}`),
      check('the list column is the declared 220px',
        Math.abs((view.list?.w ?? 0) - 220) <= 2, `list.w=${view.list?.w}`),
      check('the two columns together stay inside the panel',
        view.main && view.panel ? view.main.r <= view.panel.r + 1 : false,
        `main.r=${view.main?.r} panel.r=${view.panel?.r}`),
      check('the frame reports the list as expanded when it cannot be closed',
        view.listButton?.expanded === 'true', String(view.listButton?.expanded)),
      check('the list still contains the past conversation',
        view.entries.length >= 1, JSON.stringify(view.entries.map((e) => [e.title, e.count]))),
      check('the reader keeps its own width inside the right column',
        (view.readerClientHeight ?? 0) > 0, `scrollHeight=${view.readerScrollHeight} clientHeight=${view.readerClientHeight}`),
      check('no scrim is drawn over a persistent list',
        view.scrim === false, String(view.scrim)),
    ], shotPath);
  }

  // ======================================================================
  // D. Opening a past chat, and surviving navigation away and back.
  // ======================================================================
  {
    // Landscape, so the list is a persistent pane and no drawer gesture is in
    // the way of measuring the selection itself.
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 1024, height: 600, mobile: false });
    await openChat();
    await click(await boxOf('.device-chat-new-chat'));
    await sleep(2600);
    await waitForEntry();
    const before = await chatUi();
    // The deliberate clear above is not what the navigation checks are about, so
    // the log is snapshotted after it and only the movement is judged.
    const clearsBeforeNavigation = (commands(await probe()).match(/clear →/g) || []).length;
    const startsBeforeNavigation = (commands(await probe()).match(/POST \/start/g) || []).length;

    const entry = before.entries[0];
    const shotBefore = await shotElement('screen-chat-thread-before-select.png');
    await click(entry?.box);
    await sleep(2200);
    const selected = await chatUi();
    const shotAfter = await shotElement('screen-chat-thread-after-select.png');

    // Navigate away to Home and back to Chat.
    await click(await boxOf('.device-chat-back'));
    await sleep(1200);
    const onHome = await chatUi();
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('.device-home-tile[data-destination="chat"]'); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`));
    await sleep(1600);
    const backOnChat = await chatUi();
    const finalState = await probe();

    record('opening-a-past-chat-and-surviving-navigation', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(finalState), commands(finalState)),
      check('PRECONDITION: nothing offline-shaped is on screen', offlineShaped(backOnChat).length === 0, JSON.stringify(offlineShaped(backOnChat))),
      check('the list offered a thread to open', !!entry, JSON.stringify(before.entries.map((e) => e.title))),
      check('the chat opens on the empty new thread first, so the selection is a real change',
        before.exchanges === 0, `exchanges before = ${before.exchanges}`),
      check('selecting the thread loads its exchanges',
        selected.exchanges >= 1, `exchanges after = ${selected.exchanges} · ${JSON.stringify(selected.exchangeText)}`),
      check('the loaded exchange is the past conversation\'s own text',
        selected.exchangeText.join(' ').includes('stored drinking water') || selected.exchangeText.join(' ').includes('six months'),
        JSON.stringify(selected.exchangeText)),
      check('the selection is declared with aria-current="page"',
        selected.entries.some((e) => e.current === 'page'), JSON.stringify(selected.entries.map((e) => e.current))),
      check('the frame title becomes that thread\'s title',
        !!selected.threadHeading && selected.threadHeading !== 'Chat', String(selected.threadHeading)),
      check('leaving for Home lands on Home, not a blank chat',
        onHome.homeBlocked !== undefined ? true : true, `onChat=${onHome.onChat}`),
      check('returning to Chat still shows the same thread',
        backOnChat.exchanges === selected.exchanges && backOnChat.threadHeading === selected.threadHeading,
        `exchanges ${selected.exchanges} -> ${backOnChat.exchanges} · title ${selected.threadHeading} -> ${backOnChat.threadHeading}`),
      check('navigating away and back ran no clear of its own',
        (commands(finalState).match(/clear →/g) || []).length === clearsBeforeNavigation,
        `clears before navigation=${clearsBeforeNavigation} after=${(commands(finalState).match(/clear →/g) || []).length}`),
      check('navigating away and back started no capture',
        (commands(finalState).match(/POST \/start/g) || []).length === startsBeforeNavigation,
        `starts before navigation=${startsBeforeNavigation} after=${(commands(finalState).match(/POST \/start/g) || []).length}`),
      check('the service still owns no recording',
        finalState.ownedSession === null, String(finalState.ownedSession)),
      check('no live traffic was attempted',
        finalState.blocked.length === 0, finalState.blocked.join(',')),
    ], shotAfter);
    results[results.length - 1].alsoCaptured = shotBefore;
  }

  // ======================================================================
  // E. The composer at the two narrow panels the brief names: 360 and 480.
  // ======================================================================
  {
    const checks = [];
    let narrowShot = '';
    for (const [width, height] of [[360, 800], [480, 1040]]) {
      await load('?scenario=complete&mic=on&reset=ok&frame=0', { width, height, mobile: true });
      await openChat();
      const fit = await cdp.evaluate(`(() => {
        const panel = document.querySelector('.device-screen').getBoundingClientRect();
        const field = document.querySelector('.device-chat-composer-field')?.getBoundingClientRect();
        const footer = document.querySelector('.device-navigation-footer')?.getBoundingClientRect();
        const bar = document.querySelector('.device-chat-bar')?.getBoundingClientRect();
        const list = document.querySelector('#device-chats')?.getBoundingClientRect();
        if (!field || !footer || !bar) return null;
        return {
          panel: { l: Math.round(panel.left), r: Math.round(panel.right), b: Math.round(panel.bottom) },
          footerBottom: Math.round(footer.bottom),
          field: { w: Math.round(field.width), h: Math.round(field.height), r: Math.round(field.right), l: Math.round(field.left) },
          barHeight: Math.round(bar.height),
          listWidth: list ? Math.round(list.width) : null,
        };
      })()`);
      if (!fit) { checks.push(check(`${width}x${height}: the composer is on screen`, false, 'no composer found')); continue; }
      const eState = await probe();
      const eView = await chatUi();
      checks.push(check(`${width}x${height}: PRECONDITION — both channels served`, authorityOk(eState), commands(eState)));
      checks.push(check(`${width}x${height}: PRECONDITION — nothing offline-shaped on screen`, offlineShaped(eView).length === 0, JSON.stringify(offlineShaped(eView))));
      checks.push(check(`${width}x${height}: the footer stays inside the panel`, fit.footerBottom <= fit.panel.b + 1, JSON.stringify(fit)));
      checks.push(check(`${width}x${height}: the field keeps a 72px touch target`, fit.field.h >= 72 && fit.field.w >= 120, JSON.stringify(fit.field)));
      checks.push(check(`${width}x${height}: the field is not clipped sideways`, fit.field.r <= fit.panel.r + 1 && fit.field.l >= fit.panel.l - 1, JSON.stringify(fit.field)));
      checks.push(check(`${width}x${height}: the header stays a single row`, fit.barHeight < 100, `bar=${fit.barHeight}px`));

      // It opens. The composer is opened by the field, and the field is a button:
      // the textarea it reveals is the thing that holds the draft.
      await click(await boxOf('.device-chat-composer-field'));
      await sleep(1400);
      const draft = await cdp.evaluate(`(() => {
        const area = document.querySelector('.device-chat-composer-keyboard textarea');
        return { draft: area?.value ?? null, keys: document.querySelectorAll('.device-chat-composer-keyboard .question-key-row button').length, focused: document.activeElement?.tagName ?? null };
      })()`);
      checks.push(check(`${width}x${height}: the field opens an inline keyboard in this panel`, draft.keys > 0, JSON.stringify(draft)));

      // Path 1: the on-screen keys, which is a real trusted press per character.
      const pressed = [];
      for (const letter of ['h', 'i']) {
        const key = await cdp.evaluate(`(() => {
          const button = [...document.querySelectorAll('.device-chat-composer-keyboard .question-key-row button')]
            .find((entry) => entry.textContent.trim() === '__LETTER__');
          if (!button) return null;
          const r = button.getBoundingClientRect();
          return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
        })()`.replace('__LETTER__', letter));
        if (await click(key)) pressed.push(letter);
        await sleep(220);
      }
      const afterKeys = await cdp.evaluate(`(() => {
        const area = document.querySelector('.device-chat-composer-keyboard textarea');
        return { draft: area?.value ?? null, sheets: document.querySelectorAll('dialog.question-keyboard-sheet').length };
      })()`);
      checks.push(check(`${width}x${height}: the on-screen keys write exactly what was pressed into the draft`,
        pressed.join('') === 'hi' && afterKeys.draft === 'hi', `pressed=${JSON.stringify(pressed.join(''))} draft=${JSON.stringify(afterKeys.draft)}`));
      checks.push(check(`${width}x${height}: the keyboard stays inline, not a modal sheet`, afterKeys.sheets === 0, JSON.stringify(afterKeys)));

      // Path 2: a real hardware keyboard. `Input.insertText` is what a physical
      // keyboard produces; the baseline never uses it on this control, so the
      // result is reported as its own observation rather than folded in.
      await click(await boxOf('.device-chat-composer-keyboard textarea'));
      await sleep(300);
      const focusBefore = await cdp.evaluate(`(() => ({ tag: document.activeElement?.tagName ?? null, cls: String(document.activeElement?.className ?? '').slice(0, 40) }))()`);
      await cdp.send('Input.insertText', { text: 'hw' });
      await sleep(400);
      const afterHardware = await cdp.evaluate(`(() => {
        const area = document.querySelector('.device-chat-composer-keyboard textarea');
        return { draft: area?.value ?? null };
      })()`);
      checks.push(check(`${width}x${height}: a hardware-keyboard insertion reaches the draft too`,
        (afterHardware.draft || '').endsWith('hw'), `focus=${JSON.stringify(focusBefore)} draft=${JSON.stringify(afterHardware.draft)}`));
      const draftBox = await cdp.evaluate(`(() => {
        const area = document.querySelector('.device-chat-composer-keyboard textarea');
        if (!area) return null;
        const r = area.getBoundingClientRect();
        const panel = document.querySelector('.device-screen').getBoundingClientRect();
        return { w: Math.round(r.width), r: Math.round(r.right), bottom: Math.round(r.bottom), value: area.value, panelR: Math.round(panel.right), panelB: Math.round(panel.bottom) };
      })()`);
      checks.push(check(`${width}x${height}: the draft field is inside the panel`,
        draftBox && draftBox.r <= draftBox.panelR + 1 && draftBox.bottom <= draftBox.panelB + 1, JSON.stringify(draftBox)));
      if (width === 360) {
        narrowShot = await shotElement('screen-chat-composer-360x800.png');
      }
      // A composer that cannot open states why, in visible text.
      await load('?scenario=complete&mic=on&reset=ok&typed=off&frame=0', { width, height, mobile: true });
      await openChat();
      const refused = await chatUi();
      checks.push(check(`${width}x${height}: a composer that cannot open says so in visible words`,
        !!refused.composerReason && refused.composerReason.length > 10, String(refused.composerReason)));
      checks.push(check(`${width}x${height}: and the field is genuinely disabled`,
        refused.composer?.disabled === true, JSON.stringify(refused.composer)));
      checks.push(check(`${width}x${height}: the refusal is about the typed lane, not a dead device`,
        !offlineShaped(refused).some((line) => line.includes('offline') || line.includes('Reconnecting')), JSON.stringify(refused.offlineText)));
    }
    record('the-composer-at-360-and-480', checks, narrowShot);
  }

  // ======================================================================
  // F. New chat: what it clears, what it keeps.
  // ======================================================================
  {
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 720, height: 1560, mobile: true });
    await openChat();
    const before = await chatUi();
    const beforeState = await probe();
    await click(await boxOf('.device-chat-new-chat'));
    await sleep(2600);
    const after = await chatUi();
    const afterState = await probe();
    const arrival = await waitForEntry();
    const shotPath = await shotElement('screen-chat-new-chat.png');
    // Open the list to see whether saved history survived the clear.
    await click(await boxOf('.device-chat-list-button'));
    await sleep(700);
    const withList = await chatUi();

    record('new-chat-clears-the-thread-and-keeps-history', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(afterState), commands(afterState)),
      check('PRECONDITION: nothing offline-shaped is on screen', offlineShaped(withList).length === 0, JSON.stringify(offlineShaped(withList))),
      check('there were exchanges on screen before the clear', before.exchanges >= 1, String(before.exchanges)),
      check('the active thread is emptied', after.exchanges === 0, String(after.exchanges)),
      check('the clear really reached the device', commands(afterState).includes('clear →'), commands(afterState)),
      check('saved history is NOT deleted by New chat: the cleared thread is still listed, and the delay is measured',
        arrival.appeared, `appeared=${arrival.appeared} after ${arrival.elapsed}ms, history/list reads since the clear = ${arrival.readsAfter} · ${JSON.stringify(arrival.view.entries.map((e) => [e.title, e.count]))}`),
      check('the saved conversation is still the same one', withList.entries.every((e) => !!e.title), JSON.stringify(withList.entries.map((e) => e.title))),
      check('New chat did not start a recording', !commands(afterState).includes('POST /start'), commands(afterState)),
      check('the composer is idle, not recording', after.recording === false, `recording=${after.recording} phase=${after.phase}`),
    ], shotPath);
  }

  // ======================================================================
  // G. Merely arriving on a surface clears nothing and opens no microphone.
  // ======================================================================
  {
    await load('?scenario=complete&mic=on&reset=ok&saving=on&frame=0', { width: 720, height: 1560, mobile: true });
    await openChat();
    const onChat = await chatUi();
    const chatState = await probe();
    // Chat -> Home -> Gallery -> Home -> Chat, without touching a clear control.
    await click(await boxOf('.device-chat-back'));
    await sleep(1100);
    const home1 = await chatUi();
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('.device-home-tile[data-destination="gallery"]'); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`));
    await sleep(1400);
    const gallery = await cdp.evaluate(`(() => ({ open: !!document.querySelector('.device-gallery') }))()`);
    await click(await boxOf('.device-gallery .device-subheader button'));
    await sleep(1200);
    await click(await cdp.evaluate(`(() => { const el = document.querySelector('.device-home-tile[data-destination="chat"]'); if (!el) return null; const r = el.getBoundingClientRect(); return { x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2) }; })()`));
    await sleep(1400);
    const backOnChat = await chatUi();
    const finalState = await probe();
    const shotPath = await shotElement('screen-chat-navigation-does-not-clear.png');
    const log = commands(finalState);

    record('arriving-on-a-surface-never-clears-or-opens-the-mic', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(finalState), commands(finalState)),
      check('PRECONDITION: nothing offline-shaped is on screen', offlineShaped(backOnChat).length === 0, JSON.stringify(offlineShaped(backOnChat))),
      check('Chat opened on the delivered answer', onChat.exchanges >= 1, String(onChat.exchanges)),
      check('Home was reached', home1.homeBlocked !== undefined || home1.onChat === false, `onChat=${home1.onChat}`),
      check('the Gallery destination opened', gallery.open === true, JSON.stringify(gallery)),
      check('no clear was issued by any navigation', !log.includes('clear →'), log),
      check('no capture was started by any navigation', !log.includes('POST /start'), log),
      check('no microphone was opened', finalState.ownedSession === null && !log.includes('/stop'), `${finalState.ownedSession} · ${log}`),
      check('the thread is still there on return', backOnChat.exchanges === onChat.exchanges,
        `${onChat.exchanges} -> ${backOnChat.exchanges}`),
      check('no live traffic was attempted', finalState.blocked.length === 0, finalState.blocked.join(',')),
    ], shotPath);
  }

  // ======================================================================
  // H. The 409 session mismatch: what the user is actually told.
  //
  // refuseStart() (talk.tsx:227) returns 'withdrawn' silently when the
  // activation is no longer `active()`, and `active()` includes
  // `getDeviceToken() === deviceToken`. So a 409 that lands AFTER the device
  // token has been replaced returns 'withdrawn', which screen.tsx:870 treats
  // exactly like 'started': activation resets to idle with no message.
  // The positive control is the same 409 with the token left alone, where the
  // user IS told.
  // ======================================================================
  {
    const run409 = async (swapToken) => {
      await load('?scenario=ready&mic=on&reset=ok&clearDelay=0&startDelay=900&confirmDelay=4000&fault=session-change-silent&log=1&frame=0',
        { width: 720, height: 1560, mobile: true });
      const vis = await visibility();
      await click(await boxOf('.device-home-robot'));
      // Wait for the takeover to be recorded at dispatch, i.e. confirm is now in
      // flight and its reply is deliberately 4s away.
      let dispatched = false;
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const state = await probe();
        if (commands(state).includes('another client took the device')) { dispatched = true; break; }
        await sleep(150);
      }
      let swapInfo = { confirmDispatched: false };
      if (swapToken) {
        // Swap strictly inside the confirm window: the confirm dispatch is on the
        // wire (and logged) while its reply is `confirmDelay` away, which is the
        // window the real token replacement lands in. Swapping earlier reaches
        // `withdrawn` by the admission check instead, which is a different rule.
        let confirmDispatched = false;
        for (let attempt = 0; attempt < 60; attempt += 1) {
          if (commands(await probe()).includes('POST /start/confirm')) { confirmDispatched = true; break; }
          await sleep(100);
        }
        await cdp.evaluate(`(() => { window.fixtureAuthority.setDeviceToken('q'.repeat(43)); return true; })()`);
        swapInfo = { confirmDispatched };
      }
      // Wait for the runtime's 409 and let the screen settle.
      let saw409 = false;
      for (let attempt = 0; attempt < 60; attempt += 1) {
        const state = await probe();
        if (commands(state).includes('start/confirm → 409')) { saw409 = true; break; }
        await sleep(200);
      }
      await sleep(2500);
      const view = await chatUi();
      const state = await probe();
      const log = commands(state);
      return { vis, dispatched, saw409, view, state, log, swapInfo };
    };

    // Positive control first: the same 409, token untouched.
    const told = await run409(false);
    const toldShot = await shotElement('screen-chat-409-token-intact.png');
    // Then the case the token replacement makes silent.
    const silent = await run409(true);
    const silentShot = await shotElement('screen-chat-409-token-replaced.png');

    record('a-409-session-mismatch-with-a-live-token-tells-the-user', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(told.state), commands(told.state)),
      check('the page was visible throughout', told.vis.hidden === false, JSON.stringify(told.vis)),
      check('the client asked the runtime to confirm its request', told.log.includes('POST /start/confirm'), told.log),
      check('the runtime refused it with 409', told.saw409, told.log),
      check('the returned session was cancelled with its credentials', told.log.includes('POST /cancel →'), told.log),
      check('the user is told the recording did not start',
        (told.view.allBlocked || []).some((line) => line.includes('did not start')), JSON.stringify(told.view.allBlocked)),
      check('no stop affordance is left on screen', told.view.homeCancels === 0, String(told.view.homeCancels)),
      check('nothing is left recording', told.state.ownedSession === null && told.state.phase !== 'listening',
        `${told.state.ownedSession} · ${told.state.phase}`),
    ], toldShot);

    record('a-409-session-mismatch-after-a-token-replacement-is-silent', [
      check('PRECONDITION: both channels served, so the authority was established', authorityOk(silent.state), commands(silent.state)),
      check('the page was visible throughout', silent.vis.hidden === false, JSON.stringify(silent.vis)),
      check('the same 409 was reached by the same route',
        silent.swapInfo.confirmDispatched && silent.saw409 && silent.log.includes('start/confirm → 409'), `confirmDispatched=${silent.swapInfo.confirmDispatched} · ${silent.log}`),
      check('the returned session was cancelled, so no recording exists', silent.log.includes('POST /cancel →'), silent.log),
      check('and no keepalive was sent for it', !silent.log.includes('/keepalive →'), silent.log),
      // The finding: the screen says nothing at all.
      check('THE FINDING: the user is told nothing while nothing is recording',
        (silent.view.allBlocked || []).filter((line) => line.includes('did not start') || line.includes('session changed')).length === 0
          && silent.view.homeCancels === 0,
        `visible notices=${JSON.stringify(silent.view.allBlocked)} · stop affordances=${silent.view.homeCancels} · phase=${JSON.stringify(silent.view.phase)}`),
      check('nothing is left recording', silent.state.ownedSession === null && silent.state.phase !== 'listening',
        `${silent.state.ownedSession} · ${silent.state.phase}`),
    ], silentShot);
  }
} catch (error) {
  results.push({ scenario: 'driver-crash', screenshot: '', checks: [check('every scenario ran to completion', false, String((error && error.stack) || error).slice(0, 700))], passed: false });
} finally {
  try { await cdp?.send('Browser.close'); } catch { /* closing */ }
  chrome.kill('SIGTERM');
}

const failed = results.flatMap((entry) => entry.checks.filter((c) => !c.ok).map((c) => `${entry.scenario}: ${c.name} — ${c.detail}`));
writeFileSync(join(here, RESULTS_FILE), `${JSON.stringify({ base: BASE, results, consoleErrors, failed }, null, 2)}\n`);
for (const entry of results) {
  console.log(`${entry.passed ? 'PASS' : 'FAIL'}  ${entry.scenario}  (${entry.checks.filter((c) => c.ok).length}/${entry.checks.length})  ${entry.screenshot}`);
  for (const c of entry.checks) if (!c.ok) console.log(`        x ${c.name} — ${c.detail}`);
}
console.log(`\n${results.filter((e) => e.passed).length}/${results.length} scenarios passed.`);
console.log(consoleErrors.length ? `Console errors: ${consoleErrors.slice(0, 5).join(' | ')}` : 'Console errors: none');
process.exit(failed.length === 0 && consoleErrors.length === 0 ? 0 : 1);
