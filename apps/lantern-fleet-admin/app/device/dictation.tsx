'use client';

import { useEffect, useEffectEvent, useRef, useState } from 'react';
import { voiceCommand, parseTouchSession, replySessionContext, type TouchSession } from '../../lib/voice-control';
import { establishedSessionContext, fenceProtectedReply } from '../../lib/device-session';
import { parseDictationStatus, type DictationStatus } from '../../lib/message-dictation';
import { MicrophoneIcon, CancelIcon } from './icons';

/**
 * A dictation session whose authority moved is abandoned rather than kept.
 *
 * The wording claims only what happened: there is no recording here to carry on
 * with, and Record is the affordance the user still has.
 */
const SESSION_CHANGED_NOTICE = 'The device session changed. Tap Record to try again.';

export default function Dictation({ onText, close, title = 'Record your message' }: { onText: (text: string) => void; close: () => void; title?: string }) {
  const [status, setStatus] = useState<DictationStatus | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [started, setStarted] = useState(false);
  const mounted = useRef(false);
  const session = useRef<TouchSession | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const operation = useRef(false);
  const recording = useRef(false);
  const terminal = useRef(false);
  const closeHidden = useEffectEvent(close);

  useEffect(() => {
    mounted.current = true;
    const abandon = () => {
      clearTimeout(timer.current); recording.current = false;
      const current = session.current; session.current = null;
      if (current) void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
    };
    const hidden = () => { if (document.hidden) { abandon(); closeHidden(); } };
    document.addEventListener('visibilitychange', hidden);
    return () => { mounted.current = false; document.removeEventListener('visibilitychange', hidden); abandon(); };
  }, []);

  const poll = (current: TouchSession) => {
    timer.current = setTimeout(async () => {
      if (!mounted.current || session.current !== current) return;
      try {
        // The authority this reading is taken under, captured before the first
        // await: a status reply cannot witness the authority its own request was
        // sent under.
        const issuedUnder = establishedSessionContext();
        if (recording.current) await voiceCommand('keepalive', { session_id: current.session_id, token: current.token });
        const reply = await voiceCommand('message/status', { session_id: current.session_id, token: current.token });
        const next = parseDictationStatus(reply, current.session_id);
        /**
         * A reading is adopted only under the authority it was taken under, and
         * only while that authority still holds. A reply that fails either half
         * is not this dictation's state; it falls into the handler below, which
         * abandons the session instead of rendering somebody else's progress.
         */
        if (fenceProtectedReply(issuedUnder, replySessionContext(reply)) !== 'ok') throw new Error('Message session changed');
        if (!mounted.current || session.current !== current) return;
        setStatus(next);
        terminal.current = ['review', 'error', 'cancelled'].includes(next.phase);
        if (next.phase !== 'queued' && next.phase !== 'recording') recording.current = false;
        if (!['review', 'error', 'cancelled'].includes(next.phase)) poll(current);
        else if (next.phase === 'review') timer.current = setTimeout(() => { if (mounted.current) { setStatus(null); setError('Recording expired. Tap Cancel, then record again.'); } }, 110000);
      } catch {
        if (!mounted.current || session.current !== current) return;
        recording.current = false;
        setError('Recording interrupted. Cancel and try again.');
        void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
      }
    }, 750);
  };
  const start = async () => {
    if (operation.current || session.current) return;
    operation.current = true; setPending(true); setError('');
    const issuedUnder = establishedSessionContext();
    try {
      const reply = await voiceCommand('start', { request_id: crypto.randomUUID(), purpose: 'message' });
      const current = parseTouchSession(reply);
      /**
       * The microphone is opened only under the authority the command was issued
       * under, and only while that authority still holds. A refusal cancels the
       * credentials the reply carried rather than leaving a recording nobody
       * owns, exactly as the capture owner does for a refused start.
       */
      if (fenceProtectedReply(issuedUnder, replySessionContext(reply)) !== 'ok') {
        void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {});
        if (mounted.current) setError(SESSION_CHANGED_NOTICE);
        return;
      }
      if (!mounted.current || document.hidden) { void voiceCommand('cancel', { session_id: current.session_id, token: current.token }, true).catch(() => {}); return; }
      session.current = current; recording.current = true; setStarted(true); setStatus({ phase: 'queued' }); poll(current);
    } catch { if (mounted.current) setError('Could not start the microphone. Cancel and try again.'); }
    finally { operation.current = false; if (mounted.current) setPending(false); }
  };
  const stop = async () => {
    const current = session.current;
    if (!current || operation.current) return;
    operation.current = true; setPending(true); recording.current = false;
    try {
      await voiceCommand('stop', { session_id: current.session_id, token: current.token });
      if (mounted.current) setStatus((current) => current && ['review', 'error', 'cancelled', 'cancelling'].includes(current.phase) ? current : { phase: 'transcribing' });
    } catch { if (mounted.current && !terminal.current) setError('Could not confirm recording stopped. Cancel and try again.'); }
    finally { operation.current = false; if (mounted.current) setPending(false); }
  };
  return <section className="device-panel device-dictation" aria-label={title}>
    <h2>{title}</h2>
    {error ? <p role="alert">{error}</p> : <p role="status">{status?.phase === 'review' ? 'Check what I heard.' : status?.phase === 'recording' ? 'Listening…' : status?.phase === 'queued' ? 'Opening microphone…' : status?.phase === 'transcribing' ? 'Turning speech into text…' : status?.phase === 'error' ? 'No clear speech. Try somewhere quieter.' : status?.phase === 'cancelled' || status?.phase === 'cancelling' ? 'Recording cancelled.' : 'Speak for up to 30 seconds, then tap Done.'}</p>}
    {status?.phase === 'review' && <p className="device-message-body">{status.text}</p>}
    <div className="device-community-actions"><button onClick={close}><CancelIcon />Cancel</button>
      {!status && !started && <button className="device-community-primary" disabled={pending} onClick={() => void start()}><MicrophoneIcon />Record</button>}
      {(status?.phase === 'queued' || status?.phase === 'recording') && !error && <button disabled={pending} className="device-community-primary" onClick={() => void stop()}>Done</button>}
      {status?.phase === 'review' && !error && <button className="device-community-primary" onClick={() => onText(status.text!)}>Use this text</button>}
    </div>
  </section>;
}
