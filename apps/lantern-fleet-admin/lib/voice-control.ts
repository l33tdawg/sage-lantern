import {
  SESSION_CONTEXT_UNAVAILABLE, deviceSessionHeaders, getDeviceToken, parseSessionContext,
  type SessionContext,
} from './device-session.ts';
import { savedHistory } from './saved-history.ts';

export type TouchSession = { session_id: string; token: string; lease_seconds: number };

export class VoiceControlError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

/**
 * The context a protected reply was admitted under.
 *
 * Every successful protected reply carries one: the runtime attaches the
 * context it captured at admission and re-checks that it is still current
 * before answering. A reply whose context is present but malformed throws — it
 * cannot be compared, so it cannot be trusted, and reading it as "no context"
 * is exactly how a rotation would slip through.
 */
export function replySessionContext(value: unknown): SessionContext | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(SESSION_CONTEXT_UNAVAILABLE);
  return parseSessionContext((value as Record<string, unknown>).session_context);
}

export function parseTouchSession(value: unknown): TouchSession {
  if (!value || typeof value !== 'object') throw new Error('Invalid voice session');
  const session = value as Record<string, unknown>;
  if (typeof session.session_id !== 'string' || !/^[0-9a-f-]{36}$/i.test(session.session_id)
    || typeof session.token !== 'string' || session.token.length < 16 || session.token.length > 256
    || session.lease_seconds !== 4) throw new Error('Invalid voice session');
  return session as TouchSession;
}

export async function voiceCommand(action: 'start' | 'start/confirm' | 'keepalive' | 'stop' | 'cancel' | 'clear' | 'session/link' | 'question/start' | 'review/submit' | 'review/cancel' | 'followup' | 'message/status' | 'history/list' | 'history/settings' | 'history/delete' | 'history/clear' | 'history/photo', body: Record<string, string | boolean>, keepalive = false): Promise<unknown> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), 2500);
  const endHistoryChange = ['history/settings', 'history/delete', 'history/clear'].includes(action) ? savedHistory.beginChange() : undefined;
  try {
    const response = await fetch(`http://127.0.0.1:3197/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Voice': 'control', ...deviceSessionHeaders() },
      body: JSON.stringify(body), signal: controller.signal, cache: 'no-store', credentials: 'omit', keepalive,
    });
    if (!response.ok) throw new VoiceControlError(response.status, response.status === 409 ? 'Voice is busy. Please wait.' : 'Voice control unavailable. Try again.');
    return await response.json();
  } finally { clearTimeout(deadline); endHistoryChange?.(); }
}

/**
 * Confirm the live capture owner after a delayed start reply, without renewing
 * its lease.
 *
 * Returns the context the confirmation was admitted under, so the caller can
 * require it to be the one captured when the start was admitted instead of
 * assuming the two agree.
 */
export async function confirmTouchSession(session: TouchSession, requestId: string, deviceToken = getDeviceToken()): Promise<SessionContext | null> {
  const invalid = () => new Error('Could not confirm recording. Try again.');
  if (getDeviceToken() !== deviceToken) throw invalid();
  const value = await voiceCommand('start/confirm', { request_id: requestId, session_id: session.session_id, token: session.token });
  if (getDeviceToken() !== deviceToken || !value || typeof value !== 'object' || Array.isArray(value)) throw invalid();
  const result = value as Record<string, unknown>;
  if (result.status !== 'current' || result.session_id !== session.session_id || result.request_id !== requestId) throw invalid();
  return parseSessionContext(result.session_context);
}

export async function linkVoiceSession(): Promise<boolean> {
  if (!getDeviceToken()) return false;
  try {
    await voiceCommand('session/link', {});
    return true;
  } catch {
    return false;
  }
}
