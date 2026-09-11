import assert from 'node:assert/strict';
import test from 'node:test';
import {
  authorizeFleetCapability,
  parseRoleAssignments,
} from './operator-authorization.ts';

const scope = { type: 'global', id: '*' };
const now = new Date('2026-09-02T10:00:00Z');

test('fails closed when assignments are missing or malformed', () => {
  assert.deepEqual(parseRoleAssignments(undefined), []);
  assert.deepEqual(parseRoleAssignments('{bad json'), []);
  assert.deepEqual(parseRoleAssignments(JSON.stringify([{ role: 'fleet_operator' }])), []);
  assert.equal(authorizeFleetCapability('user-1', 'fleet.read', scope, [], now), null);
});

test('requires an exact user, capability, and scope match', () => {
  const assignments = parseRoleAssignments(JSON.stringify([{
    authenticatedUserId: 'user-1',
    role: 'technician',
    scopeType: 'global',
    scopeId: '*',
    expiresAt: '2026-09-03T10:00:00Z',
  }]));
  assert.ok(authorizeFleetCapability('user-1', 'enrollment.create', scope, assignments, now));
  assert.equal(authorizeFleetCapability('user-1', 'enrollment.approve', scope, assignments, now), null);
  assert.equal(authorizeFleetCapability('user-2', 'fleet.read', scope, assignments, now), null);
});

test('rejects expired assignments and grants fleet operators approval', () => {
  const assignments = parseRoleAssignments(JSON.stringify([
    {
      authenticatedUserId: 'expired-user',
      role: 'fleet_operator',
      scopeType: 'global',
      scopeId: '*',
      expiresAt: '2026-09-02T09:59:59Z',
    },
    {
      authenticatedUserId: 'operator-user',
      role: 'fleet_operator',
      scopeType: 'global',
      scopeId: '*',
      expiresAt: null,
    },
  ]));
  assert.equal(authorizeFleetCapability('expired-user', 'fleet.read', scope, assignments, now), null);
  assert.ok(authorizeFleetCapability('operator-user', 'enrollment.approve', scope, assignments, now));
});
