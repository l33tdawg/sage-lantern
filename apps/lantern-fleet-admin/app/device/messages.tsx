'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { communityRequest, parseCommunityResult, parseCommunityStatus, parseContacts, parseMessages, parseRadioContacts, type CommunityContact, type CommunityStatus, type MessageState, type RadioContact } from '../../lib/community';
import { replaceParagraph } from '../../lib/message-dictation';
import { BackIcon, EnvelopeIcon, MicrophoneIcon, NetworkIcon, SendIcon } from './icons';
import NearbyDirectory from './nearby-directory';
import Dictation from './dictation';
import QuestionKeyboard from './question-keyboard';
import ReadReceiptControl from './read-receipt';
import { getDeviceToken } from '../../lib/device-session';
import { useDrawerNavigation } from './drawer-navigation';
import { contactNames, deliveryLabel, folderItems, folderNotice, folderUnavailable, loadMessageFolders, messageThreads, outgoingLabel, previewPrefix, replyContact, type MessageFolder, type MessageThread, type ThreadMessage } from '../../lib/message-threads';
import { receiptEligible, type ReadReceiptOutcome } from '../../lib/read-receipts';

function stamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return '';
  return parsed.toDateString() === new Date().toDateString()
    ? parsed.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : parsed.toLocaleDateString([], { month: 'short', day: 'numeric' });
}

export default function MessagesScreen({ close }: { close: () => void }) {
  const [status, setStatus] = useState<CommunityStatus | null>(null);
  const [directory, setDirectory] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);
  const closeDrawer = useCallback(() => setDrawer(false), []);
  useDrawerNavigation(drawerRef, drawer, closeDrawer, true);
  const [threadSearch, setThreadSearch] = useState('');
  const edge = useRef<{ x: number; y: number } | null>(null);
  const [items, setItems] = useState<ThreadMessage[] | null>(null);
  const [unavailable, setUnavailable] = useState<MessageFolder[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [seen, setSeen] = useState<ReadonlySet<string>>(() => new Set<string>());
  const [radio, setRadio] = useState<RadioContact[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [composing, setComposing] = useState(false);
  const [contacts, setContacts] = useState<CommunityContact[]>([]);
  const [contactsLoading, setContactsLoading] = useState(false);
  const [recipient, setRecipient] = useState<CommunityContact | null>(null);
  const [filter, setFilter] = useState('');
  const [typingName, setTypingName] = useState(false);
  const [paragraphs, setParagraphs] = useState<string[]>([]);
  const [undo, setUndo] = useState<string[] | null>(null);
  const [recording, setRecording] = useState<{ kind: 'name' | 'paragraph'; index: number | null } | null>(null);
  const [review, setReview] = useState(false);
  const [discard, setDiscard] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [outcome, setOutcome] = useState<string | null>(null);
  const [outcomeState, setOutcomeState] = useState('unknown');
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [readOutcomes, setReadOutcomes] = useState<Record<string, ReadReceiptOutcome>>({});
  const [opening, setOpening] = useState(false);
  const detailRequest = useRef<AbortController | null>(null);
  const mounted = useRef(false);
  const sending = useRef(false);
  const requestId = useRef<string | null>(null);
  const back = useRef<HTMLButtonElement>(null);

  useEffect(() => { mounted.current = true; back.current?.focus(); return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    const token = getDeviceToken();
    const clear = () => {
      if (token !== getDeviceToken() || !getDeviceToken() || document.hidden) {
        detailRequest.current?.abort();
        setItems(null); setContacts([]); setParagraphs([]); setUndo(null); setRecipient(null);
        setRecording(null); setReview(false); setFilter(''); setReadOutcomes({}); setOutcome(null); setError('');
        setSelectedId(null); setComposing(false); setUnavailable([]); requestId.current = null;
      }
    };
    const timer = setInterval(clear, 250);
    document.addEventListener('visibilitychange', clear);
    return () => { clearInterval(timer); document.removeEventListener('visibilitychange', clear); detailRequest.current?.abort(); };
  }, []);
  useEffect(() => {
    if (directory) return;
    const controller = new AbortController();
    void (async () => {
      setError('');
      setContactsLoading(composing);
      let failure = '';
      try {
        const available = parseCommunityStatus(await communityRequest('status', {}, controller.signal));
        if (!controller.signal.aborted) setStatus(available);
      } catch { failure = 'Community status unavailable. Try Refresh.'; }
      if (composing) {
        try {
          const result = parseContacts(await communityRequest('contacts/list', {}, controller.signal));
          if (!controller.signal.aborted) setContacts(result.items);
        } catch { failure ||= 'People unavailable. Please try again.'; }
        finally { if (!controller.signal.aborted) setContactsLoading(false); }
      } else {
        const loads = await loadMessageFolders((box) => communityRequest('messages/list', { box }, controller.signal).then(parseMessages));
        if (!controller.signal.aborted) { setItems(folderItems(loads)); setUnavailable(folderUnavailable(loads)); }
      }
      try {
        const peers = parseRadioContacts(await communityRequest('contacts/radio', {}, controller.signal));
        if (!controller.signal.aborted) setRadio(peers.items);
      } catch { failure ||= 'Radio contacts unavailable. Try Refresh.'; }
      if (failure && !controller.signal.aborted) setError(failure);
    })();
    return () => controller.abort();
  }, [composing, refresh, directory]);

  const names = contactNames(radio);
  const allThreads = messageThreads(items || [], { names });
  const threads = messageThreads(items || [], { names, search: threadSearch, seen });
  const selected = selectedId ? allThreads.find((thread) => thread.id === selectedId) || null : null;
  const lastInbound = selected ? [...selected.messages].reverse().find((message) => message.box === 'inbox') || null : null;
  const route = lastInbound ? replyContact(lastInbound, radio) : null;

  const resetDraft = () => { setParagraphs([]); setUndo(null); setRecipient(null); setRecording(null); setReview(false); setDiscard(false); setOutcome(null); setOutcomeState('unknown'); setUnconfirmed(false); setFilter(''); setTypingName(false); requestId.current = null; };
  const markSeen = (thread: MessageThread) => setSeen((current) => new Set([...current, ...thread.messages.map((message) => message.id)]));
  const openThread = async (thread: MessageThread) => {
    markSeen(thread);
    setSelectedId(thread.id);
    setComposing(false);
    setDrawer(false);
    setError('');
    const inbound = [...thread.messages].reverse().find((message) => message.box === 'inbox');
    if (!inbound) return;
    detailRequest.current?.abort();
    const controller = new AbortController(); detailRequest.current = controller;
    setOpening(true);
    try {
      const result = parseCommunityResult(await communityRequest('messages/open', { id: inbound.id, share_read_receipt: false }, controller.signal), 'message');
      if (result.id === inbound.id && mounted.current && !controller.signal.aborted) {
        const state = result.state as MessageState;
        setItems((current) => (current || []).map((item) => item.id === inbound.id ? { ...item, state } : item));
      }
    } catch { if (mounted.current && !controller.signal.aborted) setError('Message unavailable. Refresh and try again.'); }
    finally { if (mounted.current && detailRequest.current === controller) setOpening(false); }
  };
  const beginReply = () => {
    if (!lastInbound || busy || opening || unconfirmed) return;
    const contact = replyContact(lastInbound, radio);
    if (!contact) { setError('No verified radio route for this SAGE key and network right now. Open People to check the connection.'); return; }
    resetDraft();
    setRecipient({ id: contact.id, name: contact.name });
    setComposing(true);
  };
  const goBack = () => {
    if (busy) return;
    detailRequest.current?.abort(); setOpening(false);
    setError('');
    if (discard) setDiscard(false);
    else if (recording) setRecording(null);
    else if (review && !outcome) setReview(false);
    else if (composing) { if (unconfirmed || paragraphs.length && !outcome) setDiscard(true); else { resetDraft(); setComposing(false); setRefresh((value) => value + 1); } }
    else if (selectedId && !drawer) setSelectedId(null);
    else if (drawer) setDrawer(false);
    else close();
  };
  const acceptText = (text: string) => {
    if (!recording) return;
    try {
      if (recording.kind === 'name') setFilter(text);
      else { setParagraphs(replaceParagraph(paragraphs, recording.index, text)); setUndo(paragraphs); requestId.current = null; }
      setRecording(null); setReview(false); setError('');
    } catch { setError('That message is too long. Record a shorter paragraph.'); }
  };
  const send = async () => {
    if (sending.current || !recipient || !paragraphs.length || !review || outcome || !status?.messaging.available) return;
    sending.current = true; setBusy(true); setError('');
    requestId.current ??= crypto.randomUUID();
    try {
      const result = parseCommunityResult(await communityRequest('messages/send', { recipient_id: recipient.id, paragraphs, request_id: requestId.current, consent: true }), 'message');
      if (mounted.current) { setOutcome(outgoingLabel(result.state as MessageState)); setOutcomeState(result.state); setUnconfirmed(result.state === 'unknown'); }
    } catch { if (mounted.current) { setOutcome('Sending status unconfirmed'); setOutcomeState('unknown'); setUnconfirmed(true); setError('Check the chat before trying again. The message may already have been sent.'); } }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const visibleContacts = contacts.filter((contact) => contact.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  if (directory) return <NearbyDirectory close={() => setDirectory(false)} />;

  const composer = composing ? <>
    {outcome ? <section className="device-panel"><h2 className={`device-community-badge state-${outcomeState}`} role="status">{outcome}</h2><p>{recipient?.name}</p>{unconfirmed && <p>Your draft is kept here until you discard it or lock the device. Do not send a duplicate while the outcome is unknown.</p>}<button onClick={() => { if (!unconfirmed) resetDraft(); setComposing(false); setRefresh((value) => value + 1); }}>Back to chats</button></section>
      : recording ? <Dictation title={recording.kind === 'name' ? 'Say a contact’s name' : recording.index === null ? 'Record a paragraph' : 'Re-record this paragraph'} close={() => setRecording(null)} onText={acceptText} />
        : review ? <section className="device-panel"><h2>Send to {recipient?.name}?</h2><p className="device-contact-id">{recipient?.id}</p><p className="device-message-body">{paragraphs.join('\n\n')}</p><div className="device-community-actions"><button disabled={busy} onClick={() => setReview(false)}>Edit</button><button disabled={busy || !status?.messaging.available} className="device-community-primary" onClick={() => void send()}><SendIcon />{busy ? 'Sending…' : 'Send'}</button></div></section>
          : <section className="device-panel device-chat-compose">
            <h2 className="device-chat-window-head">{recipient ? `To: ${recipient.name}` : 'New chat'}</h2>
            {recipient ? <p className="device-contact-id">{recipient.id} <button onClick={() => { setRecipient(null); requestId.current = null; }}>Change</button></p> : <>
              {typingName ? <QuestionKeyboard initialText={filter} label="Name in loaded contacts" submitLabel="Find" suggestions={[]} onChange={() => {}} onCancel={() => setTypingName(false)} onSubmit={(text) => { setFilter(text); setTypingName(false); }} canSubmit /> : <div className="device-community-actions"><button onClick={() => setTypingName(true)}>Type a name</button><button disabled={!status?.dictation.available} onClick={() => setRecording({ kind: 'name', index: null })}><MicrophoneIcon />Say their name</button></div>}
              {filter && <p>{filter} <button onClick={() => setFilter('')}>Clear</button></p>}
              {visibleContacts.map((contact) => <button className="device-community-row" key={contact.id} onClick={() => { setRecipient(contact); requestId.current = null; }}><strong>{contact.name}</strong><small>{contact.id}</small></button>)}
              {contactsLoading ? <p role="status">Loading people…</p> : !error && !visibleContacts.length && <p>No matches in loaded contacts.</p>}
            </>}
            {paragraphs.map((paragraph, index) => <section className="device-panel" key={index}><p className="device-message-body">{paragraph}</p><button disabled={!status?.dictation.available} onClick={() => setRecording({ kind: 'paragraph', index })}>Re-record paragraph {index + 1}</button></section>)}
            <div className="device-community-actions"><button disabled={!status?.dictation.available || paragraphs.length >= 32} onClick={() => setRecording({ kind: 'paragraph', index: null })}><MicrophoneIcon />{paragraphs.length ? 'Add paragraph' : 'Record message'}</button>{undo && <button onClick={() => { setParagraphs(undo); setUndo(null); requestId.current = null; }}>Undo</button>}<button className="device-community-primary" disabled={!recipient || !paragraphs.length || !status?.messaging.available} onClick={() => setReview(true)}>Review</button></div>
            <p className="device-muted">Draft clears when this device locks.</p>
          </section>}
  </> : null;

  return <section className={`device-subscreen device-community device-chat-mail${drawer ? ' device-show-chats' : ''}`} aria-label="Messages" onTouchStart={(event) => { const point = event.touches[0]; edge.current = point.clientX < 28 ? { x: point.clientX, y: point.clientY } : null; }} onTouchEnd={(event) => { const start = edge.current; edge.current = null; const point = event.changedTouches[0]; if (start && !busy && !composing && point.clientX - start.x > 70 && Math.abs(point.clientY - start.y) < 50) setDrawer(true); }} onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader"><button ref={back} disabled={busy} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><EnvelopeIcon /><h1>Chats</h1></div>{!composing && <button className="device-mail-folders" onClick={() => setDrawer(true)} aria-controls="device-chat-threads" aria-expanded={drawer}>☰ People</button>}</header>
    {drawer && !composing && <button className="device-chat-scrim" aria-label="Close chats" onClick={() => setDrawer(false)} />}
    <aside id="device-chat-threads" ref={drawerRef} className="device-chat-list device-chat-mail-list" aria-label="Chats with people" inert={composing || busy}>
      <div className="device-drawer-heading"><h2>People</h2><button className="device-chats-back" onClick={() => setDrawer(false)}>Close</button></div>
      <input className="device-thread-search" type="search" aria-label="Search chats" placeholder="Search a person or a message" value={threadSearch} onChange={(event) => setThreadSearch(event.target.value)} />
      {unavailable.map((box) => <p role="status" key={box}>{folderNotice(box)}</p>)}
      {items === null ? <p role="status">Loading chats…</p> : !threads.length ? !unavailable.length && <p>No chats yet.</p> : <nav aria-label="Chats">{threads.map((thread) => <button key={thread.id} aria-current={thread.id === selectedId ? 'page' : undefined} onClick={() => void openThread(thread)}><strong>{thread.title}</strong><small>{previewPrefix(thread.last)}{thread.last.text.slice(0, 80)}</small><span className="device-chat-stamp">{stamp(thread.last.created_at)}</span>{thread.unread > 0 && <span className="device-chat-unread" aria-label={`${thread.unread} unread`}>{thread.unread}</span>}</button>)}</nav>}
      <div className="device-community-actions"><button onClick={() => { detailRequest.current?.abort(); setOpening(false); setDirectory(true); }}><NetworkIcon />People</button><button onClick={() => { detailRequest.current?.abort(); setOpening(false); setError(''); setRefresh((value) => value + 1); }}>Refresh</button><button className="device-community-primary" disabled={!status?.dictation.available && !status?.messaging.available} onClick={() => { resetDraft(); setError(''); setComposing(true); setSelectedId(null); setDrawer(false); }}>New chat</button></div>
    </aside>
    <div className="device-main device-chat-window" tabIndex={0} inert={drawer && !composing}>
      {error && <p role="alert">{error}</p>}
      {discard ? <section className="device-panel"><h2>Discard this draft?</h2><p>{unconfirmed ? 'It may already have been sent. Discarding this draft will not cancel delivery.' : 'It has not been sent.'}</p><div className="device-community-actions"><button onClick={() => setDiscard(false)}>Keep draft</button><button onClick={() => { resetDraft(); setComposing(false); setRefresh((value) => value + 1); }}>Discard</button></div></section>
        : composer || (selected && <article className="device-message-thread" aria-label={`Chat with ${selected.title}`}>
          <header className="device-chat-window-head"><h2>{selected.title}</h2><p className="device-contact-id">{selected.identity ? `SAGE key: ${selected.identity.key}\nNetwork: ${selected.identity.network || '—'}` : 'Unresolved sender'}</p><p className="device-chat-route">{route ? 'Radio route · verified contact card' : 'No verified radio route right now'}</p></header>
          <div className="device-chat-exchanges">{selected.messages.map((message) => <section className={`device-chat-bubble${message.box === 'outbox' ? ' device-chat-mine' : ''}`} key={message.id}><p className="device-message-body">{message.text}</p><small>{stamp(message.created_at)} · {deliveryLabel(message)}</small>{message.box === 'inbox' && message.id === lastInbound?.id && receiptEligible(message.id) && <ReadReceiptControl key={message.id} id={message.id} prior={readOutcomes[message.id]} onOutcome={(value) => setReadOutcomes((current) => ({ ...current, [value.id]: value }))} />}</section>)}</div>
          <footer className="device-community-actions"><button disabled={busy || opening || unconfirmed || !lastInbound || !selected.identity || !status?.messaging.available} onClick={beginReply}><SendIcon />Reply</button>{opening && <span role="status">Checking route…</span>}</footer>
        </article>)
        || <section className="device-message"><h1>Chats</h1>{!threads.length && unavailable.length ? unavailable.map((box) => <p role="status" key={box}>{folderNotice(box)}</p>) : <p>Pick a person on the left, or start a new chat. Each line shows what actually happened: not delivered yet, sent, delivered or read.</p>}</section>}
    </div>
  </section>;
}
