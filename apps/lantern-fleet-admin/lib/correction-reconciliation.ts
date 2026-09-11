export type CorrectionConfirmation = {
  challenge: string;
  expectedRevision: number;
  expiresInSeconds: number;
  status: 'unrecorded' | 'reserved' | 'admitting' | 'accepted' | 'completed' | 'failed';
};

export function parseCorrectionConfirmation(value: unknown): CorrectionConfirmation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid correction confirmation');
  const record = value as Record<string, unknown>;
  const statuses = ['unrecorded', 'reserved', 'admitting', 'accepted', 'completed', 'failed'];
  if (typeof record.status !== 'string' || !statuses.includes(record.status)
    || record.retry_allowed !== false || record.risk_acknowledgement_required !== true
    || record.continuity !== 'current_snapshot_only'
    || typeof record.challenge !== 'string' || !/^[A-Za-z0-9_-]{43}$/.test(record.challenge)
    || typeof record.expected_revision !== 'number' || !Number.isSafeInteger(record.expected_revision)
    || record.expected_revision < 0 || record.expires_in_seconds !== 120
    || (record.status === 'unrecorded' ? record.expected_revision !== 0
      : record.expected_revision < 1 || record.revision !== record.expected_revision)) {
    throw new Error('Invalid correction confirmation');
  }
  return {
    challenge: record.challenge, expectedRevision: record.expected_revision,
    expiresInSeconds: record.expires_in_seconds,
    status: record.status as CorrectionConfirmation['status'],
  };
}
