import { communityRequest } from './community.ts';
import { getDeviceToken, validDeviceToken } from './device-session.ts';

export type RenderChallenge = { id: string; challenge_id: string; expires_at: number };
export type ReadReceiptOutcome = { id: string; receipt_id: string | null; state: 'queued' | 'serial_accepted' | 'unknown' };
type Request = typeof communityRequest;
const uuid = /^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const inbox = /^mesh:inbox:([0-9a-f-]{36}):[0-9a-f]{64}$/;

function invalid(): never { throw new Error('Read receipt unavailable'); }
function object(value: unknown, keys: string[]): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return invalid();
  const item = value as Record<string, unknown>;
  if (Object.keys(item).length !== keys.length || keys.some((key) => !Object.hasOwn(item, key))) return invalid();
  return item;
}
export function receiptEligible(id: string): boolean {
  const match = inbox.exec(id);
  return !!match && uuid.test(match[1]);
}
export function parseRenderChallenge(value: unknown, expectedId: string, now: number): RenderChallenge {
  const item = object(value, ['id', 'challenge_id', 'expires_at']);
  if (!receiptEligible(expectedId) || item.id !== expectedId || typeof item.challenge_id !== 'string' || !uuid.test(item.challenge_id)
      || !Number.isSafeInteger(item.expires_at) || typeof item.expires_at !== 'number' || !Number.isSafeInteger(now) || now <= 0
      || item.expires_at <= now || item.expires_at > now + 60) return invalid();
  return { id: expectedId, challenge_id: item.challenge_id, expires_at: item.expires_at };
}
export function parseReadReceipt(value: unknown, expectedId: string): ReadReceiptOutcome {
  const item = object(value, ['id', 'receipt_id', 'state']);
  if (!receiptEligible(expectedId) || item.id !== expectedId || typeof item.receipt_id !== 'string' || !uuid.test(item.receipt_id)
      || (item.state !== 'queued' && item.state !== 'serial_accepted' && item.state !== 'unknown')) return invalid();
  return { id: expectedId, receipt_id: item.receipt_id, state: item.state };
}
export function readReceiptLabel(state: ReadReceiptOutcome['state']): string {
  return { queued: 'Read receipt waiting to send', serial_accepted: 'Read receipt handed to radio · delivery unconfirmed', unknown: 'Read receipt status unconfirmed' }[state];
}

export class RenderedReceiptFlow {
  private controller = new AbortController();
  private token: string | null;
  private challenge: RenderChallenge | null = null;
  private begun = false;
  private attempted = false;
  private id: string;
  private request: Request;
  private clock: () => number;
  constructor(id: string, request: Request = communityRequest, clock: () => number = () => Math.floor(Date.now() / 1000)) {
    if (!receiptEligible(id)) invalid();
    this.id = id; this.request = request; this.clock = clock; this.token = getDeviceToken();
  }
  private live(): void {
    if (this.controller.signal.aborted || !validDeviceToken(this.token) || this.token !== getDeviceToken()
        || (typeof document !== 'undefined' && document.hidden)) invalid();
  }
  async beginRendered(): Promise<RenderChallenge> {
    this.live();
    if (this.begun) return invalid();
    this.begun = true;
    const result = await this.request('messages/render-challenge', { id: this.id, rendered: true }, this.controller.signal);
    this.live();
    this.challenge = parseRenderChallenge(result, this.id, this.clock());
    return this.challenge;
  }
  async acknowledge(consent: boolean): Promise<ReadReceiptOutcome> {
    this.live();
    if (consent !== true || this.attempted || !this.challenge || this.clock() >= this.challenge.expires_at) return invalid();
    this.attempted = true;
    try {
      const value = await this.request('messages/rendered', { id: this.id, challenge_id: this.challenge.challenge_id,
        rendered: true, share_read_receipt: true }, this.controller.signal);
      this.live();
      return parseReadReceipt(value, this.id);
    } catch {
      return { id: this.id, receipt_id: null, state: 'unknown' };
    }
  }
  close(): void { this.controller.abort(); this.challenge = null; this.token = null; }
}
