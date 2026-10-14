import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { WAIT_PHRASE_INTERVAL_MS, workingMessage, workingMessages } from './answer-wait.ts';

test('progress is friendly and an absent or unknown stage makes no specific claim', () => {
  assert.equal(workingMessage('recalling'), 'Checking on that…');
  assert.equal(workingMessage('generating'), 'Getting things together…');
  for (const stage of [undefined, null, '', 'pretend_progress', 'toString', '__proto__']) {
    assert.equal(workingMessage(stage), 'Working on that…');
    assert.deepEqual(workingMessages(stage), workingMessages());
  }
  assert.equal(WAIT_PHRASE_INTERVAL_MS, 3000);
});

test('rotating activity copy has no elapsed counter, keeps stage announcements and honors reduced motion', () => {
  const component = readFileSync(new URL('../app/device/answer-wait.tsx', import.meta.url), 'utf8');
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const css = readFileSync(new URL('../app/device/screen.css', import.meta.url), 'utf8');
  assert.match(component, /role="status" aria-live="polite"/);
  assert.doesNotMatch(component, /performance\.now|role="timer"|elapsed|Waiting ·/);
  assert.doesNotMatch(component, /aria-valuenow|progressbar/);
  for (const stage of ['starting', 'transcribing', 'recalling', 'photo_analysis', 'generating', 'answering', 'speaking', null]) {
    assert.doesNotMatch(workingMessages(stage).join(' '), /%/);
  }
  assert.match(component, /return \(\) => clearInterval\(timer\)/);
  assert.match(component, /device-wait-copy" aria-hidden="true"/);
  assert.match(component, /aria-atomic="true">\{messages\[0\]\}/);
  assert.match(screen, /view\.phase === 'processing' && <AnswerWait key=\{status\?\.voice\.session_id/);
  assert.match(css, /prefers-reduced-motion: reduce\) \{ \.device-wait-breath, \.device-wait-phrase \{ animation: none/);
});

test('processing status sits above persistent controls while errors stay visible', () => {
  const controls = readFileSync(new URL('../app/device/talk.tsx', import.meta.url), 'utf8');
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /<AnswerWait[^>]*stage=\{status\?\.voice\.progress_stage\}/);
  assert.ok(screen.indexOf('<AnswerWait') < screen.indexOf('<footer'));
  assert.doesNotMatch(controls, /AnswerWait/);
  assert.doesNotMatch(controls, /view\.title|Answering…/);
  assert.match(controls, /className="device-talk-status" role="status">\{notice\}/);
  assert.match(controls, /className="device-talk-primary" disabled=\{!canStart \|\| pending \|\| owned\}/);
});

test('waiting transcript uses cleared state and stays inside the unlocked screen boundary', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const access = readFileSync(new URL('../app/device/access.tsx', import.meta.url), 'utf8');
  assert.match(screen, /const question = waitingQuestion\(visibleStatus, connected\)/);
  assert.match(screen, /chatStatus.voice.session_id === clearedSession.*transcript: ''/);
  assert.match(screen, /<QuestionTurn[^]*?question=\{currentQuestion\}/);
  assert.match(access, /else if \(unlocked && profile\) content = <DeviceScreen/);
  assert.match(access, /setDeviceToken\(null\); setUnlocked\(false\)/);
});
