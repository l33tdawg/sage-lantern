'use client';

import { useEffect, useRef, useState } from 'react';
import { getDeviceToken } from '../../lib/device-session';
import { RenderedReceiptFlow, readReceiptLabel, type ReadReceiptOutcome, type RenderChallenge } from '../../lib/read-receipts';

export default function ReadReceiptControl({ id, prior, onOutcome }: {
  id: string; prior: ReadReceiptOutcome | undefined; onOutcome: (outcome: ReadReceiptOutcome) => void;
}) {
  const [challenge, setChallenge] = useState<RenderChallenge | null>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [unavailable, setUnavailable] = useState(false);
  const [expired, setExpired] = useState(false);
  const flow = useRef<RenderedReceiptFlow | null>(null);
  const mounted = useRef(false);
  const sending = useRef(false);
  const token = useRef(getDeviceToken());
  const hadOutcome = useRef(!!prior);

  useEffect(() => {
    mounted.current = true;
    if (hadOutcome.current) return () => { mounted.current = false; };
    const current = new RenderedReceiptFlow(id);
    flow.current = current;
    let secondFrame = 0;
    const firstFrame = requestAnimationFrame(() => {
      secondFrame = requestAnimationFrame(() => {
        if (!mounted.current || document.hidden || token.current !== getDeviceToken()) return;
        void current.beginRendered().then((value) => {
          if (mounted.current && flow.current === current) setChallenge(value);
        }).catch(() => { if (mounted.current && flow.current === current) setUnavailable(true); });
      });
    });
    const hide = () => {
      if (document.hidden || token.current !== getDeviceToken()) {
        current.close(); setChallenge(null); setChecked(false); setUnavailable(true);
      }
    };
    const timer = setInterval(hide, 250);
    document.addEventListener('visibilitychange', hide);
    return () => {
      mounted.current = false; current.close(); flow.current = null;
      cancelAnimationFrame(firstFrame); cancelAnimationFrame(secondFrame);
      clearInterval(timer); document.removeEventListener('visibilitychange', hide);
    };
  }, [id]);
  useEffect(() => {
    if (!challenge) return;
    const timer = setTimeout(() => { setExpired(true); setChecked(false); }, Math.max(0, challenge.expires_at * 1000 - Date.now()));
    return () => clearTimeout(timer);
  }, [challenge]);

  const send = async () => {
    if (!checked || !challenge || expired || busy || sending.current || prior || !flow.current
        || document.hidden || token.current !== getDeviceToken()) return;
    sending.current = true; setBusy(true);
    onOutcome({ id, receipt_id: null, state: 'unknown' });
    try {
      const outcome = await flow.current.acknowledge(true);
      if (mounted.current && token.current === getDeviceToken() && !document.hidden) onOutcome(outcome);
    } catch {
      if (mounted.current && token.current === getDeviceToken()) setUnavailable(true);
    } finally { if (mounted.current) setBusy(false); }
  };

  return <section aria-label="Read receipt">
    <h3>Tell the sender you opened this message?</h3>
    <p>Opening it does not send a receipt. This optional notice reveals that you opened this message; it does not share other messages.</p>
    {busy ? <p role="status">Queuing read receipt…</p> : prior ? <><p role="status">{readReceiptLabel(prior.state)}</p>{prior.state === 'unknown' && <p>It may already be queued or sent. Do not send another receipt while its status is unknown.</p>}</>
      : unavailable ? <p role="status">Read receipts are unavailable. No receipt was requested.</p>
        : expired ? <p role="status">This confirmation expired. Reopen the message to review it again.</p>
          : !challenge ? <p role="status">Checking read-receipt availability…</p>
            : <><label><input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} /> I want to notify the sender that I opened this message.</label>
              <div className="device-community-actions"><button disabled={!checked} onClick={() => void send()}>Send read receipt</button></div></>}
  </section>;
}
