'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import QuestionKeyboard from './question-keyboard';
import { createQuestionPredictor } from '../../lib/question-prediction';
import { correctionTextValid, type CorrectionAttempts } from '../../lib/question-correction';
import { deviceSessionHeaders, establishedSessionContext, fenceProtectedReply, getDeviceToken } from '../../lib/device-session';
import { parseTouchSession, replySessionContext } from '../../lib/voice-control';
import type { PhotoAttachment } from '../../lib/photo-preview';
import Image from 'next/image';
import englishWords from '../../lib/keyboard-words.en.json';
import { parseCorrectionConfirmation, type CorrectionConfirmation } from '../../lib/correction-reconciliation';

export default function QuestionCorrection({ sourceId, question, photo, historyPhoto = false, fromHistory = false, rankedWords = englishWords, attempts, ready, close, submitted }: {
  sourceId: string; question: string; photo?: PhotoAttachment; historyPhoto?: boolean; fromHistory?: boolean;
  rankedWords?: readonly string[]; ready: boolean; close: () => void;
  attempts: CorrectionAttempts;
  submitted: (sessionId: string, photo?: PhotoAttachment) => void;
}) {
  const predict = useMemo(() => createQuestionPredictor(rankedWords), [rankedWords]);
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [confirmation, setConfirmation] = useState<{ grant: CorrectionConfirmation; text: string; token: string; expires: number } | null>(null);
  const [valid, setValid] = useState(() => correctionTextValid(question));
  const attempted = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; controller.current?.abort(); };
  }, []);
  const review = async (text: string) => {
    const token = getDeviceToken();
    if (!ready || attempted.current || !token || !correctionTextValid(text)) return;
    attempted.current = true;
    setPending(true);
    setError('');
    controller.current = new AbortController();
    const started = performance.now();
    const deadline = setTimeout(() => controller.current?.abort(), 5000);
    try {
      const response = await fetch('http://127.0.0.1:3197/correct/reconcile', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control', ...deviceSessionHeaders() },
        body: JSON.stringify({ source_id: sourceId }), signal: controller.current.signal,
        cache: 'no-store', credentials: 'omit', redirect: 'error',
      });
      if (!response.ok) throw new Error('Reconciliation refused');
      const grant = parseCorrectionConfirmation(await response.json());
      if (mounted.current && getDeviceToken() === token && !document.hidden) {
        setConfirmation({ grant, text, token, expires: started + grant.expiresInSeconds * 1000 });
      }
    } catch {
      if (mounted.current && getDeviceToken() === token) setError('Could not check the earlier request. Your question was not sent.');
    } finally {
      clearTimeout(deadline);
      attempted.current = false;
      if (mounted.current) setPending(false);
    }
  };
  const submit = async () => {
    const token = getDeviceToken();
    if (!confirmation || attempted.current) return;
    if (!token || confirmation.token !== token) {
      setConfirmation(null);
      setError('Your session changed. Tap Ask again to check once more.');
      return;
    }
    if (!ready) return;
    if (performance.now() >= confirmation.expires) {
      setConfirmation(null);
      setError('Confirmation expired. Tap Ask again to check once more.');
      return;
    }
    const { text, grant } = confirmation;
    setConfirmation(null);
    attempted.current = true;
    setPending(true);
    // The authority this correction is submitted under, captured before the
    // command leaves. It is a write, so a refusal below reports the submission as
    // unconfirmed and never replays it.
    const issuedUnder = establishedSessionContext();
    controller.current = new AbortController();
    const deadline = setTimeout(() => controller.current?.abort(), 5000);
    try {
      const response = await fetch('http://127.0.0.1:3197/correct', {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control', ...deviceSessionHeaders() },
        body: JSON.stringify({ request_id: crypto.randomUUID(), source_id: sourceId, source_kind: fromHistory ? 'history' : 'current', question: text, ...(historyPhoto && photo ? { image_base64: photo.image } : {}),
          new_execution: { challenge: grant.challenge, expected_revision: grant.expectedRevision, consent: true, acknowledge_unknown: true } }),
        signal: controller.current.signal, cache: 'no-store', credentials: 'omit', redirect: 'error',
      });
      if (!response.ok) throw new Error('Correction refused');
      const reply: unknown = await response.json();
      const session = parseTouchSession(reply);
      /**
       * The new session is adopted only when the reply was issued under the
       * authority this submission was sent under and that authority still holds.
       * A mismatch leaves the submission unconfirmed: the request has already
       * gone out, so the honest outcome is the existing "it may have started"
       * report and a manual check, not a silent second attempt.
       */
      if (fenceProtectedReply(issuedUnder, replySessionContext(reply)) !== 'ok') throw new Error('Correction not confirmed');
      if (mounted.current && getDeviceToken() === token && !document.hidden) {
        attempts.confirmed(sourceId);
        submitted(session.session_id, photo);
      }
    } catch {
      if (mounted.current && getDeviceToken() === token) setError('Could not confirm submission. It may have started. Check the answer or Past questions before asking again.');
    } finally {
      clearTimeout(deadline);
      attempted.current = false;
      if (mounted.current) setPending(false);
    }
  };
  return <section className="device-subscreen" aria-label="Correct your question">
    <div className="device-subcontent">
      {photo && <Image src={`data:image/jpeg;base64,${photo.preview}`} width={160} height={100} unoptimized alt="Photo kept with your question" />}
      <div hidden={confirmation !== null}>
        <QuestionKeyboard initialText={question} suggestions={suggestions} onChange={(draft) => { setSuggestions(predict(draft)); setValid(correctionTextValid(draft.text)); }}
          onCancel={close} onSubmit={(text) => void review(text)} canSubmit={ready && valid && !pending} busy={pending} />
      </div>
      {confirmation && <section className="question-keyboard" aria-label="Confirm new answer">
        <h2>Create a new answer?</h2>
        <p>An earlier request may already have run. This sends your edited question again.</p>
        <div className="question-keyboard-actions">
          <button type="button" onClick={() => setConfirmation(null)}>Back</button>
          <button type="button" disabled={!ready || pending} onClick={() => void submit()}>Ask again</button>
        </div>
      </section>}
      {!valid && <p role="status">Enter a short question using ordinary text.</p>}
      {!ready && <p role="status">You can edit now. Wait for the current answer before sending.</p>}
      {error && <p role="alert">{error}</p>}
    </div>
  </section>;
}
