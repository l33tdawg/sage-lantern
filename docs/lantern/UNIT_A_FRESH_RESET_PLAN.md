# Unit A authorized fresh replacement

Status: reset/base/identity/app installation complete; startup repaired and
backend healthy; on-demand camera installed and health checked; owner passcode
and recovery confirmation complete, first name setup/unlock acceptance pending;
fresh vision/display/sleep activation pending, 2026-09-06. Earlier entries below
are chronological historical evidence, not current readiness claims.
The user approved discarding Unit A test history, photos, messages and identity
for a fresh installation. This is not authorization to erase the base OS,
network credentials, unrelated projects or the separate consensus test cluster.

The complete rebuilt ARM64 UI was subsequently archived, uploaded and unpacked
only under /tmp/lantern-fresh-ui-arm64-20260906 on Unit A. Target checksum
verification passed for archive SHA256
ebac360799ea4dafe1ab6dc6715b7e4dd2be5925abc362fbcdd5c51bfc28a636.
The built server entry exists; unshare and ip are installed for an isolated
network-namespace startup smoke test. No UI process has been started from this
candidate, and no running app files were changed. Next smoke test must not
contact the existing private auth/status backends or bind their production ports.

Subsequent isolated startup passed: candidate vinext served /device with HTTP
200 and 9,748 HTML bytes on private-namespace loopback port 3310 as nobody
(UID 65534). The harness verified its network namespace differs from PID 1,
cleared the environment, and terminated the child process group afterward.
No live auth/backend interaction or browser rendering was tested.

The first attempt failed before process start because /usr/bin/node is absent.
Unit A instead has /usr/local/bin/node pointing to
/opt/node-v22.22.0-linux-arm64/bin/node. The successful isolated test used that
exact executable. Fresh app installation still requires an executable at
/usr/bin/node, so OS prerequisite reconciliation is mandatory before reset;
the smoke test does not satisfy that installer requirement by substitution.

Review also found fresh sleep fencing assumes the old kiosk argv/profile while
the app installer supplies --profile-dir with its new unit-specific path.
Lovelace is correcting the trusted binding and regression tests. Runtime artifacts
must be rebuilt after that correction; the current release is not frozen.

The phase runner now checks the exact Node prerequisite before any base apt or
install command, rather than discovering it after partial installation. The app
installer shares this check, validates the numeric version tuple and runs the
version probe with a cleared environment. Focused suites pass: 11 phase-runner
tests and 32 app-installer tests, including missing-path, unsupported-version,
malformed-version and no-mutation-on-failure cases. This source change prevents
late failure; it does not install Node or resolve Unit A's missing path yet.

Subsequent target prerequisite repair completed with explicit approval: created
/usr/bin/node as a new root:root 0755 regular file, exclusively (no overwrite),
using the already installed v22.22.0 executable bytes. Source and destination
SHA256 both equal
8eeefcacdf48f58541a651016e604055d14a992e39df98636b76495bc7244395.
The original /opt Node tree is owned by rock, so no root service executable
symlink into that user-writable tree was created. The repair checked the source
bytes against the previously observed digest before writing and fsynced the new
file/directory. /usr/bin/node --version now returns v22.22.0. This is local-byte
integrity evidence, not a new upstream authenticity claim. No live services were
restarted, and no existing file or Lantern state was replaced. Unit B still needs
the documented Node OS prerequisite in its base image or offline provisioning.

Canonical assembly has a separately confirmed blocker: the manifest scanner
rejects apps/lantern-fleet-admin/node_modules/miniflare/dist/src/index.js for
embedded private-key material. Curie's finish-ui command exited 1 (session59599,
terminal); no assembly job remains running. Root contains 45 selected copied
files and a 36-entry lock, but no copied UI or final canonical manifest. Local
inspection places the PEM block in bundled src/http/cert.ts, adjacent to a
localhost development certificate. That context alone is not authority to
disable scanning. Curie is investigating official package provenance and exact
key/file hashes without exposing key bytes. No exception has been introduced.

Sleep binding correction is now implemented and parent-reviewed: trusted installed
unit metadata/app binding/receipt determine account, UID, exact launcher arguments
and browser profile. Missing or mismatched fresh records fail closed instead of
falling back to the legacy profile. Parent focused checks pass 44 kiosk tests and
11 controller tests. Two fresh offline runtime builds compare byte-for-byte equal:
wheel bd8325bec7f3d26940f1c59949780c6872b0fd467f9a94421b091f790eb6714a,
source snapshot 9af6fe73d7e543e4d4ac49254416efc87716f5ed50e3582a54da47611939ec86.
The separate artifact root now contains this rebuilt wheel and its updated lock
entry; all 36 selected lock entries pass size and SHA256 verification. Its earlier
non-UI assembly audit describes the old runtime and remains historical, not the
current runtime hash. UI scanner provenance remains unresolved. No sleep deploy
or physical suspend acceptance occurred for these new bytes.

Root-only radio backup checks also completed: root:root 0600 regular file,
16,777,216 bytes, SHA256
d148d72070ab71f67d1610c5ee43e2f38e03d93cdf95fa5531205f65dbf39b66,
matching the historical public inventory. No bytes copied or recovery performed.

Full-feature release review found the canonical profile/base installer omitted
the vision projector, despite the separate vision installer requiring it.
The profile now requires mmproj-BF16.gguf and base installation copies it alongside
the text model without enabling inference. Download-free retrieval of the public
model from Unit A completed: 675,569,344 bytes, SHA256
302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6,
matching both the installed artifact and documented pinned projector. No private
media/state was copied. The partial artifact lock now includes this fifth model
file. Stager/base-installer suites pass 96 tests and 77 subtests. Vision service
configuration, real inference validation and secure voice activation remain
separate gates; copying weights is not photo-answer acceptance. The historical
partial-assembly audit now also predates this added artifact; replace it with an
explicit reviewed revision before using scratch finish-ui, not by bypassing its
source-change checks.

Dependency review completed; see PUBLIC_DEPENDENCY_KEY_EXAMPLES.md for the
upstream evidence and exact whole-file hash exceptions. The scanner also rejects
escaped key literals now. All 31,128 UI files pass; manifest/stager tests pass
32 tests and 114 subtests. A new non-ui-assembly-revised.json verifies that only
the intended runtime replacement and projector addition differ from the original
audit, reconstructs/checks the old lock hash, and retains the original audit.
Scratch finish-ui then completed successfully: 31,174 copied artifact files,
37 locked files, 31,328 canonical source inputs, 517 UI packages. Stack lock SHA
a26159d030dca4bca832dd76a81437d9302790cea4cd4b6f44c4fd17dbba4e62.
Full canonical staging/provisioning gates have started (session41265, log
/private/tmp/lantern-fresh-canonical-gates-20260906.log); no result yet. Do not
interpret completed artifact assembly as deployment authorization or acceptance.

First canonical staging completed, but its subsequent provisioning check failed:
the app installer duplicated the old npm inventory algorithm (platform-excluded
parents and bundled test fixtures handled incorrectly), while the stager already
had the corrected algorithm. Parent extracted one shared dependency_inventory
validator and both consumers now call it. Missing/extra packages and dependency
closure checks remain mandatory. Focused suites passed 62 tests/105 subtests
before adding the explicit shared-validator integration regression. A new r2
stage is running with that regression and current source closure (session59451,
log /private/tmp/lantern-fresh-canonical-gates-20260906-r2.log). The earlier
/private/tmp/lantern-fresh-canonical-20260906 manifest remains historical and
must not be installed; its provisioning gate failed. No in-place manifest edits.

Latest service inspection confirms the old app runs from
/home/radxa/sage-commons/apps/lantern-fleet-admin, with device-lock, sleep and
91-deployment-recovery drop-ins. Fresh deployment uses /opt instead. Remove old
startup authority, not the operator's whole source checkout. The unprivileged
mount/AccountsService probe did not establish mount boundaries or read the
root-only login record; those checks remain required before destructive reset.

Revision r2 passed all canonical staging, current source-closure and runtime/SDK
bundle gates (session59451 exit0). Manifest SHA256:
5af1df353453becdac41731e6792c4fb14393061cf2bbc658549fd04a6e5b32f.
Output /private/tmp/lantern-fresh-canonical-20260906-r2 has 31,330 selected files.
The gate correctly keeps deployment_ready/native_acceptance false and canonical
messaging unavailable pending installed storage/auth validation. Transfer archive
preparation started; target installation/reset has not.

The corrected findmnt JSON query confirms / is ext4 with no subordinate mounts
under /opt, /var, /home or /usr; /run is tmpfs with separate user/credential mounts.
Recheck at reset, never infer that /run can be recursively cleared. GDM3 is the
default manager, graphical.target is default, and gdm3.service is boot-enabled as
an alias. Shared GDM config SHA256
93e09827355a55a1bf34a97b688b7efea1464bcf8dd9e8c454ff43222dea4a12
has AutomaticLoginEnable=true and AutomaticLogin=radxa. Root-only AccountsService
record SHA256 2e5b2a9a67ab9ebce7b5973a8733f2d80268498366e9c25a1eec7be24d789525
has Session/XSession=sage-lantern and SystemAccount=false. These are existing
shared OS records requiring explicit reconciliation with the fresh kiosk guard,
not missing prerequisites or permission to erase unrelated account fields.

Transfer archive creation completed: SHA256
2b27de36c5d8bc4a500b48bb6b6b2dbb460172e56b4a1498b5f0615120d6f2e0,
/private/tmp/lantern-fresh-canonical-20260906-r2.tar. SCP to the same basename
under Unit A /tmp is active in session57325; do not unpack a partial transfer.

The metadata-only reset preview helper/tests are implemented outside the release
closure (scripts/lantern/plan-fresh-reset.py). Parent tests pass17 tests9subtests.
Target preview completed exit2 with 61,868 observed path records, marked
inventory_complete=false. It correctly refuses to follow recursive links such
as Chromium Singleton entries and enumerates service enablement links for exact
entry review. It also preserves unrelated operator-home entries, shared OS
configuration and unknown old artifacts. JSON remains on Unit A at
/tmp/lantern-reset-preview-20260906.json. Its reset_authorized=false describes
the tool's lack of destructive authority, not withdrawal of the user's approval.
No deletion occurred; do not use the preview as an executable removal manifest.

SCP session57325 completed exit0. Unit A archive sha256sum check passed against
2b27de36c5d8bc4a500b48bb6b6b2dbb460172e56b4a1498b5f0615120d6f2e0.
Session37601 is now unpacking into a new /tmp/lantern-fresh-canonical-20260906-r2
directory and running the native Python3.11 provisioning runner in plan-only base
mode with the reviewed manifest hash. No --execute flag is supplied. Await its
actual result before treating the target copy as validated; do not restart merely
because extraction/verifier output is quiet. No installed state has been removed.

## Verified target

### Lineage completion and healthy onboarding

Native origin probe74322 proved the newly initialized chain has no runtime
stores, only its Comet genesis/config and pristine signing state (height0,
round0, step0, no signature). The exact genesis SHA is
d165d996982bb225f22ab66a53960c81111ec3109401b7f48c48ddcd41e7dec7;
chain sage-personal-njypug6rngenot4ky2mrsfo2ut; initial height1, application
version23, one validator. Both lineage markers were absent. All relevant
services were stopped. No private key bytes were read by that probe.

The narrow completion helper binds the exact binary/release, commissioning
receipt outputs, original public identities, genesis hash and pristine state;
holds the real SAGE instance flock; and exclusively/fsyncs only the missing
release-version and fork1 files. It neither relaxes migration guards nor
regenerates identity, edits genesis/AppVersion, or erases any chain data.
Four preflight regressions pass and separate review completed. Verified Python
source bytes are compiled directly rather than loading unverified cached code.

Native check98154 passed, then execution87717 completed the metadata and started
services. Helper SHA ef493805732e9b5cf99ad96879e7acd14afd107005f5243823a651d615cace5a;
target /tmp/lantern-complete-fresh-lineage-20260906.py. Durable evidence is
/var/lib/sage-lantern-hardware-recovery-20260906/fresh-lineage-completion.json.
Do not rerun this helper now: the chain is no longer an unstarted fresh origin.

Actual HTTP evidence: app /device returns200, SAGE /health returns healthy.
Setup status with the required kiosk Origin/control headers returns enabled=false,
has_existing_vault=false, recovery_backup_confirmed=false, can_initialize=true;
device state returns locked=true. The earlier no-Origin setup request correctly
returned forbidden and was not a backend fault. Owner PIN/recovery remains unset.

GDM startup initially timed out on leftover old daemon1209, then systemd removed
that process and recovered automatically; no blind restart was issued. The
authoritative later state is active gdm.service PID334348 and local x11
gdm-autologin session92, active on seat0. Chromium PID334465 owns the new
unit-a profile's SingletonLock, and parent launcher334384 includes the new
profile and reviewed kiosk script. Direct Chromium argv equality checks returned
false and were not treated as URL/profile proof; the independent launcher/lock
evidence verifies the profile. Owner was asked asynchronously to confirm the
physical setup screen and complete setup privately. No physical acceptance yet.

The root-cause initializer fix and regression test are now in the actual sibling
SAGE worktree: cmd/sage-gui/lantern_fresh_init.go and
cmd/sage-gui/lantern_fresh_init_markers_test.go. They are byte-identical to the
tested new isolated v27 copy. Offline Linux Go tests pass4 tests/16 subcases
(agent session83857, exit0; cached golang:1.25.13-alpine, network none, read-only
source/dependencies). Migration/consensus guards and frozen r3 source are unchanged.
Two corrected ARM64 binary builds are assigned to Curie for a future B-ready
release; no corrected binary or new manifest has been deployed yet. A still
runs the verified r3 binary with its explicitly completed fresh metadata.

### Identity/app installation complete; startup defect found

Identity-config session97203 completed exit0, executed=true and
configuration_applied=true. New independent Root/Companion/public identities
and genesis were created on-device; protected config and completion receipts
were installed. No PIN/recovery or vault encryption has been initialized.

App session36261 also completed exit0. The r3 app/auth/status files, mandatory
lock policy and reviewed fresh kiosk setup are now installed. This includes the
larger wake/unlock typography, but it has not yet been shown on the physical
screen. GDM has not been restarted. Public/voice/LoRa providers remain disabled.

Initial service-start session46957 reported processes active, but the subsequent
HTTP probe failed (app port3100 connection refused). Journal inspection found
the actual defect: fresh init creates genesis without version.txt and
fork-version.txt, so migrateOnUpgrade correctly refuses marker-less persisted
chain evidence. The node exits before opening stores; do not bypass that guard,
delete genesis, repeat identity creation, or claim service-start means readiness.

Session51860 completed exit0 stopping personal-SAGE/auth/app retry activity.
Status service remains separate; new keys/genesis are preserved. The root-cause
fix is being developed/tested only in a NEW isolated v27 source copy:
/private/tmp/lantern-fresh-v27-marker-source-20260906. Frozen r3 source/provenance
and deployed binary remain untouched. No v28 activation is authorized. Existing
A requires separately verified completion of missing initialization metadata;
future fresh initialization must produce it automatically. This is a concrete
native first-run regression not covered by earlier initializer-only fixtures.

### Base complete; identity commissioning started

Session39565 completed exit0: both pinned native llama-server and whisper-cli
compiled successfully, matched runtime 0.2.0 installed, and metadata prepare
completed for unit-a. The runner reports executed=true for base, ready=false,
provisioned=false. No base service was enabled. Log remains
/var/log/lantern-fresh-base-20260906-r3.log. Do not rerun the fresh base installer.

The old public-only radio binding was verified and durably preserved on-device
at /var/lib/sage-lantern-hardware-recovery-20260906/radio-public-before-reset.json,
SHA e4f1c914915568681c7bdf64a33b59826b0e9e900383695c40cfc9907d6a716a.
Its old destination is now absent for the fresh installer. Session58387 exit0;
no radio firmware or identity was altered. No private data copied off-device.

The secret-free initial YAML/commissioning plan passed a local merge/policy
check and separate agent review. Helper
/private/tmp/lantern-unit-a-prepare-identity-20260906.py, SHA
6012d1c19fed8314478f98b047aa10dcf80c468ae982c362b17438efe49fddaa,
then exclusively installed service-owned 0600 initial config on Unit A after
base success. Native session97203 is now executing frozen r3 identity-config
with explicit owner-approved independent Companion bootstrap and the recorded
YAML/plan hashes. Log /var/log/lantern-fresh-identity-20260906-r3.log is private.
Do not retry or remove a commissioning fence if this phase fails; inspect first.
No identity completion or encryption initialization is claimed yet.

Hardware-bound app invocation is staged, not executed:
/tmp/lantern-unit-a-install-app-20260906.sh, SHA
21022b0212d5acf05e9a0fdc3c4be46787174c0591eabb95782fb9e4542fe96a.
It accepts only --plan/--execute, pins r3 and the reviewed GDM/radio/audio
bindings, and opts into owner vault setup plus locked kiosk autologin. It must
run only after successful identity-config; it cannot bypass fresh install gates.

### Authorized reset executed

The first native execution (session70650) stopped safely before recovery movement
or private-state deletion: GDM stop left the old local logind session alive.
Lantern unit fragments had already been preserved outside unit load paths in
/etc/lantern-unit-a-reset-hold-20260906 and replaced with effective persistent
masks; GDM was runtime-masked and inactive. No automatic retry was performed.

Read-only PID/cgroup inspection identified only the old local touchscreen session
as the blocker: session-1.scope, radxa UID1000, gdm-x-session/bash/ssh-agent and
Chromium children. The explicit reconciliation verified the exact local x11
gdm-autologin session before terminating it. Both subsequent quiescence checks
require its kernel cgroup to be absent or populated=0, independently of argv.

Reviewed reconciliation helper SHA256:
30c2644a24ff6f239082d7793ace032fa1d84f61fb47495f1191c3a1478b40c4.
Host /private/tmp/lantern-unit-a-reset-20260906.py; target
/tmp/lantern-unit-a-reset-resume2-20260906.py. Nine focused tests pass; separate
agent re-review found no remaining concrete blocker. Tests are not native proof.

Native session39565 then reported reset_complete=true. The entire exact-inventory
recovery directory (radio/audio firmware, prior network/kiosk configs) is preserved
on-device under /var/lib/sage-lantern-hardware-recovery-20260906, root-private,
with verified hashes and fsynced files/directories before deletion. The existing
AccountsService/.dmrc records and GDM baseline were separately preserved there
as reviewed kiosk reconciliation, not broad user-account deletion. Old dedicated
Lantern state/install/config/profile roots and exact startup entries were removed
under effective masks. OS, network settings, SSH, unrelated files and service
accounts were preserved. This is not a forensic secure-erasure claim.

The replacement GDM baseline disables autologin until fresh locked kiosk setup:
dcf7432d792f721cf99f080ed03f5240d819fc6e277b14948ba4d543b77b20c0.
Native evidence: preserved directory/reset-evidence.json. GDM remains stopped;
do not start it before new locked app installation and onboarding prerequisites.

The same live session39565 now runs the r3 base phase with --execute and explicit
offline native-build authorization. Log: /var/log/lantern-fresh-base-20260906-r3.log.
No apt/network installation flag was supplied. Root ownership was established on
the verified staged release before the runner's repeated manifest checks. Await
the actual terminal result; do not rerun into a partial base installation.

A separate read-only log check (session75006, exit0) confirmed the actual offline
base installer had reached local-wheel pip installation (NumPy, matched SDK and
runtime, Piper, cryptography and ONNX dependencies). This is stronger than merely
observing the SSH wrapper, but does not prove the native builds or base completed.

Before app installation, separately reconcile the intentionally preserved old
/usr/local/share/sage-lantern/meshcore-radio-public.json: the fresh app writes its
own normalized per-unit public binding and rejects any existing destination.
Preserve the old public-only file with the other on-device recovery evidence;
do not weaken the fresh-target guard or reuse another unit's radio/SAGE identity.

Prepared commissioning inputs, not yet installed: initial YAML SHA
38a76a5932f5ee3b8e1c07bdf6790052bf195ad5a9b61f89a31d34a428bc1906;
plan SHA0467bab47eb0d88b18a473da5a0a4e361cf1084a8b1e09bdb8da3576333f18eb.
Explicit initial storage ceilings: 20GiB/250,000 objects per actor,
40GiB/500,000 objects per node, 10GiB free-space reserve. These are capacity
limits, not retention/deletion policy or proof that encryption is initialized.

### Initial private configuration and retrieval baseline

A live, root-authorized read of only the embedding provider/model/dimension
fields completed (session49041, exit0): provider=hash, dimension=768, model=null.
No full configuration, key, history or media was copied. Thus prior signed
embedding/hybrid API acceptance does not prove semantic embeddings were used.
Preserve explicit hash/768 for the fresh upgrade rather than silently changing
retrieval. Track offline semantic embedding evaluation and cross-day paraphrase
recall as remaining work; do not claim hash similarity is semantic recall.

A secret-free initial YAML candidate is prepared at
/private/tmp/lantern-unit-a-fresh-inputs/initial-sage.yaml. It binds the reviewed
private paths/loopback listeners, disables external federation/quorum, enables
the local voter with required=true (fail closed), and requests the independent
local-field-knowledge Companion at clearance1. Encryption/media start disabled
only for initialization: commissioning must enable protected media, and owner
PIN/recovery setup must initialize encryption before protected use. No chain ID,
key bytes or PIN is supplied. Candidate is not installed or native-validated.

Fresh vision sleep compatibility now passes 134 parent pytest cases: only the
complete exact installer sleep set is accepted, and the later 95- vision
override restores explicit fresh config/audio/drain arguments while retaining
sleep fences and the inference guard. This remains plan-only and outside r3.

r3 SCP completed (session49835, exit0). Target checksum/extraction/native plan
is active in session65136; no installed service or private state is changed.

Session65136 subsequently completed exit0: archive checksum passed, fresh r3
directory extracted, and native Python provisioning base plan validated against
manifest b1ad27152775e0ae4db4ca299ec9884f6561bb8315fd76be885bcf04f2f0140d.
Output explicitly reports executed=false, configuration_applied=false,
provisioned=false and ready=false. The full r3 is staged on Unit A, not installed.
Initial YAML candidate SHA256 is
38a76a5932f5ee3b8e1c07bdf6790052bf195ad5a9b61f89a31d34a428bc1906.
No authorized reset/deletion has happened. A second agent's sleep-planner review
failed at model capacity; parent inspected the exact-set/ordering change and ran
134 tests, but do not count the failed assignment as independent review.

### Latest release revision and prerequisite evidence

The r2 target extraction/base plan completed successfully (session37601, exit0),
without execution. Target OS prerequisite check also passed (session71068,
exit0): Python3.11.2, required packages/commands, exact /usr/bin/node v22.22.0,
and approximately 390GiB free. This is not installation or onboarding evidence.

The new wake/unlock typography passes 108 UI tests and production build. Frozen
r3 now passes canonical staging, current-source closure and runtime/SDK gates:
31,330 selected files; manifest SHA256
b1ad27152775e0ae4db4ca299ec9884f6561bb8315fd76be885bcf04f2f0140d.
Host output: /private/tmp/lantern-fresh-canonical-20260906-r3.
Archive SHA256:
10a1deb6aed51c0caec3aea6c34d53e4dcedf84dd3ee68b74a9853a2f9e544b9.
Transfer to Unit A /tmp is running in session49835. Do not extract a partial
transfer. Earlier r2 is historical and does not include the typography change.

Fresh vision planning passes 115 parent-run pytest cases. The initial reviewer
found preservation of the bound ALSA/config/broker drain arguments, but rejection
of installed sleep drop-ins; that integration correction is in progress. The
planner is outside the frozen release and provides no activation authority.

- SSH target: radxa@192.168.50.103; hostname sage-lantern-a; Linux aarch64.
- Boot ID at inventory: 7fc2a334-26e4-48e8-b585-42e1072c4346.
- Node v22.22.0. Existing app/auth/private-SAGE/voice/camera services active.
- Radio: Espressif 303a:1001, serial F8:5B:1B:A5:F6:08.
- Camera: Logitech 046d:0825, serial 5E4BEE10; stable video-index0 currently video1.
- Audio: SAGE Lantern Audio 303a:8000; actual ALSA and stable USB binding still
  need collection. USB presence is not capture or radio-delivery acceptance.

Subsequent ALSA/udev inventory resolves the intended capture device to
hw:CARD=Audio,DEV=0, USB serial SAGE-LANTERN-A-AUDIO, vendor/product 303a:8000,
stable path /dev/snd/by-id/usb-SAGE_Commons_SAGE_Lantern_Audio_SAGE-LANTERN-A-AUDIO-00.
The webcam has a separate microphone; do not choose it merely because it appears
first. No audio was recorded by this inventory. The kiosk operator is confirmed
radxa UID1000. Current USB observations match the stored public radio inventory's
physical serial; its radio public key remains historical Sept 2 evidence, not a
new serial protocol interrogation. A normalized nonsecret copy is prepared at
/private/tmp/lantern-unit-a-fresh-inputs/meshcore-radio-public.json, with the
fingerprint validated by the existing configuration parser before use.

The public inventory identifies a hardware factory recovery image within the
otherwise disposable old state root:
/var/lib/sage-lantern/recovery/radio/factory-v4-full-20260902.bin.
Keep this hardware recovery material on-device in a separately reviewed root-only
location before deleting that tree; do not copy it to the host, expose its bytes,
restore its identity into fresh SAGE, or erase it under a broad state-root wipe.
Its continued existence/hash have not yet been verified with root access.

## Ordered replacement boundary

1. Validate the complete matched release and execute target smoke checks before
   interrupting the working installation. Native version execution passed;
   complete app startup and release assembly remain outstanding.
2. Obtain an exact root-readable path/unit inventory, including service fragments,
   drop-ins, enablement links, app working directories and mount boundaries.
   The current non-root inventory is preliminary, not a deletion manifest.
3. Disable automatic sleep and producers, stop the kiosk and every enumerated
   Lantern service/timer, then verify no remaining processes or active units.
   Keep SSH and network management running. Do not invoke legacy migration or
   claim drained queues when intentionally discarding the authorized test state.
4. Remove only reviewed Lantern paths and stale startup links. Never follow
   symlinks into other trees or recursively cross mounted filesystems. Retain
   stopped state on failure; no blind retry or partial readiness marker.
5. Reload systemd, clear only enumerated stale failed unit state, then run the
   existing strict fresh-target guard unchanged. Unknown remnants require review,
   not removal of the guard or deletion by a broad wildcard.
6. Execute matched base, independent identity-config and locked app phases.
   Apply reviewed hardware/sleep/boot wiring separately; complete name/PIN setup
   on device and verify encryption, wake, audio, camera and public workflows.

## Preliminary removal scope to review

The core installation occupies /opt/sage-lantern, /etc/sage-lantern and
/var/lib/sage-lantern. Additional real state exists at
/var/lib/sage-lantern-device and runtime directories under /run. Current root
deployment staging directories also exist outside the core state root. Their
contents and ownership have not been inspected; do not assume they are harmless
or reuse them in the fresh release.

Lantern startup artifacts include named systemd units/drop-ins/enablement links,
/etc/systemd/system/systemd-suspend.service.d/90-sage-lantern.conf,
/etc/tmpfiles.d/sage-lantern-suspend.conf, radio/audio udev rules, three separate
/etc/sage-lantern-*.enabled markers, named /usr/local/libexec helpers,
/usr/local/bin/sage-lantern-{session,kiosk}, and the Lantern X session desktop.

The operator also has /home/radxa/.config/autostart/sage-lantern-kiosk.desktop
and /home/radxa/.config/sage-lantern-chromium. These old autostart/profile paths
must not reactivate an obsolete app or retain old authenticated browser state.
The fresh installer uses a different unit-specific browser profile. Numerous
old Lantern staging archives exist directly under /home/radxa; list and classify
them explicitly, never erase the operator's entire home.

## Preserve or separately reconcile

### Wake/unlock typography follow-up

The requested touchscreen fix uses centered 36–52px progress headings for
Waking and Unlocking, with the keypad hidden while unlocking is pending.
All 108 UI tests pass and the local production build completes. This change
was absent from frozen r2 but is installed with r3 (app session36261). The six
focused wake-input/typography tests pass again. Physical readability remains
owner acceptance, not something proved by stylesheet assertions.

### Active display binding follow-up

Read-only SSH session39362 confirms seat0 active session92, radxa UID1000,
and onboarding still pending: enabled=false, can_initialize=true, no existing
vault or confirmed recovery backup. Session3589 reads only the launcher's
DISPLAY/XAUTHORITY environment fields: DISPLAY=:1 and
XAUTHORITY=/run/user/1000/gdm/Xauthority. The old Debian-gdm sessionc1 remains
on seat0. The legacy display-power helper/installer/unit hardcoded :0; do not
activate those bytes against this newly installed kiosk or terminate the old
greeter merely to make that assumption true.

The source helper now consistently accepts a validated local DISPLAY for xset,
xprintidle and native XScreenSaver queries. Missing, remote and malformed
values fail closed before any X operation. This is configuration validation,
not session authorization: trusted active-kiosk selection and lifecycle wiring
are still being implemented separately. Tests: 52 passed, 55 subtests, including
:1 propagation and rejected remote/missing displays. No power helper activation,
physical display-off, deep-sleep or owner onboarding acceptance is claimed.

### Corrected initializer binary follow-up

Two isolated offline builds of the fresh-v27-markers source compare identical:
66257022 bytes, SHA256
4bcc653591539b04935005a67653cfda70802899f5d2efc46fabc3e230566814,
version lantern-local.20260906-fresh-v27-markers. Production inventory SHA256
a3cf669b1af9c5088b0718952560317c33b4086d1eed0bc1ff28c12adbc973b2.
Evidence: /private/tmp/lantern-fresh-sage-markers-20260906-audit.json;
outputs have -a and -b suffixes. These corrected binaries are not deployed;
Unit A still runs r3 with its previously verified fresh-lineage completion.
A marker-only canonical r4 package completed in agent session34311, exit0:
/private/tmp/lantern-fresh-canonical-20260906-r4, manifest SHA256
1f79ff6eb071a794c2915a50ccaa9c9f2896a208d847579193dc8822fba8ab39.
Its source-closure gates refer to frozen r3, not current repository changes.
Audit: /private/tmp/lantern-fresh-marker-r4-gates-20260906.json. It deliberately
does not include the in-progress display/session wiring; deployment_ready=false.
Keep this baseline immutable and use a new path for coherent activation staging.

### On-demand camera activation

Read-only SSH session8831 confirms the stable Logitech camera path exists
(video-index0 currently resolves to video1), v4l2-ctl is installed, the mandatory
lock marker is root-owned mode0644, and the installed runtime returns
lock_enabled=True and authorized(None)=False. Camera, display-power and
idle-suspend units were not-found/inactive before this activation.

Camera source equals frozen r3 byte-for-byte; 37 camera tests and 81 subtests
pass. Reviewed root activation session54896 completed exit0 after verifying
exact installer/helper/unit hashes, trusted stage ownership, mandatory guard,
and absent destination files. It installed and boot-enabled only the on-demand
camera service. GET /health returns camera_present=true, vision_ready=false.
Startup and health do not open the camera. No photo was captured or owner
session credential used; live preview and photo answers are not accepted yet.

Activation checker: /private/tmp/lantern-activate-camera-20260906.py and target
/tmp/lantern-activate-camera-20260906.py, SHA256
1b8bdd9a5459f0d543be12ef18d82fea3c995cd303b6cfdda4107202176d3f9d.
It refuses a rerun once installed. The legacy camera unit still defaults to
Unit A's exact camera serial; independent Unit B camera selection remains a
provisioning gap rather than something proved by this A-only activation.

### Immediate unlock/relock UI fix

User reported Unlocking followed immediately by Locking after setting a PIN.
SSH16748 confirms enabled/recovery_backup_confirmed/has_existing_vault=true and
can_initialize=false. No passcode, recovery bytes or session token was read.
Auth/UI have zero restarts; private SAGE's restart counter52 includes earlier
startup failures, not proof of a new crash. SSH13824 confirms voice-loop is
inactive/dead, MainPID0, port3197 connection refused; auth journal query is empty.

Root cause in the UI: both initial unlock and presence polling awaited optional
voice session/link inside authentication-failure cleanup. An unavailable voice
service thus revoked an otherwise usable device session. The new linkVoiceSession
helper contains voice failure, and the UI starts that attempt without blocking
unlock or presence renewal. Auth activation, profile loading, token-generation
checks, presence authorization, inactivity and visibility locking remain mandatory.
All 112 UI tests and production build pass; an independent agent review found no
blocker in the exact source fix or pinned UI-only deployment archive.

UI-only deployment session30270 completed exit0, HTTP/device200. Archive SHA256
7005867bf445a89b7622097aa64b3222e1df87b52724e0c674b3879f25ed6f45,
114 dist files; package.json/package-lock unchanged. Installer verifies installed
dist equals frozen r3, locks observed before and after stopping only the UI,
and retains /opt/sage-lantern/fleet-admin/dist.before-unlock-fix-20260906.
No backend, credential, PIN, vault or identity changes. Recovery is manual if a
future rollback is needed; do not rerun this one-shot installer blindly.
Checker /private/tmp/lantern-unlock-ui-fix-20260906.py SHA256
3637db8ae49e19eda2438aefee25d262433fd346b18ac3562a30140818de2e4a.

Session92568 verified dedicated browser334465 and launcher334384 by UID, exact
launcher argv/profile, executable, stable process identities and positive locked
state, then sent SIGTERM through its pidfd for the kiosk launcher to refresh it.
Browser profile files are preserved. Physical successful unlock/name setup remains
user acceptance; HTTP200 and a browser restart do not prove it. Current UI is r3
plus this hotfix, not byte-identical to frozen r3/r4 anymore; future release/update
gates must account for this explicit baseline.

### Reconnecting after successful unlock

User physically confirmed unlock now succeeds. The next home screen reported
Reconnecting. This is a distinct status-service failure, not proof of Wi-Fi loss
or merely the known inactive voice service. Root journal session25173 shows
authenticated GET/status reaches collect_status and crashes in _temperature:
NameError: glob is not defined. Auth enforcement remains intact (anonymous
status_http403, active status service, zero automatic restarts).

Parent restored the missing import and added a regression executing collect_status
and the real temperature/display/touch collectors against absent sensor fixtures.
All 28 status tests plus22 subtests pass. A pinned one-file installer proves the
only source delta is import glob, retains the old helper, replaces the file and
restarts only status. Session74324 completed exit0. New helper SHA256
e7c7d89bcb3f274e3c7fddbd04a707a658b644484a9e4796cf3f20ee3d9bc49e;
previous frozen-r3 helper8cbe2a6dd84a5730f67257a9dfefbd84ac6e003ebd8d36914acfc29ca4bdd51a.
Installer /private/tmp/lantern-install-status-glob-20260906.py SHA256
796c19ff35a27ccc1c6f5905761a4454bf8159d9fc8ceb498ebf90df2a5b41cf.
No auth, private data or UI changes in this second hotfix. Existing browser polls
automatically; no screen refresh or passcode reset is required. Actual voice/audio
readiness is still unverified/incomplete and must not be relabeled ready.

### Display native compatibility check

Added the new supervisor to canonical helper selection. Combined source tests:
130 passed,133 subtests. Native metadata-only check session21815 failed closed;
diagnostic1571 locates exact logind property-schema rejection after successful
installed binding validation. Native28744 proves this Debian loginctl emits no
fields for a comma-separated --property list, with or without --all. Native82543
using repeated -p returns all12 expected fields, including blank Display. This
must be fixed at invocation, not by weakening schema validation or selecting :0.
No display power/sleep activation occurred. Supervisor performance/deadline and
sleep-owned session integration gaps remain under active implementation.

### Independent Unit B camera binding

Current source install-camera-local.sh now defaults to a no-action unit preview
and requires --device with the unit's reviewed stable /dev/v4l/by-id path.
Explicit --install requires fresh absent camera service/files, actual character
device, and a correctly provisioned mandatory lock marker with denied anonymous
access before installing/starting. It preserves existing camera confinement and
passes the selected device in ExecStart instead of relying on A's default serial.
This is an executable fresh activation handoff, not merely a plan, but was not
run on A's existing camera installation or unavailable B hardware. Combined
camera/server/staging suites:58 passed,170 subtests; shell syntax passes.
Two plan tests exercise distinct A/B serials and reject missing/unsafe bindings.
These tests do not prove root installation, hardware capture or vision inference.

Further source integration gap identified: _audio always reports capture_unverified
while deviceView still gates on the legacy detail substring 'bridge ready'. Real
voice activation must feed explicit bound capture evidence into status and UI;
changing that text or treating USB enumeration as capture proof would be wrong.

### Explicit audio readiness and native display binding

Parent source now gates both touchscreen and admin audio readiness on literal
audio.capture_verified=true, not a descriptive 'bridge ready' substring. The
parser rejects non-boolean values; omitted/false remains unavailable. All113 UI
tests and production build pass. Backend still reports false until real bound
capture evidence is implemented; no evidence was fabricated. This UI revision
is not deployed, and differs from the installed unlock-only hotfix build.

Revised supervisor38799501cf107dec11008ebc9a26fb42f178fe1a9054fa92c5070d70b02dacd4
passes native metadata check session81026: verified=true, activated=false,
unit_id=unit-a, session_id=92, display=:1, launcher_pid334384. Repeated -p plus
--all fixes the Debian logind compatibility issue without relaxed validation.
Supervisor now has slower full reconciliation, cheap pinned checks, bounded
verification, lease-expiry group termination and failure backoff. Metadata
success does not exercise Xauthority memfd access, privilege dropping or X11
queries. A separate non-blanking native X11 check is being implemented before
any power activation. No display approval marker or enabled service was created.

### Native X11 probe and display control activation

Subsequent source supervisor3a1caace513f2121d3e17080ac9aaa8966c9ffae69e9da10d45a50a104d39c9b
adds a bounded non-blanking --check-x11 mode. Parent focused tests145 passed plus55
subtests. Install-only session30847 completed exit0: exact helpers/service copied
from a root-owned verified stage, no activation performed, then native X11 idle
query passed on display:1 with sealed memory-only authority and UID privilege drop.
No Xauthority data left the device and no screen-off command was issued by the
probe. systemd-analyze emitted an unrelated warning that vendor rkaiq_3A.service
is world-writable; no vendor file was changed.

Activation session25733 then verified the exact installed hashes, confirmed owner
passcode/recovery setup, reran --check-x11 successfully, wrote the exclusive
root-owned policy 'fresh unit-a' and started display-power only. Boot enablement
is unchanged; deep sleep remains untouched/inactive. Physical screen-off/touch
wake acceptance is still required. Activation helper SHA256
fb92510d32d5d0e062857c1dfdab230a254a440e9aa6324cbab22e1458b6d5a5,
/private/tmp/lantern-activate-display-20260906.py. Install checker SHA256
336619b104fa192fd39c4d0ce0f1da4a32df0492bb01b8c3b9ec8d91f944e80b,
/private/tmp/lantern-install-display-check-20260906.py. Do not rerun either fresh
checker over its now-existing stage/installed files/policy.

### Voice activation protocol integration

Source adds guarded deployment completion/cold-start primitives. Parent connected
mesh_daemon's verify-deployment-active and verify-deployment-completion operations
to their real retained-supervisor handlers, forwarding kernel peer UID unchanged.
Socket tests cover both retained-proof operations and root-only active evidence,
including changed producer/storage epoch and malformed input rejection.76 focused
tests pass. These are injected backend tests, not native voice startup or capture.
New runtime bytes are not built/deployed. Independent review remains pending;
legacy PROVISIONED-based mesh commissioning still needs a proper fresh receipt
path. User's Audio is not ready now reflects this unfinished activation, not the
previous crashing status endpoint or a proved microphone hardware defect.

### Audio-unavailable investigation after owner unlock

Native session33311 confirms the bound ESP32 audio USB device is enumerated,
but voice-loop, mesh-runtime and LLM are all inactive/dead with MainPID0 and
NRestarts0. Port3197 refuses connection. This is unfinished fresh startup,
not evidence of microphone capture failure. Session79263 confirms missing mesh
configuration and LOCAL_VOICE_READY prerequisites; service account UID994.
No audio was captured and no service was activated during these checks.

Fresh mesh preparation helper passes71 local tests. Staging-only native attempt
3555 refused before live storage/USB observation, retaining the stage at
/var/lib/sage-lantern-fresh-mesh-stage-20260906. Do not rerun its exclusive
staging wrapper. Read-only diagnostic99462 identifies commissioning output hash
validation as the failure. Hash-only observation75461 confirms only sage.yaml
differs: current6e32135f1368401a46b7f78c43beee92ae626e086058037a88fe9d8003192859
versus commissionedda3f6dd76e5572ece62db346e5a6650d0278e29b56ff62fb20a7cab807622c69.
Runtime TOML and both public identity/binding documents remain byte-identical.
Owner setup legitimately saves encryption configuration; current configuration
still needs semantic validation, not merely a new hash or bypass. Lovelace owns
a helper-only post-setup validation correction; original app-install receipt
validation remains unchanged. Curie owns explicit fresh audio acceptance work.

Independent review blocks warm completion: hardcoded display:0 and deployment
fence ordering do not prove surfaces restored, and failure markers do not prove
resumed services drained. Fresh cold startup is the relevant path for A now.
Cold bootstrap source now cancels on verification timeout/SIGINT/SIGTERM,
stops broker before voice, verifies empty cgroups and records containment status.
Parent92 deployment-focused tests pass. This process-owned cleanup does not
survive SIGKILL and is not a durable startup lease. None of these runtime changes
are deployed; voice, capture, inference and RF acceptance remain incomplete.

### Post-onboarding policy validation and capture admission follow-up

Read-only native private-config validator33660 passed exit0 as sage-lantern,
with the installed v27 binary and exact loopback-only service environment.
This proves its configuration checks, not microphone/inference readiness.

Fresh mesh helper now separates original immutable receipt provenance from
explicitly pinned current encrypted configuration, genesis chain and reviewed
policy, preserving original app-install validation unchanged. Parent114 focused
mesh-preparation tests pass. Before this revision,278 deployment/preparation/
status tests and22 subtests passed; do not combine these as a full release gate.

New read-only public-policy inspector passes25 parent tests. Native11842 verified
its SHA5b5519b5242aaba2d4d49674cf9c6065e6da5e17127719dc7d5b2c4b03746e3c,
but the installed venv lacks PyYAML; inspection stopped at import before reading
configuration. The new preparation helper has the same dependency. Add a pinned
offline dependency to provisioning rather than silently mixing host packages.
No policy JSON was emitted and no service/configuration was changed.

Audio commissioning must not make normal boot/wake require another microphone
test after ten minutes. Parent review caught this in draft ExecCondition logic:
short-lived capture_verified is diagnostic evidence, not permanent operational
eligibility. Curie is separating stable commissioning from live hardware/runtime
availability; parent UI's undeployed capture_verified gating must follow that
distinction. Existing lock/session/broker capture authorization stays mandatory.
No microphone recording, new voice activation or runtime wheel deployment yet.

### Offline dependency installed; storage lifecycle mismatch identified

Native68832 installed only PyYAML6.0.2 into the existing venv, using the verified
ARM64 CPython311 wheel SHA256
5d225db5a45f21e78dd9358e58a98702a0302f2659a3c6cd320564b75b86f47c.
Installer8ad6d944b3d85f5d90f20530df8fc3a8a5fa9bc27adf19d38b0f6d8ad0dc5a79
checked voice/broker/LLM inactive before/after and native isolated import passed.
Stage /var/lib/sage-lantern-pyyaml-stage-20260906 is retained; no rerun. No services
were started/restarted. Canonical source now declares/requires/verifies this
dependency; stack lock and runtime wheel have not yet been regenerated.

Public policy projection46543 confirms encryption=true, hash768, private media
20/40GiB and250k/500k quotas with10GiB free minimum, fixed loopback listeners,
federation/quorum disabled, ordinary Companion clearance1 and voter.required=true,
poll_interval=2s. Helper7139fa5f459465dbcfd34dba13f5ab3934b25c3392b353c0d983a9429e5cea52
accepts this reviewed post-setup policy; parent147 preparation tests pass.
New staging attempt48313 refused and preserves
/var/lib/sage-lantern-fresh-mesh-stage-r2-20260906. Read-only7391 proves inputs pass
and identifies live MessageStorageProbe as the remaining refusal.

Signed flags-only native3252 reports encryption_expected=false, vault_active=true,
stable=true, canonical_send_idempotency=true, encrypted_send_admission=true.
Do not weaken the probe or substitute false/zero storage observations. Frozen v27
node.go sets SetVaultExpected(true) only at encrypted startup; owner setup saves
encryption=true but its callback does not update the store expectation. Lovelace
is implementing an isolated v27 lifecycle fix retaining the previous lineage
marker correction; no native rebuild/restart or dirty v28 activation yet.

Parent source now routes verify-audio-operational to the real broker handler,
and reports audio.ready independently from capture_verified. Touchscreen/admin
readiness uses ready, so expiring diagnostic capture evidence cannot disable
normal Ask. Missing/legacy IPC fails closed; capture_verified remains explicitly
false in status until separately exposed as diagnostic evidence.114 UI tests and
production build pass;63 routing/status tests plus25 subtests pass. These UI,
status and runtime changes are not deployed. No microphone capture has occurred.

### Frozen publication fix and authenticated voice readiness build

Isolated /private/tmp/lantern-fresh-v27-vault-publication-20260906 changes only
SetVault publication plus two regression test files relative to marker-fixed
v27. Non-nil vault publication now latches expected=true under the existing
publication mutex before publishing its pointer; detach preserves expectation.
Offline cached Go1.25.13 Linux tests pass:4 focused tests plus2 subtests, and
broader store/web/node selections including lineage-marker tests. Race detector
was not run. Parent promoted the same3-line fix and tests into the sibling SAGE
worktree, preserving other dirty changes; those dirty sources were NOT built.

Two offline Go1.26 ARM64 builds compare identical (binary and manifests):
/private/tmp/lantern-sage-vault-publication-20260906-{a,b}
binary SHAebf522a8cbc1fc26df8aeabc72f9a82dfc8023dd274524e7f2689f4771c55137,
inventoryc600ed34ee860a617135afcc60e21bf8e4a791889d722e66c3f381d173af7d6c,
version lantern-local.20260906-fresh-v27-vault-publication. Neither deployed.

Voice readiness now uses a nonce-bound acknowledgement on the existing root
Unix socket with kernel PID/UID and live broker/storage/producer identity checks,
not sibling-UID proc FD reads or unauthenticated TCP health. Parent daemon owns
the acknowledgement cache; voice runs a separate periodic worker after binding
its listener, and drain joins that worker before claiming HTTP drained. This is
diagnostic only, not a capture grant. Invalid renewal cannot extend freshness.
No new broker capabilities were added. Parent244 integration tests plus70
subtests pass; independent review reports159 tests plus11 subtests. Native
latency and post-wake behaviour remain unverified.

Runtime independent builds compare identical:
/private/tmp/lantern-runtime-voice-startup-20260906-{a,b}/sage_lantern_runtime-0.2.0-py3-none-any.whl
SHA163a219664f8b60f72d4a1905cac965d67adf11a0b657a4ece40cbbb6f5a3d78,
source manifest7d6493610547672187054ce6c6fbfc3dfce1455391a01ebc422ed6848f89fbff.
Parent release/stager tests40 passed,1 skipped,201 subtests. New support helpers
are included in staging and PyYAML metadata is declared in lock generation.
R5 artifact-root assembly is assigned to Lovelace; no final canonical release
or native update is yet verified. Unit A still runs the older SAGE/runtime.

### Canonical R5 package and live restart preflight

Parent canonical staging89116 completed:
/private/tmp/lantern-fresh-canonical-20260906-r5,31340 files,
release lantern-fresh-v27-20260906-r5, manifest SHA256
388617d2d7aaa3ba78ac20fcabbd61757df27c4c3de046149c54925ae7a30001.
Input artifact-root r5 differs from r4 only in native binary/build manifest,
runtime wheel, added pinned PyYAML wheel and stack lock. Artifact stack-lock
SHA0aaf8c1bcbaee39ff7cb0b14bfa369ebf61b372ea333addc391f7021a78cba28.
Frozen canonical gates are assigned for independent verification. This is a
package, not deployment/signature/physical acceptance evidence.

Live1331 revalidates authPID334080 invocation9156c9a5b3cc4cf7a6c7be9abc103400,
private SAGE PID334079 invocationf1875ca2c33e44fd89bfa8c49c7de511;
voice/broker/LLM inactive PID0; guard reports locked=true. Authoritative native
upgrade status reports chain app-v27, binary maximum27, no pending plan or active
ballot. This is not proof of zero application queues or signer-fence clearance.

Do not substitute external SIGTERM of SAGE for its coordinated restart. The
frozen native updater prepares a reversible signing drain, rechecks signer fences
and creates a version-transition recovery snapshot. Local exact-Root/Admin signed
requests are supported operator authority even when the vault is encrypted;
browser-header spoofing, cookie extraction and unsigned fallback are unnecessary
and forbidden. Curie is implementing a narrow on-device signer for the exact
restart route, separate from unchanged vault-only RootFileSigner allowlisting.
Lovelace owns bounded cold-update preflight/source tests; no target mutation yet.
Installed auth service generation adds --drain-report-dir even though its base
unit template omits it; verify actual invocation/report before using that fact.

### Narrow restart authorization helper reviewed

scripts/lantern/request-fresh-sage-restart.py SHA256
4931d2f10a98b69f668b46b471eeb8fb3f84efd0d467ca2349db76cbd976327f
is source/test only. It pins current public Root identity, boot/unit and the
existing service-owned private-key descriptor, drops privileges before SDK key
loading, and signs only empty POST to the fixed loopback coordinated restart
route. No cookies, proxies, redirects, returned signatures or automatic retries.
Its exclusive intent survives ambiguous outcomes. Parent28 tests pass after
correcting real receipt mode0600 versus unit metadata0644. It reports202 request
acceptance only, never successful restart. It must not run without cold-update
containment and a reviewed binary transition.

Linux's frozen update_helper_other.go is a no-op: do not claim external automatic
rollback exists on A. The coordinated native handler still provides signer drain
and verified pre-transition snapshot; preserve old binary and treat any recovery
as a separate reviewed on-device operation. No helper invocation or binary swap
has occurred. Vault-only RootFileSigner allowlisting remains unchanged.

### Cold-update observation: unresolved prior auth instances

Native read-only session48001 confirms the same boot and active auth/SAGE
invocations recorded above. Voice, broker and LLM remain inactive; idle-suspend
is not installed. Installed auth arguments include the drain-report directory.
Two prior auth reports, 8074b590c6d24e298381d6ebe9d2b9fc and
9e0832e50e004fef9c2158eac6b76825, still report closing/drained/local_revoked
false and upstream_cleanup unknown. These are not successful shutdown receipts.
Do not delete, overwrite, or treat them as zero-queue evidence. The current auth
report is likewise open, as expected for its live invocation. A safe recovery
path for the prior unknown outcomes must be reviewed before native mutation.
No restart, binary replacement, runtime replacement or audio capture occurred.

The cold preflight helper must distinguish the genuinely absent idle service
from a loaded inactive service while still checking its canonical cgroup/jobs.
No installed native build manifest was found under /opt/sage-lantern or
/etc/sage-lantern; stage the original frozen R3 manifest rather than synthesizing
one from the running binary. Independent R5 verification was reported passed,
but its unchanged UI comparison is under investigation: the current UI source
contains operational audio.ready changes not present in the original R3 UI.
Do not assume frozen artifact closure proves current UI source inclusion.

### Corrected R6 UI package and native recovery review

R5 reused the original R3 UI dist. Parent assembled a new immutable R6 artifact
root, replacing only dist from the current tested build (70 changed paths,
114 UI files); the full R5 base inventory remained unchanged. Canonical R6
staging79632 and manifest verification87741 passed, 31,340 files, manifest
6dd0b682028b10072b6e249582d937697a21c492581c0ff26634082097af5c13.
Independent frozen profile/input/source/runtime gates and exact current UI dist
comparison passed. Audit:
/private/tmp/lantern-fresh-canonical-20260906-r6-verification.json
SHA cdf4b59189516a949a6ca351750e4ba68573eb16c7388cabd7838ecd9966dd9d.
Neither R5 nor R6 is deployed. Native maintenance helpers are separate from
these frozen packages. Parent121 maintenance-helper tests pass at this point.

Reviewed recovery decision: dashboard cookies exist only in the native handler's
in-memory session map; coordinated Unix restart uses exec, preserving PID but
replacing memory and generating a fresh application BootID. Verified old auth
process death plus completed native image replacement can prove old credentials
unusable NOW, not that historical cleanup succeeded. Current strict preflight
and sleep receipt consumers correctly refuse the unknown reports. Separate
capability-supersession evidence is required; preserve the original reports.
Curie owns a source-only explicit recovery driver; Lovelace owns the separate
evidence consumer/preflight contract. Completion must verify image/BootID,
listener ownership and encrypted locked state, not merely restart acceptance.
No target mutation has occurred and audio is not ready for physical testing.

### Native candidate staged on A, not installed

Native staging70350 ran the root-copied, hash-verified helper
/private/tmp/lantern-stage-native-r6-20260906.py
SHA b8cc1170846669296c4dd2e3d8b61984e03d3f2efcb923efcf6817fecad6394c.
Target /var/lib/sage-lantern-native-candidate-r6-20260906 now contains verified
sage-gui (candidate), sage-gui-before (exact installed R3 backup), manifest-new.json,
manifest-old.json and a staging-only receipt. Original manifest SHA
ea863577ebcd31b6130301f575d68003bb708ba66100c2d39e62b444908dbaa4.
The original active binary hash was checked before and after copying. No active
binary replacement, service change, key access or restart occurred. Temporary
upload directory /tmp/lantern-native-candidate-r6-20260906 remains; the executed
root-owned script is under /run/lantern-native-stage-r6-20260906. Do not rerun
this exclusive staging operation; use its retained verified files.

### Live recovery preflight and application-version distinction

Native98578 pins SAGE start_ticks2860992 and auth start_ticks2860995, preserving
the same boot/PIDs/invocations. Auth executes /usr/bin/python3.11 through the
configured venv path; installed argv includes the expected drain directory.
Parent public expected context:
/private/tmp/lantern-cold-native-expected-r6-20260906.json
SHA a69cd0a3e4ee0d101efd477c2e2f69fce60889c95e264537c7b2b4d09952598c.
Native83817 staged root-owned helper copies and expected.json alongside the
candidate. Supersession helper SHA60d7ac52341f7c3e724f18ca54ac9f68817be15ad0ae6bd1e0f00750baa07cf7,
preflight SHAb9f5d86e86177d58a70c5977a686d5ee2726bdfdc376d38591a3172ca99a7c9e,
restart helper remains4931d2f10a98b69f668b46b471eeb8fb3f84efd0d467ca2349db76cbd976327f.
Parent144 helper tests pass. Read-only recovery entry refused; no mutation.

Traceback5632 isolates the refusal to the governance version comparison, after
all preceding pinned-file/service/report checks. Native84802 reports correct
chain, catching_up=false, but node_info.protocol_version.app="23". Direct
ABCI Info4718 reports app_version="27", height818; governance status also27.
Frozen SAGE Info returns currentAppVersion; Comet's makeNodeInfo captures
state.Version.Consensus.App when constructing peer metadata. The preflight must
check authoritative ABCI Info plus governed version, not equate startup peer
metadata with current governed version. Source/test correction is assigned to
Lovelace with coordinated driver-call changes; existing staged preflight remains
unchanged and has not authorized a restart.

### Native transition20086 stopped before signed request

After the user explicitly locked A, corrected read-only recovery preflight41945
passed. Parent244 combined maintenance tests passed; independent source review
found no deterministic blocker. Driver startup polling was narrowed to only
connection refusal/no listener, preserving failures for conflicting evidence.
Driver4bc7ee3ccad5d8dee56fd86f2b3e1403d34a2bfc0450676c49b79dc080dfe9ab
was staged as update-fresh-native-r3.py and invoked once in session20086.

The invocation exited2. Read-only92406 establishes: durable journal contains
inhibit_starts_done, drain_auth_done, swap_native_done, then request_restart_intent.
NO /run/sage-lantern-native-restart-request.jsonl exists. Current auth is inactive
and its 9156c9a5b3cc4cf7a6c7be9abc103400 report now positively confirms all drain
fields. Start guards remain installed. Active pathname /opt/sage-lantern/bin/sage-gui
has new ebf522... bytes, but PID334079 is still running the OLD deleted inode;
no completed native restart or capability-supersession evidence exists yet.
Do not rerun the full driver or discard its guards/journal.

Metadata-only24443 identifies the actual refusal: /etc/sage-lantern/unit.env is
root:993 mode0640, whereas the signed helper required0644. Its content hash is
correct. Initializer receipt remains root0600; Root key remains994:9930600,
32 bytes; no key content was read. scripts/provision-lantern-unit.sh:177 explicitly
creates unit.env0640. Fix the helper's valid-mode handling, not file permissions.
Curie owns this correction and a separately pinned checkpoint-resume path;
historical receipts, original expected context and stopped transition remain
unchanged. The user-facing auth service remains inhibited pending completion.

### Native recovery and runtime replacement verified on A

Frozen corrected restart helper SHAae5e10c5f7ab27a9e79c80f3cb2b343940adeb81f2fb5b8f2b93de74696cdb13
accepts legitimate root:service-group0640 metadata without loosening key modes.
Driver2b80aca6c876fe5beb97fbf5e60e0206a5bd7c0a603a2602dd09922b45050ad0
adds a separately pinned resume inspector/request, preserving original journal.
Parent271 tests pass. Initial local resume-input upload was cancelled before
SSH authentication when a source hash changed; no stale inputs were deployed.
Actual root-staged resume-checkpoint-r2.json hash
c6badf77711b76a08e62ec90b257f0b2c5ab725632d9ddfd75fdfa2d527ffeef
binds independently observed journal e33ee1d0be3c01c43c400355370dc639964073cffcb500ec4cfcfd819d172ee0
and positive current-auth report2cf5b1fa66563e74583d63b9e870dc020fe153be6575f39b4356d938f3ab136d.

Read-only resume8614 passed; explicit one-shot resume74045 succeeded. New native
ebf522... is RUNNING, PID334079/start2860992/invocationf1875... preserved by exec;
dashboard BootID changed669a628b-b954-45ea-b74f-99fe8c79181a to
e199733a-52e9-4dac-a74d-5fb7ee364c50. Owned listener inode1550117,
encrypted=true, vault_locked=true. Root capability-supersession evidence:
/var/lib/sage-lantern-cold-native-update/capability-supersession.json
SHA2a2cb07b3f00f6ecb277eb2bbeebb17f3fd0022ff799431935aa6d763546b075.
Historical unknown auth reports remain unchanged, not rewritten as drained.

Runtime candidate is root-staged at
/var/lib/sage-lantern-runtime-candidate-r6-20260906. Expected public context
SHA417f061c4513287aa50fc39354cb7e239c890889bafda214b74deaca0b0569ff
binds camera UID63957/PID334912, display root/PID342106, status UID994/PID337174
and their actual invocations/helpers/interpreters. Runtime helper
202f71b7ea8f213f3eaf922fb51b6f23a3c280872b48f5605c4418f8a284160e:
34 focused tests; native preflight35377 passed; explicit apply88975 succeeded.
New runtime wheel163a2196... is installed and byte-verified. Old wheel preserved.
Camera/display/status are now stopped with separate runtime-maintenance guards;
auth/broker/voice/LLM remain inhibited. Native SAGE was not changed by runtime
installation. Voice/status R6 scripts are staged but not installed, UI still old,
audio not commissioned and no physical acceptance yet. Do not rerun completed
native or runtime one-shot operations. Next is helper install/basic-service
restoration, owner unlock, fresh mesh/audio activation and R6 UI deployment.

### R7 microphone commissioning correction verified installed

The commissioning check previously required the LLM to be active, while the
LLM required the successful microphone marker: a startup dependency cycle.
R7 allows only a verified inactive, empty-cgroup LLM during commissioning;
normal operational admission still requires the active model. No readiness
marker or capture evidence is fabricated. Parent audio/listener tests: 96;
separate focused basic-restore tests: 46.

After the previous terminal session expired, read-only native inspection29696
confirmed all eight R7 update journal phases, including verify_runtime_done.
Installed wheel SHA6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440;
expected SHAfc40c316cb5fecf1fd9ed30f5398ea8b73b85a88267f15ff952105b4b9bb47ff.
Auth/status/camera/display remain inactive with maintenance guards. No retry
of the completed updater is authorized. Actual service definitions and helper
hashes were collected in read-only33166 for the separate basic-service restore.
Basic restore helper revision
5e463d96ec64864d4b5e752c3d7726a5714f2a8e147697ac154d4da37a00f322
handles actual systemd ExecStart lifecycle metadata and absent idle-unit fields;
63 focused restore tests passed. Native read-only30642 passed with expected
SHA8037ee0fc11e6507d3008e378edd4ae92a0ce1db4877d65865357a898e9adbc0.
Explicit apply93831 stopped at 09-start-auth intent. Both voice/status helper
replacements and auth guard removal completed and were journaled. Auth is active
PID420488/start4313493/invocationfd453341662647c2b4d5bfefce7e761b. Native SAGE
was not restarted. Status/camera/display retain both runtime guards and remain
inactive. Preserve /var/lib/sage-lantern-basic-restore and its backups; do not
rerun the original one-shot apply.

Read-only4815 reproduced the locked-state check refusal at line369. Native17713
confirmed auth owns listener inode1568684 via fd7, with only its own PID in the
cgroup, but /state returned locked=false. An earlier check had returned true.
The refusal therefore protects against continuing while unlocked; it is not
evidence of a microphone failure or established socket-ownership defect. Asked
the owner to lock again and leave locked. A separately verified checkpoint
resume is required for remaining basic services; no automatic retry or auth
restart. Model/microphone/RF activation remains unverified. Requested explicit
consent for a local two-second microphone check without audio retention.

Read-only66131 subsequently confirmed the same auth PID/invocation and both
auth locked=false and native vault_locked=false. No further target mutation.
Separate resume source scripts/lantern/resume-fresh-basic-services.py
SHA40654d0cb289fa9f4562ea0a113d92ffc949aa63246fd171c3a740fab7fb94fd
retains and verifies the exact original nine-phase journal and helper backups,
adopts the existing auth identity only if locked, and restores only remaining
basic services. It cannot reinstall helpers, restart auth or activate voice.
The listener check compares stable socket identity with repeated ownership and
process checks, not mutable TCP bookkeeping. Parent restore/resume tests: 97;
agent combined focused tests: 402. These are source evidence, not deployment.
Public checkpoint /private/tmp/lantern-basic-restore-resume-checkpoint-20260906.json
SHA2ddfbbc8b80a763025fb28529882506e9364f38f5815c8629f75a63caecabccd
pins actual journal hashes and the existing auth process. Next: owner lock,
native read-only resume inspection, then separate authorized apply. Resume is
not yet staged or executed at that checkpoint; recovery remains incomplete.

Subsequent native78806 confirmed screen locked. Staged resume40654d... under
/var/lib/sage-lantern-basic-resume-candidate-20260906; readonly67340 refused,
without creating resume state. Diagnostic29352 located the rejection at the
original restore native health vault-locked predicate. Narrow native96102 proves
screen_locked=true, vault_locked=false, encrypted=true, unchanged expected
native version/BootID. Source confirms Menu Lock and session expiry revoke
sessions but do not unload SAGE's serving-vault keys. Asking the owner to lock
again cannot satisfy that different cryptographic predicate.

Review now targets an explicitly key-resident BASIC-only continuation: preserve
historical capability-supersession evidence, verify unchanged native identity,
encryption, locked auth session and all cold producer guards. It must not claim
vault keys unloaded or authorize audio/radio/model activation. Full cryptographic
lock/suspend lifecycle remains an explicit unfinished acceptance requirement.
No native restart, session fabrication or security-state spoofing is permitted.

Key-resident resume revision b1ee5d0873a1bf0b930bac3cc6f42cdba7abea68e46c9c8d60475baa3a2f4ab6
and checkpoint7ca46c5ff069cc9cb19783ce6f3564db737fa633d96988cb5097b3ba5833022f
passed read-only99674. Parent restore/resume tests119 passed. Explicit apply12246
stopped at resume phase07-start-status intent. Native15930 confirms status active
PID421600/invocation334ea8676e85458bb3dd6033cfe4bc47, both status runtime guards
removed; auth still locked with original PID. Camera/display remain inactive
with both guards. Preserve /var/lib/sage-lantern-basic-restore-resume; no retry.
The failed predicate is not yet established. A source-guided read-only diagnostic
must inspect it before any further recovery mutation.

Read-only diagnostic25569 verified all retained resume journal bytes and the
live status PID421600/start4427252/UID994/invocation334ea8676e85458bb3dd6033cfe4bc47,
exact executable/argv/inode/cgroup, and full reconstructed current-state checks.
All passed. Therefore no persistent current-state mismatch was reproduced;
the original failure remains a transient of unknown precise predicate, not a
proven Type=simple race. Camera/display are still not restored. Any final
continuation must adopt—not restart—existing auth/status, preserve both failed
journals, and bound service startup settling by unchanged PID/invocation and
repeated actual checks. No audio, inference, RF or cryptographic-lock acceptance.

Final camera/display helper80c14d9dd6cdcad0e713a97a8ba3f2973baa239fb5f557beb82d802f61d0b926
with checkpoint52f200c403dd28bf4c019486aa1f72b721b3cd65705f076f834fac4699775a03
passed native read-only94486; parent focused restore/resume/finish tests143 pass.
Explicit apply46354 again stopped after start intent, this time for camera.
Native72203 confirms camera active PID422122/invocation39d6dc243b614eea814828c92b4cfee2
with its two runtime guards removed. Display remains inactive and guarded.
Preserve /var/lib/sage-lantern-basic-restore-finish and seven phase records;
do not retry. The repeated post-start checker rejection is not yet explained.
Read-only camera checkpoint diagnosis and dataless failure-frame reporting are
being prepared before any final display-only recovery. Camera service activity
does not prove camera capture, image quality, photo answers, or microphone use.

Native11177 establishes a concrete camera validation defect: Python's
pwd.getpwnam('sage-lantern-camera') raises KeyError. Native85534 independently
confirms systemd DynamicUser=yes, literal User=sage-lantern-camera, active
PID422122/start4506814/UID63957/invocation39d6dc243b614eea814828c92b4cfee2.
The frozen restore process check unconditionally uses that missing NSS lookup.
This explains a camera validation failure, not the earlier status transient.
Use separately pinned kernel UID plus the exact DynamicUser unit, process,
invocation, cgroup, executable/argv/inode checks; do not substitute an arbitrary
UID or generalize a fallback to ordinary services. Captured public expected
input: /private/tmp/lantern-camera-checkpoint-expected-20260906.json. Read-only
diagnostic correction and final display restoration were pending at that point.

### Basic recovery and local UI deployment completed

Corrected camera diagnostic8dea60b4ac35efd5389e7cbc9d55afb250eb964370698c7738ea4fc248a25644
and final display helper7cbbe6367bc5a2bd59eaefb32d57a2cba4bfc2d55c4fa32f9401fe7062b362da
passed native read-only71641, including real camera DynamicUser/process and full
current checks. Parent focused diagnostic/display tests21 pass. Explicit apply
24093 completed exit0 with start_display_done and display_restored=true. Auth,
status and camera were not restarted, and native SAGE was unchanged. Preserve
/var/lib/sage-lantern-display-restore and all earlier partial recovery journals.
These prove basic-service restoration, not microphone/vision/RF acceptance.

Local-only UI deployment76400 completed exit0, HTTP3100/device200. Installer
e4fd8d2489a9ddf220d382c81168bff7098f456bad7c8ed7a06afa258d987690 verified exact
previous dist/dependency metadata and payload1306c1956b84bb51086a895f129649d27520df3f6552562957ef50cb1d1b6107.
Backup retained at /opt/sage-lantern/fleet-admin/dist.before-runtime-ui-r6-20260906.
No cloud publication, key reset, auth restart or private data export. Physical
kiosk refresh/unlock is not yet verified. Runtime remains R7, UI now reviewed R6;
the next canonical B release must incorporate this exact source/dependency set.

Voice/model/mesh still inhibited. Fresh mesh configuration, final audio/vision
inputs and real consented microphone commissioning remain next prerequisites.
No owner reply granting the two-second microphone check has arrived yet.

### Fresh mesh inputs verified, not installed

Native preparation7075 completed exit0 using a new exclusive stage
/var/lib/sage-lantern-fresh-mesh-stage-r3-20260906. Wrapper2dd0c745ad409289c713ca0cd17b74e5e936fe76f6842b932c030a3e2c8e231e
differs from the prior reviewed wrapper only by stage name and current native
SHAebf522...; all other public input pins remain verified. The preparation
performed signed on-device storage metadata observations and USB inspection;
no keys were exported, records read, microphone captured or radio opened.

Inspection92818 verifies PREPARED.json SHAda4b2b0442c12294d5d0eb8b4e9011d42c765290fb61c64f5921fd15a40af132,
mesh-runtime.json SHA24b6d9a17a25690f69cc23ca544a4f4afbc7a3fc21ae0cce59da82e8fe1241d8,
20-radio.conf SHA493cfd70e3b419143605fa26a928c278f43ac9eaaa4ae4f6bbdc1354ef40d2fa.
Receipt reports private_identity_verified/storage_verified/usb_binding_verified
true and installed/activated/ready/radio_handshake_verified/capture_verified
false. Broker is inactive, PID0, with its native-maintenance guard retained.
Separate reviewed installation remained required at that point; this is not
LoRa acceptance.

Mesh installer70dbd3a3d25f33049790d2536f01948959949e0dc93b7ec54c1e984c00e00e3e
with expected inpute730cc20bf98912fe95be9123ff61acbea8b360852d15150718d55a7f1443a03
passed native preflight86110. Explicit apply64779 completed exit0: installed=true,
activated=false, ready=false, services_started=false. Root0600 mesh-runtime.json
and root0644 broker20-radio.conf retain the exact prepared hashes. Exclusive
journal /var/lib/sage-lantern-fresh-mesh-install is preserved; do not rerun.
Parent focused installer tests17 passed with the runtime source on PYTHONPATH
(the initial invocation lacked that import path and did not exercise tests).

Separate verified daemon reload96649 succeeded. Radio, voice and model remain
inactive with all native maintenance guards present. No RF, microphone capture,
fake readiness marker, native restart or private records export. Audio guard/
ALSA input installer is source-tested but not yet deployed; microphone consent
and real commissioning remain outstanding.

Audio config preflight97213 passed using frozen helperd915321be600a8ff8a24e68975e80cf14034302be2b8bc3e3118800bcc9bc644,
R7 audio sourcec280dce990c1c32c4ae4e0816750cb68ccf792c5a3b44dcf884600aa7f5c5dcc,
guardc906b930c4dfc5f77e6e6533162003efd4e20b319881a58e95ba2ff702c01f7a and
ALSA510a8af9318333cecfd7320d5f9d83c2b5cbec297d99ebfd4f45b436bffabb59.
Explicit apply8527 wrote both files but stopped before the second done record.
Native26132 independently verifies both exact file hashes, voice PID0/inactive,
and new50-audio drop-in already visible in systemd with NeedDaemonReload=no.
The installer incorrectly assumed DropInPaths would remain unchanged until an
explicit reload. Preserve the partial /var/lib/sage-lantern-audio-config-install
journal; do not rerun or invent the missing completion record.

Separate non-capturing native inspection4233 PASSED the actual R7 snapshot and
inactive voice/broker checks: 101 bound input files, expected USB/audio identity,
LLM loaded/inactive/PID0, and retained native voice guard. Preliminary input hash
7af6d7410e8bb37ac6721b9076e6f5fa0160a0e399fe9cecb8954fb4bc966e78 includes
that maintenance guard and must NOT be reused after guard removal. No recording,
commissioning or readiness marker was created. Final reviewed guard release and
fresh input inspection must precede the owner-approved two-second microphone
test. Source-only installer portability correctioncc1797f3872006ae58ae80d83d67a33d3e557dfaefaceab838d6969fb6d5da5b
now accepts exactly its own verified new voice guard appearing after the write,
while retaining all other configuration and inactive-service checks. Parent31
focused tests pass (agent102 combined). This corrected installer has not been
rerun on A; its existing files and failed-attempt journal remain untouched.

### Owner-approved microphone commissioning — 2026-09-07

Owner explicitly approved the two-second local microphone test. Native recheck
67803 confirmed the same Linux boot and inactive voice, broker and model. Scoped
operation98038 archived only the exact voice native-maintenance guard into
`/var/lib/sage-lantern-voice-guard-release-20260907`, reloaded systemd and verified
all three services remained inactive. Other native-maintenance guards remain.
Fresh final input SHA was
`eaf14237198d0468eef0ca8b840de5e58e967680b04734a9a272d01a23396f8b`.

Native capture29143 completed exit0 using that exact input binding and the
explicit microphone approval flag: audio_commissioned=true,
capture_verified=true, retained_audio=false, physical_ptt_verified=false,
inference_verified=false. The two-second PCM capture stayed on-device in memory;
only commissioning/validation metadata is retained. Capture freshness lasts600s;
durable commissioning has separate current-binding admission checks. No model,
broker or voice service was started by these operations. End-to-end Ask/PTT,
speech recognition and photo inference remain unverified on this fresh install.
Do not rerun commissioning or replace its real evidence with readiness markers.

Native model inspection38029 confirmed the exact reviewed servicef021055...,
inactive PID0 and installed root-owned Qwen3.5 4B model. Scoped startup75466
verified durable audio admission and idle sleep state, archived the model's
maintenance drop-in under `/var/lib/sage-lantern-model-start-20260907`, then
replaced only that drop-in with `[Service]` / `Restart=no`. After daemon reload,
one explicit model start completed: loopback8081 `/health` returned statusok,
normal audio admission passed, model active PID513288, invocation
ee384709808a4276ab09b8e41ab2eb20. Broker/native guards remain retained; voice
was not started. This proves model readiness, not inference or the full Ask flow.

Broker startup77900 passed exact pinned inventory preflight after archiving only
its native guard to `/var/lib/sage-lantern-broker-start-20260907`. The single
attempt failed: broker exited1 before voice startup, bootstrap timed out and
retained `/run/sage-lantern-voice-bootstrap.json` with phasefailed,
containment_verified=false, recovery_required=true. Do not rerun or clear it.
Native66648 confirms broker failed/PID0 and voice inactive/PID0. Service stderr
is null, so journal supplies no exception detail. Independent read-only2645
verifies both cgroups empty, no broker socket/start grant, valid config and key
metadata (no key contents read); supervisor state has fence.json, owner.lock,
serial. This does not rewrite the failed containment result.

Read-only75654 narrows the startup problem: vault identity and exact voice
conditions pass, but `SuspendSystem.inactive_verified(voice)` returns unknown
despite the narrower empty-cgroup inspection passing. This is the underlying
verification path used before voice start; isolate its failing check rather
than bypassing it. Audio remains unavailable on the device. Agent Lovelace is
reviewing this source path; no successful broker/voice activation is claimed.

Root cause confirmed: effective voice conditions omitted the required negated
`/run/sage-lantern-sleep.fenced` condition. Canonical voice service now includes
it; service SHA changes45339f... to
ac652a806133ed5b87e9a881034c907f79f4fad3af184f7e33e7b48596223975.
Parent21 voice-control tests plus23 subtests passed. Staging54901 and scoped
install60833 completed with prior service preserved under
`/var/lib/sage-lantern-voice-sleep-condition-20260907`. After reload the actual
`inactive_verified(voice)` now returns confirmedtrue/reasoninactive_verified.
No services started, failed journals untouched, no new recording performed.

Changing the service invalidates current audio admission. Historical capture
evidence must be preserved rather than rebound. Requested renewed owner consent
for another two-second no-retention check. Before that, separately reconcile and
archive the failed bootstrap/supervisor state and invalidated commissioning
artifacts under an explicit recovery transition. Never erase unknown outcomes.
Source preflight now checks the same backend inactive prerequisites before intent
creation; agent45 focused/129 combined tests pass. This runtime preflight change
is source-only, not deployed on A.

Recovery-boundary inspection87864 confirms supervisor phase recovery_required,
broker ID5cecd408871927451378fb1bea0e125a, owner.lock size0 and empty serial
directory. Voice systemd execution start/exit monotonic timestamps are both0,
NRestarts0, inactive/PID0. Broker timestamps58415376316/58416512927,
NRestarts0, failed/PID0. Combined with the same-boot startup receipt and empty
cgroup inspection, this supports a pre-producer recovery path, not a general
drain claim. Curie is implementing a narrowly pinned archive/recommission helper;
no recovery archive/reset or new microphone capture has occurred yet.
Parent combined regression run:66 tests and23 subtests passed.

Synthetic model checks (no microphone, SAGE reads, or personal prompt):35446
used server defaults and exhausted32 output tokens in7.44s on reasoning with
empty visible content. Existing Lantern adapter already sets
chat_template_kwargs.enable_thinking=false; matching that setting in1359
returned visible `Ready`, finish_reasonstop,2 completion tokens in1.06s.
These are short synthetic observations with differing prompt-cache hits, not
an end-to-end latency benchmark or evidence of a new optimization.

Independent offline R8 runtime builds at
`/private/tmp/lantern-runtime-recovery-20260907-r8-a` and siblingr8-b are
byte-identical: wheelbb8d83e3faa052c38d5de19722f00d6bcb578c4ced078c6d2bfaf9ef157ea7f4,
source manifest090ad96e140b21d63180fff881bd3a2b22954e67d9359860646494b58ff0569f.
Includes source bootstrap preflight and camera identity corrections. Parent
focused runtime/camera/release tests101 passed,1 skipped,126 subtests; skipped
installed-import test requires a separately provisioned validation venv.
R8 is built only, not deployed. Fresh vision planner stale service pin corrected
to ac652a80...; parent153 planner tests passed. B has not been provisioned.

Host installed-package validation now runs in the dedicated temporary venv
`/private/tmp/lantern-r8-installed-validation-20260907`. Initial checks correctly
rejected inherited SDK11.18.4, then PyYAML residing outside the venv. Installed
the reviewed SDK wheel93b9708ec81f054f6495adc17c03aab60efda77ef4163a5653f5a0632879d455
from the frozen R6 bundle and pinned host PyYAML6.0.2 into that venv. Final run
72012:22 runtime-release tests and115 subtests passed, including installed
module hashes/imports and SDK signatures. No Unit A runtime update or service
activation occurred. Host import validation does not prove ARM64 operation.

Preproducer helpera353ed02415d74dd932d0d2cf443c83ca4e00c87f80647774088ad2a5961266a
passed native read-only preflight17705 with expected input
2f65b836fe9f4db93860cbd406f34084632227d780eb52f082f09c7c5ae19ca5.
Parent34 focused tests pass. Root staging is
`/var/lib/sage-lantern-preproducer-candidate-20260907`.
Explicit apply62433 archived all six exact artifacts and reset only broker
failure state, then refused its final check because systemd cleared execution
timestamps/status/invocation through service garbage collection. Preserve the
partial journal `/var/lib/sage-lantern-preproducer-recovery`, ending at
11-reset_broker_intent.json; do not rerun or fabricate a done record.

Independent read-only10297 confirms all six archived hashes match and originals
are absent; both producer cgroups empty. Voice and model full property snapshots
unchanged. Broker differs only by inactive/dead/success, both execution timestamps
zero, ExecMainCode/Status zero and empty InvocationID. These are current-state
observations, not rewritten historical containment. Old capture evidence and
failed bootstrap/supervisor records remain intact in the archive. No new capture
or service startup occurred. Source-only coherent-reset portability correction
requested; renewed microphone consent remained outstanding at that checkpoint.

Independent native61283 passes fresh bootstrap prerequisites, including the
formerly missing backend guard check, no residual live commissioning/startup
artifacts, and new input SHA
1b0c26b5f83681b719e8a859ffe26b516235b09cd63e8d7bceba9c683f7846d9.
Owner then explicitly approved microphone recheck. Native14109 passed the actual
two-second capture against that hash: commissioned/verified true, no retained
audio, physical PTT/inference unverified. Old receipts remain archived.

New broker-owned startup83030 passes both preflights but voice now reaches
ExecStart and crashes at initialization. Native34416 journal identifies
`ConversationService.from_config` constructing Path(config.conversation_dir)
with None. Voice PID536553 never completed initialization. This is a separate
fresh provisioning gap, not the microphone or model failing. Lovelace owns
source/test correction for protected conversation configuration and all related
directory prerequisites; do not disable protected memory as a workaround.
Unlike the previous attempt, voice executed: the preproducer-only recovery
helper must not be reused for this new failure.

Startup83030 ended exit1 with failed bootstrap receipt retained. Read-only35595
confirms both cgroups empty and protected_storage=true, correct private chain,
conversation_dir=null. Runtime voice directory absent; history directory exists
empty; separate conversations directory absent. No user records were read.
This identifies missing configuration plus the directory provisioning boundary;
it does not convert the failed attempt into a drained receipt. Corrected
preproducer helper source3d73497e59e36ffa6383d07f4f69f34f82bb15297452e6e93254d6c08211bca9
passes67 parent tests (coherent systemd reset alternatives), remains source-only,
and is expressly inapplicable to this executed-voice failure.

Protected-memory correction chosen by source review uses
`/var/lib/sage-lantern/history/conversations-v1`, inside existing voice writable
history scope, with exclusive canonical service-owned0700 directory and no ACLs.
Source assembler/installer/config validation corrected; not deployed. Native
staging63695 produces candidate TOMLdbe6b1727affc7aaca2c9df1b8d59dbcee0f004bb96efc2ecb29362d038aa639
under `/var/lib/sage-lantern-protected-memory-candidate-20260907`. Parsed candidate
differs from current configuration by exactly this one sage.conversation_dir
setting. Existing history verified empty, UID994/GID993/mode0700, canonical safe
ancestors and no access/default POSIX ACLs. Candidate not installed; no child
directory created, records changed, or audio captured.

Review also identified eager `history.consent()` before HTTP listener startup
as a potential locked-boot failure with protected storage. Curie owns voice
startup correction/tests preserving unlocked access checks; do not assume a
directory-only patch completes the protected startup path.

Locked-startup source correction skips eager consent for protected memory only;
worker/user operations retain normal admission checks. Voice candidate
ec7eb1a2dd9f3fcae2f6affc813290c6ab773a4bd17918693a19bce2a1312ddf.
Parent focused protected-history/config/factory suite passes71 tests and28
subtests after allowing its temporary loopback test listener (initial sandbox
bind denied); agent broader configuration suite191 tests/90 subtests passes.

Scoped native install63217 completed exit0: installed corrected TOMLdbe6b17...
and voice ec7eb1..., preserving old files under
`/var/lib/sage-lantern-memory-startup-fix-20260907`. Created only empty
`/var/lib/sage-lantern/history/conversations-v1` UID994/GID993/mode0700 after
rechecking canonical ancestor permissions/ACL absence and empty history. Runtime
R7 successfully parses the corrected config. Both failed services verified
PID0/jobless/empty cgroups before changes; no service starts or capture.
Current commissioning inputs changed again; do not reuse prior capture binding.
Executed-constructor-failure recovery remains distinct from the archived first
attempt; failed journals remain untouched.

Native45158 verifies `ProtectedConversationOwner` in the new canonical directory
as actual sage-lantern UID994: valid() passed and close released its lock. Only
the legitimate empty owner.lock was created; no database, private key reads,
messages or captures. Keep that ownership marker rather than downgrading to
legacy storage. Distinct constructor-failure recovery implementation is pending.
Renewed consent requested for a two-second check after all changed inputs are
settled; no third capture has occurred.

Constructor-failure binding55753: voice ExecMainPID536553,
invocation36c825a5bf954e7fa744b67ee1519cf7, monotonic start59841444121 /
exit59842232899; broker ExecMainPID536510, invocation8bdb0d4966ce46ce8f97f0f2949820cd,
start59840160244 / exit59871654477. Both failed/PID0/NRestarts0. Supervisor
broker ID ef5558a5e4d4bf55e451166921d5d7c7,
phaserecovery_required, only empty owner.lock,122-byte fence.json and empty
serial directory. These identify the second attempt separately from the first
archived preproducer failure. Its key was read locally before the constructor
error and signed storage probes preceded voice startup; never claim no SAGE
activity or no local identity use.

Matched host R9 canonical staging completed at
`/private/tmp/lantern-fresh-canonical-20260907-r9`, manifest
781c8c783f3f118715434b90b0dca848e08733eb6f91eb1de7c474a8ccd24847,
31,341 files. Two independent runtime builds match
8c89e56405bb61185ac5c871d6041a7001bfc6943311bd8fafc9f67472179656;
parent independently checked these hashes. Agent verification audit records
manifest/fresh-source, runtime-bundle, frozen-input and exact current-source
artifact checks passed;255 regression tests plus47 subtests pass. Frozen R6
artifacts unchanged, no hardlinks; new artifact root changes only runtime wheel
and lock/provenance. Includes protected config/installer, voice locked-startup,
sleep condition, camera identity and bootstrap preflight fixes. This is a staged
candidate for B, not B provisioning or A deployment/acceptance.

Constructor-recovery helperd29f61a4c5995acb284afe0022ed07074510608bc7110b3dd8e5a087b1acc80a
passed native read-only70679 with expected
5e4ef1529b150b1bd427965fd76c439e310de92e42c040d7af9ebdb4fd7ba8d5,
25 exact invocation journal rows and91 runtime source hashes. Root staging
`/var/lib/sage-lantern-constructor-candidate-20260907`. Parent196 tests passed
before final additions; agent203 helper tests plus67 preproducer tests passed.
Explicit native apply24688 completed exit0: constructor attempt archived,
current quiescence verified, historical containment false/outcomeunknown,
drain_claimed=false, services_started=false, audio_recommissioning_required=true.
Archive `/var/lib/sage-lantern-constructor-recovery` preserves all six artifacts
and journal evidence. Existing protected conversation owner retained in place.
Do not rerun recovery. Fresh input inspection and approved capture remain before
separate broker-owned activation; no third capture has occurred.

Post-recovery native53338 passes fresh bootstrap and actual backend inactive
checks with no live startup/commissioning artifacts. Final reviewed input hash
eb9b9df1e9a59a341597b0a7b8ada6d65e3aebf8fb76573223ebaef73224245e
binds the installed protected-memory and locked-startup corrections. No audio
captured or services started. Re-inspect if any inputs or native boot change;
this hash itself is not capture evidence or readiness authorization.

Native constructor dry-check67977 now passes under actual sage-lantern account
with pinned installed voice ec7e... and configurationdbe6...: real VoiceLoop
construction succeeds with protected memory and protected photo provider.
Worker/controller threads remain None; no listener, loop.run, microphone or
SAGE record request invoked. Existing identity is loaded only on-device, never
exported. Clean memory.close(timeout5) succeeds and releases resources. This
directly verifies the prior Path(None) startup failure is fixed, not full voice
operation or photo inference. New recording consent remained pending then.

Owner explicitly approved the final microphone test. Native60730 passed actual
two-second capture with final bindingeb9b9df1e9a59a341597b0a7b8ada6d65e3aebf8fb76573223ebaef73224245e,
audio_commissioned/capture_verified true and retained_audio false. Separate
broker-owned startup22892 completed exit0: broker_active, voice_active and
storage_verified alltrue. Prior failed archives remain unchanged. Runtime R7
preflight was supplemented with actual backend inactive verification; R9 remains
host-staged only.

Independent live91679 reports audio_operational_ready=true; voice active
PID550639, broker active PID550573. This is the real operational acknowledgment,
not the temporary capture receipt. User may now test on-screen Ask; physical
recording/STT/answer and photo inference are not yet accepted on this fresh run.

Sept7 persistent audio warning investigation: restarting only the status reporter
(PID577357) did not resolve the warning. A standalone status check initially
returned ready; later both root and reporter-namespace probes returned false,
while commissioning admission remained true. This is not evidence of a broken
microphone or a browser-cache problem. Broker550573 and voice550639 remain active.
Read-only signed MessageStorageProbe compared with the preserved completed
bootstrap record confirms the SAME storage instance changed from generation7
at bootstrap to generation8 now. Native dashboard login republishes the vault;
the supervisor correctly rejects a changed epoch and listener acknowledgements
cannot renew. Repeated login publication and safe stale-generation recovery need
fixing before claiming Ask works. Do not loosen the epoch check, manufacture a
drain, discard the bootstrap record, or rerun microphone capture for this issue.

Added read-only `scripts/lantern/inspect-stale-audio-epoch.py` with29 passing
focused tests. It pins completed bootstrap evidence, installed runtime and
process identities and compares two signed live storage snapshots; it offers no
apply/restart operation and does not authorize containment or rebootstrap.
Parent also ran audio readiness, listener acknowledgment and status tests:
125 passed plus25 subtests. These confirm fail-closed behavior, not a device fix.
Normal supervisor drain checks the stale epoch before stopping voice, so it is
not a supported recovery path for this active lifetime. Safe containment and
work reconciliation remain separate implementation requirements. Native repair
must preserve the deployed v27 encryption-expected publication correction from
`/private/tmp/lantern-fresh-v27-vault-publication-20260906`, not use the older
marker-source snapshot as a deployable replacement.

Native recurrence fix is now a matched, unbuilt candidate at
`/private/tmp/lantern-fresh-v27-resident-vault-matched-source-20260907`.
Durable patch `docs/lantern/patches/resident-vault-login-v27.patch` SHA256
de8054f063ba0f3498d302aea6df8fdc761c76616a28d9999a648a218c5501c1
compares equivalent resident keys under the store publication mutex, retaining
credential verification and real lock/key-change generation transitions, plus
the deployed encryption-expected correction. Agent reports Linux ARM6412 tests
and11 subtests passed; host race7 tests and9 subtests passed. Linux web race
compilation is blocked by missing gcc in the pinned offline image. No tests
remain running. No binary built or deployed; generation drift is not recovered.

Owner subsequently explicitly requested deployment. Two matched ARM64 builds
at `/private/tmp/lantern-sage-resident-vault-20260907-{a,b}` are byte-identical:
binary11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c,
manifestdba0a00a8e7af7a5e7e5f73c715568add219b2675feab885829863512b26ca0c,
version `lantern-local.20260907-fresh-v27-resident-vault`. Candidate is staged in
`/var/lib/sage-lantern-resident-vault-candidate-20260907/native`; native version
command confirms max-app-v27. Running binary is still ebf522... at this checkpoint.

Live stale inspection pins at host
`/private/tmp/lantern-resident-inspection-pins-20260907.json` and target candidate
`expected.json` have SHAa4e0c97b2005304db7d405f78a2f5ae0b00c3f1b01045b2ce0d5a590bbadb50b.
All91 installed runtime modules independently match the preserved R7 wheel.
Inspectorc818ec527df9a368ced855cdf388be8e49c21c84765529c1263785eded3648b0
passed on target. Generation subsequently advanced to10 with the same instance.

Recovery helper6ba50... initially refused read-only preflight because
`/var/lib/sage-lantern` is service994:993 mode0700 while its LOCAL_VOICE_READY
leaf is root0644. No mutation occurred. Narrow corrected helper22c6314fc26383276da2ae8f190308a277ee34810657f85ea9d0da16a14f9976
(`recover-stale-audio-epoch-22c6314f.py` in target candidate) passed read-only
preflight and explicit apply. Seventy combined local tests pass.
Both producer cgroups are empty; prior PIDs absent. Broker is inactive; voice
is failed. Historical completion is NOT claimed. Actual repeated signed scans
find exactly one validated conversation_control revision1 and no workflow work.
Commissioning hashes and histories unchanged; stale lifetime originals retained
and copied under `/var/lib/sage-lantern-stale-audio-recovery/archive`.
Complete receiptSHAf6b0b3cdab144290c457f3ca23b67d13d744678122456addcdb26c3fe89fa5f7;
reconciliationSHA8c9338a3c745c13ba1f4fa8de4d2f049f83ba831dc96b3762fcec06f4a1c922c.
Do not rerun that apply. No native stop/restart or new microphone capture occurred.

Separate native envelope host `/private/tmp/lantern-resident-native-envelope-20260907.json`,
target candidate `native-envelope.json`, SHA07295b51599eb95642010d0d76d12fbd7c04326005500ba32064016ff3538aaa,
pins transition9a04a180-20d0-4fa8-88a0-5f8a8ab21d97. Native coordinator
`deploy-resident-vault-native-5d932c67.py` SHA5d932c67696855c1a887f0ee9f964e21a37ac8065ec05e2df55d35800abbd4e4
has100 passing tests and is staged for read-only preflight. It requires genuine
current auth drain, stops model/auth only, and uses the reviewed signed native
restart child with a NEW exclusive request journal; old records remain intact.
Native activation and post-owner-unlock voice reactivation remain pending here.

Native coordinator5d932c67 then passed actual read-only preflight and explicit
apply. It returned native_updated=true, appliance_ready=false,
user_unlock_required=true. Running `/proc/334079/exe` independently hashes to
11f9a5cb...; native PID334079/start2860992/invocationf1875... preserved by the
coordinated exec. New dashboard boot36bc7ff7-1733-499c-8cb4-39e5aea4ca55;
health reports the resident-vault version, encrypted=true, vault_locked=true.
Completion journal is `transition.jsonl` (NOT journal.jsonl) under
`/var/lib/sage-lantern-resident-native-9a04a180-20d0-4fa8-88a0-5f8a8ab21d97`,
SHA37176b122ccfb8788b97367ea0f667bd225618941cd1283fcfce1c0ad0af1bc8.
No update rerun is allowed. Original signer request and old archives are intact.

Parent verified the completion snapshot and exact inactive auth/model cgroups,
archived only their new transition guards, removed those two exact guards,
reloaded and started auth/model once. `restore-auth-model.jsonl` records it;
original service fragments/drop-ins are restored unchanged. Auth is now
PID714756/start7962336/invocationddd073e0a06f45fea0cd28858e368a85.
Model is PID714770/start7962344/invocation54f9e02a93444b4184c814f358d5fa1f.
Voice remains stopped/failed and broker inactive, guarded by the stale recovery
guards. No new capture or new broker lifetime has started.

Owner was asked to unlock once through the device. Latest actual device-access
check still reports locked; no passcode/token was read or injected. Reactivation
helper `reactivate-resident-vault-voice-eca60cbd.py` is staged under candidate,
SHAeca60cbd9fda897477605ceb12b9df128faeb5f903fa6a6a1e406ecea7a13c26,
with100 focused /234 combined agent tests. Parent also ran100 focused tests.
Host draft `/private/tmp/lantern-resident-reactivation-PENDING-UNLOCK-20260907.json`
contains actual live/exited process pins but storage_epoch=null: NOT executable
acceptance evidence. After explicit owner unlock, refresh live state and obtain
a real signed storage epoch, then create reviewed complete expected input and
run read-only preflight before the separate one-shot apply. Do not invent epoch
or reuse the pre-update generation. Native patch is deployed; Ask acceptance
and lock/unlock stability verification still await this final owner step.

### September 7 owner unlock and partial voice reactivation

Owner unlocked through the device. Actual signed storage epoch is
`dd23eb0a686871d5acd705d58d74e25ba43cb66f3163ba4bfea716417a51db26:2`.
Completed expected-input SHA is
`f45e1a98f4e479264d30637cf3ad6611472e3433b00bad86b80b66b4daf33797`.
Frozen eca60 helper read-only preflight passed. Its single apply failed with
`CalledProcessError` after all three archival moves, at journal
`07-reset_sage-lantern-mesh-runtime.service_intent.json`. No reset-done or
new-lifetime-start record exists. Both stale-containment guards remain;
broker is inactive/dead with PID0 and zero exit metadata, voice remains failed
with PID0. Do not rerun the one-shot, manufacture completion, or discard its
archives. A separate checkpoint-specific continuation is under review.
Actual seven journal hashes, relocated inode metadata and broker exit fields
are recorded in host checkpoint
`/private/tmp/lantern-resident-continuation-checkpoint-20260907.json`, SHA
`46a7b779fb9020ce418a1922fd18129402134d06bcbdfb14e0baf8ab086705c9`.
At this checkpoint audio readiness and physical Ask acceptance remained blocked.

Separate continuation helper SHA
`2e9891cc3519839bfcd421f657064d4b6843b7dacf6c74490c50545b5eaa2e67`
passed98 focused /198 combined tests (parent independently ran198). Target
hashes matched; read-only preflight passed, followed by one successful apply.
It adopted the proven inactive broker without claiming the previous reset
succeeded, reset only the failed voice service, released exactly two guards,
then performed broker-owned bootstrap. No recapture/native restart/history
deletion occurred. Original partial journal remains immutable.
New journal directory:
`/var/lib/sage-lantern-resident-voice-reset-continuation`.
Final `09-new_lifetime_start_done.json` SHA
`4fb30b84696149c217c005a4ff00dc475ac02f6cb5cf363b0cd2e8efc4c60819`.
Broker PID744870 invocation23bb50680f0a43dd9d76bd79f00c5068 and voice
PID744914 invocation768e4ee2af88491fbec12292360bbd4f are active.
Actual `operational_ready()` returned true as root and as service UID994.
Owner was asked to test short Ask then lock/unlock. Physical voice/inference
and repeated-unlock acceptance are still pending; Unit A is not fully accepted.

Owner subsequently reported the screen still showed Audio is not ready. Prior
interpreter checks did not prove the long-lived status reporter's response.
Fresh UID994, threaded and status mount-namespace checks returned ready=true.
Added boolean-only audio status diagnostics after the final session check;
no transcript, response, token or key is logged. Status source changed from
5682338aa01ddadb032f4ec6c1ad3e29daeaed179be577f4cf44152367578c59 to
5e2dce93a8fdd2e287f16e6c9886e3ee54b89a85b6187608b015d27eb851c80e.
Previous source retained under candidate as status-server-before-diagnostic.py.
Restarted only status reporter PID577357 to780347. Actual browser GET /status
requests returned200 with audio present=true, ready=true repeatedly from
09:09:38 local time. Thirty status tests and25 subtests pass. This suggests
long-lived reporter state, but the exact pre-restart cause is not established.
Owner visual confirmation requested; do not call the UI or Ask flow fixed
solely from backend readiness. Native, auth, broker and voice were not restarted.

Owner still saw the warning. Subsequent inspection found fleet-admin inactive
since08:17:09 (Requires=device-auth; auth maintenance stopped its dependent),
no listener3100, while old Chromium PID335104 continued running the loaded page
and polling3199. Current on-disk R6 device-state chunk gates on audio.ready,
not capture_verified; browser reload had never been verified after deployment.
Started only fleet-admin; /device returned200. Gracefully terminated the exact
verified unit-a Chromium main process so the existing kiosk loop reloads the
current app. The initial NUL-separated argv check refused without signalling
because Chromium rewrites its command line; subsequent executable, UID and
parsed kiosk/profile/URL checks passed before SIGTERM. No cookies/PINs read.
Require UI-dependent restoration and kiosk asset refresh in deployment
acceptance; previous status-only restart was insufficient. Await physical
screen/Ask confirmation before claiming resolution.

Owner subsequently confirmed Ask works and separately confirmed lock/unlock/Ask.
History then failed with explicit saving-unconfirmed warning. Metadata-only
inspection confirmed history/history.json absent. Fresh installer regression
now initializes version2 preferences (enabled and photos_enabled true, new
generation UUID, empty local items) alongside protected owner directory;
existing state is never overwritten. Parent independently ran40 installer tests
and22 subtests. Actual A missing preferences were initialized exclusively at
10:56:30 local on September7 as UID994, mode0600, with file/directory fsync and
boot/PID/non-recording-state/ancestor checks. No record replay, history wipe,
key change, service restart or clearing of historical uncertainty occurred.
New-question saving/recall still requires actual acceptance.
SAGE consolidated roadmap:3b6667e8-7892-4dc3-a4e1-4d82ed2661a1;
Ask/unlock milestone facbe07b-d92b-43e8-9977-637a6ff21e6b marked done. Duplicate
encryption task3808894d linked to e28f5711 and dropped as consolidation only,
not an encryption-complete claim.

September7 Share/Home UI deployment: owner confirmed locked; actual auth state
matched. Package d6451af23e9713679316f5db9eeb3315dc0184ddfedf72c3f5ac33b00ce86c66
and installer c07fa86184d270b073cb08aeac933834d24db2ba3fdc947cb73eaa1e514e33dd
verified before installation. Exact R6 baseline and unchanged dependency
metadata checked; one installation passed HTTP200. Backup retained at
/opt/sage-lantern/fleet-admin/dist.before-share-ui-20260907. All114 installed
files independently match manifest; served Home client asset matches disk.
Old verified Chromium869880 gracefully closed; kiosk relaunched PID1079615.
Question now above answer, teal Share in footer, Home replaces Clear. Private
preview renders independently of unavailable public provider; submission still
requires real provider preview/consent. Build and123 tests passed. Physical UI
and new-question history acceptance pending; no public activation claim.

### September 7 reboot regression: audio startup incomplete

The stale native maintenance drop-in blocked native SAGE after reboot, cascading
to auth and the UI. After verifying the completed resident-vault transition,
the boot condition was retired with a journal under
`/var/lib/sage-lantern-native-boot-guard-retirement-20260907`; `Restart=no` and
all original maintenance evidence were preserved. Native/auth/UI started and
survived a subsequent reboot.

On Linux boot `31b687ff-dad6-483f-9369-c9e24ae09ca8`, native and auth are active,
but model, mesh broker and voice services are disabled and inactive, with no
start attempts in this boot's journal. Durable audio commissioning admission
still passes without recording. The old supervisor fence remains `active`;
fresh bootstrap correctly refuses to reuse that directory. This is an unfinished
reboot/startup lifecycle, not evidence of another microphone hardware failure.
Do not enable voice directly or discard the fence. Safe previous-lifetime
reconciliation and reboot-persistent startup remain pending. Prior physical Ask
and lock/unlock acceptance applies to the warm session only.

Cross-boot recovery is now implemented and staged, not applied. Candidate directory:
`/var/lib/sage-lantern-cross-boot-candidate-20260907`. Final helper
`recover-cross-boot-audio-v3.py` SHA256
`168c3cbef377cf6b6e3c49b677c4d41b0d15d402d1b5b6ef2c8560ae030daed5`;
read-only collector SHA256
`5d92c6174ef35480f11f6ea7b49260c9b25f759c463320306b9be61f271d708c`.
Parent independently ran 90 recovery/collector tests successfully. Earlier
collection exposed the normal root-owned `arecord -> aplay` alias; the final
helper pins both link and target while continuing to reject other symlinks.
Failure cleanup is independent of logging, has a separate bounded deadline,
verifies effective guard installation and contains attempted producers.

Actual signed read-only inventory while unlocked contained one conversation
control and one mesh inbound row; counts alone are not reconciliation proof.
Collection with the alias fix passed hardware/configuration checks but stopped
at the unlocked-access check after automatic locking. Empty failed collection
outputs remain preserved. No recovery attempt, archival, model/broker/voice
start, recording or commissioning rewrite has occurred. Final collection,
independent pin review, preflight and one-shot apply await owner unlock.
The separate startup coordinator remains source-only and is not yet integrated
with this recovery helper; successful one-shot repair will not imply automatic
reboot acceptance.

After owner unlock, final collection and independent preflight succeeded with
expected-input SHA256
`b3c755dff785202c71323671985b82c3697fff27e7977ceafd099f1a318fa249`.
The exact configured idle receiver and conversation control were validated in
repeated signed scans; no record was rewritten. One-shot recovery then completed:
model PID31767, broker PID33384, voice PID33444. Independent root and service-user
`operational_ready()` both returned true while auth remained unlocked.
The old supervisor tree is preserved under
`/var/lib/sage-lantern-cross-boot-audio/31b687ff-dad6-483f-9369-c9e24ae09ca8/supervisor`.
New durable `new-lifetime.json` SHA256
`960994ec6464abd571b6cf73c834346eaa0eca52ee86fbeec6c28c54d4ca368f`
binds the actual new boot/broker evidence. Historical outcomes remain unknown;
no capture or inference test was performed by the helper. Owner Ask acceptance
is pending. Automatic boot startup remains disabled and not accepted.

Owner subsequently confirmed Ask works. Later UI deployment reconnect observed
another Linux boot, `4c184137-0d38-4a24-82a3-1611c67b93c9`, with all three audio
services inactive again. This reaffirms the unresolved automatic-startup gap;
the cause of the reboot has not been established.

Compact header build (124 tests and build passed) is uploaded, not installed,
under `/var/lib/sage-lantern-header-candidate-20260907`, awaiting device lock.
Archive `lantern-compact-header-20260907.tgz` SHA256
`6ee0319dc7f6db7b8ba9b0a90b9ad97831fb75bbc68207830650519b304a21fa`;
installer SHA256
`75c0fcc2e657f34437d290292aeeb8ea4a53bc07b5bb9035968a0b3220840f53`.
It pins the previously deployed Share/Home UI baseline, updates only UI assets,
and preserves a distinct backup. No audio service restart is part of this update.

Compact header installed after owner confirmed lock; installer returned HTTP200
and preserved `dist.before-compact-header-20260907`. Independently verified all
114 installed asset hashes against the package. Refreshed the exact locked
kiosk process (PID1806), not audio/native services. Current Linux boot observed
at deployment is `fa412850-b01f-4a1f-973b-a4c508f65417`; reboot cause remains
unverified. Smaller logo now serves as Home, Nearby is icon-only, Menu stays
labelled. Owner visual acceptance and automatic audio boot work remain open.

### September 7 automatic startup installation

Current boot `fa412850-b01f-4a1f-973b-a4c508f65417` was recovered once with
expected SHA `3fa095e7d3fd8d9c260e87d0a3a2b1010dd1fd530cfe919b30574e56e5f55c0f`.
Three settled workflow rows passed preflight. Model PID10202, broker PID11788,
voice PID11837 remained active; operational readiness returned true. New lifetime
SHA `640955ff938bfefff623e89215f2019fb2cc19d41c64e6ef9ac3ebc6e7c0d835` is
the actual initial prior for the newly installed startup coordinator.

Installed standalone coordinator → adapter → reviewed full recovery helper;
the existing R7 package and commissioning inputs are unchanged. Target
`/opt/sage-lantern/libexec` contains the coordinator, adapter, policy and installer.
Coordinator SHA `3a3d504395d203b0916bf70ac9e469c443f81b5018adbcd250b99adbf0dfe41b`;
adapter SHA `9f73decdef15cd7dc6fcea1a137a8f6433a035f5450d92198c80ae26778e43f0`;
installer SHA `d3fd22d0f3d00d2032078fbc832882197c697d495ac4f8107e3f000ad86c488f`.
Policy SHA `4be8dd77339faf83d1437ea481fc93cafc47752e0c6d789de7d03ac2de117b39`.
Actual installed `/etc/sage-lantern/startup.json` SHA
`034b2785d45f90f6b490ab4b02a76b2bea6151637f9cebe64bc9870608af8eb0`;
service SHA `ce82967f6738fe074ea984f236c14c59f27a38ddfd058e716d395b1714e65293`.

Read-only installer preflight and one-shot install both succeeded. Independently
verified `sage-lantern-startup.service` enabled, `Restart=no`, exact standalone
ExecStart and installed hashes. It was deliberately NOT started in this already
recovered boot. Model/broker/voice remain disabled for coordinator ownership;
their active PIDs were unchanged by installation. Parent ran 80 matched startup,
installer and adapter tests successfully; independent source review found no
remaining deploy-critical blocker. Real next-boot acceptance is still pending.
Coordinator waits for activated owner unlock, validates the previous durable
broker lifetime against the surviving fence, and permits one attempt per boot.
It preserves unknown outcomes and never claims that an ambiguous failed attempt
was contained. Do not claim Unit A finished until reboot/unlock/Ask passes.

Also deployed grouped-header package SHA
`de0d9bc14547ea9625480c5ca73b63bc3034650a346d386be59212ef8e9fe21e`;
installer SHA `59f9c645c9a9ff8588ace8bf9cb30998cc8667128a57a6785e645e076dbc0f1a`.
All 114 installed assets and the served grouped-header JS were verified; the
locked kiosk was refreshed. Nearby is grouped beside the logo, with Messages
and Menu together on the right. Backup `dist.before-grouped-header-20260907`
is preserved. Host UI tests (124) and build passed.

Board identifies as ROCK 5B+; owner confirmed these are 8GB units. Linux reports
8125804 KiB usable memory. Warm idle observation: approximately 3.9GiB available,
302MiB swap occupied; llama-server approximately 5.4GiB RSS including 2.45GiB
file-backed pages. Running model is Qwen3.5-4B Q4_K_M, context4096, threads6.
This is not peak voice/photo workload evidence or a 16/32GB sizing acceptance.
Owner reports B hardware ready; do not copy A identities or represent A as done.

### Normal shutdown startup regression — 2026-09-07

Authorized reboot to `d741af22-1a90-4779-bbd2-fe263c032819` exposed a missed
normal case: the broker persisted `stopped_verified`, but startup only accepted
`active`. The coordinator failed before recovery mutation; the current-boot
recovery directory was absent. Preserve its failed coordinator journal and
stdout/stderr; do not retry that coordinator attempt or rewrite its evidence.

Patched helper, adapter and coordinator to accept the verified clean-stop phase
without relaxing broker identity, prior lifetime, inventory or epoch checks.
Independent regression run: 140 tests passed, including a real supervisor-emitted
clean-stop fence and unchanged archival. Installed standalone patches:

- Helper V4: `ed7af4161460e2e72e20a38c4394e12a15fb432b485a2a442e7d7e12a32958e0`.
- Adapter: `47d946ad9b2831b9233d4af5c48d5db3a7e2355509b97d3ca471b3c492a34bc8`.
- Coordinator: `9d903bec85a9f6581c56abe19289506ebc4aa854d84f850d579802710ce571b5`.
- Policy: `edf1dabd85422f1464435f7b62d0db48fcdf8c8ade1435c16c725adc7a5e9163`.
- Installed config: `5fc8226bf9a8fa3fd6f159534a92c62d5bed330dad673ff0a88d3b452c4fdc71`.

Prior standalone files preserved with `.before-stopped-verified-20260907` suffix.
R7 runtime, commissioning receipt and private records unchanged. Device had
auto-locked before V4 preflight; owner unlock requested. Recovery and another
real reboot/unlock/Ask acceptance remain pending. After explicit one-shot adapter
recovery, advance initial lifetime to its actual receipt before the next reboot;
do not rerun the failed same-boot coordinator.

Owner unlocked; V4 read-only preflight passed and explicit adapter recovery ran
once successfully on boot `d741af22`. Broker PID 21876, voice PID 21926;
independent `audio_readiness.operational_ready()` returned true. Actual new
lifetime SHA `972411b1e46f32f6e3953483407bea7a84bd2a7263fbac89ba7f7e3e21b6fa5f`
was independently matched to the live broker and adopted as config initial
lifetime. Config SHA is now
`d4488c4d8101aedc81773ad186773cd013974e54c2aae254b76edb38e74bb527`.
Original config saved as `startup.json.before-v4-lifetime`; failed coordinator
journal remains untouched. This proves current recovery, not next-boot
automatic acceptance. Owner subsequently confirmed Ask works.

Next owner-reported reboot `74baf7fa-c876-4457-a906-37593901f11b` automatically
reached `full_lifecycle_verified` then `operational` at 16:03:23 without any
manual restart/recovery. Lifetime SHA
`493bbc986ea04168a57718c172f7c68f661b289a834ced0e0dd89ee449aa32f8`.
Independent audio operational readiness was true; llm and voice-loop active.
The owner saw Audio Not Ready while coordinator was still running. Service
started 16:00:16 (includes unlock wait); recovery artifacts begin 16:02:22,
model phase 16:02:47–16:03:01, completion 16:03:23. This confirms automatic
recovery but exposes slow startup and misleading transitional UI. Do not claim
three minutes of model loading: model phase was approximately 14 seconds.
Source investigation assigned for truthful startup status and repeated-check
costs; no runtime changes applied in this boot. Post-boot owner Ask still pending.

Preserve Debian, SSH keys/access, NetworkManager/Wi-Fi settings, installed OS
packages, unrelated user files, unrelated services and hardware firmware.
Keep the service account but create fresh independent Lantern identities.
Inspect existing GDM/autologin, AccountsService, bootloader/splash and logind
configuration before changing them: these are shared OS files, not disposable
Lantern state directories. Do not retain or restore old keys merely because a
backup exists. Deleting files is not a claim of forensic secure erasure on flash.
