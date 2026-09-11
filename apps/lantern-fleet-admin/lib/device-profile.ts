import { deviceAuth, getDeviceToken } from './device-session.ts';

export type DeviceProfile = { version: 1; configured: boolean; setup_required: boolean; display_name: string; public_discoverable: false; revision: number };

export function parseDeviceProfile(value: unknown): DeviceProfile {
  if (!value || typeof value !== 'object') throw new Error('Profile unavailable');
  const profile = value as Record<string, unknown>;
  if (profile.version !== 1 || typeof profile.configured !== 'boolean'
    || profile.setup_required !== !profile.configured || profile.public_discoverable !== false
    || typeof profile.display_name !== 'string' || profile.display_name.length > 80
    || /[\p{Cc}\p{Cf}]/u.test(profile.display_name)
    || !Number.isSafeInteger(profile.revision) || Number(profile.revision) < 0
    || (profile.configured && !profile.display_name.trim())) throw new Error('Profile unavailable');
  return profile as DeviceProfile;
}

export async function loadDeviceProfile(): Promise<DeviceProfile> {
  const token = getDeviceToken();
  if (!token) throw new Error('Unlock required');
  return parseDeviceProfile(await deviceAuth('profile/get', { token }));
}

export async function saveDeviceName(profile: DeviceProfile, name: string): Promise<DeviceProfile> {
  const token = getDeviceToken();
  if (!token) throw new Error('Unlock required');
  return parseDeviceProfile(await deviceAuth(profile.setup_required ? 'profile/setup' : 'profile/rename', {
    token, display_name: name, expected_revision: profile.revision,
  }));
}
