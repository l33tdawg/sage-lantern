import type { RadioStatus } from './radio-status.ts';

export type RadioSample = { token: string; connected: boolean; bars: number; snr: number | null; rssi: number | null; heardAt: number; checkedAt: number };
let sample: Sample | null = null;
let counter: { packets: number; uptime: number } | null = null;

type Sample = RadioSample;

// Product ladder over the measured per-packet SNR. LoRa demodulates below 0 dB,
// so a received packet always shows at least one bar; RSSI is reported next to
// it but is not used to size the bars (the stock firmware often reads zero).
export function linkBars(snr: number): number {
  return snr >= 10 ? 4 : snr >= 5 ? 3 : snr >= 0 ? 2 : 1;
}

export function clearRadioSample() { sample = null; counter = null; }

export function getRadioSample(token: string | null) {
  return token && sample?.token === token ? sample : null;
}

export function observeRadioSample(value: RadioStatus, token: string, now: number) {
  if (sample?.token !== token) clearRadioSample();
  if (sample && now < sample.checkedAt) return;
  const health = value.health;
  const reset = !!health && !!counter && (health.uptime_seconds < counter.uptime || health.packets_received < counter.packets);
  const received = !!health && !!counter && !reset && health.packets_received > counter.packets;
  sample = { token, connected: value.serial_identity_verified, checkedAt: now,
    heardAt: received ? now : reset ? 0 : sample?.heardAt ?? 0,
    snr: received && health ? health.last_snr_db : reset ? null : sample?.snr ?? null,
    rssi: received && health ? health.last_rssi_dbm : reset ? null : sample?.rssi ?? null,
    bars: received && health ? linkBars(health.last_snr_db) : reset ? 0 : sample?.bars ?? 0 };
  if (health) counter = { packets: health.packets_received, uptime: health.uptime_seconds };
}
