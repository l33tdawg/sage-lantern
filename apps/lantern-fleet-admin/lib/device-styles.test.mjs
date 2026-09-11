import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

test('touchscreen hides the cursor without changing the external admin interface', () => {
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /\.device-screen, \.device-screen \*\s*\{\s*cursor: none;\s*\}/);
  assert.match(css, /\.device-screen button:disabled\s*\{[^}]*cursor: none/);
});

test('device hover cannot override primary button colors', () => {
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  const hover = css.match(/\.device-screen button:hover\s*\{([^}]*)\}/)?.[1];
  assert.ok(hover);
  assert.doesNotMatch(hover, /background|color|filter|opacity/);
  assert.match(css, /button:active:not\(:disabled\)/);
  assert.match(css, /button:focus-visible/);
});

test('main answer toolbar uses finger scrolling and an accessible icon-only camera', () => {
  const source = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /aria-label="Scroll (up|down)"/);
  assert.match(source, /aria-label="Open camera"><CameraIcon \/><\/button>/);
  assert.doesNotMatch(source, /className="device-question-button"|My question|showQuestion/);
  assert.match(source, /<QuestionTurn/);
  const turn = readFileSync(new URL('../app/device/question-turn.tsx', import.meta.url), 'utf8');
  assert.match(turn, /<QuestionContext[^>]*label="Your question"/);
  assert.match(turn, /<h2>Lantern<\/h2>/);
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /overflow-y: auto/);
});
