# Explicit public Share status refresh — source review

No deployment, provider activation, enrollment, or public disclosure is authorized by this change.

## Service contract

- `POST /community/shares/refresh`: exactly `id`, fresh UUID `request_id`, and `consent: true`.
- `POST /community/shares/inspect`: exactly `id` and the refresh `request_id`.
- Both use the existing current owner/session authorization. No proposal, source text, receipt, or authority supplied by the browser is accepted.
- Gateway refresh resolves the original proposal and submit request from the signed durable root Share inventory, verifies its receipt, and queues a signed status GET. Missing/uncertain submission evidence never becomes another proposal POST.
- Gateway inspect reads the queued request result without queueing another request. `observed_status: committed` remains `state: pending`, `trust_basis: pinned_gateway`; acceptance, consensus, and cache flags remain false.
- Direct-node refresh/inspect perform the existing guarded status GET against the protected receipt. Only the existing content/chain-bound trusted-node online observation can report accepted/challenged/deprecated. Neither path claims independent consensus verification or cache eligibility.
- Response keys are exactly `id`, `request_id`, `created_at`, `state`, `observed_status`, `trust_basis`, `acceptance_available`, `consensus_verified`, `cache_eligible`.

The root list carries canonical proposal metadata only to the authorized facade. The existing UI list shape stays unchanged. Pages remain limited to 20 entries and now also 48,000 encoded bytes, leaving room for the IPC envelope. Refresh resolves only entries in that current bounded page; absent entries fail closed. No pagination or automatic retry is added.

Post-dispatch uncertainty remains latched. Inspecting a result does not clear it or grant replay authority. Lock/restart clears ephemeral previews but not the signed durable Share inventory.

## HTTP and UI integration

`scripts/lantern/unit-a-voice-loop.py` now includes `/community/shares/refresh` in `COMMUNITY_MUTATIONS`: in-flight refresh work participates in drain accounting, and unknown outcomes retain the existing uncertainty latch. Inspect does not clear that latch.

The Shared knowledge screen offers separate **Request updated status**, **Check status request**, and **Reload saved submissions** actions. The first explains and explicitly consents to a status query, not another proposal submission. No timer queues or repeats refresh. The strict parser binds both IDs and preserves gateway versus direct-node semantics.

Request identities are retained in bounded page-process memory (128 entries) across view navigation and lock/unlock, not browser storage. New owner sessions must inspect afresh; old callbacks cannot publish their response under a new token. Uncertain requests remain inspect-only. A full browser reload loses this UI memory, not the root's durable records; this is not a durable reconciliation or retry authorization mechanism. Reloading the list never reuses an old displayed acceptance observation. Hiding/unmounting cancels outstanding UI requests, and hiding clears displayed observations.

## Required review before deployment

Ship the root metadata producer, facade, service, voice HTTP caller and UI as matched artifacts after independent review. This is not a backward-compatible standalone facade replacement against an older root list schema. No currently deployed A artifacts were changed.
