import assert from 'node:assert/strict';
import test from 'node:test';
import { parseCorrectionConfirmation } from './correction-reconciliation.ts';

const unrecorded = {
  status: 'unrecorded', retry_allowed: false, risk_acknowledgement_required: true,
  continuity: 'current_snapshot_only', challenge: 'a'.repeat(43),
  expected_revision: 0, expires_in_seconds: 120,
};

test('confirmation preserves explicit snapshot uncertainty without retry authority', () => {
  assert.equal(parseCorrectionConfirmation(unrecorded).expectedRevision, 0);
  for (const status of ['reserved', 'admitting', 'accepted', 'completed', 'failed']) {
    assert.equal(parseCorrectionConfirmation({ ...unrecorded, status, revision: 4, expected_revision: 4 }).status, status);
  }
  assert.equal('retry_allowed' in parseCorrectionConfirmation(unrecorded), false);
});

test('malformed or incompatible confirmation fails closed', () => {
  for (const value of [null, [], {},
    { ...unrecorded, retry_allowed: true }, { ...unrecorded, risk_acknowledgement_required: false },
    { ...unrecorded, continuity: 'verified' }, { ...unrecorded, challenge: 'short' },
    { ...unrecorded, expires_in_seconds: 121 }, { ...unrecorded, expected_revision: -1 },
    { ...unrecorded, expected_revision: Number.MAX_SAFE_INTEGER + 1 },
    { ...unrecorded, status: 'running' }, { ...unrecorded, status: 'completed', revision: 1 },
    { ...unrecorded, status: 'accepted', revision: 2, expected_revision: 1 },
  ]) assert.throws(() => parseCorrectionConfirmation(value));
});
