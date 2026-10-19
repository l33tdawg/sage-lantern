/**
 * What the owner may be told about removal and retention.
 *
 * `history.tsx` and `settings.tsx` chose their sentences from `sage_backed`
 * alone. That is the field that made the Gallery's delete confirmation tell an
 * owner their photo was off the device while the runtime's own reply said it was
 * kept. These cases pin the rule for the two remaining surfaces: the photo's fate
 * comes from `photo_removal`, retention from a known `photo_storage`, an
 * unreadable policy is described cautiously, an absent one keeps the wording that
 * was already accepted, and a surface with no photo in it says nothing about
 * photos at all.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { deleteEffect } from './gallery-navigation.ts';
import {
  photoFate, photoRetentionNote, photosAreOriginals, photosBeingSaved,
  removalNotice, retentionFacts, savingNotice,
} from './device-retention.ts';

const PHOTO = { session_id: 'a', created_at: '2026-10-04T06:00:00.000Z', transcript: 'Q', response: 'A', has_photo: true };
const NO_PHOTO = { ...PHOTO, has_photo: false };

test('a policy this build cannot read is never read as a deletion', () => {
  assert.equal(photoFate('history_link_only'), 'kept');
  assert.equal(photoFate(undefined), 'unsaid');
  // Unknown is the cautious case, not the convenient one.
  assert.equal(photoFate('some_future_mode'), 'may-keep');
  assert.equal(photoFate(''), 'may-keep');
});

test('a single record is described by the same wording as the command it runs', () => {
  // history/delete is exactly what the Gallery's viewer confirms, so a record
  // with a photo must not acquire a second sentence here that can drift from it.
  for (const photoRemoval of ['history_link_only', 'some_future_mode', undefined]) {
    for (const sageBacked of [true, false]) {
      const effect = deleteEffect(PHOTO, sageBacked, photoRemoval);
      const notice = removalNotice('delete', { sageBacked, photoRemoval, item: PHOTO });
      assert.deepEqual(notice, { title: effect.title, body: effect.body }, `${sageBacked}/${photoRemoval}`);
    }
  }
});

test('a removal that keeps the photo says so, and never says it was removed', () => {
  const kept = removalNotice('clear', { photoRemoval: 'history_link_only', photosAffected: true });
  assert.match(kept.body, /kept/i);
  assert.match(kept.body, /original records/i);
  assert.doesNotMatch(kept.body, /attached photos are removed|photo is removed/i);

  const unknown = removalNotice('clear', { photoRemoval: 'some_future_mode', photosAffected: true });
  assert.match(unknown.body, /may be kept/i);
  assert.doesNotMatch(unknown.body, /attached photos are removed|photo is removed/i);

  // The protected-storage runtime answers sage_backed true and history_link_only
  // in the same reply: the accepted claim is kept and the photo fate is added,
  // never substituted for it.
  const backed = removalNotice('clear', { sageBacked: true, photoRemoval: 'history_link_only', photosAffected: true });
  assert.match(backed.body, /Original records cannot be erased/);
  assert.match(backed.body, /photos are kept on this device/i);
});

test('an absent policy keeps the accepted wording', () => {
  const legacy = removalNotice('clear', { photosAffected: true });
  assert.equal(legacy.body, "This removes saved questions, answers and their attached photos from this device's history. It cannot be undone.");
  assert.equal(
    removalNotice('clear', { sageBacked: true, photosAffected: true }).body,
    'Removes these conversations from history and future answers. Original records cannot be erased.',
  );
});

test('a confirmation with no photo in it says nothing about photos', () => {
  for (const kind of ['delete', 'clear']) {
    const options = kind === 'delete' ? { item: NO_PHOTO } : { photosAffected: false };
    for (const sageBacked of [true, false]) {
      const notice = removalNotice(kind, { ...options, sageBacked });
      assert.doesNotMatch(notice.body, /photo/i, `${kind} must not mention a photo it does not affect`);
    }
  }
});

test('Settings offers the photo sentence only when photos are really being saved', () => {
  // A supported policy is not a photo kept. The accepted sentence claimed the
  // owner could "view saved photos" on the strength of the field alone.
  const supportedButOff = savingNotice({ photo_saving_supported: true, photos_enabled: false });
  assert.doesNotMatch(supportedButOff, /photo/i);
  assert.match(supportedButOff, /saved questions\./);

  const saving = savingNotice({ photo_saving_supported: true, photos_enabled: true, photo_storage: 'sage_immutable' });
  assert.match(saving, /view saved photos/);
  assert.match(saving, /kept as original records/i);

  const unknown = savingNotice({ photo_saving_supported: true, photos_enabled: true, photo_storage: 'some_future_mode' });
  assert.match(unknown, /may be kept/i);

  // No policy stated at all: the accepted wording stands unchanged.
  assert.equal(
    savingNotice({ photo_saving_supported: true, photos_enabled: true }),
    'Anyone using this device can read saved questions and view saved photos. Only future completed answers are saved. Nothing is published.',
  );
  // Protected storage keeps its own accepted sentence.
  assert.match(savingNotice({ sage_backed: true }), /^Save conversations so Lantern can remember them later\./);
});

test('the photo note describes the switch above it', () => {
  assert.equal(photoRetentionNote({ enabled: false, photos_enabled: true }), 'Photo saving is paused while question saving is off.');
  // On question saving but Off photos: the accepted note claimed photos were kept.
  assert.equal(photoRetentionNote({ enabled: true, photos_enabled: false, photo_saving_supported: true }), 'Photos are not kept with completed answers.');
  assert.equal(photoRetentionNote({ enabled: true, photos_enabled: true, photo_saving_supported: true, photo_storage: 'sage_immutable' }), 'Keeps the original photo with its completed answer.');
  assert.equal(photoRetentionNote({ enabled: true, photos_enabled: true, photo_saving_supported: true }), 'Keeps the original photo with its completed answer.');
  // An unreadable policy is not a promise of an unerasable original.
  assert.equal(photoRetentionNote({ enabled: true, photos_enabled: true, photo_saving_supported: true, photo_storage: 'some_future_mode' }), 'Keeps a copy of the photo with its completed answer.');
});

test('retention states the photo policy only when the runtime attests it', () => {
  assert.deepEqual(retentionFacts({ sage_backed: true }), { retention: 'Conversations remembered', storage: 'Saved on this device' });
  // The documented local window describes the questions; photos kept as
  // originals outlive it, so the pair must not read as one window for both.
  assert.deepEqual(retentionFacts({ photo_saving_supported: true, photos_enabled: true, photo_storage: 'sage_immutable' }), {
    retention: '100 answers · 30 days', storage: 'Photos kept on this device',
  });
  assert.deepEqual(retentionFacts({ photo_saving_supported: true, photos_enabled: false, photo_storage: 'sage_immutable' }), {
    retention: '100 answers · 30 days', storage: 'Saved on this device',
  });
});

test('the two policy questions stay separate', () => {
  // Saving and being an original are different claims: the first needs the
  // switch, the second needs the runtime's word for it.
  assert.equal(photosBeingSaved({ photo_saving_supported: true, photos_enabled: true }), true);
  assert.equal(photosBeingSaved({ photo_saving_supported: true }), false);
  assert.equal(photosBeingSaved({ photos_enabled: true }), false);
  assert.equal(photosAreOriginals({ photo_saving_supported: true, photos_enabled: true, photo_storage: 'sage_immutable' }), true);
  assert.equal(photosAreOriginals({ photo_saving_supported: true, photos_enabled: true }), false);
  assert.equal(photosAreOriginals({ photo_saving_supported: true, photos_enabled: false, photo_storage: 'sage_immutable' }), false);
});

test('both screens render the derived sentence instead of their own', () => {
  // The sentences above are only load-bearing if the components show them. The
  // lib suite has no DOM, so what is pinned here is the wiring.
  const source = (file) => readFileSync(new URL(`../app/device/${file}`, import.meta.url), 'utf8');
  const history = source('history.tsx');
  assert.match(history, /const notice = confirmation \? removalNotice\(confirmation, \{/);
  assert.match(history, /photoRemoval: history\?\.photo_removal/);
  assert.match(history, /photosAffected: history\?\.items\.some\(\(item\) => item\.has_photo\) === true/);
  assert.match(history, /<h2>\{notice\?\.title\}<\/h2>/);
  assert.match(history, /<p>\{notice\?\.body\}<\/p>/);
  // No second, hand-written deletion sentence may sit beside the derived one.
  assert.doesNotMatch(history, /attached photos from this device/);

  const settings = source('settings.tsx');
  assert.match(settings, /const facts = history \? retentionFacts\(history\) : null;/);
  assert.match(settings, /: savingNotice\(history\)\}/);
  assert.match(settings, /<p className="device-note">\{photoRetentionNote\(history\)\}<\/p>/);
  assert.match(settings, /<span>\{facts\?\.retention\}<\/span><span>\{facts\?\.storage\}<\/span>/);
  assert.doesNotMatch(settings, /view saved photos\. Only future/);
});
