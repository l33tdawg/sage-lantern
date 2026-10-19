'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import QuestionKeyboard from './question-keyboard';
import { QuestionEditTarget } from './question-context';
import { createQuestionPredictor } from '../../lib/question-prediction';
import { correctionTextValid } from '../../lib/question-correction';
import { replySessionContext, voiceCommand } from '../../lib/voice-control';
import { establishedSessionContext, fenceProtectedReply, getDeviceToken, type SessionContext } from '../../lib/device-session';
import englishWords from '../../lib/keyboard-words.en.json';
import './question-review.css';

export default function QuestionReview({ sessionId, reviewId, question, recordAgain, hasPhoto = false, canRecordAgain = true, typed = false, embedded = false, actionsContainer, keyboardContainer, onSubmitAttempt }: {
  sessionId: string; reviewId: string; question: string; recordAgain: () => void; hasPhoto?: boolean; canRecordAgain?: boolean;
  typed?: boolean; embedded?: boolean; actionsContainer?: HTMLElement | null; keyboardContainer?: HTMLElement | null; onSubmitAttempt?: (question: string) => void;
}) {
  const [text, setText] = useState(question);
  // A typed question has no recording to review: open the keyboard straight away.
  const [editing, setEditing] = useState(typed && !question.trim());
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [pendingLabel, setPendingLabel] = useState('Working…');
  const [uncertain, setUncertain] = useState(false);
  const [error, setError] = useState('');
  const predict = useMemo(() => createQuestionPredictor(englishWords), []);
  const mounted = useRef(false);
  const busy = useRef(false);
  const submission = useRef<Record<string, string> | null>(null);
  const keyboardRef = useRef<HTMLDialogElement>(null);
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
    const dialog = keyboardRef.current;
    if (!editing || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [editing]);

  const cancel = async (retry: boolean) => {
    if (busy.current || submission.current) return;
    const token = getDeviceToken();
    if (!token) return;
    const issuedUnder = establishedSessionContext();
    busy.current = true; setPendingLabel('Discarding this recording…'); setPending(true); setError('');
    try {
      const reply = await voiceCommand('review/cancel', credentials);
      /**
       * The cancellation is this draft's only if it came back under the
       * authority the request was issued under and that authority still holds.
       *
       * `recordAgain` below is the reason this matters: it clears the review and
       * starts a new capture, and running it on a cancellation that belongs to an
       * authority the device has left would discard a draft under one session and
       * begin recording under another. A refusal lands in the handler below,
       * which keeps the controls disabled and asks the user to try again — it
       * never replays the cancellation on its own.
       */
      let cancelledUnder: SessionContext | null;
      try { cancelledUnder = replySessionContext(reply); }
      catch { throw new Error('Cancellation not confirmed'); }
      if (fenceProtectedReply(issuedUnder, cancelledUnder) !== 'ok') throw new Error('Cancellation not confirmed');
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
    const issuedUnder = establishedSessionContext();
    submission.current ??= { ...credentials, request_id: crypto.randomUUID(), question: text };
    onSubmitAttempt?.(submission.current.question);
    busy.current = true; setPendingLabel('Sending your question…'); setPending(true); setError('');
    try {
      const value = await voiceCommand('review/submit', submission.current) as { session_id?: unknown; status?: unknown };
      if (value?.session_id !== sessionId || value?.status !== 'submitted') throw new Error('Unconfirmed submission');
      /**
       * The confirmation must also come back under the authority the submission
       * was issued under. The submission itself was already sent, so a refusal
       * here does not replay it: the handler below reports it as unconfirmed and
       * leaves the retry to the user, reusing the same request id.
       */
      let submittedUnder: SessionContext | null;
      try { submittedUnder = replySessionContext(value); }
      catch { throw new Error('Unconfirmed submission'); }
      if (fenceProtectedReply(issuedUnder, submittedUnder) !== 'ok') throw new Error('Unconfirmed submission');
    } catch {
      if (mounted.current && getDeviceToken() === token) {
        busy.current = false; setPending(false); setUncertain(true);
        setError('Submission was not confirmed. You can retry the same submission; it will not start a second answer.');
      }
    }
  };

  const actions = <div className="question-review-actions">
          <button disabled={pending || uncertain} onClick={() => void cancel(false)}>{typed ? 'Close' : 'Cancel'}</button>
          {!typed && <button disabled={pending || uncertain || !canRecordAgain} onClick={() => void cancel(true)}>Record again</button>}
          <button className="question-review-submit" disabled={pending || !correctionTextValid(text)} onClick={() => void submit()}>{pending ? 'Working…' : uncertain ? 'Retry submission' : 'Submit'}</button>
        </div>;
  /**
   * The keyboard is one component rendered in one of two frames.
   *
   * A spoken review edits through the modal sheet it has always used. A typed
   * review in Chat renders *in flow* into the composer's own slot instead, so
   * the keyboard is part of the screen the user is already looking at rather
   * than a sheet covering it. The draft, the submission and the fences are the
   * same either way; only the element they are drawn in changes.
   */
  const keyboard = editing && <QuestionKeyboard initialText={text} suggestions={suggestions} label={typed ? 'Type your question' : 'Edit your question'}
    cancelLabel={typed ? 'Close' : 'Back'} submitLabel="Done editing" canSubmit={correctionTextValid(text)} busy={pending}
    onChange={(draft) => { setText(draft.text); setSuggestions(predict(draft)); }}
    onCancel={() => setEditing(false)} onSubmit={(draft) => { setText(draft); setEditing(false); }} />;
  const inlineKeyboard = typed && !!keyboardContainer && !!keyboard;
  return <section className={`question-review${embedded ? ' question-review-embedded' : ''}`} aria-label="Review your question">
    {!embedded && <header><span className="question-review-eyebrow">BEFORE WE ANSWER</span><h1>Did we hear you right?</h1></header>}
    <QuestionEditTarget transcript={text} disabled={pending || uncertain} expanded={editing} edit={() => setEditing(true)}
      hint={pending ? pendingLabel : uncertain ? 'Retry submission below.' : typed ? 'Type your question, then Submit.' : 'Tap your question to edit, or Submit when ready.'} />
    {hasPhoto && <small>Photo attached to this question.</small>}
    {actionsContainer ? createPortal(actions, actionsContainer) : actions}
    {inlineKeyboard ? createPortal(<div className="question-review-editing question-review-inline" aria-label="Edit your question">{keyboard}</div>, keyboardContainer!)
      : keyboard && <dialog ref={keyboardRef} className="question-keyboard-sheet question-review-editing" aria-label="Edit your question"
        onCancel={(event) => { event.preventDefault(); setEditing(false); }}>
        {keyboard}
      </dialog>}
    {!typed && !canRecordAgain && <p>To record again with your photo, cancel and attach it again.</p>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
