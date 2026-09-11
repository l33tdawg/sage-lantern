import assert from 'node:assert/strict';
import test from 'node:test';
import { parseFollowUpQuestions } from './device-state.ts';

test('optional questions are bounded, ordinary text, and deduplicated', () => {
  assert.deepEqual(parseFollowUpQuestions(['How does this work?', 'What can I try?']), ['How does this work?', 'What can I try?']);
  assert.deepEqual(parseFollowUpQuestions([' How? ', 'How?']), ['How?']);
  for (const bad of [null, {}, 'question', ['a', 'b', 'c'], [''], ['x\ny'], ['<script>'], [true], ['界'.repeat(81)]]) {
    assert.deepEqual(parseFollowUpQuestions(bad), []);
  }
});
