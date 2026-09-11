# Authenticated public-memory commitment: review handoff

## Current source-only slice

Source search finds the compiled ceiling at app-v27, with app-v28 used only as
future-version test data. No production app-v28 gate was found. This is a source
observation, not a reservation against another team's concurrent changes.

Initial upstream files: `internal/store/public_memory_index.go` and its focused
test. Subsequent store-only staging work also modifies `badger.go` as described
below. No fork gate, supported version ceiling, running binary, image, node or
live store changes.

The index is a persistent 256-level sparse Merkle tree. Paths are domain-separated
SHA-256(record ID); leaves and internal/empty nodes have separate hash domains.
Canonical leaves use length-prefixed UTF-8 fields, an explicit fixed PUBLIC=0
byte and the exact 32-byte canonical content digest. Fields bind record ID,
original author key, policy principal, domain and lifecycle. Principal is not
assumed to be the original signing key. There are no local timestamps, inferred
revisions, SQL reads, content copies, keys or gateway signatures.

The canonical disclosure snapshot has **no memory_type field**. Although the
submission transaction carries type and SQL stores a projection, this leaf does
not prove that field and must never be labelled `fact`, `inference` or another
SAGE type based on that projection. Q&A eligibility requires the consumer to
hash the exact received content bytes against the proven ContentHash, then
strictly validate the supported Q&A content schema and its chain/author/domain
bindings. A valid memory branch alone never establishes Q&A/cache eligibility.
Proving SAGE memory_type would require a separate canonical commitment change.

## Interfaces

- `InitializeEmptyPublicMemoryIndex()`: requires a speculative consensus
  transaction and refuses any existing canonical memory or index keys. It cannot
  initialize an existing ledger, repair a lost index, or reset a failed build.
- `SyncPublicMemoryIndex(id)`: requires that same transaction; reads the exact
  canonical disclosure envelope from its snapshot. A nonpublic record removes
  its leaf. A public record missing required bindings or carrying unsupported
  co-commit metadata fails closed. It verifies the previous update path before
  mutation and uses existing Badger transaction poisoning/rollback primitives.
  Every synchronization error explicitly poisons the enclosing transaction,
  including validation/path failures before the first index write. Earlier
  canonical setters cannot commit with the old root after such a failure.
- `PublicMemoryRoot()`: returns the stored root, never an invented empty root
  when the root is absent. Supports the transaction view for future Finalize.
- `PublicMemoryBranch(id)`: reads canonical leaf and branch from one committed
  Badger snapshot, refuses speculative export and verifies correspondence before
  returning. No height or header claim is made by this low-level interface.
- `VerifyPublicMemoryBranch(proof, expectedRoot)`: bounded local membership
  verification against a caller-supplied root. That caller must establish root
  authenticity separately; this function does not establish consensus trust.
- `PublicMemoryCompositeHash(legacyHash, memoryRoot)`: domain-separated hash of
  two exact 32-byte roots. Not wired into AppHash selection.

One changed record costs at most 257 node writes and 256 sibling reads; unchanged
leaf updates perform no writes. Update scratch memory and proof size are bounded
independently of ledger size (the uncompressed branch contains 8192 hash bytes).
Sparse nodes at the changed path are reused/overwritten, not append-only history.
This trades storage/write amplification for a simple deterministic bound. It is
not a compressed Patricia index, and no whole-ledger performance claim is made.
The existing legacy consensus hash already scans state; production integration
must avoid accidentally rescanning all derived index nodes in that component.

## Required next slices — not implemented or activated

### Migration builder review chunk

`internal/store/public_memory_migration.go` now provides
`BuildPublicMemoryMigration(ctx, target) -> *PublicMemoryMigration`. Source and
target must be distinct, non-speculative stores; the target must be a fresh,
exclusively owned scratch database. Caller owns its lifetime and must never
install it as a serving store merely because it contains an index root.

The builder holds one source Badger read snapshot across canonical hash, sorted
record enumeration and all field reads. It writes at most 32 public records per
target transaction, including their exact canonical metadata (not content).
Private records are omitted. Source state is never written. The completion
marker is created only after all chunks and the root succeed, followed by Sync.
Any partial/failed target is terminal and cannot be reused. Exception safety
preserves the source; it does not promise deletion of failed scratch batches.

The result binds root, source v13-style AppHash and counts. Its MVCC read timestamp
is **local provenance, not a chain height**. It is not sufficient for activation
admission: a future promotion must verify the exact committed source generation
and retain the stage durably across replay. An atomic durable stage reference /
overlay is still needed; copying all nodes into the activation transaction is
specifically not an acceptable substitute. The builder's one-time work is
O(256 × public records + canonical-state scan), with bounded batch memory; it
does not make migration constant-time or yet enforce an activation time budget.

Tests cover multi-batch source immutability, private exclusion, terminal target
reuse refusal, failure after a completed batch without a completion marker, and
source mutation during migration without mixing snapshots. The index slice
under independent review remains unchanged by this chunk.

### Remaining production integration

### Store staging/promotion chunk and approval boundary

`public_memory_stage.go` adds `PreparePublicMemoryStage`,
`PromotePublicMemoryStage`, `ValidatePublicMemoryStage`,
`SyncPublicMemoryChanges`, and `ComputePublicMemoryAppHash`.

Preparation builds a fresh scratch index, copies only node hashes in 256-row
transactions into a reserved off-hash namespace, and publishes a completion
descriptor last. It binds expected activation height, source hash, base root and
stage digest. Promotion writes only a canonical descriptor and root inside the
existing speculative transaction; failures poison it. Missing overlay nodes
resolve to the immutable promoted stage. Explicit empty-node overrides prevent
deleted/private leaves from reappearing through fallback. This changes the
initial slice's empty-node deletion to explicit overrides.

Canonical typed writes/deletes now collect changed memory IDs on speculative
stores; the explicit sync method sorts/coalesces them. It is not yet called by
FinalizeBlock. The new composite computation excludes derived index nodes from
its legacy component, while the new staging namespace is excluded from all
historical hash algorithms. Tests pin unchanged legacy/v12/v13 hashes across
preparation, promotion rollback, changed-record synchronization and private-leaf
removal. Stage validation checks the promoted immutable digest and refuses lost
or corrupt data; no empty-root repair is performed.

The approval reviewer **rejected** the proposed `app.go` v28 activation/ceiling/
FinalizeBlock/AppHash patch because of chain-divergence risk. It was not applied;
the ceiling remains 27. No alternate application mechanism was used. The store
chunk is source-only and not sufficient for activation. Remaining review gates:
full-block resource amplification, activation snapshot admission, governance
predecessor/auto-vote checks, replay/restart integration, consistent export and
light-client verification. No H−1 proof endpoint or consensus/cache eligibility
is claimed. Explicit user approval is pending asynchronously. The denied boundary
is v28 governance/activation and supported-version ceiling changes, speculative
FinalizeBlock integration, and selection of the new AppHash rule in `app.go`.
That boundary must not be retried through another tool, path, or agent. Pending
approval, work is limited to authorized existing-store preparation tests/docs;
no dependent export is implemented or presented as a proof. No build,
deployment or live activation is included.

Additional store-only tests cover durable base-stage/override reopening, no
private-leaf resurrection after restart, lost-stage refusal without automatic
rebuild, and exact-source-hash mismatch poisoning earlier canonical writes.

1. Deterministic, bounded-chunk population for existing canonical ledgers, plus
   verified completion and restart integrity checks. Missing/corrupt state must
   refuse activation/export, not become an empty root. Repair requires rebuilding
   from a fenced canonical snapshot and comparing against the committed root.
   A stored root alone is not a whole-index integrity audit.
2. Coalesce changed memory IDs per consensus block; synchronize after the final
   canonical mutations, before AppHash. Wire all disclosure/lifecycle mutation
   paths and staged transactions. Define deterministic resource limits and test
   full blocks; no per-block rebuild of every memory leaf.
3. Governed app-v28 predecessor/activation/replay/clone/restart/readiness wiring.
   Preserve every preactivation AppHash byte. Activation A uses old hashing;
   new composite commitment begins at A+1. Define index-key handling in the new
   legacy component without changing any historical hash implementation.
4. Height-bound disclosure-checked export: pair the committed h snapshot and
   composite hash with signed header H=h+1. Either retain bounded historical
   snapshots or return unavailable for unsupported h; never relabel latest state.
   Latest-only low-level branches must not become an HTTP proof endpoint as-is.
5. Light-client checkpoint/validator-transition verification and offline bundle
   codec with durable rollback watermarks. LoRa requires bounded fragmentation or
   a reviewed sparse-branch compression scheme; 8 KiB is not one radio packet.

Required independent review: domain separation/encoding; canonical field origin;
storage amplification and block limits; path corruption behavior; Badger buffer
ownership; transaction poisoning; missing-index refusal; and no accidental use
of an untrusted root as BFT acceptance. Existing-ledger migration, historical
H−1 export, consensus acceptance and cache eligibility remain unavailable.
