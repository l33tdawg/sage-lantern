# Lantern signed mesh self-consent cards v1

Offline foundation, not activated. No filesystem, network, radio or service
operations. No key generation or new vault. The caller supplies its already
loaded ordinary SAGE `AgentIdentity`; signing uses its existing `sign_request`.
The module defaults both consent flags to false and never transmits a card.

## Protocol for Ptolemy review

Envelope has exactly `payload`, `nonce`, `signature`. Payload has exactly:

| Field | Contract |
| --- | --- |
| `schema` | `sage.lantern.mesh-contact.v1` |
| `chain_id` | Existing `RecipientID` contract `[A-Za-z0-9._:-]{1,128}`, case-sensitive with no normalization; self-asserted, not verified enrollment |
| `agent_id` | Full 32-byte Ed25519 public key, lowercase hex; signature verification key |
| `radio_public_key` | Full 32-byte valid Ed25519 public key, lowercase hex, different from agent key |
| `display_name` | NFC, 1–128 characters, ≤512 UTF-8 bytes, no edge whitespace, controls or line/paragraph separators |
| `revision` | Integer 0 through 2^53−1, never boolean; increment for every changed payload |
| `issued_at`, `expires_at` | Integer Unix seconds; positive issuance, issuance ≤ now < expiry, TTL ≤86400 seconds (24 h, the shared `mesh_contacts.MAX_WINDOW_SECONDS`) |
| `findable`, `messaging_allowed` | Strict booleans, independently meaningful; false defaults |

All integer fields are bounded by 2^53−1. No future-time grace. The caller must
provide a trustworthy current Unix time; this module has no clock service.

The 24-hour ceiling is the owner's approval window for an explicitly approved
message (2026-09-13): the message approval is derived from the card, so the card
has to be able to outlive a single hour. It is a ceiling, not a default — the
issuing owner still chooses the TTL, and the shortest of (now + 24 h, the peer's
`expires_at`) is what a queued message actually gets. Revocation is unaffected by
the length of the window: a *delivered* superseding card (higher revision, or
`findable`/`messaging_allowed` false) takes effect immediately, and an older
unexpired card can never be replayed back over it. What a longer TTL does widen
is the worst case when the revocation cannot be delivered at all: a peer keeps
listing the last card it holds until it is superseded or expires, now bounded by
24 h instead of 1 h. Render card age/expiry from `contacts/radio`
(`issued_at`/`expires_at`) rather than presenting a held card as currently true.

Canonical JSON is UTF-8, lexicographically sorted object keys, compact separators,
literal Unicode (`ensure_ascii=False`), no NaN/Infinity. Input must equal its
canonical encoding exactly. Reject unknown/missing/duplicate fields at every
level, invalid UTF-8/types/key encodings, and envelopes over 4096 bytes.

Signing uses the real SDK primitive:

```
identity.sign_request("LANTERN-CONTACT", "/sage-lantern/mesh-contact/v1",
                      canonical_payload_bytes, timestamp=issued_at)
```

These are domain-separation constants, **not a new HTTP method/endpoint to call**.
The signed Ed25519 message is exactly:

```
SHA256(b"LANTERN-CONTACT /sage-lantern/mesh-contact/v1\n" + canonical_payload)
  || int64_big_endian(issued_at) || nonce_bytes
```

Envelope nonce is the SDK's 8 random bytes as 16 lowercase hex characters;
signature is 64 bytes as 128 lowercase hex characters. Agent ID and timestamp
are already bound inside the payload; signer headers must match them. Signing
self-verifies before returning. Verification uses PyNaCl directly, not proof
field shape or a boolean injected verifier. Errors are generic.

## APIs and integration boundary

`sign_contact_card(identity, *, chain_id, radio_public_key, display_name, revision,
issued_at, expires_at, now, findable=False, messaging_allowed=False) -> bytes`.
Explicit callers own authorization and user approval; possession of an identity
object is not proof of an unlocked Lantern session.

`verify_contact_card_signature(card, *, known_actor=None)` verifies canonical
structure, signature and lifetime bounds without claiming current validity or a
past admission time. It returns distinct `HistoricalMeshContact`, not a
`VerifiedMeshContact`; it exposes no ContactClaim conversion. Expired/future cards
can authenticate historical evidence but cannot authorize discovery or sending.
No caller-supplied fake verification timestamp is needed.

`verify_contact_card(card, *, now, known_actor=None, previous=None)` returns frozen
`VerifiedMeshContact`. `known_actor` binds the exact `RecipientID(chain_id, agent_id)`.
`previous` is caller-retained, previously verified current or historical data for that same actor:
lower revisions, equal-revision changed payloads (including radio rotation), and
backdated issuance are rejected. An equal unchanged payload is idempotent even
with a different signing nonce. Higher-revision explicit opt-in after opt-out is
possible; no automatic re-enrollment occurs.

Verified opt-out cards are returned so the consumer can invalidate discovery and
send authorization. Do not discard false cards or keep showing old true claims.
Retain the latest complete verified card/tombstone even after expiry. Stateless
verification alone cannot detect replay, unseen revocation or clock rollback.
No persistence is provided; restart-safe revision retention remains an encrypted
store integration requirement. Do not treat a process restart as consent reset.

`verified.recipient` returns `RecipientID`. `verified.contact_claim(caller=...,
issuer=..., policy_binding=...)` adapts self-consent to `ContactClaim`. These
caller-selected fields describe the integration's trust policy, not signed
claims of a node issuer. Only call this after verification and independent
policy checks; constructing the public dataclass manually proves nothing.
Existing `RecipientCache` can consume that claim and conservatively makes
observed revocation terminal for that cache instance. It uses its own freshness
limit (60 seconds by default), not the card's maximum TTL. Keep full verified
cards separately: a ContactClaim omits the radio key and cannot alone detect
equal-revision radio-binding substitution.

## What this does not establish

- A signed advertisement proves control of the SAGE key and that keyholder's
  consent/claimed radio binding. It does not establish local chain enrollment,
  chain authority, globally unique names, identity of a human, or honest labels.
  An unknown key may sign any plausible name. Pin/confirm the exact actor before
  presenting an authenticated contact; separately check current SAGE enrollment.
- It is not proof of possession of the named radio private key. Match the full
  key to independently authenticated MeshCore evidence before routing. No MAC,
  USB serial, short-prefix identity, fixed route or LoRa trust is invented here.
- Radios need independently generated keys, not cloned firmware identities.
  Rejecting equality with the agent key does not prove independent generation.
  Replacement radios preserve the owner agent identity; issue a higher revision,
  retain the old binding's revocation state and revalidate routes separately.
- Cards reveal a name and cross-system binding if shared. No automatic public
  publication, directory registration, announcement, send or E2E encryption is
  implied. The JSON card does not fit the existing single-frame MeshCore text
  limit; transport/framing is explicitly not implemented by this protocol.
- This is an application self-consent format, not an upstream SAGE API or a
  MeshCore firmware advertisement format. It does not activate Unit A messaging.

Synthetic tests exercise the real sibling SDK signer, independent signature
verification, field/domain tampering, malformed input, opt-out defaults, expiry,
revision rollback, rotation and revocation propagation into RecipientCache.
