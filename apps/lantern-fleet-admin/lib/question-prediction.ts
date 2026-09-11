import { questionWord, type QuestionDraft } from './question-edit.ts';

function oneEdit(first: string, second: string): boolean {
  if (Math.abs(first.length - second.length) > 1) return false;
  if (first === second) return false;
  for (let index = 0; index < Math.max(first.length, second.length); index++) {
    if (first[index] === second[index]) continue;
    if (first.length < second.length) return first.slice(index) === second.slice(index + 1);
    if (first.length > second.length) return first.slice(index + 1) === second.slice(index);
    return first.slice(index + 1) === second.slice(index + 1)
      || (first[index] === second[index + 1] && first[index + 1] === second[index]
        && first.slice(index + 2) === second.slice(index + 2));
  }
  return false;
}

export function createQuestionPredictor(rankedWords: readonly string[]) {
  if (rankedWords.length > 50000) throw new Error('Prediction dictionary too large');
  const words = [...new Set(rankedWords.map((word) => {
    if (typeof word !== 'string' || !/^[a-zA-Z]{1,32}$/.test(word)) throw new Error('Invalid English prediction word');
    return word.toLowerCase();
  }))];
  return (draft: QuestionDraft): string[] => {
    if (draft.text.length > 8192) return [];
    const selected = questionWord(draft);
    const typed = draft.text.slice(selected.start, draft.start === draft.end ? draft.start : selected.end);
    if (!/^[a-zA-Z]{1,32}$/.test(typed)) return [];
    const prefix = typed.toLowerCase();
    const completions: string[] = [];
    const corrections: string[] = [];
    for (const word of words) {
      if (word === prefix) continue;
      if (word.startsWith(prefix)) completions.push(word);
      else if (prefix.length >= 3 && corrections.length < 3 && oneEdit(prefix, word)) corrections.push(word);
      if (completions.length === 3) break;
    }
    return [...completions, ...corrections].slice(0, 3).map((word) => {
      if (typed.length > 1 && typed === typed.toUpperCase()) return word.toUpperCase();
      return typed[0] === typed[0].toUpperCase() ? word[0].toUpperCase() + word.slice(1) : word;
    });
  };
}
