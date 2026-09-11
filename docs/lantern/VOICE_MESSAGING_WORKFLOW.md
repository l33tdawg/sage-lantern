# Voice messaging workflow — proposed

2026-09-05. Design only; **not implemented or verified on Units A/B**. Lorentz
owns the actual SAGE SDK/directory/transport audit. Route names, crypto formats,
directory authorization and receipt capabilities remain integration gates, not
assumed APIs. This document authorizes no federation, transmission or key changes.
The photo transport audit remains frozen in `SHARED_PHOTO_TRANSPORT.md`.

## 1. Compose and correct without a keyboard

- Choose **Private message**, then tap a large onscreen **Record** button to
  dictate and **Stop** to finish the segment. Physical push-to-talk is an
  optional equivalent, never required. Use STT only: no LLM rewrite, translation, factual
  completion or recipient inference. Show recognition errors for explicit review.
- Each explicit recording segment becomes a paragraph; **Add paragraph** starts
  another. Silence detection must not silently send or finalize the message.
  Recording controls are contextual and must not trigger the ordinary answer flow.
- Tap a paragraph, then **Edit**. Re-record into a temporary replacement; show
  old and new text with **Use replacement / Retry / Cancel**. Only the selected
  paragraph changes. Preserve the old version for **Undo** before send; failed
  transcription or interruption leaves the original untouched. Never replace
  the full draft implicitly. No hidden keyboard or keyboard-dependent recovery.
- Provide large touch targets, readable scrollable text and a visible selected
  paragraph. Suggested 640×480 targets: ≥20 px body type and ≥48 px controls,
  subject to physical-device validation, not accessibility compliance claims.
  Bound recording duration, draft size and STT work; reject over-limit content
  visibly rather than truncating. Discard raw audio after the edit decision.

## 2. Find and explicitly confirm a recipient

Dictate a directory search separately from message content. Search only the
authorized SAGE federated directory and return identities currently permitted
to be findable. No private-node enumeration, nearby-radio identity harvesting,
automatic public registration or implied permission to message everyone found.
Searching exposes the search term to that service; explain this boundary.

Display result name plus a stable identity discriminator and verified directory
provenance. Duplicate names require explicit selection; never select the first
match or use display name as a key. Final **Send private message** review shows
the selected identity and exact full text. Naming/renaming a device is not
directory registration, identity rotation or a delivery address change.

Cache only a verified recipient identity, authenticated messaging key/version,
directory authority/proof, verification/expiry time and authenticated binding
to a radio destination when the transport supports one. Radio labels and a
strong signal are not that binding. Show cached/stale status and allow fresh
verification; cached discovery is neither current findability nor online proof.
Refresh routes independently: pin the authenticated recipient, **not relays**.
Directory trust and privacy opt-out semantics must be established by the SDK
audit before this flow is enabled.

## 3. Deliberate send and durable offline outbox

After explicit confirmation, assign a stable message ID and seal the exact
reviewed text for that recipient using the supported authenticated encryption
protocol. Durably store the immutable encrypted payload and digest before
showing **Queued**. Protect local drafts, queue metadata and receipts at rest as
well; radio/link encryption alone is not end-to-end or at-rest protection.
Do not invent a crypto scheme or assume a private SAGE vault encrypts sidecars.

Retry the same sealed message bytes and logical ID within bounded attempts,
expiry, storage and airtime budgets. Persist recipient-side deduplication across
restart. Fresh transport authentication/nonces and changing route wrappers may
surround those bytes; do not replay an expired signed HTTP request. Reconcile
ambiguous outcomes before retrying any non-idempotent upstream operation.
Editing after sealing creates a newly reviewed message, not mutated retry bytes.

Keep attempt history separate from immutable content. Authenticated receipts
must bind sender/recipient, message ID, digest and outcome. Preserve the outbox
item on failure or rejection with a retryable/terminal reason; no automatic
deletion on a carrier ACK. Proposed labels:

| Label | Required evidence |
|---|---|
| Draft | Local editable text; no send authorization |
| Queued / waiting for route | Durable local admission, not peer reachability |
| Carrier accepted | Authenticated transport acknowledgement only |
| Received | Intended recipient durably accepted the exact message |
| Read | Separate recipient-authorized read receipt, if supported; otherwise unknown |
| Failed / expired / blocked | Verified outcome or explicit local policy, not fabricated delivery |

Avoid an ambiguous standalone “Delivered”; if retained, define it as recipient
receipt, never radio transmit success. Read receipts should be optional; reading
an item must not silently grant a new disclosure permission.

## 4. Lock, identity changes and privacy boundaries

- Lock clears draft text/recipient from the display, stops capture and invalidates
  in-flight STT/edit/send callbacks. No queued UI event may authorize a send after
  lock. Keep a draft only under an explicit encrypted-draft retention policy;
  otherwise discard it visibly on unlock. Never silently persist plaintext.
- Proposed first implementation pauses dispatch while locked. Already transmitted
  bytes cannot be recalled; show uncertain outcomes and reconcile after unlock.
  Resume only previously authorized immutable messages, never an unsent draft.
- Key rotation, revoked binding or blocked identity pauses affected sends. Verify
  supported authenticated rotation continuity; otherwise require recipient
  re-selection and confirmation. Never silently re-encrypt an old queued message
  to an unverified new key. Freeze/reconcile the old ID before any replacement.
- Discoverability opt-out removes discovery eligibility; it is not automatically
  key revocation or a message block. Apply the audited policy separately for
  cached contacts, expiry and blocks. Offline revocation knowledge is limited:
  do not claim a cached record proves current permission or online presence.
- Private messaging is separate from **Share knowledge** public candidates,
  public intake and semantic commitment. Neither Send nor Receive publishes
  content, writes an agent task, commits a memory, grants public subscription or
  exports photos. User inbox/outbox is not the agent's durable task inbox.

## 5. Bounded implementation gates

1. Lorentz confirms exact directory visibility/auth scope, stable identities,
   key/address binding and rotation/revocation semantics; unsupported fields or
   capabilities remain unavailable, not guessed.
2. Prove STT-only multi-paragraph editing, cancel/undo, failed recognition and
   lock during recording/review/send. Only the reviewed paragraph changes and
   no unsolicited send occurs. Validate Arabic transcription separately.
3. Synthetic A→B and B→A private text test: duplicate names, explicit identity
   confirmation, exact text, bounded offline retry, lost ACK and reboot dedup.
   Test IP first if available; do not claim LoRa until that path is measured.
4. Test unknown keys, changed/revoked bindings, stale directory data, opt-out,
   block, full disk, expiry and locked dispatcher. Preserve truthful outbox
   outcomes and prove no plaintext/private/public-plane leakage.
5. Require distinct carrier/received/read evidence. If the SDK cannot provide
   authenticated recipient receipts or persistent idempotency, expose that gap
   and block stronger delivery claims rather than synthesizing acknowledgements.
