# Protected public workflow journal

`PublicWorkflowStore` adapts the existing `WorkflowStore` using only the existing
`public_proposal` kind. No file persistence, vault/key setup, provider activation,
canonical submission, polling, automatic retry or acceptance verification occurs.

## Operator wiring

Construct with `workflow`, `chain_id`, `domain`, `author`, `access_check`,
`drain_check`, and optional `max_records=256` (maximum 4096). Pass the adapter as
`CommunityService.protected_store`; pass its distinct `sdk_journal` view as
`CommunityPublic.journal`. Do not pass the HTTP view to the SDK journal.

The access guard must capture the original authorized session/generation for
each invocation. If a trusted dispatcher selects per-call guards dynamically,
it must never substitute a general unlocked flag. The existing WorkflowStore
also probes active protected storage and checks the storage epoch on each call.
No positive storage availability is cached.

The drain guard is a **different trusted lifecycle lease**, not a user session.
Fence all producers across all instances, join workers, scan before locking the
vault, then revoke this lease. It may permit protected-row decryption after user
admission is revoked, but never after vault lock. Sequential scans are not atomic
snapshots. Unavailable storage, expired guards, malformed rows or scan overflow
raise and deny clean drain. The returned count covers this bound public/HTTP
scope only, not mesh workflows or other configured public identities.

## Durable state and bindings

Row UUIDs deterministically bind storage actor/chain, public author/chain/domain,
namespace and original operation UUID. HTTP and SDK UUIDs cannot collide across
these namespaces. Every mutation uses the exact observed CAS revision; no
reservation is recycled, including definite-cancellation tombstones.

`prepare_public(proposal, created_at)` stores immutable canonical proposal bytes,
hash, provenance and timestamp. CommunityService calls it only after the exact
preview approval/session checks and HTTP reservation, before SDK submission.
Creating a preview never calls preparation. Preparation itself does not reserve
SDK submission or grant approval. Any preparation error prevents canonical send.

SDK `reserve(operation_id, content_hash)` requires the prepared row and performs
one CAS admission. SDK `complete(operation_id, PublicReceipt)` atomically records
the exact receipt in that same row. Consequently history survives a crash before
`record_public` or HTTP completion. `record_public` validates all proposal,
receipt and timestamp bindings and is idempotent only for identical provenance.
SDK and HTTP `complete` also accept the exact `{"cancelled": true}` tombstone.

`list_public(limit=21)` scans bounded retained rows and returns newest-first
`{proposal: PublicProposal, receipt: PublicReceipt, created_at}` entries, with
deterministic tie ordering. It never invents a creation date or an empty result
for unavailable storage. Prepared/unresolved/cancelled rows are not public history.
Duplicate memory IDs are rejected. Proposal size must fit WorkflowStore's 16 KiB
escaped JSON payload including metadata; oversized content fails before sending,
never truncates. This can be stricter than CommunityPublic's envelope limit.

## Uncertainty and remaining integration

An uncertain CAS write raises. In particular, a lost reservation acknowledgement
never returns permission to send. Reopening and reserving again returns false
if already reserved. `reconcile_public(operation_id)` returns only exact prepared
proposal/timestamp/reservation/receipt state, not canonical acceptance evidence.
Exact `get` serves HTTP recovery. Identical complete/record calls may reconcile
already-persisted results without writing again; no network outcome is guessed.

A reservation with no persisted canonical receipt remains unresolved. Recovery
must verify canonical evidence externally before completing it; this adapter does
not search the public ledger or resubmit. Preparation interrupted before SDK
reservation also remains conservatively pending: automated cancellation/recovery
is not supplied. HTTP owner fingerprints remain session-bound; a restart does
not authorize replay by a new session. Service uncertainty is still sticky.

Auxiliary rows are not public acceptance evidence. HTTP replay schemas exclude
accepted/verified public states; history supplies receipts only. CommunityService
must still call CommunityPublic.poll with its separately pinned online verifier
to derive lifecycle labels. Neither a journal receipt nor a committed workflow
revision establishes truth, chain inclusion or public reviewer acceptance.

This is an unactivated adapter. Native server capability, storage encryption,
lease wiring, recovery orchestration and public-identity readiness require
separate acceptance before any provider can be enabled.
