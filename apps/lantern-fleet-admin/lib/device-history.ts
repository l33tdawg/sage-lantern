export type HistoryItem = { session_id: string; created_at: string; transcript: string; response: string; has_photo?: boolean; thread_id?: string; supersedes?: string };
export type DeviceHistory = { enabled: boolean; items: HistoryItem[]; saving_off_keeps_items?: boolean; photo_saving_supported?: boolean; photos_enabled?: boolean; sage_backed?: boolean; photo_storage?: string; photo_removal?: string; memory_warning?: string | null };

export function parseDeviceHistory(value: unknown): DeviceHistory {
  if (!value || typeof value !== 'object') throw new Error('History unavailable');
  const history = value as DeviceHistory;
  for (const field of ['photo_saving_supported', 'photos_enabled', 'sage_backed'] as const) {
    if (history[field] != null && typeof history[field] !== 'boolean') throw new Error('History unavailable');
  }
  // `photo_removal` is carried through as the runtime stated it rather than
  // checked against a list of known values. It is the one field that says what a
  // deletion did to the bytes, so a value this build has never heard of must
  // still reach the deletion wording, where anything other than the known
  // "history_link_only" is read as "the photo may have been kept". Rejecting an
  // unknown value here would fail the whole history read, and a device whose
  // runtime learned a new answer would lose its whole gallery rather than one
  // sentence's precision.
  for (const field of ['photo_storage', 'photo_removal'] as const) {
    if (history[field] != null && typeof history[field] !== 'string') throw new Error('History unavailable');
  }
  if (history.memory_warning != null && typeof history.memory_warning !== 'string') throw new Error('History unavailable');
  if (typeof history.enabled !== 'boolean' || (history.saving_off_keeps_items != null && typeof history.saving_off_keeps_items !== 'boolean') || !Array.isArray(history.items) || history.items.length > 100
    || history.items.some((item) => !item || typeof item.session_id !== 'string'
      || typeof item.created_at !== 'string' || !Number.isFinite(Date.parse(item.created_at))
      || typeof item.transcript !== 'string' || typeof item.response !== 'string'
      || ['thread_id', 'supersedes'].some((key) => { const value = (item as unknown as Record<string, unknown>)[key]; return value != null && (typeof value !== 'string' || !/^[0-9a-f-]{36}$/.test(value)); })
      || (item.has_photo != null && typeof item.has_photo !== 'boolean'))) throw new Error('History unavailable');
  if (new Set(history.items.map((item) => item.session_id)).size !== history.items.length) throw new Error('History unavailable');
  return history;
}

export function parseHistoryPhoto(value: unknown): string {
  if (!value || typeof value !== 'object') throw new Error('Photo unavailable');
  const encoded = (value as Record<string, unknown>).image_base64;
  if (typeof encoded !== 'string' || !encoded.startsWith('/9j/') || encoded.length > 2796204
    || encoded.length % 4 !== 0 || !/^[A-Za-z0-9+/]+={0,2}$/.test(encoded)) throw new Error('Photo unavailable');
  return `data:image/jpeg;base64,${encoded}`;
}
