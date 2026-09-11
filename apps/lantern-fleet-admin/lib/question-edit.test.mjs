import assert from 'node:assert/strict';
import test from 'node:test';
import { selectQuestion, insertQuestion, eraseQuestion, completeQuestion } from './question-edit.ts';

test('typing replaces selected text and retains the remainder', () => {
  assert.deepEqual(insertQuestion(selectQuestion('Who played new?', 11, 14), 'Neo'), { text: 'Who played Neo?', start: 14, end: 14 });
  assert.equal(insertQuestion(selectQuestion('Who Neo?', 4), 'played ').text, 'Who played Neo?');
});

test('suggestion replaces the entire current word, including its untyped suffix', () => {
  assert.deepEqual(completeQuestion(selectQuestion('Who played neon?', 13), 'Neo'), { text: 'Who played Neo?', start: 14, end: 14 });
  assert.equal(completeQuestion(selectQuestion('Who played ', 11), 'Neo').text, 'Who played Neo');
});

test('backspace removes whole graphemes and selections', () => {
  for (const glyph of ['é', 'e\u0301', '👩🏽‍🔬', '🇲🇾']) {
    const text = `A${glyph}`;
    assert.deepEqual(eraseQuestion(selectQuestion(text, text.length)), { text: 'A', start: 1, end: 1 });
  }
  assert.equal(eraseQuestion(selectQuestion('abc', 1, 3)).text, 'a');
  assert.equal(eraseQuestion(selectQuestion('abc', 0)).text, 'abc');
});

test('cursor positions are bounded and normalized', () => {
  assert.deepEqual(selectQuestion('abc', 9, -2), { text: 'abc', start: 0, end: 3 });
  assert.deepEqual(selectQuestion('abc', NaN), { text: 'abc', start: 3, end: 3 });
});
