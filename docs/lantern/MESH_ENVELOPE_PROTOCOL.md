# Protected peer envelope v1 — codec only

Ptolemy review handoff: `mesh_envelope.py` has no network, serial calls, key
generation/storage, enrollment, fragmentation, replay store or auto-resend.
It consumes existing ordinary PyNaCl Ed25519 SigningKey objects. The operator
must independently pin each Peer(chain_id, public_key) and authorize this key
use; no Root identity is needed. The codec cannot infer an enrolled role from
key bytes. Do not use a private Root merely because it is an Ed25519 key.

## Cryptographic layout

Canonical UTF-8 JSON body has exact fields: schema, sender, recipient, message_id,
kind, issued_at, expires_at, content, original_id, original_digest. Peers include
both chain ID and the full Ed25519 public key. The signature covers
`sage.lantern.mesh-peer.v1/signature` followed by NUL and canonical body bytes.
An exact canonical inner JSON object contains body and Base64 Ed25519 signature.
Encrypt the entire inner object with PyNaCl SealedBox, converting the recipient
Ed25519 key to Curve25519. Wire format is `LME1.` plus canonical standard Base64
ciphertext. SealedBox uses ephemeral randomness; the module generates no
persistent identity keys. Inner signatures provide sender authentication that
SealedBox alone does not. Sender metadata and content are both encrypted.

Decryption requires the expected recipient's existing signing key conversion,
and verifies the exact expected sender/recipient chain and key, strict schema,
canonical serialization, signature domain, UUID, timestamps and byte bounds.
No normalization, implicit peer discovery, plaintext fallback or photo field.
Unknown fields, malformed Base64, duplicate JSON fields and wrong domains fail.
Exceptions and dataclass repr never include plaintext or ciphertext.

## Caller API

- `seal_message(key, sender=..., recipient=..., message_id=..., content=..., issued_at=..., expires_at=...)`
- `seal_delivery_receipt(key, ..., original_id=..., original_digest=...)`
- `seal_read_receipt(key, ..., original_id=..., original_digest=..., explicit_user_read=True)`
- `open_envelope(wire, recipient_key, expected_sender=..., expected_recipient=..., now=...)`

Sealing returns wire/message_id/digest and `encoded_bytes` (ASCII wire length,
including prefix and Base64 overhead). It exposes size without logging content.
Opening returns verified fields, digest
and replay_identity `(sender_chain, sender_key_hex, message_id)`. It never sends
a receipt or declares that the user saw anything. Message text remains untrusted
instructions even when its sender signature is valid.

Before acting, atomically persist the replay identity and SHA-256 canonical-body
digest in protected storage. Same identity/same digest is duplicate; changed
digest is conflict, including attempts to reuse a message UUID for a receipt.
Preserve original approved UUID, timestamps and digest across retries. Ciphertext
is randomized, so wire hash is not replay identity. Durable dispatch/reassembly
must preserve exact original wire bytes when retrying fragments; codec does not
schedule retries. Receipt UUID is distinct and its original_id/original_digest
must match a stored outgoing message with precisely reversed peers. The codec
checks structure/signature, not that external stored-message relation.

`correlate_receipt(opened, original_sender=..., original_recipient=...,
original_id=..., original_digest=..., original_issued_at=..., original_kind="message")`
checks that relation against caller-supplied protected-history fields. It requires
exact reversed chain-qualified peers, matching original UUID/digest, a delivery
or read receipt, and receipt issue time not before the original. It rechecks the
retained inner signature and digest, so a manually constructed or replaced
OpenedEnvelope is not an authenticated receipt. Returns only
`peer_delivery_asserted` or `peer_read_asserted`; it never mutates history.
This does not establish that caller-supplied history is authentic, that the peer
assertion is truthful, or that the receipt has not replayed. Recheck expiry at
use time, then require durable replay/CAS before changing UI/history status.

Only emit delivery after verified application-envelope persistence, never a
MeshCore ACK. Only the explicit authenticated user-read operation may call the
read encoder. Its boolean is a trusted internal assertion, not an HTTP authority
or cryptographic proof of human attention. Do not create receipts for receipts.

## Bounds and privacy

Content: 512 UTF-8 bytes; chain IDs: 1–128 characters matching
`[A-Za-z0-9._:-]` exactly (same contract as RecipientID); canonical encrypted
inner object: 2048 bytes; complete ASCII wire: 4096 bytes. Lifetime is 1–3600
seconds with integer epoch timestamps. Expiry is exclusive; future-issued
messages fail (no implicit clock skew). Operators must establish clock validity;
time uncertainty is not permission to bypass expiry or replay checks.
Two 128-character chains and 512 bytes of UTF-8 content fit, including the tested
JSON-escaping-heavy case. The independent 2048-byte inner check remains enforced;
exceeding it fails explicitly, without truncating or changing the approved message.

Wire requires later bounded fragmentation: 128-byte MeshCore text is not enough
for a whole envelope. Fragment headers consume part of that budget. This codec
does not establish fragment authenticity, message completeness, memory budgets,
transport delivery or private receipt admission. Reassemble within the wire bound
before decrypting and never display partial data. Zero live A/B tests performed.

Parent's separate SL1 fragmenter can consume `sealed.wire.encode("ascii")`.
Its reported 48-byte chunk budget means `ceil(encoded_bytes / 48)` fragments;
do not interpret the 4096-byte ceiling as a radio-efficient target. The codec's
one-hour maximum envelope lifetime does not extend the fragmenter's separate
900-second assembly deadline. Reassembly completion does not replace envelope
verification or persistent replay checks. Production replay persistence remains
an explicit activation dependency, not supplied by this codec.

Radio firmware/USB sees application ciphertext if and only if callers send this
wire instead of raw plaintext. Host endpoints still handle plaintext and existing
private keys; use protected storage and ordinary-key custody. SealedBox does not
hide RF routing, length, timing or peer contact metadata; compromise of a static
recipient key can decrypt recorded ciphertext. This is not ratcheting forward
secrecy or proof against compromised endpoints. No change to frozen radio driver.
