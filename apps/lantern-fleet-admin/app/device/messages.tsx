'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { communityLabel, communityRequest, parseCommunityResult, parseCommunityStatus, parseContacts, parseMessages, parseRadioContacts, type CommunityContact, type CommunityMessage, type CommunityStatus } from '../../lib/community';
import { replaceParagraph } from '../../lib/message-dictation';
import { BackIcon, EnvelopeIcon, MicrophoneIcon, NetworkIcon, SendIcon } from './icons';
import NearbyDirectory from './nearby-directory';
import Dictation from './dictation';
import QuestionKeyboard from './question-keyboard';
import ReadReceiptControl from './read-receipt';
import { getDeviceToken } from '../../lib/device-session';
import { useDrawerNavigation } from './drawer-navigation';
import { messageIdentity, messageThreads, replyContact, type ThreadMessage, type MessageFolder } from '../../lib/message-threads';
import { receiptEligible, type ReadReceiptOutcome } from '../../lib/read-receipts';

export default function MessagesScreen({ close }: { close: () => void }) {
  const [status, setStatus] = useState<CommunityStatus | null>(null);
  const [directory, setDirectory] = useState(false);
  const [box, setBox] = useState<MessageFolder>('inbox');
  const [drawer, setDrawer] = useState(false);
  const drawerRef = useRef<HTMLElement>(null);
  const closeDrawer = useCallback(() => setDrawer(false), []);
  useDrawerNavigation(drawerRef, drawer, closeDrawer, true);
  const [threadSearch, setThreadSearch] = useState('');
  const edge = useRef<{ x: number; y: number } | null>(null);
  const [items, setItems] = useState<ThreadMessage[] | null>(null);
  const [selected, setSelected] = useState<CommunityMessage | null>(null);
  const [more, setMore] = useState(false);
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
        setSelected(null); setItems(null); setContacts([]); setParagraphs([]); setUndo(null); setRecipient(null);
        setRecording(null); setReview(false); setFilter(''); setReadOutcomes({}); setOutcome(null); setError('');
        requestId.current = null;
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
      try {
        const available = parseCommunityStatus(await communityRequest('status', {}, controller.signal));
        if (!controller.signal.aborted) setStatus(available);
        if (composing) {
          const result = parseContacts(await communityRequest('contacts/list', {}, controller.signal));
          if (!controller.signal.aborted) { setContacts(result.items); setMore(result.more); setContactsLoading(false); }
        } else {
          const inbox = { folder: 'inbox' as const, page: parseMessages(await communityRequest('messages/list', { box: 'inbox' }, controller.signal)) };
          const outbox = { folder: 'outbox' as const, page: parseMessages(await communityRequest('messages/list', { box: 'outbox' }, controller.signal)) };
          if (!controller.signal.aborted) { setItems([inbox, outbox].flatMap(({ folder, page }) => page.items.map((item) => ({ ...item, box: folder })))); setMore(inbox.page.more || outbox.page.more); }
        }
      } catch { if (!controller.signal.aborted) { setContactsLoading(false); setError(composing ? 'People unavailable. Please try again.' : 'Could not load this folder. Try Refresh.'); } }
    })();
    return () => controller.abort();
  }, [box, composing, refresh, directory]);

  const resetDraft = () => { setParagraphs([]); setUndo(null); setRecipient(null); setRecording(null); setReview(false); setDiscard(false); setOutcome(null); setOutcomeState('unknown'); setUnconfirmed(false); setFilter(''); setTypingName(false); requestId.current = null; };
  const goBack = () => {
    if (busy) return;
    detailRequest.current?.abort(); setOpening(false);
    setError('');
    if (discard) setDiscard(false);
    else if (recording) setRecording(null);
    else if (review && !outcome) setReview(false);
    else if (composing) { if (unconfirmed || paragraphs.length && !outcome) setDiscard(true); else { resetDraft(); setComposing(false); } }
    else if (selected) { setSelected(null); }
    else if (unconfirmed || composing && paragraphs.length && !outcome) setDiscard(true);
    else if (composing) { resetDraft(); setComposing(false); setItems(null); setRefresh((value) => value + 1); }
    else close();
  };
  const openMessage = async (message: CommunityMessage) => {
    detailRequest.current?.abort();
    const controller = new AbortController(); detailRequest.current = controller;
    setOpening(true); setError('');
    try {
      const result = parseCommunityResult(await communityRequest('messages/open', { id: message.id, share_read_receipt: false }, controller.signal), 'message');
      if (result.id !== message.id || result.created_at !== message.created_at) throw new Error('Message changed');
      const current = parseMessages({ items: [{ id: message.id, peer_name: message.peer_name, text: message.text, state: result.state, created_at: message.created_at }], more: false }).items[0];
      if (mounted.current && !controller.signal.aborted && detailRequest.current === controller) { setSelected(current); setDrawer(false); }
    } catch { if (mounted.current && !controller.signal.aborted) setError('Message unavailable. Refresh the folder and try again.'); }
    finally { if (mounted.current && detailRequest.current === controller) setOpening(false); }
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
      if (mounted.current) { setOutcome(communityLabel(result.state)); setOutcomeState(result.state); setUnconfirmed(result.state === 'unknown'); }
    } catch { if (mounted.current) { setOutcome('Sending status unconfirmed'); setOutcomeState('unknown'); setUnconfirmed(true); setError('Check Outbox before trying again. The message may already have been sent.'); } }
    finally { sending.current = false; if (mounted.current) setBusy(false); }
  };
  const beginReply = async () => {
    if (!selected || busy || opening || unconfirmed) return;
    const controller = new AbortController(); detailRequest.current?.abort(); detailRequest.current = controller;
    setOpening(true); setError('');
    try {
      const directory = parseRadioContacts(await communityRequest('contacts/radio', {}, controller.signal));
      const contact = replyContact(selected, directory.items);
      if (!contact) throw new Error('No current verified route');
      if (mounted.current && !controller.signal.aborted) { resetDraft(); setRecipient({ id: contact.id, name: contact.name }); setComposing(true); setDrawer(false); }
    } catch { if (!controller.signal.aborted) setError('No current verified contact for this SAGE key and network. Open People to check the connection.'); }
    finally { if (mounted.current && detailRequest.current === controller) setOpening(false); }
  };
  const threads = messageThreads(items || [], box, threadSearch);
  const identity = selected ? messageIdentity(selected) : null;
  const selectedMessages = selected ? (items || []).filter((item) => identity ? messageIdentity(item)?.id === identity.id : item.id === selected.id).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at)) : [];
  const visibleContacts = contacts.filter((contact) => contact.name.toLocaleLowerCase().includes(filter.toLocaleLowerCase()));
  if (directory) return <NearbyDirectory close={() => setDirectory(false)} />;
  return <section className={`device-subscreen device-community device-mail-layout${drawer ? ' device-mail-drawer-open' : ''}`} aria-label="Messages" onTouchStart={(event) => { const point = event.touches[0]; edge.current = point.clientX < 28 ? { x: point.clientX, y: point.clientY } : null; }} onTouchEnd={(event) => { const start = edge.current; edge.current = null; const point = event.changedTouches[0]; if (start && !busy && !composing && point.clientX - start.x > 70 && Math.abs(point.clientY - start.y) < 50) setDrawer(true); }} onKeyDown={(event) => { if (event.key === 'Escape') goBack(); }}>
    <header className="device-subheader"><button ref={back} disabled={busy} onClick={goBack}><BackIcon />Back</button><div className="device-page-title"><EnvelopeIcon /><h1>Messages</h1></div></header>
    {!composing && <button className="device-mail-folders" onClick={() => setDrawer(true)} aria-controls="device-message-folders" aria-expanded={drawer}>☰ Folders & threads</button>}
    {drawer && !composing && <button className="device-mail-scrim" aria-label="Close message folders" onClick={() => setDrawer(false)} />}
    <aside id="device-message-folders" ref={drawerRef} className="device-mail-sidebar" aria-label="Message folders and threads" inert={composing || busy}>
      <button className="device-mail-folders" onClick={() => setDrawer(false)}>Close folders</button><div className="device-community-tabs" role="group" aria-label="Message folders">{(['inbox', 'outbox', 'sent'] as const).map((name) => <button key={name} aria-pressed={box === name} onClick={() => { detailRequest.current?.abort(); setOpening(false); setBox(name); setSelected(null); setError(''); }}>{name === 'inbox' ? 'Inbox' : name === 'sent' ? 'Sent' : 'Outbox'}</button>)}</div>
      <input className="device-thread-search" type="search" aria-label="Search message keys, networks and text" placeholder="Search key, network or text" value={threadSearch} onChange={(event) => setThreadSearch(event.target.value)} />
      <nav aria-label="Message threads">{threads.map((thread) => { const last = thread.messages.at(-1)!; return <button className="device-community-row" key={thread.id} aria-current={thread.id === identity?.id ? 'page' : undefined} onClick={() => void openMessage(last)}><strong>{thread.identity ? `${thread.identity.key.slice(0, 10)}…${thread.identity.key.slice(-6)}` : 'Unresolved sender'}</strong><small>{thread.identity?.network || 'Network unavailable'}</small><span>{last.text.slice(0, 90)}</span></button>; })}</nav>
      {items === null ? <p role="status">Loading threads…</p> : !threads.length && <p>No matching threads in this folder.</p>}
      {more && <p>Latest 20 messages per folder are loaded.</p>}
    </aside>
    <div className="device-subcontent" tabIndex={0} inert={drawer && !composing}>
      {error && <p role="alert">{error}</p>}
      {discard ? <section className="device-panel"><h2>Discard this draft?</h2><p>{unconfirmed ? 'It may already have been sent. Discarding this draft will not cancel delivery.' : 'It has not been sent.'}</p><div className="device-community-actions"><button onClick={() => setDiscard(false)}>Keep draft</button><button onClick={() => { resetDraft(); setComposing(false); }}>Discard draft</button></div></section>
        : selected && !composing ? <article className="device-message-thread"><header><h2>{identity ? `${identity.key.slice(0, 10)}…${identity.key.slice(-6)}` : selected.peer_name}</h2><p className="device-contact-id">SAGE key: {identity?.key || 'Unresolved'}<br />Network: {identity?.network || 'Unavailable'}</p></header>{selectedMessages.map((message) => <section className={`device-message-bubble device-message-${message.box}`} key={message.id}><h3>{message.box === 'outbox' ? 'You' : 'Sender'}</h3><p className="device-message-body">{message.text}</p><small>{new Date(message.created_at).toLocaleString()} · {communityLabel(message.state)}</small>{message.box === 'inbox' && message.id === selected.id && receiptEligible(message.id) && <ReadReceiptControl key={message.id} id={message.id} prior={readOutcomes[message.id]} onOutcome={(outcome) => setReadOutcomes((current) => ({ ...current, [outcome.id]: outcome }))} />}</section>)}<button className="device-community-primary" disabled={busy || opening || unconfirmed || !identity || !status?.messaging.available} onClick={() => void beginReply()}>Reply</button></article>
          : composing ? <>
            {outcome ? <section className="device-panel"><h2 className={`device-community-badge state-${outcomeState}`} role="status">{outcome}</h2><p>{recipient?.name}</p>{unconfirmed && <p>Your draft is kept here until you discard it or lock the device. Do not send a duplicate while the outcome is unknown.</p>}<button onClick={() => { if (!unconfirmed) resetDraft(); setComposing(false); setBox('outbox'); setItems(null); setRefresh((value) => value + 1); }}>View Outbox</button></section>
              : recording ? <Dictation title={recording.kind === 'name' ? 'Say a contact’s name' : recording.index === null ? 'Record a paragraph' : 'Re-record this paragraph'} close={() => setRecording(null)} onText={acceptText} />
                : review ? <section className="device-panel"><h2>Send to {recipient?.name}?</h2><p className="device-contact-id">{recipient?.id}</p><p className="device-message-body">{paragraphs.join('\n\n')}</p><div className="device-community-actions"><button disabled={busy} onClick={() => setReview(false)}>Edit</button><button disabled={busy || !status?.messaging.available} className="device-community-primary" onClick={() => void send()}><SendIcon />{busy ? 'Sending…' : 'Send message'}</button></div></section>
                  : <>
                    <p className="device-muted">Draft clears when this device locks.</p>
                    <section className="device-panel"><h2>{recipient ? `To: ${recipient.name}` : 'Choose a person'}</h2>{recipient ? <><p className="device-contact-id">{recipient.id}</p><button onClick={() => { setRecipient(null); requestId.current = null; }}>Change person</button></> : <>
                      <>
                        {typingName ? <QuestionKeyboard initialText={filter} label="Name in loaded contacts" submitLabel="Find" suggestions={[]} onChange={() => {}} onCancel={() => setTypingName(false)} onSubmit={(text) => { setFilter(text); setTypingName(false); }} canSubmit /> : <><button onClick={() => setTypingName(true)}>Type a name</button><button disabled={!status?.dictation.available} onClick={() => setRecording({ kind: 'name', index: null })}><MicrophoneIcon />Say their name</button></>}
                        {filter && <p>{filter} <button onClick={() => setFilter('')}>Clear search</button></p>}
                        {visibleContacts.map((contact) => <button className="device-community-row" key={contact.id} onClick={() => { setRecipient(contact); requestId.current = null; }}><strong>{contact.name}</strong><small>{contact.id}</small></button>)}
                        {contactsLoading ? <p role="status">Loading people…</p> : !error && !visibleContacts.length && <p>No matches in loaded contacts.</p>}{more && <p>Showing the first 20 contacts.</p>}
                      </>
                    </>}</section>
                    {paragraphs.map((paragraph, index) => <section className="device-panel" key={index}><p className="device-message-body">{paragraph}</p><button disabled={!status?.dictation.available} onClick={() => setRecording({ kind: 'paragraph', index })}>Re-record paragraph {index + 1}</button></section>)}
                    <div className="device-community-actions"><button disabled={!status?.dictation.available || paragraphs.length >= 32} onClick={() => setRecording({ kind: 'paragraph', index: null })}><MicrophoneIcon />{paragraphs.length ? 'Add paragraph' : 'Record message'}</button>{undo && <button onClick={() => { setParagraphs(undo); setUndo(null); requestId.current = null; }}>Undo</button>}<button className="device-community-primary" disabled={!recipient || !paragraphs.length || !status?.messaging.available} onClick={() => setReview(true)}>Review</button></div>
                  </>}
          </> : <section className="device-mail-inbox" aria-label={`${box} threads`}>
            <header><h2>{box === 'inbox' ? 'Inbox' : box === 'sent' ? 'Sent' : 'Outbox'}</h2><p>{items === null ? 'Loading…' : `${threads.length} ${threads.length === 1 ? 'thread' : 'threads'}`}</p></header>
            {items === null ? <p role="status">Loading messages…</p> : !threads.length ? <div className="device-message-empty"><EnvelopeIcon /><h2>No messages</h2><p>Messages are grouped by SAGE key and network.</p></div> : <div className="device-mail-thread-list">{threads.map((thread) => {
              const last = thread.messages.at(-1)!;
              return <button className="device-community-row" key={thread.id} onClick={() => void openMessage(last)}><strong>{thread.identity ? `${thread.identity.key.slice(0, 10)}…${thread.identity.key.slice(-6)}` : 'Unresolved sender'}</strong><small>{thread.identity?.network || 'Network unavailable'}</small><span>{last.text.slice(0, 90)}</span></button>;
            })}</div>}
            {more && <p>Latest 20 messages per folder are loaded.</p>}
          </section>}
    </div>
    {!composing && !discard && <footer className="device-community-actions" inert={drawer && !composing}><button onClick={() => { detailRequest.current?.abort(); setOpening(false); setDirectory(true); }}><NetworkIcon />People</button><button onClick={() => { detailRequest.current?.abort(); setOpening(false); setError(''); setRefresh((value) => value + 1); }}>Refresh</button><button className="device-community-primary" disabled={!status?.dictation.available && !unconfirmed} onClick={() => { detailRequest.current?.abort(); setOpening(false); if (!unconfirmed) resetDraft(); setError(''); setComposing(true); setSelected(null); setDrawer(false); }}><MicrophoneIcon />{unconfirmed ? 'Unconfirmed draft' : 'New message'}</button></footer>}
  </section>;
}
