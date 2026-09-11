'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import QuestionKeyboard from './question-keyboard';
import { QuestionEditTarget } from './question-context';
import { createQuestionPredictor } from '../../lib/question-prediction';
import { correctionTextValid } from '../../lib/question-correction';
import { voiceCommand } from '../../lib/voice-control';
import { getDeviceToken } from '../../lib/device-session';
import englishWords from '../../lib/keyboard-words.en.json';
import './question-review.css';

export default function QuestionReview({ sessionId, reviewId, question, recordAgain, hasPhoto = false, canRecordAgain = true, embedded = false, actionsContainer, onSubmitAttempt }: {
  sessionId: string; reviewId: string; question: string; recordAgain: () => void; hasPhoto?: boolean; canRecordAgain?: boolean;
  embedded?: boolean; actionsContainer?: HTMLElement | null; onSubmitAttempt?: (question: string) => void;
}) {
  const [text, setText] = useState(question);
  const [editing, setEditing] = useState(false);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [pendingLabel, setPendingLabel] = useState('Working…');
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState('');
  const predict = useMemo(() => createQuestionPredictor(englishWords), []);
  const mounted = useRef(false);
  const busy = useRef(false);
  const submission = useRef<Record<string, string> | null>(null);
  const keyboard = useRef<HTMLDialogElement>(null);
  const credentials = useMemo(() => ({ session_id: sessionId, review_id: reviewId }), [sessionId, reviewId]);

  useEffect(() => {
    mounted.current = true;
    const abandon = () => {
      if (!submission.current) void voiceCommand('review/cancel', credentials, true).catch(() => {});
    };
    const hide = () => { if (document.hidden) abandon(); };
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', hide); abandon(); };
  }, [credentials]);

  useEffect(() => {
    const dialog = keyboard.current;
    if (!editing || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [editing]);

  const cancel = async (retry: boolean) => {
    if (busy.current || submission.current) return;
    const token = getDeviceToken();
    if (!token) return;
    busy.current = true; setPendingLabel('Discarding this recording…'); setPending(true); setError('');
    try {
      await voiceCommand('review/cancel', credentials);
      // A status poll may already have unmounted this draft after cancellation.
      if (getDeviceToken() === token && !document.hidden && retry) recordAgain();
      // Keep controls disabled until the device reports that the session ended.
    } catch {
      if (mounted.current && getDeviceToken() === token) {
        busy.current = false; setPending(false);
        setError('Could not confirm cancellation. Try again.');
      }
    }
  };
  const submit = async () => {
    if (busy.current || !correctionTextValid(text)) return;
    const token = getDeviceToken();
    if (!token) return;
    submission.current ??= { ...credentials, request_id: crypto.randomUUID(), question: text };
    onSubmitAttempt?.(submission.current.question);
    busy.current = true; setPendingLabel('Sending your question…'); setPending(true); setError('');
    try {
      const value = await voiceCommand('review/submit', submission.current) as { session_id?: unknown; status?: unknown };
      if (value?.session_id !== sessionId || value?.status !== 'submitted') throw new Error('Unconfirmed submission');
    } catch {
      if (mounted.current && getDeviceToken() === token) {
        busy.current = false; setPending(false); setUncertain(true);
        setError('Submission was not confirmed. You can retry the same submission; it will not start a second answer.');
      }
    }
  };

  const actions = <div className="question-review-actions">
          <button disabled={pending || uncertain} onClick={() => void cancel(false)}>Cancel</button>
          <button disabled={pending || uncertain || !canRecordAgain} onClick={() => void cancel(true)}>Record again</button>
          <button className="question-review-submit" disabled={pending || !correctionTextValid(text)} onClick={() => void submit()}>{pending ? 'Working…' : uncertain ? 'Retry submission' : 'Submit'}</button>
        </div>;
  return <section className={`question-review${embedded ? ' question-review-embedded' : ''}`} aria-label="Review your question">
    {!embedded && <header><span className="question-review-eyebrow">BEFORE WE ANSWER</span><h1>Did we hear you right?</h1></header>}
    <QuestionEditTarget transcript={text} disabled={pending || uncertain} expanded={editing} edit={() => setEditing(true)}
      hint={pending ? pendingLabel : uncertain ? 'Retry submission below.' : 'Tap your question to edit, or Submit when ready.'} />
    {hasPhoto && <small>Photo attached to this question.</small>}
    {actionsContainer ? createPortal(actions, actionsContainer) : actions}
    {editing && <dialog ref={keyboard} className="question-keyboard-sheet question-review-editing" aria-label="Edit your question"
      onCancel={(event) => { event.preventDefault(); setEditing(false); }}>
      <QuestionKeyboard initialText={text} suggestions={suggestions} label="Edit your question"
        cancelLabel="Back" submitLabel="Done editing" canSubmit={correctionTextValid(text)} busy={pending}
        onChange={(draft) => { setText(draft.text); setSuggestions(predict(draft)); }}
        onCancel={() => setEditing(false)} onSubmit={(draft) => { setText(draft); setEditing(false); }} />
    </dialog>}
    {!canRecordAgain && <p>To record again with your photo, cancel and attach it again.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
