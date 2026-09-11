import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = readFileSync(new URL('../app/device/radio-signal.tsx', import.meta.url), 'utf8');

test('meter shows measured bars and marks an unmeasured radio instead of empty bars', () => {
  assert.match(source, /const measured = current && current\.heardAt > 0 && current\.snr !== null && now - current\.heardAt < RECENT_WINDOW_MS/);
  assert.match(source, /const bars = measured \? measured\.bars : 0;/);
  // Crossed outline bars and dash mean unmeasured, never poor reception.
  assert.match(source, /\{!measured && <line x1="2" y1="25" x2="26" y2="7"/);
  // A fresh measurement is solid; a held one is dimmed; unmeasured keeps the crossed state.
  assert.match(source, /opacity=\{!measured \? 0\.6 : fresh \? 1 : 0\.55\}/);
  assert.match(source, /const FRESH_WINDOW_MS = 120000;/);
  assert.match(source, /const RECENT_WINDOW_MS = 900000;/);
  assert.match(source, /`Radio link \$\{measured\.snr\} dB SNR/);
  assert.match(source, /'Radio connected; no recent packet signal measured'/);
  assert.match(source, /'Radio unavailable'/);
});

test('meter never invents a signal level without a sample', () => {
  assert.match(source, /requestRadioStatus\(controller\.signal\)/);
  assert.doesNotMatch(source, /Math\.random|defaultBars|assumed/);
});
