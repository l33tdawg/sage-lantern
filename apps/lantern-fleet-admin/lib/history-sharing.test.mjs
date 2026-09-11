import assert from 'node:assert/strict';
import test from 'node:test';
import { parseHistoryShares } from './community.ts';
import { parseDeviceHistory } from './device-history.ts';

const source = '11111111-1111-4111-8111-111111111111';
const other = '22222222-2222-4222-8222-222222222222';

test('history cannot associate two different answers with one public source ID', () => {
  const item = { session_id: source, created_at: '2026-09-06T01:00:00Z', transcript: 'Question', response: 'Answer' };
  assert.throws(() => parseDeviceHistory({ enabled: true, items: [item, { ...item, response: 'Different answer' }] }));
  assert.equal(parseDeviceHistory({ enabled: true, items: [item, { ...item, session_id: other }] }).items.length, 2);
});

test('history states bind exact source IDs, not question text', () => {
  for (const state of ['not_shared', 'queued', 'pending', 'accepted', 'rejected', 'unknown']) {
    assert.deepEqual(parseHistoryShares({ items: [{ source_id: source, state }] }, [source]), { [source]: state });
  }
});

test('missing, duplicate, unrelated and malformed states never become accepted or unshared', () => {
  for (const items of [[], [{ source_id: other, state: 'accepted' }], [{ source_id: source, state: 'sent' }], [{ source_id: source, state: 'accepted', verified: true }]]) {
    assert.throws(() => parseHistoryShares({ items }, [source]));
  }
  assert.throws(() => parseHistoryShares({ items: [{ source_id: source, state: 'accepted' }, { source_id: source, state: 'pending' }] }, [source, other]));
  assert.throws(() => parseHistoryShares({ items: [] }, Array(21).fill(source)));
});
