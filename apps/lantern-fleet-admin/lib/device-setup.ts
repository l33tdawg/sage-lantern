export type DeviceSetup = { enabled: boolean; recovery_backup_confirmed: boolean; has_existing_vault: boolean; can_initialize: boolean };

export function parseDeviceSetup(value: unknown): DeviceSetup {
  if (!value || typeof value !== 'object') throw new Error('Setup unavailable');
  const setup = value as Record<string, unknown>;
  for (const field of ['enabled', 'recovery_backup_confirmed', 'has_existing_vault', 'can_initialize']) {
    if (typeof setup[field] !== 'boolean') throw new Error('Setup unavailable');
  }
  if (setup.can_initialize && (setup.enabled || setup.has_existing_vault)) throw new Error('Setup unavailable');
  return setup as DeviceSetup;
}

export function validRecoveryKey(value: unknown): value is string {
  return typeof value === 'string' && value.length === 44 && /^[A-Za-z0-9+/]{43}=$/.test(value);
}
