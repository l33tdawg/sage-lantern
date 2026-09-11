# SAGE Commons Dual-Network Architecture and Intake Options

**Status:** Product direction set; intake design pending `codex/sage` review  
**Date:** 2026-08-09  
**Programme:** SAGE Commons  
**Device product:** SAGE Lantern  
**Decision owners:** SAGE Commons product owner and `codex/sage`

## 1. Product decision

SAGE Commons has two related but sovereign layers:

1. **Personal network:** every Lantern/ROCK runs its own private, local-first SAGE node. Its voice loop and personal memory work without internet access.
2. **Public network:** stable, always-on, independently governed **Lightbearer Validators** run the public SAGE Commons chain and its validator set.

Every Lantern is a signed **public proposer and query client**. It may contribute hyperlocal knowledge to Commons and query committed public knowledge. A Lantern is **never a validator of the public chain**.

This is an intentional product boundary, not a temporary lack of privilege. Owning a Lantern does not grant consensus voting power, and the fleet dashboard must not offer validator promotion for a Lantern.

## 2. Why proposer is not validator

Current SAGE assigns validators live consensus responsibilities:

- every admitted validator's auto-voter signs one validator vote;
- silent validator weight remains in the memory-quorum denominator;
- CometBFT liveness depends on sufficient reachable voting power;
- public validators need stable P2P/RPC networking, coordinated upgrades, monitoring and secure block-signing keys; and
- one validator key must have exactly one live signer to avoid double-sign/equivocation and nonce races.

Portable devices sleep, roam, lose internet, run on batteries and may be lost or compromised. Adding them to the public validator set would make public availability depend on an intermittent consumer fleet. Keeping validation on stable governed servers protects quorum liveness, makes release coordination tractable and keeps high-value consensus keys off portable hardware.

A signed proposal still provides useful accountability. It proves control of a public proposer key and binds the candidate bytes. It does **not** prove physical location, sensor accuracy, truth or eligibility for public commitment. Stable validators retain the job of applying deterministic policy and reaching consensus.

## 3. Non-negotiable boundaries

- Personal SAGE and public Commons use different chain IDs, homes, keys, data and policies.
- Each Lantern receives a unique ordinary personal agent identity, unique public proposer identity and unique radio identity after imaging.
- Personal Root/Admin, public proposer, federation transport and radio keys are separate. No live identity material is present in the golden image.
- Private memory never becomes public through background replication.
- Publication is explicit, attributable and policy-gated.
- Public queries use live Commons when connected and an explicitly stale bounded cache when offline.
- Public proposals can be signed and queued offline, but cannot be described as submitted, proposed or committed until the corresponding public stages occur.
- MeshCore is an untrusted constrained transport. It can relay an original signed proposal to an internet gateway, but it is not a SAGE authority or validator tunnel.
- Pairwise SAGE federation remains separate. It connects sovereign SAGEs for governed Read/Copy and messaging; federation transport is off-consensus and remote Write is unavailable.
- A public proposer receives no public Admin/Root, governance, validator, arbitrary domain-claim or private-peer access.

## 4. Target topology

```text
SAGE Lantern
  voice / UI / local agent
             |
             v
  Personal SAGE (private, sovereign, offline-capable)
             |
             | explicit user/policy-approved candidate only
             v
  Commons proposal/query client
     |                |
     | signed queue   | bounded public cache
     v                v
  HTTPS or MeshCore-to-gateway transport
             |
             v
  Commons intake and quarantine
     authenticate → rate-limit → schema/privacy checks → review/pre-validation
             |
             v
  Public SAGE Commons chain
     Lightbearer Validator 0..3 → deterministic vote/quorum → committed public knowledge
```

The Lantern runs one SAGE consensus runtime: its personal node. The public side is a bounded client, queue and cache—not a second CometBFT replica.

## 5. Public proposal/query implementation options

### Option A — Direct signed submission to a public validator API

The Lantern queues a normal SAGE memory request and submits it directly to a public validator REST endpoint when online. It queries that endpoint for committed public knowledge.

**Benefits**

- Smallest amount of new infrastructure.
- Reuses current signed-request, RBAC and memory lifecycle where the existing schema is sufficient.
- Fastest route to a disposable laboratory proof.

**Risks and limits**

- Internet-facing validators absorb authentication, rate-limit, malformed-content and denial-of-service traffic.
- A request that passes the normal transaction gate becomes on-chain `proposed` before any non-deterministic moderation or external evidence check can occur.
- Existing memory fields may not bind all required hyperlocal provenance, locality precision and validity metadata.
- Sending to one validator endpoint creates avoidable ingress coupling even though consensus itself is replicated.
- Current content-validation documentation describes a historical bounded fork window; it cannot be assumed to provide a current Commons-specific intake gate.

**Use:** a tightly rate-limited lab test only, not the preferred public service.

### Option B — Stable Commons intake gateway with quarantine

Lanterns submit canonical signed logical proposal envelopes—not reusable signed HTTP requests—to a stable public intake service. The service verifies the original signature, applies cheap abuse/privacy/schema controls, owns envelope-level idempotency, persists quarantine, and performs allowed review/pre-validation. After `APPROVED`, the Lantern reconnects and issues one fresh nonce/timestamp-bound `/v1/memory/submit` under its distinct ordinary proposer identity. Queries use a registered/authenticated eligible SAGE identity or a separately governed Commons-owned serving tier; v11.18.4 has no anonymous public SAGE read API.

**Benefits**

- Shields validator REST/mempool surfaces from untrusted fleet ingress.
- Makes offline retry and idempotency first-class.
- Keeps non-deterministic moderation, external-source checks and privacy review outside consensus.
- Supports per-proposer quotas, revocation, reputation and abuse response without changing validator membership.
- Can expose clear end-to-end lifecycle states to Lanterns and administrators.

**Trust and security requirements**

- The original Lantern signature, content hash, proposer ID and immutable provenance survive intake; the Lantern remains canonical author in the preferred reconnect flow.
- Generic v11.18.4 memory submission is not request-idempotent. Ambiguous timeout triggers evidence reconciliation, never blind resubmission.
- If a future asynchronous path uses a constrained intake publisher, it submits a new derivative and becomes the canonical author; the original envelope/signature remains provenance and every UI states the distinction.
- Intake and field laptop never sign as the user. The intake publisher, if enabled, is not Root/Admin or validator.
- Gateway compromise can censor, delay or flood intake, but must not forge a Lantern signature or commit without public-validator consensus.
- Multiple stateless ingress instances may share one durable queue; idempotency is keyed by proposal ID plus content hash.
- Accepted content still passes current public-chain RBAC and deterministic validation. Quarantine approval is not consensus approval.

**Availability**

- Lanterns keep working locally during internet failure and retain an encrypted queue.
- Intake may be replicated across regions without altering the validator set.
- A gateway outage delays public proposals but cannot stop personal SAGE or public consensus between stable validators.

**Use:** recommended production direction and the principal design for the beta.

### Option C — Federation Copy as public intake

Each personal Lantern SAGE federates with Commons and offers selected local domains for Copy. Commons opts in to save those copies.

**Benefits**

- Preserves sovereign source-chain provenance and uses existing trusted Read/Copy concepts.
- Naturally supports source-side permission withdrawal for future transfers.

**Risks and limits**

- Federation is pairwise whole-SAGE trust with explicit agreements and exports, which creates heavy lifecycle and operator ceremony for a large consumer fleet.
- Copy is a synchronization relationship, not a public-submission inbox or moderation queue.
- Remote Write remains unavailable; accepted copies enter as locally authorized submissions under federation semantics that may not match public proposer attribution.
- It risks exposing broader domain trees when the product requirement is one deliberately selected public proposal.

**Use:** appropriate for trusted institution/Knowledge Point exchange, not the default Lantern-to-public contribution path.

### Option D — MeshCore store-and-forward to an intake gateway

This is an alternate transport for Option B, not a separate authority model. An offline Lantern sends a small signed proposal through MeshCore to a trusted internet-connected Knowledge Point or companion, which relays it unchanged to Commons intake.

**Benefits**

- Provides useful hyperlocal contribution where the Lantern itself lacks internet.
- Reuses the beta's constrained transport evidence.

**Risks and limits**

- LoRa payload, fragmentation, retry, expiry and duplicate behaviour are not yet measured.
- The relay learns transport metadata and can delay/drop/replay traffic.
- Original signature verification, TTL, duplicate rejection and bounded payload rules are mandatory.
- Large evidence stays referenced by digest or waits for higher-bandwidth transport.

**Use:** add only after the Heltec/MeshCore host protocol is measured. HTTPS intake remains the baseline.

## 6. Hyperlocal proposal contract

The exact current-SAGE encoding needs `codex/sage` review. Logically, each immutable signed envelope should include:

- schema version and globally unique proposal ID;
- public proposer ID and signature over the complete canonical envelope;
- privacy-preserving Lantern installation reference, separate from personal SAGE identity;
- bounded content, content hash, public domain and outcome class;
- observation time, proposal creation time and later gateway receipt time;
- locality claim and precision, such as region, named site or bounded geohash;
- locality capture method and user consent classification;
- provenance type: human observation, local inference, imported source, attached sensor or relayed report;
- supporting evidence hashes/references, without assuming private evidence is publishable;
- proposer confidence and the stated basis/method for it;
- valid-from and expiry/refresh time for time-sensitive knowledge;
- privacy/classification/redaction declaration;
- replacement or supersession reference when updating an older claim;
- software/schema version; and
- optional relay provenance that never replaces the original author.

Location is sensitive. The envelope should use the least precision that is useful for its domain. An exportable Ed25519 key cannot prove physical presence. High-assurance locality requires corroboration, trusted sensor/attestation work or multiple independent observers.

## 7. Offline and query behaviour

### Offline proposal queue

- Sign the immutable envelope only after explicit user or approved policy authorization.
- Never silently rebuild observation time, locality or confidence on reconnect.
- Encrypt queued public candidates at rest where practical and isolate them from personal SAGE identity state.
- Retry idempotently with proposal ID and content hash. A retry cannot create another public candidate.
- Expire short-lived observations before upload or submit them only as clearly historical evidence.
- Show local states honestly: `draft`, `awaiting_approval`, `queued_offline`, `relaying`, `received_by_intake`, `quarantined`, `submitted_to_chain`, `proposed`, `committed`, `deprecated`, `rejected`, `expired`, `failed`.

### Public query client

- Online queries return only the public identity's authorized result set and default to committed memories.
- A bounded local cache may support offline recall, but every result shows last verified chain height/time and current staleness.
- Cached public knowledge remains separate from personal canonical memory unless the user explicitly saves/copies it locally.
- Expired hyperlocal facts are excluded from “current” answers by domain policy while remaining auditable as historical evidence.

## 8. Intake, quarantine and validation

The intake lifecycle should remain distinct from SAGE's on-chain memory lifecycle:

```text
queued_offline → received → quarantined → eligible → submitted_to_chain
                                  ↘ rejected | expired | needs_review
submitted_to_chain → proposed → committed | deprecated | rejected
```

Before chain submission, intake may perform:

- signature, canonical encoding and replay checks;
- size, schema, supported-domain and outcome-class validation;
- per-identity, device cohort, IP and locality rate limits;
- privacy/secret and over-precise-location screening;
- exact/near-duplicate routing;
- evidence availability checks;
- human or automated moderation whose result is explicitly attributed; and
- expiry/freshness policy based on the claimed observation and trusted receipt times.

After submission, validators may perform only deterministic checks derived from canonical inputs and committed state. They must not call live web services, local moderation models, wall clocks or location APIs during consensus. Every validator must run byte-identical consensus validation logic at the activated application version.

**Freshness** belongs to the observation, not the transport. Preserve observation, receipt and inclusion times separately. **Expiry** should remove stale claims from current recall without erasing audit history. **Proposer confidence** is evidence metadata, not final truth; public confidence should also reflect validator verdicts, corroboration, source history and challenges.

## 9. Recommended staged beta

### Stage 0 — Personal Lantern acceptance

- Run one personal SAGE per Lantern with unique post-image identities.
- Prove five offline Unit A PTT loops and the bounded A↔B MeshCore exchange.
- Keep all Commons credentials out of the golden image.

### Stage 1 — Disposable public Commons testnet

- Stand up four independently operated Lightbearer Validator nodes using one exact SAGE release.
- Provision one constrained public proposer identity per Lantern.
- Implement the smallest Option B intake queue and committed-query path over HTTPS.
- Demonstrate explicit approval, signed provenance, idempotent reconnect and zero unrelated private-context leakage.

### Stage 2 — Hyperlocal intake policy

- Select one bounded public domain and schema.
- Exercise fresh, stale, duplicate, malformed, over-precise, low-confidence and conflicting observations.
- Verify quarantine, review, expiry, chain proposal, validator verdict and user-visible status transitions.

### Stage 3 — MeshCore relay

- After measuring the real Heltec protocol, relay the same immutable signed envelope through a Knowledge Point.
- Test duplicate delivery, fragmentation, delay beyond expiry and relay tampering.
- Prove that the relay cannot change author, content or confidence.

### Stage 4 — Limited field cohort

- Expand proposer enrollment only after quotas, revocation, monitoring, moderation and privacy response are tested.
- Keep the stable-server validator roster governed separately from the device fleet.

## 10. Decision gates

| Gate | Evidence required | Stop condition |
|---|---|---|
| G1: personal sovereignty | Offline voice loop and personal memory remain available | Public outage breaks local use |
| G2: identity separation | Fresh personal, public proposer and radio keys after imaging | Any cloned/live shared identity |
| G3: safe publication | Explicit consent/policy, provenance preview, redaction and no ambient private context | Implicit or over-broad publication |
| G4: reliable queue | Encrypted persistence, expiry, idempotent retries and crash recovery | Duplicate public proposals or silent loss |
| G5: intake safety | Authentication, quotas, schema/privacy checks and durable quarantine | Fleet traffic reaches validator mempool unchecked |
| G6: attribution | Original signature/content hash survive HTTPS and Mesh relays | Gateway/relay appears as observation author |
| G7: deterministic consensus | Exact validation logic/version across all stable validators | Node-local or external checks can fork decisions |
| G8: public liveness | Validators progress while all Lanterns and gateways are offline | Device fleet affects consensus quorum |
| G9: stale-data safety | Observation/receipt/inclusion times and expiry affect recall correctly | Old local facts presented as current |

## 11. Admin software implications

The two control products should share design language but not authority.

**Lantern Fleet Admin** manages provisioning, per-unit identity creation, software versions, peripherals, resource telemetry, public proposer enrollment/revocation, queue health, public-cache staleness and Mesh relay state. It never exports private memory by default and never offers public-validator promotion.

**Commons Network Admin** manages stable validator health, validator identities/power, voter readiness, proposed backlog, blocks/mempool, peer reachability, app-version compatibility, intake service health, quarantine policy, proposer enrollment/quotas, governance and audit history.

The UI must distinguish:

- personal Lantern SAGE;
- ordinary public proposer;
- intake gateway publisher;
- Mesh relay/Knowledge Point;
- federated remote SAGE;
- stable Lightbearer Validator; and
- quarantined candidate versus on-chain proposed/committed memory.

“Online,” “paired,” “synced,” “federated,” “proposer” and “validator” are never synonyms. A valid signature is not a verified locality claim, `received` is not `proposed`, and `submitted` is not `committed`.

## 12. Exact questions for `codex/sage`

1. What current v11.18.4 RBAC role/security profile should enroll each Lantern as a public proposer that can query permitted public domains and submit only to controlled intake domains, without governance, validation, arbitrary domain claims or Admin/Root access?
2. Can current `/v1/memory/submit` preserve a canonical original Lantern signature when a separate intake publisher performs the authorized chain submission, or is a new provenance envelope/transaction required?
3. Should the intake publisher be one constrained ordinary agent, one per gateway, or one per original Lantern? How is original authorship kept immutable in each model?
4. Which existing SAGE endpoint can perform deterministic pre-validation without entering the mempool, and exactly which checks does it cover at app-v26?
5. The documented layer-2 content-validation gate describes a historical bounded fork window. What is the current app-v26 extension seam for a Commons-specific closed schema, signer eligibility and deterministic hyperlocal validation?
6. Where should the durable quarantine queue live? Can existing Messages/Tasks safely model review without confusing off-chain status with the on-chain memory lifecycle?
7. Which hyperlocal fields can fit existing `MemoryRecord` canonical state, and which require a versioned content envelope or a future consensus schema?
8. How should expiry work without deleting audit history or returning stale observations as current? Does current confidence decay/deprecation cover this, or is valid-time metadata and query filtering new work?
9. How should Commons aggregate proposer confidence, validator decisions, independent corroboration, source history and challenges without allowing self-reported confidence to dominate?
10. What is the correct enrollment and revocation flow for potentially thousands of ordinary proposer identities, including Sybil/rate controls that do not affect validator PoE weight?
11. Can a MeshCore/Knowledge Point relay submit an unchanged original signature while adding separately signed relay provenance and preserving idempotency?
12. Should committed public query access go directly to validator REST APIs, through read-only gateways, or through a replicated serving tier? What proof/staleness metadata can clients receive today?
13. Which APIs already expose the validator, voter, consensus, version and intake signals required by Commons Network Admin, and which are missing?
14. Does `codex/sage` agree that Option B is the production direction, Option A is lab-only, Option C is institution federation only and Option D is merely an alternate transport into Option B?

## 13. Recommendation

Build **Option B**: every Lantern is a sovereign personal SAGE plus a constrained public proposal/query client; independently governed Lightbearer Validators alone validate the public Commons. Use HTTPS first and add **Option D** after MeshCore measurements. Reserve federation for trusted institution/Knowledge Point relationships, not ordinary fleet intake.

This topology delivers the desired dual network: private personal intelligence at the edge, signed hyperlocal contribution from every Lantern, and a public knowledge commons whose liveness and governance do not depend on portable devices.

## 14. Source basis

- Lantern boundary and acceptance: [`../lantern/PRD.md`](../lantern/PRD.md), [`../lantern/DECISIONS.md`](../lantern/DECISIONS.md), [`../lantern/HARDWARE_SOFTWARE_CONTRACT.md`](../lantern/HARDWARE_SOFTWARE_CONTRACT.md)
- SAGE topology and APIs: `sage/docs/ARCHITECTURE.md`, `sage/docs/GETTING_STARTED.md`
- Independent-chain federation: `sage/docs/FEDERATION.md`, `sage/docs/reference/federation-and-brain-api.md`
- Validator quorum/key behaviour: `sage/docs/reference/concepts/voter-operations.md`
- Block liveness and retention: `sage/docs/reference/concepts/block-production-and-idle.md`
- RBAC/state-sync boundaries: `sage/docs/reference/concepts/rbac-orgs-federation.md`
- Deterministic content-validation limits: `sage/docs/reference/concepts/content-validation-gate.md`
