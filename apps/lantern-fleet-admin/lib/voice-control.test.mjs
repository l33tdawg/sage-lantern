import assert from 'node:assert/strict';
import test from 'node:test';
import { parseTouchSession } from './voice-control.ts';

test('touch session requires scoped identity, secret and expected short lease', () => {
  const session = { session_id: 'abcd1234-1111-2222-3333-444444444444', token: 'long-random-private-token', lease_seconds: 4 };
  assert.deepEqual(parseTouchSession(session), session);
  for (const value of [null, {}, { ...session, token: '' }, { ...session, session_id: 'invalid' }, { ...session, lease_seconds: 3600 }]) {
    assert.throws(() => parseTouchSession(value));
  }
});
