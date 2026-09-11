# Explicit owner contact reconciliation

Source-only; no daemon route, UI, device action, background recovery or radio send.
Uses the approved `contact_issuance` WorkflowStore kind and unchanged native/SDK
schema admission. Match the updated owner ledger reader with the new separate
`mesh_contact_reconciliation.py` module; old readers reject the new `abandoned`
state rather than silently continuing.

## Owner contract

Construct `OwnerContactReconciliation` with the existing ordinary signer/ledger,
real current profile, exact root broker/config/radio/access-epoch readers, and live
access guards. No identity loading or enrollment is performed. Kernel root UID
is necessary but is not itself browser-owner consent: later owner-auth forwarding
must bind these calls to the current unlocked owner session. Daemon wiring is held
for the separately reviewed receipt-uncertainty fix.

- `owner-contact-reconcile-preview`: exact `op, broker_id, epoch, action`.
  Action is explicitly `abandon` for reserved, `export` for issued, or
  `acknowledge` for already abandoned. Preview performs no writes and returns
  a random challenge plus a digest-bound plan, never card bytes.
- `owner-contact-reconcile-confirm`: exact `op, broker_id, epoch, challenge,
  plan_sha256, consent:true`. One-shot, at most 60 seconds old. Binds the exact
  current signed row, original issuance plan digest, workflow/card revisions,
  current profile (including public visibility and its revision), broker ID,
  access epoch, configuration digest, owner and radio key. Any change rejects.
- `owner-contact-reconcile-cancel`: exact `op, broker_id, epoch`. Clears pending
  approval without writes; does not roll back earlier uncertain operations.

Restart loses the pending challenge. Exceptions consume it. No timer, expiry,
startup inspection or retry loop authorizes mutation. A new preview and explicit
confirmation are required after every uncertain result.

## Abandonment evidence sequence

1. Create an encrypted audit row at deterministic UUID(ledger UUID, original
   workflow revision), expected revision **0**, containing the complete exact
   original signed reserved row and signed `abandon_authorized` decision/approval.
   This is authorization evidence, **not** a claim that abandonment completed.
2. Recheck live authority and exact original. CAS the owner ledger at its exact
   original revision to a signed `abandoned` outcome, preserving original plan
   digest and card revision, binding audit UUID/hash and the fresh approved plan.
   The workflow revision advances; the abandoned card revision is burned, not
   reused. A competing original finalization can win; reconciliation then fails
   without rewriting that issued state or pretending its authorization completed.
3. Create a separate deterministic completion row, expected revision **0**, with
   the exact signed abandoned ledger row. Verify it and the authorization archive
   before returning success. Both remain after subsequent issuance overwrites the
   active ledger. Consumers verify signatures, exact content and row revision 1.

These are create-only records in this module, not a claim that generic actor-owned
WorkflowStore storage is intrinsically immutable. Updates/tampering are rejected
by readers. There is no delete, rollback, evidence pruning or revision reset here.
The normal store quota applies; storage-full/corruption stays fail-closed and needs
separate explicit operational handling, not automatic deletion of evidence.

## Partial outcomes and next issuance

- **Authorization only:** state remains reserved. Fresh preview recognizes and
  pins the existing exact signed archive; fresh confirmation reuses those bytes
  rather than re-signing the archive, and attempts the still-pending CAS once.
- **Abandoned ledger, no completion archive:** new issuance is blocked. Fresh
  `acknowledge` approval may archive the exact already-signed outcome once. It does
  not replay abandonment or re-sign any contact card.
- **Completion committed, response lost:** fresh `acknowledge` verifies existing
  bytes without writes. New issuance may advance only after completion validation.
- **Already issued:** `export` approval returns only exact committed signed bytes,
  with fresh access/profile/config/current-row checks and normal signature,
  radio binding and expiry checks. No card is minted, renewed, resigned or written.
  Expired cards are not exported as valid cards; normal explicit new issuance is
  available from issued state.

Abandonment does **not** prove a card was never signed/exported elsewhere and is
not remote revocation or RF delivery evidence. A new card uses a strictly higher
revision and still requires separate issuance consent and peer adoption.

## Validation closure

`/private/tmp/lantern-owner-contact-reconciliation-review-20260908-v1` has the
approved native/SDK v2 candidate as its exact base, manifests, narrow patches and
commands. No further production native/SDK schema changes are needed. Tests cover
restart, cancellation, lock/epoch/profile/expiry/consent denial, concurrent
reconciliation, racing original finalization, unknown audit/CAS/completion writes,
exact export, evidence tampering and monotonic continuation. The tagged real
native HTTP/vault/SDK test also performs archive-write response-loss simulation
after an actual committed native write, fresh confirmation and retained evidence
after the next issuance. It is not a claim of actual network packet loss.
