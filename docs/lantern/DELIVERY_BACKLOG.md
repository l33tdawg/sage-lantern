# SAGE Lantern delivery backlog

**Current role/task map:** 2026-09-27 — [Device roles](DEVICE_ROLES.md) and
[SAGE task map](SAGE_TASKS_20260927.md) supersede the execution order below.
A/B are single-LoRa Lamplighter/admin/knowledge/repeater support units. C–F are
real Orange Pi 5 Plus user Lanterns with dual LoRa, GPS and WiFi. A/B endpoint
completion is no longer a gate. Their public seeds are active and native personal
SAGE stores deleted; LoRa/repeater and C–F acceptance remain open.

**Historical checkpoint date:** 2026-09-12
**Purpose:** One operational backlog for the Lantern device, Fleet Admin, Fleet
Authority, radio bring-up, and later Commons federation work.

**Current model lane (2026-10-18):** Unit C production chat is the V79.6 3B
token engine with the final-reviewed response guards; see
[3B_MODEL_PRODUCTION.md](3B_MODEL_PRODUCTION.md). The live server is
`e80bd651…` and the guards are `64609722…`. The 48-request device comparison
was run with guards off; guard-on streaming and receipt/policy checks are
separate evidence. This does not close the broader model-quality,
long-conversation, field, power or multi-unit acceptance items below.

The runtime action boundary is now tracked in
`software/runtime/src/sage_lantern/action_guard.py` and
`ConversationService.dispatch_save()`: canonical request-derived call, one
admission-checked SAGE proposal, raw receipt, and no automatic retry on an
uncertain outcome. The product UI/HTTP route still needs to call it; a
non-self owner grant also needs an owner-bound executor before it can execute.
The wheel is installed in Unit C's production venv and the package-level
verification passed.

## Historical consolidated execution order (September 12)

**Messaging resume checkpoint (2026-09-12):** Fresh-session execution task
`e24e895c-3210-4725-b563-d1e7b416ef97` supersedes the session record
`a3ecb114-e1df-447c-a7a7-7872662894d6`, whose uninstalled radio-meter and drain
rollout it carries forward. B's durable receive slot — interrupted since
9 September — was reconciled through the new audited `mesh_receive_recovery`
module (audit `64ef6431-dcc8-596a-80ad-a19b1479bd1e`, slot back to `idle` at
revision 74 with its UUID unchanged), and B now answers
`messaging: {"available": true, "reason": "ready"}` with its provider
initialized. A remains blocked: every provider attempt fails inside
`meshcore_transport.get_contacts` while the health path still logs
`radio_measurement`, so A's community endpoint returns
`community_unavailable` (the on-screen "radio not ready"). The first repair step
is the reviewed cold-broker restart; if it repeats, dump the raw `0x04`
contact-list response with the daemon stopped. Owner-PIN sessions need a roughly
six-second presence heartbeat, which is why a plain API unlock lapses. Staged on
both units under `/root/lantern-contact-import/`: the owner-session driver and
the reviewed contact operator tool for the owner-approved card refresh and the
first real A→B / B→A message test. Source-only from the same session: one shared
device header on every screen and a small x over the signal meter when no
verified radio chip is present. Evidence: `work/messages-ab-20260912/`,
`work/device-header-20260912/`, `work/radio-signal-x-20260912/`.

**Current SAGE consolidation (2026-09-11):** [Task map and completed milestones](SAGE_TASKS_20260911.md)
records **12 open tasks: eight in progress and four planned**. The completed A
conversation UI deployment is recorded separately as done. Stale recording and
answer-description tasks were replaced only after their updated successors were
committed; all unfinished requirements remain linked.

A now has transcript review and local keyboard editing, clear recording feedback,
continuous conversation/scroll behavior, cached conversation lists with transient
refresh recovery, balanced controls, floating review actions and the compact
rotating waiting card. Latest verified UI archive:
`d765b2653894654f3d12d5d40e676855a98c5dba572c5101cb892a02f33f9e3c`.
Physical voice/touch acceptance and true live partial transcription remain open
under `8b5c213f-9af1-429a-b6d5-8bdc3e436eaf`.

Complete-answer and two-bubble follow-up work is a **local, unqualified prototype**
under `140f07f5-5fc6-42d2-9e61-6e5796b1aee1`; the experimental policy stays off and
has not been deployed. Hoya correction drift and missing saved follow-ups remain
unresolved. Browser cache/retry is not a backend save-reliability fix.

Performance/training remains in canonical task `00b1dbf4-ea56-4f7f-9960-780a0ad5f6f4`;
initial-turn factual grounding remains a separate quality gate. The latest lab
studies are completed but unqualified. Four **6.5-inch portrait** panels are ordered,
expected next week per owner; exact module specifications and physical acceptance
remain under the hardware parent. The older chronological entries below are
historical checkpoints; the current task map controls status and remaining work.

**SSH management checkpoint (2026-09-10):** Shared-key task
`a1221b10-a1ee-4af8-ac40-9be4b94cb44f` is planned, waiting for B access; it supersedes
the stale key-creation checkpoint `1e2520b3-6937-4396-8015-36ef64ad0991`. A accepts the dedicated
management public key; `ssh lantern-a` passed a fresh key-only login with
existing access and all six service identities preserved. B's verified host
pin and `lantern-b` alias are ready, but installation needs B's existing login
credential. The private key remains on the management Mac. See
[SSH_MANAGEMENT.md](SSH_MANAGEMENT.md) for coverage, provisioning and rotation.

**SAGE backlog consolidation (2026-09-09):** Current messaging/directory/public-sharing task: `f66a2503-8da6-48a7-ad50-ebe017e05a45`; current A/B delivery acceptance checkpoint: `324b2365-32b6-4e41-bb17-8d991abe0691`. These replace four overlapping/stale tasks, retired as superseded rather than completed. Unmet requirements remain in the linked replacements. Separate White Pages, media encryption, power recovery, later Fleet hardening and optional online research tasks remain open. Immediate order: local mailbox/backend recovery → matching Messages/People deployment and approved signed-card refresh → real A/B delivery/receipt proof → public-sharing acceptance. Built UI is not deployed functionality.

### Current acceptance checklist

**Messages UX and directory checkpoint:** User approved making both demo units discoverable and refreshing signed contact cards ("yes go ahead"); execution remains pending, and this does not authorize publishing questions or photos. New source changes add a People entry directly in Messages, separate folder loading from the sequential readiness request, preserve visible rows during Refresh, and distinguish failed loading from an empty folder. These changes are not deployed. The broker still initializes the messaging provider before reading local folders, so receiver recovery remains a backend blocker; UI changes alone do not prove fast or working messaging.

**Pending build checkpoint:** Linked-directory UI (including compact navigation icons) builds successfully; earlier artifact `/private/tmp/lantern-directory-linked-ui.tgz` predates the latest Messages changes. It requires the implemented but undeployed `contacts/radio` API. Do not replace the currently working UI with this artifact before its backend is installed and startup references reconciled.

**Directory acceptance finding:** Actual B UI browsing shows A's received radio key with fresh age, then People loads successfully but has no current shared contacts. Root diagnostic verified the stored contact's signature and reported `findable=false`, `messaging_allowed=true`, expired at epoch `1788915552`; no names or private message contents were exported. This is a consent/expiry gate, not a radio-neighbor failure. Asked user whether both demo units may become discoverable and have signed cards refreshed; do not silently override private/not-findable state. Found and corrected source-only oversized Find people icon/layout using explicit 25px icon size and spaced action buttons; that CSS change is not activated yet.

**Actual-screen radio verification:** A's Not ready state was reproduced through X11 capture. Earliest transport failure at 18:05:09 followed the lock/reload window; subsequent status requests retained a poisoned health-only coordinator. Both units now carry guarded health-only reopening: require current authorization, no messaging provider, verified scheduler closure, clear old observations, then reopen through the existing identity/fence checks. Active messaging uncertainty is not bypassed. 42 daemon tests passed. Matching startup references were reconciled on both; no reboot or kiosk reload for this repair. Both audio readiness checks returned true. Actual Radio overview captures after deployment show A Connected / 1 radio heard / RX22 / -35 dBm and B Connected / 1 radio heard / RX64 / -33 dBm. This is current on-device evidence, not merely source or service status.

**UI activation update:** User explicitly requested “activate both”. Installed the staged compatible UI archive SHA-256 `6f6305031eba30945d9a5dcaba6408b9c7b2acfdeb4290fca0a2c872217c1b31` on A/B, restarted only Fleet Admin, confirmed `/device` HTTP 200 and requested one kiosk reload each. Prior UI backups: A `dist-before-nearby-1788948458929728048`, B `dist-before-nearby-1788948461234579342` under `/opt/sage-lantern/fleet-admin`. This activates shared home/dashboard signal sampling and the basic People view using deployed `contacts/list`; it does not include the later enhanced `contacts/radio` endpoint or full-key-linked UI. Screen interaction verification remains pending after unlock. No device reboot, key/private-record changes, or audio/radio service restart in this activation.

- [x] Actual MeshCore telemetry and advertisement discovery deployed on A/B. Latest post-isolation evidence: A RX 3, B key heard at -31 dBm; B RX 51, A key heard at -31 dBm. These are last-packet radio measurements, not direct-link or per-person measurements.
- [x] Directory reads separated from messaging receiver initialization on A/B. Interrupted receive state remains preserved; messaging admission refusal no longer needs to poison the radio owner. Audio readiness verified on both following deployment.
- [ ] Activate the shared home/dashboard signal cache UI and verify repeated Nearby → Home navigation on B, including genuine packet freshness and expiry. Staged archive alone is not activation.
- [ ] Deploy the enhanced `contacts/radio` endpoint with its matching UI and boot references. Current basic contact-list API remains backward-compatible. Verify exact full-key matching and unavailable/expired/ambiguous contacts on actual devices.
- [ ] Verify on-device dashboard layout and discovered-node details. No forced kiosk refresh while activation permission is pending.
- [ ] Verify boot recovery after a coordinated reboot; updated reference files are not reboot proof.
- [ ] Separately resolve interrupted message reception while retaining its unknown historical outcome. Do not claim end-to-end messages, delivery/read receipts or public sharing accepted from radio sightings alone.

**Immediate gate:** screen activation can cause a fresh unlock. Await explicit go-ahead for one coordinated UI activation rather than triggering repeated walks to the devices. No further source-only feature expansion is needed before that check.

### Directory/radio isolation deployment record

**Deployment update:** B was subsequently observed unlocked. The daemon isolation fix is now installed on B (backup `/var/backups/lantern-radio-health-1788947404558816401`, reconciled boot baseline `/var/lib/sage-lantern-maintained-audio/0164c588-e030-4e2b-bba1-a9b4efa57cf2`) and A (backup `/var/backups/lantern-radio-health-1788947564933622205`, baseline `/var/lib/sage-lantern-maintained-audio/dedbbce8-f669-41ba-a487-8e9e086e91ad`). Both guarded recovery and startup-reference updates succeeded without reboot or kiosk refresh. B audio ready; live B RX grew from 50 to 51 and identified A's exact key, last-packet RSSI -32 then -31 dBm. UI cache update and enhanced `contacts/radio` endpoint remain undeployed; interrupted-message outcome recovery remains unfinished and must not be called delivered or replayed.

- `HardwareRuntime` now reads verified directory contacts through the existing exclusive radio scheduler without initializing message reception. Full-key inventory binding, signature/current consent validation and session guards remain required.
- An already `interrupted` receive slot rejects messaging startup before closing the radio scheduler or setting the broker uncertain. It is neither cleared nor replayed. An unresolved `receiving` slot still fails closed. This isolates the radio foundation, not a claim that interrupted message delivery has recovered.
- All 41 daemon tests passed, including a real-SDK/synthetic-radio regression proving repeated directory reads survive rejected messaging startup with the interrupted row unchanged and no transmissions.
- B deployment refused at its initial unlock check, before backup, stop or installation: B had locked. Candidate files and matching startup reconciliation are staged under `/home/radxa/lantern-radio-health-dev`; run `deploy-lantern-radio-health.py --daemon-only` followed by `reconcile-lantern-b-directory-startup.py` only during an authorized unlocked session. Source archive SHA-256 `cef9db32ca058e8c6737b80588c5754c8618afd5de8a5ff1a44ecdf139835aac`. No deployment or hardware-verification claim for this fix yet.

### Latest B recovery — no reboot or kiosk refresh

### A startup recurrence — September 9, 17:30 local

- Post-recovery RF verification found B's radio broker remains failed at request admission despite audio readiness. Earliest actual failure: September 9 08:49:23 UTC, `MeshRuntime.from_config`, immediately after successful B→A key sighting at 08:49:16. Root state-only workflow inspection confirms receiver slot `interrupted`. Runtime initialization accepts only idle/captured (line 153); provider failure marks the shared broker uncertain and subsequent radio/status calls fail. A currently transmits advertisements but RX remains zero after its reboot. Prior cross-room RF evidence is historical, not present health.
- Remaining root repair: interrupted-receiver recovery must preserve the unknown prior receive outcome without replaying, deleting or claiming delivery; opening directory must not permanently disable radio health. Do not merely restart B repeatedly or clear its receiver slot. Existing receiver test explicitly rejects replay of an interrupted capture. This is separate from—and not solved by—the UI navigation cache fix.

- A rebooted at approximately 17:03 and post-unlock startup failed at 17:30. Approved diagnostic identified exact refusal in `collector.runtime_sources`: its old compact-runtime archive's file set no longer matched the installed radio runtime. Model, broker and voice were inactive; private SAGE remained active.
- Restored A using guarded recovery without reboot/kiosk refresh. Added the same retained signed-contact recovery support already installed on B; preserved A's other installed code, diagnostic logging and original radio firmware. Latest recovery backup `/var/backups/lantern-radio-health-1788946559146435836`.
- Compared installed application sources against current source; reviewed six differences, excluding undeployed directory changes. Published an exact A source archive `0ef48243d2351fea35db7d4714bc3089d9ae032f394f4dd177bf939bb0a10ae8`, retaining byte/file-set verification rather than bypassing it. SDK hashes match the already reviewed SDK variant. Model argv/files and device identity remain checked against A's prior baseline.
- Actual lifetime and startup-policy reconciliation succeeded at `/var/lib/sage-lantern-maintained-audio/e2d79691-05d6-4545-ba5d-1d92ca57297d`; includes validated interrupted-receiver recovery. No reboot acceptance claim. B was not changed by this A-only recovery.

- Radio/contact association implementation now adds backward-compatible `contacts/radio`, returning only the existing verified/current contact path's full radio key, chain, agent and expiry. Nearby matches exact full keys with unexpired unique cards and ages sightings locally; identical names, key prefixes, expired consent and ambiguous bindings do not match. No per-contact RSSI is inferred from the radio-wide measurement. Source tests: 21 community mesh tests and focused directory parser/matching test passed. This backend/UI pair is not installed on hardware yet; previously staged archive remains the older compatible UI. Awaiting permission for the screen activation needed to verify B's reported meter issue without violating the no-forced-unlock request.

- Built Nearby → Find people using the existing authenticated `contacts/list` service, touch keyboard, explicit loaded-list search scope, failure/retry handling and session-revocation clearing. Contacts are explicitly not online/range evidence. Combined UI archive staged on A and B as `/home/radxa/lantern-nearby-ui.tgz`; neither running kiosk refreshed. Actual UI activation, hardware browsing acceptance and full-key radio-to-contact association remain pending.

- Subsequent live logs verify renewed bidirectional RF discovery: B heard A's full radio key at RX 23 / RSSI -25 dBm; A heard B's full key at RX 95 / RSSI -33 dBm. These are radio-wide last-packet measurements, not per-person or direct-link proof.
- Home-meter navigation correction built: dashboard and home now share session-scoped, packet-counter-qualified signal observations. Navigation does not discard readings or extend their 90-second expiry; counter reset and session revocation still clear them. Three focused tests and production build passed. B UI activation remains pending to avoid another forced unlock; no claim that the currently loaded kiosk has this fix.

- Confirmed the September 9 startup refusal at `settled_inventory`: the receiver slot was not idle. The existing broker preserves interrupted reception rather than claiming delivery; startup now accepts a validated `interrupted` slot without changing it, replaying it, or claiming a historical outcome. Other unsettled receiver states remain refused.
- Restored B model, broker and voice in the existing unlocked session. Recovery backup: `/var/backups/lantern-radio-health-1788942484574307651`.
- Installed the scoped recovery helper and matching adapter pin. Actual lifetime/policy reconciliation completed at `/var/lib/sage-lantern-maintained-audio/036e20a8-9e12-48a0-a824-36fe12690d3d`. Focused recovery tests: 79 passed.
- No reboot acceptance claim: do not reboot or refresh the kiosk again in this session. A was not changed. Directory integration and real device-to-device messaging acceptance remain unfinished.

**Directory-record recovery fix deployed to B:** Unsupported rows were the signed
mesh contact index and contact record, not corrupt user history. Recovery now
uses the registry's existing signature checks and verifies complete index/member
links, retaining records without renewing consent or claiming live discovery.
Four focused tests pass; three broader owner-ledger tests fail with the older
local SDK's missing contact-issuance schema, so the broader suite is not green.
Runtime backup `1788941550058459050`; reconciled boot baseline/lifetime
`/var/lib/sage-lantern-maintained-audio/245a628f-fe6a-4641-8fcf-5ccab3a32569`.
Source ZIP SHA-256 `9c2a317ae7c3779c023ef216f7c8ce730b2fea08f2c6a3650b96cfa699140f7b`.
B rebooted to boot ID `93b1f3a6-5d93-4b4f-9191-498d9a23656f`; post-unlock
automatic audio/radio recovery is still awaiting verification. A unchanged.

**Approved B recovery follow-up:** User explicitly approved recovery diagnostics
and service changes. Installed collector preflight confirmed the initial active
model violated recovery's inactive-service check. Removed the incorrect model
Requires/After dependency on B and disabled independent model boot enablement.
With the model stopped, preflight advances to saved workflow inspection and
fails at `maintenance_owner_ledgers.py:222` (unsupported record); exact schema
still needs diagnosis. Do not delete records or treat them as settled.
Restored B manually via guarded development recovery, backup
`1788940432507037980`, without touching A. This creates a new live lifetime;
next-boot baseline/lifetime must be reconciled again after the workflow fix.

**Latest correction:** B's post-update reboot did not recover audio/radio after
unlock. Adding an LLM dependency was incorrect for automatic recovery: the
collector and recovery helper require an initially inactive model and start it
themselves. The local service dependency change is reverted; B still needs that
correction applied. Manual recovery's active-model prerequisite must not be
confused with the automatic cold-boot sequence. A deeper collector diagnostic
was blocked by execution review because it may inspect private workflows or
affect services. Explicit user approval for that diagnostic/recovery is pending;
unlock confirmation alone is not approval. No automatic reboot acceptance.

### Cross-room hardware checkpoint, September 9

After B moved to another room and its failed cold-boot runtime was recovered,
both devices are unlocked and root-authorized audio readiness is true.
Fresh radio measurements across a normal advertisement interval:
A received count 56 → 58, RSSI -26 → -27 → -31 dBm;
B received count 15 → 17, RSSI -26 → -28 → -30 dBm.
Each reports the other's pinned public radio key. Compared with near-zero
same-room readings, this supports distance-responsive measurements, not calibrated
accuracy, a firmware-only fix, or range/message-delivery acceptance. A still uses
stock firmware, B r2. No further firmware flash is justified by this observation.

B rebooted during relocation. The microphone enumerated, but the startup adapter
exited with ValueError and both runtime services remained inactive. Its LLM unit
was disabled; safe recovery requires an active LLM. Enabled and started that unit,
then completed guarded broker/voice recovery; six audio checks passed. The exact
adapter refusal and full next-boot acceptance remain open; do not equate successful
manual recovery with repaired unattended startup.

Read-only follow-up confirms B's startup policy still pins an older runtime
wheel: 26 installed runtime Python files differ. The adapter checks exact wheel
contents before recovery, so this is a deterministic additional boot blocker,
not a microphone or warm-up problem. Do not disable integrity checks or bless
arbitrary installed files. Reconcile the explicitly reviewed development changes
with startup's pinned inputs before the next reboot test. Source startup unit
now declares the LLM dependency required by recovery; deployment is pending.

Follow-up: B's 105 installed runtime modules were hash-compared against local
source. Only `mesh_daemon.py` differed: its guarded contact-import path initializes
the provider before importing a verified card. That existing deployed change is
now retained in local source. B's startup systemd unit now explicitly Requires/
After the LLM, with the previous unit backed up and daemon-reload only. The old
wheel/baseline pins remain unresolved; no restart or reboot acceptance is claimed.

Prepared a source-only verification ZIP from the matching local runtime, SHA-256
`b7f916406f7bee242a320878f951c3e5cb7878587a4768c5237d0e095f6ad898`.
Collector/coordinator source admits exactly this digest while retaining complete
installed-file-set and byte checks; adapter pins the updated collector. Focused
collector/adapter tests: 82 passing. Inputs are staged in B's development folder,
not activated. Device-specific baseline and lifetime reconciliation remain pending.

B startup pins are now reconciled and activated, without stopping services.
Backup/new baseline: `/var/lib/sage-lantern-maintained-audio/5052434d-71a6-493b-92bc-819858762bbf`.
Retained the actual current complete bootstrap record and checked its evidence
against the active broker/voice processes. Exact runtime source snapshot, SDK
contact/correction schema additions, radio journal drop-in, actor, model files
and model arguments were checked. Operational audio was true before and after.
The current reviewed recovery helper replaces B's older helper; previous files
remain backed up. Next reboot is still unverified; A's baseline is not updated.

Latest radio checkpoint is from direct A/B deployment and device logs on September 9.
Older capability rows below retain their original acceptance limits; they do not
identify the latest runtime artifacts.

1. Finish the radio foundation before messaging: actual telemetry, over-air
   discovery, measured signal and discovered-node browsing. Large-card dashboards
   are deployed and owner-confirmed. Periodic zero-hop advertisements are now
   deployed on A/B while the unlocked UI polls; location-sharing policies suppress
   transmission. B has heard A's exact public radio key at **-38 dBm / 13.5 dB SNR**.
   A also heard B's exact key, establishing bidirectional advertisement reception.
   Subsequent firmware RSSI values are zero (including after enabling packet-log
   collection); do not treat these as maximum strength. The UI rejects nonnegative
   values for bars. Packet counts and SNR are observed, but reliable continuing
   RSSI measurement remains open. Saved
   contacts are not live neighbors; advertisements are not verified people.
   Owner-reported 11 September: the main-screen meter did not appear to update.
   Root cause found in the host runtime: the per-packet RF log frame
   (`PUSH_CODE_LOG_RX_DATA`, emitted for every received packet with the RSSI
   sampled immediately after reception) was discarded unless it landed inside
   the health exchange, so the meter was fed the firmware's zero instead.
   Fixed and locally tested in `meshcore_transport.py` plus the meter's
   unmeasured state; **not installed** — rollout needs the maintenance/drain
   procedure and physical acceptance. See `work/radio-meter-rssi-20260911/`.
   Advertisements remain tied to the unlocked UI, so a locked unit is silent and
   the other meter correctly reports no recent measurement.
   Owner decision 12 September: the meter reports measured link quality from
   per-packet **SNR** (LoRa demodulates below 0 dB), with RSSI shown beside it
   when the firmware reports one, and a locked unit now sends the same bounded
   **zero-hop, location-free** advertisement every 60 s through the mesh daemon
   so a peer keeps a real measurement (firmware location policy still
   suppresses it; the radio is never opened or recovered for a beacon). Both
   changes are local only and inherit the same uninstalled rollout.
2. Resolve the runtime provider admission guard blocking **Messages on both A
   and B despite being unlocked**. Root-cause investigation remains active in
   the parent workstream; this checkpoint does not claim a fix. Messaging,
   dispatch and delivery receipts are enabled, but messaging is **not usable**.
3. After admission is restored, verify a real A-to-B and B-to-A private message,
   application delivery receipts, explicit read receipts, and replay/offline/
   lock behaviour. **No real message exchange has been verified.**
4. Fix remaining listener-check contention and accept persistent startup on the
   latest installs with physical reboot, unlock, audio and Ask checks. Both
   services start, but B still logs intermittent listener-check contention;
   stable audio and reboot acceptance remain open.
5. Complete protected history/photo and correction acceptance, public proposal/
   verified acceptance/cache workflows, then remaining power/onboarding,
   discovery and relay/range/sync gates. Neither device is fully accepted.

B's radio-startup blocker was an interrupted durable receive slot. Startup now
preserves its operation ID as `interrupted`, rather than deleting it, asserting
delivery, or automatically replaying it. Radio telemetry can start; explicit
message-recovery handling remains required before messaging acceptance. Runtime
backups for the beacon deployment: A `1788925623836723951`, B
`1788925631545736330` under `/var/backups/lantern-radio-health-`. Packet-log signal
collection follow-up backups: A `1788925822777090227`, B `1788925829185933457`.

Audio-contention follow-up is deployed on both devices: storage
lease checks release the state mutex during backend I/O and revalidate the
same lease/producer/generation afterward; listener diagnostics wait up to 50 ms
per lock instead of failing on a momentary collision. After the owner unlocked
both, six consecutive operational audio checks passed on each. Backups are
A `1788927760040599846`, B `1788927765098827754`. B still logged a separate
nonblocking-lock collision in `audio_readiness.operational_broker_request`;
The remaining operational-readiness lock now has the same bounded wait, deployed
as A `1788927934428789557` and B `1788927939707131518`. All 15 subsequent B
operational checks passed with two-second gaps, and six A checks passed; no
ListenerCheckBusy appeared in B's sampled minute. This is a bounded observation,
not reboot or indefinite stability acceptance. The pinned RadioLib
SX126x implementation reads RSSI directly from packet-status hardware; the host
is not manufacturing the observed zero. Its `getRSSI(true)` takes the low byte of
`getPacketStatus()` (the third response byte), which Semtech identifies as signal
RSSI, whereas packet RSSI is the first response byte. A firmware change to expose
selecting packet RSSI and caching it before receiver rearm was built and flashed
app-only to B as `v1.17.1-d929643-r2`. Boot/settings/OTA bytes were unchanged;
broker and voice services were restored. Repeated advertisements are received
in both directions with the expected public keys, but RSSI still reports zero
or unusually strong negative values. The candidate is not an accepted RSSI fix;
A remains on the original firmware. See `firmware/lantern-meshcore/README.md`.

The host also retained a raw packet signal indefinitely and let it overwrite
newer radio-stat responses. Fixed response ordering: a radio-stat response
supersedes earlier raw events; only later events can override it. All 79
transport tests pass, including consecutive polls and interleaved packets.
Deployed to B, backup `1788931499265827599`; six root-authorized operational
audio checks pass afterward. Initial live RSSI remains -4 dBm, so this corrects
stale ordering but does not establish calibrated signal accuracy. A has not yet
received this host follow-up at that checkpoint. It is now deployed to A too,
backup `1788931620604745171`, with six root-authorized audio checks passing.
A's radio firmware remains unchanged.

Next source follow-up centralizes advertisement observation at frame decoding
so ACK waits and contact reads do not silently bypass discovery observation.
Inventory mutation guards remain in place; contacts are still not neighbors.
Transport/contact tests: 123 passing, including an advertisement interleaved
with an ACK. This follow-up is deployed to both devices: backups
A `1788931754627851229`, B `1788931760430275534`. Post-deployment logs show
both radios repeatedly receiving the other's pinned public key, with counters
increasing. RSSI remains unusually strong (A -12/-13 dBm, B -15/-8 dBm);
distance-dependent accuracy remains unverified.

The radio screen now places “Radio overview” in its top header and the breathing
Live indicator at the left above the cards. The Home signal indicator retains
its session-bound recent sample across navigation without extending expiry.
Both UI builds are deployed, both device endpoints return HTTP 200, and both
kiosks were refreshed. Both devices are currently locked after that refresh;
owner visual acceptance and fresh unlocked polling are pending.

Directory integration constraint confirmed in source: `MeshFacade.contacts`
currently enters the messaging runtime through a contacts grant, and
`CommunityService._directory` returns only contact ID/display name. Radio names
cannot safely supply this mapping. `MeshContactRegistry.resolve` requires the
full radio key, current signed findable/messaging consent, observed inventory
and binding verification. Keep those checks when exposing a read-only directory
match from the radio dashboard; do not bypass admission or equate an advertised
name with a person. This integration is not implemented or deployed yet.

Deferred until the radio foundation is working: display the transcribed question
while preparing the answer, and investigate the owner's reported misrecognition
of “tell me how LoRa networks work.” These are not reasons to interrupt radio work.
References: [pinned RadioLib implementation](https://github.com/jgromes/RadioLib/blob/6d8934836678d8894e3d556550475b37dce3e2b6/src/modules/SX126x/SX126x.cpp),
[Semtech packet-status decoding](https://github.com/Lora-net/sx126x_driver/blob/master/src/sx126x.c).

### Current capability and evidence matrix

Source implementation means local code exists, not that a provider is admitted
or its complete workflow works. Deployed state and physical acceptance are
separate; unspecified capabilities remain unverified on the latest installs.

| Capability | Source implemented | Latest deployed state | Physical acceptance / remaining gap |
| --- | --- | --- | --- |
| Matched A/B software and private identity | Native/SDK/runtime integration and guarded setup/update code | Matched native/SDK/runtime directly installed on both; independent keys, passcodes and private data preserved | Installation evidence does not close full-device or latest reboot acceptance |
| Offline Ask and audio | Local inference, voice orchestration and Ask UI | Both audio ready | Owner confirmed A Ask earlier; no new A/B Ask or latest reboot acceptance inferred |
| Radio and peer setup | MeshCore contact, peer-binding and transport code | Both read back **917.375 MHz, 62.5 kHz, SF8, CR5, 10 dBm**; mutual firmware contacts and signed application peer imports verified | Setup/readback verified, not over-air application delivery; firmware contacts and signed application peers remain distinct |
| Private Messages and receipts | Messages UI, guarded community service, messaging/receive/receipt code | Messaging, dispatch and delivery receipts enabled; **UI unavailable on BOTH unlocked devices** | Runtime provider admission guard under investigation; no real message exchange or delivery/read receipt acceptance |
| History, photos and correction | Conversation lifecycle/workflow, protected-media adapter and correction UI/backend code | Latest end-to-end activation not established by this checkpoint | Saved recall, ciphertext-at-rest, lock/restart/recovery, opt-out and correction uncertainty handling still require acceptance; adapter presence is not full secure wiring |
| Public sharing and answer reuse | Share preview UI, proposal-state storage, public transport/observation components | No working public proposer, verified acceptance or cache provider established here | Enrolled public identity, real proposal/acceptance, private-first retrieval and verified cache reuse remain acceptance gates; queue/gateway status is not consensus acceptance |
| Startup, power and onboarding | Startup, lock/suspend and setup code | Latest matched installs running with audio ready | Boot persistence is **not reboot-accepted** for these installs; sleep/wake and full secure setup remain open |
| Discovery and later networking | Existing contacts/UI scaffolding; broader White Pages and online research remain requirements/proposals | No broader discovery, relay or research deployment established here | Unified directory, relay/range/bandwidth/sync and optional online research are not accepted capabilities |

Local source anchors: `software/runtime/src/sage_lantern/community_service.py`
checks storage/provider readiness before advertising messaging;
`community_messaging.py` implements guarded messaging operations;
`private_media_store.py` supplies a protected-media adapter, not proof of full
history lifecycle integration; `knowledge_sharing.py`, `community_public.py`
and `community_public_mesh.py` contain proposal/observation components.
UI under `apps/lantern-fleet-admin/app/device/` includes `messages.tsx`,
`share.tsx` and `history.tsx`; Messages checks the backend capability rather
than treating an enabled configuration flag as readiness. These are source
references, not new deployment or acceptance evidence. See also
`AB_RADIO_COMMISSIONING_GATES.md`, `PUBLIC_KNOWLEDGE_ACCEPTANCE.md` and
`QUESTION_CORRECTION_ACCEPTANCE.md` for outstanding acceptance details.

### Historical September 8 execution record

The following retained attempts and release checkpoints describe earlier states,
not the current install status. Failures remain evidence; pending requirements
remain open unless explicitly superseded by the current checkpoint above.

SAGE checkpoint `77c1d1ad-c664-4db6-8e51-7536bc6c7cd1` supersedes the stale
`c77422f8` and `3b6667e8` progress snapshots. Those snapshots are dropped as
superseded, not completed; their outstanding requirements remain linked.

1. Repair A's projector-inventory startup regression, run real preflight/recovery,
   publish the matched next-boot configuration, then verify audio and reboot.
   The narrow source fix passes 77 tests. Actual preflight 26886 and one-shot
   recovery 5359 exited successfully; independent device postcheck 52328 reports
   operational readiness with model/broker/voice active and zero restarts.
   New lifetime hash: `bb7e90624b58da6aecdf3a4344af6f305552d25a6f46088a1fa104288c46026e`.
   That recovery did not persist: boot `ec8f4571-510a-40d6-9a99-bac9acf97382`
   again used the old startup configuration. Fresh preflight 74238 passed, but
   recovery 82503 refused the subsequently locked access epoch before mutation;
   independent postcheck 25248 confirmed no producer starts or helper attempt.
   The persistent publisher passed 22 parent tests and independent review. It
   still needs a successful current-boot recovery before publication. Owner
   unlock, publication, Ask and reboot acceptance remain pending.
   A later unlocked preflight 46196 passed, but recovery 46930 refused at its
   first access gate after relock. Independent check 56109 confirmed the same
   boot, unchanged failure journal, absent attempt directory and zero producer
   starts. No new lifetime or publication resulted from that attempt.
2. Finish B's frozen matched release staging, signed inventory, scratch prediction
   and guarded update. Preserve its working services until update authorization.
   Frozen 07f is staged, not installed. Camera-aware collection 16209 failed:
   the actual camera service is not-found, not loaded/inactive. Earlier diagnosis
   omitted LoadState. The subsequent absent-camera candidate passed focused
   tests but independent review reproduced lookup-path and cgroup races. Those
   races are now corrected and covered in the accepted composed maintenance
   closure. Neither earlier failure is a successful collection.
   Composed maintenance `8b24a171` passed 755 parent and independent tests plus
   eight subtests. The separate confined genuine-root reader fixture passed
   22 tests on B without service changes. Next: stage the exact reviewed closure,
   build and verify a new matched `2175/d61c` sealed environment, then collect
   a fresh eligible signed envelope and scratch after-inventory. Review these
   before any update attempt; old R7 environment evidence is not interchangeable.
   Target staging 66463 exited successfully: the new environment's 2,454 entries
   were verified with completion hash
   `cda0828ddd494e906658c0b7cd6e836a793074cc6fb7cfe1f4a4f292985da9dc`.
   Full immutable 318e v2 staging 43277 passed all three target gates (31,369
   files, 38 artifacts); source kit check 50415 verified all 59 source files.
   Collection 75764 failed without an envelope or prediction. Read-only check
   58048 traced the failure to the absent `/var/lib/sage-lantern-startup`
   parent, not merely an absent latest file. Collection, maintenance admission
   and publication currently assume that directory exists. A reviewed initial
   state contract is needed; do not create dummy receipts to make checks pass.
   Read-only startup-chain inspection confirms this is a legitimate pre-first-
   start layout: the enabled, never-started systemd unit declares the directory,
   and its verified coordinator can use the pinned initial lifetime. The fix
   must cover collection, admission, ongoing checks and guarded publication.
   Fresh provisioning shares this boundary: coordinator installation enables
   but does not start it, and handoff requires already commissioned active
   audio. End-to-end base-OS/onboarding/first-audio orchestration is still open;
   do not describe the current postcommission runner as completing that chain.
   The shared startup-absence fix now passes 798 parent and independent tests
   plus eight subtests (61-file closure `e0c500ee`). Its new source kit
   `9a7bf791` passes 43 parent and independent packaging tests. Target check
   45441 verifies the staged tools and unchanged payload; auth check 54043
   reports locked, collection not started. A fresh unlocked collection and
   prediction still precede any live update.
   The test-only first-start ancestry regression is integrated, with 32 tests
   passing. First-audio V2 remains unintegrated: independent review found that
   its cold-model preflight calls a bootstrap check requiring an active model,
   and installed SDK bytes are not admitted against the release SDK wheel.
   V3 must fix both with actual runtime-method regression coverage and a real
   read-only observation collector; V2's 73 passing tests do not prove usability.
3. Commission A's inference guard and benchmark actual latency changes.
4. Implement missing broker-owned radio contact provisioning, existing-config
   transition and contact-import tooling; connect real telemetry to the UI.
   Prove bidirectional messages, delivery/read receipts and replay/lock behaviour.
   Radio V2 passed 411 parent and independent tests plus 49 subtests. The exact
   15 reviewed paths are now integrated into canonical source; 220 focused
   canonical tests plus 34 subtests pass. Reviewed deployment mappings, sandbox
   journal permissions and bootstrap inventory validation are integrated locally;
   146 canonical tests plus 127 subtests pass. The combined runtime build and
   host wheel admission pass; actual target admission remains open.
   Radio UI request/expiry/lock behavior passes
   15 mounted mock tests, not browser or device acceptance. No device telemetry
   or commissioning deployment is implied by source tests.
5. Deploy and test matched SAGE correction/contact/producer integration, protected
   history/photos, verified public sharing/cache, and remaining power/onboarding
   gates. White Pages, relay/range/bandwidth/sync and foundation roadmap stay open.

Source tests, staged artifacts, deployment and physical acceptance are separate
milestones. No broad feature task is closed solely because its tests pass.

### Historical matched release checkpoint

- Combined runtime `2175cd19e275597860cdd877a1007156366667021d721766023a569d83bee519`
  is double-built identically and paired with the reviewed native/SDK artifacts.
  Its 19-file source integration matches reviewed hashes; parent and independent
  combined suites each passed 1,328 tests plus 187 subtests. The focused canonical
  integration suite passed 99 tests.
- Full appliance manifest
  `318e94ee590ca0c74c67e84b9048867f087b2161a0e1abf10e293c39b9aed78e`
  covers 31,369 files and passed independent payload, 38-artifact and runtime/SDK
  gates. It is not installed and omits six maintenance companions. Review-only
  packaging for those helpers is integrated in source; final composed helper
  repinning is under review before another release assembly. No automatic
  execution is added. The existing-config apply path remains disabled and
  separate from the component update.
- The separate B maintenance kit preserves the immutable 318e component target;
  its manifest is `d3da00c2a27d8bee4985f2a2e5fccb7dd58be8c135ae6959be384e66987746cc`.
  Parent and independent verification passed for both identical builds and all
  39 packaging tests. It contains 59 accepted sources and two metadata files,
  including the previously omitted companions. This kit is source-only and
  B-specific, not an A updater or permission to execute its disabled config path.
- The latest local UI build passes TypeScript and 158 tests. Frozen UI manifest
  `1cf2b7eafda12898dd3ef352fc608fd44c3beca8c82a565629665a20f82f0b81`
  preserves that build with verified ARM64 dependencies; this is build provenance,
  not independently reproduced UI output or physical acceptance.
- Existing-config transition V2 passes 77 tests but its apply CLI remains
  disabled. Real-reader coverage and the B optional-camera/owner-ledger composition
  are still required. Do not substitute root operator decisions for cryptographic
  radio-possession evidence or claim firmware pairing from application enrollment.
- B's corrected old-lane tools are staged and verified under receipt
  `bfcf55c8c54be49178531ce64a6be3eed442f565db7cee1cb01bfa7ca5d6b663`;
  locked owner state prevented collection. The new matched maintenance lane must
  be reviewed separately; no old receipt or environment is silently repinned.

## Roadmap to initial TII support

### Proposed optional online research

- [ ] Evaluate durable background research over direct Internet access or compact
  LoRa requests to an explicitly configured Internet gateway. This is a proposal,
  not an implemented provider or automatic permission to upload private content.
  Check the actual HTTPS service rather than treating ping as web availability.
  Keep offline answers independent; require explicit disclosure/consent, encrypted
  job storage, bounded retries/airtime/results, cancellation, citations and private
  completion notifications. Preserve jobs across lock/sleep/reboot without silently
  waking or transmitting against power/privacy policy. Scope and defaults remain
  to be agreed after basic A–B messaging. SAGE task: `008da7ad-a6b6-4df3-a2e5-4dd03d020b0f`.
  Owner-approved hosting direction: evaluate the existing Internet-connected
  SAGE Voice Bridge/Natter server for the initial research worker. Verify actual
  capacity and APIs first. Keep worker credentials, queues and resource budgets
  separate from consensus and voice; never give research access to validator keys.
  Signed research results establish provenance, not consensus acceptance. Results
  remain private unless separately proposed to public knowledge. Server role and
  capacity are owner-reported, not independently audited deployment evidence.

### People, naming and live radio follow-up

- [ ] Implement the agreed white-pages directory: browse city/region, then
  town/village/community, then household/person, with direct search throughout.
  Locality levels must adapt to real communities rather than force one hierarchy.
- [ ] Support individual, household and community Lantern profiles in onboarding
  and Settings; optional family/member/locality details remain private unless
  explicitly published. Shared device names are not separate member accounts.
- [ ] Add a tappable connectome-style directory alongside an accessible list.
  Node profile cards confirm the recipient using published details and verified
  identity hints; distinguish grouping from RF/federation connectivity and never
  imply physical location or reachability from graph layout. Acceptance includes
  duplicate household names, offline/stale listings and opt-in privacy.

- [ ] Deliver one unified Discover panel across saved contacts, radio observations
  and SAGE directory results, with person/device/node search and verified identity
  deduplication. See `UNIFIED_DISCOVER.md`; the illustrative readable address is
  not a new protocol or already-implemented namespace.

- [ ] Make typed display-name entry primary during first-run and in Settings.
  Current UI hides a limited uppercase keyboard behind suggested nature names;
  those suggestions must not substitute for a recognizable person/name.
  Keep immutable cryptographic identity separate from editable display names.
- [ ] Add typed people search with voice as an optional shortcut. Preserve exact
  user-entered names; no silent spelling correction. Handle duplicate names and
  confirm a recipient with a short identity identifier before sending.
- [ ] Keep public directory visibility explicitly opt-in. Renaming a local device
  does not prove directory publication or verified recipient discovery.
- [ ] Replace placeholder Nearby content with real provider/neighbor telemetry.
  Saved radio contacts are not live neighbors; USB detection is not RF readiness.
  Earlier disabled/zero-peer audits are superseded by the current matched RF,
  mutual firmware contacts and signed application peer imports above. These do
  not establish live neighbors or a working public mesh/gateway.
- [ ] Complete remaining A/B radio acceptance and actual over-air delivery tests
  before claiming discovery, relaying, read receipts or public knowledge sync.
  Follow `AB_RADIO_COMMISSIONING_GATES.md` for acceptance gates, not its older
  disabled/zero-peer status. Both now have messaging/dispatch/delivery receipts
  enabled, but Messages remains unavailable and neither has proven application
  delivery. Do not run a second serial reader against an active broker.

### September 8 owner benchmark and correction update

This is newer than the maintenance interruption summary below. Recovery 9693
succeeded after actual owner unlock; three normal owner Ask runs completed.
Runtime first-text times were 39.731, 31.522 and 31.574 seconds. Model logs indicate
prompt evaluation dominates; see `UNIT_A_COMPACT_OWNER_BENCHMARK_20260908.md`.
The first recording was misheard. Exact-byte comparison was false for all three,
so no identical-work speedup claim is made.

- A single-variable `--parallel 1` experiment is isolated and independently
  passes 144 host tests. Fresh maintenance-environment preparation is authorized;
  experiment application and resulting audio/owner acceptance are not done.
- The footer now shows elapsed time instead of repeating the stage heading in
  source. Current-question and history correction UI is wired behind a strict
  backend capability flag. English offline dictionary, predictive completion,
  touch editing and photo retention are source features, not deployed acceptance.
  Full UI tests: 151 pass; latest type-check, build and mounted keyboard checks
  pass. Physical keyboard usability remains unverified.
- Canonical correction backend: 344 tests and 130 subtests independently pass.
  Explicit source-kind resolves latest-photo/history-source ambiguity. Actual
  correction wheel tests pass 143 tests and 8 subtests. Matched deployment and
  unknown-submission recovery across reload/lock remain open.
- B combined update remains in integration. Independently passed component tests:
  native 131, SDK/runtime/UI 95 plus 8 subtests, maintenance/audio 113. These do not
  prove a composed device update, service recovery or independent-key acceptance.
- Full secure public/private retrieval, sharing validation, A–B messaging/LoRa,
  reboot, sleep, first-run provisioning and boot splash gates remain open.

Correction requirements and pending acceptance are in
`QUESTION_CORRECTION_ACCEPTANCE.md`. No on-device update is implied by these
source and host-test results.

### September 8 integration checkpoint

This checkpoint supersedes older status summaries below, not their retained
deployment evidence. Neither unit has full end-to-end acceptance yet.

**A maintenance interruption:** the compact prompt wheel `fd39c107…` was installed
and byte-verified, but activation attempt 95289 failed at the post-install metadata
check. Separate basic restoration 4111 succeeded and postcheck 31075 passed:
auth/UI/camera/status are restored with auth locked; voice/broker remain guarded
at PID 0. Native/model were not restarted. Basic completion journal SHA256:
`b8d76d9574429a782de596b6dacd06964acdca16bbf8878a0db6d16744df6844`.
Original failure journals are retained. Audio needs a separate reviewed
continuation; installation alone is not deployment acceptance and no paired
benchmark exists yet.

Audio continuation 71235 subsequently exited on owner-unlock timeout before any
capture/bootstrap. Its retained-failure receipt SHA256 is
`5d99802d22a1732f814b0aadb23da31c1e5a9affcdd0ec11a6ad769789f81805`.
Auth/UI remain active; voice/broker remain at PID 0 with verified guards and
containment. No automatic retry: prepare a separate reviewed attempt only when
the owner is available, preserving the expired attempt's records.

After a fresh owner-readiness message, separate rearm attempt 9179 also reached
the owner-unlock timeout without capture or bootstrap. Postcheck 56349 reports
auth locked, auth/UI active, voice/broker guarded at PID 0 and native/model
lifetimes unchanged. Both attempts remain preserved. Do not start another timed
window from an automatic continuation or an earlier unlock message.

After the owner's later Menu→Lock confirmation, preflight 85084 passed but
apply 66123 again timed out waiting for PIN unlock, before capture/bootstrap.
The third attempt is retained under
`/var/lib/sage-lantern-compact-audio-rearm/50a6add5-225d-448c-89b8-fd93d2ecb708`;
its failure receipt has the same `5d99802d…` content hash. Postcheck reports
locked auth, unchanged basic/model services and guarded voice/broker at PID 0.
No recovery process or new timer remains running. All three attempts are kept.

**Later A recovery success:** following fresh owner readiness, locked preflight
52854 passed and apply 9693 completed after actual PIN unlock. Approved two-second
capture, broker bootstrap and next-boot policy publication succeeded. Broker
702161 and voice 702218 are active; basic/model services were not replaced.
Completion receipt `35-audio_continuation_done.json` SHA256:
`b1f0f358e565497af7e532402d1ff0f474108e510dfd422d0916d148b1e78635`.
Root readiness passed; service-user readiness first failed then passed, so
stability checks remain required. All earlier attempts remain preserved.
Physical Ask, latency measurements and reboot acceptance are still open.
This success supersedes the stopped-audio status above, not its failure history.

| Lane | Verified evidence | Remaining gate |
| --- | --- | --- |
| A response latency | Latest measured request: 2.37 s STT, 3.11 s retrieval, 84.93 s prefill for 1,449 tokens, 90.44 s first displayed text, 108.05 s total. | Deploy the reviewed bounded-retrieval/UTF-8 prompt candidate through the matched maintenance path and measure actual model tokens and TTFT; synthetic tokenizer counts are not board speedups. |
| A waiting UI | Transcript plus elapsed screen-wait indicator implemented and independently reviewed. | Matched deployable UI closure, lock/disconnect/session-change checks, deployment and touchscreen acceptance. No fabricated percentage or ETA. |
| A vision | Camera presence verified; deployed model/voice lacked projector/photo-ready flags at diagnosis. Guarded activation source promoted. | Actual approved activation, synthetic image inference, fresh audio commissioning and owner photo-question acceptance. Source promotion alone does not enable vision. |
| A power on current installation | Direct read-only checks find the display policy present, but its controller disabled/inactive. Idle-suspend service/helper/approval and capability evidence are absent. | Restore reviewed persistent display control and commission guarded deep sleep; then verify screen-off, touch wake, PIN, audio/camera resume and reboot. Earlier installation's physical results do not prove this installation works. See `UNIT_A_POWER_ACCEPTANCE_20260908.md`. |
| B audio and persistence | Mesh reconciliation/install, local two-second capture commissioning, first audio bootstrap, startup installation and native handoff reported successful. Separate failed-inhibitor reconciliation completed with `released:true`; independent postchecks confirm operational audio, ordinary sleep readiness, four provisioning masks and inhibitor holders absent, and protected service lifetimes unchanged. Original failure retained. Completion SHA256 `1c064232d0e60e64fd224805938033cff563acb8b3137e2b341852332269cca4`. | Physical Ask, screen-off/wake/PIN, display boot enablement and reboot acceptance remain open. Successful inhibitor release does not establish physical suspend or reboot behavior. |
| A–B messages and public knowledge | UI and substantial protocol/storage implementation exist; B has an independently provisioned identity and configured radio. | Enable verified providers and exercise actual delivery, authenticated receipts, consent/revocation, replay/recovery and canonical public proposal status. Configured radios are not proof of RF delivery or public acceptance. |

See `UNIT_B_RELEASE_POSTCHECK_20260908.md` for the completed read-only gate.

Later B read-only assessment (handle 67082, exit zero) finds the same boot,
locked auth, active native/auth/UI/model/broker/voice/display, startup enabled
but inactive, and no four provisioning sleep masks. The new matched release
cannot use the current UI-only updater or rerun fresh installation. Existing
maintenance/native-transition helpers require reviewed B-specific current and
candidate bindings, SDK/native/UI coverage, and installed startup-policy path
compatibility. Missing files at new canonical helper paths alone do not prove
the existing startup configuration is broken. No update was applied.

The matched V3 runtime is reproducibly built, and parent archive inspection
confirms all six conversation modules retain the approved compact-prompt bytes.
See `V3_COMPACT_PAYLOAD_VERIFICATION_20260908.md`. V2 is superseded because its
context module omitted that optimization. Native/SDK candidates also exist;
combined release verification and deployment remain separate gates. These
build results are not post-update latency measurements.

The normal-Ask numeric metrics wheel `bbc7f20f…` now has independent source and
packaged-runtime validation (150 tests, 42 subtests), preserving compact prompt
modules and all admission behavior. It is not installed on either device.
The explicit static-startup-helper phase passed V2 independent review after
fixing full-release metadata limits and staging expectations; canonical
integration and a new combined release are in progress. That phase installs
only five pinned helpers, not keys, services or commissioning evidence.

Immediate order: finish A audio recovery and measured latency/UI deployment,
then vision; complete B's physical acceptance; then exercise real
A–B workflows. Secure private history/media, lock/sleep, first-run network setup,
boot presentation and reproducible provisioning remain subject to the full
acceptance matrix below, not waived by audio readiness.

Current messaging source inspection found the voice provider, durable dispatch
and consented read-receipt wrapper already wired; the early September 6 list
must not be read as the current implementation inventory. A root-only,
broker/config/epoch-bound signed peer-card import route is newly implemented
and independently reviewed. Owner-facing issuance/reconciliation routes and
automatic application delivery receipts now have source implementations, but
native-kind integration, deployment and actual
card exchange/renewal remain gates. The owner-route optimization independently
reproduces 680 to 56 auth HTTP calls in a synthetic preview/confirmation workload;
this is not a device latency benchmark. Auth endpoint and root client must ship
together. See `OWNER_CONTACT_ROUTE_PERFORMANCE_20260908.md` and
`MESH_CONTACT_ACTIVATION_20260908.md` for the exact activation prerequisites;
none of this establishes installed providers or physical delivery.

Final inbox uncertainty handling passed independent source review and 99 focused
tests (144.62 seconds). Unknown writes latch inbox and root runtime admission;
the original captured frame and durable recovery fence are retained rather than
allowing ordinary restart to resend. Four reviewed source/test hashes matched
after the run. This closes that source-review gate, not RF or deployment acceptance.

### September 6 secure-messaging source checkpoint

Real encrypted-send admission now spans SAGE store/API, Python SDK and Lantern's
canonical sender, with durable UUID-only uncertainty markers. MeshCore has a
firmware-pinned host send/receive foundation. These are **not deployed providers**
and not complete cross-device messaging. Recipient consent, exclusive lifecycle
ownership, radio identity/envelope/queue integration, public enrollment and
clean-install orchestration remain gates. See `UNIT_A_SECURE_MESSAGING_20260906.md`.
The base runtime wheel was found stale; refreshing that artifact is required
before Unit B provisioning. B availability alone does not close transport gaps.

### September 6, 10:42 MYT rollout

Messages/dictation and provider-disabled sharing UI are deployed on A, alongside
the separate **Lock after** setting (default five minutes). The fixed two-minute
auth expiry is replaced by persisted lock policy; saved after-lock screen/sleep
delays are unchanged. Native 785 Python tests/454 subtests and 94 UI tests,
lint/build pass. See `UNIT_A_COMMUNITY_LOCK_20260906.md` for pinned artifacts,
recovery guards and required owner checks. Real sending/public acceptance,
protected storage, branded boot and complete fresh provisioning remain open;
the Unit A master and community tasks are not complete.

### Unit A completion gate before Unit B — 2026-09-05

This checkpoint consolidates the latest user requests under Unit A task
`ee21422a-4603-4ae1-822d-583fdd26c265`; do not create parallel duplicate tasks.
Priority is Unit A release readiness followed immediately by scripted Unit B
provisioning, **not** permission to skip acceptance or add unrelated features.
Older implementation-status notes below are historical, not release evidence.

| Lane | Required acceptance before Unit A is called complete |
| --- | --- |
| Private SAGE | Real committed conversation/reflection writes, cross-session recall after restart, photo context, deprecation, consent and failure recovery. Native verifier plus live voice/history acceptance. |
| Security | Numeric vault unlock; direct-port access checks; PTT/stream/inference cancellation; lock/expiry/restart replay rejection; no root cookie in kiosk. Explicitly resolve plaintext storage and privileged bypass limitations. |
| Sleep/wake | Manual lock, server-enforced idle expiry, private-view cleanup, screen sleep, wake only to keypad. Screen blanking is not hardware suspend or proven power saving. |
| First run | Owner-chosen PIN and recovery acknowledgment, unique identity provisioning, hardware checks and device naming. Incomplete setup cannot claim readiness. |
| Your Lantern | First Settings section: current name and later rename without rotating keys or losing memories. PIN and sleep settings belong here when supported. |
| Nearby | User-facing connections/people/discoverability, OFF by default; technical radio/SAGE metrics under Connection details. Never present saved preference as working federation. |
| Messages/public knowledge | Preserve private/public separation and truthful pending/accepted/rejected states. Cross-device transport/relay/sync require Unit B and remain explicit two-unit acceptance work. |
| Reproducibility | Clean private source snapshot, versioned artifacts, safe rollback, power-cycle/restart checks; Unit B gets its own keys, not a cloned identity. |

Active team ownership: private SAGE adapter/native acceptance; auth daemon and
rate limits; shared access gate/security review; voice/PTT/runtime enforcement;
camera/status enforcement; profile/onboarding; parent UI, integration and release.
Each lane must return test evidence. Source implemented, tests passed, staged,
deployed and physically accepted are distinct statuses.

### Current critical path — scope frozen for A/B bring-up

**Required new-install experience (owner clarified September 6):** install the
base OS, run one bootstrap script, finish hardware/network setup from an external
browser admin interface, then choose passcode/recovery and device name on the
touchscreen. This is the release target, not current end-to-end capability.
On-device passcode/recovery/name flows exist; branded boot and fresh secure
orchestration remain open. Wireless currently reports status, not Wi-Fi setup.
Browser onboarding needs an authenticated, restricted initial access path
(Ethernet or an explicitly designed temporary setup network) before Wi-Fi is
configured; current loopback-only listeners are not a LAN setup portal. Do not
expose private endpoints or ship a universal admin password to bridge the gap.

Implementation slice now exists locally: browser `/admin/setup`, an expiring
loopback-only network broker over authenticated SSH forwarding, bounded nmcli
adapter with verified new-profile autoconnect, and a plan-first Plymouth theme
installer. Device Settings retain Change name and add revisioned screen-off /
saved deep-sleep delay controls; automatic sleep was unavailable at that stage.
No complete bootstrap chain yet. Integration, deployment
order and acceptance limits: `FIRST_INSTALL_SETUP.md`. Local validation:
232 Python tests / 140 subtests, 74 frontend tests, lint and production build
pass. Native deployment and hardware acceptance remain separate.

**A power-settings rollout, September 6:** runtime/auth/display plus the device
settings UI deployed after 128 native Python tests and 71 UI tests/lint/build.
Source hashes match, fresh lock and denial checks pass, voice/model PIDs retained.
Default policy is 30-second display-off / 5-minute saved deep-sleep delay;
automatic deep sleep was unavailable in that power-only rollout. Owner
delay/wake acceptance was pending. Wi-Fi and boot components remain staged;
the shared-access correction subsequently shipped with the suspend rollout.
See `UNIT_A_POWER_SETTINGS_20260906.md` and `AUTOMATIC_DEEP_SLEEP.md`.

September 6 read-only A review, before the later deployments: all nine checked services active/enabled with
zero reported restarts; failed-unit list empty, accepted display-helper digest
unchanged, adequate memory/storage. No disruptive operations. Automatic deep
sleep was then unintegrated; audio-after-deep-resume and release-specific reboot
acceptance remain open, as do the OS/storage privacy boundaries. Details and
explicit limits: `UNIT_A_HEALTH_20260906.md`.

1. Finish Unit A local release gates: safe automatic sleep/resume, audio after
   wake, recovery/restart checks and the existing private SAGE/security gaps.
   Keep working touchscreen UX stable; only fix regressions and release blockers.
2. Integrate the already-requested messaging/shared-knowledge paths against real
   SAGE and MeshCore contracts. Tested policy modules alone are not an enabled
   directory, composer, verified radio transport, gateway or public cache.
3. Produce a pinned, sanitized release plus a repeatable provisioning entrypoint
   and verification report. Unit B must generate its own keys, passcode/recovery,
   name and radio identity. Never clone Unit A's writable runtime state.
4. Provision B and immediately run A↔B delivery, receipt, offline retry, replay,
   relay and verified sync tests. These are necessary pair-level acceptance,
   not checks that can honestly be completed on A alone. Range/bandwidth and
   multi-validator resilience remain separately measured evidence.

Do not call either device fully complete from a passing build, policy test or
single physical success. Do not silently defer existing security requirements
to meet the provisioning milestone. Record unresolved gates explicitly.

Provisioning work in progress: hostname configuration now derives and validates
the prepared unit rather than stamping A (six tests). A public-input plan-only
validator and B-specific voice-activation preflight exist; their schemas are
not interchangeable by default. The planner's explicit `--format voice-activation`
export now bridges them, checks the inventory's unit/radio binding, and passes
the actual consumer dry run with synthetic public evidence. A complete B
appliance installer remains pending.
The legacy appliance installer now has a nonmutating public-binding preflight;
live B and guarded-A updates fail before mutation. This prevents accidental
reuse of the old installer, but does not replace the pending fresh secure path.
The planner can now export an exact per-unit normal-firmware audio udev rule
from explicit vendor/product/serial inputs; no A serial or recovery-flash rule
is silently copied to B. Installation and live microphone verification remain
pending; Unit A's working rules are unchanged.
Private-install readiness now requires strict boolean locked state and direct
loopback private-route denials, without proxies or redirects; malformed or
oversized state responses fail into the existing containment path. This is
locally tested, not a new deployment or complete fresh-install acceptance.
Base installation is now fresh-target-only: existing state/markers/systemd
units and ambiguous checks stop it before writes. An atomic install-root claim
prevents competing installer runs; interrupted installs remain blocked for
manual review. Help is safe and unsupported `--dry-run` is rejected. Latest
combined provisioning run: 171 tests and 158 subtests pass, no live installation.
Fresh secure setup orchestration remains open: independent personal SAGE and
voice enrollment precede the existing owner passcode/recovery ceremony, with
private endpoint gates present before exposure. Naming alone is not enrollment.
Activation regression tests are part of the integration gate, not just new
dry-run tests. These changes have not been deployed on A or run on B.

UX agreement: **Your Lantern**, **Nearby**, and **Messages** describe user tasks.
Expose **Let others find me** in Nearby, not general SAGE settings; default off.
Keep advanced connection details secondary and main Ask/Camera actions visible.
The private `l33tdawg/sage-lantern` repository exists; source push is authorized,
but must exclude credentials, personal media, runtime databases and test evidence.

### Private device lock — guarded prototype deployed, acceptance open

- Decision (2026-09-05): user accepts **8–12 numeric digits**, replacing the
  earlier four-digit minimum. Reuse SAGE vault credentials, no separate PIN hash.
- [x] Implement and locally test private history/photo/voice/status gates, PTT
  cancellation, pending activation, presence expiry and keypad integration.
- [x] Recheck worker admission between SDK operations; retain receipts for writes
  already dispatched, defer later submissions and stop safely without closing
  a store beneath active work.
- [x] Stage the native UI without replacing the running app: 61 tests and ARM64
  production build pass. The ignored local hosting manifest must accompany a
  native build; omitting it produces a build error, not a valid release.
- [x] Activate guarded runtime after installer/native permission checks; locked
  state and missing-credential denial verified for history, status and preview.
  All seven application/SAGE/LLM services active; kiosk refreshed for owner setup.
- [x] Owner completed passcode setup and recovery acknowledgment; read-only live
  check confirms encryption enabled, initialization disabled, profile configured
  as Cedar (revision 1), discoverability off, and all seven services active.
- [ ] Validate recovery restoration, restart locking and persistent retry limits.
- [ ] Resolve SAGE direct-login throttling and explicitly distinguish application
  access locking from cryptographic vault key unloading.
- Release checklist: `DEVICE_LOCK_ACCEPTANCE.md`. Preserve existing Unit A
  credentials and data; do not activate a cosmetic screen-only lock.
- Remaining OS, privileged bypass and at-rest security work:
  `UNIT_A_SECURITY_REMAINING.md`. Application locking alone is not completion.
- [ ] Appliance boot/power UX: branded Plymouth splash with diagnostic/recovery
  access retained; short power press should lock/blank rather than shut down.
  Native previous-boot log confirms systemd-logind handled a short press as
  poweroff. Installed systemd 252; rk805 pwrkey is input event0 at inspection.
  Quiet/splash and Plymouth already exist, but tty1/early console and boot-menu
  output need separate inspection. No bootloader flash or live power remap yet.
- Idle behavior already configured: 120-second server idle lock, then 30-second
  locked-screen blanking; Menu → Lock is available. This preserves the running
  LLM process, not a promise that per-request context caches never change.
- [x] Replace cosmetic locked-screen blanking with verified display power-off.
  Owner confirms panel-off/touch-wake and the wake-input guard fix. Separate
  helper is active and boot-enabled; 42 display tests and 68 UI tests pass.
- [ ] Complete repeated-cycle, reboot, power-measurement and automatic system
  suspend acceptance. One manual real-suspend test stopped fans and preserved
  boot/model PID; owner confirms unlock and camera recovery. Audio recovery is
  still awaiting confirmation. Automatic system suspend is now enabled; the
  2026-09-06 automatic cycle passed kernel/RTC verification (see below).
- [ ] Implement duty-cycled radio for personal Lanterns: periodically wake to
  announce, collect queued messages and incrementally sync verified shared
  knowledge, then sleep. Explicit Send / Sync now wakes it immediately. Keep
  relay/validator roles separate; sleeping Lanterns are not always-on relays.
  Verify radio hardware sleep/wake support, awake store-and-forward peers,
  retry/deduplication, jitter, bounded airtime and checkpoint freshness before
  enabling. No incoming delivery while asleep; expose last sync and pending
  messages honestly. Choose polling interval after A/B measurements, not guesswork.
- Evidence: 797 local Python tests plus 454 subtests, 160 native unittest tests,
  and 61 frontend tests pass. Native UI build passes. Owner chooses PIN and
  recovery backup on-device; physical idle/wake/restart acceptance remains open.

The TII initial-funding readiness roadmap, kept in the private `internal-docs/`
tree, is the end-to-end execution attachment to the two master tasks below. It covers
reliable Unit A UX/voice, reproducible Unit B and LoRa, the private-to-public
Commons contribution/query demonstration, security/recovery evidence, and the
costed proposal, deck, rehearsal and funding ask. Target: January 2027, at least
two polished working units and a target of four; USD 1M base or USD 1.5M expanded
funding scenarios. Exact meeting date, selected scenario and allocations remain
to be agreed. Production Fleet hardening is a downstream funded-work lane, not a
reason to conflate an initial funding demonstration with public launch.
The award's main intended use is **20–30 supported field-pilot units**, a paid
engineering/product/field-support team, Foundation establishment, and sustainable
sponsor/donation operations—not merely the cost of the January demo hardware.

Current delta: physical PTT-to-answer works on Unit A after private voice-agent
approval; five-loop/reboot acceptance remains open. The dedicated device UI is a
first deployment on Unit A, with physical usability testing pending. Radio USB/firmware bring-up has recorded evidence,
but authenticated two-device delivery is not implemented/proven. Public proposer
intake/commit/query remains an integration milestone, not a shipped capability.
Historical checked items below retain their original evidence scope and do not
override these current limitations.

## Canonical durable SAGE queue

### Unit 3/4 industrial-design workstream

User direction (2026-09-05): retain Units 1/2 as working reference hardware;
investigate a larger touch display, compact microphone-only audio, protected
battery power and a serviceable 3D-printed slab enclosure for Units 3/4. The
January TII presentation should use enclosed demo devices, not exposed boards
and loose USB cables. This is an early design study, not purchase approval or
proof that the current stack fits a final enclosure.

See the [Unit 3/4 enclosure study](UNIT_3_4_ENCLOSURE_STUDY.md) for the provisional
7–10-inch display direction, compact microphone and protected PD-bank shortlist, primary
manufacturer references and measurement gates. No new parts are approved yet.

- [ ] Compare 7-, 9- and 10-inch-class displays using full module dimensions, grip, weight and touch readability; research exact larger-screen SKUs.
- [ ] Qualify a compact capture frontend against the working voice pipeline.
- [ ] Qualify one USB camera on Unit A: capture, focus, reconnect and coexistence with the existing microphone.
- [ ] Prototype touch-only Take photo / Retake / Use photo plus spoken question; validate a local vision-capable model, memory use, latency and answer quality before claiming support.
- [ ] Keep photos private by default, with explicit capture/use, retention/deletion controls and no automatic Commons publication; test uncertain identification and safety-sensitive repair responses.
- [ ] Reserve camera aperture, mounting, lens clearance and cable routing in the Unit 3/4 enclosure; defer duplicate camera orders until validation.
- [ ] Add a large touchscreen Talk / Stop-and-send / Cancel fallback using the same voice-session controller as physical PTT; physical-button failure must not disable touch activation.
- [ ] Test arbitration between touch and GPIO, stuck/missing PTT, dropped browser connections, maximum recording duration and explicit recording indication; do not make the browser open a second microphone stream.
- Decision (2026-09-05): skip always-listening, wake-word and continuous transcription work. User explicitly rejected the added turn-detection, interruption and privacy complexity. Use explicit touchscreen/physical activation only; do not add ambient audio to memory. Revisit only if the user changes this decision.
- [ ] Measure whole-device peak power, battery runtime and enclosed thermals.
- [ ] Plan internal connectors, strain relief, cooling and antenna clearances.
- [ ] Build one enclosure/engineering sample before duplicating Units 3/4.
- [ ] Verify charging, shutdown, service access and repeatable demo operation.

These are acceptance items under the existing roadmap/master tasks, not a new
durable task. User confirmed no built-in speaker for the proposed Unit 3/4
design. Optional future Bluetooth headphone output needs local TTS, touch-only
pairing, private output selection and reconnect/volume testing; it is not yet
implemented or universally compatible. Keep onboard microphone capture and a
complete screen-only experience independent of headphones.

These are the two umbrella delivery tasks in SAGE. Linked implementation tasks
also track secure messaging activation, public sharing and private media/history;
the checklists below remain their acceptance criteria, not evidence of completion.

1. **IN PROGRESS — Unit A physical provisioning**  
   SAGE task `ee21422a-4603-4ae1-822d-583fdd26c265`  
   Finish physical display/touch/thermal evidence, USB audio and PTT, exact
   MeshCore radio/BLE/iOS/region validation, radio identity, bounded PUBLIC
   Commons proposer enrollment, and write `PROVISIONED` last.
2. **PLANNED — Fleet production hardening**  
   SAGE task `d8cca61b-aa40-41ee-a90e-410933afd8af`  
   Add live inventory/audit, certificate lifecycle, durable scoped roles,
   monitoring and restore drills, real radio/federation views, offline field
   operations, and a separately reviewed signed-command/receipt release.

The superseded broad hardware and Fleet v1 tasks are linked to these records and
closed as `dropped`; the completed Unit A Device Agent deployment task is `done`.

## Shipped and verified

- [x] Unit A base ARM64 system, local SAGE, headless inference, speech, recovery,
  storage, thermal, and identity evidence.
- [x] Owner-only hosted Fleet Admin with ChatGPT sign-in and fixture-safe fleet,
  operations, radio, federation, evidence, incident, and audit surfaces.
- [x] Loopback-only Unit A touchscreen Fleet Admin deployment and production
  service restart smoke test.
- [x] Separate Fleet Authority on the Hetzner relay host, bound to
  `127.0.0.1:8091`, with single-use enrollment, identity uniqueness, comparison
  code, proof of possession, certificate issuance, signed-observation ingest,
  idempotent retries, sanitized audit, and no command API.
- [x] `fleet-api.sage.delivery` public Web PKI TLS, hostname routing, and device
  mTLS enforcement. Existing call relay TLS, TURN, and Natter remain active.
- [x] Owner-only `/live-enrollment` console with server-resolved, fail-closed
  fleet role; same-origin mutations; server-only authority token; enrollment
  creation/list/approval adapters.
- [x] Fleet certificate renewal dry run and external loopback-port exposure
  checks.
- [x] Outbound-only Device Agent implementation: separate management/TLS keys,
  durable sequence/outbox, exact retry, allow-listed status input, enrollment
  CLI, and no inbound listener or command execution.

## Completed Fleet deployment slice

- [x] Reconnect Unit A on the operator hotspot at its current DHCP address,
  `172.20.10.6`, and verify the previously pinned SSH host key.
- [x] Complete installation of the tested Device Agent under the dedicated
  unprivileged account and isolated `/var/lib/sage-lantern-device` state root.
- [x] Start the 30-second sanitized status writer and verify its owner/group and
  allow-listed payload.
- [x] Create a 15-minute enrollment in Fleet Admin, claim it from Unit A, compare
  the six-digit code on both sides, approve it, and complete certificate issue.
- [x] Run signed observations, verify acceptance and sequence continuity through
  sequence 6, then enable the 60-second outbound daemon.
- [x] Reboot Unit A and prove durable identity, sequence, service startup, retry,
  and live observation recovery. The pinned device returned on `172.20.10.6` with
  a new boot ID, all four services active, and accepted sequence 11.
- [x] Capture a redacted end-to-end evidence bundle without setup secret, private
  keys, certificates, bearer token, or raw personal SAGE data.

## Hardware provisioning gates

- [ ] Complete display/video persistence, touch mapping, and thermal validation
  on the physical Unit A screen.
- [ ] Resolve the iOS MeshCore companion discovery issue only after confirming
  the exact LoRa board firmware/role and Bluetooth support; do not transmit
  without antenna and Malaysia region evidence.
- [ ] Install and validate the audio bridge firmware and physical push-to-talk
  path.
- [ ] Generate the separate radio identity locally and capture its public
  fingerprint.
- [ ] Write `PROVISIONED` only after every hardware and identity gate passes;
  enable normal runtime services last.

## Next control-plane slices

- [ ] Add device-certificate renewal, explicit per-device revocation, Fleet CA
  backup/restore rehearsal, expiry alerts, and rotation evidence.
- [ ] Move pilot role assignments from the exact owner bootstrap environment
  record into durable scoped D1 administration with expiry/revocation UI.
- [ ] Add live observation inventory and audit views to the main dashboard after
  the first Unit A telemetry evidence is accepted.
- [ ] Add monitoring for authority health, observation freshness, certificate
  expiry, repeated authorization failures, database backup, and disk pressure.
- [ ] Exercise restore of the SQLite database, Fleet CA, and command-authority
  key as one consistency set.
- [ ] Add remote commands only as a separate reviewed release with signed,
  expiry-bounded, target-bound commands, device-side allow-list and state checks,
  and device-signed receipts. Delivery or HTTP acknowledgement must never be
  represented as success.

## Commons and federation queue

### Voice-first private messaging and remembered contacts

2026-09-06 source checkpoint: Messages Inbox/Outbox screens, memory-only
paragraph dictation/review, authenticated voice-hosted dispatcher and real SDK
adapters are implemented; not deployed. Sending remains disabled without
verified identities, protected storage and transport. See
`COMMUNITY_WORKFLOWS_20260906.md` for exact scope and activation gates.

- [ ] Preserve the transport split: MeshCore/LoRa for local delivery and relay;
  SAGE for verified identity, directory policy and federation/backhaul between
  gateways serving different LoRa networks. Keep one logical message identity
  across hops, with bounded retry/deduplication and truthful receipt states.
  Verify gateway/radio identity bindings and confidential transport before
  sending private content. SAGE federation does not imply internet availability;
  no reachable path means queued, not a fabricated delivery or online fallback.

Initial implementation foundations: memory-only `message_drafts.py`,
`message_contacts.py` and STT-only `message_dictation.py` have 40 passing stdlib
tests. The later source checkpoint adds capture/controller and touchscreen
integration, but not live deployment or private radio transport. No private
radio delivery is advertised.

- [ ] Compose private messages by dictation using STT, not a question-answer
  model call or silent rewriting. Review recipient and message before Send.
- [ ] Represent the draft as paragraphs. Tap a paragraph → Edit → re-record
  only that paragraph; preserve the others and allow review/undo before replace.
  Support adding another dictated paragraph without a tiny on-screen keyboard.
- [ ] Resolve recipients through actual SAGE federated directory lookup,
  respecting findable/public-directory consent. Confirm the selected person
  and disambiguate duplicate names using verified stable identity information.
- [ ] Cache verified recipient identity/key and authenticated radio-address
  binding for future messages. Do not cache a relay path as identity or assume
  a contact's last route is current. Handle key rotation, stale bindings,
  revoked/blocklisted identities and offline recipients without unsafe fallback.
- [ ] Keep private message drafts/outbox and receipts separate from public
  knowledge proposals. Use reviewed encrypted transport/storage, immutable
  message IDs and idempotent retry; distinguish queued, sent and delivered.
  Do not display Read without an actual supported authenticated read receipt.
- [ ] Integrate SAGE's negotiated signed read receipts. Only acknowledge an
  actually opened/displayed message when the recipient permits read-receipt
  sharing; inbox polling, claiming and gateway admission must not imply Read.
  Bind verified receipts to exact message and participants across LoRa/gateway
  paths. Unsupported peers and delayed receipts remain unconfirmed, with a
  user-facing privacy setting rather than silently leaking read activity.
- [ ] A/B tests: dictated multi-paragraph draft, targeted re-record/undo,
  discoverable versus hidden directory entries, recipient confirmation, cached
  contact after topology change, offline retry, duplicate delivery suppression,
  lock/cancel behavior and no message leakage into the public knowledge cache.

### Automatic deep sleep follow-through (2026-09-06)

- [x] Implement saved-delay eligibility, auth/voice drain receipts, persistent
  inference tracking, kernel resume proof and fresh locked restart. See
  `AUTOMATIC_DEEP_SLEEP.md`; native regression: 624 tests, 390 subtests passed.
- [x] Deploy guarded source/configuration to A without changing keys, history
  or saved delays. One controlled model restart establishes the initial tracked
  baseline. Normal suspend is intended to preserve that model invocation.
- [x] Verify an automatic cycle after the full saved 300-second delay: kernel
  deep entry/RTC resume, success counter 1→2, zero failures, same boot and warm
  model PID. Enable the production timer at boot with saved settings unchanged.
- [ ] Obtain owner confirmation of power-button wake, PIN entry, microphone
  and camera after automatic resume; repeat cycles and test reboot behavior.
- [x] Diagnose 2026-09-06 wake-triggered shutdown: kernel resumed, then logind
  interpreted the short wake press as poweroff. Deploy the service-only
  `handle-power-key` inhibitor correction and verify it live. Owner repeat-cycle
  acceptance after the fix remains open; no community code shipped with it.
- [ ] Add reviewed recovery for orphaned inference markers and interrupted
  fenced transitions. Never expire unknown work or equate an empty live socket
  list / idle slot snapshot with confirmed completion.
- [ ] Compact confirmed auth drain receipts safely before the 1024-entry
  readiness limit; never discard unresolved cleanup evidence.
- [ ] Audit the vendor base image before security/release sign-off: A's
  Chromium directory was 0777 (tightened to 0755); its binary differs from the
  package-recorded checksum. Do not silently classify it as verified or replace
  it without a reviewed image/update path. Fold the verified result into B's
  reproducible provisioning rather than copying the weak permission.
  Also audit the world-writable vendor `rkaiq_3A.service` reported by systemd
  verification; it was not modified during this deployment.

### Shared answer reuse — explicit Unit A/B integration gap

User clarification: public knowledge is also a reusable community answer cache.
For example, a second village user asking what The Matrix is about should get
a suitable validated cached answer without repeating language generation.

The 2026-09-06 source implementation adds answer-bottom **Share this with others**,
explicit public preview/consent, and status UI; it is not deployed or activated.
The new SDK adapter supports configured trusted-node online status observation,
not offline acceptance proof or automatic answer-cache eligibility. Durable
protected archive/journal and identity/transport activation remain required.
The older CLI
`ProposalOutbox` is a local queue/state-machine scaffold; its signature-shape
check is not cryptographic verification, and its state transitions are not
verified remote acceptance receipts. The voice orchestrator recalls context
and then calls the model, so direct cached-answer reuse is not implemented.

- [ ] Add **Share knowledge** on completed answers: preview the public question,
  answer and optionally photo; explain “Help others find this answer.” Keep the
  original private record separate. Explicit consent, private-context checks,
  photo metadata removal and final confirmation precede public submission.
- [ ] Create a separately signed PUBLIC proposal with its scoped proposer key;
  validate signatures and authenticated, content-bound receipts. Render honest
  Queued / Sent / Under review / Shared / Not accepted states. Rejection leaves
  the private record intact; never label local enqueue as publicly shared.
- [ ] Implement private lookup → locally synced public knowledge → model.
  Direct answer reuse requires matching question intent/language/context,
  reusable rather than personal content, verified provenance/acceptance,
  freshness and no known disqualifying challenge/supersession. Semantic
  similarity alone must not select a different subject or different photo.
- [ ] If a valid reusable answer exists, bypass language generation and label
  it **From shared knowledge**, with source/date available. STT, lookup and any
  requested speech still cost time; radio lookup is not instant. On a miss or
  uncertainty, use retrieved content as evidence for the model, then offer
  sharing of the resulting answer rather than silently publishing it.
- [ ] Keep shared lookup usable offline from locally verified synced records;
  invalidate the active cache projection when signed corrections, challenges,
  expiry or supersession arrive. Preserve ledger history. Acceptance/consensus
  does not itself prove factual truth; freshness is bounded by last sync.
- [ ] Define bounded text/metadata delivery over LoRa separately from photo
  delivery. An image hash/reference is not the image. Only advertise a shared
  photo as available after its bytes have been transferred and verified through
  a supported transport; do not promise fast full-photo dissemination on LoRa.
- [ ] A/B acceptance: A publishes a reviewed answer; B receives a verified
  accepted record and answers offline with **zero LLM calls**. Test duplicates,
  paraphrases versus near misses, different photos, private-data leakage,
  rejection, forged receipts, interrupted sync and invalidation after challenge.

- [ ] Keep personal SAGE, fleet management, radio, Commons proposer, validator,
  recovery, and Root credentials separate.
- [ ] Enroll a bounded PUBLIC Commons proposer only after radio and device
  provisioning gates pass.
- [ ] Validate durable proposal receipt states and privacy/redaction policy.
- [ ] Connect read-only Commons query context with freshness/provenance labels.
- [ ] Keep Lanterns out of validator authority; production validator topology
  and governance remain a separate product-owner decision.
