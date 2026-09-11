# Consent-bound application delivery receipts

Source only. No provider activation, device IO, live key access or deployment.
The contact-import implementation in mesh_daemon.py is deliberately unchanged.

## Production path

`CommunityMeshProvider.from_config` attaches `MeshDeliveryReceipts` and the
root-owned `MeshActionAuthority.receipt_policy`. The existing root read-receipt
wrapper delegates delivery policy to it; read acknowledgement rules stay intact.

Inside the single serialized `MeshRuntime._poll` receive job:

1. Existing `MeshInbox.ingest` authenticates, assembles and durably stores the
   original message and signed local admission attestation. Partial/rejected
   frames and incoming delivery/read assertions never generate another receipt.
2. A live receive grant must carry `delivery_receipts_enabled=True` and
   `dispatch_enabled=True`. No user grant, HTTP boolean, firmware ACK or merely
   historical inbox read provides this authority. Default is false.
3. The helper re-verifies the immutable inbox attestation and exact original
   ID/digest/recipient, then resolves the current independently pinned sender
   contact against live full-key radio inventory.
4. The existing receipt sender queues a sealed, signed `delivery` envelope in
   its deterministic per-local-inbox/per-kind UUID slot. Issued time is the
   signed admission time; expiry is admission plus 3600 seconds. Duplicate
   frames therefore cannot replace the UUID, timestamps or sealed wire.
   The signed approval binds the exact typed root consent snapshot, including
   its revision, without adding a second consent journal or plaintext copy.
5. Only after confirmed queue state and live grant revalidation can the existing
   receiver clear its captured frame. Dispatch happens in a separate existing
   background job under current dispatch consent and the same approval binding.

This asserts **durable application inbox acceptance**, never human reading and
never canonical memory acceptance. Serial acceptance of the return envelope is
still only serial acceptance. The sender must authenticate and correlate that
return envelope before reporting `peer_delivery_asserted`.

## Failure and privacy semantics

- Lock/drain/lease/consent changes are checked during the worker and guarded CAS
  writes. If receive-while-locked is explicitly allowed, a receipt may queue
  while locked; dispatch still waits for unlock unless dispatch-while-locked was
  separately consented. No such flags are enabled by this patch.
- If queueing fails after durable admission, retain both original inbox evidence
  and captured frame. If the queue commit succeeded but its response was lost,
  the deterministic receipt row and sender uncertainty remain. No automatic
  retry or claim of no write is made. Capture-clear failure likewise preserves
  the already queued receipt and retained capture evidence when still present.
- A replay matching an already queued or serial-accepted receipt does not create
  or resend a new envelope. A `dispatching` receipt remains unknown: duplicate
  input cannot reset it, clear capture or authorize retransmission.
- Changed policy may leave an earlier queue blocked; it is not silently rebound
  to new consent. Existing explicit operator reconciliation remains necessary.
- This is not a transaction spanning inbox and receipt rows, nor a guarantee
  across destructive firmware dequeue and host persistence. Existing capture
  fencing and unknown-outcome records remain mandatory.
- No backlog scanning/backfill is added. Expired/missing approvals are not
  refreshed by replay. Read receipts still need actual session-bound render
  acknowledgement and explicit share-read consent; delivery cannot imply read.

## Integrated root configuration

After explicit ownership coordination, root configuration now exposes the
opt-in. Only `mesh_daemon.validate_config` and `HardwareRuntime.consent` changed;
the accepted import-contact method and IPC dispatch method are byte-identical.
Hypatia's separate pairing module and routes are untouched.

`delivery_receipts_enabled` is an optional exact boolean, default false when
absent, requiring both messaging and dispatch enabled when true. The root
forwards it by keyword to `MeshReceiveConsent`. No legacy config normalization
or rewrite occurs. Unknown keys, null, numeric and string values are rejected.
The existing preparation helper delegates to this same runtime validator, so
there is no duplicate schema to loosen. Canonical config hashes/lifetime pins
include the field when present. Neither receive/dispatch-while-locked nor read
consent is inferred or expanded. Obtain explicit owner consent before true.

Then package the matched runtime/SDK and rebind commissioning/startup through
the existing coordinated maintenance path. Preserve Hypatia's reviewed contact
import and Curie's vision configuration. Verify real independent A/B peer
identities/cards, firmware inventory and storage epoch before physical RF tests.
No source test is physical A/B receipt evidence.

Config/import integration: 40 tests passed, including actual root provider,
leased background receipt queue and independent read consent. Daemon/preparation
regressions: 150 passed. The integration test uses the supported 0.2-second
poll interval to bound synthetic fragmented reception; production polling is
unchanged. No activation or throughput claim follows from these tests.
