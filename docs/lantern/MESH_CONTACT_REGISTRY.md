# Encrypted mesh contact registry

Source-only foundation. No discovery, advertisements, ports, transmission,
enrollment, new key/vault or filesystem operations. Uses the existing owner
signer and real `WorkflowStore`; its pinned signed storage probe and encrypted
workflow API remain prerequisites. Tests use synthetic identities/HTTP/SQLite,
not live SAGE encryption evidence.

## API / factory handoff

`MeshContactRegistry(store, identity, owner=RecipientID(...), inventory=None,
verify_binding=None, clock=time.time)` validates the owner against the store
actor and signer. No injected inventory/policy means resolution unavailable.
Every operation requires a live `access_check` and rereads verified stored data:

- `put(raw_card_bytes, known_actor=exact_RecipientID, access_check=...)`: explicit
  admission/update of a currently valid card. Requires the expected full actor,
  never resolves names to authority. Equal signed payload/revision is idempotent;
  no re-signing of a remote card. A different nonce for identical payload does
  not replace the retained bytes.
- `historical(actor, access_check=...)`: returns `HistoricalMeshContact` after
  verifying owner record binding AND the raw remote signature. Not send authority.
- `list_current(access_check=...)`: returns current self-consenting verified cards,
  not an enrolled or globally authenticated directory. Expired/opt-out records
  stay stored and charged, but are omitted. Invalid or missing reserved records
  fail the operation rather than silently producing an incomplete directory.
- `resolve(full_radio_key_bytes32, access_check=...)`: requires one unique latest
  card mapping, current findable/messaging consent, an independently supplied
  `ContactInventory` with exact full-key membership and unique six-byte prefix,
  and `verify_binding(owner, verified_current_card, full_radio_key) is True`.
  The trusted callback must establish the configured independent radio-binding
  policy (including possession/enrollment when required), not echo a JSON flag.
  Prefix-only input is rejected. The target card is reread after policy evaluation
  to reject observed concurrent revocation/rotation before return.

Historical verification is explicit in `mesh_contacts.py`:
`verify_contact_card_signature` validates signature/structure/lifetime bounds but
returns a distinct type without current authority. `verify_contact_card` still
requires actual current time and accepts verified historical previous evidence
only for rollback comparison. Never use an untrusted card timestamp as fake now.

## Durable schema and capacity contract

Both records use existing server kind **`mesh_inbound`**; no new server kind.
UUIDv5 namespace: `8967f8dc-e1d8-4fa1-bb5d-3a4a4cde77ce`.
Namespaced input is canonical ASCII JSON (sorted keys, compact separators):

- Index ID: `{schema: "sage.lantern.mesh-contact-index.v1", owner: {chain_id,
  agent_id}, kind: "mesh_inbound"}`.
- Contact ID: `{schema: "sage.lantern.mesh-contact-record.v1", owner: {chain_id,
  agent_id}, actor: {chain_id, agent_id}, kind: "mesh_inbound"}`.

Exact case-sensitive `RecipientID` strings are preserved, including uppercase,
colon and 128-character chains. No concatenation ambiguity or normalization.

Each stored payload is `{body, timestamp, nonce, signature}`. Body has exactly
`schema, owner, kind, record_id, revision` plus `members` (index) or `card`
(original canonical UTF-8 card text). Owner SDK `sign_request` uses non-HTTP
domain `LANTERN-CONTACT-REGISTRY /sage-lantern/mesh-contact-registry/v1` and the
actual SDK timestamp/nonce framing. Verification pins owner, schema, kind, UUID
and exact outer journal revision before trusting membership or card bytes.
The contact UUID is also recomputed from the cryptographically verified remote
actor. Owner authentication cannot bless an invalid remote signature.

The index is a sorted unique list of at most **256 deterministic contact UUIDs**,
small enough for WorkflowStore's 16 KiB payload bound even with its signature.
CAS-reserve index membership **before** creating the contact row. Competing
writers for the final slot cannot both win. Updates use the contact row's own
revision CAS; index and contact commits are not one atomic multirow transaction.

Unknown index/contact writes raise `RegistryOutcomeUnknown`. Reconcile by
explicitly submitting the original card for the same deterministic actor UUID;
there is no automatic retry, fresh UUID fallback or reservation reclamation.
A reserved-but-missing row remains charged and makes listing/resolution fail
closed until reconciled. Reserved rows, expired contacts and revocation tombstones
are never evicted or deleted by this API. Known contact updates remain possible
at capacity. A newly observed signed revision cannot go backward or equivocate
at equal revision; issuance cannot be backdated relative to the retained card.

Revocation survives expiry and process restart: the expired previous card is
still signature-verified before comparing updates. An explicitly submitted
higher signed revision may opt in again; expiry/restart alone never restores
permission. No historical versions or raw private message content are stored.

## Limits / owner obligations

- Signatures do **not** prevent whole-database rollback, deletion of the entire
  index, or replay of a coherent old database snapshot. No external monotonic
  hardware or rollback-resistant checkpoint is claimed.
- Reads are bounded sequential observations, not database-wide snapshot leases.
  Index changes during scans and target changes during binding checks are
  rejected, but another process/radio can change after the last check. The
  factory must serialize registry policy changes with dispatch authorization;
  no FD ownership or cross-process radio lock is provided here.
- Duplicate latest full-radio-key mappings are denied even if one card expired
  or opted out. A signed card alone proves neither name authenticity, chain
  enrollment nor radio-key possession. Independent policy remains mandatory.
- Current-time rollback detected within an instance fails closed; there is no
  durable trusted-clock claim across restarts. Factory owns trustworthy time.
- Preserve registry schemas separately from inbox messages, receiver/assembly
  slots and rejection dispositions when scanning `mesh_inbound` records.
  Server storage capacity/errors remain unavailable, never permission to skip
  signatures, lose tombstones, auto-adopt or transmit.
