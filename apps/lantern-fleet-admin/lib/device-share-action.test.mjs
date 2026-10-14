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
  // Focus on return names the control rather than the page. Sharing is the one
  // route opened without recording an origin — the Share button sets the page
  // directly — so the page being left is still the only evidence for it.
  assert.match(screen, /target === 'share' \? shareButton\.current/);
  assert.match(screen, /page === 'sharing' && shareSource \? 'share'/);
});

test('Home resets the current interaction without deleting saved history', () => {
  const header = readFileSync(new URL('../app/device/device-header.tsx', import.meta.url), 'utf8');
  assert.match(screen, /if \(page === 'main' && !correction\) void clearAnswer\(\);/);
  // The busy guard exists to stop the destructive reset running mid-turn. In a
  // build with a Home screen the wordmark only navigates — one shared capture
  // owner survives the move — so it stays reachable during recording. Review
  // and draft editors must finish or be explicitly cancelled before navigating.
  assert.match(header, /className="device-wordmark" disabled=\{wordmarkBlocked \|\| \(busy && wordmarkStartsChat\)\} onClick=\{onHome\}/);
  assert.match(header, /aria-busy=\{clearing\}/);
  assert.match(header, /title=\{wordmarkBlocked \? 'Finish or cancel your question before returning Home\.' : home \? 'New chat' : 'Home'\}/);
  assert.match(header, /<LanternIcon \/><span>SAGE Lantern<\/span>/);
  assert.equal((header.match(/New chat; keep saved conversations/g) || []).length, 1);
  assert.match(header, /title="Nearby"><RadioSignal paused=\{busy\} \/><\/button>/);
  const homeGroup = header.slice(header.indexOf('className="device-home-group"'), header.indexOf('className="device-top-actions"'));
  assert.match(homeGroup, /device-wordmark/);
  assert.match(homeGroup, /device-lora-indicator/);
  const reset = screen.slice(screen.indexOf('const clearAnswer'), screen.indexOf('const headerPage'));
  assert.match(reset, /await voiceCommand\('clear', \{\}\)/);
  assert.match(reset, /setPhoto\(undefined\); setPhotoStart\(undefined\); setSessionPhoto\(undefined\)/);
  assert.match(reset, /setClearedSession\(current \|\| null\)/);
  assert.doesNotMatch(reset, /history\/|delete|deprecat/);
});
