import { deviceSessionHeaders, getDeviceToken } from './device-session.ts';
import { savedHistory } from './saved-history.ts';

export type TouchSession = { session_id: string; token: string; lease_seconds: number };

export class VoiceControlError extends Error {
  status: number;
  constructor(status: number, message: string) { super(message); this.status = status; }
}

export function parseTouchSession(value: unknown): TouchSession {
  if (!value || typeof value !== 'object') throw new Error('Invalid voice session');
  const session = value as Record<string, unknown>;
  if (typeof session.session_id !== 'string' || !/^[0-9a-f-]{36}$/i.test(session.session_id)
    || typeof session.token !== 'string' || session.token.length < 16 || session.token.length > 256
    || session.lease_seconds !== 4) throw new Error('Invalid voice session');
  return session as TouchSession;
}

export async function voiceCommand(action: 'start' | 'keepalive' | 'stop' | 'cancel' | 'clear' | 'session/link' | 'review/submit' | 'review/cancel' | 'followup' | 'message/status' | 'history/list' | 'history/settings' | 'history/delete' | 'history/clear' | 'history/photo', body: Record<string, string | boolean>, keepalive = false): Promise<unknown> {
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

export async function linkVoiceSession(): Promise<boolean> {
  if (!getDeviceToken()) return false;
  try {
    await voiceCommand('session/link', {});
    return true;
  } catch {
    return false;
  }
}
