# Public knowledge integration: verified source contract

Source audit, 2026-09-05. No live calls, keys, enrollment, upstream writes or
deployment. Read sibling AGENTS and reference INDEX before source. Reference
material describes v11.19.10; this does not establish the installed public-node
version. This document does not replace `DUAL_HOME_AND_PUBLICATION_CONTRACT.md`.

## Decision for the implementation team

Real SAGE supports signed ordinary-agent registration, governed enrollment,
memory submission, authenticated queries, detail lookup, corroboration and
challenge. It does **not** make the current Lantern outbox a verified Commons
intake, signed public release, or consensus-proof client. No configured public
deployment was established. The example configuration has a local personal
SAGE endpoint, not a separate Commons endpoint:
`software/config/lantern.example.toml:21`.

Implement the isolated adapter against the concrete APIs below, but keep public
transport unavailable until target configuration, enrollment and intake contract
are supplied. Do not point publication at personal SAGE as a temporary fallback.
The normative contract requires intake approval before fresh submission. Direct
submission is mechanically supported by SAGE but would bypass that product
contract unless the owner explicitly approves a revised narrow test policy.

## Actual endpoints and authority

All paths in this table target the **separate public SAGE**, not personal SAGE.

| Operation | Real interface | Required interpretation |
| --- | --- | --- |
| Register proposer | Signed `POST /v1/agent/register`; `name`, optional `role`, `boot_bio`, `provider`, `p2p_address` | Self-registration yields a restricted pending ordinary Member, not permission to publish. SDK `register_agent()` exists. |
| Check own enrollment | Signed `GET /v1/agent/me` | Inspect enrollment/registration status, clearance, profile, capabilities and home domain; never infer approval from roster membership alone. |
| Operator approval | `PUT /v1/dashboard/network/access/agents/{id}/policy` | Public node's machine-local current Root/eligible Admin operation, not a Lantern or Fleet capability. |
| Propose | Signed `POST /v1/memory/submit`; SDK `propose()` | Submit a memory transaction; does not mean knowledge accepted. |
| Reconcile known record | Signed `GET /v1/memory/{memory_id}`; SDK `get_memory()` | Read current disclosed record/status. Read access may be revoked even for its author. Not a portable signed consensus proof. |
| Public recall | Signed `POST /v1/memory/query`, `/search`, `/hybrid` | PUBLIC classification is not anonymous access. Current enrollment, visibility and domain authorization still apply. |
| Corroborate | Signed `POST /v1/memory/{memory_id}/corroborate`, optional `evidence` | Governed evidence action, not a validator vote or unilateral acceptance. |
| Challenge | Signed `POST /v1/memory/{memory_id}/challenge` | Governed action with target-specific authorization; not automatically available to every reader. |

Source anchors: `../sage/api/rest/server.go:732`,
`../sage/api/rest/server.go:786`, `../sage/api/rest/agent_handler.go`,
`../sage/web/network_handler.go:97`,
`../sage/web/appv23_access_handler.go:1004`,
`../sage/api/rest/memory_handler.go:3314`,
`../sage/api/rest/vote_handler.go:212`,
`../sage/api/rest/vote_handler.go:439`,
`../sage/sdk/python/src/sage_sdk/client.py:213`,
`../sage/sdk/python/src/sage_sdk/client.py:273`,
`../sage/sdk/python/src/sage_sdk/client.py:353`,
`../sage/sdk/python/src/sage_sdk/client.py:472`,
`../sage/sdk/python/src/sage_sdk/client.py:561`.

Enrollment must enforce Member, clearance 0, explicitly approved public domains,
and no validator/Admin/Manager authority. Prefer a pre-created owned domain per
proposer as the dual-home contract specifies. Capability bits are restrictions
as well as grants: denying foreign-domain writes overrides a foreign-domain
write grant; denying domain claim prevents implicit namespace acquisition.
Do not invent a `public_proposer` SAGE role or a generic submit permission bit.
`../sage/internal/store/agent_capabilities.go:7` defines the actual bits.
Public-node operators must approve a compatible profile/policy through the
existing validator, not copy an unreviewed capability integer from UI examples.

## Signing and payload mapping

Fresh REST requests use `X-Agent-ID`, `X-Signature`, `X-Timestamp`, `X-Nonce`.
The public ID is the 32-byte Ed25519 public key encoded as 64 hex characters.
Sign SHA-256 of `METHOD + " " + PATH[?QUERY] + "\n" + BODY`, followed by the
big-endian timestamp and fresh eight-byte nonce. Sign the exact transmitted
body; do not persist expiring HTTP signatures as offline queue authority.
Reference/source: `../sage/docs/reference/rest-api.md:7`,
`../sage/api/rest/middleware/auth.go:156`.

Submission fields are `content`, `memory_type`, `domain_tag`,
`confidence_score`, explicit numeric `classification: 0`, and supported optional
fields such as `parent_hash` or tags. Actual types are fact/observation/inference/
task; the normative envelope's procedure/other examples are not accepted API
types. Use observation for an explicitly selected observation, never task as an
idempotency workaround. Embeddings are node-derived on submission. SDK source:
`../sage/sdk/python/src/sage_sdk/client.py:213`; wire schema:
`../sage/api/rest/memory_handler.go:35`.

There is no dedicated Commons manifest field in that submit schema. The team
must version the approved representation of the immutable envelope/hash in the
submitted public content. Do not assume unknown JSON fields are stored or
consensus-bound. Preview exactly what will become public. Tags alone are not
universal canonical evidence: ordinary-domain tags remain node-local, unlike
scoped-domain canonical tags. Do not leak private source IDs, raw photos,
transcripts or ambient recall through content/provenance fields.

## Acceptance, status and proof: three different claims

1. **Intake eligibility** is an application decision. No Commons quarantine/
   approval endpoint was identified in the reviewed SAGE REST/SDK surface.
2. **Submission transaction inclusion**: the handler checks Comet CheckTx and
   FinalizeBlock result codes before returning `committed:true`, transaction hash
   and height. Its response still says `status:"proposed"`.
   `../sage/api/rest/memory_handler.go:1624`,
   `../sage/api/rest/memory_handler.go:3553`.
3. **Accepted knowledge** requires the later governed lifecycle state and
   authoritative evidence bound to the exact public chain and record. Detail
   lookup returns serving-record status; its votes/corroborations are not a
   client-verifiable threshold proof. Never count votes to manufacture truth.
   `../sage/api/rest/memory_handler.go:3314`,
   `../sage/api/rest/memory_handler.go:3450`.

Keep local, transport, intake, chain and validity states separate. A transport
timeout is unknown delivery, not rejection. `committed:true` on submission is
not the outbox's final `COMMITTED` knowledge state. A 403/404 is not proof of
rejection/deletion. Challenged/deprecated records are not simply rejected
submissions. Retain the local source item through every public outcome.

Generic observation submissions have no envelope-level idempotency facility;
the request's `idempotency_key` is for tasks. Persist exact submission intent and
received IDs before advancing state. If a response is lost before identifiers
are known, require intake reconciliation or operator review; do not blindly
repeat `propose()` or treat semantic search as an exact receipt lookup.

No general Commons signed-bundle export/verification API or portable memory
lifecycle-proof method was identified in the inspected REST router/SDK. Existing
pipeline receipts prove their specific messaging protocol, not arbitrary public
knowledge acceptance. A transaction-inclusion proof alone also does not prove a
later lifecycle state. Required options are an explicitly trusted, signed
Commons gateway receipt binding chain, record, envelope digest, lifecycle and
freshness, or a specified canonical-state proof/light-client verification path.
Those are missing integration contracts, not fields to fabricate. Unsigned
authenticated node responses may be labelled node-reported status, never
independently verified consensus. Transport authentication and proposer
signatures do not establish validator acceptance.

## Query, subscription and sync

SDK `query()` accepts an embedding, domain, top-k and `status_filter`; choose
`committed` explicitly and preserve disputed/challenged indicators rather than
assuming all returned knowledge is uncontested. The SDK's simple query method
does not expose every newer REST query field. Use the target node's supported
embedding space or its supported text/hybrid path; do not reuse personal-node
vectors without establishing compatibility.

Unit B can query eligible public records using its own enrolled identity and
explicit read policy. PUBLIC alone does not grant cross-author/domain discovery.
Subscription can initially mean bounded authenticated polling of approved
domains with explicit cached/stale/unavailable labels; it is not a verified
incremental replication protocol. Do not interpret missing results as deletions
without completeness/tombstone semantics.

Native federation sync exists at `/v1/federation/cross/{chain_id}/sync` but is
host-controlled pairwise federation, not Commons publication or a portable
public feed: `../sage/api/rest/server.go:766`. Boot state sync is not a reusable
Observer client. Do not enable federation, copy personal domain trees, install
a second SAGE node, or give a Lantern public validator keys to implement sharing.

## Existing outbox is unsafe as acceptance evidence

`software/runtime/src/sage_lantern/public_queue.py:141` checks a nonempty
signature string and algorithm label; it does not verify Ed25519, recompute the
signed digest, establish signer enrollment or validate the full manifest.
It also expects privacy classification `PUBLIC`, conflicting with the normative
`PUBLIC-CANDIDATE`. Its stored `content_sha256` hashes the entire serialized
envelope, not specifically the claim content. These hashes must have distinct
names and verification rules.

`update_state()` accepts an arbitrary receipt dictionary and performs an
unconditional update after reading state; it is neither receipt verification nor
cross-connection transition CAS. SQLite content is plaintext, and constructor
storage handling is not the bounded private managed-store pattern. Do not wire
this scaffold directly to a UI promising encrypted, signed queue admission or
verified acceptance. Anchors: `public_queue.py:56`, `public_queue.py:78`,
`public_queue.py:101` under `software/runtime/src/sage_lantern/`.

## Minimum implementable contract and release gates

1. Parent supplies trusted operator configuration: distinct Commons HTTPS/API
   target and expected chain identity/version, exact public domains, enrollment
   standing, separate proposer keystore and query scopes. Never accept endpoint,
   key path, domain authority or trust root from an ordinary UI request.
2. Build explicit Share knowledge preview/confirmation, immutable canonical
   PUBLIC-CANDIDATE bytes, real detached-signature verification, encrypted
   bounded queue and source-item retention. No transport until configured.
3. Implement the application-owned intake/idempotency/approval and receipt
   interfaces, or obtain an explicit amendment permitting a direct-submit pilot.
   A direct pilot must retain unknown-result reconciliation, not fake intake.
4. Implement fresh signed submit, persisted identifiers, authenticated status
   reconciliation and public query. Bind responses to target chain/content/
   author/domain/classification. Separate node-reported from verified outcomes.
5. Add append as a new deliberate candidate with explicit public lineage;
   corroboration/challenge use the existing governed APIs only when authorized.
   Do not mutate an accepted original, vote as a validator, or infer permission
   merely because the UI renders an action.

Acceptance fixtures must cover invalid signatures/digests, schema mismatch,
wrong chain/key/domain/classification, pending enrollment, revoked grants,
duplicate envelope with different bytes, restart during submit, lost responses,
forged/stale receipts, proposed-vs-committed distinction, challenged records,
private-field exclusion, queue confidentiality and independent Unit B identity.
End-to-end public acceptance remains blocked until a real configured public
service and the missing intake/evidence contracts are demonstrated. No count,
signature-shaped JSON, successful HTTP response or local state transition can
substitute for that evidence.

## Separate brief: PRIVATE dictated messages and recipient discovery

Added architecture audit only. This is not permission to send, enroll peers,
enable federation, publish user messages, or reuse the Commons proposer identity.
Private Messages and public knowledge remain distinct stores, consent flows,
identities/scopes and evidence labels. A dictated message must never become a
public derivative or canonical memory merely because it was sent or received.

### Composition contract (Lantern work, not an existing SAGE feature)

Dictate into a local private draft with stable paragraph IDs and draft revision.
Re-record replaces only the selected paragraph after preview/confirmation;
generation-fence late transcription so it cannot overwrite another paragraph or
an already-approved message. Preserve the remaining paragraphs. Sending requires
explicit confirmation of the exact assembled text and resolved recipient.
Changing text/recipient after confirmation invalidates approval and creates a new
send intent; retries of unchanged intent retain the same idempotency key. Clear
transient audio/text on cancellation/lock under explicit retention policy. SAGE
does not supply a speech editor or paragraph re-record API in the reviewed
surface. Incoming text is untrusted user content, not executable agent orders.

### Real discovery APIs; findable-only is not a universal flag today

- Signed `GET /v1/agents/directory` and `GET /v1/agents/lookup?name=...&limit=...`
  are local active-ordinary-agent metadata discovery, not a worldwide directory.
  Local lookup limits are 1–512 name bytes and at most 20 matches. It is not
  governed by a Lantern user's independent findable toggle. Source registration:
  `../sage/api/rest/server.go:728`; contract:
  `../sage/docs/reference/rest-api.md:762`.
- Signed `GET /v1/federation/available?agent_name=...&peer_cursor=...` provides
  caller-filtered discovery across approved agreements. Named queries require
  at least two Unicode code points, at most 512 UTF-8 bytes, and bounded peer
  paging; respect `complete`/continuation and do not silently enumerate all peers.
  Source: `../sage/api/rest/server.go:758` and
  `../sage/api/rest/federation_handler.go`.
- Legacy federation discovery depends on caller-visible shared domains.
  App-v26 linked discovery additionally supports exact current messaging
  relations with explicit receiver tuple consent; revocation and absent names
  have the same empty projection. This is narrower than a generic global
  opt-in directory. Peer lookup uses authenticated
  `POST /fed/v1/pipe/linked/directory`, not an endpoint for the browser to call.
  `../sage/internal/federation/v23_linked_directory.go:34`,
  `../sage/internal/federation/v23_linked_directory.go:391`.
- Lantern profile currently fixes `public_discoverable` to false and rejects
  true; it is not wired to SAGE federation disclosure consent:
  `software/runtime/src/sage_lantern/device_profile.py:41`,
  `software/runtime/src/sage_lantern/device_profile.py:110`.

**Gap:** user-findable-only disclosure needs a real consent/policy mapping at
every discovery and delivery boundary, including legacy shared-domain paths.
Do not implement it as a hidden UI row while SAGE still discloses that identity.
Named lookup may offer only eligible opted-in recipients. A result grants no
private-memory access and does not prove online presence. Linked projections'
presence-shaped booleans must not be displayed as real availability.

### Resolve, send, inbox and evidence

1. Signed `POST /v1/pipe/resolve` with `{"to":"..."}` resolves without sending.
   Disambiguate friendly names; retain exact `to_agent`, `source_chain_id` and
   `destination_chain_id`, never select the first similar name. Before send,
   re-resolve/revalidate the exact identity rather than rebinding by display name.
   `../sage/api/rest/pipe_handler.go:47`.
2. Federated `POST /v1/pipe/send` takes exact agent and source/destination chain,
   `payload`, optional `intent`, explicit TTL policy and caller-scoped
   `idempotency_key`. Empty `to_provider` for remote recipients. Fresh exact
   nonce-bound agent signing is required. Exact retry reuses the original row;
   changed payload under the same key conflicts. Local `POST /v1/messages` is
   the canonical exact-local-agent send, not a universal remote-send route.
   `../sage/api/rest/server.go:849`, `../sage/api/rest/server.go:864`,
   `../sage/docs/reference/rest-api.md:2334`.
3. Canonical `POST /v1/messages/receive` uses a receive token and session-bound
   claims; it mutates receipt/claim state. For passive browsing use the retained
   participant inbox/history contract, not repeated legacy `GET /v1/pipe/inbox`
   (which auto-claims). Read acknowledgement follows actual display, not polling.
   `../sage/api/rest/server.go:855`, `../sage/api/rest/server.go:865`.
4. Keep queued, delivered, claimed, read and completed separate. Negotiated
   `federated-pipeline-receipts-v2` supports exact-recipient signed claimed/read
   receipts and exact-sender `GET /v1/pipe/{pipe_id}/receipt`. Delivered is the
   remote operator's authenticated durable-admission acknowledgement, not human
   reading. Legacy/unsupported receipts stay unconfirmed. Read does not mean
   comprehension. `../sage/api/rest/server.go:870`,
   `../sage/docs/reference/rest-api.md:2296`.

### Encryption and recipient/radio cache boundary

Federation uses agreement-bound signed mTLS requests with pinned peer trust;
TLS/authentication failures do not authorize cached fallback:
`../sage/internal/federation/client.go:122`. Pipeline persistence uses vault
encryption helpers, including retained agent proofs:
`../sage/internal/store/pipeline_transport.go:197`. This is not proof of
recipient-only end-to-end encryption against either hosting SAGE operator;
vault protection depends on actual enabled/unlocked configuration. LoRa frames
do not automatically inherit mTLS protection from an HTTP federation endpoint.

Cache exact `(chain_id, agent_id)` with authenticated provenance, freshness and
policy/agreement binding. Here the credential's agent ID encodes its Ed25519
public key; do not promise continuity across credential rotation without a
verified migration binding. Display names are mutable labels, not cache keys.
Existing SAGE encrypted cached contacts are routing hints only: the transport
outbox performs live authenticated revalidation before delivery.
`../sage/internal/federation/pipe_targets.go:118`,
`../sage/internal/federation/pipe_outbox.go:171`.

No authenticated SAGE-agent-to-LoRa-address binding or private radio messaging
API was identified in the inspected directory/REST contract. Required new
binding must attest exact chain/agent key, radio identity/address, issuer,
version, validity and revocation/rotation semantics under an approved trust
root. A radio address, heard packet, signature-shaped field or fixed Unit A/B
route is not identity verification. Separate mutable transport routes from the
stable recipient identity; verify bindings on cache insertion and before use.
Until confidential payload transport and authenticated replay-safe delivery are
implemented for radio, mark private radio send unavailable, not plaintext fallback.

Acceptance additions: opted-out/removed recipient non-disclosure; ambiguous
names; revoked federation consent; stale cached identity/key/radio binding;
cross-chain ID collision; paragraph re-record races; exact retry after lost
send response; lock during dictation; receipt forgery; no automatic read claims;
no message content in public queue, public cache, telemetry or knowledge submit.
