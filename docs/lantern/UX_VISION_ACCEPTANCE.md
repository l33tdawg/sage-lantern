# Unit A — UX and photo-answer release

**Date:** 2026-09-05. **State:** deployed for operator testing, not full Unit A acceptance.

## User flow

### Real progress and photo grounding — deployed 2026-09-05

The existing processing heading now follows flushed runtime progress events:
transcribing → recalling → photo analysis or text generation → first visible
answer → optional audio synthesis. Controller transitions and status projection
are allowlisted. Whitespace-only deltas do not falsely start answering; old
delta/result-only runtimes remain accepted. No timer-driven stages, percentages,
separate OCR claim or new explanatory paragraphs. Photo analysis includes the
LLM request until visible text: finer image-encoder/prefill boundaries are not
yet exposed. Fast stages may occur between UI polls.

The last inspected reply made a group-membership claim not yet checked against
the original photo and incorrectly described Last Stage of Delirium as artists/thinkers. Its security
research identity is documented by [Security Explorations](https://security-explorations.com/materials/se-nutshell.pdf).
The photo-specific prompt now separates readable text from pretrained background,
forbids guessing names or deriving affiliations from a list, and asks for closer
photos when uncertain. This is a prompt safeguard, **not verified elimination of
hallucinations or OCR-quality acceptance**. Existing stored answers are unchanged.
Last query timing was 50.250s total, 2.343s STT, 0.013s recall and 36.863s LLM
first-token latency; no speed improvement is claimed by this release.

Local 343 Python tests/246 subtests and 46 UI tests passed; native 89 voice tests,
16 status tests, 46 UI tests/lint/build and an installed-runtime synthetic progress
smoke passed. UI 200, five services active, model not restarted, kiosk refreshed.
Original-photo export for comparison was blocked by safety review and awaits
explicit approval; no photo was copied to the development computer. User's saved
history remains unchanged. Code backup is root-private
`/opt/sage-lantern/before-progress-grounding-20260905`; paired UI backup is
`lantern-fleet-admin.before-progress-grounding-20260905` beside live app.

### Menu motion — deployed 2026-09-05

Operator requested a downward opening and upward closing menu, not sideways
motion. The panel now stays mounted with a vertical transition; its stationary
backdrop clips overflow. Closed content is inert and hidden from accessibility
navigation. Close-button focus uses `preventScroll` so focusing the animated
panel cannot scroll the underlying viewport. Reduced motion disables transitions.
Local/native 45 UI tests, lint and build passed; UI 200, voice idle/photo-ready,
kiosk refreshed. Physical motion acceptance remains with operator. UI-only
backup: `lantern-fleet-admin.before-menu-vertical-20260905` beside live app.

### Operator-directed decluttering — deployed 2026-09-05

Past questions reserves a separate title column 28px from Back. The main attached
photo is vertically centered with bounded scrolling. At the operator's request,
removed the camera-side explanatory paragraph and main-screen saving paragraph;
privacy/retention explanations remain in Settings and About. No retention or
inference behavior changed. Local/native 44 UI tests, lint/build passed; UI 200
and voice idle/photo-ready after UI-only deployment and kiosk refresh. Backup:
`lantern-fleet-admin.before-clean-ui-20260905` beside the live app.

Latest requested photo log: 48.006s total, 2.947s STT, 0.027s SAGE context,
38.825s LLM first token, 45.032s LLM total. Server reports 38.741s prompt/image
processing and 6.208s generation (32 tokens); image-batch processing interval
was approximately 25.65s. **Not a clean benchmark:** the native UI build overlapped
the request until 15:49:43, while inference ran 15:49:28–15:50:13. Temperature
afterwards was about 50.8°C, not evidence of request-time throttling. Repeat
without builds before choosing performance changes. No speed improvement claimed.

### Original-photo history — deployed 2026-09-05

Completed photo answers now retain the original captured JPEG beside their text
when question and photo saving are enabled. Past questions marks photo entries
and loads the associated image on demand; missing/corrupt media reports an error
without hiding the answer. Inference still uses the existing 256×192 derivative.
The browser preserves camera JPEG bytes rather than re-encoding the original.

Settings separates question saving and attached-photo saving. New stores default
both on; v1 migration preserves the existing question preference and records.
Opt-out pauses future saving, not deletion. Preference changes, clear and delete
invalidate in-flight consent. Main/camera screens disclose shared-device access.
The original upload is gated by verified backend capability and current saving
preferences, preserving inference-only compatibility with older controllers.

History v2 commits media before metadata and durable metadata before pruning.
Startup/access/maintenance removes recognized unreferenced media and pending
files; uncertain metadata writes never trigger immediate media deletion. Bounds
remain 100 answers/30 days/4 MiB text, 2 MiB per original and a 512 MiB free-space
floor. These are private local files, not SAGE memory or public publication.
JPEG validation is bounded header validation, not a full decoder. No encryption,
secure flash erase or gallery is implied. Storage failures show a visible warning.

Validation: 334 local Python tests + 213 subtests; 42 UI tests, lint/build;
native 82 voice/history tests + 26 media tests, plus isolated HTTP round-trip,
reopen, opt-out and deletion test. Native UI tests/lint/build passed. Actual
migration preserved all five existing questions and enabled preference; photo
saving is enabled. UI HTTP 200, voice idle/photo-ready and five runtime services
active; dedicated kiosk refreshed. No new real camera answer or physical power
cut was used for acceptance. Operator should take a fresh photo question, reopen
it in Past questions and confirm the image, then exercise deletion/settings.

Backups: UI `lantern-fleet-admin.before-photo-history-20260905` beside the live
app; root-private `/opt/sage-lantern/before-photo-history-20260905` contains the
previous voice script and pre-migration history. **Do not roll back to a v1-only
backend or restore that history snapshot after new activity:** preserve current
v2 data and use a forward-compatible repair. The installer now includes the
media module and uses the existing runtime virtual environment.

Earlier dated increments below are historical where they conflict with this release.

### Inner-screen polish — deployed 2026-09-05

Read-only subagent UX review informed consistent left Back navigation, matching
Settings/history/status icons, readable privacy cards and an explicit On/Off
saving switch. Past questions uses finger scrolling without the redundant arrow
footer; saved question and answer have separate labelled cards. About separates
privacy and answer limitations from expandable technical details. Status tables
and measured-stat cards share the new visual treatment; unknown states remain.

Local and native Unit A checks: 40 tests, lint and build passed. UI-only release;
no saving preference, voice/model service or retention behavior changed. Physical
visual/touch acceptance remains with the operator. Rollback copy:
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-inner-ui-20260905`.

### Settings follow-up — deployed 2026-09-05

Menu → Settings now contains the real text-history saving control. Past
questions shows a status indicator, browsing and explicit deletion only.
Turning saving off preserves existing items under the existing retention
limits and invalidates in-flight consent. Clearing/deleting remains separate.
The UI requires the backend `saving_off_keeps_items: true` capability before
offering a toggle, avoiding destructive behavior with an older backend.
An uncertain response requires fresh read-back before retrying.

Local/native UI: 39 tests, lint and build passed. Backend history/control/photo
tests: 56 passed with 28 subtests. Live capability read-back passed; UI returned
HTTP 200, kiosk refresh requested, UI/status/voice/model/runtime active and
voice idle/photo-ready. No user preference was changed and no real history
toggle/delete test was performed. Physical Settings acceptance remains pending.
UI backup: `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-settings-20260905`.
Backend backup: `/opt/sage-lantern/before-settings-20260905/voice-loop`.

**Still pending:** default-on notice/migration, original-photo upload/history
association and media browsing. The deployed history remains opt-in, text-only,
100 items/30 days. The new standalone `history_media.py` is not deployed or wired
to capture. It preserves captured JPEG bytes with bounded header checks, digest,
atomic storage, private paths, reserve floor and bounded locking; it is not a
full JPEG decoder, consent service or complete media-retention transaction.

**Deployed follow-up:** a bottom-left **Past questions** shortcut now
opens the existing consent-controlled history screen; its menu label and page
title match. It hides during recording/processing to preserve voice ownership.
Camera remains immediately before Ask. The answer footer wraps to two columns
on narrower screens rather than squeezing four controls into one row. Local
and native Unit A 37 UI tests, lint and build pass. The operator authorized
deployment after finishing tests; the UI returned HTTP 200 and the dedicated
kiosk refresh was requested. UI/status/voice/model/runtime services are active,
with voice idle and photo-ready. Physical acceptance of this new shortcut
remains pending. The operator reports the preceding release's features/fixes
look good; that feedback does not close unrelated provisioning/network gates.
Rollback UI: `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-past-questions-20260905`.
Only the UI changed; settings drafts remain offline and unwired.

- Main answer: My question reviews the recognized speech; Ask again records a
  replacement question. Back to answer restores the answer. No keyboard required.
- Clear returns to the default Ask view without deleting saved history. It is
  disabled while busy; the controller reserves ownership while clearing.
- Camera stays beside Ask. Take photo freezes the preview; Retake replaces it.
  Ask about photo attaches the frame and starts the microphone immediately;
  speak, then Send. Back cancels the recording and discards the photo. Remove
  discards an unsubmitted attachment after a failed start. The physical PTT does not attach a pending
  browser photo: use the explicit touchscreen photo flow.
- Menu contains History, Device status and About. Status uses aligned rows,
  measured storage usage and a temperature trend sampled while open. Missing
  battery, LoRa link/RSSI/SNR and synchronization data stay explicitly unmeasured.
- About identifies the interface release and privacy limits, without pretending
  its static text proves the currently loaded model version.
- The temporary photo thumbnail stays beside recording, question review and the
  answer. It is associated with that exact voice session, not subsequent questions.
  My question → Ask again reuses that photo; ordinary Ask starts a new question.
  Clear removes the current photo and text without deleting saved history.
- The device touchscreen hides the mouse cursor. External admin styles are unchanged.

## Boundaries

One explicitly captured frame is held in browser memory for the current interaction. The frontend makes
an aspect-preserving derivative within 256×192; the backend independently limits
JPEG bytes and dimensions. Accepted attachments use a 0700 directory and 0600
temporary files under `/run/sage-lantern-voice/photos`. They are not saved in
history or written to SAGE. SAGE recall receives the spoken question only.
The controller removes the attachment immediately after the runtime exits,
before history disk I/O, while retaining session ownership. Runtime stderr is
discarded rather than accumulated in an unbounded temporary file; UI errors
remain generic. These hardening changes are deployed and regression-tested.
The browser keeps a separate full-frame JPEG preview (quality 0.92, bounded to
2 MiB of JPEG data) through the answer. Its larger layout uses up to 224 CSS
pixels of width without changing the inference derivative. Only the small
inference image is submitted to the backend; the display preview never enters
the voice request. The browser drops both on Clear,
recording cancellation, a new unrelated session, page hiding or reload. It is
not added to history, local storage or SAGE. Backend temporary-file cleanup and
browser preview lifetime are deliberately different.

The optional single-server projector installation is documented in
[LOCAL_VISION_SPIKE.md](LOCAL_VISION_SPIKE.md). Failed/truncated image inference
does not silently retry as text-only. Photo responses are capped at 128 output
tokens. Small derivatives and a successful color test do not establish useful
OCR, object identification, plant safety or repair guidance.

Cancel applies during recording. Once Send starts processing, the existing
contract runs to completion or timeout; closing the screen does not cancel it.
An already-consented history may retain the final text answer. Runtime HTTP
timeout is 120 seconds; the outer processing deadline is 240 seconds. The
300-second limit used in the isolated model probe is not the application timeout.

## Verified checks

### Later September 5 status/performance release

Native Unit A UI: 35 tests, lint and production build passed. Local runtime
plus status suites: 126 tests and 44 subtests passed, including the 14-test
settings projection foundation. Settings is not wired to a user panel or
mutation API. The final UI returned HTTP 200, the dedicated kiosk was
refreshed, and UI/status/voice/model/runtime-gate services were active with
voice idle and photo-ready. No browser visual QA or physical acceptance claimed.

SAGE status now reads bounded cached local aggregates, not a fresh SAGE query
on every UI poll. Live verification reported 0 committed/total memories,
block 4487, 0 chain peers, 1 observed validator, ready and idle. Roster
completeness and validator connections remain unknown. These measurements
are a snapshot, not permanent fixture values. LoRa neighbors/signal remain
unmeasured; user mail is not configured and federation is not verified.
See [measured performance](UNIT_A_PERFORMANCE_20260905.md) for the retained
prompt/Whisper improvements, failed alternatives and remaining quality limits.

- UI: initial release 27 contract tests; photo-preview/direct-start follow-up
  expands this to 32, including session separation and cursor/flow contracts.
  Lint and production build passed locally and natively
  on Unit A. These are not browser or touchscreen visual acceptance.
- Runtime: 96 tests plus 3 subtests; controller: 74 tests after final cleanup
  hardening; optional installer: 14 tests; readiness smoke script: 11 tests.
- Real synthetic spoken Matrix question traversed STT, signed SAGE recall and
  the local model three times, returning general-knowledge answers rather than
  refusing for missing local context. No conversation was written to SAGE.
- Real synthetic spoken photo question plus a red 256×192 JPEG returned
  “The photo is filled with red.” Synthetic audio/image fixtures were removed;
  the live microphone was not used for those CLI inference runs.
- Live photo control: Start 201, one 0600 attachment; Cancel 200, no attachment
  remaining. The short cancelled capture was not submitted to inference.
- Live Clear 202: idle, empty current transcript/response, saved-history items
  and consent unchanged. No saved conversation contents were printed.
- Repaired runtime gate: **18 passed, 0 failed**, active. It previously checked
  model/SAGE endpoints before they finished starting. Bounded readiness retry
  now waits up to 60 seconds; file/config failures are still immediate.
- `/device` returns HTTP 200. UI, voice, camera, status and model listen on
  loopback only. Dedicated kiosk refresh requested, not a visual QA claim.

## Timing evidence

Same Piper-generated Matrix WAV, real local recall, installed multimodal server.
The first run follows model activation; later runs reuse warm service/file state.
No cache drop or network disconnection was performed. These are three samples,
not a latency distribution, p95, controlled before/after improvement or proof
that a known personal memory was retrieved.

| Run | STT | SAGE | LLM first token | Processing first text | Stage sum | CLI wall |
|---|---:|---:|---:|---:|---:|---:|
| Matrix 1 | 2.495s | 0.027s | 12.847s | 15.369s | 22.558s | 23.129s |
| Matrix 2 | 2.548s | 0.011s | 2.749s | 5.308s | 14.499s | 15.105s |
| Matrix 3 | 2.434s | 0.011s | 2.727s | 5.172s | 16.408s | 17.024s |
| Red photo | 2.468s | 0.015s | 45.402s | 47.885s | 49.306s | 49.863s |

First text here is the runtime callback, **not first grouped UI batch or screen
paint**. CLI wall excludes synthesis/recording. Different output lengths affect
totals. Prompt guidance now requests one to three short sentences by default;
this change has no isolated speedup measurement. Grouping is presentation only.

The photo server log separates about 14.6 seconds of initial text prefill from
approximately 26.8 seconds in the image-processing interval, followed by further
prefill. Overall prompt evaluation: 45.331 seconds for 315 tokens; generation:
1.421 seconds for eight tokens. Photo preprocessing/prefill, not recall, is the
dominant observed wait. Reducing detail further may hurt usefulness; no blanket
quality-preserving optimization is established.

After these checks: MemAvailable 3,523 MB, system swap usage 255 MB, model cgroup
MemoryCurrent 3,959,144,448 bytes, NRestarts=0. These are snapshots, not peaks.
Resident STT remains deferred: its roughly 2.5-second stage is smaller than the
cold prompt/image cost, and another resident worker needs a measured RAM budget.

Camera recheck used the actual loopback preview endpoint, one frame per explicit
request, with no image saved: first complete JPEG at 2798/944/2856ms after
3/0.5/3s idle. Headers arrived at 2797/944/2856ms. This excludes browser decode
and paint and does not directly measure USB runtime power state. Existing
720p/15fps and USB power policy are unchanged; no idle capture was introduced.

## Recovery and remaining acceptance

Backend code backup: `/opt/sage-lantern/before-ux-vision-20260905`.
The later status/runtime adapter backup is
`/opt/sage-lantern/before-status-perf-20260905`; latest UI predecessor is
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-sage-status-20260905`.
The preview-only predecessor is
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-sharp-preview-20260905`.
UI backup: `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-ux-vision-20260905`.
The later direct-recording/thumbnail/no-cursor UI release has its immediate
predecessor at `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-photo-direct-20260905`.
Optional model/voice override backup:
`/var/lib/sage-lantern/vision-dropins-backup-pz0u95vd`.
Use the optional installer's `--disable` to remove only its overrides and return
to the underlying text configuration. Restore compatible UI/controller/runtime
code together if rolling back this release; preserve history and identity data.
Revalidate the photo-ready assertion after any model/projector/binary change.

Before pivoting to Unit B acceptance, the operator still needs to check:

1. Camera → Take photo → Ask about photo → speak → Send with a harmless,
   recognizable object; thumbnail beside the answer, Back/Retake/Remove and
   ordinary no-photo physical PTT. Direct start is one-shot, not retried by polling;
   a failed/busy start offers an explicit Ask retry rather than starting later.
2. My question → Ask again; Clear; readable status/About; history opt-in,
   save/reopen/delete/disable and touchscreen scrolling.
3. Five controlled offline known-memory loops, actual first visible batch,
   cold reboot and USB/audio/PTT reorder/reconnect/fault recovery. The successful
   warm readiness-gate restart is not a cold-boot or sterile-provisioning test.

The two existing SAGE master tasks remain open. No Unit B, LoRa exchange,
public contribution or funding-readiness acceptance is implied by this release.
