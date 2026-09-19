import assert from 'node:assert/strict';
import test from 'node:test';
import { parseRadioStatus, radioStatusIsFresh } from './radio-status.ts';

function fixture() {
  return { schema: 'sage.lantern.radio-events.v1', observed_at_unix_ms: 1000,
    valid_for_ms: 2000, window_ms: 60000, serial_identity_verified: true,
    firmware: 'v1.17.1-d929643', receiving: true, profile: null,
    repeater: { enabled: false, source: 'firmware_info' },
    saved_contacts: { available: false, count: null },
    neighbors: { available: false, reason: 'no_rf_neighbor_discovery' },
    last_heard: { source: 'firmware_dequeued_contact_messages', entries: [{
      id: 'a'.repeat(64), dequeued_at_unix_ms: 900, age_ms: 100, snr_db: -1.25,
      rssi_dbm: null, rf_received_at_unix_ms: null,
      application_authenticated: false, direct_neighbor_verified: false }] } };
}

test('radio parser retains actual SNR without claiming neighbors or reception time', () => {
  const value = fixture();
  assert.deepEqual(parseRadioStatus(value), value);
  value.last_heard.entries[0].snr_db = null;
  assert.ok(parseRadioStatus(value));
  value.last_heard.entries = [];
  value.receiving = false;
  value.serial_identity_verified = false;
  value.firmware = null;
  value.repeater = null;
  assert.ok(parseRadioStatus(value));
});

test('radio parser reports repeater state and admits r3/r4 firmware', () => {
  const value = fixture();
  value.firmware = 'v1.17.1-d929643-r4';
  value.repeater = { enabled: true, source: 'firmware_info' };
  assert.deepEqual(parseRadioStatus(value), value);
  value.firmware = 'v1.17.1-d929643-r3';
  assert.ok(parseRadioStatus(value));
});

test('radio parser refuses forged capabilities, extra content and invalid observations', () => {
  const mutations = [
    value => { value.secret = 'text'; },
    value => { value.repeater = { enabled: true, source: 'guess' }; },
    value => { value.repeater = { enabled: 'yes', source: 'firmware_info' }; },
    value => { value.repeater = null; },
    value => { delete value.repeater; },
    value => { value.neighbors.available = true; },
    value => { value.saved_contacts.count = 0; },
    value => { value.receiving = false; },
    value => { value.serial_identity_verified = false; },
    value => { value.valid_for_ms = 3000; },
    value => { value.last_heard.entries.push(value.last_heard.entries[0]); },
    ...['application_authenticated', 'direct_neighbor_verified'].map(key => value => { value.last_heard.entries[0][key] = true; }),
    ...[-33, 32, 0.1, NaN, Infinity, true].map(snr => value => { value.last_heard.entries[0].snr_db = snr; }),
    ...[-1, 60000, 1.5, true].map(age => value => { value.last_heard.entries[0].age_ms = age; }),
    value => { value.last_heard.entries[0].rssi_dbm = -60; },
    value => { value.last_heard.entries[0].rf_received_at_unix_ms = 900; },
    value => { value.last_heard.entries[0].text = 'private'; },
  ];
  for (const mutate of mutations) {
    const value = fixture(); mutate(value);
    assert.equal(parseRadioStatus(value), undefined);
  }
});

test('radio freshness uses a bounded monotonic request lifetime', () => {
  assert.equal(radioStatusIsFresh(100, 2099), true);
  for (const now of [99, 2100, Infinity, NaN]) assert.equal(radioStatusIsFresh(100, now), false);
});
