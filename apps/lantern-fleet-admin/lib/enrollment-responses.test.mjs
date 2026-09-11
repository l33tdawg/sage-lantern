import assert from 'node:assert/strict';
import test from 'node:test';
import { parseCreatedEnrollment, parseEnrollmentList, parseEnrollmentSession } from './enrollment-responses.ts';

const session = { user: { userId: 'owner', email: 'owner@example.test', displayName: 'Owner' },
  role: null, capabilities: { read: false, createEnrollment: false, approveEnrollment: false } };
const enrollment = { enrollment_id: 'record', device_id: 'lantern-a', status: 'pending',
  expires_at: '2026-09-08T14:00:00Z' };

test('session parser preserves explicit false and rejects non-boolean authority', () => {
  assert.deepEqual(parseEnrollmentSession(session), session);
  for (const field of Object.keys(session.capabilities)) {
    for (const value of [undefined, null, 0, 1, 'false', [], {}]) {
      assert.throws(() => parseEnrollmentSession({ ...session, capabilities: { ...session.capabilities, [field]: value } }));
    }
  }
  for (const value of [null, [], {}, { ...session, user: [] }, { ...session, role: 1 }]) {
    assert.throws(() => parseEnrollmentSession(value));
  }
});

test('list validates actual rows rather than converting malformed responses to empty success', () => {
  assert.deepEqual(parseEnrollmentList({ enrollments: [] }), []);
  assert.deepEqual(parseEnrollmentList({ enrollments: [{ ...enrollment, comparison_code: null,
    approved_at: null, management_fingerprint: null, created_at: 'server-extra' }] }),
  [{ ...enrollment, comparison_code: null, approved_at: null, management_fingerprint: null }]);
  for (const value of [null, {}, { enrollments: null }, { enrollments: {} }, { enrollments: [enrollment, enrollment] },
    { enrollments: Array(501).fill(enrollment) }]) assert.throws(() => parseEnrollmentList(value));
  for (const patch of [{ enrollment_id: 1 }, { device_id: null }, { status: 'foreign' }, { expires_at: 'bad' },
    { comparison_code: 123456 }, { comparison_code: '123' }, { approved_at: 'bad' }, { management_fingerprint: {} }]) {
    assert.throws(() => parseEnrollmentList({ enrollments: [{ ...enrollment, ...patch }] }));
  }
  assert.equal(parseEnrollmentList({ enrollments: [{ ...enrollment, status: 'claimed', comparison_code: '001234' }] })[0].comparison_code, '001234');
});

test('creation requires pending state and actual single-use secret shape; errors never disclose secret', () => {
  const created = { ...enrollment, setup_secret: 'a'.repeat(43) };
  assert.deepEqual(parseCreatedEnrollment(created), created);
  for (const patch of [{ status: 'issued' }, { setup_secret: undefined }, { setup_secret: 'SECRET' },
    { setup_secret: {} }, { setup_secret: '+'.repeat(43) }]) {
    assert.throws(() => parseCreatedEnrollment({ ...created, ...patch }), (error) => {
      assert.equal(error.message, 'Invalid enrollment response');
      return true;
    });
  }
});
