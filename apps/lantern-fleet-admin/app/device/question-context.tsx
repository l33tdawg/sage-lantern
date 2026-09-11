'use client';

import { useId } from 'react';

export function QuestionEditTarget({ transcript, label = 'Your question', hint, edit, disabled = false, expanded }: {
  transcript: string; label?: string; hint?: string; edit: () => void; disabled?: boolean; expanded?: boolean;
}) {
  const textId = useId();
  return <button type="button" className="device-question-edit-target" onClick={edit} disabled={disabled}
    aria-label="Edit your question" aria-describedby={textId} aria-haspopup={expanded === undefined ? undefined : 'dialog'} aria-expanded={expanded}>
    <span className="device-question-label">{label}</span>
    <span className="device-question-text" id={textId}>{transcript}</span>
    {hint && <span className="device-question-hint">{hint}</span>}
  </button>;
}

export default function QuestionContext({ transcript, edit, label = 'You asked' }: { transcript: string; edit?: () => void; label?: string }) {
  return edit ? <QuestionEditTarget transcript={transcript} label={label} edit={edit} />
    : <section className="device-question-context"><h2>{label}</h2><p>{transcript}</p></section>;
}
