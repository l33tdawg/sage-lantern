import assert from 'node:assert/strict';
import test from 'node:test';
import { heardContactAge } from './radio-directory.ts';
import { parseRadioContacts } from './community.ts';

test('radio directory uses full keys, consent expiry and elapsed observation age', () => {
  const contact = { id: 'mesh-contact:abc', name: 'Same name', radio_public_key: 'a'.repeat(64), chain_id: 'village', agent_id: 'b'.repeat(64), expires_at: 1000 };
  assert.deepEqual(parseRadioContacts({ items: [contact], more: false }).items, [contact]);
  const node = { public_key: contact.radio_public_key, name: 'Different name', age_seconds: 20 };
  assert.equal(heardContactAge(contact, [contact], [node], 10, 100), 30);
  assert.equal(heardContactAge(contact, [contact], [node], 280, 100), null);
  assert.equal(heardContactAge(contact, [contact], [node], 0, 1000), null);
  assert.equal(heardContactAge(contact, [contact, { ...contact, id: 'other' }], [node], 0, 100), null);
  assert.equal(heardContactAge(contact, [contact], [{ ...node, public_key: 'a'.repeat(63) + 'b', name: contact.name }], 0, 100), null);
  assert.throws(() => parseRadioContacts({ items: [{ ...contact, radio_public_key: 'abcd' }], more: false }));
});
