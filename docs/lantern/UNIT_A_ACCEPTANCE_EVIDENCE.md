# SAGE Lantern Unit A Acceptance Evidence

**Historical endpoint evidence:** Since 2026-09-27, A/B are single-LoRa Lamplighter/admin/knowledge/repeater support units. Their native SAGE stores are deleted and endpoint services retired. C–F are the real dual-LoRa/GPS/WiFi Orange Pi user devices. See [device roles](DEVICE_ROLES.md).

Use this file for the first canonical Unit A acceptance run. Replace `Pending` only with measured or directly observed evidence.

## Fresh Unit A status — 2026-09-07

The fresh reset invalidates earlier deployment acceptance as evidence for the
current installation. Earlier observations below remain historical, not proof
that a feature works now. Detailed artifacts and native session evidence are in
`UNIT_A_FRESH_RESET_PLAN.md`.

| Requirement | Current evidence | Still required |
| --- | --- | --- |
| Basic services and interface | Native v27 and runtime R7 installed; auth/status/camera/display restored; reviewed UI R6 deployment returns HTTP 200 | Physical kiosk refresh, unlock and touch-flow checks |
| Voice and photo answers | Owner confirms Ask works after UI server restoration and kiosk reload, then confirms lock/unlock/Ask passes. Resident-key patch deployed; backend readiness checks passed; prior microphone commissioning retained | Physical PTT and photo inference acceptance; measured latency breakdown (owner reports answers feel slow) |
| Private SAGE memory | Signed private identity/storage metadata preparation passed; latest completed answer explicitly reports history saving unconfirmed and owner sees no saved entry | Fix protected history saving, then verify attached photos, recall/resubmission, deprecation and interrupted-write tests |
| Mesh messaging | Fresh mesh configuration installed and service settings loaded; radio remains inactive | Reviewed activation; discovery, dictation, inbox/outbox, receipts, relay/replay and disconnected delivery with B |
| Public knowledge | No fresh public workflow acceptance; owner cannot find share action (source places it after answer inside scroll area) | Make action discoverable; separately verify enrolled proposal/review outcomes, private retention on rejection, corroboration/challenge/supersession, verified bundles and lookup ordering |
| Lock and sleep | Auth screen lock observed; encryption enabled but serving-vault keys remain resident | Endpoint revocation races, coordinated cryptographic relock, idle timings, real sleep/wake and fresh authentication |
| Fresh B provisioning | Camera DynamicUser/NSS portability fix has source tests; no new release or B installation | Matched reproducible release, independent identities, first-run naming/passcode/Wi-Fi flow and power-cycle recovery |
| Multi-unit resilience | No new RF or consensus evidence | Range/bandwidth/relay/sync tests; actual validator topology and later four-validator Byzantine-fault scenarios |

Do not replace consented microphone/camera tests with ready markers, infer
cryptographic lock from the screen, clone A's keys into B, or reuse recovery
journals as fresh-provisioning receipts. Owner approved the two-second local
microphone test on September 7; capture passed, but this is not speech recognition
or end-to-end voice acceptance.

Photo follow-up: actual voice status reports photo_ready=false and the live
llama-server has no --mmproj argument although the projector file is installed.
Camera hides its Ask action on that flag. Source now keeps the action visible
but disabled with an honest explanation until backend readiness is verified.
Photo activation changes voice commissioning inputs and requires a coordinated
drain/model transition plus reviewed recommissioning; do not force the flag or
rewrite previous capture receipts. Owner asked for brief local microphone
recheck consent. Unit A photo answering remains unaccepted.

## Incremental voice recovery — 2026-09-05

This is a successful single-session observation, not completion of the full acceptance matrix below.

- Restored USB enumeration of `303a:8000` SAGE Lantern Audio after operator connection troubleshooting.
- Fixed the voice-loop recorder stop handling; verified a real capture/stop returns a valid temporary WAV, then deleted the recording.
- Approved the existing private voice identity as an ordinary Member/Companion with clearance 1 and home domain `local-field-knowledge`, through the local signed CEREBRUM policy endpoint. No public Commons enrollment or Admin promotion.
- Confirmed signed embedding and hybrid retrieval pass with the voice identity itself.
- Observed a physical PTT session reach `complete` at 2026-09-05T00:52:44Z: STT 2326 ms, SAGE context 10 ms, LLM 3315 ms, total processing 5650 ms. Operator confirmed it worked well. Transcript/answer are intentionally omitted here.
- Five-loop acceptance, reboot/reconnect repetition, and physical small-screen usability remain open. The hardened activation script has mocked regression coverage but has not been rerun on the live device during the operator's working session.

Start each major run with `scripts/capture-lantern-evidence.sh evidence/<test-id>` and link the resulting directory below. Review the output before sharing it outside the project.

## Build identity

### Live viewfinder follow-up — 2026-09-05

- Operator confirmed the initial still-photo UI works and requested a live preview. Added explicit Camera opening → live viewfinder → Take photo (freeze displayed frame, stop camera) → Retake / Back. Icons accompany short labels; no reading/writing accessibility acceptance is implied.
- Backend POST `/preview` keeps exact loopback Host/Origin/custom-header checks and a single camera lock. Live JPEG frames are bounded to 2 MiB each with first-frame/interframe timeouts, 60-second session cap, and subprocess cleanup on disconnect. Separate configuration output from the binary stream.
- 22 backend tests passed locally/on ARM64; 17 app tests, lint and staged ARM64 build passed. Real service stream decoded three 1280×720 frames locally at 3.51, 3.71 and 3.91 seconds from request start. No frames written to disk or uploaded. No `v4l2-ctl` process remained after the client closed.
- Live-viewfinder UI deployed; photo interpretation remains disabled. End-to-end touchscreen viewfinder/freeze/retake is pending operator confirmation. UI rollback at `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-live-20260905`; camera server rollback suffix `.before-live-20260905` under `/usr/local/libexec`.

### Kiosk fullscreen correction — 2026-09-05

- Operator photo showed boot-console text around the app. X11 inspection confirmed root 640×480 but Chromium 620×460 at (10,10), with no window manager registered.
- `/run/user/1000/sage-lantern-kwin.log` reported `Unknown option 'no-global-shortcuts'`. Removed the unsupported KWin argument from the session script, clear the root background, and wait briefly for WM registration. Added display-sized Chromium window fallback at (0,0).
- Started KWin with supported `--replace` in the existing X session. X11 subsequently reports Chromium 640×480 at (0,0), border 0, `_NET_WM_STATE_FULLSCREEN` and focused. UI and voice services remain active; no reboot performed.
- Installed corrected `/usr/local/bin/sage-lantern-session` and kiosk launcher. Previous scripts retained with `.before-fullscreen-20260905` suffixes. Reboot persistence is configured, not yet physically retested.

### Incremental USB camera enumeration — 2026-09-05

- Operator connected an existing Logitech C270 (`046d:0825`); detected by `lsusb` and `uvcvideo` on kernel 6.1.84.
- Stable capture path `/dev/v4l/by-id/usb-046d_0825_5E4BEE10-video-index0` resolved to `/dev/video1`; index1 is the companion metadata node. Do not hard-code video numbering.
- `v4l2-ctl --info --list-formats-ext` succeeded as radxa. Advertised YUYV/MJPEG modes include 1280×720. This is capability enumeration, not a verified frame or image-quality test.
- Camera also enumerates an ALSA microphone; original `Audio` SAGE capture card remains present and voice-loop service active. Do not change capture selection to the webcam implicitly.
- No photo taken, stored or uploaded. Frame capture, permissions under the future camera service, preview/retake UI and vision-model validation remain pending.

#### Follow-up: authorized single-frame capture

- After the operator said ready, captured one MJPEG frame at 1280×720 with a 15-second timeout and 15 warm-up frames skipped. Stream ended; no continuous recording left running.
- `/tmp/lantern-camera-test.jpg` on Unit A is 37,590 bytes, mode 0600. `file` identifies baseline JPEG, 1280×720, three components; V4L2 confirms MJPG format. Image stayed on Unit A and was not uploaded or added to SAGE.
- Full pixel decode was not verified: Pillow is absent from both system Python and the runtime venv. No packages were installed. Visual sharpness/exposure and model interpretation remain untested.
- Voice service remained active. Temporary photo retained locally for the next explicit preview/decode step; remove after testing. Earlier no-capture note describes the enumeration stage only.

### Incremental device UI deployment — 2026-09-05

#### Camera-preview follow-up

- Previously captured test JPEG fully decoded on Unit A using system Python GI/GdkPixbuf: 1280×720, three channels. No transfer/upload was needed. This supersedes the earlier missing-Pillow decode limitation; visual focus/exposure still needs operator assessment.
- Added and deployed isolated `sage-lantern-camera.service` (dynamic user, video access only) on 127.0.0.1:3198, with on-demand POST capture, fixed stable camera path, capture timeout, bounded concurrency, temporary-file cleanup and exact Host/Origin/custom-header checks.
- 13 mocked camera tests passed locally and on ARM64; 13 app tests, lint and staged ARM64 build passed. Live health reports camera present / vision not ready; untrusted-origin capture returned 403, allowed preflight returned 200. No new photo was taken during deployment checks.
- Deployed touchscreen Camera / Take photo / Retake / Discard & back; `/device` returned 200 and dedicated Chromium refreshed. Photo-answer functionality remains explicitly unavailable. User must verify the new service-to-browser capture path by tapping Take photo.
- UI backup: `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-camera-20260905`. Voice, UI and camera services active after UI-only restart. Camera port is not part of the existing external admin tunnel.
- Voice/camera arbitration is currently UI coordination plus a best-effort readable-state guard, not a shared backend session controller. Physical PTT during capture may overlap briefly; no second voice recorder is introduced. Central ownership is a prerequisite for the proposed soft Talk controls.

- Deployed dedicated `/device` and separate read-only `/admin`; root returns 307 to `/device`. Both pages and all discovered initial device JS/CSS assets returned HTTP 200 at approximately 01:33 UTC.
- Both services remain bound to 127.0.0.1 (3100 UI, 3199 status). No cloud publication or LAN listener added.
- Local and staged ARM64 app validation: 13 tests passed, ESLint passed, production build passed. Source hashes matched after deployment.
- Screen source SHA-256: `4644a3249bcf1797d2e85dd9375742cfac2d248e5f7f432888de5d687eab4ac6`; state helper: `98abc83679013d5d5edc2e25546ad0e55cd936ea3b78957c6018a544e31a5e3f`.
- Restarted only UI service and the dedicated kiosk Chromium process. Voice service retained its 08:46:06 +08 activation timestamp; audio bridge/PTT ready, core and voice services active. Existing `runtime_gate` failed status remains a separate acceptance issue, not repaired by this UI update.
- Previous complete app retained at `/home/radxa/sage-commons/apps/lantern-fleet-admin.before-ui-20260905`; launcher backup `/usr/local/bin/sage-lantern-kiosk.before-ui-20260905`. Rollback requires stopping only the UI service, moving the new app aside and restoring these backups, then starting UI and refreshing dedicated Chromium.
- New launcher targets `/device` on future starts; existing launcher restarted Chromium with root, whose redirect serves `/device`.
- Physical touch/readability, client hydration and fresh end-to-end PTT testing on this UI remain pending operator confirmation. No five-loop, reboot or general production acceptance claim.

| Field | Value |
|---|---|
| Test ID | Pending |
| Date/operator | Pending |
| ROCK board/revision | Pending |
| OS image/version | Pending |
| Application commit | Pending |
| SAGE version/commit | Pending |
| Ordinary SAGE agent ID | Pending |
| Audio firmware/interface | Pending |
| Heltec board/radio variant | Pending |
| MeshCore firmware | Pending |
| Power source/cable/port | Pending |

## Platform gates

| Gate | Result | Evidence |
|---|---|---|
| Five consecutive cold boots | Pending | Pending |
| NVMe capacity and health | Pending | Pending |
| Idle temperature and fan operation | Pending | Pending |
| Sustained-load temperature/throttling/stability | Pending | Pending |
| Display and rotated touch | Pending | Pending |
| Audio capture/playback/echo observation | Pending | Pending |
| PTT and wheel event tests | Pending | Pending |

## Offline voice proof

Acceptance conditions:

- cloud/internet inference is unavailable;
- one pre-seeded SAGE context item is fixed and identified;
- one session ID correlates the UI and every software boundary;
- five consecutive loops complete;
- process and peripheral recovery are demonstrated.

| Run | Session ID | Context ID/hash | Capture ms | STT ms | Agent + SAGE ms | TTS ms | Total ms | Result/evidence |
|---:|---|---|---:|---:|---:|---:|---:|---|
| 1 | Pending | Pending | — | — | — | — | — | Pending |
| 2 | Pending | Pending | — | — | — | — | — | Pending |
| 3 | Pending | Pending | — | — | — | — | — | Pending |
| 4 | Pending | Pending | — | — | — | — | — | Pending |
| 5 | Pending | Pending | — | — | — | — | — | Pending |

## Recovery evidence

| Test | Expected state | Observed result | Evidence |
|---|---|---|---|
| Restart voice/application process | Returns to idle without reimage | Pending | Pending |
| Disconnect/reconnect audio peripheral | Visible degraded state, then recovery | Pending | Pending |
| Disconnect/reconnect Heltec | Visible degraded state, stable re-addressing | Pending | Pending |
| Reboot with changed USB enumeration order | Stable identifiers still resolve | Pending | Pending |

## Final result

```text
Result: PENDING
Known defects:
Follow-up:
Evidence bundle location:
```
