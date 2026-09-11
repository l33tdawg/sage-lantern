import { getDeviceToken, validDeviceToken } from './device-session.ts';

export const LOCK_DELAYS = [60, 120, 300, 600, 900] as const;
export const DEFAULT_LOCK_SECONDS = 300;
export type LockSeconds = typeof LOCK_DELAYS[number];
export type LockSettings = { revision: number; lock_seconds: LockSeconds };

export function parseLockSettings(value: unknown): LockSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Lock settings unavailable');
  const item = value as Record<string, unknown>;
  if (Object.keys(item).sort().join(',') !== 'lock_seconds,revision'
      || typeof item.revision !== 'number' || !Number.isSafeInteger(item.revision) || item.revision < 0
      || !LOCK_DELAYS.some((delay) => delay === item.lock_seconds)) throw new Error('Lock settings unavailable');
  return { revision: item.revision, lock_seconds: item.lock_seconds as LockSeconds };
}

function requireSession(token: unknown): asserts token is string {
  if (!validDeviceToken(token) || token !== getDeviceToken()
      || (typeof document !== 'undefined' && document.hidden)) throw new Error('Device locked');
}

async function request(action: 'get' | 'set', fields: Record<string, number>): Promise<LockSettings> {
  const token = getDeviceToken();
  requireSession(token);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 2500);
  try {
    const response = await fetch(`http://127.0.0.1:3196/settings/lock/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Auth': 'control' },
      body: JSON.stringify({ token, ...fields }), signal: controller.signal,
      credentials: 'omit', cache: 'no-store', redirect: 'error',
    });
    if (!response.ok || response.headers.get('content-type')?.split(';')[0].trim() !== 'application/json') {
      throw new Error('Lock settings unavailable');
    }
    const reader = response.body?.getReader();
    if (!reader) throw new Error('Lock settings unavailable');
    const decoder = new TextDecoder('utf-8', { fatal: true });
    let bytes = 0;
    let text = '';
    try {
      while (true) {
        const chunk = await reader.read();
        if (chunk.done) break;
        bytes += chunk.value.byteLength;
        if (bytes > 4096) throw new Error('Lock settings unavailable');
        text += decoder.decode(chunk.value, { stream: true });
      }
      text += decoder.decode();
    } finally { await reader.cancel().catch(() => {}); }
    if (controller.signal.aborted) throw new Error('Lock settings unavailable');
    requireSession(token);
    return parseLockSettings(JSON.parse(text));
  } catch {
    requireSession(token);
    throw new Error('Lock settings unavailable');
  } finally { clearTimeout(timeout); }
}

export function loadLockSettings(): Promise<LockSettings> {
  return request('get', {});
}

export async function saveLockSettings(current: LockSettings, seconds: number): Promise<LockSettings> {
  const previous = parseLockSettings(current);
  const next = parseLockSettings({ revision: previous.revision, lock_seconds: seconds });
  return request('set', { expected_revision: previous.revision, lock_seconds: next.lock_seconds });
}
