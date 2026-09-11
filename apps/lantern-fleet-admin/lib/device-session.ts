let deviceToken: string | null = null;
export class DeviceSessionError extends Error {}
const sessionListeners = new Set<() => void>();

export function subscribeDeviceSession(listener: () => void): () => void {
  sessionListeners.add(listener);
  return () => { sessionListeners.delete(listener); };
}

export function validDeviceToken(value: unknown): value is string {
  return typeof value === 'string' && value.length === 43 && !/[^A-Za-z0-9_-]/.test(value);
}

export function setDeviceToken(value: string | null): void {
  if (value !== null && !validDeviceToken(value)) throw new Error('Invalid device session');
  if (value === deviceToken) return;
  deviceToken = value;
  for (const listener of sessionListeners) listener();
}

export function getDeviceToken(): string | null { return deviceToken; }

export function deviceSessionHeaders(): Record<string, string> {
  return deviceToken ? { 'X-Lantern-Session': deviceToken } : {};
}

export async function deviceAuth(action: 'unlock' | 'activate' | 'presence' | 'lock' | 'cancel' | 'authorize' | 'activity' | 'profile/get' | 'profile/setup' | 'profile/rename' | 'setup/status' | 'setup/enable' | 'setup/recovery' | 'setup/confirm' | 'settings/power/get' | 'settings/power/set' | 'settings/lock/get' | 'settings/lock/set', body: { pin: string; confirmation?: string } | { token: string; display_name?: string; expected_revision?: number; display_off_seconds?: number; deep_sleep_seconds?: number; lock_seconds?: number } | Record<string, never>): Promise<Record<string, unknown>> {
  const controller = new AbortController();
  const deadline = setTimeout(() => controller.abort(), action === 'unlock' || action.startsWith('setup/') ? 20000 : 2500);
  try {
    const response = await fetch(`http://127.0.0.1:3196/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Lantern-Auth': 'control' },
      body: JSON.stringify(body), signal: controller.signal, credentials: 'omit', cache: 'no-store', redirect: 'error',
      keepalive: action === 'lock' || action === 'cancel',
    });
    if (!response.ok) throw new Error(response.status === 429 ? 'rate_limited' : 'auth_unavailable');
    const text = await response.text();
    if (text.length > 4096) throw new Error('auth_unavailable');
    const value: unknown = JSON.parse(text);
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('auth_unavailable');
    return value as Record<string, unknown>;
  } finally { clearTimeout(deadline); }
}
