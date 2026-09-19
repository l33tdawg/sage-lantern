export type CustodyStatus = {
  schema: 'sage.lantern.mesh-custody-status.v1'; enabled: boolean;
  held: number; bytes: number; recipients: number;
  oldest_expires_at: number | null; delivered: number; depositors: number;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function keys(value: Record<string, unknown>, names: string): boolean {
  const expected = names.split(' ');
  return Object.keys(value).length === expected.length && expected.every((name) => Object.hasOwn(value, name));
}

function count(value: unknown, maximum: number): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 && value <= maximum;
}

export function parseCustodyStatus(value: unknown): CustodyStatus | undefined {
  if (!record(value) || !keys(value, 'schema enabled held bytes recipients oldest_expires_at delivered depositors')
    || value.schema !== 'sage.lantern.mesh-custody-status.v1' || typeof value.enabled !== 'boolean'
    || !count(value.held, 256) || !count(value.bytes, 1 << 24) || !count(value.recipients, 256)
    || !count(value.delivered, 0xffffffff) || !count(value.depositors, 64)
    || (value.oldest_expires_at !== null
      && (typeof value.oldest_expires_at !== 'number' || !Number.isSafeInteger(value.oldest_expires_at)
        || value.oldest_expires_at <= 0))) return undefined;
  if (!value.enabled && (value.held !== 0 || value.bytes !== 0 || value.recipients !== 0
    || value.delivered !== 0 || value.depositors !== 0 || value.oldest_expires_at !== null)) return undefined;
  if (value.held === 0 && (value.bytes !== 0 || value.recipients !== 0 || value.oldest_expires_at !== null)) return undefined;
  if (value.recipients > value.held) return undefined;
  return value as CustodyStatus;
}
