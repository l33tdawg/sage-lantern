export const OBSERVATION_SCHEMA = 'sage.lantern.management.observation.v1';

const MAX_SAFE_INTEGER = 9_007_199_254_740_991;
const MAX_STATE_BYTES = 65_536;
const MAX_FUTURE_SKEW_MS = 5 * 60 * 1_000;
const MAX_FRESHNESS_MS = 24 * 60 * 60 * 1_000;

type ProtocolValue = null | string | boolean | number | ProtocolValue[] | { [key: string]: ProtocolValue };

export type Observation = {
  schema: string;
  device_id: string;
  sequence: number;
  observed_at: string;
  freshness_expires_at: string;
  clock_confidence: string;
  source_fingerprint: string;
  state: Record<string, ProtocolValue>;
  state_hash: string;
};

export type SignedObservation = { observation: Observation; signature: string };

export type RegisteredManagementIdentity = {
  deviceId: string;
  fingerprint: string;
  publicKey: string;
  lastAcceptedSequence: number | null;
};

export type VerifiedObservation = {
  deviceId: string;
  sequence: number;
  observedAt: string;
  ingestedAt: string;
  clockConfidence: string;
  sourceFingerprint: string;
  stateJson: string;
  stateHash: string;
  signature: string;
};

export function canonicalJson(value: ProtocolValue): string {
  validateValue(value, 'value');
  if (value === null || typeof value === 'boolean' || typeof value === 'number' || typeof value === 'string') {
    return JSON.stringify(value);
  }
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value)
    .sort()
    .map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`)
    .join(',')}}`;
}

export async function verifySignedObservation(
  raw: unknown,
  identity: RegisteredManagementIdentity,
  now = new Date(),
): Promise<VerifiedObservation> {
  if (!isExactObject(raw, ['observation', 'signature'])) throw new Error('signed observation fields are invalid');
  const signature = raw.signature;
  if (typeof signature !== 'string') throw new Error('observation signature must be base64');
  const value = raw.observation;
  const fields = [
    'schema', 'device_id', 'sequence', 'observed_at', 'freshness_expires_at', 'clock_confidence',
    'source_fingerprint', 'state', 'state_hash',
  ];
  if (!isExactObject(value, fields)) throw new Error('observation fields do not match the v1 schema');
  const observation = value as Observation;
  if (observation.schema !== OBSERVATION_SCHEMA) throw new Error('unsupported observation schema');
  if (observation.device_id !== identity.deviceId) throw new Error('observation targets a different device record');
  if (observation.source_fingerprint !== identity.fingerprint) throw new Error('observation identity is not registered');
  if (!Number.isSafeInteger(observation.sequence) || observation.sequence < 0) throw new Error('invalid observation sequence');
  if (identity.lastAcceptedSequence !== null && observation.sequence <= identity.lastAcceptedSequence) {
    throw new Error('observation sequence is stale or replayed');
  }
  if (typeof observation.clock_confidence !== 'string' || !observation.clock_confidence) {
    throw new Error('clock confidence is required');
  }
  if (!isPlainObject(observation.state)) throw new Error('observation state must be an object');
  validateValue(observation.state, 'observation state');
  const stateJson = canonicalJson(observation.state);
  if (new TextEncoder().encode(stateJson).byteLength > MAX_STATE_BYTES) throw new Error('observation state exceeds 65536 bytes');

  const observedAt = parseTimestamp(observation.observed_at, 'observed_at');
  const expiresAt = parseTimestamp(observation.freshness_expires_at, 'freshness_expires_at');
  if (expiresAt.getTime() <= observedAt.getTime() || expiresAt.getTime() - observedAt.getTime() > MAX_FRESHNESS_MS) {
    throw new Error('observation freshness window is invalid');
  }
  if (observedAt.getTime() > now.getTime() + MAX_FUTURE_SKEW_MS) throw new Error('observation time is too far in the future');
  if (expiresAt.getTime() <= now.getTime()) throw new Error('observation is stale');

  const stateHash = await sha256Hex(stateJson);
  if (observation.state_hash !== stateHash) throw new Error('observation state hash is invalid');
  const publicKey = decodeBase64(identity.publicKey, 32, 'management public key');
  const signatureBytes = decodeBase64(signature, 64, 'observation signature');
  const key = await crypto.subtle.importKey('raw', publicKey, { name: 'Ed25519' }, false, ['verify']);
  const valid = await crypto.subtle.verify(
    { name: 'Ed25519' },
    key,
    signatureBytes,
    new TextEncoder().encode(canonicalJson(observation as unknown as ProtocolValue)),
  );
  if (!valid) throw new Error('observation signature is invalid');

  const fingerprint = `sha256:${await sha256Hex(publicKey)}`;
  if (fingerprint !== identity.fingerprint) throw new Error('registered fingerprint does not match public key');
  return {
    deviceId: observation.device_id,
    sequence: observation.sequence,
    observedAt: observation.observed_at,
    ingestedAt: now.toISOString(),
    clockConfidence: observation.clock_confidence,
    sourceFingerprint: observation.source_fingerprint,
    stateJson,
    stateHash,
    signature,
  };
}

function validateValue(value: unknown, path: string): asserts value is ProtocolValue {
  if (value === null || typeof value === 'string' || typeof value === 'boolean') return;
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || Math.abs(value) > MAX_SAFE_INTEGER) {
      throw new Error(`${path} must contain only cross-platform safe integers`);
    }
    return;
  }
  if (Array.isArray(value)) {
    value.forEach((item, index) => validateValue(item, `${path}[${index}]`));
    return;
  }
  if (isPlainObject(value)) {
    Object.entries(value).forEach(([key, item]) => validateValue(item, `${path}.${key}`));
    return;
  }
  throw new Error(`${path} contains an unsupported value`);
}

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value) && Object.getPrototypeOf(value) === Object.prototype;
}

function isExactObject(value: unknown, fields: string[]): value is Record<string, unknown> {
  return isPlainObject(value) && Object.keys(value).sort().join('\0') === [...fields].sort().join('\0');
}

function parseTimestamp(value: unknown, name: string): Date {
  if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) throw new Error(`${name} must be a timezone-aware timestamp`);
  const parsed = new Date(value);
  if (!Number.isFinite(parsed.getTime())) throw new Error(`${name} must be a valid timestamp`);
  return parsed;
}

function decodeBase64(value: string, length: number, name: string): Uint8Array<ArrayBuffer> {
  if (!/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(value)) {
    throw new Error(`${name} must be canonical base64`);
  }
  const bytes = Uint8Array.from(atob(value), (character) => character.charCodeAt(0));
  if (bytes.byteLength !== length) throw new Error(`${name} has an invalid length`);
  return bytes;
}

async function sha256Hex(value: string | Uint8Array<ArrayBuffer>): Promise<string> {
  const bytes = typeof value === 'string' ? new TextEncoder().encode(value) : value;
  const digest = new Uint8Array(await crypto.subtle.digest('SHA-256', bytes));
  return [...digest].map((byte) => byte.toString(16).padStart(2, '0')).join('');
}
