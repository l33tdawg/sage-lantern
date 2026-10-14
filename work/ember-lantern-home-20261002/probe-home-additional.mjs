/**
 * Re-run the reviewer's four additional Home probes — the ones that failed.
 *
 * These are the exact cases recorded in
 * `work/pocket-ui-start-review-20261002/home-correction-additional-probes.json`,
 * where all four came back wrong:
 *
 *   completed_answer_microphone_removed   permitted capture with no microphone
 *   error_audio_unready                   permitted capture with audio unready
 *   physical_recording_has_no_gui_stop    advertised a GUI stop for it anyway
 *   owned_touch_recording_global_busy     hid Stop/Cancel behind the busy flag
 *
 * Two of them needed an input the helper did not have: ownership. `captureOwnership`
 * is now explicit, and `releaseAction` takes the readiness the release is judged
 * against, so the fifth case Rook named — a release after readiness was lost — is
 * covered here too.
 *
 *   node probe-home-additional.mjs
 */
import { writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import { NO_CAPTURE_OWNER, releaseAction, voiceControlState } from '../../apps/lantern-fleet-admin/lib/home-navigation.ts';

const here = dirname(fileURLToPath(import.meta.url));
const SESSION = '55555555-5555-4555-8555-555555555555';
const OWNED = { owned: true, sessionId: SESSION };

function base() {
  return {
    schema: 'sage.lantern.local-status.v1',
    observed_at: new Date().toISOString(),
    local_voice_ready: true,
    audio: { present: true, detail: 'Synthetic probe microphone', ready: true, capture_verified: false, ptt_ready: true },
    radio: { present: true, detail: 'Synthetic probe radio', name: 'MeshCore USB' },
    voice: { phase: 'idle', transcript: 'Probe question', response: 'Probe answer', touch_ready: true },
    services: { voice_loop: 'active', ptt: 'active', llm: 'active', personal_sage: 'active' },
  };
}

const removedMic = base();
removedMic.voice.phase = 'complete';
removedMic.voice.session_id = SESSION;
removedMic.audio.present = false;
removedMic.local_voice_ready = false;

const unreadyAudio = base();
unreadyAudio.voice.phase = 'error';
unreadyAudio.voice.detail = 'llm_timeout';
unreadyAudio.voice.session_id = SESSION;
unreadyAudio.audio.ready = false;

const physicalRecording = base();
physicalRecording.voice.phase = 'listening';
physicalRecording.voice.session_id = SESSION;
physicalRecording.voice.input_source = 'physical';

const ownedRecording = base();
ownedRecording.voice.phase = 'listening';
ownedRecording.voice.session_id = SESSION;
ownedRecording.voice.input_source = 'touch';
ownedRecording.voice.review_ready = true;

const cases = [
  {
    name: 'completed_answer_microphone_removed',
    run: () => voiceControlState(removedMic, true, false, Date.now(), NO_CAPTURE_OWNER),
    expect: (state) => state.canStart === false && /Microphone/.test(state.blockedReason),
    expects: 'canStart=false with the microphone named',
  },
  {
    name: 'error_audio_unready',
    run: () => voiceControlState(unreadyAudio, true, false, Date.now(), NO_CAPTURE_OWNER),
    expect: (state) => state.canStart === false && /Audio/.test(state.blockedReason),
    expects: 'canStart=false with the audio state named',
  },
  {
    name: 'physical_recording_has_no_gui_stop',
    run: () => voiceControlState(physicalRecording, true, false, Date.now(), NO_CAPTURE_OWNER),
    expect: (state) => state.phase === 'busy' && !/Done/.test(state.instruction),
    expects: 'phase=busy, no stop affordance',
  },
  {
    name: 'owned_touch_recording_global_busy',
    run: () => voiceControlState(ownedRecording, true, true, Date.now(), OWNED),
    expect: (state) => state.phase === 'recording' && /Done|Cancel/.test(state.instruction),
    expects: 'phase=recording with Stop/Cancel while busy is true',
  },
  {
    name: 'release_after_readiness_lost',
    run: () => ({ action: releaseAction('tap', 'idle', false, false) }),
    expect: (state) => state.action === 'none',
    expects: "releaseAction=none — an unavailable control is phase idle, so canStart is re-read",
  },
];

const results = cases.map((entry) => {
  const actual = entry.run();
  return { name: entry.name, expects: entry.expects, actual, matches: entry.expect(actual) };
});

writeFileSync(join(here, 'additional-probe-results.json'), `${JSON.stringify(results, null, 2)}\n`);

let failures = 0;
for (const result of results) {
  if (!result.matches) failures += 1;
  console.log(`${result.matches ? 'PASS' : 'FAIL'} ${result.name}: ${JSON.stringify(result.actual)}`);
}
console.log(`\n${results.length - failures}/${results.length} previously failing probes now hold.`);
if (failures) process.exitCode = 1;
