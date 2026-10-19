/**
 * A community reply is judged against the authority its request was issued
 * under, not against the device token.
 *
 * The token cannot see an epoch rotation: the broker rotates on a new access
 * epoch while the device token stays exactly the same, so every check that
 * compared `token !== getDeviceToken()` was blind to the one change that
 * invalidates a cached chat, a contact list, an opened message or a read
 * receipt. The reply's own admission stamp is the half that can see it — the
 * runtime attaches the context it captured at admission and re-checks that it
 * is still current before answering.
 *
 * The behavioural half is here; the two protected surfaces that consume these
 * replies are pinned by source assertions at the end, and exercised for real by
 * `work/ember-lantern-home-20261002/drive-auth-fixture.mjs`.
 */
import test, { beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { communityRequest, parseCommunityStatus } from './community.ts';
import { establishedSessionContext, fenceProtectedReply, observeSessionContext, parseSessionContext, setDeviceToken, subscribeDeviceSession } from './device-session.ts';

const originalFetch = globalThis.fetch;
const originalDocument = globalThis.document;
const token = 'a'.repeat(43);
const status = { schema: 'sage.lantern.community.v1', messaging: { available: false, reason: 'encrypted_storage_unavailable' }, sharing: { available: false, reason: 'public_identity_unavailable' }, dictation: { available: true } };
/**
 * A locked device's replies carry the authority they were admitted under, and
 * its client knows that authority from a validated status reading. Both halves
 * are modelled here: a test that fabricated a locked device whose replies carry
 * no stamp would be testing a device that does not exist.
 */
const STAMP = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
const OTHER = { schema: 'sage.lantern.session-context.v1', epoch: 'c'.repeat(32), session_ref: 'b'.repeat(64) };
const establish = (reading = STAMP) => observeSessionContext(parseSessionContext(reading));
const reply = (context) => Response.json(context === null ? status : { ...status, session_context: context });

beforeEach(() => { globalThis.document = { hidden: false }; setDeviceToken(token); });
afterEach(() => { globalThis.fetch = originalFetch; globalThis.document = originalDocument; setDeviceToken(null); });

test('a stamped reply is adopted, and the stamp does not travel into the community record', async () => {
  establish();
  globalThis.fetch = async () => reply(STAMP);
  const value = await communityRequest('status');
  // The parsers require an exact key set, so a stamp left on the record would
  // reject a perfectly valid reply on a locked device.
  assert.deepEqual(value, status);
  assert.deepEqual(parseCommunityStatus(value), status);
});

test('a reply admitted under another epoch than the command is refused', async () => {
  establish(STAMP);
  globalThis.fetch = async () => reply(OTHER);
  await assert.rejects(communityRequest('status'), /Community authority changed/);
});

test('a reply that carries no stamp while the device is publishing one is refused', async () => {
  establish(STAMP);
  globalThis.fetch = async () => reply(null);
  await assert.rejects(communityRequest('status'), /Community authority changed/);
  // Unstamped is refused on every protected path, not only the status read.
  globalThis.fetch = async () => reply(null);
  await assert.rejects(communityRequest('messages/send', { consent: true }), /Community authority changed/);
});

test('a malformed stamp is refused rather than read as no stamp', async () => {
  establish(STAMP);
  for (const malformed of [
    { schema: 'other', epoch: STAMP.epoch, session_ref: STAMP.session_ref },
    { schema: STAMP.schema, epoch: 'not-hex', session_ref: STAMP.session_ref },
    { schema: STAMP.schema, epoch: STAMP.epoch, session_ref: STAMP.session_ref, extra: true },
  ]) {
    globalThis.fetch = async () => reply(malformed);
    await assert.rejects(communityRequest('status'), /session_context_unavailable/);
  }
});

test('an epoch that moves while the request is in flight refuses the reply', async () => {
  establish(STAMP);
  globalThis.fetch = async () => { establish(OTHER); return reply(STAMP); };
  await assert.rejects(communityRequest('status'), /Community authority changed/);
});

test('a legacy device that publishes no context still gets its unstamped replies', async () => {
  // `null` on a device that has not been seen publishing one is the unlocked
  // path, where an absent stamp is the correct shape rather than a refusal.
  observeSessionContext(null);
  globalThis.fetch = async () => reply(null);
  assert.deepEqual(await communityRequest('status'), status);
});

test('nothing is adopted before a reading has established an authority at all', async () => {
  // No status reading yet: nothing can be judged, and a refusal is the only safe
  // direction. It is refused before dispatch, so the request is never sent —
  // this is the locked path failing closed, not a legacy device.
  let calls = 0;
  globalThis.fetch = async () => { calls += 1; return reply(null); };
  await assert.rejects(communityRequest('status'), /Community authority unavailable/);
  globalThis.fetch = async () => { calls += 1; return reply(STAMP); };
  await assert.rejects(communityRequest('status'), /Community authority unavailable/);
  assert.equal(calls, 0);
});

test('a refused reading drops the authority, tells the seam, and stays refused', async () => {
  establish(STAMP);
  let notified = 0;
  const stop = subscribeDeviceSession(() => { notified += 1; });
  try {
    assert.equal(observeSessionContext('invalid'), 'refused');
    // The seam is what invalidates retained content and cached challenges, so a
    // refusal that stayed silent would leave them on screen.
    assert.ok(notified > 0);
    assert.equal(establishedSessionContext().state, 'lost');
    // A device that was publishing a context cannot be downgraded to the legacy
    // path by a reading that failed, however often it repeats.
    assert.equal(observeSessionContext(null), 'refused');
    assert.equal(establishedSessionContext().state, 'lost');
    // So a write is refused before it is dispatched, not after.
    let calls = 0;
    globalThis.fetch = async () => { calls += 1; return reply(STAMP); };
    await assert.rejects(communityRequest('messages/send', { consent: true }), /Community authority unavailable/);
    assert.equal(calls, 0);
  } finally { stop(); }
});

test('a context restored after a refusal cannot revive work issued before it', async () => {
  establish(STAMP);
  const issuedUnder = establishedSessionContext();
  observeSessionContext('invalid');
  establish(STAMP);
  // Same context, different authority object: the reply was issued before the
  // loss, so it may not be adopted now that the context reads the same again.
  assert.notEqual(fenceProtectedReply(issuedUnder, STAMP), 'ok');
  // Fresh work under the restored authority is fine.
  globalThis.fetch = async () => reply(STAMP);
  assert.deepEqual(await communityRequest('status'), status);
});

test('the token check still refuses a lock that lands while the request is in flight', async () => {
  establish();
  globalThis.fetch = async () => { setDeviceToken(null); return reply(STAMP); };
  await assert.rejects(communityRequest('status'), /Device locked/);
});

test('both protected community surfaces subscribe to the one invalidation seam', () => {
  const messages = readFileSync(new URL('../app/device/messages.tsx', import.meta.url), 'utf8');
  const receipt = readFileSync(new URL('../app/device/read-receipt.tsx', import.meta.url), 'utf8');
  // The seam is the only channel: `subscribeDeviceSession` already fires for a
  // replaced token *and* for a same-token rotation, so a surface that subscribed
  // to it needs no second opinion about authority.
  assert.match(messages, /import \{ getDeviceToken, subscribeDeviceSession \} from '\.\.\/\.\.\/lib\/device-session';/);
  assert.match(receipt, /import \{ getDeviceToken, subscribeDeviceSession \} from '\.\.\/\.\.\/lib\/device-session';/);
  for (const source of [messages, receipt]) {
    assert.match(source, /const unsubscribe = subscribeDeviceSession\(/);
    assert.match(source, /unsubscribe\(\);/);
    assert.doesNotMatch(source, /expectedSessionChanges/);
  }
  // The chats screen drops the cached chats, the draft in progress and the
  // receipt outcomes together, and aborts the in-flight detail read.
  assert.match(messages, /const clearProtected = \(\) => \{\n {6}detailRequest\.current\?\.abort\(\);/);
  assert.match(messages, /setRecording\(null\); setReview\(false\); setFilter\(''\); setReadOutcomes\(\{\}\); setOutcome\(null\); setError\(''\);/);
  assert.match(messages, /setSelectedId\(null\); setComposing\(false\); setUnavailable\(\[\]\); requestId\.current = null;/);
});

test('a rotated read receipt drops the challenge and cannot be reissued on its own', () => {
  const receipt = readFileSync(new URL('../app/device/read-receipt.tsx', import.meta.url), 'utf8');
  // The challenge belongs to the authority that issued it. Closing the flow is
  // what makes the rotation terminal: the outstanding challenge is dropped, no
  // replacement is requested, and the surface says so instead of asserting that
  // a receipt was queued.
  assert.match(receipt, /const invalidate = \(\) => \{\n {6}generation\.current \+= 1;\n {6}current\?\.close\(\);/);
  assert.match(receipt, /setChallenge\(null\); setChecked\(false\); setChanged\(true\);/);
  // Reached by a rotation *and* by a refused reading, so the copy may not claim
  // the session changed: a refusal means it could not be verified, which is not
  // the same claim.
  assert.match(receipt, /changed \? <p role="status">The device session is no longer verified\. Reopen the message to review it again\.<\/p>/);
  // A send already dispatched is never replayed, and a positive outcome can no
  // longer come back once the flow was closed under it.
  assert.match(receipt, /const issuedAt = generation\.current;/);
  assert.match(receipt, /\(outcome\.state === 'unknown' \|\| generation\.current === issuedAt\)\) onOutcome\(outcome\);/);
  assert.match(receipt, /onOutcome\(\{ id, receipt_id: null, state: 'unknown' \}\);/);
});
