import { deviceView, statusIsFresh, type DeviceStatus } from './device-state.ts';

export type QuestionHandoff = { sessionId: string; question: string };

// The helper clears transcript briefly when starting the typed answer worker.
// Retain only the explicitly submitted draft for this same, still-live session.
export function handoffQuestion(status: DeviceStatus | null, connected: boolean, draft: QuestionHandoff | null): string | null {
  if (!status || !connected || !statusIsFresh(status) || !draft
      || status.voice.session_id !== draft.sessionId
      || !['processing', 'complete'].includes(deviceView(status, connected).phase)) return null;
  return draft.question;
}
