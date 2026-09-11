import assert from 'node:assert/strict';
import test from 'node:test';
import { parseDeviceHistory, parseHistoryPhoto } from './device-history.ts';

test('history validates disabled and populated responses', () => {
  assert.deepEqual(parseDeviceHistory({ enabled: false, items: [] }), { enabled: false, items: [] });
  const entry = { session_id: 'one', created_at: '2026-09-05T01:00:00Z', transcript: 'Question', response: 'Answer' };
  assert.equal(parseDeviceHistory({ enabled: true, items: [entry] }).items[0].response, 'Answer');
});

test('photo capability and attachment flags are never inferred', () => {
  assert.equal(parseDeviceHistory({ enabled: true, items: [] }).photo_saving_supported, undefined);
  for (const field of ['photos_enabled', 'photo_saving_supported']) {
    assert.throws(() => parseDeviceHistory({ enabled: true, items: [], [field]: 'true' }));
  }
  const item = { session_id: 'one', created_at: '2026-09-05T01:00:00Z', transcript: 'q', response: 'a', has_photo: true };
  assert.equal(parseDeviceHistory({ enabled: true, items: [item] }).items[0].has_photo, true);
  assert.throws(() => parseDeviceHistory({ enabled: true, items: [{ ...item, has_photo: 'true' }] }));
});

test('history photo response is bounded JPEG data, never a remote URL', () => {
  assert.equal(parseHistoryPhoto({ image_base64: '/9j/2Q==' }), 'data:image/jpeg;base64,/9j/2Q==');
  for (const image_base64 of ['https://example.com/photo', 'data:text/html,test', '', '/9j/<bad', '/9j/' + 'a'.repeat(2796204)]) {
    assert.throws(() => parseHistoryPhoto({ image_base64 }));
  }
});

test('history rejects malformed or excessive records', () => {
  for (const input of [null, {}, { enabled: 'yes', items: [] }, { enabled: true, items: [null] },
    { enabled: true, items: Array(101).fill({}) }, { enabled: true, items: [{ session_id: 'x', created_at: 'y', transcript: 'q', response: 'a' }] }]) {
    assert.throws(() => parseDeviceHistory(input));
  }
});

test('retention capability must be a boolean and is never inferred', () => {
  assert.equal(parseDeviceHistory({ enabled: true, items: [] }).saving_off_keeps_items, undefined);
  assert.equal(parseDeviceHistory({ enabled: true, items: [], saving_off_keeps_items: true }).saving_off_keeps_items, true);
  assert.throws(() => parseDeviceHistory({ enabled: true, items: [], saving_off_keeps_items: 'true' }));
});
