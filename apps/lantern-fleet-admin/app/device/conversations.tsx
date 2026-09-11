'use client';
import { useEffect, useRef, useState } from 'react';
import { conversationRequest, groupConversations, parseConversationState, type ConversationState } from '../../lib/conversation-threads';
import type { HistoryItem } from '../../lib/device-history';
import { subscribeDeviceSession } from '../../lib/device-session';
import { savedHistory } from '../../lib/saved-history';
import { useSavedHistory } from './saved-history';

export function useConversations(connected: boolean, completedSession?: string) {
  const [current, setCurrent] = useState<ConversationState | null>(null);
  const { history, error: historyError, refreshing } = useSavedHistory();
  const [error, setError] = useState('');
  const [selecting, setSelecting] = useState(false);
  const generation = useRef(0);
  const selectingRef = useRef(false);
  useEffect(() => { if (connected) void savedHistory.refresh(!!completedSession); }, [connected, completedSession]);
  useEffect(() => {
    let disposed = false; let timer: ReturnType<typeof setTimeout>; let controller: AbortController;
    const clear = () => { generation.current++; controller?.abort(); setCurrent(null); };
    const unsubscribe = subscribeDeviceSession(clear);
    const hide = () => { if (document.hidden) clear(); };
    document.addEventListener('visibilitychange', hide);
    const poll = async () => {
      controller = new AbortController(); const epoch = generation.current;
      try {
        if (!connected || document.hidden || selectingRef.current) return;
        const next = parseConversationState(await conversationRequest('conversation/state', {}, controller.signal));
        if (!disposed && epoch === generation.current) { setCurrent(next); setError(''); }
      } catch { if (!disposed && !controller.signal.aborted && epoch === generation.current) setError('Chats unavailable. Reconnecting…'); }
      finally { if (!disposed) timer = setTimeout(poll, 1200); }
    };
    void poll();
    return () => { disposed = true; clearTimeout(timer); controller?.abort(); unsubscribe(); document.removeEventListener('visibilitychange', hide); };
  }, [connected, completedSession]);
  const select = async (id: string) => {
    if (selectingRef.current) return false;
    selectingRef.current = true;
    const epoch = ++generation.current;
    setSelecting(true); setError('');
    try {
      const result = parseConversationState(await conversationRequest('conversation/select', { thread_id: id }));
      if (generation.current !== epoch) return false;
      setCurrent(result); return true;
    } catch { if (generation.current === epoch) setError('Could not open this chat. Try again.'); return false; }
    finally { generation.current++; selectingRef.current = false; setSelecting(false); }
  };
  return { current: connected ? current : null, history: connected ? history : null, error: error || historyError, historyError, refreshing, selecting, select, reset: () => { generation.current++; setCurrent(null); },
    threads: connected ? groupConversations(history?.items || [], current) : [] };
}

export function ChatExchanges({ items }: { items: HistoryItem[] }) {
  return items.map((item) => <article className="device-chat-exchange" key={item.session_id}>
    <section className="device-chat-user"><h2>You</h2><p>{item.transcript}</p>{item.has_photo && <small>Photo question · open Past questions to view the saved photo</small>}</section>
    <section className="device-chat-assistant"><h2>Lantern</h2><p>{item.response}</p>{item.supersedes && <small>Question corrected</small>}</section>
  </article>);
}
