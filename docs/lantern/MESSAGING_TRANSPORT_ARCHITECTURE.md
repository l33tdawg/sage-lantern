# Lantern private messaging transport architecture

Owner-directed design, 2026-09-05. No transport activation, RF transmissions,
federation enrollment, firmware changes or deployment are authorized by this
document. This reconciles `PUBLIC_KNOWLEDGE_INTEGRATION_AUDIT.md` (PRIVATE
messaging appendix) and `RADIO_POWER_POLICY.md`; it changes neither implementation.

## 1. Architectural decision

**Use MeshCore for local LoRa delivery and mesh relaying. Use SAGE identities
and authenticated federation backhaul between gateways serving different LoRa
networks.** A local message does not need Internet access or a public SAGE node.
MeshCore remains the radio routing layer; do not reinvent its mesh forwarding
or reduce it to a permanently fixed Unit A-to-Unit B radio route.

SAGE identifies the intended sender/recipient and governs discovery/consent.
The Lantern adapter binds those identities to independently authenticated radio
contacts and gateway custody. Paths may change; authorship and logical message
identity must not. Public knowledge is a different plane: no private message,
draft, receipt or recipient directory becomes a Commons proposal, channel post,
public room post or canonical memory automatically.

## 2. Existing features versus integration work

| Layer | Established support | Not established for this release |
| --- | --- | --- |
| MeshCore upstream | Multi-hop radio routing, companion interfaces and repeater role. | Exact host framing, payload limits, ACK semantics and compatibility of the recorded Unit A firmware with the new adapter. |
| MeshCore room server | Shared-post history that returning clients can retrieve. | Confidential per-recipient custody for arbitrary Lantern envelopes, SAGE identity binding, or a sleeping private-message mailbox. |
| Unit A evidence | USB companion protocol and settings-survive-reboot recorded; profile has repeating disabled. | Two-unit RF delivery, current hardware state, companion autonomous repeating, gateway bridge or measured duty cycling. |
| SAGE | Signed exact-agent messaging, caller-filtered federation discovery, approved federation delivery and negotiated receipt evidence. | Native MeshCore endpoints or an authenticated SAGE-to-radio address binding. |
| Lantern foundations | Typed private drafts/contacts, generic envelope and duplicate-store code. | Complete verified radio adapter, encrypted transport queue, cross-path atomic delivery or live gateway integration. |

Upstream feature references: [MeshCore README](https://github.com/meshcore-dev/MeshCore)
and [MeshCore FAQ](https://github.com/meshcore-dev/MeshCore/blob/main/docs/faq.md),
read 2026-09-05. Their current documentation is not certification of the pinned
Unit A build. Radio evidence: `evidence/unit-a-radio/meshcore-radio-public.json:13`,
`evidence/unit-a-radio/meshcore-radio-public.json:30`; host validation remains in
`docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md:17`.

No serial opcode, radio sleep command, arbitrary packet endpoint, payload ceiling
or mailbox protocol is specified as existing here. Pin the exact firmware/host
library and verify its real supported operations before implementing that seam.

## 3. Stable identity and message contract

The adapter's proposed logical record binds: protocol version, cryptographically
random message ID, exact sender `(chain_id, agent_id)`, exact recipient tuple,
immutable approved payload digest, creation/expiry, and authenticated author
evidence. This is a design contract, not a claim that SAGE already accepts these
as new wire fields. Keep private text inside the protected payload. Domain-separate
message signatures from public-knowledge signatures and transport receipts.

- Select recipients through verified findable-only consent, never name equality.
  Agent ID identifies the credential key; rotation requires verified continuity.
- Radio contact binding must authenticate the exact SAGE tuple, radio identity,
  issuing authority, revision, validity and revocation policy. Heard adverts,
  similar names and radio addresses alone cannot establish that binding.
- Keep routes, next hops, retry counters and carrier IDs outside immutable author
  content. Gateway changes and direct/backhaul fallback preserve the same logical
  message ID and bytes. A content or recipient edit is a new approved message.
- Cache only authenticated bindings; fail closed on stale/revoked evidence.
  `software/runtime/src/sage_lantern/message_contacts.py:1` is memory-only policy
  with an injected verifier, not an implemented radio binding/trust service.
- A receiver may use an explicitly approved still-valid offline binding for
  local-mesh identity verification; this requires a documented bounded offline
  revocation window. It must not be presented as live federation authorization.

### Identity roles and radio replacement

| Identifier | Role | Must not be treated as |
| --- | --- | --- |
| SAGE `(chain_id, agent_id)` | Exact cryptographic messaging principal; agent ID identifies its credential public key. | A hardware serial number or automatically permanent identity across SAGE key rotation. |
| Owner/personal SAGE continuity | Governed ownership, history and recovery relationship. | Ownership inferred from whichever radio is plugged in. |
| MeshCore public key | Radio cryptographic identity, authenticated through an approved binding to the SAGE principal. | Proof of SAGE ownership merely because it was heard over RF. |
| MAC address or USB serial | Inventory, device selection and diagnostics only. | Authentication: these values may be copied or spoofed. |
| Display name | Mutable human-readable label. | Recipient identity, uniqueness proof or a signature-verification key. |
| Mesh route/next hop | Replaceable delivery information. | The recipient's identity or permission to read a message. |

Generate each radio's public/private key pair independently during its supported
provisioning procedure. An ordinary firmware image must not clone an initialized
radio identity. Verify independent identities after imaging; do not export private
keys as evidence. A MAC/USB-derived identifier does not substitute for cryptographic
key generation. SAGE credentials and radio credentials remain separate keys.

The required signed binding authenticates the exact SAGE principal and MeshCore
public key under an approved authority and consent policy. Verification must cover
its intended purpose, validity, revision, revocation and supersession context;
radio-key possession must also be established through the approved enrollment
ceremony. Neither a self-asserted RF advert nor a signature-shaped object is enough.
This states verification requirements only: no new binding wire format, signing
bytes, trust hierarchy or MeshCore command is declared implemented here.

Replacing a radio must preserve the owner's personal SAGE identity and history.
It requires explicit owner-authorized enrollment of the replacement key and
revocation/supersession of the old binding, not a new owner inferred from hardware.
Gateways and contact caches must reject rollback to old bindings and invalidate
stale routes; queued messages keep their original logical IDs and recipients.
Re-resolve the transport binding before retry rather than silently rewriting
recipient identity. Already acknowledged deliveries and their history remain intact.

Radio key rotation and SAGE credential rotation are different operations. SAGE
rotation requires verified governance/continuity evidence; a radio binding must
not manufacture that continuity. Define bounded offline revocation exposure and
explicit behavior for lost/compromised radios before enabling offline bindings.
Do not silently continue trusting a revoked radio until a cache happens to expire.
Acceptance includes duplicated firmware identities, spoofed serial/MAC values,
renamed peers, replacement radios, replayed old bindings and retained owner history.
No identity or binding is changed live by this document.

## 4. Path A: same local mesh

```text
A private draft -> recipient/text confirmation -> durable protected outbox
 -> verified recipient radio binding -> A MeshCore companion
 -> local MeshCore routing (direct or provisioned repeaters)
 -> B MeshCore companion -> B adapter authentication/reassembly/dedup
 -> durable private inbox -> explicit display/read acknowledgement
```

Local mesh discovery/routing is MeshCore's job; eligibility and SAGE-to-radio
binding are the adapter's job. A must not require a federation call for every
local RF hop. B verifies exact addressed identity and approved message bytes
before admission. Ordinary RF repeaters are not assumed to understand SAGE,
issue custody receipts or persist messages for sleeping recipients.

Inserting a radio-origin message into SAGE's canonical inbox needs a supported
authenticated admission seam. Existing send APIs execute as the actual signed
caller; do not write database rows or impersonate a remote SAGE agent to import
a packet. Until that seam is implemented, use an explicitly separate protected
adapter inbox or keep integration unavailable; never call it canonical SAGE
delivery merely because the adapter received bytes.

## 5. Path B: gateways between separate LoRa networks

```text
A -> local MeshCore mesh -> gateway G1 durable custody
 -> authenticated approved SAGE federation backhaul
 -> gateway G2 durable custody -> remote MeshCore mesh
 -> B durable private inbox
```

G1 authenticates A's immutable message and its permission to use the gateway,
then commits custody/dedup state before acknowledging. G2 authenticates the
federated carrier and original message, validates B's current radio binding,
then queues remote RF delivery without generating a new logical message.

Existing SAGE `POST /v1/pipe/resolve` and `/v1/pipe/send` support exact signed
source/destination chain and agent addresses with caller-scoped idempotency.
Use approved ordinary **gateway agents** for G1-to-G2 carrier traffic. The outer
SAGE sender is then G1's gateway agent, NOT A; preserve A's independently verified
authorship inside the immutable logical message. Never borrow A's private key
or replay its expired REST signature to make the gateway appear to be A.
Anchors: `../sage/api/rest/server.go:863`,
`../sage/docs/reference/rest-api.md:2334`.

Adapter mapping must persist logical message ID/digest, gateway identity and SAGE
carrier IDs together. Derive a stable bounded carrier idempotency key from the
logical identity and exact carrier destination; changed routes must not bypass
end-recipient dedup. Authenticate B's delivery receipt independently: SAGE
delivery to G2 proves only G2 admission, not B reception. The required gateway
authorization, identity-binding format and radio admission seam remain new work.
Federation backhaul requires explicit agreements and policy; none is enabled here.

## 6. Cross-path deduplication, loops and bounded traffic

1. Receiver dedup key includes sender chain, sender credential, message ID and
   recipient tuple; bind that key permanently for the acceptance window to one
   digest. Same key/different bytes is a conflict, not a retry or overwrite.
2. Commit inbox admission and dedup record atomically before returning delivery
   evidence. Crash/restart and simultaneous direct/gateway arrivals must produce
   one logical item. Replays may retrieve an existing receipt, not redeliver.
3. Persist gateway ingress/egress state and a bounded authenticated bridge trace
   separately from author bytes. Never bridge a message back into its ingress
   network, revisit a recorded gateway, or fan it out to all discovered peers.
   Dedup remains authoritative if a route trace is absent or inconsistent.
4. Enforce absolute signed expiry plus local maximum residence, queue bytes/items,
   fragment count/size/reassembly time, retries and airtime limits. Do not reset
   expiry or retry budget at every gateway or wake. Preserve spent budgets across
   restart. Clock uncertainty must not extend lifetime indefinitely.
5. Configure finite application bridge-hop and cross-path retry budgets; MeshCore
   RF hop behavior is separate. Select numeric budgets from measured encoded
   payload/airtime and deployment policy, not an invented firmware capability.
6. Rate-limit invalid packets, discovery, receipt replays and reassembly per
   source plus globally. Receipts must not recursively generate receipts.
   Unknown outcomes may trigger bounded reconciliation, never broadcast storms.
7. Retain minimal replay tombstones through message expiry plus the agreed skew/
   retry horizon. Queue-full rejects custody; it must not evict already-promised
   custody silently. Encryption and deletion policy cover payload copies at all
   gateways, not only the handset.

The current `DuplicateStore` keys only source-agent/message ID and records it
independently of inbox admission. It is not this chain-bound transactional
cross-path guarantee: `software/runtime/src/sage_lantern/dedupe.py:8`.

## 7. Receipt and confidentiality boundaries

| Evidence | Honest meaning |
| --- | --- |
| Host serial completion | Host/companion interaction only; RF semantics must be measured. |
| Firmware RF ACK | Only the exact meaning established for that firmware; not automatically SAGE identity or durable user delivery. |
| Gateway custody receipt | Authenticated named gateway durably buffered the exact message under disclosed TTL; not recipient delivery. |
| SAGE backhaul delivery | Remote SAGE accepted the carrier; not remote LoRa transmission or B receiving it. |
| Recipient delivery receipt | Exact authenticated B identity acknowledges durable addressed inbox admission. |
| Recipient read acknowledgement | B's client fetched/displayed and explicitly acknowledged; not comprehension, agreement or task completion. |

SAGE receipt-v2 distinguishes transport, claim and read; negotiate support and
leave missing evidence unconfirmed. It does not automatically validate a nested
radio receipt: `../sage/docs/reference/rest-api.md:2296`.

### Optional private read receipts

Read receipts require explicit recipient privacy consent. Receipt opt-out means
read state remains unknown to the sender, not unread. Only an explicit user
open that successfully renders the message while authorized may schedule a read
acknowledgement. Polling, wake notifications, background retrieval, preview,
gateway custody, claim acquisition and radio ACKs must never generate one.
Even a verified read acknowledgement proves the recipient client's action, not
human comprehension. Do not retrospectively acknowledge old messages merely
because the user enables receipts later.

Existing negotiated SAGE interfaces are:

- `GET /v1/pipe/{pipe_id}/receipt/challenge/{kind}` for the exact imported-message
  recipient, with `kind=claimed|read`.
- `PUT /v1/pipe/{pipe_id}/receipt/{kind}` submits the unchanged challenge under
  a fresh exact-recipient nonce-bound signature; claimed has a separate claimant
  session requirement. Claim evidence alone must not advance read state.
- `GET /v1/pipe/{pipe_id}/receipt` is the original sender's payload-free evidence
  projection. Negotiate `federated-pipeline-receipts-v2`; missing/legacy support
  is unsupported/unconfirmed, never a successful Read fallback.

These are existing SAGE APIs, not MeshCore commands. See
`../sage/api/rest/server.go:870` and
`../sage/docs/reference/rest-api.md:2296`. The adapter must validate the exact
protocol version and trust context, not trust a field named `signature` or
`read_status` received over radio.

Bind accepted evidence to exact original sender and recipient credentials,
source/destination chains, logical message ID and immutable payload digest,
carrier/pipe mapping, receipt kind and authorized signer. Verify against the
negotiated SAGE evidence contract and separately authenticated radio-receipt
mapping; these logical bindings are not invented extra SAGE request fields.
In the G1-to-G2 carrier, a signed read by G2's gateway agent is a **gateway read**,
not a read by B. Only B's independently verified nested acknowledgement may
advance the logical message's recipient-read projection. That bridge is adapter
work and must remain unavailable until implemented and tested.

Receipt state advances monotonically from unknown to verified received/read;
invalid, duplicate, late lower-stage or unsupported evidence cannot erase an
already verified fact. Keep gateway custody/delivery, recipient receipt/read,
and expiry/failure on independent axes. A read receipt may arrive before a
separate delivery receipt: preserve the verified read without fabricating a
missing transport record. Never order authority by peer timestamps alone.

Delayed receipts after radio sleep or message transport expiry can still be
valid evidence of an earlier acknowledged action. Preserve the necessary exact
message mapping and negotiated verification context through a bounded receipt
retention horizon. This does not waive the live protocol's signature freshness,
revocation or key-generation checks: use supported durable receipt/outbox
reconciliation, not replay of expired raw signed REST requests. If context has
been purged or verification cannot be completed, retain unknown/unconfirmed.

Bound receipt bytes, outstanding verifications, replay identifiers, retries and
retention independently from payload queues. Persist accepted evidence and replay
state atomically before updating a user-visible projection. A future pure receipt
projection module must accept only injected-verifier results bound to the exact
logical message and must not itself claim wire-cryptography support. Acceptance
must cover privacy opt-out, poll/claim without read, locked render, wrong signer,
wrong chain/digest, gateway-versus-recipient confusion, replay, out-of-order and
delayed receipts, unsupported peers and restart without invented Read state.

Gateway federation uses signed pinned mTLS:
`../sage/internal/federation/client.go:122`. That does not make the full route
recipient-only end-to-end encrypted. MeshCore and SAGE keys are separate; verify
actual private-radio confidentiality for the pinned firmware and carrier.
Never send private payloads over public channels/rooms as a fallback.

If gateways terminate encryption or handle plaintext, explicitly disclose that
their operators are trusted with content; protect their stores and logs. If the
product requires gateways unable to read text, a reviewed recipient-key encryption
and rotation/recovery design is an additional prerequisite. This document does
not invent one or claim E2E protection. Signatures establish integrity/authorship,
not secrecy. Network metadata may remain visible even with encrypted payloads.

## 8. Sleeping recipients and radio power

Battery saver remains the requested default intent, not verified operation.
Follow `RADIO_POWER_POLICY.md`: radio off means no reception; an awake repeater
is not automatically a mailbox. Room-server shared history is not proof of
private custody. G1/G2 or a designated local mailbox must explicitly implement
authenticated durable per-recipient retention and retrieval before scheduled
sleep can claim deferred private-message delivery.

Wake/poll must authenticate recipient and binding, exchange bounded summaries,
retrieve missing messages and reconcile receipts without redelivery. Drain and
persist transfer state before sleep; measure startup time, clock drift, jitter,
missed windows, expiry and quotas. Do not disclose detailed private activity
schedules in public announcements. No invented power opcode or forced waking
overrides owner Radio off. Unsupported peers get unavailable/deferred status,
not guaranteed eventual delivery. Screen sleep, access lock and RF power remain
separate policies; background custody must not expose text/audio while locked.

## 9. Bounded implementation and acceptance sequence

1. Pin/verify companion host protocol and two-node private RF payload/ACK behavior;
   implement a narrow adapter, not arbitrary serial commands. No gateway yet.
2. Implement authenticated radio identity bindings, immutable message mapping,
   protected queues and atomic receiver dedup. Prove direct and repeater delivery
   with synthetic text and distinct independently provisioned identities.
3. Implement two gateway agents and approved federation carrier mapping. Prove
   local-to-remote LoRa delivery, return receipt, and simultaneous direct/backhaul
   duplicate handling without impersonating end users.
4. Prove gateway crash recovery, partition/failover, forged/stale bindings,
   rotated keys, TTL exhaustion, fragment loss, queue pressure and bridge loops.
5. Add scheduled reception only after measured radio control plus awake mailbox
   retention/retrieval acceptance. Test missed wake windows and lost receipts.

Release evidence must identify firmware, adapter version, transport trust and
each receipt boundary. Use synthetic payloads and sanitized IDs; no key copies,
private messages or radio administrative commands in evidence. This design does
not authorize current wake-tap deployment changes, public publication or live RF.
