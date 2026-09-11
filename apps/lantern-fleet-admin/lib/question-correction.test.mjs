import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { correctionTextValid, CorrectionAttempts } from './question-correction.ts';

test('correction text limit uses UTF-8 bytes and accepts meaningful whitespace', () => {
  assert.equal(correctionTextValid(' Who played Neo?\n\t'), true);
  assert.equal(correctionTextValid('é'.repeat(2048)), true);
  assert.equal(correctionTextValid('é'.repeat(2049)), false);
  for (const text of ['', ' \n\t', 'bad\u0000text', 'bad\u007ftext', '\ud800', '\udfff']) assert.equal(correctionTextValid(text), false);
  assert.equal(correctionTextValid('What is this? 👩🏽‍🔬'), true);
});

test('correction UI binds source kind, preserves photos and never automatically retries', () => {
  const source = readFileSync(new URL('../app/device/question-correction.tsx', import.meta.url), 'utf8');
  assert.match(source, /source_kind: fromHistory \? 'history' : 'current'/);
  assert.match(source, /historyPhoto && photo \? \{ image_base64: photo.image \}/);
  assert.match(source, /attempted.current = true/);
  assert.doesNotMatch(source, /localStorage|sessionStorage/);
  assert.match(source, /correct\/reconcile/);
  assert.match(source, /parseCorrectionConfirmation/);
  assert.match(source, /new_execution: \{ challenge: grant.challenge/);
  assert.match(source, /confirmation.token !== token/);
  assert.match(source, /getDeviceToken\(\) === token/);
  assert.match(source, /controller.current\?\.abort\(\)/);
});

test('editor is capability gated and history retains its question and photo', () => {
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  const history = readFileSync(new URL('../app/device/history.tsx', import.meta.url), 'utf8');
  assert.match(screen, /correction_ready === true/);
  assert.match(screen, /setConnected\(false\); setQuestionHandoff\(null\); setCorrection\(null\)/);
  assert.match(history, /onCorrect\(selected.session_id, selected.transcript, photo\)/);
});

test('lost response stays blocked across editor reentry and both source screens', () => {
  const attempts = new CorrectionAttempts();
  assert.equal(attempts.begin('source-one'), true);
  assert.equal(attempts.blocked('source-one'), true);
  assert.equal(attempts.begin('source-one'), false);
  assert.equal(attempts.begin('source-two'), true);
  attempts.confirmed('source-two');
  assert.equal(attempts.blocked('source-two'), false);
  assert.equal(attempts.blocked('source-one'), true);
  const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
  assert.match(screen, /useState\(\(\) => new CorrectionAttempts\(\)\)/);
  assert.match(screen, /attempts=\{correctionAttempts\}/);
});
