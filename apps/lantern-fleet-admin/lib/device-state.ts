import type { SageStatus } from './sage-status';
import { readSessionContext, type SessionContextReading } from './device-session.ts';

export type DeviceStatus = {
  schema: 'sage.lantern.local-status.v1';
  observed_at: string;
  local_voice_ready: boolean;
  audio: { present: boolean; detail: string; ptt_ready?: boolean; ready?: boolean; capture_verified?: boolean };
  radio: { present: boolean; detail: string; name?: string; frequency_mhz?: number; firmware?: string };
  voice: { phase: string; session_id?: string; transcript: string; response: string; follow_up_questions?: string[]; detail?: string; touch_ready?: boolean; correction_ready?: boolean; review_ready?: boolean; typed_ready?: boolean; review_id?: string | null; photo_ready?: boolean; has_photo?: boolean; progress_stage?: string | null; input_source?: 'physical' | 'touch' | 'typed' | null };
  /**
   * The broker's current context for this reading, the fence every other
   * protected value in this payload is judged against. `null` means the device
   * is not publishing one (legacy unlocked mode); `'invalid'` means one was
   * present and unusable, which is refused rather than treated as absent.
   */
  session_context: SessionContextReading;
  services: Record<string, string>;
  sage?: SageStatus;
  network?: { present: boolean; detail: string };
  uptime_seconds?: number;
  temperature_millicelsius?: number | null;
  storage_free_bytes?: number;
  storage_total_bytes?: number;
};

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

export function parseFollowUpQuestions(value: unknown): string[] {
  if (!Array.isArray(value) || value.length > 2 || value.some((q) => typeof q !== 'string'
    || !q.trim() || new TextEncoder().encode(q).length > 240 || /[\x00-\x1f\x7f<>]/.test(q))) return [];
  return [...new Map((value as string[]).map((q) => [q.trim().toLocaleLowerCase(), q.trim()])).values()];
}

export function parseDeviceStatus(value: unknown): DeviceStatus {
  if (!record(value) || value.schema !== 'sage.lantern.local-status.v1'
    || typeof value.observed_at !== 'string' || !Number.isFinite(Date.parse(value.observed_at))
    || typeof value.local_voice_ready !== 'boolean'
    || !record(value.audio) || typeof value.audio.present !== 'boolean' || typeof value.audio.detail !== 'string'
    || !record(value.radio) || typeof value.radio.present !== 'boolean' || typeof value.radio.detail !== 'string'
    || !record(value.voice) || typeof value.voice.phase !== 'string'
    || typeof value.voice.transcript !== 'string' || typeof value.voice.response !== 'string'
    || !record(value.services) || Object.values(value.services).some((state) => typeof state !== 'string')) {
    throw new Error('Invalid device status');
  }
  const radio = value.radio;
  if ((value.network != null && (!record(value.network) || typeof value.network.present !== 'boolean' || typeof value.network.detail !== 'string'))
    || ['uptime_seconds', 'temperature_millicelsius', 'storage_free_bytes', 'storage_total_bytes'].some((key) => value[key] != null && (typeof value[key] !== 'number' || !Number.isFinite(value[key])))) {
    throw new Error('Invalid device measurements');
  }
  if ((value.voice.detail !== undefined && typeof value.voice.detail !== 'string')
    || (value.voice.touch_ready !== undefined && typeof value.voice.touch_ready !== 'boolean')
    || (value.voice.correction_ready !== undefined && typeof value.voice.correction_ready !== 'boolean')
    || (value.voice.review_ready !== undefined && typeof value.voice.review_ready !== 'boolean')
    || (value.voice.typed_ready !== undefined && typeof value.voice.typed_ready !== 'boolean')
    || (value.voice.review_id != null && (typeof value.voice.review_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.voice.review_id)))
    || (value.voice.photo_ready !== undefined && typeof value.voice.photo_ready !== 'boolean')
    || (value.voice.has_photo !== undefined && typeof value.voice.has_photo !== 'boolean')
    || (value.voice.progress_stage != null && !['starting', 'transcribing', 'recalling', 'photo_analysis', 'generating', 'answering', 'speaking'].includes(String(value.voice.progress_stage)))
    || (value.voice.input_source != null && !['physical', 'touch', 'typed'].includes(String(value.voice.input_source)))
    || (value.voice.session_id != null && typeof value.voice.session_id !== 'string')
    || (value.audio.ptt_ready !== undefined && typeof value.audio.ptt_ready !== 'boolean')
    || (value.audio.ready !== undefined && typeof value.audio.ready !== 'boolean')
    || (value.audio.capture_verified !== undefined && typeof value.audio.capture_verified !== 'boolean')
    || ['name', 'firmware'].some((key) => radio[key] != null && typeof radio[key] !== 'string')
    || (value.radio.frequency_mhz != null && (typeof value.radio.frequency_mhz !== 'number' || !Number.isFinite(value.radio.frequency_mhz)))) {
    throw new Error('Invalid device status fields');
  }
  return { ...value, voice: { ...value.voice,
    follow_up_questions: value.voice.phase === 'complete' ? parseFollowUpQuestions(value.voice.follow_up_questions) : [],
    // Read from the payload, never spread through: a malformed context must be
    // visible to the caller as unusable rather than silently carried as data.
  }, session_context: readSessionContext(value.session_context) } as DeviceStatus;
}

export type DeviceView = {
  phase: 'unavailable' | 'ready' | 'listening' | 'review' | 'processing' | 'complete' | 'error';
  title: string;
  hint: string;
};

export function statusIsFresh(status: DeviceStatus, now = Date.now()): boolean {
  const age = now - Date.parse(status.observed_at);
  return age >= -30000 && age <= 15000;
}

export function answerIsVisible(status: DeviceStatus | null, connected: boolean): boolean {
  const view = deviceView(status, connected);
  return view.phase === 'complete' || (view.phase === 'processing' && !!status?.voice.response.trim());
}

export function waitingQuestion(status: DeviceStatus | null, connected: boolean): string | null {
  if (deviceView(status, connected).phase !== 'processing' || !status?.voice.transcript.trim()) return null;
  return status.voice.transcript;
}

// Why the spoken lane cannot start a new question; null when it can. This is a
// notice, never a dead end: the typed lane beside it needs no microphone.
export function voiceLaneNotice(status: DeviceStatus | null): string | null {
  if (!status) return 'Waiting for device status.';
  if (!status.audio.present) return 'Microphone disconnected';
  if (status.audio.ready !== true) return 'Audio is not ready';
  const physicalReady = !!(status.audio.ptt_ready && status.services.ptt === 'active');
  if (!status.local_voice_ready || (!physicalReady && !status.voice.touch_ready)
    || ['voice_loop', 'llm', 'personal_sage'].some((name) => status.services[name] !== 'active')) {
    return 'Voice is not ready';
  }
  return null;
}

// A new spoken turn may only start when the lane is fully ready; a live phase
// is judged by its own evidence (freshness plus the published session).
export function voiceLaneReady(status: DeviceStatus | null): boolean {
  return !!status && voiceLaneNotice(status) === null;
}

// The local question service itself (not the microphone): the runtime that
// owns review, typed questions, inference and history. A live turn is only
// believable while this is true, and it never requires audio.
export function questionServiceRunning(status: DeviceStatus | null): boolean {
  return !!status && status.voice.touch_ready === true && status.services.voice_loop === 'active';
}

// The typed lane is served by the same local question service; it is offered
// whenever that service is running and advertises it, audio or not.
export function typedLaneAvailable(status: DeviceStatus | null, connected: boolean): boolean {
  return connected && status?.voice.typed_ready === true && status.services.voice_loop === 'active';
}

export function deviceView(status: DeviceStatus | null, connected: boolean): DeviceView {
  if (!connected || !status) return { phase: 'unavailable', title: 'Reconnecting…', hint: 'Waiting for device status.' };
  // A live session is authoritative: an answer already produced, or a question
  // already reviewed, must stay on screen even if audio or a service drops
  // mid-turn. Only the idle path depends on the spoken lane being ready.
  switch (status.voice.phase) {
    case 'listening': return { phase: 'listening', title: 'Listening…', hint: status.voice.input_source === 'touch' ? status.voice.review_ready ? 'Tap Done to review your question, or Cancel.' : 'Tap Send when finished, or Cancel.' : 'Release the side button when finished.' };
    case 'review': return status.voice.review_ready && status.voice.review_id && status.voice.session_id
      ? { phase: 'review', title: status.voice.input_source === 'typed' ? 'Type your question' : 'Check your question', hint: status.voice.input_source === 'typed' ? 'Use the keyboard, then Submit to ask.' : 'Edit or record again, then Submit when it looks right.' }
      : { phase: 'unavailable', title: 'Question review unavailable', hint: 'The device interface needs to reconnect.' };
    case 'processing': {
      const titles: Record<string, string> = { starting: 'Starting…', transcribing: 'Understanding your question…', recalling: 'Checking saved knowledge…', photo_analysis: 'Analyzing your photo…', generating: 'Preparing your answer…', answering: 'Answering…', speaking: 'Preparing audio…' };
      return { phase: 'processing', title: titles[status.voice.progress_stage || ''] || 'Working locally…', hint: '' };
    }
    case 'complete': return status.voice.response.trim()
      ? { phase: 'complete', title: 'Your answer', hint: status.voice.touch_ready ? 'Tap Ask for another question.' : 'Hold the side button to ask again.' }
      : { phase: 'error', title: 'No answer received', hint: status.voice.touch_ready ? 'Tap Ask to try again.' : 'Hold the side button to try again.' };
    case 'error': {
      if (status.voice.detail === 'no_spoken_question') return { phase: 'error', title: 'No spoken question detected', hint: 'Try again. If there’s music or noise nearby, move somewhere quieter.' };
      const detail = status.voice.detail?.toLowerCase() ?? '';
      if (detail.includes('403') || detail.includes('approved agent')) {
        return { phase: 'error', title: 'Voice access needs setup', hint: 'Ask the operator to check local SAGE approval.' };
      }
      if (detail.includes('microphone')) return { phase: 'error', title: 'Could not record', hint: status.voice.typed_ready ? 'Check the microphone, or tap Type to write your question.' : 'Check the USB microphone.' };
      return { phase: 'error', title: 'Could not answer', hint: `${status.voice.touch_ready ? 'Tap Ask to try again.' : 'Hold the side button to try again.'} If it repeats, ask the operator.` };
    }
    case 'idle': break;
    // Any phase this build does not recognise — blank, unknown, or a name a
    // newer runtime invented. The publication says nothing this client can
    // interpret, so the copy may not claim the service is starting or that a
    // takeover happened: neither is evidenced by an unreadable reading.
    default: return { phase: 'unavailable', title: 'Voice status unavailable', hint: 'Waiting for the current device status.' };
  }
  const notice = voiceLaneNotice(status);
  if (notice === null) {
    return { phase: 'ready', title: 'What would you like to know?', hint: status.voice.touch_ready ? 'Tap. Ask. Learn' : 'Hold the side button to talk.' };
  }
  return typedLaneAvailable(status, connected)
    ? { phase: 'unavailable', title: 'Ask by typing', hint: `${notice}. Tap Type to write your question.` }
    : { phase: 'unavailable', title: notice, hint: 'Local services need to finish starting or be checked.' };
}
