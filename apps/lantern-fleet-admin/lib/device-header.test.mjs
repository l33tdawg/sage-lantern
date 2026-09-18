import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
const header = readFileSync(new URL('../app/device/device-header.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');

test('one shared header renders above every unlocked device screen', () => {
  assert.equal(screen.match(/<DeviceHeader\b/g)?.length, 1);
  // No screen may return its own wrapper before the shared header renders.
  assert.doesNotMatch(screen, /if \((?:correction[^)]*|page === '[a-z]+')\) return <main/);
  assert.match(screen, /<DeviceHeader busy=\{busy\}[\s\S]*?page=\{headerPage\}/);
  // Secondary screens start below the header row instead of covering it.
  assert.match(css, /\.device-subscreen \{ grid-row: 2 \/ -1;/);
  assert.match(css, /\.device-camera \{ grid-row: 2 \/ -1;/);
});

test('the header keeps home, nearby, messages and menu reachable everywhere', () => {
  assert.match(header, /import RadioSignal from '\.\/radio-signal'/);
  assert.match(header, /className="device-wordmark"/);
  assert.match(header, /className="device-lora-indicator"/);
  assert.match(header, /className="device-messages-shortcut"/);
  assert.match(header, /aria-haspopup="dialog"/);
  assert.match(header, /aria-current=\{page === 'status' \? 'page' : undefined\}/);
  assert.match(header, /aria-current=\{page === 'messages' \? 'page' : undefined\}/);
  // The current screen keeps a visible marker without greying the control out.
  assert.match(css, /\.device-messages-shortcut\[aria-current="page"\], \.device-top \.device-lora-indicator\[aria-current="page"\] \{ background: #1d3644;/);
});

test('the menu opens from any screen, not only the home screen', () => {
  const homeBranchEnd = screen.indexOf('</>}');
  assert.ok(homeBranchEnd > 0);
  assert.ok(screen.indexOf('device-menu-backdrop') > homeBranchEnd, 'menu markup must render outside the home-screen branch');
  assert.match(screen, /onMenu=\{\(\) => setMenuOpen\(true\)\}/);
});

test('the wordmark starts a new chat only on the home screen', () => {
  assert.match(screen, /if \(page === 'main' && !correction\) void clearAnswer\(\);/);
  assert.match(screen, /else goBack\(\);/);
  assert.match(header, /aria-label=\{home \? 'New chat; keep saved conversations' : 'Back to your Lantern home screen'\}/);
});
