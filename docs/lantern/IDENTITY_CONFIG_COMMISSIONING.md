# Identity → protected config → owner setup

2026-09-06. Source-only implementation; no appliance changes or release build.

Use the existing runner in order: `--phase base`, **`--phase identity-config`**,
then `--phase app`. Base and app retain their independent explicit arguments and
fresh-target guards. There is no new pre-initialization touchscreen wizard.

## Reviewed inputs, no manual chain ID

Before the middle phase, prepare the initial SAGE YAML described in
`FRESH_IDENTITY_SETUP.md`. Supply a private staging JSON plan with exactly:

- `schema`: `sage.lantern.commission-plan.v1`.
- `unit_id`: exact prepared `unit-a` or `unit-b`.
- `runtime_template`: absolute path to the reviewed, nonsecret runtime TOML.
- `runtime_sha256`: SHA256 of that template; it must match the installed fresh
  `/etc/sage-lantern/lantern.toml` before replacement.
- `quotas`: explicit positive integer `actor_bytes`, `node_bytes`, `actor_objects`,
  `node_objects`, `min_free_bytes`. No default quotas are invented.

The runtime template must use private loopback `http://127.0.0.1:8080`,
`identity_path="/var/lib/sage-lantern/identity/lantern-agent.key"`, and the exact
initial bootstrap home domain. It must not carry a copied `chain_id` or an
existing `protected_storage` field. Other TOML fields and YAML settings are
preserved; conflicting policies reject rather than silently remap another unit.
The ordinary base template already supplies the private identity path.

```sh
python3 scripts/lantern/provision-fresh-appliance.py \
  --output "$CANONICAL_OUTPUT" --manifest-sha256 "$REVIEWED_MANIFEST_SHA256" \
  --unit-id unit-b --phase identity-config \
  --identity-config-sha256 "$REVIEWED_INITIAL_SAGE_YAML_SHA256" \
  --commission-plan "$REVIEWED_PLAN_JSON" \
  --commission-plan-sha256 "$REVIEWED_PLAN_SHA256" \
  --owner-approved-companion-bootstrap
```

Default is planning only. Add `--execute` only on the authorized native unit.
Selecting this explicit phase approves protected configuration installation as
well as the named Companion bootstrap. It does not choose a PIN, unlock a vault,
enroll publicly or start a service. The original `identity` phase remains an
identity-only operator option; the original standalone assembler still produces
disabled candidates. `python3-yaml` is now an OS prerequisite for this path.

## Actual execution and outputs

1. Verify canonical release/current source closure, installed binary match,
   prepared unit metadata, initial config hash, plan/template hashes and quotas.
   Require provenance containing the new bootstrap/listener and protected-store
   sources. Probe the actual target private filesystem for `min_free_bytes` plus
   16 MiB headroom; this is not a reservation or future capacity guarantee.
2. Before initialization, reject installed-TOML mismatches, existing backups or
   previous commissioning outputs. Exclusively create and fsync root-private
   `/etc/sage-lantern/commissioning.pending.json`, then fsync its directory.
   Only then run the bounded initializer as `sage-lantern`, capture its public JSON
   with a 120-second timeout, and reject malformed/duplicate/extra fields,
   wrong-unit IDs, reused identities or readiness/enrollment claims.
3. Feed that exact output to the assembler's `generated_config` function. The
   actual returned private chain ID becomes `[sage].chain_id`; the private key
   path remains fixed. Enable `protected_storage` and configured `private_media`
   for subsequent startup, without claiming either backend is ready now.
4. Recheck the initial configs and absence of backups/output collisions after
   initialization. Preserve public output at root-private
   `/etc/sage-lantern/initialized-public.json`; retain original config files as
   `*.before-commissioning`. Replace each config atomically, retaining ownership
   and modes. Fsync both config directories before publishing bindings. Write
   `/etc/sage-lantern/commissioned-bindings.json` with the actual
   private/public IDs and distinct fixed paths. No `.key` file is read or copied
   by the Python config integration.
5. Read back and hash both configs and the public-output/binding files. Atomically
   publish root-private `/etc/sage-lantern/commissioning-complete.json`, bound to
   the pending transaction UUID, unit, reviewed manifest/plan/initial-config
   hashes, and all four resulting file hashes. Fsync the completion directory
   before unlinking the pending fence; fsync the unlink. An unlink-fsync failure
   attempts to restore the fence and remains an error, never reported success.

Native app installation requires this exact completion receipt for its reviewed
manifest, verifies all resulting hashes, and refuses a pending fence, missing
completion or changed files. It rechecks before file installation. Bindings alone
are never completion. Check-only release planning is not native commissioning
verification. The legacy identity-only option cannot bypass this app-install gate.

Public mapping is `/var/lib/sage-lantern/identity/lantern-public.key`, separate
from the private application key. Runtime TOML receives **no** public-chain or
public-key configuration. The public mapping is input for a later independently
authorized gateway enrollment, not a registration or public validator identity.

Success reports `configuration_applied=true`, but `ready=false` and
`provisioned=false`. No PROVISIONED, LOCAL_VOICE_READY, broker grant or fake
inventory attestation is written. App installation checks installed SDK/runtime
closure later. Owner name/PIN/recovery, encrypted backend availability, committed
enrollment, actual service listeners and hardware remain acceptance gates.

The two config replacements are not a cross-file transaction. Failure/timeout
stops the phase, retains initialization state/backups, and can leave partial
configuration. Its durable pending fence remains, blocking app installation even
if bindings or a completion file already exist. Do not automatically rerun key generation or start dependent
services; reconcile retained public output and files. There is no automatic
rollback that could erase newly created identities.

Synthetic tests inject failures after each config replacement, during config and
completion directory fsync, during initial fence durability, and during fence
clear. They check retained containment, pre/post-init collision rejection, and
completion hash/unit/manifest verification. These are not power-cut/native proofs.
