export type SageStatus = {
  available: boolean;
  observed_at: string | null;
  scope: 'node_projection';
  reason?: string;
  committed_memories?: number | null;
  total_memories?: number | null;
  block_height?: number | null;
  chain_peers?: number | null;
  mempool_txs?: number | null;
  validator_count?: number | null;
  validator_connections?: number | null;
  ready?: boolean | null;
  catching_up?: boolean | null;
  idle?: boolean | null;
  stuck?: boolean | null;
  federation_enabled?: boolean | null;
};

export function parseSageStatus(value: unknown): SageStatus | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const status = value as Record<string, unknown>;
  if (typeof status.available !== 'boolean' || status.scope !== 'node_projection'
    || (status.observed_at !== null && (typeof status.observed_at !== 'string' || !Number.isFinite(Date.parse(status.observed_at))))
    || (status.reason != null && typeof status.reason !== 'string')) return undefined;
  const counts = ['committed_memories', 'total_memories', 'block_height', 'chain_peers', 'mempool_txs', 'validator_count', 'validator_connections'];
  if (counts.some((key) => status[key] != null && (typeof status[key] !== 'number' || !Number.isSafeInteger(status[key]) || (status[key] as number) < 0))) return undefined;
  if (['ready', 'catching_up', 'idle', 'stuck', 'federation_enabled'].some((key) => status[key] != null && typeof status[key] !== 'boolean')) return undefined;
  return status as SageStatus;
}

export function sageStatusIsFresh(status: SageStatus | undefined, now = Date.now()): boolean {
  if (!status?.available || !status.observed_at) return false;
  const age = now - Date.parse(status.observed_at);
  return age >= -30000 && age <= 30000;
}
