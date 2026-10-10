<!-- SPDX-License-Identifier: Apache-2.0 -->
# Proposed MPF2 receive-to-capture contract

**Protected integration remains unimplemented.** The current experimental Core source supplies a generic receive-commit API; Lantern has no qualified protected journal, private MPF2 service wiring or native provider for it. The public bundle still excludes private providers. Lantern owns protected storage, capture, consent and native lifecycle; Meshticulum owns generic crypto/flow interfaces. The dependency remains Lantern → Meshticulum.

MPF2 DATA replay acceptance must commit with an exact recoverable sealed record. Calling the current `ForwardSession.open(packet)` and then `BinaryCapture.complete(...)` does not meet that requirement.

## The current gap

At the pinned [Core cf64d62](https://github.com/l33tdawg/meshticulum/blob/cf64d623abb707c342524fac951b88cf0a1e63e6/src/meshticulum/privacy/forward_session.py), `open()` authenticates the MPF2 record, checks the binary-envelope shape, commits its receive counter and then returns sealed LM1/LM2 bytes. Lantern's [BinaryCapture](../../software/meshticulum-integration/src/lantern_meshticulum/sage/mesh_binary_capture.py) separately persists those bytes with a [WorkflowStore](../../software/meshticulum-integration/src/lantern_meshticulum/sage/workflow_store.py) compare-and-swap.

A crash or failed capture between those operations can leave a durable accepted counter with no captured bytes. Replaying the network record is refused, and persisting only the MPF2 ciphertext is insufficient when the non-resumable session keys are gone. Separate durable writes, or a second callback that runs after `open()`, do not remove this gap. This return-then-capture pipeline must remain unwired for the private mode.

## Implemented generic receive seam

The successor Core source increment adds constructor `receive_mode='commit'`, `receive_into(packet)` and a frozen `ReceiveCandidate`. It is experimental source work beyond the pinned cf64d62 baseline; a paired dependency/source-lock review is required before a candidate consumes it. Commit mode requires a callable `counters.commit_receive` before any claim and refuses `open()`. Default return mode refuses `receive_into()`. The mode is a local choice, without peer capability negotiation or storage attestation.

After full confirmation, authenticated DATA, binary shape and original liveness checks, Core passes the candidate to that same counter provider. It contains context, local role, original counter binding/counter, transcript and outer hash, original monotonic deadline, observation times and sealed bytes hidden in its representation. Only literal `True` followed by successful liveness checks returns success, without returning payload. DATA uses no separate `record_receive`; confirmations retain that path. Unknown outcomes, closing callbacks or interruption fence the incarnation while a possibly committed capsule remains the provider's recovery responsibility.

Core does not attest the callback's atomicity or native permission. A constructible candidate is not a bearer authorization; it does not supply a capture UUID, boot identity or private policy. The Lantern provider must independently bind those facts to the original prepared intention. There is no protected Lantern provider or new admitted format wired to this seam yet.

## Required protected transaction

The generic seam makes the authenticated sealed candidate available **before** the provider commits DATA acceptance. A production adapter must preserve callback fencing, original authority/deadlines and dedicated-key bindings. It must not expose decrypted application content or let callers bypass key confirmation. Core must not import Lantern or SAGE.

The Lantern adapter must join the following in one authoritative, protected, durable receive transaction:

- A create-only replay entry for the existing session-ID/local-role namespace and DATA counter. Changing context or mode must not evade an existing claim.
- The exact sealed LM1/LM2 bytes, their length/digest, the authenticated outer-record digest and the full context/transcript/peer binding needed to verify the recovery record.
- The original prepared capture operation UUID, expected sealed-object descriptor, authority binding, boot identity, epoch expiry, monotonic deadline and trusted observation metadata.
- A bounded pending disposition and authenticated commit receipt. A conflicting record under the same receive identity must fence the producer rather than overwrite it.

Every committed DATA replay entry must therefore identify recoverable sealed bytes or a durable final disposition. An entry that claims acceptance while its required capture/recovery record is missing is an integrity failure, not a retry opportunity. The transaction must validate the original intention and current native storage/owner/consent guards; uncertain or expired operations cannot publish success.

This can be a single backend transaction or one bounded protected record updated atomically. A local replay SQLite transaction and an independent REST capture CAS are two commit boundaries. SQLite atomicity covers changes inside its transaction; WAL does not make transactions across separate attached databases collectively atomic. Neither mechanism joins an unrelated backend RPC. [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html), [WAL constraints](https://www.sqlite.org/wal.html)

The receive journal may act as a durable outbox into the existing capture slot. That projection is a later, idempotent operation under the original UUID and descriptor. Its failure cannot lose the only copy of the accepted sealed bytes. The joint receipt must attest authenticated quarantine, without claiming inner-envelope admission or SAGE delivery. No ephemeral or traffic-key persistence is required to recover already committed sealed bytes; their protected storage remains a separate security requirement.

## Recovery and admission

| Failure boundary | Required result |
| --- | --- |
| Before the joint receive commit | No accepted DATA marker or recovery capsule. Preserve the original unresolved intention and prior transport cost; this document grants no fresh dispatch. |
| Joint commit succeeds but its return is lost, or a later guard/lease exit closes the producer | Fence the producer. A fresh authorized owner probes the exact receive identity and original operation, verifies the protected receipt/bytes, and reconciles; it does not reset the counter or create a new UUID. |
| Projection to BinaryCapture fails or has an unknown return | Keep the joint recovery record. Probe the recipient-signed capture state and retry only the matching original projection when permitted. |
| Inbox admission commits but its return/clear is lost | Probe the existing recipient-signed inbox admission and retain its disposition before clearing payload state, using the existing explicit reconciliation boundary. |
| Original authority expires, boot changes or consent is unavailable | No fresh admission. Current permitted reads may reconcile an existing signed admission or record an authorized rejection; otherwise retain the pending record and refuse. |

After protected capture, [MeshInbox](../../software/meshticulum-integration/src/lantern_meshticulum/sage/mesh_inbox.py) must independently open the inner envelope with pinned peers, verify its signature, enforce expiry/replay/consent and create or reconcile canonical admission. Captured bytes, a cryptographic receipt, a flow ACK and a SAGE admission are distinct outcomes. Application receipts must not precede their corresponding durable admission. Explicit payload clearance must preserve required replay claims, tombstones and prior attempt/airtime/stream-byte debt.

## Source evidence and open gates

[BinaryCapture source controls](../../software/meshticulum-integration/tests/integrations/sage/test_mesh_binary_capture.py) now refuse successful results when access/storage/peer/time callbacks, storage snapshots or lease exit close the producer. Nested WorkflowStore get/put callbacks also refuse remote dispatch after closure. Independent checks reproduced those refusals and retained committed-original-intention recovery. The callbacks use SDK MockTransport and modeled native leases; they do not qualify a production owner or implement the joint transaction above.

Core's separate receive-commit controls use real single-file SQLite transactions and modeled authority. Independent checks reproduced rollback between two inserts with neither DATA row persisted, and lost returns/post-commit closure with both replay acceptance and sealed recovery bytes retained. Reopening the fixture recovers those bytes without old session keys. The fixture is not an encrypted protected backend, original private intention or native provider.

Implementation still requires a reviewed atomic protected journal, explicit private service-format admission, bounded outbox/recovery and native lifecycle provider. Crash/power-loss, lost-return, concurrency, rollback, storage-generation, callback closure, original-expiry and boot-change controls must test the complete transaction. Key generation/purpose, storage/erasure, confirmation ordering/loss handling, private transport/performance and firmware/licensing/public-release gates remain open. No deployment, production mode or overall stronger-security claim follows from this design.
