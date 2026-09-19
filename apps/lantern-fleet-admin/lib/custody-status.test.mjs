import assert from 'node:assert/strict';
import test from 'node:test';
import { parseCustodyStatus } from './custody-status.ts';

function fixture() {
  return { schema: 'sage.lantern.mesh-custody-status.v1', enabled: true, held: 3, bytes: 240,
    recipients: 2, oldest_expires_at: 1789803690, delivered: 5, depositors: 2 };
}

test('custody parser accepts held counts and the disabled empty shape', () => {
  assert.deepEqual(parseCustodyStatus(fixture()), fixture());
  const off = { schema: 'sage.lantern.mesh-custody-status.v1', enabled: false, held: 0, bytes: 0,
    recipients: 0, oldest_expires_at: null, delivered: 0, depositors: 0 };
  assert.deepEqual(parseCustodyStatus(off), off);
});

test('custody parser refuses content, impossible counts and enabled-empty lies', () => {
  const mutations = [
    value => { value.wire = 'SLC1.secret'; },
    value => { value.held = -1; },
    value => { value.held = 1.5; },
    value => { value.recipients = 4; },
    value => { value.held = 0; },
    value => { value.oldest_expires_at = 0; },
    value => { value.enabled = false; },
    value => { value.depositors = 65; },
  ];
  for (const mutate of mutations) {
    const value = fixture(); mutate(value);
    assert.equal(parseCustodyStatus(value), undefined);
  }
});
