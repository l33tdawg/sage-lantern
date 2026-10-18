# Next session — finish C before D/E/F

Updated 2 October 2026 after live r17 verification, reconciliation of an
incomplete prior auth receipt, protected-storage audit, crash-policy hardening
and kiosk privilege separation, plus SAGE consolidation and source-bound native
desktop guard candidate preparation, root-created launch authority, explicit
guarded sleep profile and a disabled matched review bundle.
C's installed software baseline passes; physical and product acceptance remain
open. No recurring automation is scheduled.

**2 October authentication overlay is deployed on C.** Seven pinned files were
installed over the exact r17 closure; the runtime now has 116 Python modules.
Auth, voice, status and the existing fleet-admin server are active. SAGE, LLM,
VLM, GNOME and all 201 served UI assets are unchanged. The historical r17 receipt
is retained; this is r17 plus the auth overlay, not a new full appliance release.
Real PIN/token/context checks passed, including matching context on protected
link/status replies and 401/403 after token revocation. The device is locked
after verification; UI context subscription/reply fencing remains pending with
Ember and private-action policy/physical acceptance remain open.

The initial voice shutdown reported `memory_drained=false`. After an
owner-authorized vault login, signed inventory under the existing producer lock
found one control record and no queued, dispatching, unknown or pending-media
work. The lock stayed held through installation and unchanged-inventory checks.
The exact negative receipt was archived unchanged, not relabelled as a clean
historical drain, and the deployment guard was released after fresh health
checks. Do not retry either historical apply command. Revoking a dashboard/device
session does **not** relock the SAGE serving vault; verify live storage metadata.
Cleanup of the first temporary dashboard cookie is unconfirmed because the first
login used the wrong sign-out route; no cookie or PIN was exported or persisted.
Subsequent broker verifications revoked their own cookies and test device tokens.
See [deployment and recovery evidence](../../work/auth-integration-20261002/unit-c/DEPLOYMENT_RESULT.json).

Rook recovered the inbox with the same pinned identity after the original MCP
transport closed. Ember's UI writer returned the retained 42/42 integrated
driver record and a separate Settings review with two failures, then delivered
the corrected Settings closure against Rook's decisions. Current hashes and
the navigation/focus causes were checked; this is not whole-UI acceptance.
Ember acknowledged the earlier replies and has working tools again. The UI
context fence remains pending. Preserve historical frozen results beside the
dated correction and its exact changed-source closure.
See [inbox review and decisions](../../work/auth-integration-20261002/unit-c/INBOX_REVIEW.json).

**Next auth increment is a disabled source candidate.** The actual AuthBroker
and DeviceLock now compose with an installation-pinned consent issuer and the
existing action coordinator under one gate. The scoped regression passes 326
tests and 178 subtests. Synthetic PIN verification, consent, node policy and
canonical records remain explicit; this source is not installed on C. Dependent
workers participate in auth drain, and unknown cleanup cannot become confirmed
merely because those workers finish later. The qualification digest includes
the new issuer/auth sources, so old qualifications do not admit this revision.
The local auth file includes unrelated native-desktop work. A separate exact
C-matched candidate is now prepared against all 116 captured deployed runtime
modules: 173 tests and 97 subtests passed, with one inapplicable desktop test
skipped. Its route/handler AST is unchanged, and its three changed modules
compiled with C's Python 3.11.16 without imports or device writes. It is uninstalled.
See [owner-binding candidate and gates](../../work/auth-integration-20261002/owner-binding/README.md).

The fresh read-only C policy observation pins its running binary and config.
The federation master switch is false. Generic P2P/relay settings and an omitted
required-voter field block the strict private-policy attestation; no active
federation/export was inferred. The reviewable four-field policy correction is
uninstalled and needs matching core round-trip/provenance and fresh live
canonical/export/grant evidence. The exact core request was read-confirmed, then
explicitly declined at 08:07 UTC because that chat is assigned to the response-loss
bug/REST-budget PR402; that fix does not qualify C. Owner authorization to queue
the prepared C correction in the existing core chat is pending. No keys, records
or service state were changed by this observation.

Ember's exact UI writer returned the Settings F1/F2/R1 correction and acknowledged
the earlier decisions. Current hashes, retained results and preserved historical
bytes are independently reviewed; this remains fixture/source evidence. The
409 wording and auth context/epoch fence were **not started in Ember's 07:57
reply**, pending the owner's direct go-ahead in its own UI chat. Later shared
source contains new auth/409 work; its writer closure/provenance is still
pending. Do not infer authorization or acceptance from that observation. Rook
preserves the UI lane and returned draft findings about original admission
context and obsolete status polls with the exact Settings reply. The other
menu-origin issue remains a separate unmeasured follow-up.

## C now

C is `lantern-c`, **currently `192.168.50.2`** (mDNS `lantern-c.local`), Orange
Pi 5 Plus 16 GB, with SPI/NVMe boot and its independent identity. It is already
commissioned. *Address updated 14 Oct 2026: these notes said `192.168.50.104`,
which answered nothing at layer 2 when checked, while `lantern-a` and `lantern-b`
still replied on that subnet — so the unit moved, not the network. There is no
`lantern-c` ssh alias (the alias file defines only A and B), so reach it by
address, and re-verify before relying on either number.*
Do not repeat base installation or key initialization, wipe its state, or clone
its NVMe to D/E/F.

The installed release is **`lantern-cf-20260930-r17`**, manifest
`8f4be6fa6e903f1d4c9af249dd74826b2c1428b4cbd70ca827807a3d95226a00`.
It was installed on 30 September and booted on 1 October. The receipt's
`restart_required: true` records the installation requirement; today's service
start times show the runtime transition has happened. Do not rewrite that
historical receipt or reinstall r17 to clear the flag.

Live checks on 1 October:

- All 115 installed runtime Python modules match the r17 payload; no missing,
  extra or mismatched modules.
- All 119 console-bundle destinations match the deployed bundle3 manifest.
  App build is `4d6d0f82-4083-49b9-9989-81cb0795f1af`.
- All nine console-lane checks pass, including typed/review readiness and the
  sleep guard check. Audio admission exits 0.
- Personal SAGE, device-auth, status, fleet-admin, question loop, NPU chat and
  NPU vision services are active and enabled; no failed units.
- The owner vault is enabled, an existing vault is present and recovery backup
  is confirmed. Initialization is unavailable because setup already exists.
  A locked `/question/start` request returns `403 device_locked`.
- The session supervisor reports portrait kiosk mode, scale 1.0, keyboard
  attached and no mouse. GNOME idle lock, lock shortcut and unmanaged automatic
  suspend are disabled and pass read-back checks.
- Automatic sleep remains disabled: its opt-in marker is absent. No inference,
  capture, suspend, reboot or owner unlock was performed in this verification.

Evidence: [1 October closeout](../../work/c-closeout-20261001/README.md),
[r17 installation](../../work/cf-package-20260930-r17/C_INSTALL_20260930.md),
[r17 package](../../work/cf-package-20260930-r17/README.md),
[console activation runbook](CF_CONSOLE_LANE_ACTIVATION.md).

## Sleep receipt reconciliation

The first live check refused with `Sleep drain hooks unavailable`. Auth receipt
`b3bc900c4ea946379ff2f2a8c024de7b.json` belonged to an ended prior instance and
still reported `closing=false`, `drained=false`, `local_revoked=false` and
`upstream_cleanup=unknown`. The live invocation is
`3d243d0a3d894026bb7bf48b71e1812c` on boot
`10b61161-4469-43de-9fc3-d947ee7147fb`.

Under the owner-approved 30 September archive policy, the old receipt was moved
unchanged to `/var/backups/sage-lantern-auth-drain-20261001T090845Z/`, with a
`RECONCILIATION.json` recording its digest, metadata and unknown historical
outcome. The current receipt was retained unchanged. The sleep guard then
passed. This manual reconciliation does not prove a clean historical drain or
automatic recovery after power loss.

## Resume order

1. **Qualify power before heavy model or sleep acceptance.** The owner confirmed
   on 1 October that the adapter has not arrived. Three 5 V/4 A adapters were
   bought, ship by 7 October; meter acceptance remains open. Orange Pi 5 Plus
   does not negotiate PD. Require
   5 V held at least 4.8 V under uncapped load and at least 4 A available with
   the cable in the path. Do not repeat the known resetting CPU load on the
   existing under-provisioned supply.
2. **Owner panel acceptance.** Confirm the current console is visible, unlock
   on the glass, check the touch corners/centre/keypad/drawer/scrolling, and ask
   a typed question after power qualification. A rehearsal with substituted
   lock/epoch checks never counts as this acceptance.
3. **One guarded sleep cycle on C.** Follow the reviewed
   `lantern-idle-suspend --once --test-wake-after 60` procedure after power
   qualification. Verify suspend counter +1, unchanged boot ID and resident
   model instances, console resumed, audio admission rc 0 and sleep check green.
   Automatic sleep opt-in remains a distinct operator step. Photo-in-flight
   rehearsal needs fitted CSI camera and voice capture; panel-off on the HDMI
   profile remains best-effort.
4. **Peripherals and device UX.** Camera, microphone/PTT, XIAO ESP32S3 +
   Wio-SX1262 radios, GNSS, supported WiFi and licensed offline maps still need
   their physical commissioning and acceptance. The ES8388 capture contract
   is not proof of intelligible microphone capture. Presence is not touch or
   camera acceptance. C–F HDMI/USB-touch measurements are bench evidence;
   product display acceptance must be repeated on the final DSI hardware.
5. **Security, messaging and public retrieval.** Complete the protected
   storage/lifecycle gates under the combined C–F acceptance task, and
   authenticated bidirectional delivery/discovery and public-cache/private-home
   separation under their separate SAGE tasks.
   The read-only storage audit is done: protected paths are configured, inspected
   legacy content is empty, and synthetic tests pass (240 Python plus 59 native).
   **Crash-policy configuration is now applied and passes read-back**:
   Apport/forwarding/upload collectors are masked, the kernel dump handler is
   disabled, and explicit zero core limits are configured. Existing reports are
   preserved, including the GNOME memory report. Lantern, model and desktop
   instances and boot ID are unchanged. Fresh-session hard-limit checks,
   browser minidumps, a disposable crash canary, reboot persistence and old
   artifact disposition remain open; see [crash policy](CRASH_PRIVACY.md).
   **Kiosk privilege configuration is also applied and verified**: `sage` was
   removed from disk/sudo/docker/systemd-journal, its explicit sudo deny rule is
   active, and an obsolete rule for an absent PSD helper was backed up and
   removed. Separate root key-only SSH still works. Running graphical processes
   retain cached groups until a fresh session. See
   [operator boundary](KIOSK_OPERATOR_BOUNDARY.md).
   Next independent software work is the **whole docked-desktop PIN boundary**:
   the current supervisor opens a desktop window without authentication and
   full GNOME runs beneath portrait. Account changes do not close D-022.
   Preserve apps behind a PIN lock by default; do not sign out or discard unknown
   work without the owner's choice. No session or model instance was restarted.
   **Desktop PIN compatibility preflight is now recorded** under
   `work/c-desktop-pin-preflight-20261001/`. The installed GNOME 50.1 compositor
   has a native modal lock shield and GDM/PAM reauthentication, but C's current
   settings disable locking and its stock password stack does not verify the
   SAGE PIN. Installed source also shows that an external session-mode JSON
   cannot supply an `unlockDialog`, and missing/failed dialogs can deactivate
   the shield. A successor must cover startup/failure behavior and a dedicated,
   session-bound RAM-only compositor grant in the existing broker; its public
   unlocked status cannot grant desktop access. This is source-bound preparation,
   not implementation or D-022 acceptance. Seven service lifecycles and boot ID
   were unchanged during the read-only preflight. The owner reconfirmed the
   adapter is still pending; power and fresh-session gates remain open.
   **The desktop-auth backend candidate is now implemented locally and tested**
   (330 tests plus 287 subtests): separate RAM-only native grants, shared PIN
   verification/rate limits, revocation/race fences and an optional root-peer-only
   Unix control surface. Pending/active grants inhibit sleep readiness. Source
   and evidence are frozen under `work/c-desktop-pin-candidate-20261001/`.
   It has not been installed on C and does not yet enforce the desktop boundary.
   The trusted native-peer adapter and mandatory GNOME PIN guard now have
   source-bound local candidates, with exact-PID enrollment, EOF/PIN cancellation
   fences, opaque/modal concealment, shortcut restore fences and a guarded
   supervisor mode lease. They are not installed on C. Frozen source and checks
   are under `work/c-desktop-native-guard-20261001/`; see
   [native guard candidate](DESKTOP_PIN_GUARD_CANDIDATE.md).
   **Root-created startup and guarded sleep now have local candidates.** Root
   constructs the Shell environment and enrolls only its own child; exact kernel
   credentials gate readiness. Authority loss locks without automatically
   restarting apps. The explicit `wayland-mutter-native-pin` profile returns to
   a fresh PIN after wake, with no public-unlock fallback. A disabled matched
   review bundle with 138 hashed payloads is frozen under
   `work/c-desktop-root-launch-20261002/`. Local verification passes 310 distinct
   Python tests plus 108 subtests and 19 JavaScript tests. It contains no
   activation file or installer and is not a deployable release. Read-only
   checks on 2 October confirm the same boot, Shell/session and seven service
   identities; C has no native guard socket.
   Next finish root-controlled GDM/session selection and user override/alternate
   launch closure, qualify root-child logind/DRM seat access, and integrate the
   complete runtime snapshot against r17. Then qualify actual startup/failure,
   the guarded sleep profile, portrait duplicate-login UX and physical input in the
   guarded fresh-session window after power acceptance. Local/mock tests do not
   close the whole-desktop lock. Stock Wayland Shell failure still shuts down
   the session; application-preservation behavior needs explicit resolution.
   C's actual shell is `org.gnome.Shell@ubuntu.service`, using the `@.service`
   template with `Restart=no`; do not target an assumed `@wayland` instance or
   depend on optional extensions surviving session failure. See
   [backend candidate and remaining guard work](KIOSK_OPERATOR_BOUNDARY.md).
   Exact deployed-native photo/reopen and physical lifecycle acceptance
   remain open. See [audit findings](C_PROTECTED_STORAGE_AUDIT_20261001.md).
   An enabled vault or locked-request refusal alone is not full security
   acceptance. Do not import public packs into the private home to bridge the
   missing node-side public-domain policy.
6. **D/E/F only once C is accepted.** Follow the consolidated replication
   playbooks with an independent identity, installed-release anchor and evidence
   per unit. Never accept a unit using C's receipts. Inspect actual D/E/F state
   before applying the r17 predecessor-closure procedure: C's old hand step is
   evidence of a local-deploy collision, not a universal prerequisite.

Portrait is the default (D-020). Docked desktop requires login (D-022); losing
the RAM-only browser token on a mode switch is intended. Running conversation
state should survive without the switch unlocking the UI. PIN recovery UX is
deferred to the owner's later UAT/UI/UX pass.

## Other units and current SAGE tasks

A/B are Lamplighter carriers with approved public file caches, seed services,
operator commands and dashboard screens. Their old personal stores were wiped
with owner approval; legacy endpoint services remain inhibited. LAN pack
transfer/resume evidence does not establish LoRa delivery or repeater readiness.
Their endpoint UX is not a prerequisite for finishing C. See
[device roles](DEVICE_ROLES.md) and [operator runbook](AB_OPERATOR_RUNBOOK.md).

| Work | Current task | State |
| --- | --- | --- |
| r17 release integration | `5dfec3ed-f841-40f1-84e5-6e0afb862a3c` | Done; installed and verified on C |
| C–F device acceptance: commissioning, power, sleep, protected storage, desktop PIN and UX | `91af02e7-3b17-46c8-aeab-6a4ef9fb89d7` | In progress; crash/account configuration verified; desktop PIN, fresh-session, power, physical and lifecycle acceptance open |
| D/E/F replication | `fac41d94-8a36-43b1-8a90-0e546fc5e2e0` | Held until C is accepted; r17 pointer refreshed |
| Messaging/discovery | `cd2312c4-5f8c-4da8-b421-6710152ab8b4` | Open |
| Public knowledge/cache/scale | `178dfca7-822f-4ce9-a992-acb7228f35ee` | Open |
| A/B radio/repeater and C–F integration | `b6ce3661-40ce-4f6b-b462-7c2b375e8ee6` | Open; preparation complete; r17 resume pointer refreshed |
| G/H hardware and internal display/camera bring-up | `92680e1c-6832-4bae-ab88-09bc2cadb4ef` | Planned; retain existing purchase/decision and physical connector gates |
| Trust-root distribution and key rotation | `30741772-d618-4fe0-a426-9805b7daf0e9` | Planned; signed update/rotation qualification open |
| Fleet production operations | `d8cca61b-aa40-41ee-a90e-410933afd8af` | Planned; monitoring, scoped roles and credential/backup lifecycle open |
| Mail/domain follow-ups | `7c72ccdf-d446-43d2-a3bf-0bdb61ebf4c6` | Planned; owner actions/decisions and forwarding audit retained |
| December readiness and January/February launch | `fc77ff85-5768-407e-86f5-851d27f92e45` | Planned; repository, evidence and publication gates retained |
| Optional asynchronous online research | `008da7ad-a6b6-4df3-a2e5-4dd03d020b0f` | Deferred proposal; scope/default approval remains open |

SAGE is the durable backlog. Historical task maps and release evidence retain
their dated observations; use the current successor tasks and live unit state
for new work. Four personal Lanterns are not four public validators.

The earlier 1 October consolidation reduced **16 open tasks to 13** by merging
the sleep and deferred PIN-recovery items into commissioning and UX. The latest
owner-requested consolidation reduced **13 to 11** by combining commissioning,
security and device UX under `91af02e7-3b17-46c8-aeab-6a4ef9fb89d7`.

The compact successor links all three immutable predecessor records:
`9cd35402-4d8b-4b33-b1c6-06c334e1cfac`,
`bfdd3303-9119-426e-8461-c7a8c617e358` and
`c6e5bd53-b7a4-4508-9579-d513d6b15bb6`. Their detailed requirements, evidence and
constraints remain binding. The approved completion fact
`4061287c-6e2c-4aff-9df0-1bc4db4137cc` records the applied crash/account hardening
and supersedes those predecessors' earlier configuration observations. Physical,
security and lifecycle gates remain open; PIN-recovery UX is still deferred.

The successor was committed and read back before the predecessors were marked
`dropped` as superseded, rather than completed. D/E/F, A/B, messaging and public
knowledge tasks also link to the successor. All ten other open task descriptions
and statuses were retained; the full paged backlog read-back verified 11 open
tasks with no remaining page. Older task IDs in immutable descriptions refer to
historical scopes; follow their successor links and this current map.
