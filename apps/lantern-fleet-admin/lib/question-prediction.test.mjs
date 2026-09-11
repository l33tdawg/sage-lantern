import assert from 'node:assert/strict';
import test from 'node:test';
import { createQuestionPredictor } from './question-prediction.ts';
import { selectQuestion } from './question-edit.ts';

test('ranked prefixes take precedence and suggestions are bounded', () => {
  const predict = createQuestionPredictor(['played', 'player', 'place', 'planet', 'played']);
  assert.deepEqual(predict(selectQuestion('Who pla', 7)), ['played', 'player', 'place']);
});

test('local typo correction handles substitution, insertion, deletion and transposition', () => {
  const predict = createQuestionPredictor(['matrix']);
  for (const typed of ['matrox', 'matri', 'maatrix', 'matirx']) {
    assert.deepEqual(predict(selectQuestion(typed, typed.length)), ['matrix']);
  }
  assert.deepEqual(predict(selectQuestion('maxrox', 6)), []);
});

test('suggestions preserve capitalization without changing the draft', () => {
  const predict = createQuestionPredictor(['matrix']);
  for (const [typed, expected] of [['Mat', 'Matrix'], ['MAT', 'MATRIX']]) {
    const draft = selectQuestion(typed, typed.length);
    assert.deepEqual(predict(draft), [expected]);
    assert.equal(draft.text, typed);
  }
});

test('empty, unsupported, oversized and exact words do not produce spurious suggestions', () => {
  const predict = createQuestionPredictor(['matrix']);
  for (const typed of ['', 'matrix', 'مرحبا', '123', 'a'.repeat(8193)]) {
    assert.deepEqual(predict(selectQuestion(typed, typed.length)), []);
  }
  assert.throws(() => createQuestionPredictor(['bad word']));
  assert.throws(() => createQuestionPredictor(Array(50001).fill('word')));
});
