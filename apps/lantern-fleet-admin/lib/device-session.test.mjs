import assert from 'node:assert/strict';
import test from 'node:test';
import {
  deviceAuth, deviceSessionHeaders, establishedSessionContext, fenceProtectedReply, getDeviceToken,
  observeSessionContext, parseSessionContext, sessionContextEqual, setDeviceToken, subscribeDeviceSession,
  validDeviceToken,
} from './device-session.ts';
import { parseDeviceProfile } from './device-profile.ts';

test('device capabilities are bounded and cleared from request headers on lock', () => {
  const token = 'a'.repeat(43);
  setDeviceToken(null);
  assert.deepEqual(deviceSessionHeaders(), {});
  assert.equal(getDeviceToken(), null);
  for (const invalid of [null, '', 'a'.repeat(42), token + '\n', ' '.repeat(43), 123]) assert.equal(validDeviceToken(invalid), false);
  assert.throws(() => setDeviceToken('invalid'));
  setDeviceToken(token);
  assert.deepEqual(deviceSessionHeaders(), { 'X-Lantern-Session': token });
  setDeviceToken(null);
  assert.deepEqual(deviceSessionHeaders(), {});
});

test('auth requests use fixed loopback routes without browser cookies or redirects', async (context) => {
  const calls = [];
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify({ authorized: false }), { status: 200 });
  });
  assert.deepEqual(await deviceAuth('authorize', { token: 'a'.repeat(43) }), { authorized: false });
  assert.equal(calls[0].url, 'http://127.0.0.1:3196/authorize');
  assert.equal(calls[0].options.credentials, 'omit');
  assert.equal(calls[0].options.redirect, 'error');
  assert.equal(calls[0].options.cache, 'no-store');
  assert.equal(calls[0].options.headers['X-Lantern-Auth'], 'control');
});

test('auth failure never exposes response details', async (context) => {
  context.mock.method(globalThis, 'fetch', async () => new Response('private backend details', { status: 500 }));
  await assert.rejects(deviceAuth('unlock', { pin: '12345678' }), { message: 'auth_unavailable' });
});

test('profile rejects unsupported discovery, unsafe names and contradictory setup states', () => {
  const profile = { version: 1, configured: true, setup_required: false, display_name: 'River', public_discoverable: false, revision: 1 };
  assert.deepEqual(parseDeviceProfile(profile), profile);
  for (const invalid of [null, {}, { ...profile, public_discoverable: true }, { ...profile, setup_required: true }, { ...profile, display_name: '\u202Ename' }, { ...profile, display_name: '' }, { ...profile, revision: -1 }]) assert.throws(() => parseDeviceProfile(invalid));
});

const context = (epoch = 'a'.repeat(32), session_ref = 'b'.repeat(64)) => ({ schema: 'sage.lantern.session-context.v1', epoch, session_ref });

test('a malformed session context is refused, not read as absent', () => {
  // Absent is a device that publishes none (the legacy unlocked mode); malformed
  // is a context that cannot be compared. Collapsing the two is how a rotation
  // would be ignored, so the second one throws.
  assert.equal(parseSessionContext(undefined), null);
  assert.equal(parseSessionContext(null), null);
  assert.deepEqual(parseSessionContext(context()), { epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) });
  for (const invalid of [
    {}, { epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) }, { ...context(), schema: 'sage.lantern.session-context.v2' },
    { ...context(), epoch: 'A'.repeat(32) }, { ...context(), epoch: 'a'.repeat(31) }, { ...context(), epoch: 1 },
    { ...context(), session_ref: 'b'.repeat(63) }, { ...context(), session_ref: null },
    { ...context(), extra: 'x' }, [], 'session', 7,
  ]) assert.throws(() => parseSessionContext(invalid), { message: 'session_context_unavailable' });
});

test('contexts compare by both halves, and half a match is not a match', () => {
  const first = parseSessionContext(context());
  const other = parseSessionContext(context('c'.repeat(32), 'd'.repeat(64)));
  assert.equal(sessionContextEqual(first, first), true);
  assert.equal(sessionContextEqual(null, null), true);
  assert.equal(sessionContextEqual(first, null), false);
  assert.equal(sessionContextEqual(first, other), false);
  // Rotating only one half is still a different authority.
  assert.equal(sessionContextEqual(first, parseSessionContext(context('a'.repeat(32), 'd'.repeat(64)))), false);
  assert.equal(sessionContextEqual(first, parseSessionContext(context('c'.repeat(32), 'b'.repeat(64)))), false);
});

test('a same-token epoch rotation is reported through the one invalidation seam', () => {
  // Rotating the epoch does not change the device token, so setDeviceToken never
  // fires and nothing subscribed to it would learn the authority moved. The
  // observation is what reaches them — through the same seam, not a second one.
  setDeviceToken(null);
  const seen = [];
  const unsubscribe = subscribeDeviceSession(() => seen.push(getDeviceToken()));
  const token = 'b'.repeat(43);
  setDeviceToken(token);
  assert.deepEqual(seen, [token]);
  seen.length = 0;

  assert.equal(observeSessionContext(parseSessionContext(context())), 'established');
  assert.deepEqual(seen, [], 'the first context establishes rather than invalidates');
  assert.equal(observeSessionContext(parseSessionContext(context())), 'same');
  assert.deepEqual(seen, [], 'the same context republished is not a change');
  assert.equal(observeSessionContext(parseSessionContext(context('c'.repeat(32), 'd'.repeat(64)))), 'changed');
  assert.deepEqual(seen, [token], 'the rotation fires the seam with the token unchanged');
  unsubscribe();
  setDeviceToken(null);
});

test('a reading that cannot be compared is refused rather than rendered', () => {
  setDeviceToken(null);
  // A malformed reading is itself evidence that the device publishes contexts,
  // so it drops the authority: a later reading without one is refused rather
  // than read as the unlocked path, which is exactly how a rotation would be
  // lost.
  assert.equal(observeSessionContext('invalid'), 'refused');
  assert.equal(observeSessionContext(null), 'refused');
  assert.equal(observeSessionContext('invalid'), 'refused');
  // Only a fresh valid reading re-establishes an authority.
  assert.equal(observeSessionContext(parseSessionContext(context())), 'established');
  // Once one is established, a reading that has lost it is refused: that is a
  // context that should be there and is not.
  assert.equal(observeSessionContext(null), 'refused');
  assert.equal(observeSessionContext('invalid'), 'refused');
  // Replacing the token clears the observation instead of comparing a new
  // session against a dead epoch, so a device that publishes nothing is the
  // legacy path again.
  setDeviceToken('c'.repeat(43));
  assert.equal(observeSessionContext(null), 'legacy');
  setDeviceToken(null);
});

test('the established authority distinguishes nothing-observed from a legacy device', () => {
  setDeviceToken(null);
  setDeviceToken('d'.repeat(43));
  // Nothing has been observed yet: not the same answer as a device that
  // publishes no context, and a protected reply cannot be judged against it.
  assert.deepEqual(establishedSessionContext(), { state: 'unknown' });
  assert.equal(observeSessionContext(null), 'legacy');
  assert.equal(establishedSessionContext().state, 'legacy');
  assert.ok(Number.isSafeInteger(establishedSessionContext().generation));
  const reading = parseSessionContext(context());
  assert.equal(observeSessionContext(reading), 'established');
  const established = establishedSessionContext();
  assert.equal(established.state, 'context');
  assert.deepEqual(established.context, reading);
  // Repeating the same reading re-establishes nothing: it is the same authority
  // object, so work captured under it is still current.
  assert.equal(observeSessionContext(reading), 'same');
  assert.deepEqual(establishedSessionContext(), established);
  // A replaced token clears it: the new session's authority is not established
  // until a reading says what it is.
  setDeviceToken('e'.repeat(43));
  assert.deepEqual(establishedSessionContext(), { state: 'unknown' });
  setDeviceToken(null);
});

test('a protected reply is judged against the authority it was issued under', () => {
  setDeviceToken(null);
  setDeviceToken('f'.repeat(43));
  const a = parseSessionContext(context());
  const b = parseSessionContext(context('c'.repeat(32), 'd'.repeat(64)));
  // Nothing established: the reply cannot be judged, which is not permission.
  assert.equal(fenceProtectedReply({ state: 'unknown' }, a), 'unverifiable');
  observeSessionContext(null);
  // A legacy device stamps nothing, and a reply that does carry a context under
  // it is somebody else's.
  assert.equal(fenceProtectedReply(establishedSessionContext(), null), 'ok');
  assert.equal(fenceProtectedReply(establishedSessionContext(), a), 'not-issued-under');
  assert.equal(observeSessionContext(a), 'established');
  const issuedUnder = establishedSessionContext();
  assert.equal(fenceProtectedReply(issuedUnder, a), 'ok');
  assert.equal(fenceProtectedReply(issuedUnder, b), 'not-issued-under');
  // The reply is the one this request was admitted under, and the epoch moved
  // while it was in flight: true when written, obsolete when it landed.
  assert.equal(observeSessionContext(b), 'changed');
  assert.equal(fenceProtectedReply(issuedUnder, a), 'moved');
  // A→B→A ends on the very same context, and must not revive the authority
  // captured before the move: the object was re-keyed, so the reply issued under
  // it is no longer the one this client holds.
  assert.equal(observeSessionContext(a), 'changed');
  assert.equal(fenceProtectedReply(issuedUnder, a), 'not-issued-under');
  // A stamp that cannot be compared at all is refused rather than trusted as
  // "no context", and a locked device's reply always carries one.
  assert.equal(fenceProtectedReply(establishedSessionContext(), null), 'not-issued-under');
  setDeviceToken(null);
});
