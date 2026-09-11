# Unit A touch and streaming rollout — 2026-09-05

## Evidence

- Operator reported a long Preparing your answer screen. The latest completed
  state recorded STT 2396ms, SAGE lookup 16ms, model 26549ms, total 28961ms.
  The old path provided no first-token measurement. Recent service journal
  contained no entries before deployment; these timings came from state.json.
- Runtime: 65 tests and 3 subtests passed. Voice controls/stream wrapper: 40
  mocked tests passed. Camera: 22 tests passed. UI tests, lint and production
  build passed locally and on the staged ARM64 device release.
- Installed runtime adapter/orchestrator/CLI, voice controller and unit,
  status server, camera 15fps setting, and staged device UI. Model and private
  SAGE services were not restarted. The general-knowledge fix is preserved.
- All six application services active, /device HTTP 200, voice health reports
  touch_ready=true, status exposes touch readiness, and CORS preflight succeeds.
- Live control checks: hostile Origin rejected with 403; a second start while
  recording rejected with 409; explicit cancel and lost four-second lease
  both release ownership. No input WAVs remained afterwards. No captured
  audio was sent for inference by these checks.
- Dedicated kiosk refresh requested after deployment. Physical Talk/Send,
  PTT regression and visible progressive answer rendering remain operator
  acceptance checks, not established by endpoint or mocked tests.

## Camera and latency limits

Cold 720p trials with three-second idle gaps measured 5fps startup at
3102/3331ms and 15fps at 2753/2747ms; small samples, not an SLA. The selected
15fps setting keeps explicit on-demand capture and unchanged USB power policy.
Streaming improves access to early text, not necessarily total generation
time. Screen polling adds up to roughly 700ms plus request/render overhead.
Next measurement: repeated first_text, llm_first_token and total timings on
fixed short questions, including cold/warm runs. Resident speech recognition
remains a candidate requiring memory/quality validation, not an installed gain.

## Rollback and remaining gates

Runtime package and three installed scripts plus the voice unit are backed up
under `/opt/sage-lantern/before-talk-stream-20260905`. Previous UI is
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-talk-stream-20260905`.
To roll back in an idle maintenance window, stop voice/UI, restore those
matching runtime/scripts/unit/UI files, daemon-reload, then restart voice,
status, camera and UI. Do not restore only the old runtime CLI beneath the
new event-consuming wrapper. No model or private-SAGE rollback is needed.

Existing runtime_gate failure remains a separate provisioning acceptance
item. No reboot acceptance, local vision answers, durable voice-memory writes,
always-listening, or public network sharing is claimed by this release.
