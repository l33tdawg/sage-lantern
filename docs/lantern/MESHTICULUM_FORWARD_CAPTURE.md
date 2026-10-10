<!-- SPDX-License-Identifier: Apache-2.0 -->
# Experimental MPF2 receive journal and capture contract

**The protected receive journal and `BinaryCapture.import_forward` are implemented as experimental source and validated against a temporary encrypted SAGE backend.** Native private integration remains unimplemented: there is no qualified owner/consent provider, admitted MPF2 service format or private flow wiring. The public bundle excludes private providers. Lantern owns protected storage, capture, consent and native lifecycle; Meshticulum owns generic crypto/flow interfaces. The dependency remains Lantern → Meshticulum.

MPF2 DATA replay acceptance must commit with an exact recoverable sealed record. Calling the current `ForwardSession.open(packet)` and then `BinaryCapture.complete(...)` does not meet that requirement.

## The current gap

At the historical [Core cf64d62](https://github.com/l33tdawg/meshticulum/blob/cf64d623abb707c342524fac951b88cf0a1e63e6/src/meshticulum/privacy/forward_session.py), `open()` authenticates the MPF2 record, checks the binary-envelope shape, commits its receive counter and then returns sealed LM1/LM2 bytes. Lantern's [BinaryCapture](../../software/meshticulum-integration/src/lantern_meshticulum/sage/mesh_binary_capture.py) separately persists those bytes with a [WorkflowStore](../../software/meshticulum-integration/src/lantern_meshticulum/sage/workflow_store.py) compare-and-swap.

A crash or failed capture between those operations can leave a durable accepted counter with no captured bytes. Replaying the network record is refused, and persisting only the MPF2 ciphertext is insufficient when the non-resumable session keys are gone. Separate durable writes, or a second callback that runs after `open()`, do not remove this gap. This return-then-capture pipeline must remain unwired for the private mode.

## Implemented generic receive seam

The reviewed [Core 30da536](https://github.com/l33tdawg/meshticulum/blob/30da5360961b17fd50a8ba304d6bc82e58c8ca26/src/meshticulum/privacy/forward_session.py) supplies constructor `receive_mode='commit'`, `receive_into(packet)` and a frozen `ReceiveCandidate`. Both Lantern dependencies and the 61-input source lock pin this Core. Commit mode requires a callable `counters.commit_receive` before any claim and refuses `open()`. Default return mode refuses `receive_into()`. The mode is a local choice, without peer capability negotiation or storage attestation.

After full confirmation, authenticated DATA, binary shape and original liveness checks, Core passes the candidate to that same counter provider. It contains context, local role, original counter binding/counter, transcript and outer hash, original monotonic deadline, observation times and sealed bytes hidden in its representation. Only literal `True` followed by successful liveness checks returns success, without returning payload. DATA uses no separate `record_receive`; confirmations retain that path. Unknown outcomes, closing callbacks or interruption fence the incarnation while a possibly committed capsule remains the provider's recovery responsibility.

Core does not attest the callback's atomicity or native permission. A constructible candidate is not a bearer authorization; it does not supply a capture UUID, boot identity or private policy. The Lantern provider must independently bind those facts to the original prepared intention. The experimental Lantern journal consumes this seam in source and backend fixtures. There is no native private provider or admitted format wired to it.

## Implemented protected journal

[ForwardReceiveJournal](../../software/meshticulum-integration/src/lantern_meshticulum/sage/forward_receive_journal.py) uses one recipient-signed `mesh_inbound` WorkflowStore record. Its stable claim ID depends only on the existing session-ID/local-role counter binding; changing context, mode, operation UUID or dedicated key pins cannot evade a retained claim. Creation is the journal's sole original receive preparation, before handshake/flow. It persists the owner-supplied operation UUID, expected sealed length/digest, authority binding, boot and original epoch/monotonic deadlines with preparation observations.

That same record owns transmit reservations, confirmation replay counter 1 and one expected DATA counter 2. Capsule presence is DATA acceptance. Exact sealed bytes, full context, transcript, outer digest and observation times enter the same signed CAS; there is no later DATA replay write or capture RPC. Conflicting/unknown writes fence the producer and retain committed state. The provider allows one expected DATA per original intention/session and does not rotate sessions, renew deadlines, refund counters or clear prior transport debt.

Live instances remember their highest validated/confirmed revision and canonical body. Older signed rows or different signed content at the same revision fence that incarnation. This does not prove protection against database restoration after a fresh process starts. Claims and capsules are retained; deletion, backend restoration and eventual final disposition remain separate review gates.

The native caller supplies the exclusive lease and current access/storage/peer/dispatch permission guards. This new interface requires permission observations both inside the lease and after exit; ownership and current permission are independent checks. A callback that requires a held lease will fail closed after exit and is not automatically compatible. Storage/time observations finish before the final permission guard, so callbacks cannot revoke read permission and still publish a stale result. No production native provider has been qualified against this contract.

A fresh authorized instance can read the signed quarantine through `recover()` without old session keys. It preserves the original preparation, observation, UUID, descriptor and deadlines. Permitted historical reads can occur after original expiry or boot change; they grant no fresh dispatch or application admission. `ReceiveRecovery` is a constructible data object, not bearer authority, signed import evidence or a native attestation. The capture importer therefore reads and verifies the actual protected journal instead of accepting that object or caller-provided bytes.

## Implemented capture import

[`BinaryCapture.import_forward`](../../software/meshticulum-integration/src/lantern_meshticulum/sage/mesh_binary_capture.py) requires a fresh, unclaimed `ForwardReceiveJournal` validator with the exact same WorkflowStore, local recipient/signing identity, current boot and lease factory as the capture owner. One shared lease protects the operation; the validator does not acquire a second lease. Both objects stay busy through final observations and permission guards, fencing closing or caught reentrant callbacks. Current read-permission checks must work inside the lease and after exit.

Import performs an actual protected GET, verifies the recipient's journal signature, context, intention and committed capsule, and checks current storage/peer/read permission. It accepts neither a `ReceiveRecovery` object nor a caller-supplied packet. The journal remains the sole authoritative original intention created before handshake/flow. Import does not call `BinaryCapture.prepare()` or stamp a new preparation.

The capture preserves the original operation UUID, sender, sealed-object length/digest, authority, epoch/monotonic deadlines and original boot. It also retains original preparation and observation times: epoch preparation/capture fields and monotonic provenance fields come from the signed source. Historical quarantine can be imported after expiry or a boot change when current protected reads are permitted. A separate `process_pending()` first reconciles an existing signed inbox admission; otherwise it rejects expired or changed-boot work instead of creating fresh admission. Import alone does not open the inner envelope, project a private message or issue a delivery/read receipt.

Every replay import re-reads the actual signed source. Capture provenance retains the original journal revision/body hash, context/transcript/outer hashes and original intention, plus the latest observed source revision/body hash. A legitimate transmit-counter advance may update that monotonic observation while preserving the original import proof and immutable capsule facts. Older source rows or same-revision hash changes are refused against the retained observation. This does not prove resistance to restoration of both protected records or a native rollback attack.

Successful processing carries that provenance into `last_completed`; acknowledgement clears the retained capture payload while preserving the signed provenance/tombstone. Re-importing the same settled operation does not resurrect it. An unrelated outstanding intention cannot be replaced. Import uncertainty or later admission uncertainty retains the authoritative source for explicit recovery under the original UUID. **The forward journal is never cleared by import, processing or acknowledgement.**

## Required protected transaction

The generic seam makes the authenticated sealed candidate available **before** the provider commits DATA acceptance. A production adapter must preserve callback fencing, original authority/deadlines and dedicated-key bindings. It must not expose decrypted application content or let callers bypass key confirmation. Core must not import Lantern or SAGE.

The Lantern adapter must join the following in one authoritative, protected, durable receive transaction:

- A create-only replay entry for the existing session-ID/local-role namespace and DATA counter. Changing context or mode must not evade an existing claim.
- The exact sealed LM1/LM2 bytes, their length/digest, the authenticated outer-record digest and the full context/transcript/peer binding needed to verify the recovery record.
- The original prepared capture operation UUID, expected sealed-object descriptor, authority binding, boot identity, epoch expiry, monotonic deadline and trusted observation metadata.
- A bounded pending disposition and authenticated commit receipt. A conflicting record under the same receive identity must fence the producer rather than overwrite it.

Every committed DATA replay entry must therefore identify recoverable sealed bytes or a durable final disposition. An entry that claims acceptance while its required capture/recovery record is missing is an integrity failure, not a retry opportunity. The transaction must validate the original intention and current native storage/owner/consent guards; uncertain or expired operations cannot publish success.

This can be a single backend transaction or one bounded protected record updated atomically. A local replay SQLite transaction and an independent REST capture CAS are two commit boundaries. SQLite atomicity covers changes inside its transaction; WAL does not make transactions across separate attached databases collectively atomic. Neither mechanism joins an unrelated backend RPC. [SQLite atomic commit](https://www.sqlite.org/atomiccommit.html), [WAL constraints](https://www.sqlite.org/wal.html)

The receive journal acts as the recoverable source for the later capture import. That import is a separate CAS under the original UUID and descriptor; its failure cannot remove the authoritative accepted sealed bytes. The signed journal and capture provenance establish quarantine, without claiming inner-envelope admission or SAGE delivery. No ephemeral or traffic-key persistence is required to recover already committed sealed bytes; authenticated protected reads and the recipient identity remain required.

## Recovery and admission

| Failure boundary | Required result |
| --- | --- |
| Before the joint receive commit | No accepted DATA marker or recovery capsule. Preserve the original unresolved intention and prior transport cost; this document grants no fresh dispatch. |
| Joint commit succeeds but its return is lost, or a later guard/lease exit closes the producer | Fence the producer. A fresh authorized owner probes the exact receive identity and original operation, verifies the protected receipt/bytes, and reconciles; it does not reset the counter or create a new UUID. |
| Capture import fails or has an unknown return | Keep the joint recovery record. A fresh unclaimed validator re-reads the actual signed source and capture state; retry only the matching original import when current reads are permitted. |
| Inbox admission commits but its return/clear is lost | Probe the existing recipient-signed inbox admission and retain its disposition before clearing payload state, using the existing explicit reconciliation boundary. |
| Original authority expires, boot changes or consent is unavailable | No fresh admission. Current permitted reads may reconcile an existing signed admission or record an authorized rejection; otherwise retain the pending record and refuse. |

After protected capture, [MeshInbox](../../software/meshticulum-integration/src/lantern_meshticulum/sage/mesh_inbox.py) must independently open the inner envelope with pinned peers, verify its signature, enforce expiry/replay/consent and create or reconcile the locally signed inbox admission. Captured bytes, a cryptographic receipt, a flow ACK, inbox admission and canonical SAGE commit are distinct outcomes. Application receipts must not precede their corresponding durable admission. Explicit payload clearance must preserve required replay claims, tombstones and prior attempt/airtime/stream-byte debt.

## Source evidence and open gates

[BinaryCapture source controls](../../software/meshticulum-integration/tests/integrations/sage/test_mesh_binary_capture.py) now refuse successful results when access/storage/peer/time callbacks, storage snapshots or lease exit close the producer. Nested WorkflowStore get/put callbacks also refuse remote dispatch after closure. Independent checks reproduced those refusals and retained committed-original-intention recovery. The callbacks use SDK MockTransport and modeled native leases; they do not qualify a production owner or implement the joint transaction above.

Core's separate receive-commit controls use real single-file SQLite transactions and modeled authority. They prove their fixture transaction boundaries, not a native protected backend or private policy.

[Lantern journal controls](../../software/meshticulum-integration/tests/integrations/sage/test_forward_receive_journal.py) use actual MPF2 crypto and the signed SDK adapter with a modeled backend/native policy. They cover joint commit/lost return, create-only claims, descriptor/context refusal, replay, competing CAS, live revision rollback/equivocation, callback closure/reentry, lease-exit and final-observation revocation, storage generations and original preparation/deadline recovery. Independent source review reproduced the final snapshot/clock refusals and preserved capsules.

[Capture-import controls](../../software/meshticulum-integration/tests/integrations/sage/test_forward_capture_import.py) add 37 cases for original-fact preservation, shared-lease/fresh-validator enforcement, actual source re-read, historical rejection, provenance/tombstone retention, legitimate source advancement, callback fencing and uncertain import/inbox recovery. The bounded import/capture/journal suite passes 170 tests; the full Lantern integration suite passes 659 with one expected skip. These source controls use modeled backend/native policy.

The final [finite encrypted backend fixture](../../software/meshticulum-integration/tests/interop/run_backend_fixture.py) passes 12 checks against temporary real SAGE REST/SQLite/vault with the compatible SDK. Actual MPF2 journal normal/lost-return cases and capture import normal/lost-HTTP-PUT-return cases preserve the original inbox admission, replay and acknowledgement. Main/WAL files contain no fixture plaintext canary. All 91 recorded Python/fixture inputs remained unchanged during the final run. The backend process stayed live; no backend restart, physical power loss or network disconnect is claimed. Native lifecycle/consent, boot inputs and sender counters remain fixtures.

Implementation still requires a native owner/consent provider, explicit private service-format admission and flow wiring, plus qualification of the complete disposition/recovery lifecycle. Professional cryptographic review, independent key generation/purpose and secure erasure, native rollback/crash/power-loss/backend-restart behavior, confirmation ordering/loss handling, private transport/performance and firmware/source/licensing/clean-history/public-release gates remain open. No native deployment, private production mode or overall stronger-security claim follows from this source and fixture evidence.
