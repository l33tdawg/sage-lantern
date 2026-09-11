import { deviceAuth, getDeviceToken } from './device-session.ts';

export const DISPLAY_DELAYS = [30, 60, 120, 300] as const;
export const SLEEP_DELAYS = [300, 600, 900, 1800] as const;
export type PowerSettings = { revision: number; display_off_seconds: number; deep_sleep_seconds: number; deep_sleep_available: boolean };

export function parsePowerSettings(value: unknown): PowerSettings {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Power settings unavailable');
  const result = value as Record<string, unknown>;
  if (Object.keys(result).sort().join(',') !== 'deep_sleep_available,deep_sleep_seconds,display_off_seconds,revision'
      || !Number.isSafeInteger(result.revision) || Number(result.revision) < 0
      || !DISPLAY_DELAYS.some((delay) => delay === result.display_off_seconds)
      || !SLEEP_DELAYS.some((delay) => delay === result.deep_sleep_seconds)
      || Number(result.deep_sleep_seconds) <= Number(result.display_off_seconds)
      || typeof result.deep_sleep_available !== 'boolean') throw new Error('Power settings unavailable');
  return result as PowerSettings;
}

export async function loadPowerSettings(): Promise<PowerSettings> {
  const token = getDeviceToken();
  if (!token) throw new Error('Device locked');
  return parsePowerSettings(await deviceAuth('settings/power/get', { token }));
}

export async function savePowerSettings(current: PowerSettings, display: number, sleep: number): Promise<PowerSettings> {
  const token = getDeviceToken();
  if (!token) throw new Error('Device locked');
  parsePowerSettings({ ...current, display_off_seconds: display, deep_sleep_seconds: sleep });
  return parsePowerSettings(await deviceAuth('settings/power/set', {
    token, expected_revision: current.revision, display_off_seconds: display, deep_sleep_seconds: sleep,
  }));
}

export function delayLabel(seconds: number): string {
  return seconds < 60 ? `${seconds} seconds` : seconds === 60 ? '1 minute' : `${seconds / 60} minutes`;
}
