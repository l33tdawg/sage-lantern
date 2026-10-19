# Message transport router: radio first, SAGE fallback, bounded retry

Status: design, 2026-09-12; **partially implemented 2026-09-13** — the
owner-approved window is now 24 hours (see "Window change, 2026-09-13" below).
The router itself (radio → SAGE → bounded retry) is still not implemented and not
deployed.
Owner direction (2026-09-12): "if we cannot deliver over radio, we should try and
fall back to sage and then if that fails - then we periodically try (1 time an
hour or something if the device is on)". Refined the same day: "think of it like
icq bro - you send a message if your friend never comes online it just never
delivers but its never 'lost'".

## Product rules

1. Prefer radio when a verified radio contact exists and the dispatch can
   complete.
2. Otherwise fall back to the SAGE network: agent-to-agent from the unit's own
   node identity to the peer's `agent_id@chain_id`, both already carried in
   `/etc/sage-lantern/mesh-runtime.json`.
3. If neither transport completes, retry periodically while the unit is awake.
4. Never deliver the same message id on both transports (dedupe by
   message/request id, and never re-send after any transport has written).
5. Receipts and read states stay transport-agnostic. No delivery claim without
   the peer's own signed evidence; a radio ACK, a serial write or a SAGE HTTP 200
   is not delivery.

### Durable-pending (ICQ) model — owner direction

A message the owner sends is a **durable local artifact**, not a one-hour job:

* it stays in the outbox as `not delivered yet` for as long as it takes, and is
  retried whenever a transport can reach the peer (hourly while awake, with
  backoff);
* "never lost" means the row is never discarded because a window lapsed — expiry
  of a *route credential* (peer card, radio key, node reachability) only defers
  the next attempt;
* the only honest terminal outcomes are `delivered`/`read` **with the peer's own
  signed evidence**, or an explicit owner action (cancel/delete);
* the UI must never show `sent` for something no transport confirmed. It shows
  `not delivered yet` (with the transport it will try next), which is exactly what
  ICQ showed for an offline contact.

## Building blocks that already exist (repo, not all deployed)

* `community_messaging.py` — `CommunityMessaging` plus the `SageMessagingClient`
  protocol (`pipe_inbox_history`, `pipe_outbox`, `lookup_agents`, `pipe_resolve`,
  `message_send(to_agent, payload, idempotency_key, intent, ttl_minutes)`).
  Prerequisites-gated, disabled by default, deliberately same-node-only today.
* `mesh_outbox.py` — sealed approval (content, ciphertext wire, hashes), CAS
  progress, `dispatch_one` for one RF fragment, signed peer receipts, states
  `queued → dispatching → awaiting_receipt → delivered/read`, plus the explicit
  owner `cancelled` terminal (2026-09-21) for a row that never became confirmed
  evidence: it stops this unit's work and keeps the row visible, and a genuine
  peer receipt still promotes it because the peer's signed word outranks a local
  cancel.
* `mesh_daemon.py` — background dispatch job, fail-closed `_uncertain` latch,
  single-use radio opener.
* `community_service.py` — `/community/messages/send` sets
  `expires_at = min(now + 3600, card.expires_at)`.
* Chat UI already renders a route label (`apps/lantern-fleet-admin/app/device/messages.tsx`).

## Window change, 2026-09-13 (implemented, not yet deployed)

Owner decisions the same day set a 24-hour approval window and made an unknown
dispatch outcome retryable inside it. Four ceilings gate that window and all four
now read one constant, `mesh_contacts.MAX_WINDOW_SECONDS = 86400`:

| Cap | Site | Was |
| --- | --- | --- |
| contact card TTL | `mesh_contacts.MAX_TTL_SECONDS` | 3600 |
| owner card-issue TTL | `mesh_contact_forwarding` (`/contacts/preview`) | 3600 literal |
| signed message approval | `mesh_outbox._validate` | 3600 literal |
| sealed wire envelope | `mesh_envelope.MAX_LIFETIME_SECONDS` | 3600 |

`community_service` seals `expires_at = min(now + MAX_WINDOW_SECONDS,
card.expires_at)`, so the card is still the binding ceiling and the shortest of
the two wins. `tests/test_mesh_window.py` pins all four together and fails if any
one is raised alone — the envelope is re-validated with the *peer's* clock, so a
24-hour approval sealed with a one-hour envelope (or the reverse) is refused on
receipt rather than degraded.

Consequence to carry into every later step: the peer card now lives up to 24
hours, which is also how long a peer may keep listing a card whose revocation
could not be *delivered*. A delivered superseding card still revokes immediately
(pinned by `test_revocation_takes_effect_on_delivery_not_at_the_day_long_ttl`),
and `contacts/radio` already returns `expires_at` for the UI to render age. When
the router below lands, per-attempt re-sealing is what lets the card TTL shrink
back to minutes while owner consent stays durable.

## Code-level obstacles to the durable-pending model

The storage already behaves like ICQ: rows are never deleted and there is no
expiry sweep. Five code facts still enforce a one-hour mindset; 1, 2 and 5 are
now raised to the 24-hour window above (they no longer block a long-lived
approval), while 3 and 4 are still exactly what makes an orphaned row undeliverable:

1. ~~`mesh_outbox._validate` caps the signed approval at
   `issued_at + 1 <= expires_at <= issued_at + 3600`.~~ Now
   `issued_at + MAX_WINDOW_SECONDS` (24 h).
2. ~~`community_service.py` seals the send with
   `expires_at = min(now + 3600, card.expires_at)`, so the *peer card's* one-hour
   life also caps the message.~~ Now `min(now + MAX_WINDOW_SECONDS,
   card.expires_at)` against a card that may itself live 24 h.
3. `dispatch_one` refuses to transmit once `now >= expires_at` — for that reason
   alone today's orphaned rows can never be re-sent.
4. Nothing schedules a later attempt: the background dispatch job runs on the
   current poll only, so an unreachable peer is retried zero times.
5. **The sealed wire cannot be the durable artifact.** `mesh_envelope._validate`
   enforces `issued_at <= now < expires_at` with `MAX_LIFETIME_SECONDS`, and
   `open_envelope` re-checks it with the *receiver's* current time. That ceiling
   is now 24 h, so re-transmitting the stored ciphertext works for a day, but it
   still cannot work forever: an envelope that arrives after its lifetime is
   rejected by the peer.

The fix is to split the three things that are currently one artifact:

* **Owner consent to send this content** — durable, revocable, not a one-hour
  lease (a message the owner tapped Send on stays approved until delivered or the
  owner cancels it).
* **Attempt-time envelope** — sealed fresh on every attempt with the current
  `issued_at`/`expires_at`, because the peer validates freshness on receipt. The
  approval binds the *content and recipient*; the wire is a per-attempt artifact,
  not part of the durable approval. (Today `content_digest` folds in
  `issued_at`/`expires_at`, which is what forces the wire to be sealed once — that
  binding has to move.)
* **Route credentials at attempt time** — a peer card, radio key and node
  reachability that are short-lived by design and must be *re-resolved on every
  attempt*; absence of a live route defers the attempt, it does not kill the
  message.

Receipts already tolerate this: per `MESH_OUTBOX.md` a delivered/read assertion
stays valid even after its own receipt lifetime, because the *original* is what
persists.

Dedupe keeps re-sealing safe: the peer keys inbound messages by
`(sender, message_id)` (`inbox_id`), so a later attempt of the same message id
cannot appear twice in the peer's inbox even though each attempt carries fresh
timestamps. The sender must equally never send the same id on both transports.

## Design

1. **Row schema.** Split the record into: durable consent (`content`,
   `recipient`, `message_id`, owner approval proof, no expiry), and per-attempt
   state (`attempts_radio`, `attempts_sage`, `next_attempt_at`, `last_failure`,
   `wire_hash` of the most recent attempt, `transport`). Add it as *optional*
   keys to the progress body and accept both the old and new key sets: a new
   *required* key would make the rows already in the vault fail closed, and
   `MeshDaemon.reconcile` would then latch the daemon. Old rows keep working by
   treating their approval's `issued_at`/`expires_at` as the first attempt's.
2. **Router.** New `mesh_router.py`: per row decide radio (verified contact with a
   unique prefix, inside the window) → otherwise SAGE → otherwise wait until
   `next_attempt_at`. Sticky rule: once any transport has written for that message
   id, the other transport is never used for it.
3. **SAGE provider.** Outbound: `message_send(to_agent=peer, payload=sealed
   envelope, idempotency_key=<message id>, ttl_minutes=…)`. Inbound: bounded poll
   of the node inbox into the same mailbox, deduped by message id, with the same
   receipt/read semantics as radio.
4. **Cadence.** The daemon's existing poller wakes the router; per row
   `next_attempt_at` backs off (1 min → 5 min → 15 min → 60 min) while the
   approval window allows, and stops at the window edge. No unbounded background
   work: one attempt per row per wake, all inside the existing deadlines.
5. **Visibility.** Expose `transport` and `route_state` per message through the
   community API; the chat window shows "via radio" / "via SAGE network" /
   "not yet delivered" and whether a retry is scheduled.
6. **No silent retire.** A row that cannot be delivered stays pending and visible
   as `not delivered yet`; the four orphaned rows on A and one on B become
   ordinary pending messages under the new model. The only terminal non-delivery
   outcome is an explicit owner cancel/delete, recorded as such (audited, no
   delivery claim, evidence preserved).

## Acceptance gates

* Unit: router policy (radio preferred, SAGE fallback, no double send, pending
  survives route expiry, backoff schedule), schema tolerance for rows written
  before the change.
* On device: (i) A→B over radio as today; (ii) the same test with the radio path
  made unavailable — the message must appear in B's durable journal with the SAGE
  route recorded; (iii) the peer offline for longer than a card lifetime, then
  reachable again — the pending message must still deliver and must never have
  been shown as delivered in between.
* Every gate reads the durable row and the peer's evidence, never an HTTP code.

## Deployment

`sage_lantern` package change → staged directory + hash manifest → check/apply/
rollback in the style of `work/messages-ab-20260912/deploy_receive_fix.py` → audio
recommission in the same window → reviewed cold start. Add the precondition found
on 2026-09-12: a read-only root radio read (drain, handshake, health, close)
between the cold start and the daemon's first request, or B latches on its first
provider use.

## Open product decisions

1. ~~Consent lifetime~~ **Confirmed by the owner 2026-09-12, bounded
   2026-09-13**: a sent message stays approved until it is delivered or the
   owner cancels it, inside a **24-hour** signed window; escalation to the owner
   happens only after the window actually lapses. The chat UI must therefore list
   pending messages with a cancel action and an expiry/re-approve action. The cap
   is `MAX_WINDOW_SECONDS` today; the router's per-attempt re-sealing is what
   would later let consent stay durable past a day without a longer envelope.
2. While-locked: both while-locked flags are false, and the SAGE path also needs
   a readable vault, so the same decision governs both transports.
3. Retry ceiling and quiet hours: hourly while awake, with backoff — confirm there
   is no attempt ceiling, and whether radio attempts should back off further than
   SAGE attempts to protect airtime.
