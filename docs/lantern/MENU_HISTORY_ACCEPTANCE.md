# Unit A compact menu and local history — 2026-09-05

Latest toolbar refinement: removed main-answer up/down buttons in favor of
native finger scrolling, preserving separate question/answer scroll positions.
Camera is now icon-only at every width, with a blue surface, white icon and
accessible name. My question has more width, a blue outlined surface and a
clear Back to answer state; Ask remains amber. SAGE and Lantern now share
the same compact type size/weight/spacing beside the native lantern icon.
This remains a question/answer toggle, not transcript editing. No Clear,
About, status-chart or photo-answer feature is added by this styling update.

Follow-up UI refinement: Camera moved out of Menu beside the renamed Ask
button; idle/retry guidance uses Ask consistently. Added an inline SVG lantern
mark using the existing amber accent. Menu now holds only History and Device
status. Camera returns focus to its own shortcut; recording/busy navigation
guards remain. At narrow widths Camera keeps its icon and accessible name.
No backend, history-consent, camera-capture or model behavior changed.

Header follow-up: LoRa signal shortcut sits immediately beside Menu and opens
Device status. Its crossed outline bars and dash explicitly mean unmeasured,
not poor reception, link verification or a live RSSI estimate. Removed the
generic device hover background which had higher specificity than the amber
primary-action rules. Hover no longer changes action colors; actual pressing
uses brief brightness feedback and keyboard focus remains visible. A CSS
regression test guards the hover-color contract.

## Scope

Replace the Radio shortcut with Menu → History / Camera / Device status.
The main answer toolbar puts My question, up/down and Talk in one row,
removing the second control row. The slide-out panel has large labelled
targets, Close, backdrop dismissal, focus containment and reduced-motion
support. No swipe or keyboard is required. Recording and uncertain touch
requests lock navigation; the shared physical/touch recording owner remains.

History defaults off. Explicit confirmation enables future successful text
answers only, stored under `/var/lib/sage-lantern/history/history.json` with
0700 directory / 0600 file permissions. Limits: 100 items, 30 days, 4MiB.
Retention is enforced on access/save and periodic maintenance, not while the
device is powered off. Atomic writes and consent generations prevent an
in-flight answer from repopulating history after clear or disable. Disable
also deletes entries. No audio, photo, SAGE-write or public-publication path
is introduced. This is shared-device history, not authenticated user memory,
and deletion does not securely erase media or clear the latest main answer.

Device status displays existing SAGE/model/voice service states, reported
network connection, temperature, storage and uptime. LoRa link, RSSI/SNR,
last message, battery and synchronization metrics are explicitly unavailable
where no measurement exists. USB detection is not link or delivery proof.

Answer updates are grouped into three initial complete words, then five-word
groups, with punctuation/80-character/approximately 1.2-second pending-text
flushes. This can add presentation delay and must be assessed by the operator;
it does not increase model throughput. Completion flushes remaining text.
The latest pre-rollout session recorded 5.27s processing-to-first-model-text,
2.82s model-to-first-text, and 40.22s total. These exclude screen paint and
are not a controlled comparison. No model or camera changes in this release.

## Validation and acceptance boundaries

- 61 backend tests passed locally and on Unit A, including history consent,
  persistence, retention, deletion, disk failures, HTTP boundaries and idle
  batch flushing. 23 UI contract tests passed; lint and production build pass.
- Full TypeScript check still reports errors in unrelated live-enrollment and
  observation-contract code; no device/history component errors were reported.
- Physical menu/history usability, new voice-loop regression and perceived
  batching improvement require operator testing. No browser visual QA or
  reboot persistence acceptance is claimed from automated checks alone.

## Release recovery

Deployed on Unit A: UI and voice controller restarted; model and private SAGE
remained running. All four services active, /device HTTP 200, voice health
touch_ready=true and idle. Live history checks passed: hostile Origin 403,
non-boolean setting 400, initial off/empty, enable/clear/disable cycle with no
recordings. Final setting is off with zero entries. Live history ownership and
permissions verified as sage-lantern, 0700 directory and 0600 file. Dedicated
kiosk refresh requested. The reviewed UI build and 23 tests also passed natively.

Rollback code paths: `/opt/sage-lantern/before-menu-history-20260905` for the
voice script/unit and
`/home/radxa/sage-commons/apps/lantern-fleet-admin.before-menu-history-20260905`
for the UI. No user history is included in those release backups.

Keep the previous voice script/unit and UI together for rollback. The new
history API is not available under the previous voice controller. Preserve
any real history data during code rollback; do not back it up into release
archives or silently change its consent setting. Returning to older software
does not erase previously saved history, and older software does not enforce
the new retention policy while it is running.
