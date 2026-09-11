# Guarded R7 vision activation and provisioning default

Source review candidate, not deployed. The vision CLI still rejects `--apply`
before loading inputs. Owner approval for A's model restart and one two-second
local capture has been relayed; no additional owner approval is being requested.
Independent acceptance, a fresh successful native preflight and deliberate
source promotion remain required before activation. Existing frozen maintenance
v3, runtime wheels, A services and B remain untouched by this source work.

## Current A diagnosis

Boot `7bfcc4c6-b7d7-4485-9273-a2097c083bf5`, unlocked and audio operational.
Camera HTTP 200 reports present. Actual voice PID 17282 lacks `--photo-ready`;
actual model PID 15665 lacks `--mmproj`. The projector exists, 675569344 bytes.
Deployed voice SHA
`ec7eb1a2dd9f3fcae2f6affc813290c6ab773a4bd17918693a19bce2a1312ddf`
contains the explicit photo-unavailable 503 gate. No photo was submitted during
diagnosis; absence of recent logged errors is not an observed photo HTTP result.

## Concrete transition

`activate-fresh-vision.py` uses the maintenance backend with an explicit R7-only
candidate profile: installed and candidate wheel pins must be identical R7.
Its replacement method never runs pip. The normal runtime lane remains retrieval
only by default. No retrieval/performance change is folded into this operation.

The transition preserves unknown historical receipts, cooperatively contains
UI/voice/broker, freezes signed settled-work evidence, requires a fresh positive
current-auth receipt, and stops basic importers. Native SAGE remains running.
Existing inference uncertainty must be genuinely empty, not cleared by this
helper. The model receives its own effective maintenance inhibit and empty-group
verification before configuration changes or restart.

An absent-only `95-lantern-local-vision.conf` appends only `--mmproj`,
`--no-mmproj-offload`, and `--image-max-tokens 256` to the actual pinned model argv.
Existing thread/parallel/context/memory policy is not silently changed. The
binary, model and projector must be in the reviewed file inventory; model SHA
is `00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4`,
projector SHA is `302b92d565080b9cc0281186979ae75a7429ec23d14f6f7607a035539b21f3a6`.
The actual binary must advertise the new flags. Its live executable inode,
argv, new invocation, UID, owned listener, health and idle slots are checked.

Two deterministic 64x64 PNGs, entirely red and blue, are generated in memory.
Only these synthetic images and a fixed color question go to the paused model's
owned loopback chat endpoint. Each response must complete normally and identify
the correct color; HTTP 200 alone is insufficient. Thinking is disabled for
these bounded tests. Private photos/questions are neither sampled nor exported.
Durable synthetic-inference uncertainty remains on any incomplete request.

Only after both probes does the voice definition gain `--photo-ready`, retaining
the exact fresh config/ALSA/broker-request arguments. Existing 30/40/50 and
reviewed sleep fences remain. Basic services restore behind newly locked auth;
the owner then unlocks within the bounded interval. Old audio evidence and
lifetime trees are archived intact, the real two-second local validator creates
new commissioning, and only ColdBrokerBootstrap starts the new voice lifetime.
The capture snapshot is updated solely for the checked new voice definition and
the actually verified model invocation. Startup policy includes the new model
argv/projector/configuration pins and the distinct durable lifetime.

Failures re-inhibit/contain producers and the model, preserve journals and
unknown outcomes, and never automatically retry or roll back. No acceptance is
claimed for SIGKILL/power-loss recovery. Original envelope and effective-input
revision hashes are separately recorded; changed observations are not presented
as the original pins.

## Type=simple correction

The shared backend now requires `/usr/lib/systemd/systemd` in the pinned file
inventory. Initial startup may observe only that exact image followed by the
required pinned executable, with stable PID/start ticks/invocation and canonical
cgroup. Only root/expected service UID are accepted in that pre-exec interval;
camera DynamicUser uses the exact camera unit/user contract rather than NSS.
Once the required image is observed, reversion is rejected and final UID/argv
are strict. Model and inherited auth/status/display/camera/UI restores use this
predicate. Tests exercise the actual process reader on filesystem-backed proc
fixtures and the real start/restore methods, including wrong argv/UID, foreign
images, identity changes and timeout; native systemd is not simulated proof.

## Camera-equipped provisioning defaults

`install-camera-local.sh` now always installs an exclusive root-controlled
`/etc/sage-lantern/vision-default.json` requirement before camera installation.
`camera-vision-defaults.py` binds the selected stable camera and matching
model/projector pins. It does not write either execution flag or claim readiness.
The same guarded activation driver consumes and verifies that requirement; for
existing A it installs the same requirement within its retained transaction.
Thus camera transport installation cannot silently be described as completed
photo inference provisioning: the installer explicitly reports required guarded
vision commissioning. There is no direct model or voice start in the installer.
Capture consent is not inferred from the default preference.

Camera-less selection produces only `camera_configured:false`,
`vision_required:false`, `readiness_verified:false`, and writes nothing. No B
camera presence or readiness claim is made. Existing B/frozen release files are
not changed; inclusion in a future canonical provisioning release is separate.

## Review/deployment input contract

Envelope schema: `sage.lantern.fresh-vision-activation.v1`; exact fields:
`schema`, `backend`, `defaults`, `maintenance`, `camera_path`.
Both script pins use absolute path/full SHA and trusted hash-checked source.
The defaults helper must sit beside the driver. The nested maintenance schema
is unchanged, except the composition explicitly accepts only identical R7 wheel
pins. It must include the actual systemd image, native/model/projector and
service-definition hashes, plus an existing default policy hash when present.
Camera metadata must resolve to the selected actual video character device and
match the running camera's argv. Current boot/access/storage/process pins must
be freshly observed after startup has finished; the earlier baseline is stale.

CLI: `--expected --expected-sha256 --backend --backend-sha256`; default read-only.
After review, promotion must require both `--approve-model-restart` and
`--approve-microphone-capture`, with `--wait-owner-seconds` bounded 1–300.
No tokens, keys or credentials are expected JSON fields. A sealed independent
R7 environment already exists, but fresh complete live pins have not yet been
collected for this vision envelope. Do not run the legacy installer or rerun a
partial transaction to substitute for this preflight.

## Reproduction

Frozen review bundle: `/private/tmp/lantern-fresh-vision-review-20260908-v1`.
Run `sh review-tests.sh` inside it. The bundle contains the complete supporting
scripts, camera template, test fixtures, runtime test sources and pinned SDK
wheel. SHA256.json defines this source/test closure, not an installed release.
Existing maintenance v1/v2/v3 snapshots remain historical and unchanged.
