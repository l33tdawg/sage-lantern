# Unit A dedicated device interface

Status: first implementation deployed to Unit A on 2026-09-05 after two independent source reviews; physical usability validation is pending.

## First deployment

- Local `/device` is the voice-first touchscreen; `/` redirects there. No keyboard, login forms or administration tabs on this surface.
- Local `/admin` is separate read-only maintenance, accessed externally through authenticated loopback SSH forwards for ports 3100 and 3199. See the app README. Neither route is available in hosted mode; existing hosted Fleet authentication is unchanged.
- Answer/question reading positions survive navigation; large scroll buttons indicate bounds. New voice sessions return from Radio to the recording display. Radio cannot be opened during listening/processing, and USB presence is explicitly not link verification.
- Single-flight status polling has a timeout, schema validation and freshness checks. Missing audio, unready capture bridge, service failures and approval errors have distinct recovery guidance. Losing status suppresses old readiness claims.
- All 13 app tests, lint and native ARM64 build passed. Live route/assets HTTP checks passed; dedicated Chromium restarted. These checks do not establish touch readability, successful hydration or five-loop physical acceptance.
- Test on Unit A: hold/release PTT, read a long answer, scroll both ways, toggle question/answer, open/close Radio, and start a question from Radio. Check recording/processing visibility and usable touch targets. Operator-controlled disconnect/reconnect checks remain pending.

## Product split

### Low-literacy design requirement

User clarified remote-area deployment includes people who cannot read or cannot
write. Large text alone is not sufficient. Use familiar pictures plus short
labels, stable control positions, one obvious primary action per state, direct
visual feedback and reversible actions. No typed setup or mandatory text input.
Camera aiming requires a live viewfinder, not blind capture followed by a photo.
Do not assume icons are universally understood: test unaided completion with
representative local-language users. Spoken guidance/answers remain an explicit
accessibility gap until optional audio output and local TTS are implemented;
current text answers are not a complete experience for non-readers.

### Voice activation follow-up

User raised physical-button failure and hands-free use on 2026-09-05. Proposed
next control is a large touch Talk button, then Stop & send and Cancel; retain
physical hold-to-talk as an optional parallel input. Both must operate one
recording/session owner, not separate audio capture processes. Touch readiness
must not depend on the physical `PTT_VERIFIED` marker or GPIO service health.
Do not simulate GPIO by overwriting its state file. Add an authenticated local
command boundary, session ownership, bounded recording, cancel/disconnect
semantics and explicit status before enabling the UI control.

User decision (2026-09-05): skip always-listening and wake-word work because of
silence/turn detection, interruptions and privacy implications. Explicit touch
or physical activation is the intended interaction. No background microphone
monitoring, ambient retention or automatic ambient-memory ingestion. Do not
resume hands-free experiments unless the user changes this decision.

The 3.5-inch device screen is for asking, waiting, and reading. Browser administration remains a separate experience. Do not shrink the existing dashboard into the device viewport.

Keep this work within the active Unit A task `ee21422a-4603-4ae1-822d-583fdd26c265`; Fleet production hardening remains downstream in `d8cca61b-aa40-41ee-a90e-410933afd8af`.

## Review findings

- Essential existing text is 7–12 CSS pixels, with secondary operator text as small as 6 pixels.
- Header and five-tab navigation reserve 126 pixels at a nominal 640×480 CSS viewport; instructions and a large PTT graphic compete with the answer.
- The 620-pixel responsive breakpoint misses a 640-pixel viewport and does not solve limited height.
- A failed status poll retains old readiness; the voice view does not independently know the connection is stale.
- The largest button-like graphic is not an interactive control. Physical PTT is the real input.
- USB detection, running services, and readiness markers are not proof of a successful voice session.

## Proposed interaction

Use one state-driven screen with a compact status strip, dominant content area, and instruction/action strip. Remove permanent Home/Voice/Radio/System/Setup tabs, hardware identifiers, and operator diagnostics from the everyday screen.

| State | Main content | Instruction or action |
|---|---|---|
| Ready | Ready to listen | Hold the side button to talk |
| Listening | Clear recording indicator and Listening | Release when finished |
| Processing | Preparing your answer; no fabricated progress percentage | Wait; do not imply cancellation exists |
| Answer | Answer first, readable full-width text; question secondary | Touch paging for long answers; physical PTT for another question |
| Capture error | Plain-language microphone problem | Check connection or retry only when prerequisites are available |
| Enrollment error | Local voice access needs setup | Refer to operator setup; no repeated blind retry |
| Disconnected | Device status unavailable | Reconnecting; never preserve an old Ready badge |

Use only measured runtime phases. The current pipeline exposes a combined processing phase, not live transcription or separate model progress. No live waveform without a real signal source. No speaker/playback button until implemented. Wheel support is optional until physical acceptance; touch must work independently.

## Initial layout targets

These are design targets, not validated physical dimensions or accessibility compliance claims.

- Design explicitly for 640×480, then verify actual Chromium CSS viewport and device pixel ratio.
- Start with a 48-pixel status strip, 344-pixel main region, and 88-pixel footer, including padding.
- State headings: 32–40 CSS pixels. Answer: 26–30 pixels. Instructions: 22–26 pixels. Secondary status: 18–20 pixels.
- Touch targets: at least 72×72 CSS pixels initially, with 12–16 pixel gaps.
- Use text plus icons, strong contrast, visible focus, and reduced-motion support. Do not use color alone for readiness.
- Paginate or scroll long answers without reducing text size. Preserve reading position across status refreshes; reset only for a new session.

## Implementation boundary

Prefer a dedicated local-only `/device` route in the existing app/build with scoped styling. Preserve browser administration separately; review local diagnostic-route compatibility before changing existing `/` behavior. Hosted `/device` must not expose local data.

Keep app and status services bound to loopback. Local-mode flags and CORS are not authentication. Do not introduce a public status proxy or LAN-wide unauthenticated admin service; status contains private transcripts and answers.

Change kiosk launch and readiness probes together. Verify successful content, not merely a redirect. Coordinate installation instructions and recovery behavior with the route change. This app contains `.openai/hosting.json`; implementation must follow its Sites build/hosting workflow without accidentally publishing private device data.

Status fetching should be abortable, single-flight, schema-validated, and resistant to out-of-order results. Bound/cache expensive probes. Preserve producer timestamps, session IDs, and health separately: voice/PTT state is event-driven, so an old idle event alone is not proof of failure. A fresh HTTP response alone is not proof the producer is healthy.

## Acceptance before kiosk rollout

1. Render every state at the measured device viewport; confirm no clipped essential controls or horizontal scrolling.
2. Read a short and long answer on the physical screen; test touch paging without the wheel.
3. Verify five physical hold/speak/release loops; do not count capture-only smoke tests as end-to-end success.
4. Test disconnected audio, enrollment denial, stopped producer, missing/malformed status, delayed polling, and recovery. Never show false readiness.
5. Confirm browser admin authentication and local/hosted route boundaries remain intact; no transcript exposure through a new network endpoint.
6. Reboot offline and verify kiosk startup, direct route reload, local assets, and preserved recovery access.

Keep the existing kiosk as rollback until these checks pass. This proposal changes no deployed interface.
