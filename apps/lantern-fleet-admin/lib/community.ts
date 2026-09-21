import { getDeviceToken } from './device-session.ts';

export type Capability = { available: boolean; reason: string };
export type CommunityStatus = { schema: 'sage.lantern.community.v1'; messaging: Capability; sharing: Capability; dictation: { available: boolean } };
export type MessageState = 'queued' | 'sent' | 'delivered' | 'read' | 'unknown' | 'failed' | 'cancelled';
export type ShareState = 'queued' | 'pending' | 'accepted' | 'rejected' | 'superseded' | 'challenged' | 'deprecated' | 'unknown';
export type CommunityMessage = { id: string; peer_name: string; text: string; state: MessageState; created_at: string };
export type CommunityContact = { id: string; name: string };
export type RadioContact = CommunityContact & { radio_public_key: string; chain_id: string; agent_id: string; expires_at: number };
export type CommunityShare = { id: string; question: string; state: ShareState; created_at: string };
export type SharePreview = { preview_id: string; digest: string; question: string; answer: string; photo_included: false; expires_at: string };
export type ShareSource = { source_id: string; question: string; answer: string; has_photo: boolean };
export type HistoryShareState = ShareState | 'not_shared';

export function parseHistoryShares(value: unknown, requested: string[]): Record<string, HistoryShareState> {
  if (requested.length > 20 || new Set(requested).size !== requested.length || requested.some((id) => !uuid.test(id))) return invalid();
  const result = record(value); exact(result, ['items']);
  if (!Array.isArray(result.items) || result.items.length !== requested.length) return invalid();
  const states: Record<string, HistoryShareState> = {};
  for (const entry of result.items) {
    const item = record(entry); exact(item, ['source_id', 'state']);
    if (typeof item.source_id !== 'string' || !requested.includes(item.source_id) || item.source_id in states || typeof item.state !== 'string' || (item.state !== 'not_shared' && !shareStates.has(item.state))) return invalid();
    states[item.source_id] = item.state as HistoryShareState;
  }
  return states;
}

const reasons = new Set(['ready', 'not_ready', 'encrypted_storage_unavailable', 'transport_unavailable', 'public_identity_unavailable', 'acceptance_proof_unavailable']);
const messageStates = new Set(['queued', 'sent', 'delivered', 'read', 'unknown', 'failed', 'cancelled']);
const shareStates = new Set(['queued', 'pending', 'accepted', 'rejected', 'superseded', 'challenged', 'deprecated', 'unknown']);
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function invalid(): never { throw new Error('Invalid community response'); }
function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  return value as Record<string, unknown>;
}
function exact(value: Record<string, unknown>, keys: string[]): void {
  if (Object.keys(value).length !== keys.length || keys.some((key) => !(key in value))) invalid();
}
function text(value: unknown, maximum: number): string {
  if (typeof value !== 'string' || !value.trim() || new TextEncoder().encode(value).length > maximum || /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u202a-\u202e\u2066-\u2069]/.test(value)) return invalid();
  return value;
}
function identifier(value: unknown): string {
  const result = text(value, 256);
  if (!/^[A-Za-z0-9_.:-]+$/.test(result)) return invalid();
  return result;
}
function date(value: unknown): string {
  const result = text(value, 40);
  if (!/^\d{4}-\d{2}-\d{2}T.*(?:Z|[+-]\d{2}:\d{2})$/.test(result) || !Number.isFinite(Date.parse(result))) return invalid();
  return result;
}
function capability(value: unknown): Capability {
  const item = record(value); exact(item, ['available', 'reason']);
  if (typeof item.available !== 'boolean' || typeof item.reason !== 'string' || !reasons.has(item.reason) || (item.available && item.reason !== 'ready')) return invalid();
  return { available: item.available, reason: item.reason };
}
export function parseCommunityStatus(value: unknown): CommunityStatus {
  const item = record(value); exact(item, ['schema', 'messaging', 'sharing', 'dictation']);
  const dictation = record(item.dictation); exact(dictation, ['available']);
  if (item.schema !== 'sage.lantern.community.v1' || typeof dictation.available !== 'boolean') return invalid();
  return { schema: item.schema, messaging: capability(item.messaging), sharing: capability(item.sharing), dictation: { available: dictation.available } };
}
function page<T>(value: unknown, parse: (item: unknown) => T): { items: T[]; more: boolean } {
  const result = record(value); exact(result, ['items', 'more']);
  if (!Array.isArray(result.items) || result.items.length > 20 || typeof result.more !== 'boolean') return invalid();
  const items = result.items.map(parse);
  const ids = items.map((item) => (item as { id: string }).id);
  if (new Set(ids).size !== ids.length) return invalid();
  return { items, more: result.more };
}
export function parseMessages(value: unknown) {
  return page<CommunityMessage>(value, (value) => {
    const item = record(value); exact(item, ['id', 'peer_name', 'text', 'state', 'created_at']);
    if (typeof item.state !== 'string' || !messageStates.has(item.state)) return invalid();
    return { id: identifier(item.id), peer_name: text(item.peer_name, 256), text: text(item.text, 8192), state: item.state as MessageState, created_at: date(item.created_at) };
  });
}
export function parseContacts(value: unknown) {
  return page<CommunityContact>(value, (value) => {
    const item = record(value); exact(item, ['id', 'name']);
    return { id: identifier(item.id), name: text(item.name, 256) };
  });
}
export function parseRadioContacts(value: unknown) {
  return page<RadioContact>(value, (value) => {
    const item = record(value); exact(item, ['id', 'name', 'radio_public_key', 'chain_id', 'agent_id', 'expires_at']);
    if (typeof item.radio_public_key !== 'string' || !/^[0-9a-f]{64}$/.test(item.radio_public_key)
      || typeof item.agent_id !== 'string' || !/^[0-9a-f]{64}$/.test(item.agent_id)
      || typeof item.expires_at !== 'number' || !Number.isSafeInteger(item.expires_at) || item.expires_at <= 0) return invalid();
    return { id: identifier(item.id), name: text(item.name, 256), radio_public_key: item.radio_public_key,
      chain_id: text(item.chain_id, 256), agent_id: item.agent_id, expires_at: item.expires_at };
  });
}
export function parseShares(value: unknown) {
  return page<CommunityShare>(value, (value) => {
    const item = record(value); exact(item, ['id', 'question', 'state', 'created_at']);
    if (typeof item.state !== 'string' || !shareStates.has(item.state)) return invalid();
    return { id: identifier(item.id), question: text(item.question, 4096), state: item.state as ShareState, created_at: date(item.created_at) };
  });
}
export function parseSharePreview(value: unknown): SharePreview {
  const item = record(value); exact(item, ['preview_id', 'digest', 'question', 'answer', 'photo_included', 'expires_at']);
  if (typeof item.preview_id !== 'string' || !uuid.test(item.preview_id) || typeof item.digest !== 'string' || !/^[0-9a-f]{64}$/.test(item.digest) || item.photo_included !== false) return invalid();
  return { preview_id: item.preview_id, digest: item.digest, question: text(item.question, 4096), answer: text(item.answer, 8192), photo_included: false, expires_at: date(item.expires_at) };
}
export function parseCommunityResult(value: unknown, kind: 'message' | 'share'): { id: string; state: MessageState | ShareState; created_at: string } {
  const item = record(value); exact(item, ['id', 'state', 'created_at']);
  if (typeof item.state !== 'string' || !(kind === 'message' ? messageStates : shareStates).has(item.state)) return invalid();
  return { id: identifier(item.id), state: item.state as MessageState | ShareState, created_at: date(item.created_at) };
}
export function communityLabel(state: MessageState | ShareState): string {
  return { queued: 'Waiting to send', sent: 'Sent · delivery unconfirmed', delivered: 'Delivered', read: 'Read', unknown: 'Status unconfirmed', failed: 'Not sent', cancelled: 'Cancelled · delivery unconfirmed', pending: 'Under review', accepted: 'Accepted publicly', rejected: 'Not accepted', superseded: 'Replaced by newer information', challenged: 'Being challenged', deprecated: 'Withdrawn from use' }[state];
}

export type CommunityAction = 'status' | 'radio/status' | 'custody/status' | 'messages/list' | 'contacts/list' | 'contacts/radio' | 'messages/send' | 'messages/open' | 'messages/render-challenge' | 'messages/rendered' | 'shares/preview' | 'shares/submit' | 'shares/list' | 'shares/status' | 'shares/refresh' | 'shares/inspect';
export async function communityRequest(action: CommunityAction, body: Record<string, unknown> = {}, signal?: AbortSignal): Promise<unknown> {
  const token = getDeviceToken();
  if (!token || signal?.aborted || (typeof document !== 'undefined' && document.hidden)) throw new Error('Device locked');
  const encoded = JSON.stringify(body);
  if (new TextEncoder().encode(encoded).length > 32768) throw new Error('Request too large');
  const controller = new AbortController();
  const abort = () => controller.abort();
  signal?.addEventListener('abort', abort, { once: true });
  const timeout = setTimeout(abort, 10000);
  try {
    const response = await fetch(`http://127.0.0.1:3197/community/${action}`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control', 'X-Lantern-Session': token }, body: encoded, signal: controller.signal, credentials: 'omit', cache: 'no-store', redirect: 'error' });
    if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') throw new Error('Community unavailable');
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Community unavailable');
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let bytes = 0; let result = '';
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        bytes += chunk.value.byteLength;
        if (bytes > 262144) throw new Error('Response too large');
        result += decoder.decode(chunk.value, { stream: true });
      }
      result += decoder.decode();
    } finally { await reader.cancel().catch(() => {}); }
    if (controller.signal.aborted || token !== getDeviceToken() || (typeof document !== 'undefined' && document.hidden)) throw new Error('Device locked');
    return JSON.parse(result);
  } finally { clearTimeout(timeout); signal?.removeEventListener('abort', abort); }
}
