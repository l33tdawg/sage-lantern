# Durable mesh outbox: source integration contract

Implemented in `software/runtime/src/sage_lantern/mesh_outbox.py`; no activation,
artifact rebuild or deployment is performed by this module. It uses the existing
ordinary SAGE `AgentIdentity` and its in-memory NaCl key. No key creation, file
reads, key conversion, token persistence, radio setup or worker loop.

## Public API

Construct `MeshOutbox(store, identity, sender=Peer(...), guard=..., verify_contact=...,
clock=...)` with the real `WorkflowStore` backed by its pinned `MessageStorageProbe`.
The clock returns positive integer Unix seconds. `guard(OutboxAction)` must grant
the exact operation under the current exclusive user session. The trusted contact
callback returns freshly verified `VerifiedMeshContact`, bound to the recipient,
full radio key and current consent policy. Merely constructing that dataclass is
not verification. No manual readiness flags substitute for either callback.

Every operation takes `access_check=...`:

- `queue(message_id=..., recipient=..., radio_public_key=..., content=...,
  approval_binding=..., consent_binding=..., issued_at=..., expires_at=...)` seals
  once and CAS-creates revision 1. Reusing an existing UUID never reseals/overwrites.
- `dispatch_one(message_id, device=..., radio_timestamp=...)` inventories the actual
  radio, checks full-key/prefix uniqueness, commits `dispatching`, then dispatches
  exactly one fragment. Successful serial acceptance commits the next fragment
  index or `awaiting_receipt`. Neither radio ACK nor serial acceptance means delivery.
- `retry_decision(message_id, expected_revision=..., decision_binding=...)` is an
  explicit guarded decision for `dispatching`, preserving original wire, UUID,
  fragment index and radio timestamp. Guard must fence the original worker across
  processes; CAS cannot retract already-admitted RF. It is not automatic recovery.
- `inspect(message_id)` verifies stored proofs and returns progress only.
- `read_sent(message_id)` verifies creation approval, progress and retained receipt
  proofs before returning original content in a repr-hidden `SentMessage`.
  Returned text is untrusted user content, never instructions or HTML.
- `ingest_receipt(message_id, wire=...)` opens the sealed receipt with our existing
  key, verifies its sender signature, correlates reversed qualified peers, original
  UUID/digest and issuance order, persists replay binding, then CAS-promotes state.

## Signed data and encrypted storage

All rows use the real encrypted SAGE workflow API, journal kind `mesh_outbound`.
The canonical outgoing record ID is the original message UUID. The current schema
requires original text plus two proofs; old staged sealed-only rows fail closed.
There is no migration, automatic schema reset or re-signing to bless old rows.

Creation approval permanently signs `{message_id, approved}` using SDK
`sign_request("LANTERN-OUTBOX-APPROVAL", "/sage-lantern/mesh-outbound/v1", body)`.
Approved data includes original text, recipient/sender, radio key, consent/approval
bindings, issuance/expiry, ciphertext wire and its SHA-256, and the original signed
envelope-body digest. The digest is recomputed from the original text/peer/time
fields on every validation. The approval proof is preserved byte-for-byte across
progress changes; reads never generate replacement approval signatures.

Every revision also has a `LANTERN-OUTBOX` progress signature binding the complete
body and exact journal revision. All signatures use the SDK's actual timestamp/
nonce framing, with distinct non-HTTP domain-separation methods. Progress updates
verify the unchanged creation proof before signing a new progress revision.
Tampered ledger text/wire/state is not authorized merely because a JSON field says
approved. Confidentiality at rest comes from SAGE encrypted storage, not signatures.
The test HTTP server uses synthetic plaintext SQLite to model CAS, not to prove
SAGE encryption. No local plaintext copy is introduced in production.

## Receipt replay and promotion

Replay reservation ID is UUIDv5(namespace
`f1d66cf1-379c-48bb-a90e-d3a75f208e61`, canonical ASCII JSON of
`{sender: qualified_receipt_peer, receipt_id: receipt_uuid}`). These control rows
also use `mesh_outbound`; consumers scanning journal rows must distinguish payload
body schema `sage.lantern.mesh-receipt-replay.v1` from outbound records. Never
present replay control rows as messages. They are immutable revision-1 records,
signed under `LANTERN-OUTBOX-RECEIPT-REPLAY` and bound to our receiver, receipt
body digest, original UUID and original digest. Do not delete/reuse them while
their replay protection is required. Store capacity failure is unavailable, not
permission to skip replay protection.

Before allocating a UUID replay row for promotion, a fixed per-original/per-kind
CAS slot pins the first verified receipt identity and digest. Its ID is UUIDv5 of
the same namespace and canonical JSON `{schema: "sage.lantern.mesh-receipt-slot.v1",
receiver: our_qualified_peer, original_id: original_uuid, kind: "delivery"|"read"}`.
The immutable revision-1 slot carries the replay binding plus these fields, signed
under `LANTERN-OUTBOX-RECEIPT-SLOT`. A conflicting receipt cannot replace it, even
after restart or a failed promotion. At most two slots and two UUID replay rows
are newly allocated per original, including concurrent competing workers.

Slot reservation, UUID reservation and promotion are three CAS commits, not a
fictional multirow transaction. New same/lower-rank assertions first undergo
genuine signature/correlation checks and an existing-UUID binding check, but
allocate no rows. They are discarded as redundant, not durably accepted events.
Previously reserved UUIDs still reject changed content. A never-reserved discarded
UUID does not acquire a permanent global replay binding merely by being observed.

A crash after reservation leaves the original unpromoted; re-submit the same
receipt/original UUID to reconcile. A committed promotion with lost response is
reconciled by loading that exact original. Same qualified receipt UUID and signed
content is idempotent even if sealed ciphertext is randomized again. Changed
content under that UUID—including reuse for another original—is rejected.
There is no automatic retry or replacement UUID after uncertainty.

`delivered` means a genuine recipient-signed assertion was verified AND persisted.
`read` means its genuine signed read assertion was verified AND persisted; it does
not prove human comprehension. No polling, claiming, radio ACK or local decode
generates a read receipt. Higher state wins monotonically; delivery after read
cannot downgrade. At most two promotion receipts (delivery and read) are retained
with their verification times and sealed wire. Their cryptographic evidence is
revalidated on reads. Additional same/lower-rank assertions allocate nothing.
Existing rows from earlier builds are not deleted or silently migrated; the bound
applies to allocations through this implementation, not retroactive quota repair.

New receipts must be current under their own signed lifetime; the original message
may already have expired. Historical persisted evidence remains valid after its
receipt expires. Exact retained-wire replay can use its recorded verification
time; newly resealed expired input is conservatively rejected. The last local
fragment index is not fabricated by receipt promotion: another path or an uncertain
prior attempt may have delivered the message.

## Lifecycle / remaining integration responsibilities

All public operations share a condition-based exclusive admission gate.
`close(timeout=0..30)` permanently closes admission, makes live guards fail, and
joins this instance's active operation within the bound. It returns false while
work remains or after sticky observed journal/RF uncertainty. Closing does not
delete records, unlock a vault, unload keys, cancel a physical radio queue, or prove
other processes drained. A new instance is not reconciliation evidence for an old
one. Root still needs the original-worker/service fence and actual RF drain policy.
Dependencies must obey bounded IO; close cannot kill an uncooperative callback.

Private text now exists in the encrypted auxiliary journal for sent-history reads;
the lifecycle/UI owner must wire notice, retention/deletion and authenticated reads.
Deletion must not silently erase replay/uncertainty evidence. No UI or retention
defaults are changed here. Rebuild source artifacts only after parent freeze and
approval; tests are not a deployment or complete Unit A/B activation claim.
