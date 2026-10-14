'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import QuestionKeyboard from './question-keyboard';
import { QuestionEditTarget } from './question-context';
import { createQuestionPredictor } from '../../lib/question-prediction';
import { correctionTextValid } from '../../lib/question-correction';
import { voiceCommand, parseTouchSession, replySessionContext, VoiceControlError } from '../../lib/voice-control';
import { establishedSessionContext, fenceProtectedReply, getDeviceToken } from '../../lib/device-session';
import englishWords from '../../lib/keyboard-words.en.json';
import './question-review.css';

export default function SuggestedQuestion({ sourceId, question, actionsContainer, close, askSomethingElse, submitted }: {
  sourceId: string; question: string; actionsContainer: HTMLElement | null;
  close: () => void; askSomethingElse: () => void; submitted: (sessionId: string, question: string) => void;
}) {
  const [text, setText] = useState(question);
  const [editing, setEditing] = useState(false);
  const [words, setWords] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [rejected, setRejected] = useState(false);
  const [error, setError] = useState('');
  const predict = useMemo(() => createQuestionPredictor(englishWords), []);
  const mounted = useRef(false);
  const busy = useRef(false);
  const submission = useRef<Record<string, string> | null>(null);
  const token = useRef(getDeviceToken());
  const keyboard = useRef<HTMLDialogElement>(null);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const dialog = keyboard.current;
    if (!editing || !dialog) return;
    dialog.showModal();
    return () => dialog.close();
  }, [editing]);
  const submit = async () => {
    if (busy.current || rejected || !correctionTextValid(text) || !token.current
      || token.current !== getDeviceToken() || document.hidden) return;
    const issuedUnder = establishedSessionContext();
    submission.current ??= { source_id: sourceId, question: text, request_id: crypto.randomUUID() };
    busy.current = true; setPending(true); setError('');
    try {
      const reply = await voiceCommand('followup', submission.current);
      const session = parseTouchSession(reply);
      /**
       * The answer this follow-up starts belongs to the authority the request was
       * issued under, and to that authority only while it still holds. A refusal
       * falls into the handler below, which reports the submission as unconfirmed
       * and reuses the same request id if the user retries — the request has
       * already been sent, so nothing here replays it.
       */
      if (fenceProtectedReply(issuedUnder, replySessionContext(reply)) !== 'ok') throw new Error('Submission not confirmed');
      if (mounted.current && token.current === getDeviceToken() && !document.hidden) submitted(session.session_id, submission.current.question);
    } catch (failure) {
      if (mounted.current && token.current === getDeviceToken()) {
        if (failure instanceof VoiceControlError && [400, 403, 409].includes(failure.status)) {
          busy.current = false; setPending(false); setUncertain(false); setRejected(true);
          setError('This suggestion could not be submitted. Cancel to return to the conversation, or ask something else.');
          return;
        }
        busy.current = false; setPending(false); setUncertain(true);
        setError('Submission was not confirmed. Retry uses the same request and will not start a second answer.');
      }
    }
  };
  const actions = <div className="question-review-actions">
    <button disabled={pending || uncertain} onClick={close}>Cancel</button>
    <button disabled={pending || uncertain} onClick={askSomethingElse}>Ask something else</button>
    <button className="question-review-submit" disabled={pending || rejected || !correctionTextValid(text)} onClick={() => void submit()}>{pending ? 'Working…' : uncertain ? 'Retry submission' : 'Submit'}</button>
  </div>;
  return <section className="question-review question-review-embedded" aria-label="Review suggested question">
    <QuestionEditTarget transcript={text} disabled={pending || uncertain} expanded={editing} edit={() => setEditing(true)}
      hint={pending ? 'Sending your question…' : 'Tap to edit, or Submit to ask this follow-up.'} />
    {actionsContainer ? createPortal(actions, actionsContainer) : actions}
    {editing && <dialog ref={keyboard} className="question-keyboard-sheet question-review-editing" aria-label="Edit suggested question"
      onCancel={(event) => { event.preventDefault(); setEditing(false); }}>
      <QuestionKeyboard initialText={text} suggestions={words} label="Edit suggested question"
        cancelLabel="Back" submitLabel="Done editing" canSubmit={correctionTextValid(text)} busy={pending}
        onChange={(draft) => { setText(draft.text); setWords(predict(draft)); }}
        onCancel={() => setEditing(false)} onSubmit={(draft) => { setText(draft); setEditing(false); }} />
    </dialog>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
