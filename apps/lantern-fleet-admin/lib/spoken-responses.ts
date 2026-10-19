import { deviceAuth, getDeviceToken } from './device-session.ts';

/**
 * The user's answer-read-aloud preference.
 *
 * Deliberately the same shape as `power-settings.ts`: a revisioned record read
 * with `settings/spoken/get` and written with `settings/spoken/set` using
 * compare-and-set, so a stale write is refused instead of winning a race.
 *
 * The runtime is the authority. Its validator requires **exactly** `revision` and
 * `spoken_responses`, and requires `spoken_responses` to be a real boolean — `1`,
 * `0` and `"true"` are refused rather than coerced, because a truthy value that
 * reaches the speaker is how a configuration mistake becomes a device that starts
 * talking. This module mirrors those rules so the screen can explain a refusal
 * before sending it, and it does not invent extra fields the runtime would reject.
 *
 * Off by default: an untouched device must not speak.
 *
 * `deviceAuth`'s action union does not yet name these two actions — it is declared
 * inline in `lib/device-session.ts`, which another session is mid-edit on, so this
 * module does not widen it. `spokenAuth` below casts the *function* once to the two
 * actions and their body shape; casting the action argument instead is a type error,
 * because an unrelated member of the existing union makes the body resolve to
 * `never`. When the union gains `'settings/spoken/get' | 'settings/spoken/set'`,
 * delete `spokenAuth` and call `deviceAuth` directly — the action strings must not
 * change, because the broker routes and the runtime validator are pinned to them.
 */

/// The action that reads the preference.
export const SPOKEN_GET_ACTION = 'settings/spoken/get' as const;
/// The action that changes it.
export const SPOKEN_SET_ACTION = 'settings/spoken/set' as const;

/// The only keys the runtime will accept in a stored record or a reply.
export const SPOKEN_REPLY_KEYS = ['revision', 'spoken_responses'] as const;

export type SpokenResponses = { revision: number; spoken_responses: boolean };

type SpokenAction = typeof SPOKEN_GET_ACTION | typeof SPOKEN_SET_ACTION;
type SpokenBody = { token: string; expected_revision?: number; spoken_responses?: boolean };

/**
 * `deviceAuth` for the two spoken actions only, until the shared union names them.
 * The cast is confined to this one line and adds no action beyond these two.
 */
const spokenAuth = deviceAuth as unknown as (
  action: SpokenAction, body: SpokenBody,
) => Promise<Record<string, unknown>>;

/**
 * Accept a reply only if it is exactly what the runtime is allowed to send.
 *
 * Extra keys are refused as firmly as missing ones: the runtime will not produce
 * them, so their presence means something other than the runtime answered.
 */
export function parseSpokenResponses(value: unknown): SpokenResponses {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Spoken responses unavailable');
  const result = value as Record<string, unknown>;
  if (Object.keys(result).sort().join(',') !== [...SPOKEN_REPLY_KEYS].sort().join(',')
      || !Number.isSafeInteger(result.revision) || Number(result.revision) < 0
      || typeof result.spoken_responses !== 'boolean') throw new Error('Spoken responses unavailable');
  return result as SpokenResponses;
}

export async function loadSpokenResponses(): Promise<SpokenResponses> {
  const token = getDeviceToken();
  if (!token) throw new Error('Device locked');
  return parseSpokenResponses(await spokenAuth(SPOKEN_GET_ACTION, { token }));
}

/**
 * Write the preference, refusing a stale revision.
 *
 * The current revision is sent as `expected_revision`, so two screens racing each
 * other cannot silently overwrite one another — the loser is told to reload.
 */
export async function saveSpokenResponses(current: SpokenResponses, enabled: boolean): Promise<SpokenResponses> {
  const token = getDeviceToken();
  if (!token) throw new Error('Device locked');
  parseSpokenResponses({ ...current, spoken_responses: enabled });
  return parseSpokenResponses(await spokenAuth(SPOKEN_SET_ACTION, {
    token, expected_revision: current.revision, spoken_responses: enabled,
  }));
}
