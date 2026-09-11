# Matched appliance manifest (unactivated)

`scripts/appliance-release-manifest.py` hashes an explicitly selected, closed,
separately staged release tree. It does not build/copy artifacts, execute staged
code, install, provision identities or certify readiness. No current live/staged
production artifacts were rebuilt for this tool.

## Selection and layout

Supply external JSON with exactly `schema`, `release_id`, `components`, `files`.
Schema is `sage.lantern.appliance-selection.v1`; components contain exact `sage`,
`sdk`, `runtime` version strings. Each files entry is exactly `{path, role}` with
a normalized relative path. Selection is operator-reviewed input, not automatic
discovery of everything in a checkout.

Required roles: installer, root_helper, systemd, dropin, config_template, ui,
runtime_source, sdk_source, sage_binary, sage_manifest, sdk_wheel, runtime_wheel.
The last four select exactly one file each. Additional support/dependency files
are permitted only when explicitly selected. Stage generated drop-ins as actual
reviewable files; a script that later synthesizes policy is not a substitute.

Keep runtime source at `software/runtime` and original SDK source at
`sources/sage-sdk`. The runtime wheel is checked against that exact source set;
the SDK wheel must match its original/staged manifests and required API surface.
Select the SAGE builder's manifest, matching binary and component version. Its
ELF architecture, binary digest and presence of source/dependency/toolchain hash
maps are checked, not independently rebuilt. SAGE provenance remains assertions
from the reviewed builder, not a cryptographic publisher attestation.

The UI role must include `apps/lantern-fleet-admin/dist/server/index.js`,
package.json, package-lock.json and explicit node_modules files. Include all
other built client/server assets and required runtime dependencies in the exact
selection. The manifest does not prove npm output corresponds to frontend source
or that dependency selection is sufficient to execute; separate build/native
acceptance must establish that. Stock node_modules symlinks are rejected: prepare
a separately reviewed regular-file deployment tree, never silently dereference
an arbitrary checkout or A's application directory.

Every payload must be a single-link regular file mode 0644/0755; directories must
be 0755. Links, extra/missing files, unselected empty directories, state/key/cache
paths, and recognizable PEM private-key material are rejected. This is not a
general secret detector: arbitrary embedded credentials cannot be ruled out by
file extensions/content markers. Only reviewed public sources/templates belong
in staging. Never include A's identities, actual configuration with credentials,
Root/PIN/recovery state, cookies, history, photos, outboxes or radio identity.

## Commands after source freeze

```sh
python3 scripts/appliance-release-manifest.py build \
  --root /absolute/sterile-stage --selection /absolute/reviewed-selection.json \
  --manifest /absolute/new-appliance-manifest.json
python3 scripts/appliance-release-manifest.py verify \
  --root /absolute/sterile-stage --manifest /absolute/new-appliance-manifest.json
```

Metadata stays outside the closed stage. Build refuses existing output. It takes
the full file/directory inventory, hashes with file-identity checks, validates
artifact matching, then rehashes and re-enumerates. Selection edits, inode/mode/
mtime/ctime changes and file-set drift abort. Repeat builds over unchanged bytes
produce identical manifests. A subsequent edit still requires verification at
consumption; two scans do not lock out future writers.

Both modes report deployment_ready=false, runtime_acceptance_verified=false and
signed_authenticity=false. There is no signing key or implied authenticated
distribution. Protect the manifest/selection through a separately trusted
release channel. No untrusted staged Python scripts are imported by this tool.

## Why installer preflight is not wired yet

The final source/artifact freeze, full reviewed file selection, destination and
ownership mapping, fresh B appliance installer, source-bound frontend build,
complete dependency inventory and native acceptance remain open. Required roles
prove inventory coverage, not semantic completeness of arbitrary selected code.
Existing installers can synthesize policy or expect files beyond a partial
selection. Wiring this generic inventory check as a finished installer gate now
would imply a completeness contract that does not yet exist. Preserve old
artifacts; promotion and independent B identity provisioning remain separate.
