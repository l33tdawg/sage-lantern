# Matched-release UPDATE: staging and bounded apply preflight, not activation

2026-09-06. Source/tests only; no Unit A/B access, service actions, real deployment
stage, secret/state copies, or active-release changes occurred.

`scripts/lantern/stage-appliance-update.py` is separate from the fresh installer.
The canonical release inventory now includes it. It never invokes a fresh
installer against an existing appliance and does not inspect or migrate legacy
conversation/history/queue data.

## Available command

Create an explicit owned 0700 staging directory outside live installation/state
paths, then stage an independently reviewed canonical OUTPUT:

```sh
python3 scripts/lantern/stage-appliance-update.py \
  --output "$NEW_CANONICAL_OUTPUT" \
  --manifest-sha256 "$REVIEWED_NEW_MANIFEST_SHA256" \
  --unit-id unit-a --stage-root "$PRIVATE_UPDATE_STAGING"
```

This command writes **only staging files** under a new random transaction UUID.
It verifies the exact reviewed manifest, canonical appliance profile, selection,
and current source closure using the existing provisioner/manifest checks.
Generic role filler is not a valid candidate. It repeats source verification
after copying; no download, build, activation or native operation is performed.

Optionally add `--rollback-output "$OLD_CANONICAL_OUTPUT"` and
`--rollback-manifest-sha256 "$REVIEWED_OLD_MANIFEST_SHA256"`. Both are required
together. The selected rollback must satisfy the supported canonical profile and
integrity verifier, but is deliberately not compared with today's source closure.
An older incompatible/stale one-off manifest is rejected rather than relabeled.
Omitting rollback permits candidate staging, **not future update admission**.

Inspect later without target actions:

```sh
python3 scripts/lantern/stage-appliance-update.py --inspect "$TRANSACTION_DIRECTORY"
```

Inspection verifies the append-only hash-linked journal, unit/transaction/reviewed
manifest bindings, metadata, and every sealed artifact. Partial staging reports
`complete=false` and reconciliation required. A complete stage reports
`activation_authorized=false`, `ready=false` and its actual rollback availability.

## Durable artifact/journal contract

- `candidate/` and optional `rollback/` contain independent byte copies of the
  matched payload, manifest and selection. No hardlinks/symlinks are accepted.
  Payload files are sealed 0444/0555; original deployment modes remain in the
  canonical manifest. Directory/file fsync precedes the corresponding journal
  record. Sealed copies use a separate verifier because canonical source modes
  intentionally differ from sealed modes.
- `journal/000001.json` onward are exclusive-created, fsynced records binding the
  unit, transaction UUID, reviewed candidate/rollback hashes, monotonically
  numbered revision and previous-record SHA256. Phases are `staging_started`,
  `candidate_sealed`, optional `rollback_sealed`, then `staged_no_authority`.
  A failure leaves the last durable record and partial files; no automatic wipe
  or resume/retry of installation is attempted.
- `stage.json` is read-only and its hash is bound by the terminal staging record.
  `rollback-artifacts.json` names/hash-binds the selected rollback artifact set,
  explicitly marking `installed_baseline_verified=false`, `restore_state=false`
  and `automatic_rollback=false`. This is not a snapshot or proof of Unit A's
  currently installed bits, and never includes private databases or keys.

“Immutable” here means exclusive-created, read-only sealed artifacts, checked
again by content hash—not a filesystem immutable flag or protection against root
or a malicious same-UID actor changing permissions. No staged directory is an
active application path. Nothing launches directly from it.

## Socrates aggregate-authority handoff

**Staging has no activation CLI, permissive callback, default drain receipt or
service restart path.** Its `--activate` is rejected. The separate apply consumer
described below also remains production-blocked; its existence is not activation.
Queue status is explicitly `not_inspected`; staging cannot promote that to zero.
The next orchestration layer must consume Socrates's actual aggregate authority,
not reinterpret a supplied JSON boolean as native evidence.

Required integration properties, pending the authority's exact schema:

1. Bind authority to this transaction and reviewed candidate plus observed
   installed/rollback baseline, native boot/process identities and relevant
   producer/vault epochs. Rehash artifacts and inspect effective installed units.
2. Acquire/retain the deployment and sleep/admission fences using the existing
   root coordination protocol, not by treating marker existence as drain proof.
3. Require positive bound auth/voice/community/storage/mesh drain, camera/kiosk
   and inference evidence, and verified inactive empty cgroups where required.
   Existing mesh DrainProof alone is not aggregate deployment authority.
4. Inspect all configured queue backends, including any legacy local journals.
   Unknown/untracked outcomes block activation. Preserve and reconcile them;
   do not delete, recreate, dispatch or claim them migrated during staging.
5. Only after those gates, journal and perform reviewed artifact switches. Keep
   rollback artifact-only; database/schema compatibility requires explicit review.
   No automatic old-binary restart after possible forward state changes.
6. Retain fences on uncertainty. Restart/release admission needs separate
   verified acceptance; a copied artifact, stopped PID, timeout or queued job
   is not completion.

The missing authority/orchestration remains a real deployment blocker. This
stage does not make the current stale Unit A one-off manifest deployable, infer
its baseline, authorize a rollback, or mark a unit PROVISIONED.

Tests use synthetic artifact trees and explicit mocks only at the upstream
release-validation boundary. They exercise actual copying, sealing, journal
fsync failures, restart inspection, hash tampering and partial-stage containment.
No test supplies or fabricates a native drain authority.

## Bounded apply consumer: implemented mechanics, blocked production entry

`scripts/lantern/apply-appliance-update.py` provides real read-only preflight for
an **existing-file UI-only patch**, not a full matched-release installer. It calls
the stage inspector, requires an independently reviewed candidate **and** rollback
manifest hash and matching staging unit, and rehashes the exact installed UI tree
against rollback artifacts. Unknown files, missing files, links, hardlinks,
untrusted writable directories and byte/mode mismatches deny preflight. It does
not read private databases, keys or queue contents, or execute staged code.

```sh
python3 scripts/lantern/apply-appliance-update.py --inspect \
  --stage "$TRANSACTION_DIRECTORY" --unit-id unit-a \
  --candidate-manifest-sha256 "$REVIEWED_NEW_MANIFEST_SHA256" \
  --rollback-manifest-sha256 "$REVIEWED_OLD_MANIFEST_SHA256"
```

Both sealed inventories must have identical paths. Only existing canonical UI
files may change, at most 128 files and 32 MiB per changed file. Release destination
metadata is never executed or used to choose target paths. Runtime/SDK/SAGE,
helpers, units, model, dependency archive and configuration changes are rejected;
there is no wheel installation, native build, deletion, new-directory creation,
state restoration or schema migration. The destination is fixed to
`/opt/sage-lantern/fleet-admin`; there is no CLI root-prefix or force override.

Successful inspection reports `installed_ui_baseline_verified=true`, but
`installed_baseline_verified=false`, `queue_status=not_inspected`, `ready=false`
and `activation_authorized=false`. It does **not** bind the entire observed
appliance or prove that the staging unit label identifies the installed device.
No concrete all-configured-backend/legacy-queue authority API was found in the
current source contracts. Consequently `--apply` explicitly denies **before**
creating its lock/journal or invoking native drain. No JSON receipt, command-line
boolean or positive callback default can clear these production blockers.

The internal copy composition uses actual `DeploymentDrain.acquire(...)` with
`verify_root_broker`, not a shape-only verifier. Its binding callback reruns the
stage inspector and checks the pinned seal and both reviewed manifest hashes.
It loads only the fixed installed suspend helper, checking its bytes against the
rollback inventory, and retains the drain owner through `prepare()` and `run()`.
Synthetic tests exercise this internal composition with an explicitly mocked
native acquisition boundary; they do not make it a supported activation API.

Implemented copy ordering, pending the missing production authorities:

1. Exclusive-create a retained transaction apply lock and hash-linked
   `apply-journal/000001.json` onward. Bind records to staging transaction, unit,
   manifest hashes and seal hash; record prepared drain identities separately.
2. Reobserve the entire UI baseline after drain, before any artifact mutation.
3. For each bounded file, assert live authority, verify old/source bytes, and
   fsync an intent naming old/new inventories and a random same-directory temp.
4. Reassert authority before exclusive temp creation; fsync copied bytes and
   directory. Recheck old/temp bytes and assert again immediately before atomic
   replacement. Fsync the target directory, rehash target, reassert authority,
   then fsync the matching done record. There is no multi-file atomicity claim.
5. Verify the resulting UI inventory and finish the single-use drain `run`.
   Record `artifacts_copied_fenced`, **not activation or restart acceptance**.
   Close ownership without clearing recovery fences or restarting anything.

Any exception leaves existing records, temp files and any already-replaced files
in place. There is no retry/adoption, cleanup, automatic rollback, old-binary
restart, fence release or PROVISIONED write. A missing done record means an
uncertain switch, even if the target looks new. Read-only journal inspection is:

```sh
python3 scripts/lantern/apply-appliance-update.py \
  --inspect-journal "$TRANSACTION_DIRECTORY/apply-journal"
```

It validates record chaining/order and intent/done correspondence against the
bound plan; it reports `reconciliation_required=true` even after recorded copy
completion. It does not reconstruct a live lease or certify current target bytes.

### Rolling-upgrade bootstrap remains separate

An old installed mesh daemon does not gain `verify-deployment-drain` because the
candidate contains that handler. Likewise candidate LLM/idle fence conditions do
not prove effective installed guards. Missing installed APIs, matching trusted
runner/runtime, broker dispatch or effective conditions require a separately
reviewed bootstrap and native acceptance **before** this admission can work.
Never replace the old broker/helper/guard first using this consumer to obtain the
very proof needed to authorize that replacement. There is no fallback to candidate
code, echoed JSON, mesh-only drain or temporarily weakened guards.

Tests in `scripts/tests/test_apply_appliance_update.py` use synthetic artifacts
with an explicit mocked upstream release-validation boundary and synthetic native
acquisition. Copying, installed-byte checks, journal hashing, failure containment
and filesystem operations are real temporary-filesystem exercises. No native
service, socket authority, SDK/vault, queue coverage or Unit A/B acceptance is
claimed. Full-release mapping, installed-unit/baseline binding, all-backend queue
inspection, canonical release inclusion of the new consumer, and separate
restart/release acceptance remain integration work outside this bounded path.
