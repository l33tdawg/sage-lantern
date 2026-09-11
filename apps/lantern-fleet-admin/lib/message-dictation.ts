export type DictationStatus = { phase: 'queued' | 'recording' | 'transcribing' | 'cancelling' | 'cancelled' | 'review' | 'error'; text?: string };

export function parseDictationStatus(value: unknown, sessionId: string): DictationStatus {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Invalid dictation');
  const item = value as Record<string, unknown>;
  if (item.session_id !== sessionId || item.purpose !== 'message' || typeof item.phase !== 'string' || !['queued', 'recording', 'transcribing', 'cancelling', 'cancelled', 'review', 'error'].includes(item.phase)) throw new Error('Invalid dictation');
  if (item.phase === 'review') {
    if (typeof item.text !== 'string' || !item.text.trim() || new TextEncoder().encode(item.text).length > 2048 || /[\u0000-\u001f\u007f\u2028-\u202e\u2066-\u2069]/.test(item.text)) throw new Error('Invalid dictation');
    return { phase: 'review', text: item.text };
  }
  if (item.text !== undefined && item.text !== '') throw new Error('Invalid dictation');
  return { phase: item.phase as DictationStatus['phase'] };
}

export function replaceParagraph(paragraphs: string[], index: number | null, text: string): string[] {
  if (typeof text !== 'string' || !text.trim() || new TextEncoder().encode(text).length > 2048 || /[\u0000-\u001f\u007f\u2028-\u202e\u2066-\u2069]/.test(text)) throw new Error('Invalid paragraph');
  if (index !== null && (!Number.isInteger(index) || index < 0 || index >= paragraphs.length)) throw new Error('Invalid paragraph');
  const result = index === null ? [...paragraphs, text] : paragraphs.map((old, position) => position === index ? text : old);
  if (result.length > 32 || new TextEncoder().encode(result.join('\n\n')).length > 8192) throw new Error('Message too long');
  return result;
}
