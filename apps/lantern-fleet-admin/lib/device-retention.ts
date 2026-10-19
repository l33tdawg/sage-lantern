/**
 * What the device's own policy fields let the owner be told about removal and retention.
 *
 * `history.tsx` and `settings.tsx` retold deletion and saving semantics from
 * `sage_backed` alone — the same unread field that made the Gallery's delete
 * confirmation contradict the runtime. Neither sentence was as wrong as the
 * Gallery's (neither claimed the photo was destroyed for a record that had one),
 * but a phrase chosen without reading the policy is only right by luck.
 *
 * Two rules, both taken from the Gallery's correction so there is one voice for
 * one command:
 *
 *  - `photo_removal` is what a removal did to the bytes. `"history_link_only"`
 *    means the photo is a retained original; a value this build does not know is
 *    read as "may have been kept", because overstating a deletion is the failure
 *    this wording exists to prevent; an absent field is the older reply shape and
 *    keeps the wording that was already accepted.
 *  - `photo_storage` is where a kept photo lives. "Kept as an original record"
 *    may only be said when the runtime says so. A supported photo *policy* is not
 *    evidence that photos are being saved: that needs `photos_enabled`.
 *
 * Nothing here changes a request, a saving decision, consent or authorization —
 * it is selected copy, and each sentence may state only what was said.
 */
import { deleteEffect } from './gallery-navigation.ts';
import type { DeviceHistory, HistoryItem } from './device-history.ts';

/** What the runtime's own `photo_removal` permits us to say about the bytes. */
export type PhotoFate = 'kept' | 'may-keep' | 'unsaid';

export function photoFate(photoRemoval?: string): PhotoFate {
  if (photoRemoval === 'history_link_only') return 'kept';
  if (photoRemoval === undefined) return 'unsaid';
  return 'may-keep';
}

/** True only when this runtime both supports photo saving and has it switched on. */
export function photosBeingSaved(history: Pick<DeviceHistory, 'photo_saving_supported' | 'photos_enabled'>): boolean {
  return history.photo_saving_supported === true && history.photos_enabled === true;
}

/** True only when the runtime states that a kept photo is an original record. */
export function photosAreOriginals(history: Pick<DeviceHistory, 'photo_saving_supported' | 'photos_enabled' | 'photo_storage'>): boolean {
  return photosBeingSaved(history) && history.photo_storage === 'sage_immutable';
}

export type RemovalNotice = { title: string; body: string };

/**
 * The confirmation body for removing history: one record, or all of it.
 *
 * A record with no photo says nothing about photos, and a clear that affects no
 * photo record says nothing about them either — a photo clause on a screen with
 * no photo in it is noise that teaches the owner the sentence is boilerplate.
 * Where a photo *is* affected, its fate comes from `photo_removal`, and the
 * sentence only ever gets more cautious than the accepted wording, never bolder.
 */
export function removalNotice(
  kind: 'delete' | 'clear',
  options: { sageBacked?: boolean; photoRemoval?: string; item?: HistoryItem; photosAffected?: boolean },
): RemovalNotice {
  const { sageBacked = false, photoRemoval } = options;
  if (kind === 'delete') {
    // One command, one wording: a single record's photo is removed by exactly the
    // call the Gallery's viewer confirms, so it is described by the same function
    // rather than by a second sentence that can drift from it.
    if (options.item?.has_photo) {
      const effect = deleteEffect(options.item, sageBacked, photoRemoval);
      return { title: effect.title, body: effect.body };
    }
    return {
      title: 'Remove this question?',
      body: sageBacked
        ? 'Removes this conversation from history and from future answers. Original records cannot be erased.'
        : 'Removes the saved question and its answer from this device. It cannot be undone.',
    };
  }

  const affected = options.photosAffected === true;
  if (!affected) {
    return {
      title: 'Remove all history?',
      body: sageBacked
        ? 'Removes these conversations from history and future answers. Original records cannot be erased.'
        : "This removes saved questions and answers from this device's history. It cannot be undone.",
    };
  }
  const fate = photoFate(photoRemoval);
  if (fate === 'unsaid') {
    // The older reply shape. The accepted wording stands, including its
    // "attached photos" clause: on a runtime that sends no policy the files are
    // pruned with the link, which is what that sentence has always described.
    return {
      title: 'Remove all history?',
      body: sageBacked
        ? 'Removes these conversations from history and future answers. Original records cannot be erased.'
        : "This removes saved questions, answers and their attached photos from this device's history. It cannot be undone.",
    };
  }
  if (sageBacked) {
    return {
      title: 'Remove all history?',
      body: fate === 'kept'
        ? 'Removes these conversations from history and future answers. Original records cannot be erased; the attached photos are kept on this device too.'
        : 'Removes these conversations from history and future answers. Original records cannot be erased; the attached photos may be kept on this device.',
    };
  }
  return {
    title: 'Remove all history?',
    body: fate === 'kept'
      ? "This removes saved questions and answers from this device's history. The attached photos are kept as original records and cannot be erased. It cannot be undone."
      : "This removes saved questions and answers from this device's history. The attached photos may be kept on this device. It cannot be undone.",
  };
}

/**
 * The body of the "start saving" confirmation.
 *
 * The photo half is offered only when photos are really being saved; a runtime
 * that merely *supports* keeping photos has not said it keeps any, and telling
 * the owner they will be able to "view saved photos" is the same class of claim
 * as the Gallery's deleted-photo sentence. Retention is described from a known
 * `photo_storage`, and an absent field keeps the accepted wording.
 */
export function savingNotice(history: Pick<DeviceHistory, 'sage_backed' | 'photo_saving_supported' | 'photos_enabled' | 'photo_storage'>): string {
  if (history.sage_backed === true) {
    return 'Save conversations so Lantern can remember them later. Anyone using this device can read them. Original records cannot be erased; nothing is shared publicly.';
  }
  if (!photosBeingSaved(history)) {
    return 'Anyone using this device can read saved questions. Only future completed answers are saved. Nothing is published.';
  }
  if (history.photo_storage === undefined) {
    return 'Anyone using this device can read saved questions and view saved photos. Only future completed answers are saved. Nothing is published.';
  }
  // `photo_storage` and `photo_removal` are different vocabularies: where a kept
  // photo lives, and what a removal did to it. Reading one with the other's words
  // is how a retained original gets described as merely "may be kept".
  if (photosAreOriginals(history)) {
    return 'Anyone using this device can read saved questions and view saved photos. The photos are kept as original records on this device and cannot be erased. Only future completed answers are saved. Nothing is published.';
  }
  return 'Anyone using this device can read saved questions and view saved photos. The photos may be kept on this device. Only future completed answers are saved. Nothing is published.';
}

/**
 * The note under the "Keep attached photos" switch.
 *
 * It describes the switch above it, whatever that switch currently says. The
 * accepted wording claimed photos were kept whenever question saving was on,
 * which contradicted its own Off switch — a supported policy is not a photo
 * being saved, and neither is an On question switch.
 */
export function photoRetentionNote(history: Pick<DeviceHistory, 'enabled' | 'photo_saving_supported' | 'photos_enabled' | 'photo_storage'>): string {
  if (!history.enabled) return 'Photo saving is paused while question saving is off.';
  if (history.photos_enabled !== true) return 'Photos are not kept with completed answers.';
  if (history.photo_storage === undefined || history.photo_storage === 'sage_immutable') {
    return 'Keeps the original photo with its completed answer.';
  }
  // A storage policy this build cannot read is not a promise that the photo is
  // an original: say what happens, not what cannot be confirmed.
  return 'Keeps a copy of the photo with its completed answer.';
}

/**
 * The two retention chips.
 *
 * "100 answers · 30 days" is the documented local window (`README.md`), so it
 * stays. What must not stand beside it is a bare "Saved on this device" when the
 * runtime states that kept photos are original records: the window describes the
 * questions, and the photos outlive it.
 */
export function retentionFacts(history: Pick<DeviceHistory, 'sage_backed' | 'photo_saving_supported' | 'photos_enabled' | 'photo_storage'>): { retention: string; storage: string } {
  return {
    retention: history.sage_backed === true ? 'Conversations remembered' : '100 answers · 30 days',
    storage: photosAreOriginals(history) ? 'Photos kept on this device' : 'Saved on this device',
  };
}
