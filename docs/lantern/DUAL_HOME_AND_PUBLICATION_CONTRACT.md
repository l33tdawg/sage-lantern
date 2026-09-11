# SAGE Lantern Dual-Home and Public Publication Contract v1

**Status:** Normative beta contract; SAGE encoding details pending `codex/sage` confirmation  
**Version:** 1.0-draft  
**Date:** 2026-08-09  
**Programme:** SAGE Commons  
**Product:** SAGE Lantern

## 1. Purpose

This contract defines how one Lantern can own a sovereign personal SAGE while also querying and proposing knowledge to the separate public SAGE Commons.

The beta baseline is:

- one full personal SAGE node;
- one bounded Commons proposal/query client with an offline proposal queue and public-only cache; and
- no local public-chain consensus process.

A separate long-lived public Observer process is `unsupported-do-not-use`. Post-v23 SAGE has Member/Manager/Admin roles with Root separate; a boot-time state-sync receiver is not a reusable Observer. The beta uses only the bounded Commons adapter/cache and an authenticated eligible query identity or future Commons query service.

Normative terms **MUST**, **MUST NOT**, **SHOULD** and **MAY** have their usual requirements meaning.

## 2. Absolute public-validator exclusion

**A SAGE Lantern is never a validator of the public SAGE Commons chain.**

Accordingly, a Lantern, its image and Fleet Admin MUST NOT:

- receive, generate, import, escrow, rotate, recover or display a public-chain validator private key;
- be admitted to the public CometBFT validator set or assigned public voting power;
- run a public-chain auto-voter;
- expose a “promote to validator” or equivalent action;
- use its proposer reputation, purchase status, radio identity, query/cache status or hardware identity as consensus authority; or
- describe public submission, peer synchronization or observation as validation.

Stable, always-on, separately governed servers operate the Commons validator set. Removing portable devices from the public quorum denominator is a liveness and key-safety property of the product.

## 3. Roles and authority planes

| Plane | Runtime/identity | Authority | Forbidden authority |
|---|---|---|---|
| Personal SAGE | Full local SAGE and personal ordinary agents | Personal memories, local agents, local domains and deliberate local federation | Public Commons governance or validation |
| Commons adapter | Public proposer key, queue and public cache | Prepare/sign eligible PUBLIC derivatives, submit to intake, query authorized committed public knowledge | Routine read access to the personal store; validator/Admin/Root authority |
| Public query/cache client | Commons adapter using an authenticated eligible identity or future external query service | Query/cache authorized public committed state | SAGE Observer role/process, voting, governance, public writes, personal-store access |
| MeshCore companion | Separate radio identity | Bounded store-and-forward transport | SAGE identity, publication decision or delivery proof |
| Fleet Admin | Dedicated device-management identity | Provisioning status, software/hardware lifecycle and sanitized evidence | Personal CEREBRUM authority, public consensus or proposal authorship |
| Commons Network Admin | Public-governance identities | Intake policy, proposer enrollment, stable validators, public releases and audit | Personal SAGE administration |
| Local CEREBRUM | Machine-local personal Root/current authorized Admin | Personal policy, agents, domains, local publication consent/policy and recovery | Fleet-wide control or automatic public validator admission |

Authority is checked at the target plane. A Fleet Admin role, dashboard session or successful transport does not become local CEREBRUM or Commons authority.

## 4. Process and storage isolation

### 4.1 Required baseline

The baseline Lantern runs:

1. `personal-sage` — the full private local SAGE;
2. `commons-adapter` — a non-consensus client containing the PUBLIC derivative builder, signed outbound queue, receipt journal and public query cache; and
3. the Lantern UI/voice services, which request typed operations from those two components.

The Commons adapter MUST NOT mount, scan or open the personal SAGE home. A typed local API controlled by personal policy provides only the exact candidate fields deliberately selected for derivation. Ambient recall results, prompts, transcripts, audio, secrets and unrelated personal context MUST NOT cross that API.

The adapter's cache contains PUBLIC Commons material only. Cached public material is not personal canonical memory unless the user explicitly performs a governed local save/copy action.

### 4.2 Unsupported second process

The Lantern MUST NOT run or advertise a long-lived SAGE Observer process. First-class multi-instance packaging is also unsupported. Mechanically starting another process does not create a supported product role. If a future SAGE release defines such a role, it requires a new RFC, fully separate homes/chain IDs/keys/listeners/configuration/backups, hardware evidence and capability-state promotion before use.

### 4.3 Resource priority

Personal PTT, audio, local inference and personal SAGE take priority over Commons cache synchronization, indexing, pack seeding and updates. The supervisor MUST pause background work before allowing thermal throttling, low-memory instability, storage exhaustion, material voice latency regression or unsafe portable-power behaviour.

## 5. Identity and key isolation

| Identity/key | Generated | Storage | May be reused? |
|---|---|---|---|
| Personal SAGE Root/current credential | On-device personal bootstrap/recovery ceremony | Personal SAGE home only | Only through a supported exclusive full-node restore of the same personal SAGE |
| Personal ordinary agent | On-device after imaging | Personal managed identity location | Never cloned to another Lantern |
| Commons public proposer | On-device during Commons enrollment | Commons adapter keystore only | Never cloned; rotation preserves explicit public lineage |
| Radio identity | On-device/Heltec provisioning | Radio/adapter storage | Never cloned |
| Public validator key | Never generated or installed | Not applicable | Forbidden on Lantern |

Private keys MUST NOT leave their owning trust plane during routine provisioning, monitoring, evidence export or support.

For the v11.18.4 beta, the Commons proposer is an ordinary public-chain **Member** identity with clearance `0`/`PUBLIC`, registered and approved through the normal agent lifecycle. It receives only the exact read and submission scopes required for pre-created hyperlocal intake domains. It receives no Admin, Manager, governance-operator or validator authority. A practical two-unit layout is one pre-created domain per proposer, such as `commons.proposer.<short-public-id>.hyperlocal`, owned or granted explicitly rather than claimed through an unrestricted shared namespace.

## 6. PUBLIC derivative

### 6.1 Definition

A **PUBLIC derivative** is a newly created, deliberately bounded public candidate derived from an explicitly selected observation or personal item. It is not the private source record, a database export or a transparent copy of local context.

Creation requires:

1. an explicit user action or a locally approved narrow publication policy;
2. selection of the exact public claim and evidence references;
3. privacy/locality minimization and preview;
4. deterministic construction of the manifest;
5. signature by the Lantern's Commons proposer key; and
6. durable queueing before transport.

The signed manifest is immutable. Retry counts, relay hops, intake decisions and receipts are stored beside it and MUST NOT rewrite it.

The queue stores this canonical logical envelope and its detached proposer signature; it does **not** store a pre-signed HTTP request. SAGE REST action proofs contain a timestamp and fresh nonce and can expire while a Lantern is offline. The adapter therefore verifies the queued envelope and signs the live SAGE request freshly when connectivity and the required approval are present. The encrypted queue is persisted before the UI reports `QUEUED_LOCAL`.

### 6.2 Canonical manifest schema

The v1 logical media type is `application/vnd.sage.commons.public-derivative+json;v=1`. Final byte canonicalization and SAGE transaction mapping require `codex/sage` approval. Until then, implementations MUST use one documented canonical JSON encoding and sign its SHA-256 digest.

```json
{
  "schema": "sage.commons.public-derivative.v1",
  "envelope_id": "uuid-or-equivalent-128-bit-id",
  "proposer": {
    "agent_id": "hex-ed25519-public-id",
    "installation_ref": "privacy-preserving-public-installation-id"
  },
  "claim": {
    "content": "bounded public claim",
    "content_sha256": "hex-sha256-of-canonical-content",
    "memory_type": "fact|observation|inference|procedure|other-versioned-value",
    "domain": "approved-public-domain",
    "outcome_class": "approved-versioned-class",
    "language": "BCP-47-tag"
  },
  "provenance": {
    "source_class": "human_observation|local_inference|sensor|imported_source|relayed_report",
    "source_commitment": "opaque-local-source-commitment-or-null",
    "evidence": [
      {
        "kind": "versioned-evidence-kind",
        "sha256": "hex-sha256",
        "public_uri": "optional-public-uri"
      }
    ],
    "derivation": ["bounded-versioned-transformation-label"]
  },
  "locality": {
    "scheme": "named-area|geohash|administrative-area|none",
    "value": "policy-minimized-location",
    "precision": "explicit-versioned-precision",
    "capture_method": "user_selected|sensor|inferred|reported|none",
    "consent_scope": "public"
  },
  "time": {
    "observed_at": "RFC3339-or-null",
    "created_at": "RFC3339",
    "valid_from": "RFC3339-or-null",
    "expires_at": "RFC3339-or-null"
  },
  "confidence": {
    "score": 0.0,
    "method": "versioned-method-label",
    "basis": "bounded-public-explanation"
  },
  "privacy": {
    "classification": "PUBLIC-CANDIDATE",
    "redactions_applied": true,
    "review_mode": "explicit_user|approved_local_policy",
    "reviewed_at": "RFC3339"
  },
  "lineage": {
    "replaces_public_memory_id": null,
    "derived_from_public_memory_ids": []
  },
  "producer": {
    "lantern_release": "version",
    "sage_release": "version",
    "manifest_builder": "version"
  },
  "signature": {
    "algorithm": "Ed25519",
    "signed_digest_sha256": "hex-sha256-of-canonical-manifest-without-signature",
    "value": "encoded-signature"
  }
}
```

### 6.3 Manifest rules

- `envelope_id`, proposer identity and signed digest form the idempotency identity.
- `content_sha256` MUST be recomputed at every boundary.
- Envelope `classification` MUST equal the Commons application label `PUBLIC-CANDIDATE`; any other value fails local preflight. It is not yet canonical SAGE `PUBLIC`. Only an approved fresh submit maps it through a versioned adapter to SAGE numeric classification `0`/`PUBLIC`, and the request/evidence records that mapping version.
- Location MUST use the least precision useful to the approved domain. A signature proves key control, not physical presence.
- `observed_at`, `created_at`, intake receipt time and chain inclusion time remain separate facts.
- `expires_at` controls current usefulness, not deletion of audit history.
- Proposer confidence is evidence metadata, not a validator verdict or final Commons confidence.
- A private source identifier MUST NOT be published. `source_commitment`, when used, is an opaque non-reversible commitment suitable only for later local proof.
- Evidence is embedded only when explicitly PUBLIC and within limits; otherwise the manifest carries a digest or approved public URI.
- Relay metadata is appended as separately signed transport records and MUST NOT alter proposer authorship.

## 7. Local publication flow

```text
private source/observation
  → select exact claim
  → minimize locality and evidence
  → PUBLIC preview and approval
  → deterministic manifest
  → proposer signature
  → durable offline queue
  → approved HTTPS or MeshCore-to-gateway transport
```

The local preflight MUST reject:

- application classification other than `PUBLIC-CANDIDATE`;
- unsupported domain/outcome class/schema;
- absent or invalid consent/policy evidence;
- content, locality, evidence or explanation over configured bounds;
- impossible/reversed validity times;
- already expired current-state claims, unless explicitly reclassified as historical evidence;
- missing/invalid signature or digest;
- direct identifiers/secrets detected by the approved local privacy rules; and
- a proposal ID already bound to different bytes.

A model MAY suggest a derivative. A model MUST NOT authorize or sign publication by itself unless a specific locally governed policy has explicitly granted that narrow behaviour.

## 8. Intake, quarantine and federation boundary

The baseline submission target is a stable Commons intake service, not a validator mempool endpoint and not Federation Copy.

Intake performs signature/canonicalization/replay checks, size/rate/schema/domain limits, privacy and locality screening, duplicate routing, expiry checks and allowed moderation. It persists the original immutable manifest in quarantine. The queued envelope is not a reusable signed HTTP request. Intake owns envelope-level idempotency. After `APPROVED`, the Lantern MUST reconnect and issue exactly one fresh nonce/timestamp-bound `/v1/memory/submit` while online so that its ordinary Commons identity remains the canonical author. Generic v11.18.4 memory submission is not request-idempotent: after an ambiguous timeout, the adapter MUST reconcile transaction/memory evidence and MUST NOT blindly resubmit. The request explicitly uses classification `0`/`PUBLIC`, an approved pre-created domain, a time-bounded `observation` type where appropriate, and the immutable envelope/hash.

If asynchronous admission is later required, a narrowly authorized intake agent MAY submit a new curated derivative containing the original signed envelope and hash. In that mode the intake agent—not the Lantern—is the canonical on-chain author, and every API/UI MUST say so. Intake and field laptop never sign as the user. Intake approval never equals validator commitment in either mode.

Quarantine is off-consensus review state. It MUST NOT be described as SAGE `proposed` or `committed` memory. Non-deterministic model moderation, external lookups, live geofencing and malware services remain outside consensus. Public validators apply only byte-identical deterministic logic based on canonical inputs and committed state.

Native SAGE federation MUST NOT be used as the default Lantern publication API:

- federation is pairwise whole-SAGE trust rather than fleet-scale public intake;
- JOIN shares no domains by itself and Read/Copy require explicit policy;
- Copy is a two-sided synchronization action, not a proposal quarantine queue;
- remote Write is unavailable; and
- federation must not expose an agent's private domain tree merely to publish one derivative.

Current v11.18.4 Federation Copy is additionally unsuitable for canonical Lantern authorship: the receiving operator becomes the destination-chain author and origin remains in off-consensus synchronization provenance. Copy also has a SQLite receiver limitation. It MAY remain an operator migration or trusted-institution tool, never the Lantern-facing hyperlocal intake lane.

Federation MAY be used separately for deliberately trusted institution or Knowledge Point relationships. It does not replace the PUBLIC derivative contract.

## 9. Receipt and state contract

One overloaded status is insufficient. Each proposal record has independent axes.

### 9.1 Local state

`draft | awaiting_approval | signed_queued | cancelled | local_expired | local_error`

### 9.2 Transport state

`not_sent | sending | relay_queued | intake_delivered | retry_wait | transport_failed`

### 9.3 Intake state

`unknown | received | quarantined | needs_review | eligible | intake_rejected | intake_expired`

### 9.4 Chain state

`not_submitted | submitted | proposed | committed | challenged | deprecated | chain_rejected`

Reinstatement is a canonical transition from `challenged` back to `committed`, recorded as an event/receipt rather than a separate enduring memory status.

### 9.5 Knowledge validity state

`not_yet_valid | current | stale | expired | superseded | conflicted`

### 9.6 Minimum user-visible receipts

The product-facing vocabulary is:

`QUEUED_LOCAL | SENT | RECEIVED | QUARANTINED | NEEDS_ACTION | APPROVED | SUBMITTED | COMMITTED | REJECTED | EXPIRED | SUPERSEDED`

Every transition includes `envelope_id`, `reason_code`, `updated_at`, the immutable content hash and an evidence, intake, proposal, memory or transaction reference when one exists. Duplicate transport returns the existing envelope lineage and receipt; it does not create another logical observation. `RECEIVED` is never `SUBMITTED`, and `SUBMITTED` is never `COMMITTED`.

Transitions MUST be monotonic within a manifest generation except for retryable transport attempts. A replacement creates a new `envelope_id` and lineage link; it never rewrites the signed original.

The following evidence is distinct:

- **Local signed receipt:** proves durable queue admission on the Lantern.
- **Transport receipt:** proves a carrier accepted bytes; it does not prove Commons intake.
- **Intake receipt:** intake signs proposal ID, digest, received time and intake record ID.
- **Chain submission receipt:** binds the intake record and proposal digest to a transaction hash/reference.
- **Consensus receipt:** identifies committed/deprecated/rejected outcome, chain ID, height and canonical public memory ID where applicable.

Fleet Admin MUST report these receipts without routinely ingesting proposal content. “Sent,” “received,” “eligible,” “proposed” and “committed” are never synonyms.

## 10. Query and cache contract

The Commons adapter uses a registered/authenticated eligible identity to query authorized committed public knowledge through the approved SAGE read surface. “Unexpired/current” is a Commons cache/publisher policy, not a generic v11.18.4 canonical TTL. Current-answer views MUST require canonical `committed`, exclude `deprecated`, apply the named publisher validity policy and return locality plus visible provenance; canonical `challenged` and historical/deprecated material remain available only in appropriately labelled views. The adapter maintains a bounded public-only cache containing:

- result content and public memory ID;
- public chain ID and last verified height/time;
- domain/locality scope covered by the sync/query;
- confidence and validity/expiry metadata;
- source/provenance fields permitted for public recall; and
- integrity proof or signed gateway receipt available from the supported SAGE surface.

The UI labels each answer source `personal`, `public-live`, `public-cached`, `public-stale` or `unavailable`. An expired item MUST NOT be presented as current. Public cache loss MUST NOT damage personal SAGE; personal backup MUST NOT silently include or restore a stale public cache as canonical personal memory.

There is no anonymous v11.18.4 SAGE read path. Live canonical reads use a registered/authenticated eligible identity and SAGE classification/RBAC. A future external Commons query service remains an application-owned boundary with its own abuse/privacy/cache policy and never makes the local cache canonical.

## 11. Provisioning and recovery

### 11.1 Golden image

The image MUST contain no initialized personal `SAGE_HOME`, Root/Admin key, ordinary agent key, public proposer key, second SAGE home/node/TLS/key material, receive token, idempotency journal, signed proposal queue, receipt journal, federation identity/agreement or radio identity.

### 11.2 Clone completion

After cloning, each Lantern generates all required identities locally and proves duplicate absence before becoming Ready. Unit B MUST NOT inherit Unit A proposal IDs, nonces, queue entries, receipts or public cache trust state.

### 11.3 Recovery rules

- A supported full stopped-node restore may preserve the same personal SAGE identity only when the old instance is proven unable to run concurrently.
- Public proposer recovery/rotation follows Commons enrollment and revocation policy; it MUST NOT copy the key casually from another Lantern.
- Pending manifests signed by a retired key remain immutable and are quarantined until policy decides whether that historical signature is still eligible. They are never silently re-signed.
- Reimaging a lived-in personal SAGE is not routine recovery. Use the supported complete backup/restore procedure.
- No recovery path may ever introduce a public validator key onto a Lantern.

## 12. Administration boundaries

### Fleet Admin may

- inventory public identity fingerprints and last-observed state;
- coordinate identity-safe provisioning and signed software rollout;
- show sanitized personal readiness, proposal queue/receipt counts and public cache freshness;
- revoke device-management access and request public proposer suspension through the Commons governance workflow; and
- collect sanitized acceptance evidence.

Fleet Admin MUST NOT browse personal memories, approve local agent/domain policy, hold private proposer keys, manufacture publication intent, change Commons consensus membership or operate personal recovery authority.

### Commons Network Admin may

- enroll/suspend/revoke public proposer identities;
- operate intake schema, quotas, quarantine, moderation and publisher identities;
- operate stable validators and public release/governance procedures; and
- inspect public proposals, receipts, public chain outcomes and abuse evidence under policy.

Commons Network Admin MUST NOT administer a Lantern's personal SAGE or infer access from fleet ownership.

### Local CEREBRUM may

- govern personal agents, domains, Access Groups and local federation;
- approve or revoke the local policy that may derive a PUBLIC candidate;
- show the exact derivative preview and consent evidence; and
- perform supported personal backup, handover and recovery.

Local CEREBRUM cannot commit a proposal to Commons or grant public validator power.

## 13. Beta evidence

### 13.1 Mandatory baseline evidence

The baseline passes only when evidence shows:

1. five offline PTT voice loops use the personal SAGE while Commons, Fleet Admin and all public validators are unreachable;
2. one eligible PUBLIC derivative is previewed, approved, canonically built, signed and durably queued offline;
3. reboot preserves the exact queued bytes, signature and proposal ID;
4. reconnect delivers the proposal idempotently; repeated transport produces one intake record;
5. intake quarantine and chain states produce distinct verifiable receipts through terminal outcome;
6. malformed, altered, duplicate, expired, unsupported-domain and non-PUBLIC manifests fail at the correct boundary;
7. a signed proposal through MeshCore, when enabled, preserves original authorship and detects relay alteration/duplication;
8. live, cached, stale, expired and unavailable public query results are labelled correctly;
9. no unrelated personal memory, prompt, transcript, raw audio, key or source identifier appears in the manifest, adapter store, cache, telemetry or Fleet Admin;
10. imaging Unit B creates unique personal, proposer and radio identities and empty queues/journals; and
11. inspection confirms no public validator key, voting power, voter process or promotion control exists on either Lantern.

### 13.2 Unsupported-capability evidence

Inspection MUST confirm no second public SAGE process/home, long-lived state-sync receiver, Observer label, public validator key or multi-instance dependency exists. Negative tests prove the Commons adapter/cache operates without those unsupported capabilities.

## 14. Confirmed v11.18.4 mapping and remaining encoding decisions

`codex/sage` has confirmed the beta role and transport mapping:

- the proposer is a distinct ordinary Member identity at clearance `0`/`PUBLIC`, with exact read/submission grants and no Admin/Manager/governance/validator authority;
- the queue stores an encrypted canonical logical envelope and detached signature, while the live SAGE REST request is signed freshly at send time;
- durable intake/quarantine precedes trusted use; Federation Copy is not the Lantern-facing publication path;
- the preferred approval path asks the Lantern to submit freshly so its ordinary identity remains canonical author;
- public answers filter for committed and unexpired records and visibly distinguish raw proposer observations from curated derivatives; and
- app-v26 does not currently provide a hyperlocal schema, freshness or factual-verification gate, so the product MUST NOT claim otherwise.

Before implementation is called v1-compatible, the remaining encoding/API details still require agreement:

1. exact per-domain grants and rate policy for the proposer and any constrained intake identity;
2. which manifest fields belong in canonical `MemoryRecord` state versus versioned content;
3. the authoritative public receipt/proof and committed-query interfaces;
4. expiry/supersession/conflict semantics for hyperlocal knowledge;
5. canonical JSON/signature encoding, size ceilings and compatibility rules for `sage.commons.public-derivative.v1`; and
6. exact authenticated public-query and future Commons query-service boundaries.

Until those are resolved, the manifest is the logical application contract and the public chain integration remains behind a beta feature flag.
