export type RadioTraffic = {
  id: string; dequeued_at_unix_ms: number; age_ms: number; snr_db: number | null;
  rssi_dbm: null; rf_received_at_unix_ms: null;
  application_authenticated: false; direct_neighbor_verified: false;
};

export type RadioStatus = {
  health?: RadioHealth;
  schema: 'sage.lantern.radio-events.v1'; observed_at_unix_ms: number;
  valid_for_ms: 2000; window_ms: 60000; serial_identity_verified: boolean;
  firmware: string | null; receiving: boolean; profile: null;
  repeater: { enabled: boolean; source: 'firmware_info' } | null;
  saved_contacts: { available: false; count: null };
  neighbors: { available: false; reason: 'no_rf_neighbor_discovery' };
  last_heard: { source: 'firmware_dequeued_contact_messages'; entries: RadioTraffic[] };
};

export type RadioHealth = {
  battery_mv: number; uptime_seconds: number; error_flags: number; tx_queue: number;
  noise_floor_dbm: number; last_rssi_dbm: number; last_snr_db: number;
  tx_airtime_seconds: number; rx_airtime_seconds: number;
  packets_received: number; packets_sent: number; sent_flood: number; sent_direct: number;
  received_flood: number; received_direct: number; receive_errors: number;
  discovered_nodes: { public_key: string; name: string | null; age_seconds: number;
    source: 'firmware_advertisement'; direct_neighbor_verified: false }[];
};

function validHealth(value: unknown): value is RadioHealth {
  if (!record(value)) return false;
  const counters = 'battery_mv uptime_seconds error_flags tx_queue tx_airtime_seconds rx_airtime_seconds packets_received packets_sent sent_flood sent_direct received_flood received_direct receive_errors';
  if (!keys(value, `${counters} noise_floor_dbm last_rssi_dbm last_snr_db discovered_nodes`)
    || counters.split(' ').some((key) => typeof value[key] !== 'number' || !Number.isInteger(value[key]) || value[key] < 0 || value[key] > 0xffffffff)
    || typeof value.noise_floor_dbm !== 'number' || !Number.isInteger(value.noise_floor_dbm) || value.noise_floor_dbm < -32768 || value.noise_floor_dbm > 32767
    || typeof value.last_rssi_dbm !== 'number' || !Number.isInteger(value.last_rssi_dbm) || value.last_rssi_dbm < -128 || value.last_rssi_dbm > 127
    || typeof value.last_snr_db !== 'number' || !Number.isFinite(value.last_snr_db) || value.last_snr_db < -32 || value.last_snr_db > 31.75
    || !Array.isArray(value.discovered_nodes) || value.discovered_nodes.length > 256) return false;
  const seen = new Set<string>();
  return value.discovered_nodes.every((node) => {
    if (!record(node) || !keys(node, 'public_key name age_seconds source direct_neighbor_verified')
      || typeof node.public_key !== 'string' || !/^[0-9a-f]{64}$/.test(node.public_key) || seen.has(node.public_key)
      || (node.name !== null && (typeof node.name !== 'string' || node.name.length > 64))
      || typeof node.age_seconds !== 'number' || !Number.isFinite(node.age_seconds) || node.age_seconds < 0 || node.age_seconds >= 300
      || node.source !== 'firmware_advertisement' || node.direct_neighbor_verified !== false) return false;
    seen.add(node.public_key); return true;
  });
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function keys(value: Record<string, unknown>, names: string): boolean {
  const expected = names.split(' ');
  return Object.keys(value).length === expected.length && expected.every((name) => Object.hasOwn(value, name));
}

function positive(value: unknown): value is number {
  return typeof value === 'number' && Number.isSafeInteger(value) && value > 0;
}

export function parseRadioStatus(value: unknown): RadioStatus | undefined {
  if (record(value) && Object.hasOwn(value, 'health')) {
    const { health, ...base } = value;
    const parsed = parseRadioStatus(base);
    return parsed && validHealth(health) ? { ...parsed, health } : undefined;
  }
  if (!record(value) || !keys(value, 'schema observed_at_unix_ms valid_for_ms window_ms serial_identity_verified firmware receiving repeater profile saved_contacts neighbors last_heard')
    || value.schema !== 'sage.lantern.radio-events.v1' || !positive(value.observed_at_unix_ms)
    || value.valid_for_ms !== 2000 || value.window_ms !== 60000
    || typeof value.serial_identity_verified !== 'boolean' || typeof value.receiving !== 'boolean'
    || (value.receiving && !value.serial_identity_verified) || value.profile !== null
    || (value.serial_identity_verified ? !['v1.17.1-d929643', 'v1.17.1-d929643-r1', 'v1.17.1-d929643-r2', 'v1.17.1-d929643-r3', 'v1.17.1-d929643-r4'].includes(value.firmware as string) : value.firmware !== null)) return undefined;
  if (value.serial_identity_verified) {
    const repeater = value.repeater;
    if (!record(repeater) || !keys(repeater, 'enabled source') || typeof repeater.enabled !== 'boolean'
      || repeater.source !== 'firmware_info') return undefined;
  } else if (value.repeater !== null) return undefined;
  const saved = value.saved_contacts, neighbors = value.neighbors, heard = value.last_heard;
  if (!record(saved) || !keys(saved, 'available count') || saved.available !== false || saved.count !== null
    || !record(neighbors) || !keys(neighbors, 'available reason') || neighbors.available !== false || neighbors.reason !== 'no_rf_neighbor_discovery'
    || !record(heard) || !keys(heard, 'source entries') || heard.source !== 'firmware_dequeued_contact_messages'
    || !Array.isArray(heard.entries) || heard.entries.length > 32
    || (heard.entries.length > 0 && !value.receiving)) return undefined;
  const seen = new Set<string>();
  for (const entry of heard.entries) {
    if (!record(entry) || !keys(entry, 'id dequeued_at_unix_ms age_ms snr_db rssi_dbm rf_received_at_unix_ms application_authenticated direct_neighbor_verified')
      || typeof entry.id !== 'string' || !/^[0-9a-f]{64}$/.test(entry.id) || seen.has(entry.id)
      || !positive(entry.dequeued_at_unix_ms) || typeof entry.age_ms !== 'number' || !Number.isSafeInteger(entry.age_ms) || entry.age_ms < 0 || entry.age_ms >= 60000
      || (entry.snr_db !== null && (typeof entry.snr_db !== 'number' || !Number.isFinite(entry.snr_db) || entry.snr_db < -32 || entry.snr_db > 31.75 || !Number.isInteger(entry.snr_db * 4)))
      || entry.rssi_dbm !== null || entry.rf_received_at_unix_ms !== null
      || entry.application_authenticated !== false || entry.direct_neighbor_verified !== false) return undefined;
    seen.add(entry.id);
  }
  return value as RadioStatus;
}

export function radioStatusIsFresh(startedAt: number, now: number): boolean {
  return Number.isFinite(startedAt) && Number.isFinite(now) && now >= startedAt && now - startedAt < 2000;
}
