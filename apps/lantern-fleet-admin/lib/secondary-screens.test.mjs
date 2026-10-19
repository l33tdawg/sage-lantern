/**
 * Heartbeat and Hardware: the two secondary screens whose 2c acceptance was held.
 *
 * Rook's 3 October review (`work/rook-heartbeat-hardware-review-20261003/REVIEW.md`)
 * found four things in these two screens. Two are component behaviour and are
 * pinned here; the other two are layout and are pinned in the browser fixture.
 *
 *   1. Exposure that goes away must take the held interval with it. The field used
 *      to keep rendering the last edited value while the state card said
 *      Unavailable — a number attributed to a device that had stopped answering.
 *   2. Hardware's overview counted non-null measurements while the detail said
 *      Not reported. A finite negative uptime or storage is refused by the detail
 *      formatter, so the count now asks the formatters themselves.
 *
 * The tests mount the **live** sources with the real `parseDeviceStatus`, so the
 * readings below are validated device payloads rather than hand-built objects.
 * They also guard the properties the correction must not break: a reading that
 * arrives still never overwrites an edit, zero is a reading, a below-zero
 * temperature is a reading, half-known storage keeps its wording, and every
 * published string is retained.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import ts from 'typescript';
import { parseDeviceStatus } from './device-state.ts';

const require = createRequire(import.meta.url);
const runtime = require('react/jsx-runtime');
const nulled = new Proxy({}, { get: () => () => null });

// The screens move focus on mount through the ref the shim renders without a DOM.
const realAnimationFrame = globalThis.requestAnimationFrame;
globalThis.requestAnimationFrame = () => 0;
test.after(() => { globalThis.requestAnimationFrame = realAnimationFrame; });

/** Compile one device module to CommonJS, resolving imports through `load`. */
function compile(file, load) {
  const source = readFileSync(new URL(`../app/device/${file}`, import.meta.url), 'utf8');
  const js = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2022 },
  }).outputText;
  const cjs = { exports: {} };
  new Function('require', 'module', 'exports', js)(load, cjs, cjs.exports);
  return cjs.exports.default;
}

/** The flattened text of a compiled JSX child list. */
const textOf = (node) => {
  if (node === null || node === undefined || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textOf).join('');
  return textOf(node.props?.children);
};

function mountWith(file, initial = {}) {
  const slots = [];
  const effects = [];
  const props = { ...initial };
  let cursor = 0;
  let tree;
  const hooks = { ...React,
    useState(value) {
      const index = cursor++;
      slots[index] ??= { value: typeof value === 'function' ? value() : value };
      return [slots[index].value, (next) => { slots[index].value = typeof next === 'function' ? next(slots[index].value) : next; }];
    },
    useRef(value) { const index = cursor++; slots[index] ??= { current: value }; return slots[index]; },
    useCallback(callback) { return callback; },
    useEffect(callback, dependencies) { schedule(callback, dependencies); },
    useLayoutEffect(callback, dependencies) { schedule(callback, dependencies); },
  };
  function schedule(callback, dependencies) {
    const index = cursor++;
    const previous = slots[index];
    const changed = !previous || !dependencies
      || dependencies.some((value, offset) => value !== previous.dependencies[offset]);
    if (!changed) return;
    previous?.cleanup?.();
    const slot = previous ?? {};
    slot.dependencies = dependencies;
    slot.effect = callback;
    slots[index] = slot;
    effects.push(() => { slot.cleanup = callback(); });
  }
  const Screen = compile(file, (name) => name === 'react' ? hooks
    : name === 'react/jsx-runtime' ? runtime
      : name === './icons' ? nulled
        : name.endsWith('.css') ? {}
          : require(name));
  const expand = (node) => {
    if (!node || typeof node !== 'object') return node;
    if (Array.isArray(node)) return node.map(expand);
    if (typeof node.type === 'function') return expand(node.type(node.props));
    const children = node.props?.children;
    if (children === undefined || children === null) return node;
    return { ...node, props: { ...node.props, children: expand(children) } };
  };
  const render = () => {
    cursor = 0;
    tree = expand(Screen(props));
    while (effects.length) effects.shift()();
    return tree;
  };
  const nodes = () => {
    const found = [];
    const walk = (node) => {
      if (!node || typeof node !== 'object') return;
      if (Array.isArray(node)) { node.forEach(walk); return; }
      found.push(node);
      walk(node.props?.children);
    };
    walk(tree);
    return found;
  };
  const button = (pattern) => nodes().find((node) => node.type === 'button' && pattern.test(textOf(node.props.children)));
  const press = (pattern) => {
    const target = button(pattern);
    assert.ok(target, `a button matching ${pattern} is on screen`);
    assert.equal(!!target.props.disabled, false, `the button matching ${pattern} is enabled`);
    target.props.onClick();
    render();
  };
  const field = (id) => nodes().find((node) => node.props?.id === id);
  /** The flattened text of every `.device-panel` on screen, in order. */
  const panels = () => nodes().filter((node) => String(node.props?.className ?? '').includes('device-panel')).map((node) => textOf(node.props.children));
  render();
  return {
    render,
    props,
    setProps(next) { Object.assign(props, next); render(); },
    markup: () => renderToStaticMarkup(tree),
    nodes,
    press,
    button,
    field,
    panels,
  };
}

const mountHeartbeat = (props = {}) => mountWith('heartbeat.tsx', { close() {}, ...props });
// Connected by default: every case below is about what the device reported, and a
// screen whose transport has dropped qualifies all of it. Cases that are about the
// drop itself pass `connected: false` and expect the qualifier.
const mountHardware = (props = {}) => mountWith('hardware.tsx', { close() {}, connected: true, ...props });

/** A validated device payload. Every reading below goes through the real parser. */
const rawStatus = (overrides = {}) => ({
  schema: 'sage.lantern.local-status.v1',
  observed_at: '2026-10-03T01:00:00.000Z',
  local_voice_ready: false,
  audio: { present: true, detail: 'usb microphone', ready: true, ptt_ready: false, capture_verified: true },
  radio: { present: true, detail: 'sx1262', name: 'SX1262', frequency_mhz: 868, firmware: '1.2.3' },
  voice: { phase: 'ready', transcript: '', response: '' },
  services: { 'sage-lantern-voice': 'active' },
  ...overrides,
});
const status = (overrides) => parseDeviceStatus(rawStatus(overrides));

test('an unknown interval stays empty, and unavailable is never Off', () => {
  const m = mountHeartbeat();
  const text = m.markup();
  assert.match(text, /Unavailable/);
  assert.doesNotMatch(text, />Off</);
  const interval = m.field('heartbeat-interval');
  assert.equal(interval.props.value, '', 'the field is empty rather than borrowing a default');
  assert.equal(interval.props.disabled, true);
  assert.equal(interval.props.placeholder, 'Unknown');
  assert.equal(m.button(/^Unknown$/).props.disabled, true, 'there is no switch to turn off');
  assert.equal(m.button(/^Save$/).props.disabled, true);
  assert.match(text, /Heartbeat is not available on this device yet\./);
  m.unmount?.();
});

test('a supplied reading seeds the controls, and Save never claims a write', () => {
  const m = mountHeartbeat({ binding: { exposed: true, active: true, intervalMinutes: 15 } });
  assert.match(m.markup(), /A labelled fixture supplied this reading/);
  assert.equal(m.field('heartbeat-interval').props.value, '15');
  assert.equal(m.field('heartbeat-interval').props.disabled, false);
  const toggle = m.button(/^On$/);
  assert.equal(toggle.props['aria-checked'], true);
  m.press(/^Save$/);
  assert.match(m.markup(), /This is a synthetic fixture\. No device setting was written\./);
  assert.doesNotMatch(m.markup(), /saved|Saved/);
  m.unmount?.();
});

test('exposure loss empties the interval instead of keeping the edited value', () => {
  const m = mountHeartbeat({ binding: { exposed: true, active: true, intervalMinutes: 15 } });
  m.field('heartbeat-interval').props.onChange({ target: { value: '5' } });
  m.render();
  assert.equal(m.field('heartbeat-interval').props.value, '5');
  m.setProps({ binding: null });
  assert.match(m.markup(), /Unavailable/, 'the state card reports the loss');
  assert.equal(m.field('heartbeat-interval').props.value, '', 'and the field does not keep the edited 5');
  assert.equal(m.field('heartbeat-interval').props.disabled, true);
  m.unmount?.();
});

test('a reading that arrives still never overwrites an edit', () => {
  const m = mountHeartbeat({ binding: { exposed: true, active: false, intervalMinutes: 15 } });
  m.field('heartbeat-interval').props.onChange({ target: { value: '7' } });
  m.render();
  // A caller handing over a fresh object of the same shape must not re-seed it.
  m.setProps({ binding: { exposed: true, active: false, intervalMinutes: 15 } });
  assert.equal(m.field('heartbeat-interval').props.value, '7');
  m.unmount?.();
});

test('the overview counts only the measurements the detail can state', () => {
  const m = mountHardware({ status: status({ uptime_seconds: -60, storage_free_bytes: -1, storage_total_bytes: -1, temperature_millicelsius: 25000 }) });
  const overview = textOf(m.button(/^Measurements:/)?.props.children ?? '');
  assert.match(overview, /1 of 3 reported/);
  m.press(/^Measurements:/);
  const detail = m.panels().at(-1) ?? '';
  assert.match(detail, /Uptime: Not reported/);
  assert.match(detail, /Storage: Not reported/);
  assert.match(detail, /Temperature: 25\.0 °C/);
  m.unmount?.();
});

test('zero and half-known measurements are still reported', () => {
  const zero = mountHardware({ status: status({ uptime_seconds: 0, storage_free_bytes: 0, storage_total_bytes: 0, temperature_millicelsius: 0 }) });
  assert.match(textOf(zero.button(/^Measurements:/)?.props.children ?? ''), /3 of 3 reported/);
  zero.press(/^Measurements:/);
  const detail = zero.panels().at(-1) ?? '';
  assert.match(detail, /Uptime: Less than a minute/);
  assert.match(detail, /Temperature: 0\.0 °C/);
  assert.match(detail, /Storage: 0 B free of 0 B/);
  zero.unmount?.();

  const half = mountHardware({ status: status({ storage_free_bytes: 1024 }) });
  assert.match(textOf(half.button(/^Measurements:/)?.props.children ?? ''), /1 of 3 reported/);
  half.press(/^Measurements:/);
  assert.match(half.panels().at(-1) ?? '', /Storage: 1 KiB free; total not reported/);
  half.unmount?.();
});

test('a below-zero temperature is a reading, not a refusal', () => {
  const m = mountHardware({ status: status({ temperature_millicelsius: -5000 }) });
  assert.match(textOf(m.button(/^Measurements:/)?.props.children ?? ''), /1 of 3 reported/);
  m.press(/^Measurements:/);
  assert.match(m.panels().at(-1) ?? '', /Temperature: -5\.0 °C/);
  m.unmount?.();
});

test('published hardware strings are kept exactly as the device sent them', () => {
  const radioName = 'S'.repeat(250);
  const serviceName = 'svc-' + 'n'.repeat(300);
  const serviceValue = 'v'.repeat(400);
  const m = mountHardware({ status: status({ radio: { present: true, detail: 'sx1262', name: radioName }, services: { [serviceName]: serviceValue } }) });
  // The overview summarises every item, so the radio name reaches the screen there.
  assert.ok(m.markup().includes(radioName), 'the 250-character radio name is present in full');
  m.press(/^Radio:/);
  assert.ok((m.panels().at(-1) ?? '').includes(radioName), 'and again in its own detail');
  m.press(/^Services:/);
  const services = m.panels().at(-1) ?? '';
  assert.ok(services.includes(serviceName), 'the 300-character service name is present in full');
  assert.ok(services.includes(serviceValue), 'the 400-character service value is present in full');
  m.unmount?.();
});

test('Hardware with no reading reports every item unknown', () => {
  const m = mountHardware({ status: null });
  assert.match(m.markup(), /Hardware controls are not available on this device\./);
  assert.match(m.markup(), /The device has not reported yet/);
  assert.doesNotMatch(m.markup(), /RK3588|GPIO|BCM/);
  m.unmount?.();
});

test('a reading that outlives its transport is stated as last reported, not as current', () => {
  // The poll keeps the last reading so a reconnect does not have to re-report, so
  // `status` alone cannot say whether anything is still reporting. Without the
  // connection the same values were offered as what the device says now.
  const stale = mountHardware({ status: status(), connected: false });
  assert.match(stale.markup(), /The device is not reporting\./);
  assert.match(stale.markup(), /the last values it sent, and they may be out of date/);
  // The qualifier travels with the readings rather than sitting only in a notice
  // that scrolls away from them.
  assert.match(stale.markup(), /Overview · last reported/);
  assert.match(stale.markup(), /Selected item · last reported/);
  // And it is a qualifier, not a blanking: what was reported is still readable.
  assert.match(stale.markup(), /Microphone: Present, audio ready/);
  stale.unmount?.();

  // A screen with a live transport claims nothing of the sort, and a device that
  // never reported has no last reading to qualify.
  const live = mountHardware({ status: status(), connected: true });
  assert.doesNotMatch(live.markup(), /last reported|not reporting/);
  live.unmount?.();
  const silent = mountHardware({ status: null, connected: false });
  assert.doesNotMatch(silent.markup(), /last reported/);
  assert.match(silent.markup(), /The device has not reported yet/);
  silent.unmount?.();
});
