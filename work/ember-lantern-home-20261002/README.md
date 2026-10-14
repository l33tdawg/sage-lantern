# Ember — Home 2a correction and disconnected fixture

2 October 2026. Response to the review in `work/pocket-ui-start-review-20261002/`
(`HOME_FIRST_REVIEW.md`, `LEAD_REVIEW.md`, `home-helper-probe-results.json`) and to the
owner's direct clarification that the UI is **portrait**.

The task is `c423d913-de77-4327-a98c-2e179f238f20`.

> **Correction — 2 October 2026 07:51 UTC.** The Settings review (`SETTINGS-REVIEW.md`)
> found this file's hash list and gate numbers stale, and Rook directed a dated correction
> rather than an edit to the historical record. The current hashes, gates and run artifacts
> are in **"Correction — Settings origin, confirmation focus, and this record"** at the end of
> this file; the closure for the two product findings is `SETTINGS-CORRECTION.md`. Sections
> above that heading are the historical revision and are left as they were written: where
> their numbers disagree with the correction section, the correction section is current. The
> pre-correction bytes are preserved, with a SHA-256 manifest, in
> `frozen-20261002T0735Z-pre-correction/`.

## Read this first

- **Portrait is the canonical screen.** 720x1560 native, then 480x1040 and narrow portrait
  reflow. Nothing here is a landscape edition, and the desktop preview frames a portrait
  device rather than filling a wide window.
- All review findings are corrected, each with a regression test and — where it is
  behavioural — a browser scenario driven through the real component with trusted input.
- **The bounded integration into `app/device/screen.tsx` is NOT started.** Home is still an
  unrouted component with no entry in the app's page state machine, so `homeEnabled` does not
  exist yet and no release default changed. Nothing here is integrated, accepted, released or
  device-qualified. The capture owner, the New chat reset policy and the Chat destination are
  integration decisions, and inventing a second capture owner is the bug class the brief
  forbids — so that step belongs to a session that can read the existing owner first.

## Exact files and hashes

| File | Previously reviewed | Now |
| --- | --- | --- |
| `apps/lantern-fleet-admin/app/device/home.tsx` | `6d833bfa814fe838…` | `fc073b8f3ddbfb7b42348deca6aed4003da7fb41e0b94c068b01d904809b2c4c` |
| `apps/lantern-fleet-admin/app/device/home.css` | `cad3b5b632af515b…` | `060ce82a38e8bd6db9cd6b5f1f044de212bee5524f80d9bbc4d7c6cb9d863f16` |
| `apps/lantern-fleet-admin/lib/home-navigation.ts` | `774d552942f018b2…` | `0e86d34491387633bb0d9cdd631457d971626ee4bca99fbd6dce69a7b9373956` |
| `apps/lantern-fleet-admin/lib/home-navigation.test.mjs` | `04fb04864a3d2ddc…` | `4d819485d58e5b7b123dcb1720293cb143289a653063298ede2a7471dbb65b95` |

No other file was edited. `lib/device-state.ts` was read, not modified. The fixture records its
bundle inputs in `build-metafile.json`, so the browser artefact ties back to these hashes.

## The interaction findings

| Review finding | Correction |
| --- | --- |
| Pointer tap on the recording control produced no callback | A press is now accepted while recording. That is the second tap that finishes capture; gating it on `canStart` made the stop affordance unreachable, because `canStart` is false *because* a recording is running. |
| Hold-timer / stop conflation | A press that begins as a stop never starts a hold timer, so a stop gesture can never become a second start. |
| A busy update cancelled the local gesture without reporting cancellation | The start intent is **preserved** across the busy transition — the parent reports busy *because* it is starting the capture this gesture asked for — and the release decides: `finish` a confirmed recording, `fence` a start that never became one. `releaseAction` gained the `fence` action and a `startPending` argument. |
| A delayed start lost its release/cancel intent | The fence goes to the parent through `onCancelCapture`, and `pointercancel` / lost capture fence the same way. A late acknowledgement can no longer leave a recording running with no gesture left to stop it. |
| Keyboard repeat and assistive activation could duplicate a start | `event.repeat` is ignored, `preventDefault` suppresses the browser's own activation click, the pointer's compatibility click is rejected by its device detail count, and a detail-0 assistive click is accepted. |
| Enter produced one finish | Control case, kept: exactly one activation, asserted in the browser. |

Two further defects were found by the fixture and fixed:

- Releasing a push-to-talk hold was cancelled by the parent's own `busy` transition, so
  push-to-talk could never finish. This is the mirror of the review finding: cancelling on
  *any* busy transition is as wrong as never cancelling. Covered by the "hold becomes
  push-to-talk" scenario.
- `CameraIcon` carries only a `viewBox`, so the Vision tile scaled it to fill the tile and
  dragged its whole row tall while the other three glyphs stayed 34px. All tile glyphs now
  have an explicit, consistent size.

## The readiness findings

`node --experimental-strip-types probe-home-readiness.mjs` → `readiness-probe-results.json`,
same shape as the reviewer's file so the two diff directly, plus the `activation` field.

```
PASS fresh_touch_idle: canStart=true activation=direct strip="Ready to ask"
PASS stale_touch_idle: canStart=false activation=direct strip="Status unavailable" — The last device report is too old to trust.
PASS physical_ptt_without_touch: canStart=false activation=direct strip="Ready to ask" — Hold the side button to talk.
PASS complete_new_chat: canStart=true activation=new-chat strip="Your answer"
PASS error_retry_new_chat: canStart=true activation=new-chat strip="Could not answer"

5/5 cases match the reviewer's expectations.
```

A finished or failed turn is startable again, but it is reported as `activation: 'new-chat'`, so
the parent runs its explicit New chat preparation first: capture begins only after a successful
reset, never into a context that did not reset.

## Layout, measured

The fixture renders Home inside the **real shell rows** — `76px | minmax(0, 1fr) | 94px` from
`screen.css` / `portrait.css` — because Home does not own the whole panel and must not be
measured as if it did. `gesture-results.json` holds every rectangle.

| Panel | Control | Tile map | Result |
| --- | --- | --- | --- |
| 720x1560 idle | 200x200 at (260, 552.5) | y=1124 h=318 | pass |
| 720x1560 long answer | 200x200 at (260, 269.69) | y=1124 h=318 | pass |
| 720x1560 recording | 200x200 at (260, 524.84) | y=1124 h=318 | pass |
| 480x1040 long answer | 160x160 at (160, 285.77) | y=670 h=258 | pass |
| 360x800 long answer | 128x128 at (116, 204.75) | y=454 h=234 | pass |

The earlier revision measured the control at **102px high, overlapping the strip and identity**
on 720x1560 with a long answer. It is now 200x200 with no overlap, and the tile map holds
`y=1124 h=318` in both the idle and long-answer states — an answer never displaces navigation.

What changed structurally:

- **Settings has a real 72px column** in the top strip and the status column wraps beside it.
  It was absolutely positioned and covered the status label.
- **One explicit main region.** The control and the optional reader live in a single `stage`
  area, so optional feedback cannot add an implicit grid row. The reader is bounded inside it
  and scrolls, so a long answer cannot push the map or squash the control.
- **The region is bounded, not merely laid out.** It clips and scrolls rather than letting flex
  children overflow onto the strip above and the map below, which is how the overlap happened.
- **Short panels reflow without shrinking readable text.** Under 900px tall the identity goes
  first, the control reflows to 128px and the status strip tightens its own type scale. Every
  status word stays. Chat and answer text are never reduced.

## Gesture evidence, in a real browser

`node drive-fixture.mjs` drives headless Chrome over the DevTools Protocol with **trusted**
input — real mouse presses, real key events — against the real component.

```
PASS tap starts one chat: start
PASS second tap finishes the recording: finish · phase review
PASS hold becomes push-to-talk and the release finishes it: start → finish · phase review
PASS release while the start is still pending fences it: start → cancel · abandoned true
PASS holding Enter produces exactly one activation: start
PASS an assistive click activates once: start
PASS a cancelled pointer gesture does not become a tap: cancel

All 7 gesture scenarios and 8 geometry checks pass.
```

The pending-start scenario is the race from `LEAD_REVIEW.md`. The harness models an
asynchronous New chat preparation and the release lands while it is still in flight. The
component fences, the harness honours the fence, and the late acknowledgement is ignored: the
settled state is `phase idle · busy false · abandoned true`. Both frames are captured
(`gesture-pending-race-720x1560.png`, `gesture-pending-race-settled-720x1560.png`).

## Screens

21 captures in `screenshots/`, portrait first.

| Screen | File |
| --- | --- |
| Idle, ready | `home-idle-720x1560.png` |
| Recording, explicit stop affordance | `home-recording-720x1560.png` |
| Processing | `home-processing-720x1560.png` |
| Answer, long text scrolls in a bounded reader | `home-answer-720x1560.png` |
| Answer, streaming | `home-answer-streaming-720x1560.png` |
| Answer, incomplete | `home-answer-incomplete-720x1560.png` |
| Stale report — strip and control agree | `home-stale-720x1560.png` |
| Physical-only device — blocked, names the side button | `home-physical-only-720x1560.png` |
| Failed turn — startable again through New chat | `home-error-720x1560.png` |
| Microphone disconnected | `home-no-mic-720x1560.png` |
| Agent and Gallery unavailable, positions kept | `home-restricted-tiles-720x1560.png` |
| No connection | `home-offline-720x1560.png` |
| 480x800 reference, 480x1040, 360x800 narrow | `home-reference-480x800.png`, `home-idle-480x1040.png`, `home-answer-480x1040.png`, `home-idle-360x800.png`, `home-answer-360x800.png` |
| Desktop preview, portrait device framed | `preview-desktop-portrait-1440x1000.png`, `preview-desktop-portrait-answer-1440x1000.png` |
| Rotation fallback, stress only — not an edition | `stress-landscape-1024x600.png`, `stress-landscape-answer-1024x600.png` |

## Reproducing

```bash
cd apps/lantern-fleet-admin
node --experimental-strip-types --test lib/home-navigation.test.mjs   # 58 pass
node --experimental-strip-types --test lib/*.test.mjs                # 260 pass, 0 fail
npx tsc --noEmit --incremental false                                 # exit 0

cd work/ember-lantern-home-20261002
node --experimental-strip-types probe-home-readiness.mjs             # 5/5
node build-fixture.mjs                                               # bundles the real source
node drive-fixture.mjs                                               # 7/7 gestures, 8/8 geometry, 21 screenshots
```

Fixture URL (local file):
`file:///Users/l33tdawg/nodejs-projects/sage-commons/work/ember-lantern-home-20261002/home-fixture.html?scenario=ready&log=1`

Parameters: `scenario=ready|stale|physical-only|complete|error|listening|processing|no-mic|restricted|offline`,
`parent=inert|confirm|slow`, `feedback=none|short|long|streaming|incomplete`, `log=1`,
`shell=0` (render Home bare), `frame=1` (frame the portrait device in a desktop window).

## What is real and what is synthetic

Real: the component, `lib/home-navigation.ts`, `lib/device-state.ts`, the app CSS, React 19,
the browser, the layout, the input events, the callback sequences, the shell row heights.

Synthetic: the device status, the shell rows' *contents* (labelled placeholders — only their
heights are modelled), and the parent. There is no transport, no capture owner, no microphone,
no model, no NPU, no reset, no router and no device. The harness records callbacks and models
one parent behaviour — an asynchronous preparation that can be fenced — and **nothing here
persists a task, a schedule, a pin, a photo, a gallery entry or a model detection**. The Agent
and Gallery tiles report themselves unavailable in this build because they are, and their
positions are kept rather than dropped.

## Honest limits

- Headless Chrome runs with `--no-sandbox`: Chrome's own sandbox cannot initialise inside a
  restricted shell on this host. Only local `file://` pages built from this repository are
  loaded; no remote content is fetched. The screenshots are unaffected, but this is a weaker
  browser configuration than a normal run.
- Below the 480x800 reference — the 360x800 check — the reader is bounded and scrolls
  internally at about two lines. The control and the map are never displaced, and no text is
  reduced, but that panel is a stress check, not a target.
- Freshness is evaluated when the status prop changes, from the parent's polling. A reading
  that goes stale between polls is not re-evaluated on a timer here; the parent owns polling.
- Accessibility is checked by construction and by the detail-0 click path, not with a screen
  reader.
- No live acceptance claim: no device, service, model, audio, session, power or deployment
  behaviour was touched, and none of this is integrated into the app's routing.

---

# Integration into Screen (bounded wiring) — 2 October 2026

Rook's integration contract (`work/pocket-ui-start-review-20261002/HOME_INTEGRATION_CONTRACT.md`)
asked for the already-authorised bounded wiring plus three source gaps closed. This section is
that increment. The component above is unchanged in intent; what changed is that it is now
driven by the real parent, the real capture owner and the real New chat policy.

## What was wired

`app/device/screen.tsx` gained a `home` destination and a `homeEnabled` flag that defaults to
**false**. When enabled the app starts on Home; Chat is still `main`; `Agent` and `Gallery` stay
in their tile positions and report themselves unavailable because 2b/2c do not exist yet.

- **One capture owner.** The existing `TalkControls` is mounted once and never moves. Home and
  Chat share it through an explicit interface (`TalkController`: `start`, `finish`, `cancel`)
  plus an ownership report. The footer that contains it is `hidden` on Home rather than
  unmounted, so navigating Home → Chat → Home cannot remount the owner mid-recording.
- **New chat is explicit.** With Home enabled the header wordmark navigates; a labelled
  **New chat** button in the conversation drawer runs the existing reset. Without Home the old
  behaviour is untouched.
- **Preparation with an outcome.** `clearAnswer()` now returns `success | failure | refused`
  instead of swallowing failure, because capture is only allowed to follow success. Each
  activation carries a monotonic token checked after every `await`, so a gesture that ends
  during the reset cannot leave a recording behind it, and a late `/start` acknowledgement is
  cancelled with the credentials it returned rather than adopted.

## The three contract gaps

Each is asserted as behaviour in `screen-gesture-results.json`, against the real component tree
in a real browser — not as source text.

| Gap | What it was | What it does now |
| --- | --- | --- |
| Re-asking from a finished turn | `questionServiceRunning` alone permitted capture, so a completed answer with the microphone unplugged still offered "ask again" | The spoken lane must be ready as well. The finished answer stays readable — it never depended on the microphone — and the reason is shown. |
| Physical/other-client recording | any `listening` reading advertised "Tap Done when finished, or Cancel" | Ownership is an explicit input. Only a session this owner was acknowledged for *and* the device still publishes offers Stop/Cancel; anything else is shown without a GUI stop command. |
| Owned recording under the real busy flag | `busy` was tested first, and the real owner reports `owned \|\| pending \|\| uncertain`, so Stop/Cancel vanished exactly while recording | A confirmed owned recording outranks the busy flag. |
| Release after readiness was lost | an unavailable control is phase `idle`, so a press that began ready could still dispatch a start | `releaseAction` takes the current `canStart`; and the component no longer clears a pending start just because a new press began. |

## Two defects the wiring itself exposed

Both were found by driving the integrated screen, not by reading it.

1. **The status poll bounced Home back to Chat.** `screen.tsx` returned to `main` on every voice
   session change. Because Home is a full surface rather than a detached screen, every visit to
   Home lasted exactly one poll (700 ms). The contract named this bounce; the fix keeps `home`
   alongside `messages`/`sharing`.
2. **The conversation drawer fell into normal flow on Home.** The drawer's portrait behaviour is
   driven by the `device-thread-layout` class, which Home did not carry, so the drawer rendered
   as a block above the tiles. Home now joins the one navigation model instead of inventing one.

## Reproducing

```bash
cd apps/lantern-fleet-admin
npx tsc --noEmit                                   # clean
npm test                                           # 266 pass, 0 fail
npx eslint app/device/{home,screen,talk,device-header}.tsx lib/home-navigation.ts

cd ../../work/ember-lantern-home-20261002
node build-screen-fixture.mjs                      # bundles the real screen.tsx
python3 -m http.server 8791 --bind 127.0.0.1       # serve this directory
node drive-screen-fixture.mjs http://127.0.0.1:8791/screen-fixture.html
node probe-home-readiness.mjs                      # 5/5 reviewer readiness cases
node probe-home-additional.mjs                     # 5/5 previously failing probes
node build-fixture.mjs && node drive-fixture.mjs   # standalone component: 7 gestures, 8 geometry checks
```

## What the integrated preview is, and is not

`screen-fixture.html` renders the real `screen.tsx`, the real `TalkControls` and the real Home.
The **device service is simulated in-page**: `window.fetch` answers only `127.0.0.1:3199/status`
and `127.0.0.1:3197/*` from a small state machine, and **every other origin is refused and
recorded** — the driver asserts nothing else was ever attempted. The banner above the panel says
the same thing, so a screenshot cannot be mistaken for device evidence.

Scenarios: `scenario=ready|complete|error|streaming|listening-physical|no-mic|stale|offline`,
`mic=on|unready|off`, `reset=ok|refuse|fail`, `startDelay=<ms>`, `frame=1`, `log=1`. The
synthetic service keeps a **loaded thread** (one saved exchange) and publishes the live
session as the active conversation, so loaded-thread and reading-position cases are real
rather than empty-state ones.

Still not established by any of this: no device, service, model, audio, session, power or
deployment behaviour was touched. The preview is browser review, not bench-panel or DSI
acceptance, and `homeEnabled` remains a candidate flag that Rook decides on.


---

# Review response — msg-bce23074, msg-35b5b7e2, msg-79bbf9ec

Rook reviewed the wiring mid-edit and reproduced three defects. All three are fixed and each
has a scenario in the driver, so the fix is behaviour rather than a claim.

## 1. Reset succeeded and no recording followed (reproduced)

`scenario=complete&reset=ok` → activate → `POST /clear → 200`, no `/start`, and Home showed
*Recording is not available right now.* Two causes, both removed:

- the readiness the activation re-checks after its own reset included this screen's `clearing`
  flag, so the reset's own completion read back as an external busy operation. The dispatch
  input is now `captureBlocked`, which excludes it;
- the reset only ran when the control reported `activation: 'new-chat'`. Every intentional Home
  start now runs the New chat policy first — an idle device can still have a loaded thread.

Scenario `reset-succeeds-then-record` asserts `/clear` **precedes** `/start`, that no error is
shown, and that the owned recording exposes its stop affordance.

## 2. The conversation drawer consumed the Home portrait (reproduced)

`.device-chat-list` filled the region above Home at 720×1560. Home now carries the existing
`device-thread-layout` model rather than inventing its own, takes the content column explicitly,
and the drawer — a Chat affordance Home has no control for — is `display: none` on Home in both
orientations. Scenario `conversation-drawer-does-not-cover-home`.

## 3. Stop left the review on Home without its editor (reproduced)

After a stop, the authoritative poll reports *Check your question · Edit or record again…* while
the Screen still rendered Home. Home is the preparation and listening surface; the review editor
lives in Chat. The poll now routes an authoritative `review` into Chat **once per session**, and
that route never runs the New chat policy as a side effect of polling. Scenario
`review-routes-to-chat` asserts the editor and its actions are present.

## The remaining contract points from msg-bce23074

- **Lifecycle fencing.** `fenceActivation()` bumps the activation token on a new session, on a
  failed or stale poll, and on navigating away from Home; `abandonActivation()` also withdraws the
  owner's unacknowledged start and is used when the screen hides or the gesture is cancelled.
  `TalkControls.abandon()` now bumps its own intent token, so a hide/show that lands before the
  acknowledgement cannot adopt the late session.
- **Navigation while a turn is live.** The wordmark's busy guard is now
  `busy && wordmarkStartsChat`: navigation is presentation and the single capture owner survives
  it, so viewing the same turn from either surface stays possible while only the destructive New
  chat action remains blocked. Scenario `viewing-another-surface-does-not-stop-a-recording`
  records a live recording, opens Chat, returns Home, and asserts no `/stop` and no second
  `/start`.
- **Loaded thread.** The synthetic service now publishes a saved exchange and the live session as
  the active conversation. Scenario `loaded-thread-survives-navigation`.

## Current status of the fixture limits Rook listed

Still **not** modelled, and therefore not claimed: retained photos, lock/epoch transitions, and
mid-stream duplicate/out-of-order ordering. Progressive text is exercised only in the honest
sense the real contract allows — Home renders whatever the authoritative status publishes
(`streaming` scenario shows partial text with the live caret); there is no replay timer, and the
"Incomplete" marking remains unwired because the status contract publishes no interruption flag.

---

# Revision: six-tile Home, pending-start withdrawal, queued clear

Rook's owner-approved Home expansion (`work/pocket-ui-start-review-20261002/HOME_SHORTCUT_EXPANSION.md`,
canonical brief revision 4) plus the four pre-checkpoint lifecycle items from `msg-a69e7993`. Everything
below is source-only, disconnected-preview evidence. `homeEnabled` is still false by default, no device
was contacted, and no acceptance is claimed.

## What changed

| File | SHA-256 | What |
| --- | --- | --- |
| `app/device/home.tsx` | `40fb0d550344f4b75c27c8d6aeceeeddbb9db2002bcddaa160717462a5eca9bc` | Six tiles, active-pointer identity, pending Cancel, tile focus restoration, reserved caption slot |
| `app/device/home.css` | `93254d8d278b35f722115c78fb75df1928cb8300415e26af47e1aabeb1375b19` | Equal three-row grid, screen-scoped tile rules, short-panel reflow, reader floor |
| `app/device/screen.tsx` | `5277bdba76f9c8de9816b0c48732a084498a981ecb1e733ebf2c6438caac4ba0` | Pending-start withdrawal on leaving Home, post-reset slot wait, origin-aware Back, expected session changes |
| `app/device/talk.tsx` | `fc7db4df6412a7f96d527750098e3c5cfe51a27088584edb57fc2e761a2cf17a` | `withdrawStart()` on the controller: the pending start only, never an accepted recording |
| `lib/home-navigation.ts` | `be55367151db51744017f1b681f171b0994172eb9e0b541401dbad326a2ef7b1` | Six destinations, Messages/Nearby routes, `resetSlotWait` |
| `lib/home-navigation.test.mjs` | `242e984053e9db216e5f86d236ecce0ae79c88478ea906fb98ba9b45a410b96b` | Six-tile order, reference positions preserved, slot-wait contract |
| `lib/home-integration.test.mjs` | `9d54f0d3c040154a56f14f88279007263d09285ffdb4ff9f96ab834fa760d11c` | Pins the wiring decisions below as source assertions |

## The four lifecycle items

1. **Pending-start withdrawal.** `leaveHome` fenced only the parent's activation token while
   `TalkControls.start` compared its own intent token, so Chat adopted a recording the user had already
   left Home during. `TalkController` now exposes `withdrawStart()`, which bumps the owner's intent
   without touching an accepted session, and `leaveHome` calls it. Asserted by
   `pending-start-withdrawn-when-leaving-home` (8/8) and by
   `viewing-another-surface-does-not-stop-a-recording` (6/6), which is the mirror case: an accepted
   recording survives the same navigation.
2. **Visible Pending Cancel.** Home rendered Cancel only while `recording`. It now renders it while a
   start is pending too, tagged `data-cancel-scope="preparing"`, with a "Preparing a new chat…" line.
   Asserted by `pending-cancel-withdraws-the-start` (6/6) and `queued-clear-holds-the-slot` (5/5);
   `screen-17-queued-clear-pending.png` shows it on the panel.
3. **Queued reset ordering.** The fixture now models the real binding: `/clear` answers
   `{"status":"clearing"}`, reserves the slot, and answers `409 voice_busy` to a start until its worker
   finishes — the contract `probe-reset-contract.py` replayed from the actual methods. The service also
   publishes the real `clearing` phase, so the status strip honestly reads *Starting voice… Waiting for
   the voice service* rather than a fabricated readiness. `startNewChat` now waits for a reading the
   device produced **after** the acknowledgement before dispatching a start, bounded by
   `RESET_SLOT_WAIT_MS` and cancellable through the activation token.
4. **Readiness at the real dispatch.** The wait above forced a genuine bug into the open: the poll woke
   a waiting preparation in the same tick it counted the reading, but `liveInputs` only caught up one
   render later, so the wait judged the *pre-reset* status and dispatched a start the device refused.
   The poll now writes `status`/`connected` into that ref in the tick it counts them.
5. **Active pointer identity.** A second finger's `pointerup`/`pointercancel` was releasing the first
   gesture. The gesture now records its owning `pointerId` and ignores any other pointer's release.

## Session changes that are this screen's own

A queued clear republishes the voice session, and so does an accepted start. Fencing on those withdrew
the activation that had just asked for them. `expectedSessionChanges` counts the changes this
activation causes, and the poll only skips the fence while an activation is genuinely pending and an
expectation is outstanding — an unrelated session change still fences. `resetSlotWait` returns
`withdrawn` first, so no reading can revive an activation whose gesture is over.

## Six-tile Home

Fixed row-major 2×3: Chat | Vision, Agent | Gallery, Messages | Nearby. The first four keep the
reference positions. Messages routes to the existing `messages` page and Nearby to the existing `status`
page; both open even when their transport is down, and neither sends a message, acknowledges a receipt,
announces, pairs or writes a radio by being opened. Home→Messages→Back and Home→Nearby→Back return to
Home and restore focus to the originating tile; nested Nearby views return to Nearby first; the header
and menu entries keep their own origin (`main`).

## Geometry, measured on the panel

`frame=0` now means *the panel is the viewport*. Before this the frame stayed pinned at 720×1560, so the
480 and 360 checks were measuring the 720-wide panel through a smaller window and their media queries
never applied — the viewports were cropped, not tested. Corrected and re-measured:

| Viewport | Equal tile rows | All six in the panel | Overlap |
| --- | --- | --- | --- |
| 720×1560 | 160px | yes | none |
| 480×1040 | 140px | yes | none |
| 360×800 | 106px | yes | none |

The starting geometry Rook gave was 160/132/104. 480×1040 settles at 140 and 360×800 at 106 because the
caption line's own content requires it — "Increase a row equally if its contents require it". Rows stay
equal at every size.

Three layout defects were found and fixed while measuring:

- `.device-screen button` (one class + one element) beat a bare `.device-home-tile`, so the integrated
  Screen silently dropped the tile minimum height while the standalone fixture honoured it. Every tile
  rule is now declared twice — unscoped *and* screen-scoped — because scoping it only to the screen
  broke the standalone fixture instead. The integration test pins both.
- A caption that appeared on two tiles and not the other four made those rows taller; because rows are
  equalised, the whole map grew and squeezed the control. The caption slot now exists on every tile.
- The reader was allowed to shrink to nothing and was measured at 42px on the short panels. It now has a
  48px floor, which makes the *stage* scroll — the fallback the contract names — instead of collapsing
  the answer. The geometry driver accepts a scrolling region only when it actually scrolls **and**
  scrolling brings the control fully into view; otherwise it still fails.

## Gates on this revision

- `node --experimental-strip-types --test lib/*.test.mjs` — **281 passed, 0 failed**.
- `npx tsc --noEmit` — exit 0.
- Scoped ESLint over the changed files — 0 errors. Full app ESLint still reports the same 8 errors in
  `nearby-directory.tsx`, `share.tsx` and `share-render.test.mjs`, all outside this scope and untouched.
- Integrated Screen driver — **24/24 scenarios, 0 console errors**, over the local HTTP preview with
  every non-loopback request refused and recorded.
- Standalone Home fixture — **7/7 gestures, 8/8 geometry checks**.

## Still not modelled, and therefore not claimed

Retained photo bytes, lock/epoch transitions, mid-stream duplicate and out-of-order events, and a real
device. Balance, thermal behaviour, latency, audio quality and everything under DSI/panel qualification
remain unmeasured; no device was contacted for any result above.

---

# Revision: review corrections P1/P2, and the separately-reviewable 2b screens

Two workstreams are in this revision and they are separable on purpose: the two
Home capture corrections Rook reproduced (`work/home-final-review-20261002/REVIEW.md`,
handed over in `msg-c5c6285b`), and the 2b Gallery/Camera/Chat composition. The
Home corrections are the priority and their files are listed separately below.

## P1 — the fence now reaches the capture owner

`fenceActivation` incremented the parent's activation token and cleared the
presentation, but `TalkControls` compares its own intent token, so a start already
awaiting its acknowledgement could still arrive, be adopted and be kept alive.
Every authority and freshness invalidation now withdraws it:

```
captureController.current?.withdrawStart();
```

`withdrawStart` bumps the intent and nothing else, so an *accepted* recording is
untouched — that is what keeps Home↔Chat viewing safe. Navigation was the only
path that did this before; the failed/stale poll and the session-change branch now
do it too. A start landing after the fence is cancelled by the owner with the
credentials it returned.

**The session-change case needed a second fix, because a fence cannot judge what it
cannot identify.** While a start awaits its acknowledgement the device may
legitimately publish the session it is about to hand us, so fencing on the first
publication would destroy the very recording the user asked for — and the parent
cannot tell that session from a foreign one until the owner holds something. So
the poll now compares the session our owner holds against the session the device
is publishing, and cancels a superseded one:

- judged off the fresh reading inside the poll, not in an effect (a synchronous
  setState in an effect is also a React lint error here);
- only when the published phase is live (`listening`/`review`/`processing`) — an
  idle reading may simply predate our own publication, and cancelling on it would
  end a legitimate recording the moment it started;
- cancelled with its own credentials via `cancel()`, never adopted, never kept
  alive.

Reproductions, both in the integrated driver:

| Scenario | Fault | Result |
| --- | --- | --- |
| `fault-disconnect-cannot-adopt-a-late-start` (7/7) | status reads fail after `/start` is dispatched, acknowledgement delayed 1800ms | Home reports Reconnecting, the late session is cancelled with its credentials, never left listening, **zero** keepalives |
| `fault-session-change-cannot-adopt-a-late-start` (6/6) | another client takes the device while the start is out | the superseded session is cancelled, never left listening, **zero** keepalives |

The fixture fault had to be corrected to be the case it claimed: a decoy that
disappears the moment our own acknowledgement lands leaves the device legitimately
on the session we asked for, and there is nothing to detect. It now moves the
device onto the foreign session and keeps it there.

## P2 — a live turn closes the destinations that would cancel it

Every destination except Chat renders outside the subtree holding the single
`TalkControls`, so opening one unmounts that owner and its cleanup cancels the
session it holds. The policy is now a pure, tested property rather than an
implicit consequence of which JSX branch a destination happens to use:

- `destinationKeepsCapture(id)` — only Chat does;
- `tileCommand(id, available, locked)` and `unavailableReason(id, available, locked)`
  — a locked destination resolves to no navigation **and** says
  "Finish the current question first.";
- `openHomeTile` applies the same check, because a disabled button is not a guard:
  a stale render, a synthetic click or a keyboard activation still reaches the
  handler.

Transport health is deliberately not part of this: an idle, disconnected Messages
or Nearby route stays usable, which the pre-existing idle navigation scenarios and
their Back/focus evidence still cover.

`live-recording-closes-unsafe-home-destinations` (10/10) asserts the recording is
owned, all five non-Chat tiles are closed and each says why, Chat stays open, **a
forced click on a locked tile does not navigate and the recording survives it with
no `/cancel`**, and after stopping the destination opens again.

Revision `home-helper-tests` here are **72/72** (Rook measured 68 at the receipted
revision; the four added cases are the lock policy).

## A third finding, measured but NOT fixed

`review-navigation-documented-behaviour` (6/6) records a defect I did not repair,
because the fix is a design choice Rook owns:

**Leaving Chat while a question is under review discards it.** `QuestionReview`
sends `review/cancel` from its own unmount, so pressing the header's Home control
during review cancels an authoritative review. The tile lock cannot protect this
case, because being on Home during review is only reachable *by* cancelling it.
Reproduction is the scenario above; `screen-34-review-after-leaving-chat.png` is
the panel. The choice is between locking the header's Home control during review
and letting the editor survive navigation — I did not pick one unasked.

## 2b, separately reviewable

Delivered in the same revision, in its own files, with its own scenarios:

- **Gallery** (`gallery.tsx`, `gallery.css`, `gallery-navigation.ts` + tests):
  a separate destination for photos actually retained with a question, read
  through the existing protected history binding. Real validated JPEG bytes, not
  filenames; bounded thumbnail batches; image state in memory only, dropped on
  lock/owner change, hidden document, unmount and deletion; selection followed by
  session identity so a deletion closes the viewer rather than showing a
  neighbour; deletion worded as the whole effect because there is no photo-only
  command.
- **Camera → Gallery** shortcut, returning to the camera.
- **Chat** (`chat-shell.tsx`, `navigation.css`): Chat's own title bar with the
  conversation-list control. The message stack, composer, review and reading
  state are reused, not rewritten.
- Home's Gallery tile becomes openable (`routeFor('gallery')` is now an existing
  route); Agent remains the one undelivered 2c destination and still reports why.

Gallery scenarios: 10, covering grid/viewer/prev-next boundaries, staging a photo
into Chat **without starting the microphone**, combined deletion, empty vs
saving-off vs unsupported, a listed record whose bytes are gone, and a photo with
no microphone.

## Gates on this frozen revision

- `node --experimental-strip-types --test lib/*.test.mjs` — **314 passed, 0 failed**
  (the receipted revision was 281; the 303/305 Rook saw was the mixed snapshot's two
  stale route assertions, now updated).
- `npx tsc --noEmit` — exit 0. The `Gallery` missing from `HeaderPage` error Rook
  measured is fixed.
- Scoped ESLint over every changed file — **0 errors**. The only warnings are
  ESLint reporting the three `.css` files as "ignored because no matching
  configuration was supplied": this app's ESLint config lints TS/JS only, so
  the stylesheets are reviewed by measurement instead (the geometry checks).
- Integrated Screen driver — **38/38 scenarios, 0 console errors**.
- Standalone Home fixture — **7/7 gestures, 8/8 geometry**.

## Hashes, grouped so each change set can be reviewed alone

Home 2a: `home.tsx ced9bd10…`, `home.css 93254d8d…`, `home-navigation.ts 738047d1…`,
`home-navigation.test.mjs 0672e3a1…`, `home-integration.test.mjs f3d3acad…`.

Home wiring and the shared screen: `screen.tsx abea9f13…`, `talk.tsx fc7db4df…`
(unchanged from the receipt), `device-header.tsx 6c3295fd…`, `icons.tsx c6c5d4f9…`,
`camera.tsx bdfeebb5…`.

2b only: `gallery.tsx d50d6afc…`, `gallery.css 38192fab…`, `chat-shell.tsx a189d7d0…`,
`navigation.css 87fa1988…`, `gallery-navigation.ts cc332014…`,
`gallery-navigation.test.mjs 799b9663…`.

Nothing is accepted, `homeEnabled` stays false, and no device, microphone, model,
radio, lock/epoch transition or physical panel was exercised.

---

# Correction — Settings origin, confirmation focus, and this record

2 October 2026, 07:51 UTC. Task `c423d913-de77-4327-a98c-2e179f238f20`, program
`acc40de9-090c-4ede-ad09-d63dd2a89975`. Directed by Rook's reply to `msg-2913bdc8`
(`work/auth-integration-20261002/unit-c/INBOX_REVIEW.json`, `decisions.evidence`):

> *"Preserve historical frozen bytes/results. Add dated current correction with full hashes,
> commands, counts and provenance, point README to it. Run affected scenarios/gates into new
> artifacts without overwriting the 42-scenario record. Return changed-file closure for
> independent review."*

The product correction itself — F1, the Settings origin, and F2, the confirmation's focus —
is closed in `SETTINGS-CORRECTION.md`. This section is the **record correction**: the hash list
and gate numbers above were stale when they were reviewed (six hashes did not match the tree,
and the gate counts were 314/38 against a tree at 320/42), and five further files changed in
this correction. Everything below is measured on the current tree at the time above.

## Hashes, current

> The three shared-screen files below (`screen.tsx`, `question-turn.tsx`,
> `question-flow.css`) were revised again by the partial-answer retention increment
> of 3 October; the current values are in
> [`PARTIAL-ANSWER-RETENTION.md`](PARTIAL-ANSWER-RETENTION.md). Every Home 2a file and
> every other hash in this section is unchanged.

Home 2a:

```
df39f7b5821f6cbfbd110d6db015fdf4f2b7d4d8e3be8b538ba6edb0a5189283  app/device/home.tsx
93254d8d278b35f722115c78fb75df1928cb8300415e26af47e1aabeb1375b19  app/device/home.css
c63136b026405329bf3528cdb13c0dd922d50fc91a80e08147b536099b144773  lib/home-navigation.ts
0672e3a1781cd6d8b0a176464b8af52c82d61f6e1c7765c9641afcb74d467c39  lib/home-navigation.test.mjs
af4e150461a3ec599fd5096be2fff7683d88f043f0ca143a89cad566cb5afd9f  lib/home-integration.test.mjs
```

Home wiring and the shared screen:

```
490765f314b5252a8cb962cf5898fe0253cb9cbd56a8835cc9b0200981d738ea  app/device/screen.tsx
7cc81ee10b1e82bb28233146976d57f7608daa0a2636c3a0dd582725da91536f  app/device/talk.tsx
7415ee9ec68f505d52091ca1f0e376a5cc74b8f2365c12e74cf974e5cb2c6983  app/device/device-header.tsx
c6c5d4f981c0a1320d5e641169485af3cceb7a0897b743549a9760ee42241ad5  app/device/icons.tsx
bdfeebb5c56db4eef7776f6dad80663a50125afb54020370da01eab077594981  app/device/camera.tsx
```

2b only:

```
36d5b4dc5088a5b23cd390f3c5092ddde75ca28b55e5035a1e357375e7303335  app/device/gallery.tsx
12dbb09c2ccefa4cf5468378d02dd65b8cd59a7856912718f6426de7c20b4be8  app/device/gallery.css
a189d7d0ae7c1b824027d2cd44c56577121f017e94ab1a3966e79495b51fe5d0  app/device/chat-shell.tsx
87fa198873a01c4c053cf5dafe3201ee38f7ef66953701c0619884f204c2dadf  app/device/navigation.css
cc33201494cff518e92c2e86acfdd8563c115d3278e65f5a9540468f6820ff82  lib/gallery-navigation.ts
799b96632199725a40c22720e5c84cbe70b02e0a8e6cc83a4299b21f04110a6e  lib/gallery-navigation.test.mjs
```

Settings surface, and the tests this correction touched:

```
2ddcb3e104b011ec4ce9682c672379a12356d3015526c03a3029e8f087b99c0b  app/device/settings.tsx
73c9b2296ca5e1ab48041b1aeb6c7b69128cde7e76ce973c10d904384e47016f  app/device/power-settings.tsx
22cc7a8bdad0ab6b3d83b7ac816d25de84341e280a9bc9f7ea577780a5d4f7c6  app/device/name.tsx
f2d36ea2504a00ddfe2cb5cf6d38523727eea0d6614b2c85a3d94755f82f3da4  lib/device-history-shortcut.test.mjs
d8545278160d1b1ec28e636f9ab275addec33c6d4f832fa4b5d89affc24440c7  lib/device-share-action.test.mjs
```

`power-settings.tsx` and `name.tsx` are unchanged from `HEAD` `91b7a9c`, as `SETTINGS-REVIEW.md`
recorded. Of the sixteen hashes in the historical list above, **eight still match and eight do
not**: four were already stale when the Settings review measured them (`talk.tsx`,
`device-header.tsx`, `gallery.tsx`, `gallery.css` — the first two are Rook's own files), and
four changed in this correction (`home.tsx`, `home-navigation.ts`, `screen.tsx`,
`home-integration.test.mjs`). The other eight — `home.css`, `home-navigation.test.mjs`,
`icons.tsx`, `camera.tsx`, `chat-shell.tsx`, `navigation.css`, `gallery-navigation.ts`,
`gallery-navigation.test.mjs` — are byte-identical to what the historical list recorded.

## Gates, current

```
cd apps/lantern-fleet-admin
node --experimental-strip-types --test lib/*.test.mjs   # 323 tests, 323 pass, 0 fail
npx tsc --noEmit                                        # exit 0
npx eslint <changed files>                              # exit 0, 0 errors, 1 warning
```

The single ESLint warning is its own "file ignored because no matching configuration was
supplied" for `home.css` — this app lints TS/JS only, so stylesheets are reviewed by
measurement instead. Three tests were added as pins for F1 (`home-integration.test.mjs`), and
three existing source-text assertions were updated rather than weakened: they matched the old
inline focus chain and the old `focusTile` origin field, both of which this correction
replaced.

```
cd work/ember-lantern-home-20261002
node build-screen-fixture.mjs && node build-fixture.mjs
python3 -m http.server 8791 --bind 127.0.0.1
node drive-settings-fixture.mjs http://127.0.0.1:8791/screen-fixture.html correction
node drive-screen-fixture.mjs   http://127.0.0.1:8791/screen-fixture.html correction
node drive-fixture.mjs correction
```

| Run | Result | Artifacts |
| --- | --- | --- |
| Settings driver, corrected | **6/6 scenarios, 36/36 checks, 0 console errors** | `settings-correction-results.json`, `screenshots/settings-correction-*.png` |
| Integrated driver, corrected | **42/42 scenarios, 233/233 checks, 0 console errors** | `screen-correction-results.json`, `screenshots/screen-correction-*.png` |
| Standalone Home fixture, corrected | **7/7 gestures, 8/8 geometry, 21 screenshots** | `gesture-correction-results.json`, `screenshots/*-correction-*.png` |

The affected scenarios were the Settings ones; the integrated run is a regression check,
because `goBack`/`BackOrigin` are shared wiring and four of its scenarios assert Home-route
Back behaviour (`home-to-messages-and-back-to-home`, `home-to-nearby-and-back-to-home`,
`home-to-gallery-and-back`, `camera-to-gallery-and-back-to-camera`) — all four still pass.

## Provenance

The pre-correction bytes are preserved, not overwritten, in
`frozen-20261002T0735Z-pre-correction/`: the three results records, both fixture bundles and
their metafiles, and all 75 screenshots as they stood, with `SHA256SUMS` covering all 84
files. Re-hashing the live tree against that manifest shows only the four rebuilt files differ
(the two bundles and their metafiles); every record and screenshot is still byte-identical.
In particular `screen-gesture-results.json`, `settings-review-results.json` and every
unlabelled `screenshots/*.png` were never written to by this correction. The three drivers
take an optional trailing `label` argument; with no label they write the original names.

Current bundle and driver bytes, so the artifacts above can be tied to their producer:

```
b65ae039ac6dd956058ada692d02fcad72f1d5eadaabf20d6ba82028df6bf212  screen-fixture.js
6331c2971d7f5eedad507f9da0c9d2c215f973a8e106ee5c7e7404c9756c64dd  home-fixture.js
0aa767eb663b22545525810e2683fe32cbc68a954b771957189ea4068777c26d  screen-build-metafile.json
a0463a822d44db7f18209a58cf20c5ad5c4ea35f0bf201c9df0368503968f984  build-metafile.json
59d445789e130330cdee1f533890b7661406e8bd19c821a782983040737c2dd4  drive-screen-fixture.mjs
1fdd81c52c53f2611ce0e17d181ede39b9f37368cd853072f03afe011d6e67bb  drive-settings-fixture.mjs
1a8f9c73c7540667cdcbcb481d091026255fa6ae6b36e7399e086e5592068c63  drive-fixture.mjs
```

Two other evidence defects were found and recorded rather than quietly absorbed:

- The frozen 42-scenario record referenced three screenshots that were never captured
  (`screen-04-hold.png`, `screen-08-withdrawn-start.png`, `screen-25-gallery-boundaries.png`);
  verified absent from the frozen snapshot. The corrected run completes those captures at the
  state each scenario asserts, and `screen-correction-results.json` references 42 existing
  files and no missing ones. The frozen record was not edited.
- **O2, not fixed.** The other five menu entries still record no origin, so a previous Home
  origin can survive into them: Home → Messages tile → menu → Past questions
  (`screen.tsx:836`) → Back returns Home rather than Messages. Source-derived, not measured in
  the browser, and left for Rook because the fix changes five screens' Back behaviour.

## What this correction still does not claim

Nothing here is accepted, released or device-qualified. `homeEnabled` stays `false`. No
Settings write succeeds in this fixture, so a successful change remains unmeasured; the
name/profile panel and "Lock now" still need props the preview does not pass; the power and
lock panels were exercised only in their unavailable branch. Focus and keyboard behaviour are
measured in headless Chrome on this host, not on the physical panel. No device, microphone,
model, radio, lock/epoch transition or deployment was touched.

---

# Auth context fence, and the refused-start notice

2 October 2026, 08:21 UTC. The increment Rook queued after this correction (`msg-7a6db3c6`),
with the owner's go-ahead. Full record: **`AUTH-CONTEXT-FENCE.md`**.

Two product changes, both driven by the backend contract read from source rather than assumed:
the broker's noncredential `session_context` (derived from the access epoch **and** the device
token, so it survives every voice publication and moves only on a real rotation) now fences
admission, lease renewal, capture completion and rendering — replacing the
`expectedSessionChanges` counter, which is removed — and it reaches the existing
`subscribeDeviceSession` seam so a same-token rotation invalidates protected surfaces without a
second channel. A refused start (409 on the confirmation, or a context that cannot be compared)
now tells an active activation *"The recording did not start. Tap to try again."* while a
withdrawn one stays silent, and claims nothing about a takeover.

Evidence, in new files, with every historical record untouched:

| Run | Result | Artifacts |
| --- | --- | --- |
| `node drive-auth-fixture.mjs` (new) | **7/7 scenarios, 30/30 checks, 0 console errors** | `auth-fence-results.json`, `screenshots/auth-*.png` |
| `node drive-screen-fixture.mjs … auth` (regression) | **42/42 scenarios, 233/233 checks, 0 console errors** | `screen-auth-results.json`, `screenshots/screen-auth-*.png` |

Gates on the current tree: 330 tests pass / 0 fail, `tsc` exit 0, scoped ESLint 0 errors.
`context=off` remains the fixture default, so the earlier records stay comparable with what
produced them. One observation is reported rather than fixed (O3): after a rotation Home's strip
still reads "Starting voice…", which is a copy decision inside the accepted Home 2a hash set.
