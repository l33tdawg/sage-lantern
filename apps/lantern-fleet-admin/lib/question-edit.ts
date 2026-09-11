export type QuestionDraft = { text: string; start: number; end: number };

export function selectQuestion(text: string, start: number, end = start): QuestionDraft {
  const clamp = (position: number) => Number.isFinite(position) ? Math.max(0, Math.min(text.length, Math.trunc(position))) : text.length;
  const first = clamp(start);
  const last = clamp(end);
  return { text, start: Math.min(first, last), end: Math.max(first, last) };
}

export function insertQuestion(draft: QuestionDraft, inserted: string): QuestionDraft {
  const current = selectQuestion(draft.text, draft.start, draft.end);
  const text = current.text.slice(0, current.start) + inserted + current.text.slice(current.end);
  return selectQuestion(text, current.start + inserted.length);
}

export function eraseQuestion(draft: QuestionDraft): QuestionDraft {
  const current = selectQuestion(draft.text, draft.start, draft.end);
  if (current.start !== current.end) return insertQuestion(current, '');
  if (!current.start) return current;
  const segments = new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(current.text);
  for (const segment of segments) {
    const end = segment.index + segment.segment.length;
    if (end >= current.start) return insertQuestion({ ...current, start: segment.index, end }, '');
  }
  return current;
}

export function questionWord(draft: QuestionDraft): QuestionDraft {
  const current = selectQuestion(draft.text, draft.start, draft.end);
  if (current.start !== current.end) return current;
  const segments = new Intl.Segmenter(undefined, { granularity: 'word' }).segment(current.text);
  for (const segment of segments) {
    const end = segment.index + segment.segment.length;
    if (segment.isWordLike && segment.index < current.start && end >= current.start) {
      return { ...current, start: segment.index, end };
    }
  }
  return current;
}

export function completeQuestion(draft: QuestionDraft, word: string): QuestionDraft {
  return insertQuestion(questionWord(draft), word);
}
