import assert from 'node:assert/strict';
import test from 'node:test';
import { deviceAuth, deviceSessionHeaders, getDeviceToken, setDeviceToken, validDeviceToken } from './device-session.ts';
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
