import { communityRequest, parseCommunityResult } from './community.ts';
import { getDeviceToken } from './device-session.ts';

export type ShareObservation = {
  id: string; request_id: string; created_at: string;
  state: 'queued' | 'pending' | 'unknown' | 'accepted' | 'challenged' | 'deprecated';
  observed_status: null | 'proposed' | 'validated' | 'committed' | 'challenged' | 'deprecated';
  trust_basis: 'pinned_gateway' | 'trusted_node_online' | 'unverified';
  acceptance_available: boolean; consensus_verified: false; cache_eligible: false;
};

export function parseShareObservation(value: unknown, id: string, requestId: string): ShareObservation {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid share observation');
  const item = value as Record<string, unknown>;
  const keys = ['id', 'request_id', 'created_at', 'state', 'observed_status', 'trust_basis', 'acceptance_available', 'consensus_verified', 'cache_eligible'];
  const accepted = { committed: 'accepted', challenged: 'challenged', deprecated: 'deprecated' } as Record<string, string>;
  const verified = ['accepted', 'challenged', 'deprecated'].includes(String(item.state));
  if (Object.keys(item).length !== keys.length || keys.some((key) => !(key in item)) || item.id !== id || item.request_id !== requestId
      || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(requestId) || /^0{8}-0{4}-0{4}-0{4}-0{12}$/.test(requestId)
      || typeof item.state !== 'string' || !['queued', 'pending', 'unknown', 'accepted', 'challenged', 'deprecated'].includes(item.state)
      || ![null, 'proposed', 'validated', 'committed', 'challenged', 'deprecated'].includes(item.observed_status as null | string)
      || typeof item.trust_basis !== 'string' || !['pinned_gateway', 'trusted_node_online', 'unverified'].includes(item.trust_basis)
      || typeof item.acceptance_available !== 'boolean' || item.acceptance_available !== verified
      || item.consensus_verified !== false || item.cache_eligible !== false
      || verified && (item.trust_basis !== 'trusted_node_online' || accepted[String(item.observed_status)] !== item.state)) {
    throw new Error('Invalid share observation');
  }
  parseCommunityResult({ id: item.id, state: item.state, created_at: item.created_at }, 'share');
  return item as ShareObservation;
}

export class ShareRefreshRequest {
  readonly id: string;
  private readonly token = getDeviceToken();
  requestId: string | null = null;
  result: ShareObservation | null = null;
  uncertain = false;
  busy = false;

  constructor(id: string) { this.id = id; }

  currentSession(): boolean { return this.token !== null && getDeviceToken() === this.token; }

  forCurrentSession(): ShareRefreshRequest {
    if (this.currentSession()) return this;
    const next = new ShareRefreshRequest(this.id);
    next.requestId = this.requestId;
    next.uncertain = this.uncertain || this.busy;
    return next;
  }

  get canRefresh(): boolean { return !this.busy && (!this.requestId || !this.uncertain && this.result?.observed_status != null); }

  async run(action: 'refresh' | 'inspect', signal: AbortSignal): Promise<ShareObservation> {
    const authorized = () => this.token !== null && getDeviceToken() === this.token && !signal.aborted;
    if (!authorized() || this.busy || action === 'refresh' && !this.canRefresh || action === 'inspect' && !this.requestId) throw new Error('Status request unavailable');
    if (action === 'refresh') this.requestId = crypto.randomUUID();
    const requestId = this.requestId!;
    this.busy = true;
    try {
      const body = { id: this.id, request_id: requestId, ...(action === 'refresh' ? { consent: true } : {}) };
      const value = await communityRequest(action === 'refresh' ? 'shares/refresh' : 'shares/inspect', body, signal);
      if (!authorized()) throw new Error('Device locked');
      const result = parseShareObservation(value, this.id, requestId);
      this.result = result;
      if (action === 'refresh') this.uncertain = false;
      return result;
    } catch (error) {
      this.uncertain = true;
      throw error;
    } finally { this.busy = false; }
  }
}

const requests = new Map<string, ShareRefreshRequest>();

export function shareRefreshRequest(id: string): ShareRefreshRequest {
  const previous = requests.get(id);
  if (!previous && requests.size >= 128) throw new Error('Status request inventory full');
  const current = previous?.forCurrentSession() ?? new ShareRefreshRequest(id);
  requests.set(id, current);
  return current;
}
