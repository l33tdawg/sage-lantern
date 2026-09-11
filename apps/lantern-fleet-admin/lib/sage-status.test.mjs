import assert from 'node:assert/strict';
import test from 'node:test';
import { parseSageStatus, sageStatusIsFresh } from './sage-status.ts';

const status = { available: true, scope: 'node_projection', observed_at: '2026-09-05T08:00:00Z', committed_memories: 0, block_height: 4487, validator_count: 1, chain_peers: 0, federation_enabled: null };

test('SAGE telemetry distinguishes real zero counts from unavailable measurements', () => {
  assert.equal(parseSageStatus(status).committed_memories, 0);
  assert.equal(parseSageStatus({ ...status, committed_memories: null }).committed_memories, null);
  for (const value of [-1, NaN, Infinity, 1.5, true, '0', Number.MAX_SAFE_INTEGER + 1]) {
    assert.equal(parseSageStatus({ ...status, committed_memories: value }), undefined);
  }
  assert.equal(parseSageStatus({ ...status, federation_enabled: 'on' }), undefined);
});

test('SAGE freshness is independent from overall device status polling', () => {
  const now = Date.parse(status.observed_at);
  assert.equal(sageStatusIsFresh(status, now + 20000), true);
  assert.equal(sageStatusIsFresh(status, now + 31000), false);
  assert.equal(sageStatusIsFresh({ ...status, available: false }, now), false);
  assert.equal(sageStatusIsFresh({ ...status, observed_at: null }, now), false);
});
