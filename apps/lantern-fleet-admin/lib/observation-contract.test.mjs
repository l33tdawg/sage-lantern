import assert from 'node:assert/strict';
import test from 'node:test';
import { canonicalJson, OBSERVATION_SCHEMA, verifySignedObservation } from './observation-contract.ts';

const encoder = new TextEncoder();
const base64 = (value) => Buffer.from(value).toString('base64');
const hex = (value) => [...new Uint8Array(value)].map((byte) => byte.toString(16).padStart(2, '0')).join('');

async function fixture() {
  const keys = await crypto.subtle.generateKey({ name: 'Ed25519' }, true, ['sign', 'verify']);
  const publicKey = new Uint8Array(await crypto.subtle.exportKey('raw', keys.publicKey));
  const fingerprint = `sha256:${hex(await crypto.subtle.digest('SHA-256', publicKey))}`;
  const state = { release: '0.1.0', services: { runtime: 'ready' }, temperature_millicelsius: 48_000 };
  const stateHash = hex(await crypto.subtle.digest('SHA-256', encoder.encode(canonicalJson(state))));
  const observation = {
    schema: OBSERVATION_SCHEMA,
    device_id: 'lantern-a',
    sequence: 10,
    observed_at: '2026-09-02T08:00:00Z',
    freshness_expires_at: '2026-09-02T08:05:00Z',
    clock_confidence: 'ntp-synchronized',
    source_fingerprint: fingerprint,
    state,
    state_hash: stateHash,
  };
  const signature = await crypto.subtle.sign('Ed25519', keys.privateKey, encoder.encode(canonicalJson(observation)));
  return {
    signed: { observation, signature: base64(signature) },
    identity: { deviceId: 'lantern-a', fingerprint, publicKey: base64(publicKey), lastAcceptedSequence: 9 },
  };
}

test('accepts a registered, fresh, monotonic signed observation', async () => {
  const { signed, identity } = await fixture();
  const verified = await verifySignedObservation(signed, identity, new Date('2026-09-02T08:01:00Z'));
  assert.equal(verified.deviceId, 'lantern-a');
  assert.equal(verified.sequence, 10);
  assert.equal(verified.stateHash, signed.observation.state_hash);
});

test('rejects stale, replayed, and tampered observations independently', async () => {
  const { signed, identity } = await fixture();
  await assert.rejects(
    verifySignedObservation(signed, identity, new Date('2026-09-02T08:06:00Z')),
    /stale/,
  );
  await assert.rejects(
    verifySignedObservation(signed, { ...identity, lastAcceptedSequence: 10 }, new Date('2026-09-02T08:01:00Z')),
    /replayed/,
  );
  const tampered = structuredClone(signed);
  tampered.observation.state.release = 'evil';
  await assert.rejects(
    verifySignedObservation(tampered, identity, new Date('2026-09-02T08:01:00Z')),
    /state hash/,
  );
});

test('canonical contract rejects floating point telemetry', () => {
  assert.throws(() => canonicalJson({ temperature_celsius: 48.5 }), /safe integers/);
});

test('verifies a golden observation signed by the Python Device Agent', async () => {
  const signed = JSON.parse('{"observation":{"clock_confidence":"ntp-synchronized","device_id":"lantern-golden","freshness_expires_at":"2026-09-02T08:05:00Z","observed_at":"2026-09-02T08:00:00Z","schema":"sage.lantern.management.observation.v1","sequence":42,"source_fingerprint":"sha256:56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c","state":{"nested":{"enabled":true,"labels":["kampung","灯"]},"temperature_millicelsius":48500},"state_hash":"57554fe607d43b730773a09134d8f992f5fcbc695abc1a8cb6af0433fd22c979"},"signature":"yBL01UUJwf/38+SIwtIQMSYv77+MOZcX4jqQze9SsjfWu6C+cTpsVysAkODRu13il8bndB7ZRz3tb3z1A9IIDA=="}');
  const identity = {
    deviceId: 'lantern-golden',
    publicKey: 'A6EHv/POEL4dcN0Y50vAmWfk1jCbpQ1fHdyGZBJVMbg=',
    fingerprint: 'sha256:56475aa75463474c0285df5dbf2bcab73da651358839e9b77481b2eab107708c',
    lastAcceptedSequence: 41,
  };
  const verified = await verifySignedObservation(signed, identity, new Date('2026-09-02T08:01:00Z'));
  assert.equal(verified.sequence, 42);
});
