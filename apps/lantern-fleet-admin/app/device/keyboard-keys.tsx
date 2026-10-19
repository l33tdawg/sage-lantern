'use client';

/**
 * The key grid of Lantern's inline keyboard.
 *
 * Extracted from `question-keyboard.tsx` so the Agent add/edit form uses *the
 * same* keyboard the question surfaces use. A second copy of these rows would be
 * a second keyboard: the key set, the Shift/123 state and the pointer handling
 * would drift apart, and the form's keys would stop matching the device's.
 *
 * The caller owns the editable field and the actions (submit, cancel, done) and
 * receives every edit; this file owns the keys, which case or symbol set they
 * currently show, and nothing else.
 */
import { useState } from 'react';
// The key rows' own styling travels with the keys, so a caller that renders the
// grid without the full question surface still gets the device's key sizes.
import './question-keyboard.css';

export default function KeyboardKeys({ label = 'Question keyboard', busy = false, onInsert, onErase, onDone, doneLabel = 'Done' }: {
  /** The key grid's accessible name. */
  label?: string;
  busy?: boolean;
  onInsert: (text: string) => void;
  /** Remove one grapheme back, or the selection, through the caller's editor. */
  onErase: () => void;
  /** Present only where the keyboard can be put away without leaving the screen. */
  onDone?: () => void;
  doneLabel?: string;
}) {
  const [uppercase, setUppercase] = useState(false);
  const [symbols, setSymbols] = useState(false);
  return <div className="question-key-rows" role="group" aria-label={label}>
    {(symbols ? ['1234567890', '.,!?\'"-:;', '()/&+%='] : ['qwertyuiop', 'asdfghjkl', 'zxcvbnm']).map((row) => <div className="question-key-row" key={row}>{[...row].map((letter) => <button key={letter} disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => onInsert(uppercase ? letter.toUpperCase() : letter)}>{uppercase ? letter.toUpperCase() : letter}</button>)}</div>)}
    <div className="question-key-row question-key-tools">
      <button disabled={busy} aria-pressed={uppercase} onClick={() => setUppercase(!uppercase)}>Shift</button>
      <button disabled={busy} aria-label={symbols ? 'Show letters' : 'Show numbers and punctuation'} onClick={() => setSymbols(!symbols)}>{symbols ? 'ABC' : '123'}</button>
      <button className="question-space-key" disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => onInsert(' ')}>Space</button>
      <button disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={() => onInsert('?')}>?</button>
      <button disabled={busy} aria-label="Delete previous character" onPointerDown={(event) => event.preventDefault()} onClick={onErase}>⌫</button>
      {onDone && <button disabled={busy} onPointerDown={(event) => event.preventDefault()} onClick={onDone}>{doneLabel}</button>}
    </div>
  </div>;
}
