import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const screen = readFileSync(new URL('../app/device/screen.tsx', import.meta.url), 'utf8');
const css = readFileSync(new URL('../app/device/community.css', import.meta.url), 'utf8');

test('footer orders history, teal share, camera, and ask with no oversized row', () => {
  const footer = screen.slice(screen.indexOf('<footer'), screen.indexOf('</footer>'));
  const actions = ['device-history-shortcut', 'device-share-answer', 'device-camera-shortcut', '<TalkControls'];
  for (let index = 0; index < actions.length - 1; index++) {
    assert.ok(footer.indexOf(actions[index]) >= 0);
    assert.ok(footer.indexOf(actions[index]) < footer.indexOf(actions[index + 1]));
  }
  assert.match(footer, /<ShareIcon \/>Share<\/button>/);
  assert.match(footer, /label=\{suggestedQuestions.length \? 'Ask something else' : chats.current\?\.items.length \|\| turnVisible \? 'Follow up' : 'Ask'\}/);
  assert.doesNotMatch(screen, /device-answer-actions|My question|showQuestion/);
  assert.match(css, /\.device-navigation-footer > \.device-share-answer \{[^}]*min-height: 72px;[^}]*background: #116566;/);
});

test('reader shows the question before Answer and preserves finger scroll', () => {
  const reader = screen.slice(screen.indexOf('id="device-answer"'), screen.indexOf('<footer'));
  assert.match(reader, /aria-label="Your question and answer"/);
  const turn = readFileSync(new URL('../app/device/question-turn.tsx', import.meta.url), 'utf8');
  assert.match(reader, /<QuestionTurn/);
  assert.ok(turn.indexOf('<QuestionContext') >= 0 && turn.indexOf('<QuestionContext') < turn.indexOf('<h2>Lantern</h2>'));
  assert.match(reader, /question=\{currentQuestion\}/);
  assert.match(reader, /readingPosition.current = element.scrollTop/);
  assert.match(screen, /element.scrollTop = readingPosition.current/);
  assert.doesNotMatch(reader, /device-share-answer/);
});

test('share opens only a completed session preview and never sends directly', () => {
  const action = screen.slice(screen.indexOf('<button ref={shareButton}'), screen.indexOf('<button ref={cameraButton}'));
  assert.match(screen, /const canShareAnswer = connected && !busy && view.phase === 'complete' && !!status\?\.voice.session_id/);
  assert.match(action, /disabled=\{!canShareAnswer\}/);
  assert.match(action, /if \(!canShareAnswer \|\| !status\?\.voice.session_id\) return/);
  assert.match(action, /source_id: status.voice.session_id, question: status.voice.transcript, answer: status.voice.response, has_photo: !!status.voice.has_photo/);
  assert.match(action, /setPage\('sharing'\)/);
  assert.doesNotMatch(action, /shares\/submit|communityRequest|consent: true/);
  assert.match(screen, /page === 'sharing' && shareSource \? shareButton.current/);
});

test('Home resets the current interaction without deleting saved history', () => {
  assert.match(screen, /disabled=\{busy\} onClick=\{\(\) => void clearAnswer\(\)\} aria-label="New chat; keep saved conversations"/);
  assert.match(screen, /className="device-wordmark" disabled=\{busy\}/);
  assert.match(screen, /title="New chat" aria-busy=\{clearing\}><LanternIcon \/><span>SAGE Lantern<\/span>/);
  assert.equal((screen.match(/aria-label="New chat;/g) || []).length, 1);
  assert.match(screen, /title="Nearby"><RadioSignal paused=\{busy\} \/><\/button>/);
  const homeGroup = screen.slice(screen.indexOf('className="device-home-group"'), screen.indexOf('className="device-top-actions"'));
  assert.match(homeGroup, /device-wordmark/);
  assert.match(homeGroup, /device-lora-indicator/);
  const reset = screen.slice(screen.indexOf('const clearAnswer'), screen.indexOf("if (page === 'settings')"));
  assert.match(reset, /await voiceCommand\('clear', \{\}\)/);
  assert.match(reset, /setPhoto\(undefined\); setPhotoStart\(undefined\); setSessionPhoto\(undefined\)/);
  assert.match(reset, /setClearedSession\(current \|\| null\)/);
  assert.doesNotMatch(reset, /history\/|delete|deprecat/);
});
