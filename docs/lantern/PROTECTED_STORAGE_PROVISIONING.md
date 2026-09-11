# Protected-storage provisioning contract

2026-09-06. Source-only disabled-candidate assembler implemented; no live
activation or completed provisioning is claimed. Generic SAGE/private-media and Lantern
runtime protected-storage defaults remain disabled for legacy compatibility.

## Two configurations, two gates

The operator-controlled SAGE `config.yaml` needs the explicit `private_media`
block documented in the sibling SAGE repository's
`docs/PRIVATE_MEDIA_PROVISIONING.md`. Enabling requires all five positive,
validated ciphertext-byte/object quotas and a free-space reserve. The intended
SQLite filesystem must support the native probe; a staging host's free-space
observation is not evidence about either Unit A or Unit B. No quota approval
creates a vault or proves it is unlocked.

The fresh Lantern runtime configuration additionally requires these entries
inside its existing `[sage]` section:

```toml
[sage]
protected_storage = true
chain_id = "<exact operator-verified LOCAL private-chain ID>"
```

This is a documentation placeholder, **not a ready-to-install template**. The
assembler refuses absent, obvious placeholder, or inconsistent chain inputs;
it cannot independently verify a supplied chain without target evidence, so
its outputs always remain disabled. This example is for the later activation stage.
Keep the independently verified existing `base_url`, `identity_path`, and
`domain` fields; do not replace them with this partial example or create a
duplicate `[sage]` table.

`chain_id` means the exact LOCAL private SAGE chain bound by the operator to
this unit's endpoint and ordinary-agent identity. It is not a domain, friendly
device/network name, unit ID, public chain ID, or a value derived from any of
those. Never copy Unit A's chain binding as a Unit B default. Runtime syntax
validation is not independent verification of this operator binding.

## Proper assembly stage

1. Keep sterile/base configuration generic and disabled. Existing prepare and
   inventory scripts do not establish protected-storage readiness.
2. Obtain the exact operator-verified per-unit local chain binding without
   inventing an identity or modifying keys. Record public binding evidence
   separately from secrets.
3. Verify the intended installed SAGE binary supports configured private media
   and guarded workflow records. Verify the corresponding updated SDK and
   runtime artifacts. Merely retaining the old `11.19.10` base or matching a
   version label is insufficient. Validate the explicit SAGE media operator
   settings and intended filesystem/probe prerequisites.
4. Only then emit `protected_storage = true` and the exact verified `chain_id`
   into the unit's protected runtime profile. Do not enable these settings in
   the shared generic example or emit them during metadata-only preparation.
5. An initially locked vault remains a valid dormant boot state, not proof of
   usable storage. After owner unlock, verify the authenticated protected
   storage paths, media backend, uncertainty recovery and lock/drain behavior
   before reporting functional readiness. Nothing here writes `PROVISIONED`.

The protected constructor intentionally defers vault I/O until unlock. It uses
the raw `SageClient` with `timeout=5` and `trust_env=False`; environment proxy
settings must not redirect the pinned local storage client. Do not perform a
constructor-time vault probe, substitute the legacy 8-second client branch, or
fall back to plaintext storage when protected initialization fails.

## Truthful planning/status fields

A plan can distinguish `operator_approved`, `candidate_config`,
`artifacts_verified`, `chain_binding_verified`, and `target_disk_verified`.
Keep `applied`, `backend_verified`, and `provisioned` false until their separate
real gates pass. A candidate's `enabled: true` is a requested SAGE setting, not
an observation that media is active. Do not mark a remotely unavailable unit
ready, or convert legacy identity attestations into protected-storage proof.

## Runnable staging assembler

`scripts/lantern/assemble-protected-config.py` consumes operator-exported,
secret-free base configurations, an existing public inventory, and a public
personal-identity manifest. It does not read the referenced identity key file.
Python 3.11+ and PyYAML are required (tests used PyYAML 6.0.2); nothing is
installed or downloaded by this command. Supply actual operator-selected
paths and quotas through the following variables; these are not quota defaults:

```sh
python3 scripts/lantern/assemble-protected-config.py \
  --unit-id "$UNIT_ID" --operator-approved \
  --chain-id "$VERIFIED_LOCAL_CHAIN_ID" \
  --sage-yaml "$EXPORTED_SAGE_YAML" --runtime-toml "$EXPORTED_RUNTIME_TOML" \
  --inventory "$UNIT_PUBLIC_INVENTORY" --public-identity "$UNIT_PUBLIC_IDENTITY" \
  --staging-root "$PRIVATE_STAGING_DIRECTORY" \
  --actor-bytes "$ACTOR_BYTES" --node-bytes "$NODE_BYTES" \
  --actor-objects "$ACTOR_OBJECTS" --node-objects "$NODE_OBJECTS" \
  --min-free-bytes "$MIN_FREE_BYTES"
```

The staging root must already exist, be owned by the caller, and have mode
0700. Use a new unit output directory; existing output is never overwritten.
The assembler writes `UNIT_ID/sage.candidate.yaml`, `lantern.candidate.toml`,
and `plan.json` (last), with directory mode 0700 and files 0600. Incomplete
output after an I/O failure is not ready; inspect it and choose a fresh staging
root rather than treating absence of the plan as success.

Both `private_media.enabled` and `sage.protected_storage` are **false** in
every output. Quotas and the explicit chain ID are assembled for later review.
An already enabled or conflicting input is rejected, not silently downgraded.
Endpoint, identity-path, domain, and unrelated settings are preserved, with
semantic equality checked after merging. Duplicate fields/tables, YAML
anchors/aliases, missing bindings, linked inputs, and recognized embedded
secret fields are rejected. Inputs must be exported configurations, never
private key/database files or live `.sage` state. Unknown settings are retained:
the caller must ensure exported inputs contain no unrecognized inline secrets.

Binding checks match unit ID, local-chain string, and public genesis/voice
fingerprints across supplied evidence; the voice fingerprint is recomputed
from its public key object. They establish **input consistency**, not independent
chain provenance or identity uniqueness. Unit B requires its own evidence;
there is no default copied from Unit A. Caller-forged consistent evidence is
not an attestation, hence `chain_binding_verified` stays false.

Optional artifact arguments are `--sage-manifest PATH --sage-binary PATH`,
`--runtime-wheel PATH`, and `--sdk-wheel PATH`. Unsupported/incomplete manifests
or failed checks reject assembly. SAGE checks ELF64 AArch64, binary hash, and
required source-input inventory, not signed build provenance or live API behavior.
Runtime checks reuse `verify-runtime-release.py`; SDK checks reuse the existing
builder's current-source payload verifier and check its embedded source hashes.
Missing artifacts are permitted only as unchecked, disabled candidates. These
checks perform no installation, build, network call, or target execution.

`plan.json` reports `mode=disabled-candidate`, `binding_inputs_consistent=true`,
per-artifact checks, input/output hashes and explicit blockers. It always reports
`applied=false`, `backend_verified=false`, `target_disk_verified=false`,
`chain_binding_verified=false`, `provisioned=false`, and `activation_required=true`.
No `PROVISIONED` marker is written. Neither `provision-lantern-unit.sh prepare`
nor its legacy `verify` action is changed into an activation mechanism.

## Required assembly tests

- Missing/false approval, missing budgets, invalid ordering/overflow, failed or
  insufficient target disk evidence: no enabled protected configuration emitted.
- Missing/unverified local chain ID, public-chain/domain/name substitution,
  or another unit's binding: reject, never derive a replacement value.
- Missing or mismatched SAGE/SDK/runtime capability evidence: no activation
  claim, even when the candidate YAML is syntactically valid.
- Generic defaults remain false; existing unrelated runtime fields survive
  assembly; no duplicate TOML table or unintended environment override.
- Locked construction performs no vault/network I/O; initialization waits for
  unlock and fails closed without plaintext fallback.
- Planning writes no keys, live configuration, services, or `PROVISIONED`;
  Unit B cannot inherit Unit A's readiness or identity evidence.

Source anchors: `software/runtime/src/sage_lantern/config.py` reads TOML
`sage.protected_storage` and `sage.chain_id`;
`software/runtime/src/sage_lantern/conversation_service.py` builds the deferred
protected store and owned SDK client. Those files are parent-owned and are not
modified by this documentation change.
