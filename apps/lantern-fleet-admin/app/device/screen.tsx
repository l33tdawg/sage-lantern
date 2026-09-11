'use client';

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { answerIsVisible, deviceView, parseDeviceStatus, statusIsFresh, waitingQuestion, type DeviceStatus } from '../../lib/device-state';
import './screen.css';
import './community.css';
import './threads.css';
import { useDrawerNavigation } from './drawer-navigation';
import { ChatExchanges, useConversations } from './conversations';
import CameraScreen from './camera';
import HistoryScreen from './history';
import NearbyScreen from './nearby';
import AboutScreen from './about';
import SettingsScreen from './settings';
import MessagesScreen from './messages';
import ShareScreen from './share';
import type { ShareSource } from '../../lib/community';
import { voiceCommand } from '../../lib/voice-control';
import { deviceSessionHeaders } from '../../lib/device-session';
import type { DeviceProfile } from '../../lib/device-profile';
import { CameraIcon, LanternIcon, SettingsIcon, HistoryIcon, InfoIcon, LockIcon, NetworkIcon, EnvelopeIcon, ShareIcon } from './icons';
import RadioSignal from './radio-signal';
import TalkControls from './talk';
import AnswerWait from './answer-wait';
import QuestionTurn from './question-turn';
import { handoffQuestion, type QuestionHandoff } from '../../lib/question-flow';
import QuestionCorrection from './question-correction';
import QuestionReview from './question-review';
import SuggestedQuestion from './suggested-question';
import { CorrectionAttempts } from '../../lib/question-correction';
import Image from 'next/image';
import { savedHistory } from '../../lib/saved-history';
import { photoForSession, type PhotoAttachment, type SessionPhoto } from '../../lib/photo-preview';

export default function DeviceScreen({ onLock, profile, onProfileChange }: { onLock?: () => void; profile?: DeviceProfile; onProfileChange?: (profile: DeviceProfile) => void } = {}) {
  const [status, setStatus] = useState<DeviceStatus | null>(null);
  const [connected, setConnected] = useState(false);
  const chats = useConversations(connected, status?.voice.phase === 'complete' ? status.voice.session_id : undefined);
  const [showChats, setShowChats] = useState(false);
  useEffect(() => { if (showChats) void savedHistory.refresh(); }, [showChats]);
  const chatDrawer = useRef<HTMLElement>(null);
  const closeChats = useCallback(() => setShowChats(false), []);
  const narrowChats = useDrawerNavigation(chatDrawer, showChats, closeChats);
  const [chatSearch, setChatSearch] = useState('');
  const edgeSwipe = useRef<{ x: number; y: number } | null>(null);
  const [page, setPage] = useState<'main' | 'camera' | 'history' | 'status' | 'about' | 'settings' | 'messages' | 'sharing'>('main');
  const [shareSource, setShareSource] = useState<ShareSource | undefined>(undefined);
  const [clearedSession, setClearedSession] = useState<string | null>(null);
  const [clearing, setClearing] = useState(false);
  const [clearError, setClearError] = useState('');
  const [photo, setPhoto] = useState<PhotoAttachment | undefined>(undefined);
  const [photoStart, setPhotoStart] = useState<string | undefined>(undefined);
  const [sessionPhoto, setSessionPhoto] = useState<SessionPhoto | undefined>(undefined);
  const [menuOpen, setMenuOpen] = useState(false);
  const [touchBusy, setTouchBusy] = useState(false);
  const [correction, setCorrection] = useState<{ sourceId: string; question: string; photo?: PhotoAttachment; historyPhoto?: boolean; fromHistory?: boolean } | null>(null);
  const [correctionAttempts] = useState(() => new CorrectionAttempts());
  const readingPosition = useRef(0);
  const followLatest = useRef(true);
  const [scrollRequest, setScrollRequest] = useState(0);
  const showLatest = () => {
    followLatest.current = true;
    setScrollRequest((request) => request + 1);
  };
  const menuButton = useRef<HTMLButtonElement>(null);
  const cameraButton = useRef<HTMLButtonElement>(null);
  const historyButton = useRef<HTMLButtonElement>(null);
  const messagesButton = useRef<HTMLButtonElement>(null);
  const shareButton = useRef<HTMLButtonElement>(null);
  const menuClose = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const reader = useRef<HTMLDivElement>(null);
  const session = useRef<string | undefined>(undefined);
  const reviewRetry = useRef<{ sessionId: string; photo?: PhotoAttachment } | null>(null);
  const [questionHandoff, setQuestionHandoff] = useState<QuestionHandoff | null>(null);
  const [reviewActions, setReviewActions] = useState<HTMLDivElement | null>(null);
  const [suggestedDraft, setSuggestedDraft] = useState<{ sourceId: string; question: string } | null>(null);

  useEffect(() => {
    let disposed = false;
    let nextPoll: ReturnType<typeof setTimeout>;
    let controller: AbortController;
    const poll = async () => {
      controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 4000);
      try {
        const response = await fetch('http://127.0.0.1:3199/status', { cache: 'no-store', signal: controller.signal, headers: deviceSessionHeaders() });
        if (!response.ok) throw new Error('Status unavailable');
        const next = parseDeviceStatus(await response.json());
        if (!statusIsFresh(next)) throw new Error('Status is stale');
        if (!disposed) {
          if (next.voice.session_id !== session.current) {
            session.current = next.voice.session_id;
            setQuestionHandoff(null);
            setSuggestedDraft(null);
            setPhoto(undefined); setPhotoStart(undefined); setPage((current) => current === 'messages' || current === 'sharing' ? current : 'main'); setMenuOpen(false);
            setSessionPhoto((current) => current?.sessionId === next.voice.session_id ? current : undefined);
            readingPosition.current = 0; followLatest.current = true;
          }
          setStatus(next); setConnected(true);
        }
      } catch { if (!disposed) { setConnected(false); setQuestionHandoff(null); setCorrection(null); setSuggestedDraft(null); } }
      finally { clearTimeout(deadline); if (!disposed) nextPoll = setTimeout(poll, 700); }
    };
    void poll();
    return () => { disposed = true; clearTimeout(nextPoll); controller?.abort(); };
  }, []);

  useEffect(() => { if (menuOpen) menuClose.current?.focus({ preventScroll: true }); }, [menuOpen]);
  useEffect(() => {
    const hide = () => { if (document.hidden) { setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined); setQuestionHandoff(null); setSuggestedDraft(null); } };
    document.addEventListener('visibilitychange', hide);
    return () => document.removeEventListener('visibilitychange', hide);
  }, []);
  const inCurrentChat = !chats.current || status?.voice.session_id === chats.current.active_session_id || chats.current.items.some((item) => item.session_id === status?.voice.session_id);
  // The voice poll leads the independent chat poll during a new turn. Keep the
  // in-flight question visible instead of flashing an empty/ready screen.
  const voiceInFlight = !!status && (['listening', 'review', 'processing'].includes(status.voice.phase)
    || (status.voice.phase === 'complete' && questionHandoff?.sessionId === status.voice.session_id));
  const chatStatus = status && !inCurrentChat && !voiceInFlight ? { ...status, voice: { ...status.voice, phase: 'idle', transcript: '', response: '' } } : status;
  const visibleStatus = chatStatus && clearedSession && chatStatus.voice.session_id === clearedSession ? { ...chatStatus, voice: { ...chatStatus.voice, phase: 'idle', transcript: '', response: '' } } : chatStatus;
  const view = deviceView(visibleStatus, connected);
  // Recording belongs to the current voice session even while the chat poll
  // still contains the previous active_session_id. It exposes no prior text.
  const recordingView = deviceView(status, connected);
  const recording = recordingView.phase === 'listening';
  const reviewing = recordingView.phase === 'review';
  const answerVisible = answerIsVisible(visibleStatus, connected);
  const toolbarWaiting = view.phase === 'processing' && !answerVisible;
  const question = waitingQuestion(visibleStatus, connected);
  const currentQuestion = handoffQuestion(visibleStatus, connected, questionHandoff) || question || (answerVisible ? visibleStatus?.voice.transcript : null) || null;
  const turnVisible = recording || reviewing || view.phase === 'processing' || answerVisible;
  const displayedPhoto = photo || (connected ? photoForSession(sessionPhoto, status?.voice.session_id) : undefined);
  const text = status?.voice.response || '';
  const draft = connected && view.phase === 'complete' && suggestedDraft?.sourceId === visibleStatus?.voice.session_id ? suggestedDraft : null;
  const suggestedQuestions = view.phase === 'complete' ? visibleStatus?.voice.follow_up_questions || [] : [];

  // Submit resumes following even if the user was reading an older exchange.
  // Apply after the new layout, before paint; passive scrolling can otherwise
  // show the old position or mistake layout changes for a scroll away.
  useLayoutEffect(() => {
    const element = reader.current;
    if (!element) return;
    if (followLatest.current) element.scrollTop = element.scrollHeight;
    else element.scrollTop = readingPosition.current;
  }, [scrollRequest, text, currentQuestion, page, correction, answerVisible, recording, reviewing, draft, status?.voice.phase, status?.voice.session_id, chats.current?.thread_id, chats.current?.items.length]);

  const closeMenu = () => { setMenuOpen(false); requestAnimationFrame(() => menuButton.current?.focus()); };
  const goBack = () => { setPage('main'); requestAnimationFrame(() => (page === 'messages' ? messagesButton.current : page === 'sharing' && shareSource ? shareButton.current : page === 'history' ? historyButton.current : menuButton.current)?.focus()); };
  const busy = clearing || chats.selecting || touchBusy || recording || reviewing || !!draft || view.phase === 'processing';
  const canShareAnswer = connected && !busy && view.phase === 'complete' && !!status?.voice.session_id;
  const canAskAgain = connected && !!status?.voice.touch_ready && !busy && ['ready', 'complete', 'error'].includes(view.phase);
  useEffect(() => {
    const retry = reviewRetry.current;
    if (!retry || !connected || status?.voice.session_id !== retry.sessionId || status.voice.phase !== 'idle') return;
    const frame = requestAnimationFrame(() => {
      reviewRetry.current = null;
      setPhoto(retry.photo); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID()); setPage('main');
    });
    return () => cancelAnimationFrame(frame);
  }, [connected, status]);
  const editQuestion = !busy && view.phase === 'complete' && status?.voice.correction_ready === true && connected && visibleStatus?.voice.session_id && visibleStatus.voice.transcript.trim() && (!status.voice.has_photo || displayedPhoto)
    ? () => setCorrection({ sourceId: visibleStatus.voice.session_id!, question: visibleStatus.voice.transcript, photo: displayedPhoto }) : undefined;
  const askAgain = (attachment?: PhotoAttachment) => {
    if (!canAskAgain) return;
    setPhoto(attachment); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID());
    setPage('main'); setClearError('');
    if (status?.voice.session_id) setClearedSession(status.voice.session_id);
    readingPosition.current = 0;
  };
  const clearAnswer = async () => {
    if (busy) return;
    const current = status?.voice.session_id;
    setClearing(true); setClearError('');
    try {
      await voiceCommand('clear', {});
      chats.reset(); setShowChats(false);
      setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined);
      setQuestionHandoff(null);
      setSuggestedDraft(null);
      setClearedSession(current || null);
      readingPosition.current = 0;
    } catch { setClearError('Could not clear. Please try again.'); }
    finally { setClearing(false); }
  };

  if (correction && connected) return <main className="device-screen"><QuestionCorrection key={correction.sourceId} {...correction} attempts={correctionAttempts}
    ready={status?.voice.correction_ready === true && !busy && (correction.fromHistory ? canAskAgain : view.phase === 'complete' && status.voice.session_id === correction.sourceId)}
    close={() => setCorrection(null)} submitted={(sessionId, attachment) => { showLatest(); setCorrection(null); setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(attachment ? { ...attachment, sessionId } : undefined); setPage('main'); }} /></main>;
  if (page === 'settings') return <main className={`device-screen device-${view.phase}`}><SettingsScreen close={goBack} profile={profile} onProfileChange={onProfileChange} onLock={onLock} /></main>;
  if (page === 'messages') return <main className="device-screen"><MessagesScreen close={goBack} /></main>;
  if (page === 'sharing') return <main className="device-screen"><ShareScreen source={shareSource} close={goBack} /></main>;

  return <main className={`device-screen device-${view.phase}${page === 'main' ? ' device-thread-layout' : ''}${showChats ? ' device-show-chats' : ''}`} onTouchStart={(event) => { const touch = event.touches[0]; edgeSwipe.current = touch.clientX < 28 ? { x: touch.clientX, y: touch.clientY } : null; }} onTouchEnd={(event) => { const start = edgeSwipe.current; edgeSwipe.current = null; const touch = event.changedTouches[0]; if (start && !busy && page === 'main' && touch.clientX - start.x > 70 && Math.abs(touch.clientY - start.y) < 50) setShowChats(true); }}>
    {page === 'camera' ? <CameraScreen photoReady={connected && status?.voice.photo_ready} onAttach={(image) => { setPhoto(image); setPhotoStart(crypto.randomUUID()); setPage('main'); if (status?.voice.session_id) setClearedSession(status.voice.session_id); }} close={() => { setPage('main'); requestAnimationFrame(() => cameraButton.current?.focus()); }} /> : page === 'history' ? <HistoryScreen close={goBack} canAsk={canAskAgain} onAskAgain={askAgain} onCorrect={connected && status?.voice.correction_ready === true ? (sourceId, question, attachment) => setCorrection({ sourceId, question, photo: attachment, historyPhoto: !!attachment, fromHistory: true }) : undefined} /> : page === 'status' ? <NearbyScreen status={status} connected={connected} close={goBack} /> : page === 'about' ? <AboutScreen close={goBack} /> : <>
      <header className="device-top" inert={menuOpen}>
        <div className="device-home-group">
          <button className="device-wordmark" disabled={busy} onClick={() => void clearAnswer()} aria-label="New chat; keep saved conversations" title="New chat" aria-busy={clearing}><LanternIcon /><span>SAGE Lantern</span></button>
          <button className="device-lora-indicator" disabled={busy} onClick={() => setPage('status')} aria-label="Open nearby connections" title="Nearby"><RadioSignal paused={busy} /></button>
        </div>
        <div className="device-top-actions">
          <button ref={messagesButton} className="device-messages-shortcut" disabled={busy} onClick={() => setPage('messages')} aria-label="Messages" title="Messages"><EnvelopeIcon /></button>
          <button ref={menuButton} disabled={busy} onClick={() => setMenuOpen(true)} aria-haspopup="dialog" aria-expanded={menuOpen}><span aria-hidden="true">☰</span> Menu</button>
        </div>
      </header>
      {showChats && <button className="device-chat-scrim" aria-label="Close chats" onClick={() => setShowChats(false)} />}
      <aside id="device-chats" ref={chatDrawer} className="device-chat-list" aria-label="Conversations" inert={menuOpen}>
        <div className="device-drawer-heading"><h2>Conversations</h2><button className="device-chats-back" onClick={() => setShowChats(false)}>Close</button></div>
        <input className="device-thread-search" type="search" aria-label="Search conversations" placeholder="Search chats" value={chatSearch} onChange={(event) => setChatSearch(event.target.value)} />
        {chats.error && <p role="status">{chats.error} {chats.historyError && <button disabled={chats.refreshing} onClick={() => void savedHistory.refresh(true)}>Try again</button>}</p>}
        {!chats.history && !chats.error && !chats.threads.length && <p role="status">Loading conversations…</p>}
        {chats.current && chats.current.items.length > 4 && <p>For older details, you may need to remind Lantern.</p>}
        <nav aria-label="Past conversation threads">{chats.threads.filter((thread) => thread.items.some((item) => (item.transcript + ' ' + item.response).toLocaleLowerCase().includes(chatSearch.toLocaleLowerCase())) || !chatSearch).map((thread) => <button key={thread.id} disabled={busy || !connected} aria-current={thread.id === chats.current?.thread_id ? 'page' : undefined} onClick={() => {
          if (thread.id === chats.current?.thread_id) setShowChats(false);
          else void chats.select(thread.id).then((ok) => { if (ok) { setQuestionHandoff(null); setPhoto(undefined); setSessionPhoto(undefined); setShowChats(false); readingPosition.current = 0; } });
        }}><strong>{thread.title}</strong><small>{thread.items.length} {thread.items.length === 1 ? 'exchange' : 'exchanges'}</small></button>)}</nav>
        {chats.history?.enabled === false && <p>Saving is off. This chat clears when you leave it or lock the device.</p>}
        <button disabled={busy} onClick={() => setPage('history')}>Manage saved history</button>
      </aside>
      <section className="device-main" aria-label="Voice interaction" inert={menuOpen || (showChats && narrowChats)}>
        {photo && <div className="device-photo-attached"><CameraIcon /><span>{photoStart ? 'Starting photo question…' : touchBusy ? 'Photo attached' : 'Photo attached · tap Ask to retry'}</span><button disabled={busy} onClick={() => { setPhoto(undefined); setPhotoStart(undefined); }}>Remove</button></div>}
        {clearError && <p className="device-inline-error" role="alert">{clearError}</p>}
        {answerVisible && view.phase === 'complete' && status?.voice.detail && <p className="device-inline-error" role="status">{status.voice.detail}</p>}
        <div className="device-phase" role="status"><span className="device-indicator" aria-hidden="true" />{recording ? 'RECORDING' : reviewing || draft ? 'REVIEW YOUR QUESTION' : view.phase === 'complete' ? 'ANSWER · ON DEVICE' : view.phase === 'ready' ? 'READY · ON DEVICE' : view.phase === 'processing' ? answerVisible ? 'ANSWERING · ON DEVICE' : 'WORKING · ON DEVICE' : 'DEVICE STATUS'}</div>
        <div className={`device-content${displayedPhoto ? ' device-content-photo' : ''}`}>
        {displayedPhoto && <figure className="device-photo-preview"><Image src={`data:image/jpeg;base64,${displayedPhoto.preview}`} width={1280} height={720} unoptimized alt="Photo attached to this question" /><figcaption>Your photo</figcaption></figure>}
        <div id="device-answer" ref={reader} className="device-reader" tabIndex={0} aria-label="Your question and answer" onScroll={(event) => {
          const element = event.currentTarget;
          readingPosition.current = element.scrollTop;
          followLatest.current = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
        }}><ChatExchanges items={(chats.current?.items || []).filter((item) => !turnVisible || item.session_id !== visibleStatus?.voice.session_id)} />
          {turnVisible ? <QuestionTurn key={status?.voice.session_id || 'active'} phase={recordingView.phase} question={currentQuestion}
            answer={answerVisible ? text : ''} reply={!!chats.current?.items.length} hint={recordingView.hint} edit={editQuestion}
            review={reviewing && status?.voice.session_id && status.voice.review_id ? <QuestionReview
              key={status.voice.review_id} embedded actionsContainer={reviewActions} sessionId={status.voice.session_id} reviewId={status.voice.review_id} question={status.voice.transcript}
              hasPhoto={status.voice.has_photo} canRecordAgain={!status.voice.has_photo || !!displayedPhoto}
              onSubmitAttempt={(submitted) => { showLatest(); setQuestionHandoff({ sessionId: status.voice.session_id!, question: submitted }); }}
              recordAgain={() => { reviewRetry.current = { sessionId: status.voice.session_id!, photo: displayedPhoto }; }} /> : undefined} />
            : (!chats.current?.items.length || view.phase === 'error' || view.phase === 'unavailable') && <div className="device-message" aria-live="polite"><h1>{view.title}</h1><p>{view.hint}</p></div>}
          {answerVisible && !draft && suggestedQuestions.length > 0 && <section className="device-suggested-questions" aria-label="Suggested follow-up questions">
            <h2>Explore a little more</h2>
            {suggestedQuestions.map((suggestion) => <button key={suggestion} disabled={!canAskAgain} onClick={() => {
              if (!canAskAgain || !visibleStatus?.voice.session_id) return;
              setSuggestedDraft({ sourceId: visibleStatus.voice.session_id, question: suggestion }); showLatest();
            }}>{suggestion}<span aria-hidden="true">↗</span></button>)}
          </section>}
          {draft && <SuggestedQuestion key={`suggestion-${draft.sourceId}`} {...draft} actionsContainer={reviewActions}
            close={() => setSuggestedDraft(null)}
            askSomethingElse={() => { setSuggestedDraft(null); setPhoto(undefined); setSessionPhoto(undefined); setPhotoStart(crypto.randomUUID()); showLatest(); }}
            submitted={(sessionId, submittedQuestion) => { setQuestionHandoff({ sessionId, question: submittedQuestion }); showLatest(); }} />}
        </div>
        </div>
        {view.phase === 'processing' && <AnswerWait key={status?.voice.session_id || 'observed-processing'} stage={status?.voice.progress_stage} expanded={toolbarWaiting} />}
      </section>
      <footer className={`device-footer device-navigation-footer${toolbarWaiting ? ' device-toolbar-waiting' : ''}${suggestedQuestions.length ? ' device-has-suggestions' : ''}`} inert={toolbarWaiting || menuOpen || (showChats && narrowChats)} aria-hidden={toolbarWaiting || undefined}>
        <div className="device-review-actions" ref={setReviewActions} hidden={!reviewing && !draft} />
        <button ref={historyButton} className="device-history-shortcut" disabled={busy || !connected} onClick={() => setShowChats(true)} aria-controls="device-chats" aria-expanded={showChats || !narrowChats}>Chats</button>
        <button ref={shareButton} className="device-share-answer" disabled={!canShareAnswer} onClick={() => { if (!canShareAnswer || !status?.voice.session_id) return; setShareSource({ source_id: status.voice.session_id, question: status.voice.transcript, answer: status.voice.response, has_photo: !!status.voice.has_photo }); setPage('sharing'); }}><ShareIcon />Share</button>
        <button ref={cameraButton} className="device-camera-shortcut" disabled={busy || !connected} onClick={() => setPage('camera')} aria-label="Open camera"><CameraIcon /></button>
        <TalkControls status={status} photo={photo} autoStart={photoStart} onAutoStartHandled={() => setPhotoStart(undefined)} onStarted={(sessionId, attachment) => setSessionPhoto(attachment ? { sessionId, ...attachment } : undefined)} onCancelled={() => { setPhoto(undefined); setPhotoStart(undefined); setSessionPhoto(undefined); }} cancelLabel={displayedPhoto ? 'Back' : 'Cancel'} connected={connected && !clearing && !chats.selecting && !draft && (!photo || !!status?.voice.photo_ready)} compact onBusyChange={setTouchBusy} label={suggestedQuestions.length ? 'Ask something else' : chats.current?.items.length || turnVisible ? 'Follow up' : 'Ask'} />
      </footer>
      <div className={`device-menu-backdrop${menuOpen ? ' device-menu-open' : ''}`} inert={!menuOpen} aria-hidden={!menuOpen} onClick={(event) => { if (event.target === event.currentTarget) closeMenu(); }}>
        <div ref={menu} role="dialog" aria-modal="true" aria-label="Device menu" className="device-menu" onKeyDown={(event) => {
          if (event.key === 'Escape') closeMenu();
          if (event.key === 'Tab') {
            const buttons = menu.current?.querySelectorAll<HTMLButtonElement>('button:not(:disabled)');
            if (!buttons?.length) return;
            if (event.shiftKey && document.activeElement === buttons[0]) { event.preventDefault(); buttons[buttons.length - 1].focus(); }
            else if (!event.shiftKey && document.activeElement === buttons[buttons.length - 1]) { event.preventDefault(); buttons[0].focus(); }
          }
        }}>
          <header><h1>Menu</h1><button ref={menuClose} onClick={closeMenu}>Close</button></header>
          <button onClick={() => { setMenuOpen(false); setPage('history'); }}><HistoryIcon /> Past questions</button>
          <button onClick={() => { setMenuOpen(false); setPage('status'); }}><NetworkIcon /> Nearby</button>
          <button onClick={() => { setMenuOpen(false); setPage('settings'); }}><SettingsIcon /> Settings</button>
          <button onClick={() => { setMenuOpen(false); setShareSource(undefined); setPage('sharing'); }}><ShareIcon /> Shared knowledge</button>
          <button onClick={() => { setMenuOpen(false); setPage('about'); }}><InfoIcon /> About</button>
          {onLock && <button onClick={onLock}><LockIcon /> Lock</button>}
          <p>Saved questions and device details.</p>
        </div>
      </div>
    </>}
  </main>;
}
