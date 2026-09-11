import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { parsePowerSettings, savePowerSettings, loadPowerSettings } from './power-settings.ts';
import { setDeviceToken } from './device-session.ts';

const settings = { revision: 0, display_off_seconds: 30, deep_sleep_seconds: 300, deep_sleep_available: false };

test('power settings reject unsafe delays and false capability shapes', () => {
  assert.deepEqual(parsePowerSettings(settings), settings);
  for (const changes of [{ revision: -1 }, { display_off_seconds: 0 }, { deep_sleep_available: 1 }, { display_off_seconds: 300 }, { unexpected: true }]) assert.throws(() => parsePowerSettings({ ...settings, ...changes }));
});

test('power settings use authenticated CAS and never silently unlock', async (context) => {
  setDeviceToken(null);
  await assert.rejects(loadPowerSettings(), /locked/);
  setDeviceToken('a'.repeat(43));
  context.after(() => setDeviceToken(null));
  context.mock.method(globalThis, 'fetch', async (url, options) => {
    assert.equal(url, 'http://127.0.0.1:3196/settings/power/set');
    assert.deepEqual(JSON.parse(options.body), { token: 'a'.repeat(43), expected_revision: 0, display_off_seconds: 60, deep_sleep_seconds: 600 });
    return Response.json({ ...settings, revision: 1, display_off_seconds: 60, deep_sleep_seconds: 600 });
  });
  assert.equal((await savePowerSettings(settings, 60, 600)).revision, 1);
});

test('device keeps rename and separate honest screen/deep-sleep controls', () => {
  const source = readFileSync(new URL('../app/device/settings.tsx', import.meta.url), 'utf8');
  assert.match(source, /Change name/);
  assert.match(source, /PowerSettingsPanel/);
  const panel = readFileSync(new URL('../app/device/power-settings.tsx', import.meta.url), 'utf8');
  assert.match(panel, /Turn screen off after/);
  assert.match(panel, /does not turn deep sleep on/);
});
