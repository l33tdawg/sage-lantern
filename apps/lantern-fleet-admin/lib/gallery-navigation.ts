/**
 * Gallery navigation and state derivation for the Lantern device screen.
 *
 * Pure functions only: no React, no fetch, no object URLs, no device access. The
 * gallery component renders what these return, so which records count as photos,
 * what the empty and unavailable states are allowed to say, which image the
 * viewer is on after a deletion, and how many thumbnails may be in flight can be
 * tested without a browser and argued about without a device.
 *
 * Three rules drive everything here, all from the gallery binding decisions:
 *
 *  1. **Bytes, not filenames.** A retained record is only something the gallery
 *     can show once its image bytes have been read and validated. A session id
 *     with `has_photo` is a *promise* of an image, not an image, and it is never
 *     treated as one.
 *  2. **Selection follows identity, not position.** Refreshes, deletions and
 *     reorderings happen underneath the viewer. The open photo is tracked by its
 *     stable session id, so deleting the third item cannot silently swap what
 *     the user is looking at.
 *  3. **Honest absence.** Saving off, nothing saved, no photo capability and a
 *     failed load are four different states with four different sentences. None
 *     of them is allowed to read as "no photos" when it means "we could not
 *     ask".
 */
import type { DeviceHistory, HistoryItem } from './device-history.ts';

/** A retained record the gallery can show, once its bytes have been read. */
export function photoItems(items: readonly HistoryItem[] | undefined): HistoryItem[] {
  // `has_photo === true` only: an absent or false flag means there is no image,
  // and a record without one belongs in Past questions, not in a photo grid.
  return (items ?? []).filter((item) => item.has_photo === true);
}

export type GalleryState =
  | { phase: 'unavailable'; reason: string }
  | { phase: 'loading' }
  | { phase: 'empty'; reason: string }
  | { phase: 'ready'; items: HistoryItem[]; note: string };

/**
 * Derive the gallery's own state from the protected history snapshot.
 *
 * `loading` is a distinct phase rather than an empty list, because "we have not
 * heard back yet" and "there is nothing" look identical on screen and mean
 * opposite things to a user deciding whether their photos are gone.
 */
export function galleryState(history: DeviceHistory | null, options: { failed?: boolean; loading?: boolean } = {}): GalleryState {
  if (options.failed) return { phase: 'unavailable', reason: 'Saved photos could not be loaded. Try again in a moment.' };
  if (!history) return options.loading === false
    ? { phase: 'unavailable', reason: 'Saved photos are not available on this device yet.' }
    : { phase: 'loading' };
  // The device telling us it cannot keep photos at all is not an empty gallery:
  // there is nothing to be empty of.
  if (history.photo_saving_supported === false) {
    return { phase: 'unavailable', reason: 'This device cannot keep photos with a question yet.' };
  }
  const items = photoItems(history.items);
  if (items.length) {
    return { phase: 'ready', items, note: history.photos_enabled === false ? 'Saving is off. These photos were kept earlier and stay until you remove them.' : '' };
  }
  // Saving off with nothing retained is still not the same as "no photos yet",
  // and the difference is what tells a user whether anything was ever kept.
  if (history.photos_enabled === false) {
    return { phase: 'empty', reason: 'No saved photos. Keeping photos is off, so new ones are not saved.' };
  }
  if (history.enabled === false && history.saving_off_keeps_items !== true) {
    return { phase: 'empty', reason: 'No saved photos. Saving questions is off, so nothing is kept to look back at.' };
  }
  return { phase: 'empty', reason: 'No saved photos yet. Photos you keep with a question appear here.' };
}

/**
 * Which photo the viewer should be showing.
 *
 * Returns the retained record whose id matches, or `null` when it is gone. The
 * caller decides what to do with `null` (close the viewer, or step to a
 * neighbour); this function never silently picks a different photo, because
 * that is how a viewer ends up showing somebody a picture they did not open.
 */
export function clampSelection(items: readonly HistoryItem[], selectedId: string | null): HistoryItem | null {
  if (!selectedId) return null;
  return items.find((item) => item.session_id === selectedId) ?? null;
}

/**
 * The neighbour to move to, or `null` at the ends.
 *
 * Deliberately does **not** wrap: a previous on the first photo and a next on
 * the last are boundaries, and a viewer that jumps from the oldest photo to the
 * newest makes it impossible to tell where you are.
 */
export function stepSelection(items: readonly HistoryItem[], selectedId: string | null, delta: number): string | null {
  if (!items.length || !Number.isInteger(delta) || delta === 0) return null;
  const index = items.findIndex((item) => item.session_id === selectedId);
  if (index === -1) return null;
  const next = index + Math.sign(delta);
  if (next < 0 || next >= items.length) return null;
  return items[next].session_id;
}

/** What the viewer shows about position. One-based, because it is read by people. */
export function positionLabel(items: readonly HistoryItem[], selectedId: string | null): string {
  const index = items.findIndex((item) => item.session_id === selectedId);
  return index === -1 ? '' : `${index + 1} of ${items.length}`;
}

/**
 * Split the grid into bounded batches.
 *
 * Every visible thumbnail is a separate authenticated read of real bytes. Asking
 * for all of them at once is how a portrait panel with a hundred retained photos
 * stalls behind a queue of image requests, so the caller walks these in order and
 * only starts the next batch when the previous one has settled.
 */
export function thumbnailBatches(items: readonly HistoryItem[], size = 6): HistoryItem[][] {
  if (!Number.isInteger(size) || size < 1) throw new Error('thumbnail batch size must be a positive integer');
  const batches: HistoryItem[][] = [];
  for (let offset = 0; offset < items.length; offset += size) batches.push(items.slice(offset, offset + size));
  return batches;
}

/**
 * Drop the failures a read has since recovered from.
 *
 * A failed thumbnail read is retried on the next batch pass — the bytes are not in
 * the source map, so the record is asked for again — but the record of the failure
 * outlived the failure. A photo whose second read succeeded stayed marked missing
 * in the grid and in its own viewer while its bytes were on screen, which is the
 * one thing the grid must not say. The caller passes the ids whose bytes just
 * arrived; nothing else is touched.
 *
 * Returns the input unchanged when there is nothing to clear, so a batch that
 * recovered nothing does not cost a render.
 */
export function withoutMissing<T extends Record<string, true>>(missing: T, loaded: Iterable<string>): T {
  const ids = [...loaded];
  if (!ids.some((id) => missing[id] === true)) return missing;
  const next = { ...missing };
  for (const id of ids) delete next[id];
  return next;
}

export type AskAvailability = { allowed: boolean; reason: string };

/**
 * Whether "Ask about this photo" may be offered.
 *
 * Four independent things have to be true and each has its own reason, because
 * "nothing happens when I press it" is the failure this exists to prevent. The
 * bytes are load-bearing: offering the action for a record whose image failed to
 * load would stage an attachment the eventual request cannot carry.
 */
export function askAboutPhoto(input: {
  hasItem: boolean; hasBytes: boolean; photoReady: boolean; connected: boolean; busy: boolean;
}): AskAvailability {
  if (!input.hasItem) return { allowed: false, reason: 'Open a photo first.' };
  if (!input.connected) return { allowed: false, reason: 'Reconnecting to the device service.' };
  if (!input.hasBytes) return { allowed: false, reason: 'This photo could not be loaded, so it cannot be attached to a question.' };
  if (!input.photoReady) return { allowed: false, reason: 'Photo questions are not available on this device yet.' };
  if (input.busy) return { allowed: false, reason: 'Finish the current question first.' };
  return { allowed: true, reason: '' };
}

export type DeleteEffect = { title: string; body: string; confirmLabel: string };

/**
 * What confirming a deletion actually does.
 *
 * `history/delete` removes the saved question and its answer. What happens to
 * the photo is the runtime's answer to give: the bytes are removed only when
 * the reply says so, and they are retained as an immutable original when
 * `photo_removal` says `history_link_only`. There is no photo-only command in
 * the voice interface, so the gallery must not offer one and must not state an
 * outcome the reply did not. The wording is derived here rather than written in
 * the component so it cannot drift into the gentler, wrong version.
 *
 * `sage_backed` alone does **not** decide whether the bytes survive. A runtime
 * with protected storage answers `sage_backed: true` and `photo_removal:
 * "history_link_only"` in the same reply — the bytes are the immutable record
 * and only the history link is removed (`unit-a-voice-loop.py:450`). The
 * original wording was selected on `sage_backed` and told the owner the photo
 * was "removed from this device too", which the runtime's own field
 * contradicted. `photo_removal` is therefore the signal that selects the
 * sentence, and it is the only one that may state where the photo went.
 *
 * Three cases, and none of them may claim more than was said:
 *
 *  - `"history_link_only"` — the bytes are a retained, immutable record. The
 *    owner must be told the photo stays, or they will believe a deletion
 *    destroyed it.
 *  - not stated (`undefined`) — the older reply shape. `sage_backed` keeps its
 *    existing, accepted wording distinction unchanged.
 *  - any other value — an answer this build does not know. It is read as "the
 *    photo may have been kept", because a sentence that overstates a deletion
 *    is the failure this function exists to prevent, and an unrecognised value
 *    is not evidence that the bytes are gone.
 */
export function deleteEffect(item: HistoryItem, sageBacked = false, photoRemoval?: string): DeleteEffect {
  // `item` stays in the signature: this wording belongs to one record, and the
  // confirmation dialog is keyed on the photo the viewer actually has open.
  if (photoRemoval === 'history_link_only') {
    return {
      title: 'Remove this question and its photo?',
      body: sageBacked
        ? 'Removes this conversation from history and from future answers. The photo is kept as an original record on this device and cannot be erased.'
        : 'Removes the saved question and its answer from this device. The photo is kept as an original record on this device and cannot be erased. Other copies you already shared are not affected.',
      confirmLabel: 'Remove',
    };
  }
  if (photoRemoval !== undefined) {
    return {
      title: 'Remove this question and its photo?',
      body: sageBacked
        ? 'Removes this conversation from history and from future answers. The photo may be kept on this device.'
        : 'Removes the saved question and its answer from this device. The photo may be kept on this device. Other copies you already shared are not affected.',
      confirmLabel: 'Remove',
    };
  }
  return {
    title: 'Remove this question and its photo?',
    body: sageBacked
      ? 'Removes this conversation from history and from future answers. Its saved photo is removed from this device too. Original records cannot be erased.'
      : 'Removes the saved question, its answer and the saved photo from this device. Other copies you already shared are not affected. It cannot be undone.',
    confirmLabel: 'Remove',
  };
}

/**
 * Why the viewer is showing no image for a record that claims one.
 *
 * A failed read is not a deletion. `history/photo` can fail because the device
 * was busy, the read timed out, the request was locked out or the capability is
 * absent, and none of those is evidence that the bytes are gone — the same rule
 * the delete wording above follows. The sentence therefore reports what was
 * observed, and leaves the record's fate open rather than asserting a removal
 * nothing established.
 */
export function imageUnavailableReason(): string {
  return 'This photo could not be loaded. It may still be saved with this question; the question and answer are still in Past questions.';
}
