'use client';

import { useEffect, useRef, useState } from 'react';
import { parseHistoryPhoto, type HistoryItem } from '../../lib/device-history';
import { savedHistory } from '../../lib/saved-history';
import { useSavedHistory } from './saved-history';
import { voiceCommand } from '../../lib/voice-control';
import { BackIcon, CameraIcon, HistoryIcon, MicrophoneIcon, ShareIcon } from './icons';
import ShareScreen from './share';
import { communityLabel, communityRequest, parseHistoryShares, type HistoryShareState } from '../../lib/community';
import { removalNotice } from '../../lib/device-retention';
import { prepareHistoryPhoto } from '../../lib/history-retry';
import type { PhotoAttachment } from '../../lib/photo-preview';
import Image from 'next/image';

function SavedPhoto({ sessionId }: { sessionId: string }) {
  const [source, setSource] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let disposed = false;
    void voiceCommand('history/photo', { session_id: sessionId }).then((value) => {
      if (!disposed) setSource(parseHistoryPhoto(value));
    }).catch(() => { if (!disposed) setFailed(true); });
    return () => { disposed = true; };
  }, [sessionId]);
  return <figure className="device-saved-photo">{source && !failed ? <Image src={source} width={1920} height={1080} unoptimized alt="Photo saved with this question" onError={() => setFailed(true)} /> : <p role="status">{failed ? 'Saved photo unavailable. Your answer is still here.' : 'Loading saved photo…'}</p>}<figcaption>Attached photo</figcaption></figure>;
}

export default function HistoryScreen({ close, canAsk, onAskAgain, onCorrect }: { close: () => void; canAsk: boolean; onAskAgain: (photo?: PhotoAttachment) => void; onCorrect?: (sourceId: string, question: string, photo?: PhotoAttachment) => void }) {
  const { history, error: historyError } = useSavedHistory();
  const [selected, setSelected] = useState<HistoryItem | null>(null);
  const [confirmation, setConfirmation] = useState<'clear' | 'delete' | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [sharing, setSharing] = useState(false);
  const [shareRefresh, setShareRefresh] = useState(0);
  const [shareStates, setShareStates] = useState<Record<string, HistoryShareState>>({});
  const asking = useRef(false);
  const mounted = useRef(false);
  const back = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);

  useEffect(() => {
    mounted.current = true;
    back.current?.focus();
    void savedHistory.refresh();
    return () => { mounted.current = false; };
  }, []);

  useEffect(() => {
    if (!history || sharing) return;
    const controller = new AbortController();
    void (async () => {
      for (let offset = 0; offset < history.items.length; offset += 20) {
        const ids = history.items.slice(offset, offset + 20).map((item) => item.session_id);
        try {
          const states = parseHistoryShares(await communityRequest('shares/status', { source_ids: ids }, controller.signal), ids);
          if (!controller.signal.aborted) setShareStates((current) => ({ ...current, ...states }));
        } catch { break; }
      }
    })();
    return () => controller.abort();
  }, [history, sharing, shareRefresh]);

  const perform = async () => {
    if (!confirmation || busy) return;
    setBusy(true); setError('');
    try {
      if (confirmation === 'delete' && selected) await voiceCommand('history/delete', { session_id: selected.session_id });
      else if (confirmation === 'clear') await voiceCommand('history/clear', {});
      await savedHistory.refresh();
      setSelected(null); setConfirmation(null);
    } catch { setError('Could not update history. Please try again.'); }
    finally { setBusy(false); }
  };
  const askAgain = async () => {
    if (!selected || busy || asking.current || (!canAsk && !onCorrect)) return;
    asking.current = true;
    setBusy(true); setError('');
    try {
      const photo = selected.has_photo ? await prepareHistoryPhoto(parseHistoryPhoto(await voiceCommand('history/photo', { session_id: selected.session_id }))) : undefined;
      if (mounted.current && !document.hidden) {
        if (onCorrect) onCorrect(selected.session_id, selected.transcript, photo);
        else onAskAgain(photo);
      }
    } catch { if (mounted.current) setError('Could not load the attached photo. Please try again.'); }
    finally { asking.current = false; if (mounted.current) setBusy(false); }
  };
  const goBack = () => {
    if (busy) return;
    setError('');
    if (confirmation) setConfirmation(null);
    else if (selected) setSelected(null);
    else close();
  };
  if (sharing && selected) return <ShareScreen source={{ source_id: selected.session_id, question: selected.transcript, answer: selected.response, has_photo: !!selected.has_photo }} close={() => { setShareStates({}); setSharing(false); setShareRefresh((value) => value + 1); }} />;
  // What the confirmation may say is decided by the runtime's own policy fields,
  // not by `sage_backed` alone: a removal that keeps the photo as an original
  // must not be described as taking it off the device.
  const notice = confirmation ? removalNotice(confirmation, {
    sageBacked: history?.sage_backed === true,
    photoRemoval: history?.photo_removal,
    item: selected ?? undefined,
    photosAffected: history?.items.some((item) => item.has_photo) === true,
  }) : null;
  const badge = (id: string) => {
    const state = shareStates[id];
    if (state === 'not_shared') return null;
    return <span className={`device-history-public state-${state ?? 'unknown'}`}><ShareIcon /><span>{state ? communityLabel(state) : 'Public status unavailable'}</span></span>;
  };
  return <section className="device-subscreen" aria-label="Question history" onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader device-history-header"><button ref={back} disabled={busy} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><HistoryIcon /><h1>Past questions</h1></div></header>
    <div ref={content} className="device-subcontent" tabIndex={0} key={`${selected?.session_id || 'list'}:${confirmation}`}>
      {error && <p role="alert">{error}</p>}
      {historyError && <p role="status">{historyError} <button onClick={() => void savedHistory.refresh(true)}>Try again</button></p>}
      {confirmation ? <div className="device-confirm" role="alertdialog" aria-label="Confirm history change">
        <h2>{notice?.title}</h2>
        <p>{notice?.body}</p>
        <p>The latest answer may still be visible on the main screen.</p>
        <div className="device-actions"><button disabled={busy} onClick={() => setConfirmation(null)}>Cancel</button><button className="device-action-danger" disabled={busy} onClick={() => void perform()}>{busy ? 'Working…' : 'Remove'}</button></div>
      </div> : selected ? <><section className="device-panel"><span className="device-section-label">Your question · {new Date(selected.created_at).toLocaleString()}</span><h2>{selected.transcript}</h2></section><section className="device-panel"><span className="device-section-label">Saved answer</span>{selected.has_photo && <SavedPhoto key={selected.session_id} sessionId={selected.session_id} />}<p className="device-history-answer">{selected.response}</p></section><div className="device-actions device-history-actions"><button className="device-action-danger" disabled={busy} onClick={() => setConfirmation('delete')}>Delete question</button><button className="device-history-ask" disabled={busy || (!canAsk && !onCorrect)} onClick={() => void askAgain()}><MicrophoneIcon />{busy ? 'Preparing…' : 'Ask again'}</button></div></>
        : history ? <>
          <p className="device-muted">{history.enabled ? 'Saving is on' : 'Saving is off'} · Change in Menu → Settings</p>
          <p className="device-muted">Anyone using this device can read saved conversations.</p>
          {history.memory_warning && <p role="status">Remembering past conversations is temporarily unavailable.</p>}
          {!history.items.length && <div className="device-empty-state"><HistoryIcon /><h2>No saved questions yet</h2><p>{history.enabled ? 'Your next completed answer will appear here.' : 'Turn on saving in Settings to keep future questions and answers.'}</p></div>}
          {history.items.map((item, index) => <div key={item.session_id}>
            {(index === 0 || new Date(item.created_at).toDateString() !== new Date(history.items[index - 1].created_at).toDateString()) && <h3>{new Date(item.created_at).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })}</h3>}
            <button className="device-history-item" onClick={() => { setSelected(item); content.current?.scrollTo(0, 0); }}><span>{item.transcript || 'Voice question'}</span><small>{new Date(item.created_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}{item.has_photo && <span className="device-history-photo-label"><CameraIcon /> Photo attached</span>}</small></button>
            {badge(item.session_id)}
          </div>)}
          {!!history.items.length && <div className="device-actions"><button onClick={() => { setShareStates({}); setShareRefresh((value) => value + 1); }}><ShareIcon />Refresh public status</button><button className="device-action-danger" onClick={() => setConfirmation('clear')}>Clear all history</button></div>}
        </> : !error && !historyError && <p role="status">Loading history…</p>}
      {selected && !confirmation && <div className="device-history-sharing">{badge(selected.session_id)}<button className="device-share-answer" disabled={busy} onClick={() => setSharing(true)}><ShareIcon />Share this with others</button></div>}
    </div>
  </section>;
}
