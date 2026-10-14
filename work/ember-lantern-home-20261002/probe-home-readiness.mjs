/**
 * Re-run the reviewer's five Home readiness cases against the current helper.
 *
 * These are the exact cases from
 * `work/pocket-ui-start-review-20261002/home-helper-probe-results.json`, where
 * four of five failed. The output shape matches that file so the two can be
 * diffed directly, with one addition: the `activation` the control now reports,
 * which is what routes a finished turn through New chat preparation.
 *
 *   node probe-home-readiness.mjs
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { homeStatus, voiceControlState } from '../../apps/lantern-fleet-admin/lib/home-navigation.ts';

const here = dirname(fileURLToPath(import.meta.url));

function base(overrides = {}) {
  return {
    schema: 'sage.lantern.local-status.v1',
    observed_at: new Date().toISOString(),
    local_voice_ready: true,
    audio: { present: true, detail: 'Synthetic probe microphone', ready: true, capture_verified: false, ptt_ready: true },
    radio: { present: true, detail: 'Synthetic probe radio', name: 'MeshCore USB' },
    voice: { phase: 'idle', transcript: 'Probe question', response: 'Probe answer', touch_ready: true },
    services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
    ...overrides,
  };
}

const stale = base();
stale.observed_at = new Date(Date.now() - 60_000).toISOString();

const physicalOnly = base();
physicalOnly.voice.touch_ready = false;

const complete = base();
complete.voice.phase = 'complete';
complete.voice.session_id = 'probe-session';

const error = base();
error.voice.phase = 'error';
error.voice.detail = 'llm_timeout';

const cases = [
  { name: 'fresh_touch_idle', status: base(), expected_canStart: true },
  { name: 'stale_touch_idle', status: stale, expected_canStart: false },
  { name: 'physical_ptt_without_touch', status: physicalOnly, expected_canStart: false },
  { name: 'complete_new_chat', status: complete, expected_canStart: true },
  { name: 'error_retry_new_chat', status: error, expected_canStart: true },
];

const results = cases.map((entry) => {
  const actual = voiceControlState(entry.status, true, false);
  const strip = homeStatus(entry.status, true);
  return {
    name: entry.name,
    expected_canStart: entry.expected_canStart,
    actual,
    strip,
    matches: actual.canStart === entry.expected_canStart,
  };
});

writeFileSync(join(here, 'readiness-probe-results.json'), `${JSON.stringify(results, null, 2)}\n`);

let failures = 0;
for (const result of results) {
  if (!result.matches) failures += 1;
  const mark = result.matches ? 'PASS' : 'FAIL';
  const reason = result.actual.blockedReason ? ` — ${result.actual.blockedReason}` : '';
  console.log(`${mark} ${result.name}: canStart=${result.actual.canStart} activation=${result.actual.activation} strip="${result.strip.label}"${reason}`);
}
console.log(`\n${results.length - failures}/${results.length} cases match the reviewer's expectations.`);
if (failures) process.exitCode = 1;
