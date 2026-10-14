import { parseDeviceStatus, type DeviceStatus } from './device-state.ts';
import { sessionContextEqual, type SessionContextReading } from './device-session.ts';

export type LiveVoiceStatus = {
  schema: 'sage.lantern.voice-status.v1';
  observed_at: string;
  session_context: SessionContextReading;
  voice: DeviceStatus['voice'];
};

const CAPABILITIES = ['touch_ready', 'correction_ready', 'review_ready', 'typed_ready', 'photo_ready'] as const;

function unknownTelemetry(): Pick<DeviceStatus, 'local_voice_ready' | 'audio' | 'radio' | 'services'> {
  return { local_voice_ready: false, audio: { present: false, detail: 'Waiting for device telemetry' },
    radio: { present: false, detail: 'Waiting for device telemetry' }, services: {} };
}

function sameContext(a: SessionContextReading, b: SessionContextReading): boolean {
  return a !== 'invalid' && b !== 'invalid' && sessionContextEqual(a, b);
}

export function parseLiveVoiceStatus(value: unknown): LiveVoiceStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)
    || (value as Record<string, unknown>).schema !== 'sage.lantern.voice-status.v1') throw new Error('Invalid live voice status');
  const raw = value as Record<string, unknown>;
  // Reuse the existing content/context validator. These conservative placeholders
  // are validation inputs only and never establish readiness.
  const parsed = parseDeviceStatus({ ...raw, schema: 'sage.lantern.local-status.v1', ...unknownTelemetry() });
  const voice = { ...parsed.voice };
  // A hot reading must never grant capabilities, even if an unexpected server
  // field appears. Only the full status path verifies those.
  for (const key of CAPABILITIES) delete voice[key];
  return { schema: 'sage.lantern.voice-status.v1', observed_at: parsed.observed_at,
    session_context: parsed.session_context, voice };
}

export function mergeLiveVoiceStatus(current: DeviceStatus | null, live: LiveVoiceStatus,
  telemetryObservedAt: string | null, now = Date.now()): DeviceStatus {
  const age = telemetryObservedAt === null ? Infinity : now - Date.parse(telemetryObservedAt);
  const retainTelemetry = !!current && sameContext(current.session_context, live.session_context)
    && age >= -30000 && age <= 15000;
  const base = retainTelemetry ? current! : unknownTelemetry();
  const voice = { ...live.voice };
  if (retainTelemetry && current!.voice.session_id === live.voice.session_id && current!.voice.phase === live.voice.phase) {
    for (const key of CAPABILITIES) voice[key] = current!.voice[key];
  }
  return { ...base, schema: 'sage.lantern.local-status.v1', observed_at: live.observed_at,
    session_context: live.session_context, voice };
}

export function mergeTelemetryStatus(current: DeviceStatus, telemetry: DeviceStatus): DeviceStatus | null {
  if (!sameContext(current.session_context, telemetry.session_context)) return null;
  const voice = { ...current.voice };
  // A slow hardware reply cannot replace newer answer text, phase or session.
  // Readiness is only transferable to the exact publication it checked.
  if (current.voice.session_id === telemetry.voice.session_id && current.voice.phase === telemetry.voice.phase) {
    for (const key of CAPABILITIES) voice[key] = telemetry.voice[key];
  }
  return { ...telemetry, observed_at: current.observed_at, session_context: current.session_context, voice };
}

export function liveVoicePollDelay(status: DeviceStatus | null, pending = false): number {
  return pending || ['listening', 'review', 'processing'].includes(status?.voice.phase ?? '') ? 100 : 700;
}
