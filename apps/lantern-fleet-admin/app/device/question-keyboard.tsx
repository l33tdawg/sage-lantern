'use client';

import { useLayoutEffect, useRef, useState } from 'react';
import { completeQuestion, eraseQuestion, insertQuestion, selectQuestion, type QuestionDraft } from '../../lib/question-edit';
import './question-keyboard.css';

export default function QuestionKeyboard({ initialText, suggestions, onChange, onCancel, onSubmit, canSubmit = false, busy = false, label = 'Your question', submitLabel = 'Ask again', cancelLabel = 'Cancel' }: {
  initialText: string;
  suggestions: readonly string[];
  onChange: (draft: QuestionDraft) => void;
  onCancel: () => void;
  onSubmit: (text: string) => void;
  canSubmit?: boolean;
  busy?: boolean;
  label?: string;
  submitLabel?: string;
  cancelLabel?: string;
}) {
  const [draft, setDraft] = useState(() => selectQuestion(initialText, initialText.length));
  const [uppercase, setUppercase] = useState(false);
  const [symbols, setSymbols] = useState(false);
  const editor = useRef<HTMLTextAreaElement>(null);
  const restoreSelection = useRef(false);
  const update = (next: QuestionDraft, restore = false) => {
    restoreSelection.current = restore;
    setDraft(next);
    onChange(next);
  };
  useLayoutEffect(() => {
    if (!restoreSelection.current || !editor.current) return;
    restoreSelection.current = false;
    editor.current.focus({ preventScroll: true });
    editor.current.setSelectionRange(draft.start, draft.end);
  }, [draft]);
  // Read the actual caret at key press: touch selection may precede React's
  // selection update, especially when quickly tapping a replacement key.
  const currentDraft = () => editor.current
    ? selectQuestion(editor.current.value, editor.current.selectionStart, editor.current.selectionEnd) : draft;
  const insert = (text: string) => update(insertQuestion(currentDraft(), text), true);
  const words = [...new Set(suggestions)].filter((word) => word.trim() && word.length <= 48).slice(0, 3);
  return <section className="question-keyboard" aria-label={label}>
    <label htmlFor="question-draft">{label}</label>
    <textarea id="question-draft" ref={editor} value={draft.text} disabled={busy} inputMode="none" autoComplete="off" autoCorrect="off" spellCheck={false}
      onChange={(event) => update(selectQuestion(event.currentTarget.value, event.currentTarget.selectionStart, event.currentTarget.selectionEnd))}
      onSelect={(event) => { const field = event.currentTarget; update(selectQuestion(field.value, field.selectionStart, field.selectionEnd)); }} />
    <div className="question-suggestions" aria-label="Word suggestions">
      {words.map((word) => <button key={word} disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => update(completeQuestion(currentDraft(), word), true)}>{word}</button>)}
    </div>
    <div className="question-key-rows" role="group" aria-label="Question keyboard">
      {(symbols ? ['1234567890', '.,!?\'"-:;', '()/&+%='] : ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']).map((row) => <div className="question-key-row" key={row}>{[...row].map((letter) => <button key={letter} disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => insert(uppercase ? letter.toUpperCase() : letter)}>{uppercase ? letter.toUpperCase() : letter}</button>)}</div>)}
      <div className="question-key-row question-key-tools">
        <button disabled={busy} aria-pressed={uppercase} onClick={() => setUppercase(!uppercase)}>Shift</button>
        <button disabled={busy} aria-label={symbols ? 'Show letters' : 'Show numbers and punctuation'} onClick={() => setSymbols(!symbols)}>{symbols ? 'ABC' : '123'}</button>
        <button className="question-space-key" disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => insert(' ')}>Space</button>
        <button disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => insert('?')}>?</button>
        <button disabled={busy} aria-label="Delete previous character" onPointerDown={(event) => event.preventDefault()} onClick={() => update(eraseQuestion(currentDraft()), true)}>⌫</button>
      </div>
    </div>
    <div className="question-keyboard-actions"><button disabled={busy} onClick={onCancel}>{cancelLabel}</button><button disabled={busy || !canSubmit || !draft.text.trim()} onClick={() => onSubmit(draft.text)}>{submitLabel}</button></div>
  </section>;
}
