import assert from 'node:assert/strict';
import test from 'node:test';
import {
  askAboutPhoto, clampSelection, deleteEffect, galleryState, imageUnavailableReason, photoItems,
  positionLabel, stepSelection, thumbnailBatches, withoutMissing,
} from './gallery-navigation.ts';

const photo = (id, hasPhoto = true) => ({
  session_id: id, created_at: '2026-10-02T01:00:00Z', transcript: `Q ${id}`, response: `A ${id}`, has_photo: hasPhoto,
});

const history = (overrides = {}) => ({
  enabled: true, items: [photo('a'), photo('b')], saving_off_keeps_items: true,
  photo_saving_supported: true, photos_enabled: true, ...overrides,
});

// --- Which records are photos ---------------------------------------------

test('only records that actually carry a photo are gallery entries', () => {
  // An explicitly absent flag is the case that matters: `has_photo` is optional
  // on the wire, and a default parameter would hide it.
  const withoutFlag = { session_id: 'c', created_at: '2026-10-02T01:00:00Z', transcript: 'Q c', response: 'A c' };
  assert.deepEqual(photoItems([photo('a'), photo('b', false), withoutFlag]).map((item) => item.session_id), ['a']);
});

test('an absent or missing history is no photos rather than a crash', () => {
  assert.deepEqual(photoItems(undefined), []);
  assert.deepEqual(photoItems([]), []);
});

// --- Gallery state ---------------------------------------------------------

test('a state that has not answered yet is loading, not empty', () => {
  // "We have not heard back" and "there is nothing" look identical on screen and
  // mean opposite things to someone checking whether their photos are gone.
  assert.equal(galleryState(null).phase, 'loading');
  assert.equal(galleryState(null, { loading: true }).phase, 'loading');
});

test('a device without photo capability says so instead of showing an empty grid', () => {
  const state = galleryState(history({ photo_saving_supported: false, items: [] }));
  assert.equal(state.phase, 'unavailable');
  assert.match(state.reason, /cannot keep photos/i);
});

test('a failed load is unavailable, not an empty gallery', () => {
  const state = galleryState(history({ items: [] }), { failed: true });
  assert.equal(state.phase, 'unavailable');
  assert.match(state.reason, /could not be loaded/i);
});

test('a settled history that never arrived is unavailable rather than empty', () => {
  const state = galleryState(null, { loading: false });
  assert.equal(state.phase, 'unavailable');
});

test('saving off with retained photos still shows them and says why', () => {
  // Turning saving off preserves existing items; it does not empty the gallery.
  const state = galleryState(history({ photos_enabled: false }));
  assert.equal(state.phase, 'ready');
  assert.deepEqual(state.items.map((item) => item.session_id), ['a', 'b']);
  assert.match(state.note, /saving is off/i);
});

test('saving off with nothing retained is not the same as having none yet', () => {
  const off = galleryState(history({ photos_enabled: false, items: [] }));
  assert.equal(off.phase, 'empty');
  assert.match(off.reason, /keeping photos is off/i);

  const never = galleryState(history({ items: [] }));
  assert.equal(never.phase, 'empty');
  assert.match(never.reason, /no saved photos yet/i);
  assert.notEqual(off.reason, never.reason);
});

test('a ready gallery reports no note when saving is on', () => {
  assert.equal(galleryState(history()).note, '');
});

// --- Selection by identity -------------------------------------------------

test('the open photo is followed by its id, not by its position', () => {
  // A refresh reorders underneath the viewer; the user must still be on 'b'.
  const reordered = [photo('c'), photo('a'), photo('b')];
  assert.equal(clampSelection(reordered, 'b').session_id, 'b');
});

test('a deleted photo closes rather than silently showing its neighbour', () => {
  // Picking a neighbour here is how a viewer ends up showing a picture the user
  // did not open; the caller decides what happens next.
  const items = [photo('a'), photo('c')];
  assert.equal(clampSelection(items, 'b'), null);
});

test('an absent selection stays absent', () => {
  assert.equal(clampSelection([photo('a')], null), null);
  assert.equal(clampSelection([], 'a'), null);
});

// --- Boundaries ------------------------------------------------------------

test('previous and next move one step and stop at the ends', () => {
  const items = [photo('a'), photo('b'), photo('c')];
  assert.equal(stepSelection(items, 'b', 1), 'c');
  assert.equal(stepSelection(items, 'b', -1), 'a');
  // Deliberately no wrap: jumping from the oldest to the newest makes position
  // impossible to read.
  assert.equal(stepSelection(items, 'c', 1), null);
  assert.equal(stepSelection(items, 'a', -1), null);
});

test('stepping from an item that is gone is a boundary, not a guess', () => {
  const items = [photo('a'), photo('c')];
  assert.equal(stepSelection(items, 'b', 1), null);
  assert.equal(stepSelection([], 'a', 1), null);
  assert.equal(stepSelection(items, 'a', 0), null);
});

test('the position label is one-based and empty when nothing is open', () => {
  const items = [photo('a'), photo('b'), photo('c')];
  assert.equal(positionLabel(items, 'a'), '1 of 3');
  assert.equal(positionLabel(items, 'c'), '3 of 3');
  assert.equal(positionLabel(items, 'gone'), '');
});

// --- Bounded thumbnail loading ---------------------------------------------

test('thumbnails are read in bounded batches rather than all at once', () => {
  const items = Array.from({ length: 7 }, (_, index) => photo(`p${index}`));
  const batches = thumbnailBatches(items, 3);
  assert.deepEqual(batches.map((batch) => batch.length), [3, 3, 1]);
  assert.deepEqual(batches.flat().map((item) => item.session_id), items.map((item) => item.session_id));
});

test('a nonsense batch size is a programming error, not a silent default', () => {
  assert.throws(() => thumbnailBatches([photo('a')], 0), /positive integer/);
  assert.throws(() => thumbnailBatches([photo('a')], 1.5), /positive integer/);
});

test('an empty gallery produces no batches, so no request is issued', () => {
  assert.deepEqual(thumbnailBatches([], 6), []);
});

// --- Ask about this photo --------------------------------------------------

test('ask needs a real image, not a filename', () => {
  const ready = { hasItem: true, hasBytes: true, photoReady: true, connected: true, busy: false };
  assert.equal(askAboutPhoto(ready).allowed, true);
  const missing = askAboutPhoto({ ...ready, hasBytes: false });
  assert.equal(missing.allowed, false);
  assert.match(missing.reason, /could not be loaded/i);
});

test('ask says which of the four gates is closed', () => {
  const base = { hasItem: true, hasBytes: true, photoReady: true, connected: true, busy: false };
  assert.match(askAboutPhoto({ ...base, hasItem: false }).reason, /open a photo/i);
  assert.match(askAboutPhoto({ ...base, connected: false }).reason, /reconnecting/i);
  assert.match(askAboutPhoto({ ...base, photoReady: false }).reason, /not available on this device/i);
  assert.match(askAboutPhoto({ ...base, busy: true }).reason, /finish the current question/i);
});

// --- Deletion semantics ----------------------------------------------------

test('deletion is described as the whole effect, never as a photo-only action', () => {
  // There is no photo-only command in the voice interface, so offering one would
  // be a promise the device cannot keep.
  const effect = deleteEffect(photo('a'));
  assert.match(effect.title, /question and its photo/i);
  assert.match(effect.body, /saved question, its answer and the saved photo/i);
  assert.doesNotMatch(effect.body, /^delete photo/i);
});

test('a SAGE-backed record says the original cannot be erased', () => {
  const effect = deleteEffect(photo('a'), true);
  assert.match(effect.body, /original records cannot be erased/i);
  assert.doesNotMatch(deleteEffect(photo('a'), false).body, /original records cannot be erased/i);
});

test('a shared copy is not claimed to be erased', () => {
  assert.match(deleteEffect(photo('a')).body, /copies you already shared are not affected/i);
});

// The deployed runtime answers history/delete with `sage_backed: true` *and*
// `photo_removal: "history_link_only"` when protected storage is on: the bytes
// are the immutable record and only the history link is removed
// (unit-a-voice-loop.py:450). Selecting the sentence on `sage_backed` alone told
// the owner the photo was "removed from this device too" while the runtime's own
// field said otherwise, so these lock the field as the decisive signal.
test('a retained original is never described as removed from the device', () => {
  // Both halves of the same reply: SAGE-backed *and* told the bytes are kept.
  const effect = deleteEffect(photo('a'), true, 'history_link_only');
  assert.match(effect.body, /photo is kept as an original record on this device/i);
  assert.doesNotMatch(effect.body, /removed from this device too/i);
  assert.doesNotMatch(effect.body, /photo is removed from this device/i);
  // The SAGE-backed distinction the accepted wording carried is preserved.
  assert.match(effect.body, /from history and from future answers/i);
  assert.match(effect.body, /cannot be erased/i);
});

test('a retained original over a local record still says the photo stays', () => {
  const effect = deleteEffect(photo('a'), false, 'history_link_only');
  assert.match(effect.body, /photo is kept as an original record on this device/i);
  assert.doesNotMatch(effect.body, /removed from this device too/i);
  assert.match(effect.body, /copies you already shared are not affected/i);
});

test('an unrecognised removal answer does not claim the bytes were destroyed', () => {
  // Forward compatibility: a value this build has never heard of is not evidence
  // that the photo is gone, and overstating a deletion is the failure this
  // wording exists to prevent.
  for (const answer of ['something_new', '']) {
    const effect = deleteEffect(photo('a'), true, answer);
    assert.match(effect.body, /the photo may be kept on this device/i);
    assert.doesNotMatch(effect.body, /removed from this device too/i);
    assert.doesNotMatch(effect.body, /photo is kept as an original record/i);
    // Unknown metadata establishes neither immutable originals nor erasure.
    assert.doesNotMatch(effect.body, /original records cannot be erased/i);
  }
});

test('a runtime that states no removal behaviour keeps the accepted wording', () => {
  // The pre-protected reply shape carries no `photo_removal` at all, and the
  // branch Rook accepted on `sage_backed` is unchanged for it.
  for (const effect of [deleteEffect(photo('a'), true, undefined), deleteEffect(photo('a'), true)]) {
    assert.match(effect.body, /removed from this device too/i);
    assert.match(effect.body, /original records cannot be erased/i);
  }
  assert.match(deleteEffect(photo('a'), false, undefined).body, /saved photo from this device/i);
});

test('a failed photo read is reported as a failed read, not a deletion', () => {
  const reason = imageUnavailableReason();
  assert.match(reason, /could not be loaded/i);
  assert.match(reason, /still in Past questions/i);
  // A timeout, a busy device, a locked read or an absent capability is not
  // evidence that the bytes are gone, so the sentence may not claim a removal
  // the reply never stated.
  assert.doesNotMatch(reason, /no longer on the device/i);
  assert.doesNotMatch(reason, /removed|deleted|erased/i);
});

test('bytes that arrive clear the record of the read that failed', () => {
  // A failed thumbnail is retried on the next batch pass, because its bytes are
  // not in the source map. The failure record outlived the failure, so a photo
  // whose second read succeeded stayed marked missing in the grid and in its own
  // viewer while its bytes were on screen.
  const failed = { a: true, b: true };
  assert.deepEqual(withoutMissing(failed, ['a']), { b: true });
  assert.deepEqual(withoutMissing(failed, ['a', 'b']), {});
  // A batch that recovered nothing returns the same object, so a retry that failed
  // again does not cost a render.
  assert.equal(withoutMissing(failed, []), failed);
  assert.equal(withoutMissing(failed, ['c']), failed);
  // Records that were never missing are not invented, and unknown ids are ignored.
  assert.deepEqual(withoutMissing({}, ['a']), {});
});
