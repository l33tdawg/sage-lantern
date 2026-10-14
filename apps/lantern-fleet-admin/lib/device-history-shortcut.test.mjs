import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('Chats stays first in the footer and is disabled during voice ownership', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const footer = screen.slice(screen.indexOf('<footer'), screen.indexOf('</footer>'));
  assert.match(footer, /<button ref=\{historyButton\} className="device-history-shortcut" disabled=\{busy \|\| !connected\} onClick=\{\(\) => setShowChats\(true\)\} aria-controls="device-chats" aria-expanded=\{showChats \|\| !narrowChats\}>Chats/);
  assert.doesNotMatch(screen, />‹ Chats<\/button>/);
  assert.ok(footer.indexOf('device-history-shortcut') < footer.indexOf('device-share-answer'));
  assert.ok(footer.indexOf('device-camera-shortcut') < footer.indexOf('<TalkControls'));
  // Back restores focus from the *recorded origin* rather than from the page being
  // left: the record names the control, so the history entry keeps its own, and the
  // one journey with nothing recorded still resolves the page being left to it.
  assert.match(screen, /target === 'history' \? historyButton\.current/);
  assert.match(screen, /page === 'history' \? 'history'/);
});

test('shortcut opens the existing consent-controlled history screen', () => {
  const history = readFileSync(new URL('../app/device/history.tsx', import.meta.url), 'utf8');
  assert.match(history, /<h1>Past questions<\/h1>/);
  assert.match(history, /Anyone using this device can read saved conversations/);
  assert.doesNotMatch(history, /history\/settings/);
  assert.match(history, /Change in Menu → Settings/);
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /\.device-navigation-footer \{ grid-template-columns: minmax\(0, 1fr\) minmax\(0, 1fr\);/);
});

test('settings uses the real retention API only with non-destructive capability', () => {
  const settings = readFileSync(new URL('../app/device/settings.tsx', import.meta.url), 'utf8');
  assert.match(settings, /history.saving_off_keeps_items !== true/);
  assert.match(settings, /voiceCommand\('history\/settings', \{ enabled: !history.enabled \}\)/);
  assert.match(settings, /Existing saved conversations stay available/);
  assert.match(settings, /setHistory\(null\)/);
  assert.match(settings, /role="switch"/);
});

test('inner screens share labelled back navigation and bounded icons', () => {
  for (const page of ['settings', 'history', 'status', 'about']) {
    const source = readFileSync(new URL(`../app/device/${page}.tsx`, import.meta.url), 'utf8');
    assert.match(source, /className="device-subheader(?: device-history-header)?"/);
    assert.match(source, /<BackIcon \/>Back/);
  }
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /<SettingsIcon \/>\s*Settings/);
  const history = readFileSync(new URL('../app/device/history.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(history, /device-subscroll/);
  assert.match(history, /device-action-danger/);
  assert.match(history, /device-subheader device-history-header/);
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /\.device-subheader svg \{ width: 28px; height: 28px;/);
  assert.match(css, /\.device-subscreen \.device-history-header \{ display: grid; grid-template-columns: max-content minmax\(0, 1fr\); column-gap: 28px;/);
});

test('opening the sliding menu cannot scroll its background into view', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /if \(menuOpen\) menuClose.current\?\.focus\(\{ preventScroll: true \}\)/);
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /\.device-menu-backdrop \{[^}]*overflow: clip;/);
  assert.match(screen, /inert=\{!menuOpen\} aria-hidden=\{!menuOpen\}/);
  assert.doesNotMatch(screen, /menuOpen && <div className="device-menu-backdrop"/);
  assert.match(css, /transform: translateY\(-100%\); transition: transform .18s ease-out;/);
  assert.match(css, /\.device-menu-open \.device-menu \{ transform: translateY\(0\);/);
  assert.doesNotMatch(css, /device-slide-in/);
});

test('wireless is distinct from LoRa and Bluetooth controls are not falsely offered', () => {
  const status = readFileSync(new URL('../app/device/status.tsx', import.meta.url), 'utf8');
  assert.match(status, /\['Device', 'SAGE', 'LoRa', 'Wireless'\]/);
  const settings = readFileSync(new URL('../app/device/settings.tsx', import.meta.url), 'utf8');
  assert.match(settings, /<h2>Bluetooth audio<\/h2>/);
  assert.match(settings, /Pairing and audio output controls are not available in Lantern yet/);
});
