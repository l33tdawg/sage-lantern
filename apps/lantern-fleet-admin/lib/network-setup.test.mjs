import assert from 'node:assert/strict';
import test from 'node:test';
import { networkSetupRequest, parseSetupNetworks, parseSetupStatus } from './network-setup.ts';

test('setup status and scan validate actual response shapes', () => {
  const status = { unit_id: 'unit-b', interface: 'wlan0', connected: false, ssid: null };
  assert.deepEqual(parseSetupStatus(status), status);
  for (const value of [{ ...status, connected: 1 }, { ...status, unit_id: 'unit-b;id' }, { ...status, connected: true }, null]) assert.throws(() => parseSetupStatus(value));
  const networks = [{ ssid: 'Village network', signal: 70, security: 'wpa-psk' }];
  assert.deepEqual(parseSetupNetworks({ networks }), networks);
  for (const change of [{ signal: 101 }, { signal: '50' }, { ssid: 'x'.repeat(33) }, { ssid: '\n' }, { security: 'enterprise' }]) assert.throws(() => parseSetupNetworks({ networks: [{ ...networks[0], ...change }] }));
});

test('setup client uses ephemeral header, fixed endpoint and no cookies', async (context) => {
  const token = 'a'.repeat(43);
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:3195/status');
    assert.equal(options.headers['X-Lantern-Setup'], token);
    assert.equal(options.credentials, 'omit');
    assert.equal(options.redirect, 'error');
    assert.equal(options.cache, 'no-store');
    return Response.json({ connected: false });
  });
  assert.deepEqual(await networkSetupRequest('status', token), { connected: false });
  await assert.rejects(networkSetupRequest('status', 'bad'));
});

test('setup errors do not expose backend text and bound response size', async (context) => {
  const mock = context.mock.method(globalThis, 'fetch', async () => new Response('secret detail', { status: 500 }));
  await assert.rejects(networkSetupRequest('scan', 'a'.repeat(43)), /could not be confirmed/);
  mock.mock.mockImplementation(async () => new Response('x'.repeat(32769), { headers: { 'content-type': 'application/json' } }));
  await assert.rejects(networkSetupRequest('scan', 'a'.repeat(43)), /Invalid setup response/);
});
