import { deviceView, questionServiceRunning, statusIsFresh, type DeviceStatus } from './device-state.ts';

export type QuestionHandoff = { sessionId: string; question: string };

// The helper clears transcript briefly when starting the typed answer worker.
// Retain only the explicitly submitted draft for this same, still-live session.
export function handoffQuestion(status: DeviceStatus | null, connected: boolean, draft: QuestionHandoff | null): string | null {
  // Readiness still gates the browser-held draft: an already-submitted question
  // is device evidence, a draft is UI memory, so a half-dead lane never shows
  // the draft as if the device had accepted it.
  if (!status || !connected || !statusIsFresh(status) || !questionServiceRunning(status) || !draft
      || status.voice.session_id !== draft.sessionId
      || !['processing', 'complete'].includes(deviceView(status, connected).phase)) return null;
  return draft.question;
}
