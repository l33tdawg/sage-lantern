import { parseDeviceHistory, type HistoryItem } from './device-history.ts';
import { DeviceSessionError, getDeviceToken } from './device-session.ts';

export type ConversationState = { thread_id: string; revision: number; active_session_id: string | null; items: HistoryItem[]; context_turn_limit: number };
export type ConversationThread = { id: string; title: string; items: HistoryItem[]; updated: string };
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
export function parseConversationState(value: unknown): ConversationState {
  if (!value || typeof value !== 'object') throw new Error('Conversation unavailable');
  const row = value as ConversationState;
  if (!uuid.test(row.thread_id) || !Number.isSafeInteger(row.revision) || row.revision < 0
    || (row.active_session_id !== null && !uuid.test(row.active_session_id)) || row.context_turn_limit !== 4) throw new Error('Conversation unavailable');
  const items = parseDeviceHistory({ enabled: false, items: row.items }).items;
  if (items.length > 40 || items.some((item) => item.thread_id !== row.thread_id)) throw new Error('Conversation unavailable');
  return { ...row, items };
}
export function groupConversations(saved: HistoryItem[], current: ConversationState | null): ConversationThread[] {
  const unique = new Map(saved.map((item) => [item.session_id, item]));
  for (const item of current?.items || []) unique.set(item.session_id, item);
  const groups = new Map<string, HistoryItem[]>();
  for (const item of unique.values()) {
    const id = item.thread_id || item.session_id;
    groups.set(id, [...(groups.get(id) || []), item]);
  }
  return [...groups].map(([id, rows]) => {
    const superseded = new Set(rows.map((row) => row.supersedes).filter(Boolean));
    const items = rows.filter((row) => !superseded.has(row.session_id)).sort((a, b) => Date.parse(a.created_at) - Date.parse(b.created_at));
    return { id, title: items[0]?.transcript || 'New chat', items, updated: items.at(-1)?.created_at || '' };
  }).sort((a, b) => a.id === current?.thread_id ? -1 : b.id === current?.thread_id ? 1 : b.updated.localeCompare(a.updated));
}

function waitForHistoryRetry(signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const abort = () => { clearTimeout(timer); signal.removeEventListener('abort', abort); reject(new DOMException('History read cancelled', 'AbortError')); };
    const timer = setTimeout(() => { signal.removeEventListener('abort', abort); resolve(); }, 1250);
    signal.addEventListener('abort', abort, { once: true });
    if (signal.aborted) abort();
  });
}

export async function conversationRequest(action: 'conversation/state' | 'conversation/select' | 'history/list', body: Record<string, string> = {}, signal?: AbortSignal): Promise<unknown> {
  const token = getDeviceToken();
  if (!token || signal?.aborted || document.hidden) throw new DeviceSessionError('Device locked');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, action === 'history/list' ? 10000 : 4000);
  try {
    let response: Response;
    for (let attempt = 0; ; attempt++) {
      if (token !== getDeviceToken() || document.hidden) throw new DeviceSessionError('Device locked');
      response = await fetch(`http://127.0.0.1:3197/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control', 'X-Lantern-Session': token }, body: JSON.stringify(body), signal: controller.signal, cache: 'no-store', credentials: 'omit', redirect: 'error' });
      // Protected history reads can briefly contend with background maintenance.
      // Retry only this read, within the original deadline; never replay a selection.
      if (action !== 'history/list' || attempt >= 2 || ![502, 503, 504].includes(response.status)) break;
      await response.body?.cancel();
      await waitForHistoryRetry(controller.signal);
    }
    if (response.status === 401 || response.status === 403) throw new DeviceSessionError('Device locked');
    if (!response.ok) throw new Error('Conversation unavailable');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Conversation unavailable');
    let size = 0; let text = ''; const decoder = new TextDecoder('utf-8', { fatal: true });
    try {
      while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 4 * 1024 * 1024) throw new Error('Conversation too large');
        text += decoder.decode(chunk.value, { stream: true });
      }
      text += decoder.decode();
    } finally { await reader.cancel().catch(() => {}); }
    if (controller.signal.aborted || token !== getDeviceToken() || document.hidden) throw new DeviceSessionError('Device locked');
    return JSON.parse(text);
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}
