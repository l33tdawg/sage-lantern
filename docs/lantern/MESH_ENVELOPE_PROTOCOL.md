# Protected peer envelopes — codec and durable identity

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
inner object: 2048 bytes; complete ASCII wire: 4096 bytes. Lifetime is 1–86400
seconds (the shared `mesh_contacts.MAX_WINDOW_SECONDS`, so a sealed envelope can
carry a full 24-hour owner approval) with integer epoch timestamps. Expiry is exclusive; future-issued
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
24-hour maximum envelope lifetime does not extend the fragmenter's separate
900-second assembly deadline. Reassembly completion does not replace envelope
verification or persistent replay checks. Production replay persistence remains
an explicit activation dependency, not supplied by this codec.

Radio firmware/USB sees application ciphertext if and only if callers send this
wire instead of raw plaintext. Host endpoints still handle plaintext and existing
private keys; use protected storage and ordinary-key custody. SealedBox does not
hide RF routing, length, timing or peer contact metadata; compromise of a static
recipient key can decrypt recorded ciphertext. This is not ratcheting forward
secrecy or proof against compromised endpoints. No change to frozen radio driver.

## V2 renewed message attempts

New messages and delivery/read receipts retain the v1 format. Reapproving an
expired outbound message now creates a v2 message attempt, retaining the same
logical `message_id`, content and chain-qualified peers. The v2 body contains
every v1 field plus `attempt_id`, `logical_issued_at` and `logical_expires_at`.
Its schema is `sage.lantern.mesh-peer.v2`, signature domain is
`sage.lantern.mesh-peer.v2/signature` followed by NUL, and wire prefix is `LME2.`.
Prefix, schema and signature domain must agree. V2 permits message kind only;
receipt structure and signature verification remain v1.

`attempt_id` is a distinct nonzero canonical UUID. The logical timestamps retain
the original approved v1 window; their lifetime uses the same 86400-second bound.
The original window must have ended by the new attempt's issue time. The new
`issued_at`/`expires_at` window controls fresh reception and transmission. The
same content, inner-size, wire-size and SL1 fragmentation bounds apply. There is
no permission to replay an expired v1 wire at a later clock.

`seal_message(..., attempt_id=..., logical_issued_at=...,
logical_expires_at=...)` produces v2. Supply all three fields together.
`OpenedEnvelope.digest` remains SHA-256 of the entire current attempt body.
`OpenedEnvelope.logical_digest` reconstructs the original v1 canonical message
body using the stable UUID, content, peers and original timestamps. For v1 it is
the original envelope digest. This commitment includes the original approval
window; it is not merely a hash of user text. The sender's current v2 signature
authenticates both the commitment's inputs and the renewed attempt window.

Inbox admission still keys by the qualified sender and logical UUID, retains
exact first-admission wire and signed admission time, and never replaces that
record on duplicate reception. Same kind and logical commitment is duplicate;
changed content, recipient, original window or kind remains equivocation. First
reception of a renewed v2 message is admitted normally and can produce a v1
receipt referring to that attempt's digest. A duplicate can use the already
admitted original's receipt; receipt creation remains a separate authorized
operation. No partial assembly or serial acknowledgement grants delivery.

The signed outbound journal accepts `sage.lantern.mesh-outbound.v1` and `.v2`.
Queueing preserves v1. Reapproval upgrades a verified v1 row in one CAS to v2,
preserving the original logical timestamps and adding `attempt_history`. The
history retains the original v1 digest/window (`attempt_id=null`) and each v2
attempt's UUID, digest and window, including the current attempt. Every digest
is reconstructed from the immutable message, original window and attempt
metadata; duplicate attempt UUIDs, missing mappings, overlapping renewal windows
and malformed histories fail closed. There are at most eight approvals per
logical row, including the original. At capacity, further reapproval is refused
without dropping history or changing the row. Fragment retries inside one
approval retain exactly that attempt's existing wire and cursor semantics.

Receipt correlation selects exactly one retained attempt by `original_digest`,
then verifies reversed peers, logical UUID, signature, receipt expiry and issue
time against that attempt. Replay/receipt-slot reservations retain the digest
the receipt actually asserted. Delivery/read promotion remains CAS-based and
monotonic across reordered assertions; a delayed authentic assertion can still
promote a locally cancelled row. Cancellation cannot renew the row or undo work
already received. Current access and owner guards remain required. Signed
history is auxiliary evidence and does not establish SAGE consensus or reading.

Schema filters in runtime dispatch, startup reconciliation, drain, mailbox,
maintenance retention and cancellation must accept both journal versions. Sent
and received mailbox ordering uses original logical issuance when available.
Custody may carry an opaque LME2 envelope under its existing bounded wrapper;
custody admission does not authenticate or decrypt its logical message.

This is a source implementation pending coordinated endpoint rollout. Existing
v1 receivers reject LME2; old senders/readers cannot use v2 journal rows. There is
no automatic downgrade to the unsafe v1 renewal behavior. MT03 capability
negotiation and MT19 staging remain separate prerequisites for deployment.
Already-renewed v1 rows whose original digest/window was overwritten cannot be
retroactively reconstructed from the latest row. They remain readable as their
retained v1 version, but reconciling an older admission needs preserved original
evidence and a separately reviewed recovery decision. This implementation does
not claim to recover that missing history or qualify real LoRa backhaul.

## Binary carrier representation

`mesh_binary_envelope.to_binary(sealed.wire)` removes the outer Base64 encoding
for a separately admitted binary transport. A record is two ASCII magic bytes
`LM`, one version byte (`0x01` for LME1, `0x02` for LME2), followed by the original
SealedBox ciphertext. No inner fields, signatures, keys, encryption or expiry
change. `to_text(record)` restores the exact canonical LME wire for the existing
`open_envelope` function. A record has at most 2099 bytes; noncanonical text,
unknown versions, wrong types and out-of-bound records are refused.

The carrier must provide a complete, bounded record and explicit format
agreement. The codec is not stream framing or automatic capability negotiation.
It never falls back to plaintext or a different version. The outer version is
only a selector: the recipient opener requires the encrypted, signed schema and
signature domain to agree with it. Conversion alone does not authenticate a
record. Always open against independently pinned peers and current time, then
apply protected durable replay, consent and service admission before acting.
The logical replay identity stays the same across text/binary conversion and
resealing. A fresh carrier grant cannot renew an expired application envelope.

Relays can remove their adjacent link encryption to reassemble and forward this
opaque record. They still cannot open the recipient's ciphertext or sign as the
original sender. Keep these records out of Lamplighter public caches, manifests,
availability advertisements and diagnostics. Link ACK or reassembly completion
does not create a delivery/read receipt. Existing key-lifecycle and metadata
privacy limits still apply.

Source controls carry these records through two independent authenticated
Meshticulum flow sessions with durable session/attempt stores and modeled radio
workers. They exercise loss, corrupted and replayed link packets, and a relay
that sends mutated, falsely signed, relabelled, plaintext or expired records
under a valid second-hop grant. These are local composition checks; they do not
qualify private service admission, autonomous routing, mixed physical carriers
or an endpoint rollout. No firmware or active device path selects this format
yet. Byte and minimum DATA-frame savings are recorded separately from measured
RF throughput in `work/meshticulum-private-binary-20261010/`.
