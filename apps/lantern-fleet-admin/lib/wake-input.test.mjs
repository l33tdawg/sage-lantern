import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { WakeInputGuard, WAKE_QUIET_MS } from './wake-input.ts';

test('ordinary keypad taps pass, wake pointer and compatibility click are swallowed', () => {
  const guard = new WakeInputGuard();
  assert.equal(guard.consume('click', 0, false), false);
  assert.equal(guard.consume('pointerdown', 10, true, 1), true);
  assert.equal(guard.consume('pointerup', 100, false, 1), true);
  assert.equal(guard.consume('click', 400, false), true);
  assert.equal(guard.release(400 + WAKE_QUIET_MS - 1), false);
  assert.equal(guard.release(400 + WAKE_QUIET_MS), true);
  assert.equal(guard.consume('click', 2000, false), false);
});

test('rapid repeated taps extend the quiet period rather than becoming digits', () => {
  const guard = new WakeInputGuard();
  for (let timestamp = 0; timestamp < 5000; timestamp += 100) {
    assert.equal(guard.consume('click', timestamp, true), true);
    assert.equal(guard.release(timestamp + 99), false);
  }
  assert.equal(guard.release(4900 + WAKE_QUIET_MS), true);
});

test('held fingers, multiple pointers and held keys cannot outlast the guard', () => {
  const guard = new WakeInputGuard();
  guard.consume('pointerdown', 0, true, 1);
  guard.consume('pointerdown', 20, false, 2);
  assert.equal(guard.release(10000), false);
  guard.consume('pointerup', 10000, false, 1);
  assert.equal(guard.release(12000), false);
  guard.consume('pointercancel', 12000, false, 2);
  guard.consume('keydown', 12010, false, 0, 'Enter');
  assert.equal(guard.release(14000), false);
  guard.consume('keyup', 14000, false, 0, 'Enter');
  assert.equal(guard.release(14000 + WAKE_QUIET_MS), true);
});

test('lost focus clears held contacts but still requires a fresh quiet period', () => {
  const guard = new WakeInputGuard();
  guard.consume('pointerdown', 0, true, 1);
  guard.consume('blur', 100, false);
  assert.equal(guard.release(100), false);
  assert.equal(guard.release(100 + WAKE_QUIET_MS), true);
});

test('access boundary intercepts wake before keypad and does not unlock through guard', () => {
  const source = readFileSync(new URL('../app/device/access.tsx', import.meta.url), 'utf8');
  assert.match(source, /event\.stopImmediatePropagation\(\)/);
  assert.match(source, /capture: true, passive: false/);
  assert.match(source, /else if \(waking\)/);
  assert.match(source, /Waking…/);
  assert.match(source, /WAKE_IDLE_MS/);
  assert.ok(source.indexOf('else if (waking)') < source.indexOf('else content = <LockScreen'));
});

test('wake and unlock progress use large centered touchscreen text', () => {
  const css = readFileSync(new URL('../app/device/lock.css', import.meta.url), 'utf8');
  const source = readFileSync(new URL('../app/device/lock.tsx', import.meta.url), 'utf8');
  assert.match(css, /\.device-lock-wait h1\s*\{[^}]*font-size:\s*clamp\(36px, 6vw, 52px\)/);
  assert.match(source, /if \(busy\) return <main className="device-screen device-lock-wait" aria-busy="true">/);
  assert.match(source, /<h1 role="status">\{submitLabel === 'Unlock' \? 'Unlocking…' : 'Please wait…'\}/);
});
