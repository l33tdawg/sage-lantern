import test from 'node:test';
import assert from 'node:assert/strict';
import { appendTemperature, storagePercent, temperatureSegments } from './device-metrics.ts';
test('bounded measured samples never interpolate outages', () => {
  let samples = [];
  for (let index = 0; index < 30; index++) samples = appendTemperature(samples, index * 5000, 40000);
  assert.equal(samples.length, 24);
  assert.equal(appendTemperature(samples, 145001, 40000), samples);
  assert.equal(temperatureSegments([{ at: 0, celsius: 40 }, { at: 5000, celsius: null }, { at: 10000, celsius: 41 }]).length, 0);
  assert.equal(temperatureSegments([{ at: 0, celsius: 40 }, { at: 20000, celsius: 41 }]).length, 0);
});
test('storage chart requires valid measured total', () => {
  assert.equal(storagePercent(25, 100), 75);
  for (const pair of [[0, 0], [101, 100], [-1, 100], [1, Infinity], [undefined, 100]]) assert.equal(storagePercent(...pair), null);
});
