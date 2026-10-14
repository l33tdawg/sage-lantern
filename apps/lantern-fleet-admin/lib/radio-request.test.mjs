import assert from 'node:assert/strict';
import test from 'node:test';
import { getDeviceToken, observeSessionContext, parseSessionContext, setDeviceToken, subscribeDeviceSession } from './device-session.ts';
import { requestRadioStatus } from './radio-request.ts';

test('session revocation notifies observers synchronously and supports cleanup', () => {
  setDeviceToken(null);
  const observed = [];
  const unsubscribe = subscribeDeviceSession(() => observed.push(getDeviceToken()));
  try {
    setDeviceToken('a'.repeat(43));
    setDeviceToken('a'.repeat(43));
    setDeviceToken(null);
    assert.deepEqual(observed, ['a'.repeat(43), null]);
    unsubscribe();
    setDeviceToken('b'.repeat(43));
    assert.equal(observed.length, 2);
  } finally { unsubscribe(); setDeviceToken(null); }
});

test('radio reads use the authenticated local route and reject revoked sessions', async () => {
  const original = globalThis.fetch;
  const value = { schema: 'sage.lantern.radio-events.v1', observed_at_unix_ms: 1000,
    valid_for_ms: 2000, window_ms: 60000, serial_identity_verified: false,
    firmware: null, receiving: false, repeater: null, profile: null,
    saved_contacts: { available: false, count: null },
    neighbors: { available: false, reason: 'no_rf_neighbor_discovery' },
    last_heard: { source: 'firmware_dequeued_contact_messages', entries: [] } };
  // A locked device stamps every successful protected reply with the authority it
  // admitted the command under; an unstamped reply from a device that claims to
  // be locked is not modelling a device that exists.
  const stamp = { schema: 'sage.lantern.session-context.v1', epoch: 'a'.repeat(32), session_ref: 'b'.repeat(64) };
  const stamped = (body) => Response.json({ ...body, session_context: stamp });
  try {
    setDeviceToken('a'.repeat(43));
    observeSessionContext(parseSessionContext(stamp));
    globalThis.fetch = async (url, options) => {
      assert.equal(url, 'http://127.0.0.1:3197/community/radio/status');
      assert.equal(options.headers['X-Lantern-Session'], 'a'.repeat(43));
      assert.equal(options.body, '{}');
      return stamped(value);
    };
    assert.deepEqual((await requestRadioStatus()).value, value);
    globalThis.fetch = async () => {
      setDeviceToken(null);
      return stamped(value);
    };
    await assert.rejects(requestRadioStatus(), /locked/);
    setDeviceToken('a'.repeat(43));
    observeSessionContext(parseSessionContext(stamp));
    globalThis.fetch = async () => stamped({ ...value, receiving: true });
    await assert.rejects(requestRadioStatus(), /unavailable/);
    const controller = new AbortController();
    controller.abort();
    globalThis.fetch = async () => { assert.fail('aborted request must not fetch'); };
    await assert.rejects(requestRadioStatus(controller.signal));
  } finally {
    globalThis.fetch = original;
    setDeviceToken(null);
  }
});
