import assert from 'node:assert/strict';
import test from 'node:test';
import { clearRadioSample, getRadioSample, linkBars, observeRadioSample } from './radio-sample.ts';

test('dashboard samples survive home remount without inventing fresh packets', () => {
  clearRadioSample();
  const status = (packets, uptime = 100, snr = 6.5) => ({ serial_identity_verified: true,
    health: { packets_received: packets, uptime_seconds: uptime, last_rssi_dbm: -91, last_snr_db: snr } });
  observeRadioSample(status(10), 'session', 1000);
  assert.equal(getRadioSample('session').bars, 0);
  observeRadioSample(status(11), 'session', 2000);
  assert.equal(getRadioSample('session').bars, 3);
  assert.equal(getRadioSample('session').snr, 6.5);
  assert.equal(getRadioSample('session').rssi, -91);
  observeRadioSample(status(11), 'session', 6000);
  assert.equal(getRadioSample('session').heardAt, 2000);
  assert.equal(getRadioSample('other'), null);
  observeRadioSample(status(0, 1), 'session', 7000);
  assert.equal(getRadioSample('session').bars, 0);
  assert.equal(getRadioSample('session').snr, null);
  clearRadioSample();
  assert.equal(getRadioSample('session'), null);
});

test('bars follow measured SNR, including zero and negative values', () => {
  assert.equal(linkBars(13.5), 4);
  assert.equal(linkBars(10), 4);
  assert.equal(linkBars(9.75), 3);
  assert.equal(linkBars(5), 3);
  assert.equal(linkBars(4.75), 2);
  assert.equal(linkBars(0), 2);
  assert.equal(linkBars(-0.25), 1);
  assert.equal(linkBars(-7.5), 1);
});

test('a measured packet updates the meter even when the firmware reports zero RSSI', () => {
  clearRadioSample();
  const status = (packets) => ({ serial_identity_verified: true,
    health: { packets_received: packets, uptime_seconds: 100, last_rssi_dbm: 0, last_snr_db: 12 } });
  observeRadioSample(status(4), 'session', 100);
  observeRadioSample(status(5), 'session', 200);
  const sample = getRadioSample('session');
  assert.equal(sample.bars, 4);
  assert.equal(sample.rssi, 0);
  assert.equal(sample.snr, 12);
});
