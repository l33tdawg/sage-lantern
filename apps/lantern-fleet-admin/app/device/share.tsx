'use client';

import { useEffect, useRef, useState } from 'react';
import { communityLabel, communityRequest, parseCommunityResult, parseCommunityStatus, parseSharePreview, parseShares, type CommunityShare, type CommunityStatus, type SharePreview, type ShareSource } from '../../lib/community';
import { BackIcon, ShareIcon } from './icons';
import { shareRefreshRequest, type ShareRefreshRequest, type ShareObservation } from '../../lib/share-refresh';

export default function ShareScreen(props: { source?: ShareSource; close: () => void }) {
  return <ShareContent key={JSON.stringify(props.source ?? null)} {...props} />;
}

function ShareContent({ source, close }: { source?: ShareSource; close: () => void }) {
  const [status, setStatus] = useState<CommunityStatus | null>(null);
  const [preview, setPreview] = useState<SharePreview | null>(null);
  const [items, setItems] = useState<CommunityShare[] | null>(null);
  const [more, setMore] = useState(false);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<string | null>(null);
  const [outcomeState, setOutcomeState] = useState('unknown');
  const [error, setError] = useState('');
  const [refresh, setRefresh] = useState(0);
  const [expired, setExpired] = useState(false);
  const mounted = useRef(false);
  const submitting = useRef(false);
  const requestId = useRef<string | null>(null);
  /**
   * Request state is render-visible — the list renders each item's own request —
   * so it is state rather than a ref. A ref read during render is not a dependency
   * React tracks, which is the thing the lint rule is right to forbid.
   */
  const [statusRequests, setStatusRequests] = useState(new Map<string, ShareRefreshRequest>());
  const activeRequest = useRef<AbortController | null>(null);
  const listRequest = useRef<AbortController | null>(null);
  const [observations, setObservations] = useState<Partial<Record<string, ShareObservation>>>({});
  const back = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    mounted.current = true; back.current?.focus();
    const hide = () => {
      if (!document.hidden) return;
      activeRequest.current?.abort();
      listRequest.current?.abort();
      setItems(null); setPreview(null); setObservations({}); setOutcome(null); setChecked(false);
    };
    document.addEventListener('visibilitychange', hide);
    return () => { mounted.current = false; activeRequest.current?.abort(); document.removeEventListener('visibilitychange', hide); };
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    listRequest.current = controller;
    void (async () => {
      try {
        const current = parseCommunityStatus(await communityRequest('status', {}, controller.signal));
        if (controller.signal.aborted) return;
        setStatus(current);
        if (source && current.sharing.available) {
          const result = parseSharePreview(await communityRequest('shares/preview', source, controller.signal));
          if (!controller.signal.aborted) setPreview(result);
        } else if (!source && current.sharing.available) {
          const result = parseShares(await communityRequest('shares/list', {}, controller.signal));
          if (!controller.signal.aborted) {
            // Retain the requests already known and add the ones this list
            // returned, so reloading does not forget a request the user has made.
            setStatusRequests((current) => {
              const next = new Map(current);
              for (const item of result.items) next.set(item.id, shareRefreshRequest(item.id));
              return next;
            });
            setItems(result.items); setMore(result.more);
          }
        }
      } catch { if (!controller.signal.aborted) setError('Shared knowledge unavailable. Nothing new has been submitted.'); }
    })();
    return () => controller.abort();
  }, [source, refresh]);
  useEffect(() => {
    if (!preview) return;
    const timeout = setTimeout(() => { setExpired(true); setChecked(false); }, Math.max(0, Date.parse(preview.expires_at) - Date.now()));
    return () => clearTimeout(timeout);
  }, [preview]);

  const submit = async () => {
    if (submitting.current || !preview || !checked || !status?.sharing.available || outcome || expired || Date.parse(preview.expires_at) <= Date.now()) return;
    submitting.current = true; setBusy(true); setError('');
    requestId.current ??= crypto.randomUUID();
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const result = parseCommunityResult(await communityRequest('shares/submit', { preview_id: preview.preview_id, digest: preview.digest, request_id: requestId.current, consent: true }, controller.signal), 'share');
      if (mounted.current && !controller.signal.aborted) { setOutcome(communityLabel(result.state)); setOutcomeState(result.state); }
    } catch { if (mounted.current && !controller.signal.aborted) { setOutcome('Submission status unconfirmed'); setError('It may already have been submitted. Check Shared knowledge before trying again.'); } }
    finally { submitting.current = false; if (activeRequest.current === controller) activeRequest.current = null; if (mounted.current) setBusy(false); }
  };
  const refreshShare = async (id: string, action: 'refresh' | 'inspect') => {
    if (submitting.current || !status?.sharing.available) return;
    const attempt = statusRequests.get(id);
    if (!attempt) return;
    submitting.current = true; setBusy(true); setError('');
    setObservations((values) => { const next = { ...values }; delete next[id]; return next; });
    const controller = new AbortController();
    activeRequest.current = controller;
    try {
      const result = await attempt.run(action, controller.signal);
      if (mounted.current && !controller.signal.aborted) setObservations((values) => ({ ...values, [id]: result }));
    } catch {
      if (mounted.current && !controller.signal.aborted) setError('Status request unconfirmed. Check the same request; do not submit the answer again.');
    } finally {
      submitting.current = false;
      if (activeRequest.current === controller) activeRequest.current = null;
      if (mounted.current) setBusy(false);
    }
  };
  return <section className="device-subscreen device-community" aria-label={source ? 'Public sharing preview' : 'Shared knowledge'} onKeyDown={(event) => { if (event.key === 'Escape' && !busy) close(); }}>
    <header className="device-subheader"><button ref={back} disabled={busy} onClick={close}><BackIcon />Back</button><div className="device-page-title"><ShareIcon /><h1>{source ? 'Public preview' : 'Shared knowledge'}</h1></div></header>
    <div className="device-subcontent" tabIndex={0}>
      {error && <p role="alert">{error}</p>}
      {outcome ? <section className="device-panel"><h2 className={`device-community-badge state-${outcomeState}`} role="status">{outcome}</h2><p>Your original question and answer stay separate on this device.</p><p>Check Menu → Shared knowledge for updates.</p></section> : source ? <>
          <p className="device-muted">Private preview · nothing has been published.</p>
          {!status && !error && <p role="status">Checking public sending…</p>}
          {(error || status && !status.sharing.available) && <section className="device-panel"><h2>Public sending isn’t ready yet</h2><p>You can review the text below. Nothing is being published.</p></section>}
          <section className="device-panel"><h2>Question</h2><p className="device-message-body">{preview?.question ?? source.question}</p><h2>Answer</h2><p className="device-message-body">{preview?.answer ?? source.answer}</p><p className="device-muted">AI-generated answer · not independently checked.</p></section>
          {source.has_photo && <p>Photo sharing is not available yet. No photo is included in this preview.</p>}
          <section className="device-panel device-public-warning"><h2>Share publicly?</h2><p>Anyone may receive this question and answer. Public records and other people’s copies cannot be recalled—even if you later correct or withdraw the information.</p><p>Check for names, private details, and anything you do not want others to know.</p></section>
          <button className="device-consent-button" disabled={!preview || expired || !status?.sharing.available} aria-pressed={checked} onClick={() => setChecked((value) => !value)}><span aria-hidden="true">{checked ? '✓' : '○'}</span>I checked this for private information</button>
          {expired && <p role="status">Preview expired. Go back and review it again.</p>}
      </> : !status ? <p role="status">Checking shared knowledge…</p> : !status.sharing.available ? <section className="device-panel"><h2>Public sharing isn’t ready yet</h2><p>Public identity, secure storage and verified status tracking must be connected first.</p></section> : items === null ? <p role="status">Loading your submissions…</p> : items.length ? items.map((item) => {
        const observed = observations[item.id];
        const attempt = statusRequests.get(item.id);
        const state = attempt?.busy || attempt?.uncertain ? 'unknown' : observed?.state ?? item.state;
        return <article className="device-panel" key={item.id}><h2>{item.question}</h2><p className={`device-community-badge state-${state}`} role="status">{communityLabel(state)}</p>
          {observed?.trust_basis === 'pinned_gateway' && <p>Gateway report{observed.observed_status ? `: ${observed.observed_status}` : ' pending'}. Public acceptance is not independently verified.</p>}
          {state === 'rejected' && <p>Your private original is unchanged. Copies already submitted may remain.</p>}{state === 'accepted' && <p>Public acceptance does not guarantee that an answer is correct.</p>}
          <p>Requesting an update sends a status query for this existing public submission. It does not submit your answer again.</p>
          <button disabled={busy || attempt !== undefined && !attempt.canRefresh} onClick={() => void refreshShare(item.id, 'refresh')}>Request updated status</button>
          {attempt?.requestId && <><button disabled={busy} onClick={() => void refreshShare(item.id, 'inspect')}>Check status request</button><p className="device-muted">Status request: {attempt.requestId}</p></>}
          {attempt?.uncertain && <p>Earlier request outcome remains unconfirmed. No new request will be sent from this view.</p>}
        </article>;
      }) : <p>No public submissions yet.</p>}
      {more && <p>Showing a bounded page of the latest submissions.</p>}
    </div>
    <footer className="device-community-actions">{source ? <><button disabled={busy} onClick={close}>{outcome ? 'Done' : 'Cancel'}</button>{!outcome && <button className="device-community-primary" disabled={busy || !checked || !preview || expired || !status?.sharing.available} onClick={() => void submit()}><ShareIcon />{busy ? 'Submitting…' : 'Submit publicly'}</button>}</> : <button disabled={busy} onClick={() => { setError(''); setItems(null); setObservations({}); setRefresh((value) => value + 1); }}>Reload saved submissions</button>}</footer>
  </section>;
}
