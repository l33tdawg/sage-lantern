# Unit B provisioning gates

**Historical endpoint plan, superseded 2026-09-27:** A/B now serve single-LoRa Lamplighter/admin/knowledge/repeater roles. Their native personal SAGE stores are deleted and old endpoint services inhibited. Endpoint acceptance now belongs on C–F. Use [device roles](DEVICE_ROLES.md) and [the current task map](SAGE_TASKS_20260927.md); do not resume the A-before-B gate below.

Updated read-only critical-path audit, 2026-09-07. Scope: finish Unit A locally,
freeze a deliberately matched release, provision B independently, then measure
two-radio delivery. No live device operation, secret inspection, key read,
cloning, unlock-state transfer, or deployment was performed for this audit.
Repository scripts are not evidence of the effective installed configuration.

## Decision

**A fresh phased B installer now exists; a fully accepted one-script appliance
bring-up does not.** The supported source path is `provision-fresh-appliance.py`
with `base` → `identity-config` → `app`, followed by explicit startup, owner setup,
hardware commissioning and supervised activation. The older legacy fleet
installer is not this path. Do not substitute B's address into A recovery commands.

**B is identified by the parent on SD at `.96`: ROCK 5B Plus, SD root
`/dev/mmcblk1p3`, SD serial `0x128338a1`, NVMe serial
`AA20260312512G069617`. NVMe boot and peripheral bindings are not yet accepted.
Unit A is NOT fully accepted.** Parent owns the final
A acceptance/release decision. Successful resident-key native replacement on A
does not prove voice reactivation, repeated unlock, all security/hardware flows,
or B installation. No target action is authorized by this document alone.

## Current release blocker — resolve before B execution

### New locally staged candidate: artifact closure resolved, target acceptance pending

A new independent candidate now exists at:

`/private/tmp/lantern-independent-b-20260907-closure2/canonical`

Its canonical manifest SHA256 is
`834023b1798e0ad278834e3440a604b38d8087e7e7e5685e622f2f3b110e6ea3`.
Canonical staging verified native binary provenance, exact R7 wheel/source and
SDK closure, dependencies and UI artifacts. The frozen payload's **own** phase
runner passed its full read-only `base` plan for `unit-b` (`executed=false`).
This is a real candidate directory, not instructions to patch the old R9 bundle.

Selected native is `11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c`;
runtime is `6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440`.
UI is the grouped-header package
`de0d9bc14547ea9625480c5ca73b63bc3034650a346d386be59212ef8e9fe21e`.
Current required installer/helper sources were frozen; runtime source was
reconstructed and hash-checked from R7, not substituted from the working tree.
Only the new candidate lock was changed to match native version/hash and runtime.

The separate six-file startup source bundle is at
`/private/tmp/lantern-independent-b-20260907-closure2/startup-sidecar`, manifest
`fe3e82a6d610b45a3946778591f1923b0403174a392f65c4ac357eec058908b6`.
Its adapter/helper/collector hash constants match. It contains **no** policy,
prior lifetime, identity, commissioning data or enablement. The first B lifetime
handoff remains necessary; do not use an A startup config with this source bundle.

After transfer, set `CANONICAL_OUTPUT` to B's actual staged directory and repeat
the same read-only plan (do not retain the host `/private/tmp` path by assumption):

```sh
python3 "$CANONICAL_OUTPUT/payload/scripts/lantern/provision-fresh-appliance.py" \
  --output "$CANONICAL_OUTPUT" \
  --manifest-sha256 834023b1798e0ad278834e3440a604b38d8087e7e7e5685e622f2f3b110e6ea3 \
  --unit-id unit-b --phase base --allow-native-build
```

Only the separately authorized target operator adds `--execute` after NVMe root,
OS/Node prerequisites and fresh-target checks. Missing packages need the separate
`--install-os-packages` decision. Follow the independent identity-config and app
phases below. Source assembly and host planning do not prove ARM64 installation,
owner setup, camera/audio commissioning or repeated reboot readiness.

Assembler: `scripts/lantern/assemble-independent-release.py`. The failed first
attempt (`closure1`) is retained with its version-mismatch evidence; do not use
it. All older release directories and live A state were left unchanged.

Packaging correction: the first closure2 tar inadvertently included 21 generated
CPython 3.12 bytecode files from a sidecar test using the frozen runtime on
`PYTHONPATH`. They were not in the manifest. The target verifier correctly
refused the extra directory. Parent quarantined that exact directory outside B's
payload; the host copy is likewise retained separately. No manifest entry or
state/secret allowlist was relaxed. The original archive is not a pristine
transfer artifact and must not be reused as-is.

Use `scripts/lantern/test-startup-sidecar-source.sh PYTHON FROZEN_RUNTIME_SRC`
for future sidecar tests: it sets `PYTHONDONTWRITEBYTECODE=1`, passes `-B`, and
disables pytest's cache provider. Package only with the strict boundary below,
which verifies exact canonical and sidecar trees before and after archiving and
rejects extra files/directories instead of silently filtering them:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/lantern/package-independent-release.py \
  --work /private/tmp/lantern-independent-b-20260907-closure2 \
  --output "$NEW_ARCHIVE_PATH" \
  --manifest-sha256 834023b1798e0ad278834e3440a604b38d8087e7e7e5685e622f2f3b110e6ea3
```

The canonical manifest hash remains unchanged after exact bytecode quarantine.
Only a successfully verified new archive's reported hash is a valid transfer pin.

The 2026-09-07 artifact audit found three different release states:

| Artifact set | Native SAGE SHA256 | Runtime wheel SHA256 |
| --- | --- | --- |
| Checked-in `software/stack.lock.json` | `945c98c535f512371e526ab4083ddbd55411686f20bae90950a69f9d8fe13799` | `21809cefad4566b45aed592bea66ee01c211f9add372ecba70981324ad04bccd` |
| Frozen host R9 canonical payload | `ebf522a8cbc1fc26df8aeabc72f9a82dfc8023dd274524e7f2689f4771c55137` | `8c89e56405bb61185ac5c871d6041a7001bfc6943311bd8fafc9f67472179656` |
| Reported A resident-key deployment with installed R7 runtime | `11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c` | `6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440` |

The resident-key native version is
`lantern-local.20260907-fresh-v27-resident-vault`, max-app-v27; its manifest SHA256
is `dba0a00a8e7af7a5e7e5f73c715568add219b2675feab885829863512b26ca0c`.
Neither the checked-in lock nor frozen R9 currently selects that binary. R9 is
also not identical to A's installed R7 runtime. Version strings alone cannot
establish parity, and copying a new executable into an old release invalidates
the release's integrity closure.

Release owner must deliberately select the resident-key patch and compatible
runtime/SDK/helpers/UI/configuration sources, rebuild the canonical manifest and
lock bindings, and verify exact installed closure and acceptance. Do not assume
R7 or R9 is accepted merely because it exists. Transfer only that reviewed sterile
release—not A's live venv, configuration, keys, history or recovery directories.
This audit does not update or approve a release.

### Follow-up artifact audit and immediate local gate

The existing `/private/tmp/lantern-fresh-canonical-20260907-r9/manifest.json`
hash is `781c8c783f3f118715434b90b0dca848e08733eb6f91eb1de7c474a8ccd24847`.
Its native/runtime hashes still match the R9 row above, not the resident/R7 pair.
Its `unit-a-status-server.py` differs from current source. Its auth helper and
personal-SAGE unit match current source at this audit, but that does not establish
deployed A parity. Startup coordinator/adapter/installer are absent from R9.

Before transferring a new release, run this read-only selection/integrity gate
from the reviewed local source tree, with the release owner's selected hashes:

```sh
python3 scripts/lantern/check-fresh-release-selection.py \
  --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --native-sha256 "$REVIEWED_NATIVE_SHA256" \
  --runtime-sha256 "$REVIEWED_RUNTIME_SHA256"
```

The gate rejects an old native/runtime pair even if its own manifest is valid.
It neither changes the lock nor approves runtime/SDK/source/UI compatibility.
To reproduce the currently reported resident/R7 basis, the selection is native
`11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c`
and runtime
`6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440`;
release owner must explicitly approve that choice and its source closure.
Do not substitute the current working-tree runtime source for R7 source bytes.

The local staging interface, after preparing genuinely matched source/artifact
roots, is:

```sh
python3 scripts/stage-appliance-release.py \
  --source-root "$REVIEWED_SOURCE_ROOT" --artifact-root "$REVIEWED_ARTIFACT_ROOT" \
  --output "$NEW_CANONICAL_OUTPUT" --release-id "$NEW_RELEASE_ID"
```

Use a new output directory and regenerate its canonical manifest/lock bindings;
do not edit a frozen payload in place. Source-only tests do not turn the currently
mismatched R9 directory into this new release.

### B-specific recurring startup handoff

The startup service runs a standalone coordinator under `/opt/sage-lantern/libexec`,
not a module added to R7. Its sole child is the pinned startup audio adapter,
which owns collection and full recovery. Keep this separately reviewed sidecar
out of the exact R7 wheel/source closure.

**A's prior lifetime cannot seed B.** Fresh B must first complete its own identity,
owner unlock, hardware commissioning and supervised first broker bootstrap.
Retain and independently verify B's own boot/broker/inventory lifetime before
creating B's startup policy and initial-lifetime pin. The recurring adapter
requires prior evidence and supported settled workflow state; it is not a
fresh-empty-device initializer. Until that handoff exists, leave recurring
startup disabled rather than copying A's receipt, actor, commissioning or policy.

The status/runtime/source fixes and sidecar must be selected together by the
release owner. This audit adds a selection guard and command path, not an accepted
new canonical artifact or a claim that first B voice startup is automatic.

#### Concrete first-lifetime handoff source

`scripts/lantern/handoff-first-audio-lifetime.py` closes the policy-generation
handoff after B's independently commissioned first audio bootstrap. It is a new
standalone supplement, **not inside the already frozen closure2 archive**. Stage
and review it separately; do not mutate that archive or its manifest.

Prerequisites: B metadata and mesh configuration both identify `unit-b`; B's
commissioning binding identifies `unit-b`; the owner is actively unlocked; the
first real `ColdBrokerBootstrap` receipt exists in `/run`; broker/voice and the
model are live. The helper verifies real broker proof, current audio admission,
model PID/start/invocation/executable/argv/listener and idle slots, and the exact
R7 source closure. Missing `/slots` support is a model-readiness blocker, not
permission to infer idle. It does not perform that first bootstrap or capture.

Prepare a root-pinned tools document with exact keys:

```text
schema = sage.lantern.first-audio-tools.v1
unit_id = unit-b
coordinator = {path, sha256}
adapter = {path, sha256}
helper = {path, sha256}
collector = {path, sha256}
runtime_wheel = {path, sha256}
```

Use the matched standalone sidecar paths and exact R7 wheel, with coordinator at
`/opt/sage-lantern/libexec/startup-coordinator.py`. Adapter-embedded helper and
collector hashes are checked; no A actor, identity, policy or receipt is input.

Inspect without writing, starting services or creating identities:

```sh
sudo /opt/sage-lantern/venv/bin/python -I -B \
  /opt/sage-lantern/libexec/handoff-first-audio-lifetime.py \
  --tools "$B_TOOLS_JSON" --tools-sha256 "$B_TOOLS_SHA256"
```

Review its public metadata plan and retain the exact output as a root-owned file.
With explicit handoff-write authorization, invoke the same command adding:

```text
--expected B_REVIEWED_PLAN_JSON --expected-sha256 B_REVIEWED_PLAN_SHA256 --apply
```

Apply reobserves the same access epoch, boot, model identity, receipt and static
inputs before an exclusive claim, then checks again before recording completion.
It creates B's actual `new-lifetime.json` under the canonical per-boot lifetime
directory, plus `baseline.json`, `policy.json`, `startup.json` and a completion
record under `/var/lib/sage-lantern-first-audio-handoff/BOOT_UUID/`. It never
rewrites commissioning or claims historical drain. Interrupted/partial claims
remain for review; there is no automatic overwrite, cleanup or retry.

The generated `startup.json` and its returned hash are the missing config inputs
for the existing reviewed `install-startup-coordinator.py` interface. First run
that installer without `--apply`, then explicitly install/enable only after
review. The handoff itself neither enables startup nor accepts a reboot. Supported
settled workflow state is still independently checked by the recurring adapter;
this helper does not seed or rewrite personal workflow records to make it pass.

### Model launch parity: explicit slot count is missing from the base unit

Source audit found a concrete configuration difference, not a proven cause of
latency or reboot failures:

| Path or observation | Parallel setting | Evidence boundary |
| --- | --- | --- |
| `software/systemd/sage-lantern-llm.service` base `ExecStart` | No `--parallel` argument | Verified in current source and frozen R9 base unit |
| `scripts/lantern/install-vision-local.sh` generated model override | `--parallel 1` | Source template; not proof the override is installed/loaded |
| `scripts/lantern/plan-fresh-vision.py` model candidate | `--parallel 1` | Source candidate; does not change the active unit |
| September 5 baseline versus current A fresh launch | Explicit single slot versus reported automatic four slots | Parent-reported runtime comparison; not independently probed by this audit |

Note 2026-09-29 (D-016): the planner now targets the NPU vision lane
(`npu-text-8081-npu-vision-8082`) and no longer emits a llama.cpp CPU model
candidate, so the `--parallel 1` rows above describe the retired CPU fallback
source rather than the current vision path. A/B analysis using those rows remains
historical.

The base unit retains `--ctx-size 4096 --threads 6`, but its omitted parallel
argument delegates slot selection to the installed server. Do not infer that all
llama.cpp versions default to four, or infer effective argv from a template.
The loaded unit, all ordered drop-ins, process argv and server slot observations
must agree before declaring A/B model parity. Merely shipping the vision script
does not apply its model override.

**Future benchmark candidate:** explicitly add `--parallel 1` to the reviewed
effective model `ExecStart`, preserving all other effective arguments, loaded
guards and source/binary/model hashes. This candidate is not applied, validated
or claimed to improve performance. Compare against the current automatic-slot
configuration with identical model/mmproj, context, threads, image token budget,
GPU settings, workload, thermal conditions and warm/cold state. Record slot
count, time to first token, completion latency, throughput, memory and failures;
include text and photo requests separately.

A model-unit/argv change invalidates the relevant pinned model baseline and
startup-policy expectations. Treat it as a separately reviewed configuration
transition and regenerate only the affected evidence through its owner; never
rewrite active commissioning receipts or silently edit a release lock to make a
new argv pass. Record the chosen setting in B's matched release before claiming
performance parity. This documentation change alters no active configuration.

## Concrete safe phased path

All commands below are templates with independently verified B inputs. B's
management address is parent-reported; login, Wi-Fi interface and USB bindings
remain independently selected inputs. See `NVME_MIGRATION_PREFLIGHT.md` for the
parent's verified compressed OS-image hash and separate NVMe boot acceptance.

1. **Boot and identify B independently.** Use the reviewed vendor Debian Bookworm
   ARM64 image and B-specific storage checks, not the A NVMe/SPI migration script.
   Establish management Ethernet/SSH and verify host identity. Confirm Python
   3.11, `python3-yaml`, NetworkManager, `/usr/bin/node` >=22.13, and the reviewed
   GDM3/X11/Chromium/operator-account prerequisites. Bootstrap neither flashes an
   OS nor supplies all desktop/Node/network prerequisites. On the selected B only,
   `bash scripts/bootstrap-lantern-os.sh --check` is the nonmutating OS check.
2. **Plan the matched release's base phase.** `$CANONICAL_OUTPUT` is the reviewed
   canonical output containing `payload`, `selection.json` and `manifest.json`.
   Use that release's scripts, not unrelated working-tree scripts:

   ```sh
   python3 "$CANONICAL_OUTPUT/payload/scripts/lantern/provision-fresh-appliance.py" \
     --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
     --unit-id unit-b --phase base --allow-native-build
   ```

   Only after release/host approval, add `--execute` on the fresh native target.
   The runner checks prerequisites, installs the sterile base, then runs metadata
   `prepare --unit-id unit-b`. `--install-os-packages` separately authorizes apt
   changes; without it, missing packages stop the phase. Node is checked before
   installation. Failures retain partial state; do not blindly rerun or clean it.
3. **Create B's independent identities and protected configuration.** Prepare
   separately reviewed initial SAGE YAML and explicit storage quota/template
   plan as described in `FRESH_IDENTITY_SETUP.md` and
   `IDENTITY_CONFIG_COMMISSIONING.md`. Plan:

   ```sh
   python3 "$CANONICAL_OUTPUT/payload/scripts/lantern/provision-fresh-appliance.py" \
     --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
     --unit-id unit-b --phase identity-config \
     --identity-config-sha256 "$REVIEWED_INITIAL_SAGE_YAML_SHA256" \
     --commission-plan "$REVIEWED_PLAN_JSON" \
     --commission-plan-sha256 "$REVIEWED_PLAN_SHA256" \
     --owner-approved-companion-bootstrap
   ```

   Explicit authorized `--execute` invokes native `init-lantern-private` as
   `sage-lantern`, creating independent Root/Companion/public keys and private
   genesis. It binds the actual generated chain to protected config. No keys are
   passed on argv, copied from A or derived from the chosen device name. The
   public key is not public enrollment. The identity-only phase is insufficient
   for the app's commissioning-completion gate.
4. **Install the locked app.** Use the same runner with `--phase app`, the same
   release arguments, and the complete B-specific `--account`, radio inventory,
   radio serial/VID/PID/stable-path/fingerprint, audio serial/VID/PID/stable-path
   and ALSA binding arguments after `--`; exact invocation is in
   `FRESH_PROVISION_CHAIN.md`. Add `--allow-owner-vault-setup` only with explicit
   permission. Default is planning; `--execute` checks then installs. Optional
   GDM autologin requires `--enable-kiosk-autologin --gdm-base-sha256 "$GDM_SHA"`
   and the reviewed stopped-GDM baseline. Default installation starts nothing;
   kiosk opt-in enables app/auth/status for next boot, not voice/mesh readiness.
5. **External-browser Wi-Fi setup remains an operator handoff.** After guarded
   app startup, retain management connectivity and forward the exact loopback
   ports from the operator computer:

   ```sh
   ssh -N -L 3100:127.0.0.1:3100 -L 3195:127.0.0.1:3195 "$B_USER@$B_HOST"
   ```

   In a controlling terminal on B, explicitly start the reviewed broker:

   ```sh
   sudo /opt/sage-lantern/venv/bin/python -I -B \
     "$CANONICAL_OUTPUT/payload/scripts/lantern/lantern-network-setup.py" \
     --serve --unit-id unit-b --interface "$VERIFIED_B_WIFI_INTERFACE"
   ```

   Browse `http://127.0.0.1:3100/admin/setup`; manually enter its fresh 15-minute
   capability, choose a supported visible network, verify connection, then
   finish/revoke. This is not an automatic LAN portal/hotspot, and the capability
   is not the device PIN. Do not expose private daemons on all interfaces.
   Wi-Fi setup currently follows installed software; initial management access
   cannot depend on that page. See `FIRST_INSTALL_SETUP.md` for limitations.
6. **Owner setup, then separate activation.** Start independently initialized
   private SAGE/auth through their installed guards. The touchscreen walkthrough
   obtains name, passcode and recovery confirmation. Operator-authorized key
   creation precedes this ceremony; naming neither creates nor enrolls identity.
   Confirm encryption, committed local enrollment and storage before approved
   audio commissioning and root-supervised mesh/voice activation. Camera,
   display/suspend and boot-theme installation remain separate handoffs. Do not
   write readiness markers or run legacy `verify` early to bypass the sequence.

The phase runner is not yet an accepted end-to-end startup/commissioning wrapper.
The detailed source flow is `FRESH_PROVISION_CHAIN.md`; native owner setup and
each activation gate require separate evidence.

## Planner-to-activation integration — 2026-09-06

The planner now supports `--format voice-activation` in addition to its existing
plan-only JSON. Supply the same explicit prepared-unit, radio and audio arguments,
plus `--identity-inventory /var/lib/sage-lantern/inventory/unit-inventory.env`.
It validates that the inventory unit and radio fingerprint match the newly built
plan, then emits the exact `sage-lantern-voice-activation-v1` configuration:
prepared metadata, inventory, stable audio path and per-unit identity/recovery
evidence paths. Current voice-installer support is limited to A/B with radxa.

Save successful output as `voice-activation.json`, then run the real, nonmutating
consumer preflight:

```sh
bash scripts/lantern/activate-unit-a-local-voice.sh \
  --dry-run --unit-id unit-b --unit-config /absolute/path/voice-activation.json
```

For off-device fixture testing only, `--activation-staging-root /absolute/stage`
maps target evidence paths beneath that tree. Prepared metadata and inventory
remain the explicitly supplied input paths; audio remains the real target path,
not a fabricated staged hardware device. The installer rejects staged metadata
and inventory for live activation. Default planner output is not an activation
config and must not be passed directly to the installer.

This export neither creates identity/recovery evidence nor certifies it. The
activation preflight still checks the complete inventory, identity correlations,
recovery manifest and attestations; target hardware/services need separate live
acceptance. Tests run the real producer and consumer together using synthetic
public evidence and assert no service calls or file mutations. This closes one
schema mismatch, not the complete B appliance installation gap.

## What is actually runnable

### Per-unit audio rule export — 2026-09-06

The unit planner accepts `--format audio-udev` with explicit
`--audio-vendor-id` and `--audio-product-id`, in addition to the existing
prepared-unit, radio and audio arguments. Use the observed **normal-firmware**
USB IDs, not ROM download-mode IDs. It emits one normal sound-control rule
matching vendor, product and exact audio USB serial, with group `sage-lantern`,
mode `0660` and alias `sage-lantern-audio-control`. No wildcard serials, shell
actions or automatic recovery-flash permission are generated.

The known `SAGE-LANTERN-A-AUDIO` binding is rejected for other units. That guard
does not establish uniqueness of arbitrary microphones: supplying B's actual
serial remains an operator requirement. Optional audio IDs are also retained
in normal plan JSON when supplied; both must be present and valid.

This export writes only stdout. It does not install rules, reload udev, select
an ALSA PCM device or certify microphone capture. The fixed legacy audio rules
remain unchanged for A; the fresh secure installer must consume a reviewed
per-unit artifact and verify its effective hardware match. Recovery-mode access
needs separate explicit hardware binding and authorization.

| Source | Actual behavior and B limitation |
| --- | --- |
| `scripts/lantern/provision-fresh-appliance.py` | Current canonical runner: `base`, `identity-config`, then `app`. Default plans only; explicit `--execute` runs target phases. Requires reviewed release/configuration digests and independent B inputs. No complete startup, owner setup, capture or RF acceptance claim. |
| `scripts/lantern/install-fleet-appliance-local.py` | Current fresh A/B locked-app installer, distinct from the legacy shell installer below. Requires independent identity/config commissioning and matched installed artifacts. Optional owner-setup/GDM gates; no default service start or mesh grant. |
| `scripts/bootstrap-lantern-os.sh` | Generic already-booted Bookworm ARM64 prerequisite check/install. `--check` and `--dry-run` are nonmutating; neither is an OS image installer. Apt versions depend on configured repositories. |
| `scripts/install-lantern-unit.sh` | Fresh-target-only root/aarch64 base installation with fixed roots and `sage-lantern` account. Side-effect-free help; unknown arguments including `--dry-run` are rejected. Before mutation, refuses existing Lantern roots, private markers and installed/loaded units, drop-ins or masks. Rechecks after bundle validation; atomic install-root creation admits one installer and leaves partial installs blocked. Requires Python 3.11, installs offline wheels, builds native AI binaries and installs three services. Creates no identities and enables no services. Not an upgrade, recovery or secure-app installer. |
| `scripts/provision-lantern-unit.sh` | Real parameterized `prepare`/`verify`, `--unit-id`, staging root, dry-run. Creates no keys. Verify checks fingerprint syntax, within-inventory distinctness, immutable inventory, and operator attestations. It does not interrogate devices, prove recovery, compare B against A, verify encryption, or test RF. |
| `scripts/lantern/install-fleet-admin-local.sh` | Now supports explicit A/B public-binding dry runs. Live B and guarded A are rejected before helper loading or mutation. Legacy unguarded A still uses radxa, fixed paths/audio rules and target npm builds; selected radio evidence must match prepared unit metadata. Not a fresh secure installer or offline prebuilt release consumer. |
| `scripts/lantern/configure-unit-a-hostname.py` | Derives hostname from prepared unit metadata; optional unit argument must match. Alternate metadata is dry-run only. Historical filename no longer implies stamping A onto B. |
| `scripts/lantern/activate-unit-a-local-voice.sh` | Supports A/B unit configuration and nonmutating evidence preflight. B requires explicit configuration; live activation rejects staged metadata/inventory. Temporary readiness marker and rollback remain. Passing this legacy preflight does not certify hardware or replace current protected-storage/root-supervised activation. |
| `scripts/lantern/install-unit-a-private-access.sh` | An existing-device, one-time cutover, not fresh provisioning. Requires existing app/runtime/config, active local SAGE/LLM, idle voice state and absent security markers. Uses fixed radxa app path; freezes/hashes source and prebuilt app, copies all runtime `.py` files into site-packages, checks the local operator's vault status, installs security gates and may clear test history only with explicit flag. No dry-run; it performs sensitive local operations and must not be used for this audit. |
| `scripts/lantern/install-vision-local.sh` | Requires already installed model/projector/binary, hash-bound local vision validation and local voice readiness. Base installer does not install the projector. Not a complete vision bootstrap. |
| `scripts/lantern/unit-a-nvme-migrate.sh` | Destructive A-specific disk/SPI procedure: fixed source root, target NVMe device/model/size/**serial**, image and SPI backup paths. Not a B installer. Do not generalize by deleting the serial guard. |
| `scripts/lantern/unit-a-personal-recovery-test.sh` | Stops the personal node and creates a private backup using A-named archive/evidence paths. Not read-only, not a transfer artifact, and not safe to run for a dry-run audit. |

The audited install entry points are local-target scripts, not SSH installers
with a selectable host. Their hardcoding is primarily hostname, account,
filesystem roots, evidence and peripheral identity. Any parent transfer wrapper
must separately verify the selected B host; no remote wrapper is certified here.

## Minimal critical path and recommended ownership

1. **Parent/release owner — finish and freeze A.** Record physical PTT capture,
   transcript/answer, photo flow if in release, lock/startup/shutdown behavior,
   reboot, peripheral reconnect, and the native conversation result. Keep new
   shared-answer cache unwired/disabled; it is not a provisioning dependency.
   Produce a reviewed sterile artifact allowlist, not a live-filesystem clone.

2. **Installer owner — use the current phased path.** Validate the canonical
   runner and fresh Python app installer with B's prepared metadata, account,
   public radio inventory and stable audio binding. Do not revive the legacy
   shell fleet installer or treat its dry-run as fresh-install acceptance.

3. **Release/security installer owner — accept the implemented fresh gates.**
   Exercise independent identity/config commissioning, locked app installation
   and first exposure on the selected matched release. No automatic history wipe,
   inherited unlock session, copied recovery state or temporary unguarded UI.
   Preserve the operator-initialization/owner-vault-setup boundary and prove
   encryption and local enrollment rather than assuming a PIN screen proves them.

4. **OS/hardware operator — boot B independently.** Use the approved vendor image
   and B-specific storage checks; do not use the A NVMe script unchanged. Install
   sterile base, prepare `unit-b`, generate independent identities through their
   owning systems, and perform local recovery/enrollment. Record public evidence
   only. Reuse established local verification, not privileged voice credentials.

5. **Radio/acceptance owner — test B locally, then A ↔ B.** Verify hardware and
   firmware gates below, then bounded synthetic text and acknowledgements in both
   directions. No photo transport, new routing feature or public-cache work is
   required for this release. Keep transport success distinct from SAGE commit.

6. **Parent/provisioning owner — attest last.** Compare independent public
   fingerprints, verify recovery and release evidence, then run the real
   provisioner verification with genuine B public values. Never manufacture
   fingerprints or tick attestations just to open a gate.

These are ownership recommendations, not code changes performed by this audit.

## Reproducibility and transfer boundary

`scripts/verify-lantern-bundle.py` verifies size and SHA-256 for files listed in
`software/stack.lock.json`. It does not authenticate the lock itself, reject all
unlisted files, or prove that the runtime wheel equals the latest source tree.
The current base installer selects runtime 0.2.0 and SDK
11.19.10+lantern.20260906, NumPy 1.26.4, Piper 1.4.2, llama.cpp b9637 and
whisper.cpp v1.8.6. SAGE's actual patched native image must be checked by digest
and manifest, not the older 11.19.10 component label. Native compiler/OS package
versions are not thereby pinned. R7/R9 are release identifiers, not distinct
runtime package version strings; version-only parity is insufficient.

Release evidence must include:

- Trusted release identifier and authenticated manifest digest; exact runtime
  wheel/source-overlay hashes, helper scripts, systemd units/drop-ins and UI
  prebuilt artifact/dependency lock hashes. Build once, verify copied bytes on B.
- SAGE executable/SDK/wheel hashes, models and vision projector if used, native
  binaries plus source/compiler/build flags; OS image checksum, kernel and
  package versions. Record any deliberately different B hardware settings.
- An explicit sterile transfer allowlist. Do not copy the whole working repo,
  `/home/radxa`, `/etc`, `/var/lib/sage-lantern`, live venv, browser profile or A
  backup directories. A private-access backup includes configuration and is not
  a sterile distribution, even though it also contains code manifests.
- Exclude personal SAGE homes, Root/Admin/validator/agent/proposer/radio keys,
  receive tokens, TLS private material, vault/PIN/recovery/unlock state, photos,
  conversations, outboxes, tombstones, receipts, message IDs/nonces and replay or
  cache trust state. B establishes its own trust policy and empty working state.
  Do not clone SSH host keys or machine identity from a provisioned OS image.

## Security and readiness gates are different

### First-run ordering is wired; native acceptance remains

The supported base/provisioner order is **base install, then prepare**. Live
`provision-lantern-unit.sh prepare` requires the service account supplied by the
base installer. A prepared or partially installed target must not be treated as
empty and silently reinstalled. Retain evidence and review recovery separately.

The base installer's fresh-target check is now enforced, not just guidance. It
checks dangling links as existing state, inspects systemd configuration and
loaded units, and fails if it cannot establish their status. After verified
bundle inputs it checks again, then atomically claims `/opt/sage-lantern`
before creating accounts or installing files. A failed install leaves that
claim for review rather than deleting potentially valuable state. This excludes
competing runs of this installer, not arbitrary concurrent privileged writers.

The touchscreen naming/passcode ceremony does not bootstrap every SAGE identity.
`sage-lantern-device-auth.service` supplies the local personal node's existing
Root credential to `VaultSetup`; that service depends on personal SAGE. The
ordinary voice agent uses a separate key and explicit local enrollment. Device
naming updates the local profile; it is not authenticated federation enrollment
or radio ownership binding. These boundaries must remain separate in B's setup.

The current identity-config and fresh app phases implement independent bootstrap,
protected configuration and lock-gated app installation before the owner's
passcode/recovery ceremony. Actual committed Companion enrollment follows native
startup and must be verified. Do not run the old unguarded UI temporarily, copy
A's Root key, invent recovery attestations or publish the local profile as a
verified directory entry to skip these dependencies. The one-time private-access
migration remains an existing-device tool, not the fresh provisioning path.

Current personal-SAGE startup instead gates on device-lock policy, completed app
installation and independently initialized files, so it can start for owner PIN
setup before voice readiness. Dependent voice/model readiness remains separate;
the runtime service is a one-shot check, not the PTT daemon. Inspect the matched
release's exact units/drop-ins rather than applying old blanket
LOCAL_VOICE_READY or PROVISIONED descriptions to every service.

The activation script temporarily creates LOCAL_VOICE_READY before dependency
verification. Preserve failure containment; neither marker presence nor active
systemd status proves physical voice acceptance. Private-access lock and vault
setup markers are additional independent requirements. Marker absence can
select legacy access, so never remove security markers to make startup pass.

PROVISIONED is currently an **identity inventory attestation marker**. It is
written after the inventory, via rename, and contains its digest. The provisioner
does not enforce all hardware/security/release gates or fsync a transactional
release checkpoint. Enforce this checklist in the release procedure; if crash-
durable all-gates completion is required, assign that narrowly scoped extension
to the provisioner owner. Do not redefine marker semantics silently.

Fresh owner setup must verify actual vault capability/enabled/recovery status,
not infer encryption from a PIN screen. Local outbox/history/photos and backups
have a separate at-rest confidentiality scope; application lock is not whole-
disk encryption, key unloading, or protection against root/physical theft.
See `UNIT_A_SECURITY_REMAINING.md`. No new encryption/key scheme is authorized
here. B must not inherit A's unlocked state or claim all records are encrypted.

## Hardware and immediate LoRa acceptance

### Missing acceptance on the selected new release

- B host/IP, base OS/kernel, real boot storage, desktop baseline and B-specific
  audio/camera/radio identities; hardware availability alone proves none of these.
- Fresh install through first reboot, guarded service startup, owner name/PIN
  setup and recovery, encrypted unlock, committed private Companion enrollment
  and public-fingerprint comparison against A. Never compare or export seeds.
- Repeated lock/unlock after the resident-key patch: stable intended storage
  lifetime and restored microphone readiness, real capture/STT/answer, then
  camera preview/photo retention/vision if included. A's native replacement alone
  is not acceptance of these workflows or of runtime R9.
- External-browser Wi-Fi association, capability expiry/finish, persistence and
  reconnect after reboot. Boot theme/rendering, display-off, suspend/wake and
  wake-touch suppression require their own physical checks.
- Real A↔B RF delivery, acknowledgements and application read receipts, bounded
  retries, replay/duplicate rejection, disconnect/reconnect and relay/range tests.
  Keep RF delivery, private SAGE commit and public enrollment/acceptance distinct.
  Public sharing, directory admission and chain sync are not certified by locally
  generated public keys; two Lantern proposers are not a four-validator BFT test.

No item above is marked complete by this audit. Unit A remains incomplete, and
no B deployment or target verification was performed.

Identity clarification: the SAGE key remains the security principal. A MeshCore
public key must be authentically bound to it through the reviewed enrollment
flow; a radio MAC or USB serial is inventory, not authentication or proof of
ownership/global uniqueness. Radio replacement must not replace the personal
SAGE identity or discard its history. The plan-only `lantern-unit-config.py`
retains the radio public key/fingerprint and USB binding, rejects mismatched
unit IDs and duplicate keys in supplied peer inventories, and explicitly reports
whether fleet comparison occurred. Shape/hash checks and operator-supplied
bindings do not establish authenticated ownership. Missing peer inventories
mean no fleet uniqueness comparison, not an implicit pass.

- Record B board/OS/architecture, intended boot root, storage model/serial/free
  space, power/thermal/throttling state and failed services. Enumerating NVMe does
  not establish that the root filesystem uses it.
- Record display/touch, camera, audio firmware and **B's** persistent USB paths;
  test physical PTT, capture stop, speaker if enabled, reconnect and reboot.
  Do not replace missing identity checks with transient `/dev/ttyUSB0` or card
  indices. A's audio firmware serial may need a separately assigned B identity.
- Confirm both Heltec board variants, compatible MeshCore companion firmware
  and exact host protocol, independent radio public identities, antenna and
  reviewed regional channel/power profile before transmitting. USB enumeration
  and copied radio JSON are not RF evidence. Do not invent region settings.
- With authorized synthetic content, record payload byte count, message ID,
  per-direction send/receive/ACK status, latency and bounded retry behavior.
  Exercise duplicate/malformed rejection and disconnect/reconnect. Distinguish
  radio ACK from application receipt and SAGE acceptance/semantic commit. Never
  claim LoRa delivery from an IP-only test or USB port presence.

## Rollback

Base install is not an atomic release swap: failure can leave mixed binaries or
site-packages. Keep a reviewed previous **code-only** artifact and manifest,
perform updates with affected services stopped, verify hashes before release,
and retain failure evidence. Do not restore A's state onto B or roll back B's
identity, queue/replay watermarks, or recovery material with code.

The private-access installer deliberately contains failed private services and
warns against restoring an unguarded backend. Preserve that policy on B: locked
or stopped is safer than a working UI over exposed private endpoints. Recheck
effective units/drop-ins and reboot after rollback. Never manually touch a
readiness marker or remove lock gates to conceal an incomplete installation.

## Historical local checks (no target acceptance)

The test counts below describe earlier source slices, not a new run during this
2026-09-07 documentation audit and not validation of the selected future B release.

Latest combined provisioning regression run: **171 tests and 158 subtests
passed**, including the fresh-base guard and all focused suites below. Shell
syntax checks passed. No base install or remote operation was run on A or B.

The private-access installer's final smoke check now uses fixed loopback HTTP
connections without proxy or redirect handling. Locked admission requires an
HTTP 200 JSON response with exactly one boolean `locked: true` field, bounded
to 256 bytes; duplicate fields, numeric substitutes and malformed responses
fail verification. Each unauthenticated private-route probe must return direct
HTTP 403. Errors remain inside the existing containment trap. These are stricter
readiness checks, not evidence of an auth bypass or a completed fresh installer.

Combined local regression result for this slice: **95 tests passed** across
unit planning/audio export, appliance preflight, voice activation, provisioning,
hostname and private-access installation helpers. No native udev reload, device
installation or hardware acceptance was performed.

The appliance preflight added on 2026-09-06 can validate public fixtures:

```sh
bash scripts/lantern/install-fleet-admin-local.sh --dry-run --unit-id unit-b \
  --metadata /absolute/fixture/unit.env \
  --radio-evidence /absolute/fixture/meshcore-radio-public.json
```

It checks prepared unit identity, public radio inventory and its key digest,
without service calls, hardware probes or writes. This is consistency checking,
not proof of radio ownership or hardware readiness. Alternate metadata is never
accepted for live installation. Tests cover mismatches, malformed inputs,
live-B rejection and both existing and dangling guarded-A markers. These guards
protect against accidentally using this legacy installer on the tested secure A;
they do not make its remaining unguarded-A installation path a secure installer.

Run from the repository root as an ordinary user, without sudo:

```bash
bash -n scripts/install-lantern-unit.sh scripts/provision-lantern-unit.sh \
  scripts/lantern/install-unit-a-private-access.sh \
  scripts/lantern/install-fleet-admin-local.sh \
  scripts/lantern/activate-unit-a-local-voice.sh
bash scripts/provision-lantern-unit.sh --help
bash scripts/provision-lantern-unit.sh prepare --unit-id unit-b \
  --staging-root /tmp/sage-lantern-unit-b-audit-dry-run --dry-run
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/tests -p test_provision_lantern_unit.py
```

Audit results: shell syntax passed; prepare dry-run reported only intended paths;
**7 provisioner tests passed** using isolated temporary staging and synthetic
fingerprints. These are not hardware or native B acceptance. No staging prepare
without dry-run and no live verify was executed.

Optional separate read-only artifact check: `python3 scripts/verify-lantern-bundle.py`
reads/hashes the locally listed release artifacts and may take time; it was not
run in this audit. On the already booted target, a later authorized operator may
use bootstrap `--check`/`--dry-run`; they are not portable host simulations.
Provisioner `verify --dry-run` still requires prepared metadata and complete real
public evidence arguments. Do not paste test attestations into a production
verify command. No existing A activation, migration, recovery or fleet installer
command is a safe dry-run substitute.

### September 7 verified migration preparation

Parent now owns B while agents own A optimization. Tested migration helpers:
32 tests pass. B full read-only preflight passed: exact board, SD serial/root,
NVMe serial `AA20260312512G069617`, 512110190592 bytes, no partitions/signatures
or holders. Compressed and decompressed image hashes match the reviewed image;
decompressed length is 7726932992 bytes. No NVMe write performed yet.

Root staging `/var/lib/sage-lantern-nvme-candidate` contains helper SHA
`c86b8a124632c2efb437077eb3e11f4cc89bc9525cb7ff257e45d2c8a368053e`,
writer SHA `4deb8ec238e711eb135ecc5bf6ae9587f5b70cb1d05fd9e748cc7169fb4f0f44`,
and expected JSON SHA
`757ce8e1b49217990130e34b5d4ce9ae896c0c9a390057ab920745c7fb6aa084`.
Actual 16 MiB SPI backed up there as `unit-b-spi-before.bin`, SHA
`9e6ea454f05389eec6bfc68f4be58666b8c114a4dc1e8c52b220e90ca55692ff`.
Installed vendor bootloader reconstructed as `unit-b-spi-expected.bin`, SHA
`2887f1fba8490891f4a63c9913609a0e6712c96019460c0d9503f6ff30f0cc26`;
it differs from actual SPI. No firmware write performed. Owner asked to confirm
SSD can be erased and keep stable power before proceeding.

Owner explicitly approved SSD erasure and subsequent required bootloader work.
NVMe writer completed under systemd sleep/shutdown/idle inhibition: exact-byte
readback SHA `063bc9500c8406fc86d4926eddefc938e4a114b6720251f635f13089bc871024`
matches all 7726932992 decompressed image bytes. This proves image write, not
NVMe boot or filesystem expansion. SPI update via installed Radxa `rsetup` is
the separate next operation, with preserved original firmware and byte comparison
against the reconstructed expected image. SPI byte comparison subsequently
passed (`SPI_READBACK_VERIFIED`). Parent issued sync and clean poweroff; owner
must remove SD while powered off and restart for NVMe-root verification. Fresh
image SSH host keys/network address may change; verify B identity, not merely IP.

### September 7 NVMe boot accepted

After owner removed SD, connected networking and enabled SSH, parent confirmed
root `/dev/nvme0n1p3`, NVMe serial `AA20260312512G069617`, 476.6 GiB ext4
partition and 445 GiB available. OS remains Bookworm ARM64, Python 3.11.2;
`/usr/bin/node` is absent and must be installed before appliance phases.
New image SSH ED25519 fingerprint is
`SHA256:vi/yQWb7M7HAk3Vmy3YhaQqE90S7YCAKRpKbhTpc8ag`, recorded separately
from the old SD host key. Independent canonical bundle transfer started;
Lantern installation and owner setup remain pending.

### September 7 base installation started

B resumed from vendor desktop auto-suspend. A bounded two-hour systemd inhibitor
`lantern-provision-awake.service` now blocks sleep/shutdown/idle during setup.
Installed missing OS prerequisites via reviewed bootstrap (python3-venv,
build-essential, cmake and apt dependencies). Installed `/usr/bin/node` v22.23.2
from official ARM64 archive SHA
`fff4078c5def658577f92c88db7db3bc0072924bfb93fe52c1e744a54e94abb8`, checked
against nodejs.org release sums.

Transferred archive SHA20a735431d0d43115c7f5b3a2a28d4daad342658a278ea434e426ebb617d4ff3
verified. Preflight caught 21 host CPython3.12 bytecode files packaged outside
the manifest. Preserved original archive and moved that exact `__pycache__`
directory outside payload to `quarantined-host-pycache`; unchanged canonical
manifest then passed full B base plan. Packaging regression assigned to worker.
No manifest exclusions or security checks relaxed.

Authorized base execution started as `lantern-base-provision.service`, using
the frozen payload's phase runner and unchanged reviewed manifest. Native
compilation, base installation and metadata prepare are pending its result;
do not rerun against partial state. Identity-config/app phases remain separate.

### September 7 base completed and identity preparation

Base phase finished successfully at 11:20:34 UTC: pinned llama-server and
whisper-cli compiled; runtime installed; unit-b metadata prepared. No services
enabled or identities created by base. UID994/GID993 and initial runtime hash
`96354f7b09de6068bfcc5754328c90a2557ecee7d70dc456372cbddee6177c62` verified.

Public identity-input archive SHA
`ba1309321e3a44f27645a2d38368a3c3c9bbad57d7aa5fa5e771587c6527ccff` verified
and staged root-owned. Initial YAML installed exclusively; identity-config
read-only plan started. Hash/768 embeddings match A's documented baseline;
they are not semantic embeddings. Media ceilings are 64GiB actor, 128GiB node,
8GiB reserve, not deletion policy. Native identity generation subsequently
completed through the frozen identity-config runner with executed=true and
configuration_applied=true. Protected config installed successfully; no keys or
private payloads collected. App installation, owner name/PIN/recovery, radio
commissioning and native startup acceptance remain, not messaging readiness.

B USB inventory sees touchscreen and Espressif303a:1001 stable serial
`44:1B:F6:62:56:54`, but no USB microphone/audio card or camera. Owner asked
to connect microphone. Serial enumeration alone does not prove MeshCore firmware
or radio readiness. Do not copy A radio identity/evidence.

### B peripheral firmware preparation

Audio board connected: factory serial `28:84:85:B2:B9:B0`, USB303a:1001,
ESP32-S3 revision0.2, 8MB PSRAM, 16MB flash. Dedicated esptool5.3.1 installed
under `/opt/lantern-provision-tools`, separate from commissioned runtime.
Full audio backup attempts at460800 and115200 both stopped at2117632 bytes
with packet-transfer failure. Preserve both partial files/logs under root-only
`/var/lib/lantern-b-factory-backups`; neither is a valid full backup. No flash.

B-specific audio firmware archive SHA
`24eac88a95c8b90d1548f547d7e720c212e10c608fe5f8323e523823b8d940fb`
transferred. Build uses B USBserial `SAGE-LANTERN-B-AUDIO`, not A's identity.

Radio factory serial `44:1B:F6:62:56:54`: ESP32-S3 revision0.2, 2MB PSRAM,
16MB flash verified. Owner confirms default Heltec display. MeshCore query
refused; successful inventory not emitted. Official matching firmware staged:
`heltec_v4_companion_radio_usb-v1.17.1-d929643-merged.bin`, SHA
`67ef59c7687787255c23659e7525b81c1fc88bf679521f4183099a50580d0a3d`.
Radio full backup started independently; successful completion not yet checked.
The owner subsequently explicitly waived factory backups for B; the incomplete
audio backups remain preserved, but are not a provisioning gate.

### B peripheral firmware verified after owner waiver

Both audio and MeshCore firmware writes completed with esptool hash verification.
Audio now enumerates as USB303a:8000, serial `SAGE-LANTERN-B-AUDIO`, ALSA
`hw:CARD=Audio,DEV=0`. Enumeration is not yet microphone capture acceptance.
The radio public inventory query succeeded and is persisted on B at
`/var/lib/lantern-b-public-inventory/radio.json`. Its independent public key is
`c98510036349cec6495b3712880a12ea58d23cd8f09ea9fe9cd804cd98b29e54`.
RF profile configuration and A/B message delivery remain unverified.
The radio factory backup did finish: 16777216 bytes, SHA256
`058ee4ea81706533f6902632b9ae7b24a60ca7c30c4a8927816eb6b4525b9ff1`.
Chromium installation completed successfully for the touchscreen kiosk.

The bounded inhibitor expired; parent added runtime-only masks for sleep,
suspend, hibernate and hybrid-sleep during provisioning. Remove these masks
before device sleep acceptance. Persistent power policy unchanged.

### B vendor AccountsService baseline before kiosk installation

The first app check-only invocation requires the radio inventory basename
`meshcore-radio-public.json`, not `radio.json`. Parent preserved the original and
staged identical public content at
`/var/lib/lantern-b-public-inventory/meshcore-radio-public.json`.

Read-only native diagnosis then passed native prerequisites, independent
commission completion, installed runtime/SDK source closure and personal-SAGE
onboarding service checks. The next gate, `kiosk_plan`, correctly refused the
vendor-created `/var/lib/AccountsService/users/radxa`: root-owned mode0644,
78 bytes, empty Session, SHA256
`b0b572e61e76c7733bdac48892a5092d5f2e8e7848f0d2ed0f2829dd27c7d2e6`.
This is an existing vendor desktop baseline, not proof of prior Lantern setup.

Parent separately reviewed that baseline, stopped `accounts-daemon`, rechecked
the exact hash and exclusively preserved the file at
`/var/lib/lantern-b-provision-baseline/accountsservice-radxa`. Nothing was
deleted. Parent is rerunning the app phase; success is not yet recorded here.
The general no-overwrite rule remains: do not remove arbitrary AccountsService,
GDM or session files to force a fresh install. New devices need their own exact
baseline review and preservation; AccountsService can recreate its record while
running. Do not reuse B's hash as an assumption about another unit.

App completion is `/etc/sage-lantern/appliance-installed.json`; kiosk completion
is `/etc/sage-lantern/kiosk-boot.json`, not a path under the recovery directory.
The installer performs kiosk checks before application writes and configures
next-boot enablement only after app completion. Verify both receipts and restore
the deliberately stopped desktop services only after the corresponding stage
succeeds. Never interpret a missing kiosk receipt alone as proof of no writes.

### B app deployment and initial service startup confirmed

Parent reports the app phase completed with executed=true, installed=true and
kiosk enablement successful. Both `/etc/sage-lantern/appliance-installed.json`
and `/etc/sage-lantern/kiosk-boot.json` exist. Parent started personal SAGE,
device-auth, status and fleet-admin, then AccountsService and GDM3, and reports
successful active service checks. Parent subsequently verified
`curl -sL --max-time 10 http://127.0.0.1:3100/` reached final HTTP200 after
redirects. Personal SAGE, auth, fleet-admin and GDM3 remain active; owner setup
is still unconfirmed. HTTP success is not voice or storage acceptance.

The separately staged helper deliverable is
`/private/tmp/lantern-b-mesh-audio-helpers-20260907.tar`, SHA256
`262fb9cdfacb41889ade82c907118dfdff70674743a8aad5bbbeccdd0ca4b3f5`.
It contains six helper sources, per-file SHA256SUMS and command handoff, with
159 source tests passing against frozen R7 imports. No target installation of
these helpers is claimed. Its mesh installer still requires reviewed adaptation
of the A-maintenance-fence assumption before fresh B application; staging is
not an end-to-end deployment acceptance.

This confirms application deployment/startup, not completed owner setup or
audio readiness. Owner name/passcode/recovery, encrypted unlocked storage
acceptance, B mesh configuration, microphone commissioning, first supervised
broker/voice lifetime and recurring startup remain pending. Do not label B
ready-to-test voice or fully provisioned from active services alone.

### B helper v3 reviewed and staged; owner setup still pending

Independent final review approved the fresh-B containment/install/release helper
with no reported blocker: 59 installer tests, including 15 cases using the real
fresh installer-created history layout; 201 combined mesh/audio tests pass.
This supersedes the v1 A-maintenance-fence blocker and v2 history-layout mismatch.
The A path remains unchanged. Fresh B uses a distinct, actual containment
transition and explicit verified release; no prior A lifetime is fabricated.

Parent staged `/opt/lantern-b-mesh-audio-helpers-v3` from
`/private/tmp/lantern-b-mesh-audio-helpers-20260907-v3.tar`, SHA256
`7b72839fe4cd01aa84bd69ae08e261a969ad60cc5dcc3a4a60191104f86bdf19`.
All eight file checks passed on B. Installer SHA256 is
`d51c0a9406d92dc0e77201b266845c9e00106d1838e56f85ff0e269c5da4681f`.
No containment or mesh apply has run. Source review and staging are not native
containment, commissioning, or end-to-end acceptance.

Live `/setup/status` reports enabled=false and recovery_backup_confirmed=false.
Owner setup/unlock remains the prerequisite for signed private-storage mesh
preparation; do not bypass that gate. Separately, parent applied the B radio
name and 917.375MHz profile frequency and reports successful readback. This is
radio configuration evidence, not A/B message delivery or federation acceptance.

### First B mesh/audio lifetime: remaining source gates

After successful app installation, start guarded personal SAGE/auth/status/UI
and the kiosk for the owner's name, passcode and recovery ceremony. Confirm an
actively unlocked, encrypted independent B node before preparing mesh storage
bindings. Keep broker/voice and recurring startup inactive at this stage.

`prepare-fresh-mesh-runtime.py` requires B's actual commission/app/binding
receipts, public radio inventory and fingerprint, matched native binary and
current reviewed SAGE policy/hash. It performs signed local storage observations
using B's existing Companion; it does not enroll B in public federation or prove
radio delivery. `install-prepared-fresh-mesh.py` installs only the prepared mesh
configuration and radio device drop-in after separate review; it starts nothing.
These supplemental helpers must be pinned separately when not in the frozen
release; do not edit its payload or manifest in place.

Prepare the exact installed-R7 audio guard/ALSA constants through
`install-audio-commissioning-config.py`. Reload the reviewed units before
`sage_lantern.audio_readiness --inspect`; commission with the observed input hash
and explicit microphone-capture approval. UAC enumeration alone is insufficient.
The admission check binds the actual voice command, guard, runtime files and B
audio identity. Do not hand-write audio-ready or commissioning markers.

Verify the local model, then use `sage_lantern.deployment_bringup --inventory
PATH --inventory-sha256 SHA` before its separately authorized `--execute`.
Inventory here is the complete deployment file-hash map, NOT radio inventory.
`ColdBrokerBootstrap` requires fresh inactive broker/voice cgroups, exact unit
drop-ins and Restart=no, no prior supervisor/deployment fence, and unlocked
storage. It owns broker/voice startup. Only its real completed first lifetime
may seed `handoff-first-audio-lifetime.py` and B's recurring startup policy.
Any failed attempt is retained for recovery review rather than deleted/retried.

### September 7 physical NVMe detection

Owner reinstalled B's NVMe with power disconnected and brought it online.
Read-only SSH inspection at `192.168.50.96` identifies `rock-5b-plus`, board
Radxa ROCK 5B+, still booting `/dev/mmcblk1p3` (ext4) on SD serial `0x128338a1`.
`/dev/nvme0n1` is detected as Netac NVMe SSD 512GB, 476.9 GiB, serial
`AA20260312512G069617`. `lsblk` shows no partitions or filesystem for that NVMe;
this is not a secure-erasure or blank-media certification. No storage writes,
formatting, firmware updates or Lantern provisioning were performed. Use B's
exact observed serial for future reviewed provisioning; never A's serial.
