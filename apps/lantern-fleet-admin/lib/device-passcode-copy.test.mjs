import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const setup = readFileSync(new URL('../app/device/setup.tsx', import.meta.url), 'utf8');
const lock = readFileSync(new URL('../app/device/lock.tsx', import.meta.url), 'utf8');

test('initial setup names the passcode and requires matching second entry before enabling', () => {
  assert.ok(setup.includes('Create your passcode'));
  assert.ok(setup.includes('Confirm your passcode'));
  const check = setup.indexOf("if (phase === 'confirm' && entered !== pin.current) return 'incorrect'");
  assert.ok(check >= 0 && check < setup.indexOf("'setup/enable'"));
  assert.ok(setup.includes('key="choose"'));
  assert.ok(setup.includes('key={phase}'));
});

test('passcode reveal is setup-only, temporary, and cleared on submit or page hiding', () => {
  assert.ok(lock.includes('allowReveal = false'));
  assert.ok(setup.includes("allowReveal={phase === 'confirm'}"));
  assert.ok(lock.includes('allowReveal && revealed ? pin'));
  assert.ok(lock.includes('aria-pressed={revealed}'));
  assert.ok(lock.includes('setTimeout(() => setRevealed(false), 15000)'));
  assert.match(lock, /setPin\(''\);\s+setRevealed\(false\);/);
  assert.ok(lock.includes("window.addEventListener('pagehide', hide)"));
  assert.doesNotMatch(setup + lock, /Choose your numbers|forget your numbers/);
});
