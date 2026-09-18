import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../app/device/radio-signal.tsx', import.meta.url), 'utf8');

test('meter shows measured bars and marks an unmeasured radio instead of empty bars', () => {
  assert.match(source, /const measured = current && current\.heardAt > 0 && current\.snr !== null && now - current\.heardAt < RECENT_WINDOW_MS/);
  assert.match(source, /const bars = measured \? measured\.bars : 0;/);
  // Crossed outline bars and dash mean unmeasured, never poor reception.
  assert.match(source, /\{!measured && !unavailable && <line x1="2" y1="25" x2="26" y2="7"/);
  // A fresh measurement is solid; a held one is dimmed; unmeasured keeps the crossed state.
  assert.match(source, /opacity=\{!measured \? 0\.6 : fresh \? 1 : 0\.55\}/);
  assert.match(source, /const FRESH_WINDOW_MS = 120000;/);
  assert.match(source, /const RECENT_WINDOW_MS = 900000;/);
  assert.match(source, /`Radio link \$\{measured\.snr\} dB SNR/);
  assert.match(source, /'Radio connected; no recent packet signal measured'/);
  assert.match(source, /'Radio unavailable; radio chip not detected or not ready'/);
});

test('meter marks a not-ready radio chip with a small x over the meter', () => {
  // serial_identity_verified false means no verified radio chip, not a weak link.
  assert.match(source, /const unavailable = !!current && !current\.connected;/);
  // The crossed "unmeasured" line belongs to a connected radio only.
  assert.match(source, /\{!measured && !unavailable && <line x1="2" y1="25" x2="26" y2="7"/);
  // The small x replaces the status dot while the chip is not detected or ready.
  assert.match(source, /\{unavailable \? <g stroke="#d1ae65" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><line x1="25\.6" y1="0\.6" x2="30\.4" y2="5\.4" \/><line x1="30\.4" y1="0\.6" x2="25\.6" y2="5\.4" \/><\/g> : <circle cx="28" cy="3" r="2"/);
  // The x never promises a signal level, and no sample still means "checking".
  assert.match(source, /const bars = measured \? measured\.bars : 0;/);
  assert.match(source, /!current \? 'Checking radio'/);
});

test('meter never invents a signal level without a sample', () => {
  assert.match(source, /requestRadioStatus\(controller\.signal\)/);
  assert.doesNotMatch(source, /Math\.random|defaultBars|assumed/);
});
