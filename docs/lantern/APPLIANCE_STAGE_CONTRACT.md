# Canonical appliance stage (v1)

`scripts/stage-appliance-release.py` selects this repository's actual source paths,
not caller-supplied role filler. It creates a **new, unpromoted directory** only.
No dependency installation, build, network, target inspection, identity access,
service action, or deployment occurs. Do not build a production stage until the
source/artifact freeze; temporary synthetic fixtures exercise the contract now.

## Inputs and output

Three absolute, non-overlapping roots are required. Symlinks in ancestor paths
are refused (on macOS use `/private/tmp`, not the `/tmp` alias).

- `--source-root`: reviewed sage-commons source checkout/export. Exact required
  installers, helpers, units, templates, theme and verifier/build scripts are
  selected by `required_sources()`. All direct runtime Python modules plus its
  pyproject are selected automatically. Source `__pycache__` is never copied.
  The UI source package/lock and public assets must match the supplied UI artifact.
- `--artifact-root`: a **separate sterile export**, containing exactly the layout
  below, not a device home and not the whole repository's historical cache.
- `--output`: new directory with an existing parent. Existing output is refused.
  Failure can leave a partial stage, but never publishes a final manifest after
  a detected source change. Do not consume output until the command succeeds.

Artifact layout:

```
software/stack.lock.json
software/bundle/linux-arm64/bin/sage-gui
software/bundle/python-wheels/*.whl
software/cache/models/ggml-base.bin
software/cache/models/qwen3.5-4b-q4_k_m.gguf
software/cache/models/mmproj-BF16.gguf
software/cache/models/en_US-lessac-medium.onnx
software/cache/models/en_US-lessac-medium.onnx.json
software/cache/sources/llama.cpp-b9637.tar.gz
software/cache/sources/whisper.cpp-v1.8.6.tar.gz
release/sage-manifest.json
sources/sage-sdk/pyproject.toml
sources/sage-sdk/README.md
sources/sage-sdk/src/sage_sdk/*.py
apps/lantern-fleet-admin/package.json
apps/lantern-fleet-admin/package-lock.json
apps/lantern-fleet-admin/dist/**
apps/lantern-fleet-admin/public/**
apps/lantern-fleet-admin/node_modules/**
```

The SDK sources are the exact original upstream inputs, before the SDK builder's
local-version substitution. SAGE manifest is the corresponding
`sage.lantern.binary-staging.v1` output. The artifact-root stack lock must contain
exactly the selected binary, wheels, five model files and two source archives, with
their hashes/sizes and Linux ARM64/Python 3.11 target. Old downloaded archives,
prebuilt llama binaries/libraries, old SDK wheels and unrelated cache entries
are rejected, not deleted. Prepare a separate reviewed minimal lock for the new
release; do not modify/promote the historical bundle automatically.

The existing matched-component verifier checks runtime/source hashes, SDK
original/staged source hashes and API signatures, and SAGE ELF/provenance/hash
consistency. It does not execute SAGE or attest the actual server's capabilities.
Base numpy, piper, PyNaCl and cryptography wheels are required at the installer
pins. All selected wheel bytes are inventoried; this builder does not replace
an offline target `pip` resolution/import test or prove transitive ABI fitness.

Output:

```
OUTPUT/payload/                 # closed appliance release root
OUTPUT/payload/release/destinations.json
OUTPUT/selection.json           # generic manifest input
OUTPUT/manifest.json            # published only after final source recheck
```

Safe staging command after freeze (writes only the new local output):

```
python3 scripts/stage-appliance-release.py \
  --source-root /absolute/reviewed-source \
  --artifact-root /absolute/sterile-artifacts \
  --output /absolute/new-checkpoint --release-id lantern-reviewed-1
python3 scripts/appliance-release-manifest.py verify \
  --root /absolute/new-checkpoint/payload \
  --manifest /absolute/new-checkpoint/manifest.json
```

## Full UI inventory, not a placeholder dependency directory

The complete supplied `dist`, `public`, and `node_modules` file set is hashed;
no dependency subset is silently copied or production-pruned. The package and
npm v3 lock must match source. Every applicable Linux/ARM64/glibc lock package,
including dev and applicable optional packages, must be present with its exact
locked version. Nested dependencies count separately; unlocked package roots,
missing package metadata and missing declared dependency graph nodes are refused.
Other-platform packages must be absent. Public assets match source byte-for-byte.
The vinext CLI and server entry must exist. This protects closure/inventory, not
the authenticity of npm packages or correspondence of a frontend build to every
source file. Native execution and peer/semver/ABI compatibility remain gates.

No symlink/hardlink materialization is performed. A normal npm `.bin` symlink
tree, workspace links, or platform-mixed development node_modules will fail.
Supply a separately prepared, reviewed Linux ARM64 dependency tree without bin
links (`--bin-links=false` at its offline preparation step); the fresh installer
executes `/usr/bin/node .../node_modules/vinext/dist/cli.js` directly. Do not drop
packages just to make verification pass. Permission modes must be 0644/0755 and
directories 0755. Node 22.13+ is an external target prerequisite, not bundled.

## Destination contract for the fresh installer

`release/destinations.json` is itself included in the integrity manifest. It
contains exact source paths, roles, destination paths and operations:

`destination_mode` specifies 0755 for installed helpers/binary, 0644 for copied
units/templates, or `preserve_manifest` for UI files. Non-copy operations have no
file mode and must not be interpreted as a filesystem write instruction.

- `copy`: byte-identical destination, e.g. SAGE `/opt/sage-lantern/bin/sage-gui`,
  models `/opt/sage-lantern/models/`, full UI `/opt/sage-lantern/fleet-admin/`.
- `wheel_install` / `offline_build`: installation/build input, **not** a file to
  copy onto the destination directory/executable.
- `template`: requires per-unit protected configuration assembly; do not mistake
  `lantern.example.toml` for a ready vault-bound configuration.
- `installer_transform`: auth unit is transformed by the fresh app installer.
- `reviewed_copy`: established legacy destination only; needs feature/Unit B
  review before installation or activation.
- `review_only` / `evidence_only`: retained tooling/provenance; not auto-installed.

Leibniz integration: `scripts/lantern/install-fleet-appliance-local.py` and all its
helper dependencies are mandatory exact source paths. Its existing `--release-root`
input is `OUTPUT/payload`; `--manifest` is `OUTPUT/manifest.json`. This stage
implements its required UI layout and role contract without editing its code.
The installer currently enforces the generic manifest, not this canonical profile;
independent canonical-profile enforcement remains a follow-up integration change.
Do **not** blindly loop over destination rows as a root installer. The fresh
installer still validates separate public unit/radio/audio/prepared metadata,
generates unit-specific locked service/policy bytes, and refuses existing state.
The legacy `/home/radxa/sage-commons` UI service is evidence only; the new service
is generated under the `sage-lantern` account with `/opt` working directory.

The lock-required UI drop-in is shipped as a review-only proposal; the fresh
installer already sets this environment in its generated unit. Additional sleep
drop-ins/tmpfiles/ExecStart transformations remain the reviewed idle-installer
policy, bound through its exact source, not automatically applied here. Legacy
Unit A status/audio/camera/voice activation helpers are not certified for B by
inclusion. No enable marker, unlock store, private genesis, device binding, photo,
outbox, key, PIN, recovery state or `PROVISIONED` is selected from source/device.

## Integrity and remaining release gates

Full source file identities and hashes are compared after copying and manifest
validation; runtime/SDK module sets and artifact trees are re-enumerated. Changes,
including identical-byte inode replacement, abort final manifest publication.
All copied file contents are streamed from validated descriptors; input size/file
limits are inherited from the generic verifier. JSON is canonical and timestamps
are fixed. Two builds of the same reviewed inputs have identical file hashes and
manifest bytes. No archive is built here; a later archiver must fix ownership,
ordering and compression parameters too.

Hash consistency is **not signed authenticity**, nor a general secret detector.
The private-key heuristic and prohibited paths do not discover every secret a
caller could hide in an otherwise allowed JavaScript/source file. Inputs must be
independently reviewed sterile exports. No physical-theft encryption claim follows
from staging. Every readiness/activation flag remains false until native runtime,
independent B identity provisioning, protected storage/PIN/recovery, radio/audio,
camera/display/suspend and end-to-end acceptance succeed. Neither this builder nor
the generic manifest is the final appliance installer or an authorization to run
legacy provisioning `verify`/write `PROVISIONED` early.
