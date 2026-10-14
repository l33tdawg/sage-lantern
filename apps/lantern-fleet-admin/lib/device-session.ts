let deviceToken: string | null = null;
export class DeviceSessionError extends Error {}
const sessionListeners = new Set<() => void>();
/** The context of the last validated reading, or null before one arrives. */
let observedContext: SessionContext | null = null;
/**
 * Whether an accepted reading has established an authority at all.
 *
 * `observedContext === null` cannot say it on its own: it is both "nothing
 * observed yet" and "the device publishes no context", which is the legacy
 * unlocked path. A fence that read those two as one would accept a protected
 * reply it cannot actually judge, so the distinction is kept here.
 */
let observedState: 'unknown' | 'legacy' | 'context' | 'lost' = 'unknown';
/**
 * Which authority object the client is currently on.
 *
 * Moved whenever the authority is re-keyed: a replaced token, a context that
 * changed, or a reading that could not be compared at all. An expectation
 * captured before a move can never be satisfied again — and it has to work that
 * way, because the *context* alone cannot tell the two cases apart. A→B→A and
 * lose-then-restore both end on the very same context, so a rule that compared
 * only the context would let a reply issued before the move be adopted after it,
 * which is precisely the work the move was supposed to invalidate.
 */
let authorityGeneration = 0;

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
  // A replaced token has its own context; the next reading establishes it, and
  // holding the old one would compare a new session against a dead epoch. The
  // generation moves with it, so work captured under the old token cannot be
  // revived by a token that later comes back.
  observedContext = null;
  observedState = 'unknown';
  authorityGeneration += 1;
  for (const listener of sessionListeners) listener();
}

export function getDeviceToken(): string | null { return deviceToken; }


export function deviceSessionHeaders(): Record<string, string> {
  return deviceToken ? { 'X-Lantern-Session': deviceToken } : {};
}

/**
 * The live auth broker's noncredential session context.
 *
 * The broker derives `session_ref` from the access epoch *and* the device token,
 * which is what makes this usable as an epoch fence: it is stable across every
 * voice publication — a new question, a queued clear, a finished turn, a second
 * client's session under the same token — and changes only when the epoch
 * rotates or the token is replaced. `voice.session_id` is deliberately not used
 * for this: a new voice session is a publication, not a new authority, and the
 * expectation counter that approximated the difference could both miss a real
 * rotation and excuse a real one.
 *
 * It is not an authorization credential: comparing it never replaces current
 * token authorization, and it is never sent or rendered as if it were a secret.
 */
export type SessionContext = { epoch: string; session_ref: string };

const SESSION_CONTEXT_SCHEMA = 'sage.lantern.session-context.v1';
export const SESSION_CONTEXT_UNAVAILABLE = 'session_context_unavailable';

/**
 * Parse a context field.
 *
 * Absent (`undefined`/`null`) means the device is not publishing one — the
 * locked path is refused by the server before this, so it is the legacy
 * unlocked case — and is returned as `null`. A value that is present but
 * malformed **throws**: an unverifiable context must never be read as "no
 * context", because that is exactly how a rotation would be ignored.
 */
export function parseSessionContext(value: unknown): SessionContext | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== 'object' || Array.isArray(value)) throw new Error(SESSION_CONTEXT_UNAVAILABLE);
  const context = value as Record<string, unknown>;
  if (Object.keys(context).length !== 3 || context.schema !== SESSION_CONTEXT_SCHEMA
    || typeof context.epoch !== 'string' || !/^[0-9a-f]{32}$/.test(context.epoch)
    || typeof context.session_ref !== 'string' || !/^[0-9a-f]{64}$/.test(context.session_ref)) {
    throw new Error(SESSION_CONTEXT_UNAVAILABLE);
  }
  return { epoch: context.epoch, session_ref: context.session_ref };
}

/** A status reading's context: absent, valid, or present and unusable. */
export type SessionContextReading = SessionContext | null | 'invalid';

/** Read a context field without throwing; malformed becomes `'invalid'`. */
export function readSessionContext(value: unknown): SessionContextReading {
  try { return parseSessionContext(value); } catch { return 'invalid'; }
}

/** Two contexts are the same authority only when both halves match. */
export function sessionContextEqual(a: SessionContext | null, b: SessionContext | null): boolean {
  if (a === null || b === null) return a === b;
  return a.epoch === b.epoch && a.session_ref === b.session_ref;
}

/** What a validated reading's context did to the observed authority. */
export type ContextObservation = 'legacy' | 'established' | 'same' | 'changed' | 'refused';

/**
 * Report the context a validated reading published, and judge it.
 *
 * This is the same-token half of the invalidation seam: rotating the epoch does
 * not change the device token, so `setDeviceToken` never fires and nothing
 * subscribed to it would learn the authority moved. Reporting the observation
 * here — rather than inventing a second channel beside it — keeps
 * `subscribeDeviceSession` the one thing a protected surface subscribes to.
 *
 * `refused` covers both ways a reading cannot be compared: a malformed context,
 * and a missing one on a device that was publishing contexts a moment ago. Both
 * are refused rather than read as "no context", which is exactly how a rotation
 * would go unnoticed.
 */
export function observeSessionContext(reading: SessionContextReading): ContextObservation {
  if (reading === 'invalid') return loseAuthority();
  if (reading === null) {
    // A device that was publishing a context and now publishes none is the same
    // refusal, never the legacy path: reading it as legacy would accept an
    // unstamped reply on the strength of a reading that failed.
    if (observedState === 'context' || observedState === 'lost') return loseAuthority();
    observedState = 'legacy';
    return 'legacy';
  }
  const previous = observedContext;
  const established = observedState;
  observedContext = reading;
  observedState = 'context';
  if (established === 'context') {
    if (sessionContextEqual(previous, reading)) return 'same';
    authorityGeneration += 1;
    for (const listener of sessionListeners) listener();
    return 'changed';
  }
  // Establishing over a lost or legacy authority is a re-key too, so anything
  // captured beforehand stays stale even when the context reads the same.
  authorityGeneration += 1;
  return 'established';
}

/**
 * The reading could not be compared, so the authority it was compared against
 * is gone.
 *
 * Refusing the reading is not enough. Keeping the last good context would leave
 * `establishedSessionContext()` advertising an authority this client can no
 * longer verify, and a well-stamped reply issued under it would still fence as
 * `ok`. So the authority is dropped, the generation moves, and the *one* seam is
 * told — which is what invalidates retained content, cached consent challenges
 * and in-flight work in the same tick, rather than leaving them to be cleared by
 * whatever the next reading happens to say.
 */
function loseAuthority(): ContextObservation {
  if (observedState !== 'lost') {
    observedContext = null;
    observedState = 'lost';
    authorityGeneration += 1;
    for (const listener of sessionListeners) listener();
  }
  return 'refused';
}

/**
 * The authority this client is currently operating under.
 *
 * `unknown` and `legacy` are deliberately different answers. `legacy` is a
 * device that publishes no context — the unlocked path — where a reply carrying
 * none is the correct shape. `unknown` means no validated reading has
 * established anything yet, so a reply cannot be judged at all and a protected
 * consumer must refuse rather than adopt on the strength of a guess.
 */
export type EstablishedContext =
  | { state: 'unknown' }
  | { state: 'legacy'; generation: number }
  | { state: 'lost' }
  | { state: 'context'; context: SessionContext; generation: number };

export function establishedSessionContext(): EstablishedContext {
  if (observedState === 'legacy') return { state: 'legacy', generation: authorityGeneration };
  if (observedState === 'lost') return { state: 'lost' };
  if (observedState === 'context' && observedContext !== null) {
    return { state: 'context', context: observedContext, generation: authorityGeneration };
  }
  return { state: 'unknown' };
}

/** What a protected reply's own context says about whether it may be adopted. */
export type ReplyFence = 'ok' | 'unverifiable' | 'not-issued-under' | 'moved';

/**
 * Judge a protected reply against the authority its request was issued under.
 *
 * Both halves are needed and neither implies the other. The first says the reply
 * is the one this request was admitted under: a rotation that lands while the
 * command is in flight is admitted by the runtime under the *new* epoch, so the
 * delayed reply carries it and agrees with itself. The second says that
 * authority is still the one this client holds — a reply correctly issued under
 * A and delivered after the epoch moved to B was true when it was written and is
 * obsolete by the time it lands, which is exactly when a consumer would
 * otherwise adopt a conversation, a selection or a submission into a world that
 * has moved on.
 *
 * `reply` is the context read off the reply (`replySessionContext`), which for a
 * locked device is attached to every successful protected reply and absent only
 * on the legacy path and on errors. Nothing is invented for a reply that carries
 * no stamp: against an established authority it is refused as
 * `not-issued-under`, which is the safe direction.
 */
export function fenceProtectedReply(issuedUnder: EstablishedContext, reply: SessionContext | null): ReplyFence {
  if (issuedUnder.state === 'unknown') return 'unverifiable';
  if (issuedUnder.state === 'lost') return 'not-issued-under';
  const expected = issuedUnder.state === 'legacy' ? null : issuedUnder.context;
  if (!sessionContextEqual(expected, reply)) return 'not-issued-under';
  const held = establishedSessionContext();
  if (held.state === 'unknown') return 'unverifiable';
  if (held.state === 'lost') return 'not-issued-under';
  const current = held.state === 'legacy' ? null : held.context;
  if (!sessionContextEqual(current, reply)) return 'moved';
  // The expectation belongs to the authority object it was captured from, not
  // merely to the context it named. Once that object has been re-keyed the reply
  // cannot be judged against it again, however familiar its context looks — this
  // is what separates "still current" from "issued before the move and now back
  // under a context that reads the same".
  if (issuedUnder.generation !== authorityGeneration) return 'not-issued-under';
  return 'ok';
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
