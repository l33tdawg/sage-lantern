/**
 * Focus travels with the surface that replaced the control the user activated.
 *
 * Three low-severity findings from the 4 October walk
 * (`work/ember-ui-walk-20261004/findings/taskform-settings.md` W4 and
 * `gallery-agent.md` G9) are all one rule: when a surface replaces the panel that
 * held the focused control, focus must move into the new surface and come back to
 * the control that opened it.
 *
 * The behavioural evidence is the browser probe in
 * `work/ember-ui-focus-20261004/probe/`: it mounts these live sources, clicks with
 * real trusted input and reads `document.activeElement`, and its two G9 checks go
 * red when the gallery effect is removed. The lib suite has no DOM, so what is
 * pinned here is the *wiring* those probes exercise — that the refs exist, that
 * the opener records itself, and that no later edit returns focus to the element
 * the same write is about to unmount.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const source = (file) => readFileSync(new URL(`../app/device/${file}`, import.meta.url), 'utf8');

test('the name screen takes focus on entry, with Back when it has one', () => {
  const name = source('name.tsx');
  // Setup renders this screen with no `close`, so Back is absent and the first
  // control is a suggestion — the fallback is what keeps that case from landing
  // on the document body.
  assert.match(name, /\{close && <button ref=\{back\}/);
  assert.match(name, /ref=\{index === 0 \? firstChoice : undefined\}/);
  assert.match(name, /useEffect\(\(\) => \{ \(back\.current \?\? firstChoice\.current\)\?\.focus\(\); \}, \[\]\);/);
});

test('Settings returns focus to the button the rename surface replaced', () => {
  const settings = source('settings.tsx');
  // The control that opens it is unmounted by the switch to the name screen, so
  // which control opened it has to be remembered, exactly as the saving
  // confirmation remembers its switch.
  assert.match(settings, /renameOpened\.current = true; setRenaming\(true\)/);
  assert.match(settings, /ref=\{nameSwitch\}/);
  assert.match(settings, /if \(renaming \|\| !renameOpened\.current\) return;\s*renameOpened\.current = false;\s*nameSwitch\.current\?\.focus\(\);/);
  // A first mount is not a return: acting on one would take focus off Back, which
  // the mount effect has just placed there.
  assert.doesNotMatch(settings, /if \(!renaming\) nameSwitch\.current\?\.focus\(\)/);
});

test('the removal confirmation takes focus, and gives it back to Remove…', () => {
  const gallery = source('gallery.tsx');
  assert.match(gallery, /onClick=\{\(\) => \{ confirmOpened\.current = true; setConfirming\(true\); \}\}/);
  assert.match(gallery, /if \(confirming\) \{ confirmCancel\.current\?\.focus\(\{ preventScroll: true \}\); return; \}/);
  assert.match(gallery, /\(removeButton\.current \?\? back\.current\)\?\.focus\(\{ preventScroll: true \}\);/);
});

test('a removal does not return focus to the record it is about to unmount', () => {
  const gallery = source('gallery.tsx');
  // The record's thumbnail is still in the grid when the viewer closes — the
  // refresh that drops it has not landed yet — so returning focus there puts it on
  // an element the next render removes, and focus falls to the document body.
  assert.match(gallery, /closeViewer\(true\);/);
  assert.match(gallery, /const closeViewer = \(removed = false\) => \{/);
  assert.match(gallery, /const target = removed \? back\.current : \(returning && thumbRefs\.current\.get\(returning\)\) \|\| back\.current;/);
  // The ordinary close still goes back to the photo it was opened from.
  assert.match(gallery, /if \(selectedId\) \{ closeViewer\(\); return; \}/);
});
