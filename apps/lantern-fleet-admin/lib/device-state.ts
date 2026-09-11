import type { SageStatus } from './sage-status';

export type DeviceStatus = {
  schema: 'sage.lantern.local-status.v1';
  observed_at: string;
  local_voice_ready: boolean;
  audio: { present: boolean; detail: string; ptt_ready?: boolean; ready?: boolean; capture_verified?: boolean };
  radio: { present: boolean; detail: string; name?: string; frequency_mhz?: number; firmware?: string };
  voice: { phase: string; session_id?: string; transcript: string; response: string; follow_up_questions?: string[]; detail?: string; touch_ready?: boolean; correction_ready?: boolean; review_ready?: boolean; review_id?: string | null; photo_ready?: boolean; has_photo?: boolean; progress_stage?: string | null; input_source?: 'physical' | 'touch' | null };
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
    || (value.voice.review_id != null && (typeof value.voice.review_id !== 'string' || !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(value.voice.review_id)))
    || (value.voice.photo_ready !== undefined && typeof value.voice.photo_ready !== 'boolean')
    || (value.voice.has_photo !== undefined && typeof value.voice.has_photo !== 'boolean')
    || (value.voice.progress_stage != null && !['starting', 'transcribing', 'recalling', 'photo_analysis', 'generating', 'answering', 'speaking'].includes(String(value.voice.progress_stage)))
    || (value.voice.input_source != null && !['physical', 'touch'].includes(String(value.voice.input_source)))
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
  } } as DeviceStatus;
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

export function deviceView(status: DeviceStatus | null, connected: boolean): DeviceView {
  if (!connected || !status) return { phase: 'unavailable', title: 'Reconnecting…', hint: 'Waiting for device status.' };
  if (!status.audio.present) {
    return { phase: 'unavailable', title: 'Microphone disconnected', hint: 'Check the audio board’s USB cable.' };
  }
  if (status.audio.ready !== true) {
    return { phase: 'unavailable', title: 'Audio is not ready', hint: 'The audio board is detected but its capture bridge needs checking.' };
  }
  const physicalReady = status.audio.ptt_ready && status.services.ptt === 'active';
  const retry = status.voice.touch_ready ? 'Tap Ask to try again.' : 'Hold the side button to try again.';
  if (!status.local_voice_ready || (!physicalReady && !status.voice.touch_ready)
    || ['voice_loop', 'llm', 'personal_sage'].some((name) => status.services[name] !== 'active')) {
    return { phase: 'unavailable', title: 'Voice is not ready', hint: 'Local services need to finish starting or be checked.' };
  }
  switch (status.voice.phase) {
    case 'listening': return { phase: 'listening', title: 'Listening…', hint: status.voice.input_source === 'touch' ? status.voice.review_ready ? 'Tap Done to review your question, or Cancel.' : 'Tap Send when finished, or Cancel.' : 'Release the side button when finished.' };
    case 'review': return status.voice.review_ready && status.voice.review_id && status.voice.session_id
      ? { phase: 'review', title: 'Check your question', hint: 'Edit or record again, then Submit when it looks right.' }
      : { phase: 'unavailable', title: 'Question review unavailable', hint: 'The device interface needs to reconnect.' };
    case 'processing': {
      const titles: Record<string, string> = { starting: 'Starting…', transcribing: 'Understanding your question…', recalling: 'Checking saved knowledge…', photo_analysis: 'Analyzing your photo…', generating: 'Preparing your answer…', answering: 'Answering…', speaking: 'Preparing audio…' };
      return { phase: 'processing', title: titles[status.voice.progress_stage || ''] || 'Working locally…', hint: '' };
    }
    case 'complete': return status.voice.response.trim()
      ? { phase: 'complete', title: 'Your answer', hint: status.voice.touch_ready ? 'Tap Ask for another question.' : 'Hold the side button to ask again.' }
      : { phase: 'error', title: 'No answer received', hint: retry };
    case 'error': {
      if (status.voice.detail === 'no_spoken_question') return { phase: 'error', title: 'No spoken question detected', hint: 'Try again. If there’s music or noise nearby, move somewhere quieter.' };
      const detail = status.voice.detail?.toLowerCase() ?? '';
      if (detail.includes('403') || detail.includes('approved agent')) {
        return { phase: 'error', title: 'Voice access needs setup', hint: 'Ask the operator to check local SAGE approval.' };
      }
      if (detail.includes('microphone')) return { phase: 'error', title: 'Could not record', hint: `Check the USB microphone. ${retry}` };
      return { phase: 'error', title: 'Could not answer', hint: `${retry} If it repeats, ask the operator.` };
    }
    case 'idle': return { phase: 'ready', title: 'What would you like to know?', hint: status.voice.touch_ready ? 'Tap. Ask. Learn' : 'Hold the side button to talk.' };
    default: return { phase: 'unavailable', title: 'Starting voice…', hint: 'Waiting for the voice service.' };
  }
}
