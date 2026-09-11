import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { readFileSync } from 'node:fs';
import { getDeviceToken, setDeviceToken } from './device-session.ts';
import { linkVoiceSession } from './voice-control.ts';

const originalFetch = globalThis.fetch;
const token = 't'.repeat(43);
afterEach(() => { globalThis.fetch = originalFetch; setDeviceToken(null); });

test('an offline voice service never revokes an authenticated device session', async () => {
  setDeviceToken(token);
  globalThis.fetch = async () => { throw new TypeError('Connection refused'); };
  assert.equal(await linkVoiceSession(), false);
  assert.equal(getDeviceToken(), token);
});

test('voice rejection or malformed response does not become authentication authority', async () => {
  setDeviceToken(token);
  for (const response of [new Response('{}', { status: 403 }), new Response('invalid')]) {
    globalThis.fetch = async () => response;
    assert.equal(await linkVoiceSession(), false);
    assert.equal(getDeviceToken(), token);
  }
});

test('voice session linking requires an existing token and retries after recovery', async () => {
  let requests = 0;
  globalThis.fetch = async (url, options) => {
    requests += 1;
    assert.equal(url, 'http://127.0.0.1:3197/session/link');
    assert.equal(options.headers['X-Lantern-Session'], token);
    return new Response('{}');
  };
  assert.equal(await linkVoiceSession(), false);
  assert.equal(requests, 0);
  setDeviceToken(token);
  assert.equal(await linkVoiceSession(), true);
  assert.equal(requests, 1);
});

test('unlock and presence do not wait for voice; authentication failures still lock', () => {
  const source = readFileSync(new URL('../app/device/access.tsx', import.meta.url), 'utf8');
  assert.equal((source.match(/void linkVoiceSession\(\)/g) || []).length, 2);
  assert.doesNotMatch(source, /await (?:linkVoiceSession|voiceCommand)/);
  assert.match(source, /if \(activated.authorized !== true\) throw/);
  assert.match(source, /if \(result.authorized !== true\) lock\(\)/);
  assert.match(source, /if \(!disposed\) \{ setLockError\(true\); if \(getDeviceToken\(\)\) lock\(\); \}/);
});
