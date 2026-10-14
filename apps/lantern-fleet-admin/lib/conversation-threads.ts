import { parseDeviceHistory, type HistoryItem } from './device-history.ts';
import {
  DeviceSessionError, establishedSessionContext, fenceProtectedReply, getDeviceToken, parseSessionContext,
  type SessionContext,
} from './device-session.ts';

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
/** Compare actual instants, retaining the runtime's submillisecond precision. */
function compareCreatedAt(a: string, b: string): number {
  const microseconds = (created: string) => Number((created.match(/[T ]\d{2}:\d{2}:\d{2}[.,](\d+)/)?.[1] || '').padEnd(6, '0').slice(3, 6));
  return Date.parse(a) - Date.parse(b) || microseconds(a) - microseconds(b);
}

/** Replay saved records without changing retention or the records themselves. */
export function effectiveThreadItems(items: HistoryItem[], threadId: string): HistoryItem[] {
  const rows = items.filter((item) => (item.thread_id || item.session_id) === threadId)
    .sort((a, b) => compareCreatedAt(a.created_at, b.created_at));
  let branch: HistoryItem[] = [];
  for (let begin = 0; begin < rows.length;) {
    let end = begin + 1;
    while (end < rows.length && compareCreatedAt(rows[begin].created_at, rows[end].created_at) === 0) end++;
    const bucket = rows.slice(begin, end);
    begin = end;
    const edits = new Map(bucket.filter((row) => row.supersedes).map((row) => [row.session_id, row]));
    if (!edits.size) {
      branch.push(...bucket);
      continue;
    }

    // Plain tied rows cannot establish which side of a correction they belong
    // to. Preserve only the provable earlier prefix and one complete edit chain.
    const bucketIds = new Set(bucket.map((row) => row.session_id));
    let cut = branch.length;
    for (const edit of edits.values()) {
      const target = edit.supersedes!;
      const index = branch.findIndex((item) => item.session_id === target);
      if (index >= 0) cut = Math.min(cut, index);
      else if (!bucketIds.has(target)) cut = 0;
    }
    branch = branch.slice(0, cut);

    const replaced = new Set([...edits.values()].map((row) => row.supersedes));
    const terminals = [...edits.keys()].filter((id) => !replaced.has(id));
    if (terminals.length !== 1) continue;
    const terminal = terminals[0];
    let currentId: string | undefined = terminal;
    const visited = new Set<string>();
    while (currentId !== undefined && edits.has(currentId) && !visited.has(currentId)) {
      visited.add(currentId);
      currentId = edits.get(currentId)!.supersedes;
    }
    if (visited.size === edits.size && (currentId === undefined || !edits.has(currentId))) branch.push(edits.get(terminal)!);
  }
  return branch;
}

export function groupConversations(saved: HistoryItem[], current: ConversationState | null): ConversationThread[] {
  const unique = new Map(saved.map((item) => [item.session_id, item]));
  const activeIds = new Set(current?.items.map((item) => item.session_id));
  const groups = new Map<string, HistoryItem[]>();
  for (const item of unique.values()) {
    const id = item.thread_id || item.session_id;
    // The active Snapshot is already the effective branch. Saved inventory must
    // neither reintroduce descendants nor expand a bounded or empty Snapshot.
    if (id === current?.thread_id || activeIds.has(item.session_id)) continue;
    groups.set(id, [...(groups.get(id) || []), item]);
  }
  const threads = [...groups].flatMap(([id, rows]) => {
    const items = effectiveThreadItems(rows, id);
    return items.length ? [{ id, title: items[0].transcript || 'New chat', items, updated: items[items.length - 1].created_at }] : [];
  });
  if (current?.items.length) {
    const items = [...current.items];
    threads.push({ id: current.thread_id, title: items[0]?.transcript || 'New chat', items, updated: items.at(-1)?.created_at || '' });
  }
  return threads.sort((a, b) => a.id === current?.thread_id ? -1 : b.id === current?.thread_id ? 1 : compareCreatedAt(b.updated, a.updated));
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
  /**
   * The authority this read is issued under.
   *
   * Captured before dispatch, because a reply cannot be asked to witness the
   * authority its own request was sent under: a rotation that lands while the
   * read is in flight is admitted by the runtime under the new epoch, so the
   * reply carries it and agrees with itself.
   */
  const issuedUnder = establishedSessionContext();
  // The authority must still be the one captured before dispatch. This also
  // gates each history retry: a fresh reading cannot revive an old request.
  const requireAuthority = () => {
    if ((issuedUnder.state !== 'context' && issuedUnder.state !== 'legacy')
      || fenceProtectedReply(issuedUnder, issuedUnder.state === 'context' ? issuedUnder.context : null) !== 'ok') {
      throw new DeviceSessionError('Conversation authority changed');
    }
  };
  requireAuthority();
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timer = setTimeout(abort, action === 'history/list' ? 10000 : 4000);
  try {
    let response: Response;
    for (let attempt = 0; ; attempt++) {
      if (token !== getDeviceToken() || document.hidden) throw new DeviceSessionError('Device locked');
      requireAuthority();
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
    const value: unknown = JSON.parse(text);
    const fields = value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
    /**
     * The read is adopted only under the authority it was issued under, and only
     * while that authority still holds.
     *
     * This is the consumer-side half of the fence the status poll cannot provide:
     * the poll invalidates *after* it has observed a rotation, so a reply that
     * lands in the window before that would otherwise be adopted whole — a
     * conversation, a selection, a page of saved history. A reply carrying no
     * stamp is refused rather than trusted: against an established authority the
     * device always stamps one, so its absence is itself the anomaly.
     */
    let stamp: SessionContext | null;
    try { stamp = parseSessionContext(fields?.session_context); }
    catch { throw new DeviceSessionError('Conversation authority changed'); }
    if (fenceProtectedReply(issuedUnder, stamp) !== 'ok') throw new DeviceSessionError('Conversation authority changed');
    if (!fields) return value;
    // The stamp is transport admission metadata, not conversation state: it is
    // read here and does not travel on into the parsers and caches.
    const state = { ...fields };
    delete state.session_context;
    return state;
  } finally { clearTimeout(timer); signal?.removeEventListener('abort', abort); }
}
