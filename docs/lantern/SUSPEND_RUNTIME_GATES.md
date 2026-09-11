# Suspend Runtime Gates — Exact Integration Audit

Status: source-only review, 2026-09-05. Only this document is added. No runtime
edits, tests, service operations, device interrogation or suspend performed.
Line anchors describe the current working tree, not proof of installed behavior.
Parent owns root integration; Ptolemy owns the transition journal. This document
maps existing code to `suspend_transition.Gate`, not another sleep controller.

## 1. Immediate verdict

Current APIs cannot produce a complete `QUIESCE` acknowledgement. Auth outage,
`locked:true`, voice `idle`, camera health, and seven seconds of UI grace are
individually insufficient. Root must keep the fence and decline sleep until the
missing acknowledgements exist. Preserve the model process only if its admitted
request completion can be established; client cancellation alone does not prove
server-side inference ended.

## 2. Auth: real revocation, incomplete cleanup evidence

| Exact source | What it does | What root may acknowledge |
| --- | --- | --- |
| `scripts/lantern/unit-a-device-auth.py:196` | `AuthBroker.unlock` captures broker generation before setup status and holds `upstream_gate` across status and PIN verification. `_unlock` checks generation before/after device unlock. | Existing generation fencing covers this request path, not future admission after a bare shutdown call. |
| `scripts/lantern/unit-a-device-auth.py:238` | `setup_request` also holds `upstream_gate`; generation is checked before its response. | Waiting for all broker upstream work matters even if no PIN unlock is currently pending. |
| `scripts/lantern/unit-a-device-auth.py:295` | `shutdown()` increments broker generation and calls `DeviceLock.lock()` under `gate`, without waiting on upstream I/O. | Revocation primitive; not an upstream drain receipt or a persistent closing latch. |
| `software/runtime/src/sage_lantern/device_lock.py:434` | `lock()` increments device generation and clears active/pending/presence state. | Explicit token revocation independent of SQLite health. |
| `scripts/lantern/unit-a-device-auth.py:526` | `server_close()` revokes, shuts down tracked client sockets, then calls superclass close. Server workers are non-daemon at line 478. | Graceful worker drain is attempted; closing client sockets does not close each worker's separate upstream HTTP connection or certify its outcome. |
| `scripts/lantern/unit-a-device-auth.py:566` | SIGTERM/SIGINT set a stop flag; the main loop then runs server/device cleanup. | Graceful exit path exists. Stop timeout/forced kill cannot be upgraded to cleanup success. |
| `software/runtime/src/sage_lantern/device_lock.py:154` | Verifier finally revokes the captured cookie and checks auth rejection. Failure becomes `verification_failed`. | Successful verification includes cleanup, but shutdown has no aggregate positive cleanup receipt. Unknown login cookies remain an upstream uncertainty. |
| `software/runtime/src/sage_lantern/vault_setup.py:223` | `_operate` attempts revocation of collected cookies and raises on failed revoke. | Setup cleanup can fail independently of local token revocation; ordinary process exit is not proof all these operations cleaned up. |

Smallest auth hooks:

1. Add an idempotent transition-bound closing latch under `AuthBroker.gate` at
   `shutdown():295`, rejecting unlock/activate/setup admission while latched.
   Preserve current nonblocking revocation; do not hold this gate across I/O.
2. Account for all admitted upstream operations around `upstream_gate` acquisition
   and release at lines 199/209 and 243/277. Record completion AND cleanup outcome,
   not just lock availability. A sticky unknown/failed cleanup outcome must
   survive a worker finishing; an empty worker set alone cannot clear it.
3. Extend verifier/setup completion reporting at their finally blocks to distinguish
   positive cleanup from failure/unknown without exporting PINs or cookies. Emit
   the transition-bound drain receipt before graceful auth process exit. Journal
   unknown outcomes; do not turn an error handler into a success receipt.

Root can independently prove `AUTH_INACTIVE` through trusted service/job and
process-group/cgroup inspection after stop, with competing starts fenced. Existing
`GET /state` is not that proof. Keep persistent rate SQLite untouched; restart does
not reset the budget (`device_lock.py:352`).

## 3. Voice, recording and conversation writes

| Exact source | Existing behavior | Acknowledgement gap / surgical hook |
| --- | --- | --- |
| `scripts/lantern/unit-a-voice-loop.py:437` | `VoiceControl.revoke_device()` clears linked auth and marks current action cancel. | Useful first action, not cancellation completion. Add latched quiescing admission checked by `start():456`, `take()` and `begin_processing():587`. |
| `scripts/lantern/unit-a-voice-loop.py:532` | `command()` rejects processing at line 538; cancellation otherwise sets intent and returns `cancelled`. | Never treat this response as recorder/process exit. Root cannot use it as a universal cancel API. |
| `scripts/lantern/unit-a-voice-loop.py:750` | Every POST, including `/cancel`, requires current device auth when enabled. | After stopping auth, browser cancellation is intentionally denied. Add a narrow trusted local quiesce control path, not an unauthenticated exception to the browser route. |
| `scripts/lantern/unit-a-voice-loop.py:938` | Capture repeatedly checks access; finally calls `stop_recording()`. | Stop escalation at line 912 can wait 5+3+3 seconds. Need tracked recorder handle and explicit reaped/no-capture acknowledgement; seven seconds cannot prove it. |
| `scripts/lantern/unit-a-voice-loop.py:1089` | Runtime child starts a new process session; loop checks access at line 1100 and bounded selector intervals. Failure calls `stop_pipeline()` at line 1150. | Track this process group and completion separately from parent loop state. SIGTERM/SIGKILL and leader wait at line 1173 do not themselves expose a trusted all-descendants-drained receipt. |
| `scripts/lantern/unit-a-voice-loop.py:1213` | DeviceLocked handler writes redacted `idle` before final cleanup. Cleanup unlinks audio, finishes photo/owner state, and clears session at lines 1217–1224. | `idle` is explicitly not a drained marker. Signal quiescence only after these finalizers and child checks complete. |
| `scripts/lantern/unit-a-voice-loop.py:875` | Auth failure redacts state and forces `phase=idle`. | Polling state JSON cannot distinguish “revoked but draining” from fully idle. |
| `software/runtime/src/sage_lantern/conversation_service.py:130` | `stop(timeout)` fences new work, joins worker and waits for admitted foreground work; returns False on timeout. `close(timeout):154` closes only after successful drain. | This IS a reusable in-process drain result. Convert exact True to the transition receipt only after other gates pass. Preserve receipts/outbox if False; never close or reset the journal to force completion. |
| `scripts/lantern/unit-a-voice-loop.py:1249` | `run()` starts the HTTP thread and memory worker. Finally shuts server down and calls `memory.stop()` without a timeout at line 1279. | No SIGTERM handler is installed by `main():1282`; do not assume a service stop executes Python finally. Add explicit graceful-stop signaling, bounded memory drain and an acknowledgement before exit. |

Minimal voice integration: one `quiesce(transition_id, deadline)` internal method
sets admission latch, calls `revoke_device`, interrupts/waits for tracked capture
and pipeline work, then waits for session-finalizer completion and
`memory.stop(remaining_budget)`. Track/drain existing HTTP requests too:
`VoiceHTTPServer:651` uses daemon request threads with no public active-request
drain API. A history mutation admitted before revocation must finish/reconcile;
denying the next request does not finish the old one.

Stopping the voice service is an alternative only after adding the graceful
drain receipt. Process death may prove hardware handles closed but cannot prove
an in-flight private SAGE write's outcome; preserve it as unknown/reconcile-only.
Quiescence means no active work, not that every queued write was dispatched.

## 4. Camera: response denial differs from sensor stop

- `scripts/lantern/unit-a-camera-server.py:93`: `capture()` invokes blocking
  `subprocess.run(v4l2-ctl, timeout=15)` at line 105. There is no cancellation or
  auth recheck inside that operation. Handler auth checks before capture and
  before image response do not stop acquisition immediately after auth shutdown.
- `preview():133` starts a continuous capture subprocess. Its finally at line 168
  terminates/reaps the process and releases the camera lock. This is the correct
  completion hook, not the last successful frame or HTTP status.
- `PreviewStream.frame():43` may wait up to ten seconds for first frame or five
  seconds later. Handler checks auth around frames at lines 331/345, not within
  that blocking wait. Disconnect/lock detection is not instantaneous.
- `CameraServer:188` uses daemon request threads; `main():371` directly runs
  `serve_forever()`, without explicit signal/drain handling. `/health:303` reports
  device presence, not inactivity. Neither API acknowledges quiescence.

Smallest hook: track capture/preview child handles and active requests on Camera;
add a quiescing flag checked before process spawn, cancellation wakeup for frame
waits, and a bounded join/reap method that acknowledges only with no active child,
no streaming response and the camera lock released. A root-verified camera service
stop with all children gone can replace a live camera-drain API, but cannot claim
the existing Python cleanup ran. Keep admission closed until resume handling.

## 5. UI, display and playback evidence

- `apps/lantern-fleet-admin/app/device/access.tsx:46`: `lock()` increments UI
  generation, clears token/profile and switches away from the unlocked view.
  Polling at line 72 detects auth failure, but depends on a live event loop and
  outstanding request completion. No root-facing cleared acknowledgement exists.
- `access.tsx:207`: unlocked DeviceScreen is conditional; its removal triggers
  child cleanup. `screen.tsx:66` aborts status polling; `camera.tsx:102` aborts
  preview, drops original frame and zeros canvas width on unmount. These are
  useful hooks for a logical UI-cleared acknowledgement, not proof of physical
  panel state or erasure of all browser memory.
- `talk.tsx:45`: abandon requests `/cancel` and ignores failure. Once auth is
  stopped it may be denied, and processing cancellation already returns 409.
  Never include this best-effort request as server cancellation evidence.
- `scripts/lantern/lantern-display-power.py:211`: display policy samples auth
  state. Unknown state invokes wake rather than proving a private view cleared.
  It has no root transition acknowledgement and must not race the coordinator's
  preparation display policy.
- `software/runtime/src/sage_lantern/orchestrator.py:108` and
  `adapters.py:285`: SPEAKING currently means Piper WAV synthesis. No playback
  invocation was found in the reviewed runtime, Lantern scripts or device UI.
  Status text describing capture/playback hardware is not an active-playback
  inventory. Any native launcher/player outside this source scope remains unknown.

Smallest UI hook: a trusted, transition-bound clear-only notification channel
that remains available while auth is stopped. Acknowledge after generation
invalidation, private view removal, request aborts and canvas/attachment cleanup;
report pending local work rather than immediately acknowledging the notification.
The root controller must bound this wait. If the UI is hung, use a separately
verified kiosk termination/restart path or abort sleep; never synthesize an ACK
after a delay. This channel must not authorize unlocking or private reads.

## 6. Process/service lifecycle limits

- `software/systemd/sage-lantern-voice-loop.service:3` requires LLM and personal
  SAGE; it has no auth dependency or suspend admission condition. Auth stopping
  therefore does not constitute a voice process stop. Do not assume dependency
  ordering implements quiescence.
- `software/systemd/sage-lantern-camera.service:8` runs separately as DynamicUser;
  `sage-lantern-ptt.service:10` runs a separate GPIO monitor. PTT monitoring can
  remain if its consumer is fenced, but wake must not replay a held/rising input
  into a fresh session. Voice's edge state at `unit-a-voice-loop.py:1242` needs a
  resume baseline and fresh authenticated link.
- `software/systemd/sage-lantern-runtime.service:11` is a oneshot readiness check,
  not the running CLI pipeline supervisor. Its active state is not work evidence.
- `software/systemd/sage-lantern-llm.service:11` hosts a persistent HTTP server.
  Killing a CLI process group closes the client, not this server process. No
  correlated server-side cancellation/idle ACK was established by this review.
  To preserve model PID, add/verify a supported request-completion observation;
  do not claim inference stopped solely because the client disappeared.

Inspect effective installed units/drop-ins and worker groups in a later authorized
native check. Source unit names, MainPID exit and cached status alone do not prove
all descendants stopped or prevent dependency-triggered restarts. This audit
does not require stopping/restarting personal SAGE or deleting any queue/state.

## 7. Exact root-adapter gate mapping

| Coordinator gate | Admissible evidence / current status |
| --- | --- |
| RETAIN_FENCE | Ptolemy/parent's root journal and serialized admission latch; not browser auth status. Must cover starts and existing service admission. |
| STOP_AUTH / AUTH_INACTIVE | Graceful shutdown plus trusted no-job/no-worker observation. Existing lifecycle assists; no HTTP endpoint provides this ACK. |
| QUIESCE | Conjunction of auth upstream-cleanup receipt, voice child/session/HTTP drain, `ConversationService.stop(timeout) is True`, camera drain or verified process stop, UI clear, and actual output/inference inventory. Currently missing aggregate producer. |
| IDLE_ELIGIBLE | Fresh exact locked observation captured before auth stop plus new X11 idle/input-generation evidence while preparing. Do not require `/state` to respond after intentionally stopping auth, or reinterpret unavailable as locked. |
| SUBMIT_SLEEP | Supported OS job admission receipt only; never implies resume. No runtime API reviewed here produces it. |
| VERIFY_RESUMED / VERIFY_ABORTED | Trusted correlated OS/job observation; cannot derive from UI grace, endpoint denial or service restart. |
| START_AUTH_LOCKED | Fresh broker health with locked state using unchanged persistent rate DB, after verified fence release; reinitialize UI/PTT links without restoring old tokens. |

For every ACK, bind transition ID, service instance/epoch and completed operation;
reject stale acknowledgements from a previous service run. Record bounded status
codes/counts only, never PINs, cookies, transcripts or photo bytes. QUIESCE timeout
leaves the root fence retained and transition recovery-required, not successful.

Required owner tests: stop during verifier/setup finally; upstream revoke failure;
cancel during capture/processing; child exits before grandchild; camera first-frame
stall; admitted history write during lock; memory drain timeout retaining receipt;
UI event-loop stall; stale UI/worker ACK; model still computing after client death;
held PTT across resume; and competing service restart. None was executed here.
