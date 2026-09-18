import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { photoForSession } from './photo-preview.ts';

test('preview is associated with exactly one voice session', () => {
  const photo = { sessionId: 'photo-question', image: 'small-inference-image', preview: 'sharp-display-image' };
  assert.equal(photoForSession(photo, 'photo-question'), photo);
  assert.equal(photoForSession(photo, 'other-question'), undefined);
  assert.equal(photoForSession(photo, undefined), undefined);
  assert.equal(photoForSession(undefined, 'photo-question'), undefined);
});

test('question and answer share a preview with discard and clear controls', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const header = readFileSync(new URL('../app/device/device-header.tsx', import.meta.url), 'utf8');
  assert.match(screen, /alt="Photo attached to this question"/);
  assert.match(screen, /cancelLabel=\{displayedPhoto \? 'Back' : 'Cancel'\}/);
  assert.match(screen, /onCancelled=\{\(\) => \{ setPhoto\(undefined\); setPhotoStart\(undefined\); setSessionPhoto\(undefined\)/);
  assert.match(header, /New chat; keep saved conversations/);
});

test('photo attachment explicitly requests recording through the existing voice owner', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /onAttach=\{\(image\) => \{ setPhoto\(image\); setPhotoStart\(crypto.randomUUID\(\)\)/);
  assert.match(screen, /autoStart=\{photoStart\} onAutoStartHandled=\{\(\) => setPhotoStart\(undefined\)\}/);
  const talk = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
  assert.match(talk, /image_base64: photo.image/);
  assert.match(talk, /if \(canStart && !document.hidden\) void start\(\)/);
});

test('history retry retains its photo and starts the existing recording owner without a chooser', () => {
  const history = readFileSync(new URL('../app/device/history.tsx', import.meta.url), 'utf8');
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(history, /selected.has_photo \? await prepareHistoryPhoto/);
  assert.match(history, /session_id: selected.session_id/);
  assert.match(history, /onClick=\{\(\) => void askAgain\(\)\}/);
  assert.doesNotMatch(history, /Remove photo|Keep photo|setRetrying/);
  assert.match(history, /device-actions device-history-actions/);
  assert.ok(history.indexOf('device-history-actions') > history.indexOf('device-history-answer'));
  assert.match(screen, /canAsk=\{canAskAgain\} onAskAgain=\{askAgain\}/);
  assert.match(screen, /setPhoto\(attachment\); setSessionPhoto\(undefined\); setPhotoStart\(crypto.randomUUID\(\)\)/);
});

test('sharp display preview is separate from the bounded inference upload', () => {
  const camera = readFileSync(new URL('../app/device/camera.tsx', import.meta.url), 'utf8');
  assert.match(camera, /256 \/ source.width, 192 \/ source.height/);
  assert.match(camera, /originalFrame.current = new Uint8Array/);
  assert.match(camera, /preview = btoa\(binary\)/);
  assert.match(camera, /onAttach\(\{ image: data, preview \}\)/);
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /displayedPhoto.preview/);
  const talk = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
  assert.match(talk, /original_base64: photo.preview/);
  assert.match(talk, /history\?\.photo_saving_supported === true && history.enabled && history.photos_enabled === true/);
});

test('photo start is consumed once and cancelled on unmount, not retried by polling', () => {
  const talk = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
  assert.match(talk, /consumedStart.current === autoStart/);
  assert.match(talk, /consumedStart.current = autoStart;\s+startPhotoQuestion\(\)/);
  assert.match(talk, /return \(\) => cancelAnimationFrame\(frame\);\s+\}, \[autoStart\]\)/);
  assert.match(talk, /onAutoStartHandled\?\.\(\);/);
});

test('camera controls do not include a cluttered side notice', () => {
  const camera = readFileSync(new URL('../app/device/camera.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(camera, /className="device-camera-notice"/);
  assert.match(camera, /aria-label="Ask about photo: start microphone"/);
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(screen, /className="device-saving-notice"/);
});

test('photo confirmation keeps Ask visible without claiming unavailable vision works', () => {
  const camera = readFileSync(new URL('../app/device/camera.tsx', import.meta.url), 'utf8');
  assert.doesNotMatch(camera, /phase === 'photo' && photoReady &&/);
  assert.match(camera, /disabled=\{!photoReady \|\| !onAttach\}/);
  assert.match(camera, /id="photo-answer-unavailable" role="status"/);
  assert.match(camera, /phase !== 'photo' \|\| !photoReady \|\| !onAttach/);
});

test('attached photo is vertically centered with bounded overflow', () => {
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(css, /\.device-photo-preview \{[^}]*align-self: center;[^}]*max-height: 100%; overflow-y: auto;/);
});
