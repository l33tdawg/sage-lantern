import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDeviceSetup, validRecoveryKey } from './device-setup.ts';

test('setup requires explicit booleans and rejects contradictory initialization', () => {
  const fresh = { enabled: false, recovery_backup_confirmed: false, has_existing_vault: false, can_initialize: true };
  assert.deepEqual(parseDeviceSetup(fresh), fresh);
  for (const invalid of [null, [], {}, { ...fresh, enabled: 'false' }, { ...fresh, enabled: true }, { ...fresh, has_existing_vault: true }]) {
    assert.throws(() => parseDeviceSetup(invalid));
  }
  for (const field of Object.keys(fresh)) {
    const incomplete = { ...fresh };
    delete incomplete[field];
    assert.throws(() => parseDeviceSetup(incomplete));
  }
});

test('recovery display accepts bounded base64 keys only', () => {
  assert.equal(validRecoveryKey(Buffer.alloc(32, 1).toString('base64')), true);
  for (const invalid of [null, 123, '', 'secret', 'a'.repeat(44), 'a'.repeat(43) + '=\n', ' '.repeat(43) + '=']) {
    assert.equal(validRecoveryKey(invalid), false);
  }
});
