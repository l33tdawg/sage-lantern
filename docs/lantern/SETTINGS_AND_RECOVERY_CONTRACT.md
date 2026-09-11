# Lantern Settings and Recovery Contract

**Status:** Partially implemented; local history controls deployed, broader operator/recovery contract proposed.  
**Source review:** 2026-09-05; dated implementation increment includes Unit A validation, earlier inventory is source-only.  
**Scope:** Everyday preferences, operator feature gates, local authorization, encrypted personal recovery.  
**Placement:** Undecided: touchscreen, external local administration, or a split. Fleet Admin is a separate authority plane regardless of presentation.

## 1. Boundaries and decisions

**Latest implemented increment (2026-09-05):** Menu → Settings controls question
and attached-photo saving. Original JPEGs are deployed in linked local history;
new stores default both on with a shared-device notice. Migration preserves
existing question preferences and records. Saving off keeps existing text/photos
under retention limits; delete/clear remains separate and revokes in-flight
consent. Capability guards preserve old-backend compatibility. Privileged
operator/recovery controls are not enabled. Historical inventory below describes
the earlier review where it conflicts with this dated increment.

Standalone `software/runtime/src/sage_lantern/history_media.py` provides bounded
UUID-addressed original-JPEG storage, digest checking, private modes, atomic
rename/fsync, a default 512 MiB free-space floor and a one-second lock wait.
It is now integrated with voice-session consent, history v2 metadata, orphan
cleanup and origin/host/header-guarded loopback access. This is shared-device
access, not individual-user authentication. Header checks do not prove full JPEG decodability. Directory
ancestors and same-UID/root writers are trusted; ACL isolation requires deployment
review. No real photos or keys were read for tests. Actual migration preserved
five existing questions; physical photo/reboot acceptance remains pending.

The initial review was documentation-only. The first backend foundation now exists
in `software/runtime/src/sage_lantern/settings.py`: immutable typed definitions
and a pure read-only projection with separate desired/effective values, explicit
unknown/stale observations, and unavailable messaging/recovery. Its observations
are caller-supplied, not live probes; no authorization, persistence, revision
CAS, mutation API, backup, or UI wiring is implemented by that module. Fourteen
focused tests cover these limits. This is only part of phase 1, not a settings
release. No key inspection or export was performed. Source references below
describe the inspected checkout, not deployed versions; concurrent changes may
move line numbers.

The next offline slice, `software/runtime/src/sage_lantern/settings_store.py`,
persists **drafts only** for voice capture, photo answers and spoken responses.
A fixed SQLite snapshot/revision uses transactional compare-and-swap, bounded
lock waits and strict schema/value checks. No accepted desired state, effective
observation, consent or execution receipt is created. History is deliberately
excluded so a saved draft cannot replace its existing informed-consent API;
messaging and recovery are also excluded. Storage requires a pre-provisioned
operator-owned 0700 directory, private regular database files and trusted
ancestors without inherited ACL access. Link/mode checks are not a filesystem
sandbox against root or a malicious same-UID process. This module is not
installed on Unit A or exposed through a web endpoint.

MUST/MUST NOT describe implementation requirements, not claims that these protections already exist. Existing normative identity boundaries remain in `docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md:23`, `:74`, and `:303`.

1. A requested value is not an effective capability. No optimistic “enabled” status without verified local application and fresh observation.
2. Everyday controls cannot grant operator, personal Root/Admin, Commons, or Fleet authority. Fleet ownership does not imply personal-memory ownership.
3. Local physical presence plus authorization is mandatory for sensitive operations. A loopback request, kiosk session, browser Origin, SSH connection, or Fleet login alone is insufficient.
4. “Back up keys” means an encrypted, consistent recovery set for the same personal SAGE, not individual private-key downloads or a device-cloning facility.
5. No public Commons validator key may ever be generated, imported, backed up, restored, or displayed on a Lantern. The full personal node's personal validator material is a different trust plane and must remain consistent with its own consensus state.
6. Unit B is independently provisioned. Unit A keys, personal home, signed queues, nonces, receipts, and cache trust state MUST NOT be copied onto Unit B.

## 2. Existing functionality and gaps

Source references are repository-relative `path:line` anchors. “Present” means code exists; it is not physical acceptance or proof of the running configuration. Absence findings are limited to the reviewed settings/configuration, runtime, provisioning, recovery, and UI paths.

| Area | Actual implementation and evidence | Missing or limited behavior |
|---|---|---|
| Runtime configuration | `software/config/lantern.example.toml:1` contains runtime paths, STT threads/model, LLM endpoint/model, TTS, SAGE identity/domain, and static context. `software/runtime/src/sage_lantern/config.py:27` loads TOML and checks required values. `software/runtime/src/sage_lantern/cli.py:21` selects static versus SAGE context and optional TTS. | No general settings API, revision reconciliation, strict feature registry, or transactional multi-service application here. Parser defaults TTS to true (`config.py:44`), while the example explicitly disables it; neither is a live-device observation. Generic endpoint configuration does not prove enforced offline-only networking. |
| Real local history setting | `scripts/lantern/unit-a-voice-loop.py:114`, `:147`, `:208`, `:250`, and `:914` implement opt-in text history, atomic file replacement, consent generations, limits, deletion, and periodic maintenance. `apps/lantern-fleet-admin/app/device/history.tsx:28` invokes these real endpoints. | This is shared-device history, not per-user authenticated storage. It is not a general settings engine or encrypted backup. Limits are fixed constants (`unit-a-voice-loop.py:42`), not user-adjustable controls. |
| Local API boundary | `scripts/lantern/unit-a-voice-loop.py:466` binds control to loopback. `:525` and `:558` validate Host, Origin, headers, body bounds, and route-specific payloads. | These are browser/network boundary checks, not proof of a human owner's identity or fresh physical presence. History requests do not establish an owner session. Do not reuse this boundary alone for recovery. |
| Everyday voice and clear | `apps/lantern-fleet-admin/app/device/talk.tsx:127` exposes start/send/cancel. `scripts/lantern/unit-a-voice-loop.py:272` arbitrates touch/physical recording ownership; `:887` handles clear and removes session audio on normal cleanup. `app/device/screen.tsx:102` has a separate current-answer Clear action. | No master persistent microphone setting or implemented wake-phrase setting found in these paths. Current-answer clear is not history deletion, SAGE erasure, or secure media wipe. Abrupt power loss is not covered by a Python finally block. |
| Voice enablement and TTS | `scripts/lantern/activate-unit-a-local-voice.sh:20` has failure cleanup; `:43` checks prerequisites; `:69` requires TTS false for the screen-answer prototype. `software/systemd/sage-lantern-voice-loop.service:3` separates required core services from wanted PTT. | Activation is a supervised script, not an everyday toggle. Its failure path stops/disables services rather than restoring an arbitrary prior running configuration. It still requires `PTT_VERIFIED`; do not make a new touch-only capability inherit that historical physical-PTT gate. |
| Camera versus photo reasoning | `scripts/lantern/unit-a-camera-server.py:192` binds locally; `:282` reports camera presence separately from vision readiness. `scripts/lantern/unit-a-voice-loop.py:965` exposes an explicit `--photo-ready` flag. `apps/lantern-fleet-admin/app/device/camera.tsx:136` gates photo questions on reported readiness. | Preview availability does not prove model/projector readiness or photo-answer quality. A command-line flag is not independently measured inference health. No general camera privacy-setting persistence is established here. |
| Optional vision activation | `scripts/lantern/install-vision-local.sh:42` checks artifacts, `:55` requires hash-bound validation evidence, `:71` builds paired model/voice drop-ins, `:113` saves/restores prior drop-ins on failure, and `:158` exposes check/enable/disable. | Useful bounded script, not a crash-recoverable settings transaction across all services. Its own health check explicitly is not inference validation (`:108`). Disable removes its own overrides; it does not necessarily reproduce every previously active profile. |
| Fleet preference UI | `apps/lantern-fleet-admin/app/dashboard.tsx:242` labels AI controls as unsent desired-state fixture drafts; `apps/lantern-fleet-admin/app/data.ts:91` supplies voice/wake/TTS/history/offline/Commons fixture values. | These switches do not change Lantern state. Fixture TTS/voice observations must not override local evidence or the screen-answer requirement. |
| Fleet protocol versus execution | `software/runtime/src/sage_lantern/management.py:23` allow-lists declarative actions; `:372` verifies signature, target, expiry, expected hash, installed handler, and durable replay acceptance before execution. `software/runtime/DEVICE_AGENT.md:3` says the outbound client does not fetch or execute commands. | Allow-listed `apply_ai_preferences`/`apply_device_profile` are not installed feature handlers. Durable acceptance is not a durable end-to-end transaction/result journal. On handler exception the protocol uses supplied prior state (`management.py:417`), not measured rollback proof; partial side effects need reconciliation. |
| Enrollment and Fleet roles | `software/runtime/DEVICE_AGENT.md:23` documents separate locally generated management/TLS keys and comparison-code enrollment; `:74` documents owner-only state. `apps/lantern-fleet-admin/lib/operator-authorization.ts:26` maps scoped Fleet read/enrollment capabilities. | Enrollment possession proof is not reusable personal recovery authorization. `docs/lantern/DEVICE_AGENT_MANAGEMENT_CONTRACT.md:50` still calls enrollment a next slice; the runtime documentation is more specific about implemented enrollment. Neither establishes deployed feature-command execution. |
| Personal voice authority | `scripts/lantern/approve-local-voice.py:41` requires local endpoint/domain and existing distinct identities; `:53` checks current personal Root; `:60` retains a same-device consent-key copy. `docs/lantern/UNIT_A_BOOTSTRAP.md:154` requires protected backup of both voice-key locations. | A root-run shell helper is not a local human-authentication UI. Recovery must account for the external voice-key path as well as its personal-home inventory copy, without displaying either. |
| Provisioning and clone prevention | `scripts/provision-lantern-unit.sh:11` promises no private-key handling; `:125` checks public inputs/attestations; `:147` rejects duplicate fingerprints within the submitted unit inventory; `:187` prohibits cloned identities/state; `:244` writes PROVISIONED last. | Attestations and within-unit comparisons are not automatic cross-fleet duplicate detection or cryptographic proof that another instance is offline. |
| Recovery rehearsal | `scripts/lantern/unit-a-personal-recovery-test.sh:23` checks a specific SAGE process pattern is stopped; `:45` calls full backup; `:53` calls restore; `:68` compares public identities and runs upgrade preflight; `:87` removes secret-bearing duplicates after success. | This is an in-place Unit A rehearsal, not a production encrypted workflow. It specifies a `.tar.gz` archive and no encryption layer, does not explicitly archive the external voice key, and leaves SAGE stopped. It has no general supervisor fence, encrypted removable-media handling, owner-authentication ceremony, or failure-path cleanup journal. The invoked SAGE backup/restore internals are not inspected here. Its “atomic” message is not independent proof of crash safety. |

Existing test anchors: `scripts/tests/test_unit_a_voice_history.py:42` covers history defaults and subsequent consent/retention/permission cases; `scripts/tests/test_unit_a_voice_photo.py:230` covers cleanup ordering and `:305` separates current-answer clear from history. `scripts/tests/test_provision_lantern_unit.py:83` checks required recovery attestation. Protocol/configuration suites exist under `software/runtime/tests/`. These are starting points, not evidence that the proposed contract passes; no tests or device scripts were run for this document.

## 3. Actionable settings and feature matrix

**Later user decision, pending implementation (2026-09-05):** new-device
history and full captured-photo saving should default on with a clear shared-use
notice before retained capture and a Settings opt-out. Preserve existing
explicit opt-outs and fail closed on damaged established preferences. Turning
off future saving must be separate from deleting existing items. This supersedes
the proposed default-off history row below for the next product slice, not the
currently deployed behavior. Existing history is still opt-in, text-only, and
its disable API deletes history. Do not flip a constant or merely move that
toggle without migrating its semantics. Full media retention, safe storage
policy and optional future Gallery are tracked in
[the premise test](PREMISE_TEST_ACCEPTANCE.md).

Roles: **E** = everyday person using the shared unit; **O** = locally authenticated device operator; **P** = authorized personal SAGE owner/current Root or explicitly delegated recovery authority; **F** = scoped Fleet operator. O and P are not interchangeable. The defaults below are proposed safe product defaults, not observed Unit A values.

Every setting MUST expose desired value, effective value, capability status/reason, last verification time, and whether a restart is required. Unsupported rows are informational, not working-looking switches. Actions such as backup, clear, enrollment, and restore are deliberate workflows, not booleans.

| Setting/action | Everyday authority | Operator authority | Proposed default; prerequisites; application behavior |
|---|---|---|---|
| Ask / cancel / clear current answer | E, explicit per-session action | O can disable capture for maintenance | Existing functionality. One capture owner for touch and physical PTT. No restart; clear does not delete saved history or personal SAGE. |
| Save local text history | E can decline, disable, or clear; enabling requires informed shared-history consent and any owner policy | P sets whether shared retention is permitted; F cannot enable it | Off. Existing API and consent-generation behavior; enabling affects future successful text answers only. Disable deletes saved entries. No restart and no rollback that resurrects deleted text. |
| History retention limits | E sees limits and deletes sooner | P may choose only within supported privacy caps | Existing fixed maximum: 100 items, 30 days, 4 MiB. Configurable lower caps are missing. Never silently raise caps; prune before exposing results after downtime. |
| Microphone availability / physical PTT | E may turn capture off; ordinary Ask is not blanket consent | O validates hardware; P authorizes a persistent re-enable policy | New persistent control is missing. Turning off cancels capture and blocks both input paths at the server. Touch input must remain usable without GPIO/PTT hardware when otherwise ready. Enable requires working capture and local voice readiness; no background listening. |
| Wake phrase / hands-free activation | No enable until a real gated implementation exists | O/P must review privacy, resource budget, and explicit consent | Unsupported, off. No microphone may start merely because a fixture toggle changed. Separate always-listening consent and acceptance are required before promotion. |
| Spoken responses / volume | E may mute; may enable only within validated local policy | O validates engine, model, playback and audio route | Off for current screen-answer prototype. Runtime TTS configuration exists, but safe integrated settings/playback gating remains missing. Drain active speech before changing route; no claim that volume controls exist. |
| Camera preview / capture permission | E explicitly opens/captures; may disable | O grants hardware/service availability; P approves persistent privacy policy | No automatic capture. Existing camera interaction is separate from missing persistent privacy switch. Disable must close preview/capture and revoke access server-side, not just hide Camera. |
| Ask about photo | E per-photo consent only after ready | O enables validated model/projector pair under maintenance lock | Off unless fresh hash-bound capability checks pass. Existing flag/script is only a building block. Paired model/voice changes require idle window, memory budget, and rollback; ordinary text voice must still work when vision is unavailable. |
| Display brightness / text size / sleep | E within safe limits | O validates display drivers, kiosk behavior, accessibility bounds | Proposed, no persistent controls identified. Sleep must not hide active recording or a recovery confirmation. UI placement and exact defaults await physical usability review. |
| AI model/profile, STT threads, inference limits | E selects only approved profiles once implemented | O owns artifact/version selection and resource ceilings; F may eventually propose bounded profiles | Preserve currently verified profile; no auto-change from this contract. Validate hashes, architecture, endpoint locality, disk/RAM/thermal budget, compatibility and voice latency. Apply at idle with paired config/service rollback. |
| Offline-only AI / external inference | E may request stricter privacy, never bypass it | P approves personal-data egress policy; O implements enforcement | Local-only required baseline. Existing loopback config is not a firewall. Remote inference enablement remains unsupported until an explicit egress allow-list, consent boundary and negative network tests exist. Do not add an arbitrary URL textbox. |
| Personal SAGE access/domain/voice-agent policy | E only uses already authorized capabilities | P governs agents/domains/clearance; O may inspect sanitized readiness | Not an everyday/Fleet switch. Existing approval helper requires exact local Root. Policy changes use owning SAGE API, not edits to key files or a Fleet profile. Static context is a test aid, not a substitute for verified personal retrieval. |
| Commons query/cache | E may decline suggestions | O configures approved client; relevant Commons authority grants identity/scopes | Off/unavailable until eligible authenticated bounded adapter, freshness labeling and privacy gates pass. No second public Observer or validator process. Keep cache separate from personal backup. |
| Commons publication / queue transmission | E explicitly previews and consents to an exact PUBLIC derivative | P governs derivation; Commons authority governs proposer eligibility; F cannot manufacture consent | Off until publication contract acceptance. Query enablement does not enable publish. Disabling transport pauses pending queue without rewriting signed bytes; re-enable rechecks eligibility and expiry. |
| Wi-Fi / network profile / radio transmit | E sees measured connection state, not secret credentials | O local maintenance only; region/antenna/hardware gates apply before transmit | No generic settings handlers established. Preserve measured configuration; unknown region means no TX. Network apply needs local timed rollback so remote loss cannot strand the operator. USB detection is not a radio link. |
| Fleet enrollment / sanitized telemetry | E sees enrollment state and privacy notice | O explicitly pairs; F performs scoped enrollment approval | Separate from personal account. Existing outbound client can report allow-listed sanitized status; no private data or recovery paths. Disabling management must not disable private offline use. No feature-command claim until installed handlers and receipt reconciliation exist. |
| Backup / verify / restore / owner handover | E sees only locally approved coarse status; no archive access | P plus fresh physical presence; O only under explicit P delegation; F has no recovery authority | Missing encrypted product workflow. Use sections 6–8. No key-only download, no Fleet archive upload, no routine cross-unit restore. |
| Factory reset / identity rotation / software release | No everyday switch | Explicit local O/P workflow as applicable; releases may use future bounded Fleet staging | Distinct destructive/lifecycle operations. Reset does not equal recover, rotate does not equal clone, and rollback must not undo privacy deletion or revocation. Require a separate tested implementation before exposing actions. |

## 4. Desired, effective, and capability state

Proposed registry fields: `setting_id`, schema/version, typed value/range, authority plane, consent scope, prerequisites, resource budget, restart group, safe default, rollback class, read-back probe, and evidence expiry. Unknown settings/fields and wrong types MUST fail closed; do not coerce strings to booleans as a general settings policy.

Persist separately:

- **Desired:** accepted revision, values, requesting actor/plane, policy/consent generation, operation ID, expected prior revision/hash, requested time and expiry. An unsubmitted browser draft is not desired state accepted by the device.
- **Effective:** actual values, configuration/artifact hashes, applied revision, boot ID, local measured time, freshness, health outcome and explicit reason when different from desired. Secrets, file contents and personal text never enter this projection.
- **Capability:** `unsupported`, `blocked`, `available`, `degraded`, or `unknown`, with bounded reasons such as `missing_handler`, `missing_artifact`, `owner_consent_required`, `busy`, `insufficient_resources`, `verification_failed`, or `stale_observation`.
- **Operation:** `draft → validated → awaiting_local_authorization → accepted → applying → verifying → succeeded`, or `rejected / expired / rolling_back / rolled_back / recovery_required`. Persist terminal outcomes and unresolved interrupted operations.

Example: photo reasoning desired=on, effective=off, capability=blocked, reason=missing_projector. A saved checkbox, HTTP 202, service active state, or stale green Fleet card MUST NOT turn this into “on.” If verification ages out or a prerequisite disappears, effective becomes unknown/degraded as appropriate, rather than silently remaining healthy.

Policy precedence: immutable safety boundary > local owner privacy policy > authorized operator capability envelope > everyday preference. Fleet requests cannot widen the envelope. Simultaneous touchscreen/external requests use expected-revision compare-and-swap; reject stale requests and show the new state instead of last-writer-wins. Every UI consumes this same local contract.

## 5. Transactional apply and rollback

The existing history atomic-write pattern and vision script are reusable patterns, not a general transaction implementation. Add a local privileged coordinator with narrow typed handlers; never forward a user-provided shell command, service name, file path, environment block, or arbitrary endpoint.

1. **Preflight without mutation:** resolve capabilities, validate complete dependency set, actor/consent, free disk, power/thermal/resource requirements and installed compatible artifacts. Return the exact plan, affected services, privacy implications, outage estimate and rollback class. No downloads, key handling or device probes with side effects disguised as settings preview.
2. **Authorize and reserve:** acquire a durable per-unit maintenance lock shared by settings, releases, provisioning, recovery and benchmark reservations. A held parent benchmark reservation returns busy; do not stop its services. Bind a single-use approval to unit, operation, expected revision, exact plan digest and expiration. Quiesce affected capture/inference only at an agreed idle boundary; privacy-off may cancel immediately.
3. **Prepare:** journal intent durably before side effects. Snapshot only relevant non-secret config, service enablement and active-state metadata into a restricted rollback generation. Stage complete validated config on the target filesystem; fsync files and directories. Do not snapshot history, private homes or keys as ordinary settings rollback data.
4. **Apply:** activate a coherent config generation atomically where possible; journal each bounded service transition. Multiple file renames and systemd operations are not one filesystem transaction: incomplete steps must be recoverable from the journal. Maintain the maintenance fence through restart and verification.
5. **Verify:** read back loaded config and service state, check fresh readiness and the feature-specific probe. Hardware/audio/inference tests require explicit authorization and scheduled acceptance, not covert recording from a settings click. Only commit effective revision once required checks pass; publish an operation receipt with before/after hashes and bounded results.
6. **Rollback on failure:** restore previous coherent config and service enablement/active state, re-run probes, and record the actual measured result. Retain failed desired revision as failed, with effective pointing to verified prior revision. Never fabricate a prior-state hash after an unknown partial change. If rollback cannot be verified, stop affected unsafe features and report `recovery_required`, not success.
7. **Recover after crash:** boot reconciliation resolves the journal before starting affected services. Duplicate operation IDs return stored status/receipt and cannot repeat side effects. A lost response prompts status lookup, not a second apply. Remote retries still require valid authorization and replay checks.

Privacy-off, history deletion, key revocation and publication consent withdrawal are monotonic safety actions: rollback MUST NOT restore deleted content, re-enable collection, reactivate credentials, or recreate consent. An explicit new authorization is required. A previously signed/published external action cannot be “rolled back” by a local config restore; report and follow its owning-plane withdrawal policy.

For network changes, require a local confirmation deadline and automatic recovery to the last known working profile; timer/reboot behavior must be implemented locally, not in a browser. For model/vision changes, restore the matching model/voice configuration together without altering personal SAGE data. Bench performance budgets and their evidence are owned by the parent; do not invent pass thresholds or mark them accepted here.

## 6. Local authentication and shared ownership

### Authorization ceremony

Implement a recovery-specific local service, isolated from camera, ordinary voice/UI and the Fleet agent. It accepts bounded operations over authenticated local IPC. The browser is a presentation layer, never a private-key broker. Minimize privilege and secret-readable filesystem scope; the current shared `sage-lantern` account alone is not evidence of this isolation.

Sensitive actions require both:

- A current local P credential, or a supported offline recovery authority established during owner onboarding. Recovery when the original disk/credential is lost needs a separate recovery environment and offline proof; it cannot depend exclusively on logging into the node being restored. A Fleet password reset cannot replace this proof.
- A fresh physical interaction delivered through a trusted on-device confirmation path. Display unit identity, operation, destination class and plan digest; require a locally generated single-use confirmation bound to those values. Do not treat the enrollment comparison code as a standing recovery credential. Timeout after two minutes, on lock/session change, or on plan change; authorization is consumed once. If a trustworthy touch/physical path is unavailable, block self-service recovery and use a separately reviewed attended recovery procedure, not an SSH bypass.

P authentication secrets and archive decryption material MUST NOT travel through Fleet, be stored in browser local/session storage, appear in URL/query/argv/environment, telemetry, screenshots, logs, clipboard export, or support bundles. Enter via a protected local prompt/IPC channel; rate-limit authentication failures and lock the session after five minutes of inactivity. Presence timeouts use a monotonic clock and do not extend after wall-clock rollback. Exact authenticators and trusted display/input integration are implementation gates, not existing features.

If external administration is chosen, it may show a sanitized plan/status and request a local ceremony. It MUST NOT turn a web login into P authority or receive plaintext/decryption material. Any local remote-admin pairing requires authenticated transport, short-lived scoped sessions, request forgery/replay defenses, and final independent confirmation on the physical device. Touchscreen-only placement uses the same backend authorization; proximity alone still does not prove ownership.

### Account and owner implications

- The existing kiosk and text history are shared. A person who asks a question is not automatically a separate authenticated SAGE owner. Clearly state that retained text may be visible to the next person using the unit; default off. Until per-owner isolation is implemented, do not label it “my private history.”
- Distinguish OS service account, current local human owner, personal SAGE Root/Admin/agents, organizational asset custodian, and Fleet account. Maintain explicit delegation and revocation; employee departure or Fleet reassignment does not hand over personal memories.
- Encrypted full personal recovery necessarily contains personal state and privileged recovery material. Consent must name the personal SAGE being backed up and the authorized custodian. A shared-unit organization needs an explicit custodianship policy before backup; daily users must not receive archive access merely because they used the device.
- No account selector may switch between overlapping homes or imply supported multi-user isolation. Separate-user memory access, attribution and unlock/lock boundaries require their own supported SAGE authorization design before release.
- Owner handover requires the existing owner's explicit export/retention decision and a reviewed SAGE handover procedure, or sanitized reprovisioning with fresh identities for the new owner. Do not restore the previous owner's archive under a new owner's login. Revoke old local/Fleet delegations separately and track externally held backups; deletion of the live account does not delete offline copies.

## 7. Encrypted backup contract

### Contents and exclusions

The product recovery unit is a **complete stopped personal-node backup** produced by the supported, version-pinned SAGE backup facility, wrapped in authenticated encryption. Do not reconstruct it by hand from selected Root/validator files. Confirm with the owning SAGE implementation that its manifest includes the complete database/consensus state, signing safety state, identities, policies, and required configuration before enabling production backup.

Include a narrowly enumerated, encrypted personal adjunct for the external ordinary voice-agent key and necessary non-secret voice config, because `docs/lantern/UNIT_A_BOOTSTRAP.md:154` requires both voice-key locations. Check internal consistency entirely within the authorized recovery process. A mismatch blocks backup/restore; do not silently select one copy. Treat both copies as one identity, never as two agents.

Exclude by default: shared history, recordings, photos, transient answers, public cache, Commons proposer secrets/queues/receipts, radio identity, Fleet management/TLS keys/outbox, fleet command-authority secrets, browser sessions, enrollment setup secrets and OS credentials. Shared history is not canonical personal SAGE and has no backup option in this first release. Public proposer, radio and management recovery use separate owning-plane re-enrollment/rotation. Never sweep all of `/var/lib/sage-lantern` into an archive. Forbidden validator/foreign-plane material in a candidate set blocks the operation; do not silently call a partial set “full.”

### Backup sequence (proposed, not an instruction to run the rehearsal)

1. P authenticates and confirms physical presence, personal identity, exclusions, destination and downtime. Explain that losing all decryption/recovery material means no Fleet-assisted decryption. Show no private key or secret preview.
2. Validate a supported pinned encryption implementation and backup format. Use an established authenticated encrypted archive format, not password-protected ZIP or custom cryptography. First implementation should pin and review `age` with an explicit owner-managed recipient/recovery-secret policy; exact packaging, offline recovery-key onboarding and ARM64 resource validation are phase gates. Do not equate an ordinary login PIN with an adequate archive secret or imply `age` is already installed.
3. Validate destination as explicitly selected removable/local owner-controlled storage: no symlink traversal, existing-file overwrite, automatic network upload, or device executable hooks. Check space, restrictive access, mounted identity, write/read-back capability and power margin. Refuse exporting through Fleet or a browser download route. A public encryption recipient is permitted inside the local recovery plane; its decryption key stays separately with the owner.
4. Acquire the shared maintenance lock; stop all personal writers and dependent voice operations gracefully; fence systemd/restart paths and verify no alternate writer can restart. The rehearsal's process-name check alone is insufficient. Cancel before backup if quiescence cannot be proven. Record stopped-state metadata without secrets.
5. Generate the supported complete snapshot and encrypted manifest in private staging. Prefer streaming into the authenticated encryption writer when the supported exporter permits it. If SAGE requires a plaintext file, use a bounded encrypted scratch filesystem with ephemeral keys and controlled swap/core-dump policy; refuse when it cannot be safely provisioned. Never use ordinary `/tmp`, release backups, a repo, or an unencrypted removable archive as plaintext staging.
6. Encrypt the entire manifest and all entries, including identity/account labels and adjuncts. Outer metadata contains only format version, random archive ID, encryption framing and ciphertext size/digest; do not put personal filenames, chain IDs, usernames or memory counts in an unencrypted sidecar. Authenticate contents before any restore use; a SHA-256 digest alone is not authenticity.
7. Write a new `.partial` ciphertext file, fsync, reopen/read back and verify completeness/authentication with owner recovery material locally, then finalize with an atomic destination rename and directory fsync where supported. Otherwise require an explicit incomplete/complete protocol and reject incomplete archives on restore. A mounted destination changing mid-operation fails closed. Label creation, cryptographic verification, and an actual restore rehearsal separately.
8. Resume only the originally authorized services after private staging teardown; verify ordinary readiness and persist a sanitized result. On cancellation, disk-full, unplug, encryption/read-back error or crash, remove incomplete ciphertext and destroy ephemeral staging keys; never mark the backup verified. Startup cleanup/reconciliation must handle leftovers before affected services start.

### Permissions, retention and evidence

- Recovery staging directories: 0700; plaintext/secret-bearing files and archives on POSIX storage: 0600; creation umask 0077, no inherited ACL access, no symlink/hardlink substitution. Verify ownership and permissions before use; fail closed on unsafe pre-existing state. Only the dedicated recovery authority and required owning SAGE process can access plaintext. Fleet/UI/camera service accounts cannot.
- Non-POSIX removable media cannot promise those modes: permit only finalized authenticated ciphertext under explicit owner acknowledgment; no plaintext scratch there. Plaintext lifetime is bounded to the locked operation, and failure cleanup must be independently tested. Unlinking flash files is not secure erasure; use cryptographic destruction for encrypted scratch and a reviewed wipe procedure for retired physical media.
- Proposed backup policy: manual, no unattended schedule initially; keep the latest two verified generations and one pre-upgrade generation, normally at most 90 days, under owner custody. Expiry prompts local review rather than silently deleting the only verified recovery copy. Never prune the last verified generation before verifying a replacement. Offline/unmounted copies cannot be automatically expired; surface them as unverified retention obligations, not deleted.
- Archive deletion and key rotation do not revoke copies already held by someone with the old decryption key. New-owner handover requires explicit old-copy handling; do not promise remote cryptographic revocation of previously shared archives.
- Keep local content-free operation audit for 90 days by default: random operation/archive IDs, version, actor reference, times, status and bounded error codes. Local public identity comparisons stay in the authorized recovery plane. Fleet may receive only approved coarse readiness, last verification time and bounded status; no archive, manifest, local paths, secret-derived values or personal content. A separate approved provisioning workflow may inventory public fingerprints. Audit expiration while powered off occurs on next startup before ordinary exposure.

## 8. Restore contract and no-clone gate

Restore is initially **same-unit, same-owner, same-personal-SAGE only**. It is not an import/export feature and not Unit B provisioning. Replacement hardware recovery is blocked until an independently reviewed retirement/fencing and ownership ceremony exists; this contract does not authorize it.

1. Require P/offline recovery authorization plus fresh physical presence in a trusted recovery environment. Confirm exact target unit, current ownership epoch, personal identity and destructive scope. The owner decrypts locally; never upload an archive or decryption material to Fleet for inspection.
2. Authenticate the full archive before trusting inner metadata or extracting into a live path. Bound encrypted input, decompressed size, entry count, nesting, CPU and KDF resources; reject corruption, truncation, unsupported format, unsafe paths, absolute paths, traversal, device nodes, symlinks/hardlinks and unexpected identity planes. Stage privately; never execute archive-provided programs/hooks.
3. Compare authenticated archive unit/owner/chain/genesis/public fingerprints with independently trusted local provisioning or offline owner records, not just archive assertions or editable hostnames. Unit A archive targeting Unit B is rejected before live writes, even if renamed or requested by a Fleet operator. Require cross-instance exclusivity evidence in addition to IDs. A copied filesystem identifier is not hardware attestation.
4. Check version compatibility and the owning SAGE restore/preflight procedure. Reject partial/key-only sets, mismatched voice copies, revoked/obsolete owner credentials, foreign keys and public-validator material. A stale full snapshot may contain old consensus/signing state: no blind downgrade or forced start. Require owning SAGE approval of signing-state safety and current authorization/revocation reconciliation, or remain fenced.
5. Obtain exclusive maintenance fencing for all writers and restart paths. Keep the previous tree inactive and private as a bounded rollback candidate on encrypted staging; do not run a second instance to “test” the restored identity. Loss/theft of an unfenced original is not sufficient proof of exclusivity; deny same-identity recovery in that case and use the owning system's separate fresh-identity/revocation process.
6. Apply the supported full restore to a private staged generation, with its personal adjuncts and a durable operation journal. Normalize ownership/permissions and validate identity consistency/public fingerprints, data integrity, version preflight and configuration before activation. Use the owning SAGE atomic-restore mechanism only after its crash semantics are verified; do not infer them from the rehearsal's log text.
7. Start only the restored personal instance under the fence, verify private readiness and explicitly approved retrieval checks, and keep Commons transmissions/management enrollments blocked. Public cache is disposable, signed proposals remain quarantined under their owning policy, and excluded identities are re-enrolled/rotated rather than copied. Do not automatically restore historical collection consent; shared history remains off and empty.
8. Commit only after read-back and safety checks. Destroy inactive secret-bearing rollback staging after successful commitment. On failure before the restored instance has signed or advanced state, restore the known-safe old generation only after proving the new one stopped. Once signing/state advancement might have occurred, do not blindly roll back old signing state: remain fenced and invoke owning SAGE recovery guidance. Persist `recovery_required` when safety cannot be established.

Backup verification without activation may inspect the authenticated archive offline in isolated staging. A restore rehearsal that starts the node must stop/fence the original first and must not create a concurrent clone. Personal backup success must not set Commons PROVISIONED or claim Fleet enrollment recovery.

## 9. Implementation phases and release gates

### Full-backup source verification — 2026-09-05

Read-only review of sibling `sage` checkout `d9dfd176`, with no changes to
`cmd/sage-gui/backup_full.go` reported by Git. This identifies source behavior,
not the installed Unit A binary or a completed recovery rehearsal.

- `runBackupFull` (`../sage/cmd/sage-gui/backup_full.go:98`) archives SAGE_HOME
  and a separate configured data directory when required; this covers more
  than the serving SQLite projection. The command explicitly warns its
  tar.gz includes keys, vault material, TLS keys and MCP tokens unencrypted.
  External Lantern voice identity still needs the separately verified adjunct.
- `requireStoppedNode` (`backup_full.go:229`) checks SAGE's instance lock but
  immediately releases it. It is a liveness check, **not** an exclusive fence
  held throughout export/restore. Lantern must prevent supervisor restarts
  and all other writers for the complete operation. Never use backup `--force`.
- `runRestore` (`backup_full.go:526`) checks backup kind and consensus fork,
  then derives destination layout from the archive manifest. The future
  wrapper must authenticate the archive and validate destinations against
  an independently trusted local plan before invoking restoration; manifest
  paths are not authority to overwrite arbitrary locations.
- Extraction is staged before replacement, but the target trees are renamed
  sequentially (`backup_full.go:627`). This is not one multi-tree atomic
  transaction. Previous trees remain as secret-bearing `.pre-restore-*`
  directories; a crash/restart journal, signing-state-safe reconciliation and
  bounded encrypted cleanup are still required.
- A recreated data-directory-link failure can be reported as a warning after
  trees have moved (`backup_full.go:664`). Exit success alone therefore cannot
  establish recovery readiness; verify layout, identity, preflight and actual
  authorized readiness before releasing the fence.

No archive was created, extracted, restored or read. Encryption format,
trusted owner/presence integration and version-pinned synthetic recovery
acceptance remain release gates; a CLI wrapper alone does not satisfy them.

| Phase | Concrete deliverable | Exit gate |
|---|---|---|
| 0 — Inventory and boundary review | Confirm source/deployed version mapping after parent benchmarks; record registry rows, present handlers and measured probes. Resolve supported SAGE full-backup contents, restore/signing-state semantics, offline recovery authority, unit-binding trust source and encryption packaging with owning maintainers. | Every unsupported setting visibly blocked; no speculative live-state claims. Backup/restore remains unavailable until unresolved safety dependencies are answered. No benchmark interference. |
| 1 — Local state contract | Typed registry, desired/effective/capability store, revision CAS, safe read-only projection and privacy-preserving journal. Adapt existing history API without losing consent generations or fixed caps. | Schema, race, stale-observation and history tests pass; fixture preferences cannot mutate real desired/effective state. |
| 2 — Bounded application | Shared maintenance lock, transactional config generations, measured read-back, reboot reconciliation and tested rollback for one low-risk profile, then paired vision/model handler. Add server-enforced privacy-off before any new collection toggle. | Fault-injection tests pass at every persistent transition; failed rollback is visible and safe. Parent-approved voice/performance regression acceptance passes in a scheduled window. |
| 3 — Local authority and custody | Isolated recovery service, owner enrollment/delegation, trusted physical confirmation, offline recovery path, secret-safe prompts and retention/custodianship policy. | Unauthorized kiosk/Fleet/local-process access denied; no secret reaches UI/Fleet/support channels. Shared-owner and lost-credential cases are tested. |
| 4 — Encrypted recovery | Version-pinned exporter/encryption wrapper, authenticated manifest, encrypted staging, same-unit restore, exclusivity fence, retention bookkeeping and resumable failure journal. | Synthetic recovery and adversarial archive tests pass; separate explicitly authorized stopped-node rehearsal proves recovery without cloning. No Unit B identity reuse. |
| 5 — UI placement and rollout | Choose touchscreen/external/split using physical usability evidence; wire only accepted features to the same API, with progress, desired/effective mismatch, restart impact, local confirmation and unsupported explanations. | Touch and external clients cannot bypass backend authority; inaccessible hardware input fails safely. Fleet feature execution remains a separately gated command slice, not a prerequisite for local use. |

## 10. Acceptance tests

The following are required tests, not claimed results. Implement automated cases using synthetic identities, fake services, bounded temporary directories and fault injection. Live audio, reboot, network, cryptographic recovery or Unit A changes require a separate operator-approved maintenance window; the parent owns benchmark timing. Never use production keys as fixtures.

| ID | Test and required outcome |
|---|---|
| S01 | Toggle a Fleet fixture switch, refresh, and inspect local state: no device mutation, receipt or effective-state success is created. Unsupported features have a reason and cannot submit executable requests. |
| S02 | Send unknown setting, wrong boolean/type, out-of-range threads, arbitrary path/URL/service, forged actor and stale revision: reject without side effects. Two simultaneous valid revisions produce one winner and one conflict. |
| S03 | Request vision with missing/wrong projector, stale evidence, unavailable model, insufficient memory or active benchmark lock: desired/effective mismatch is explicit and no services restart. Loss of a prerequisite after success marks capability degraded/unknown. |
| S04 | Fail after every journal/config/service/verification step; kill the coordinator and reboot its fixture environment. Reconcile to verified old/new coherent state or fenced recovery-required, never a silently mixed generation. Replayed operation returns status without repeated mutation. |
| S05 | Force rollback failure after partial handler effects: reported resulting state comes from read-back, not the supplied old snapshot. Loss of the HTTP response does not turn accepted/delivered into succeeded. |
| S06 | Disable/clear history during an in-flight answer, restart, and retry old consent: no resurrection. Enforce 100 items/30 days/4 MiB, 0700/0600, defaults off, disk-full and unsafe-file failures. Current-answer Clear preserves deliberately saved history. |
| S07 | Turn capture/camera off while active: resources close and all server entry points deny new collection. Rollback cannot re-enable it. Missing GPIO does not disable otherwise ready touch Ask. TTS stays unavailable until playback-specific acceptance. |
| S08 | Test local-only policy with attempted remote endpoints and packet observation in a sandbox: no private prompt/audio/photo leaves permitted local services. Service-up and configured-loopback alone do not satisfy the gate. |
| A01 | Ordinary kiosk visitor, LAN client, forged Origin, SSH-only operator, Fleet admin, camera and voice service accounts attempt backup/restore: all fail without both proper personal authority and fresh trusted presence. No archive bytes reach a remote client. |
| A02 | Replay/expire a confirmation; change target, plan digest, owner epoch or destination; roll back wall clock; revoke delegation mid-operation before commit: authorization is rejected/revalidated safely and does not silently broaden. |
| A03 | Test shared use and owner departure: history notice/default are correct, no implied personal account isolation, Fleet reassignment grants no memory access, previous-owner archive cannot be restored under a new owner's account. Lost local credentials require supported offline recovery authority, not Fleet reset. |
| B01 | Create and authenticate a synthetic complete backup plus matching external voice adjunct. Verify full personal data/policy/signing state completeness through supported exporter tests. Raw audio/photos/history and excluded identity planes are absent. |
| B02 | Scan synthetic process args/environment, logs, crash artifacts, browser storage, support output, Fleet observations and removable media for planted secret canaries: no plaintext or decryption material appears outside authorized recovery staging. Verify users/groups/ACLs, umask and symlink/hardlink defenses. |
| B03 | Wrong recovery key, tampered/truncated ciphertext/manifest, malicious paths, decompression bomb, oversized KDF/input and unsupported version: bounded rejection before live writes; previous node remains intact and unavailable secrets are never printed. |
| B04 | Disk full, USB unplug/replacement, cancellation, process kill and power-loss simulation during export/encryption/rename: no finalized-success status for incomplete archive; encrypted scratch keys are destroyed and pending work reconciles on boot. |
| B05 | Retention with two verified copies, one failed replacement, one pre-upgrade copy, offline media and clock uncertainty: do not delete last verified copy; expose unknown external retention; deleting live history/account does not claim deletion of backup copies. |
| R01 | Restore to the same synthetic unit/owner with original fenced: public identity and complete private state are consistent; at most one instance can start; fresh permissions and recovery checks pass before commitment. Verify creation, archive verification and actual restore rehearsal statuses remain distinct. |
| R02 | Present Unit A archive to Unit B, including renamed outer file and tampered unit metadata: reject before target writes. Also reject absent independent binding/exclusivity proof; changing a hostname is not an override. Fresh Unit B provisioning records distinct public fingerprints with no inherited queues/nonces/receipts. |
| R03 | Old instance still running/restartable, key-only/partial set, mismatched voice copy, foreign/public-validator key or obsolete signing/owner state: refuse activation and retain a clear bounded reason. |
| R04 | Crash at each restore stage, including after possible signing advancement: never run old and restored trees together; never roll signing state backward blindly. Restore failure either proves safe prior-state recovery or remains fenced with recovery-required. |
| R05 | After successful synthetic restore, verify shared history off/empty, no stale public cache treated as canonical personal memory, no automatic Commons transmission, and no Fleet/TLS/radio key restoration from the personal archive. |
| U01 | In the chosen UI, distinguish off/unsupported/blocked/stale/applying/failed, show exact restart/privacy impact and local confirmation. Physical touch targets, disconnects, expired prompts and unavailable input paths are evaluated separately from HTTP/build success. |

Completion requires evidence for the applicable phase gates and acceptance IDs, with unresolved features remaining blocked. This document alone enables no toggle and authorizes no backup or restore.
