# Product Requirements Document: SAGE Lantern Fleet Admin

**Status:** Draft for product and architecture review  
**Version:** 0.1  
**Date:** 2026-08-09  
**Programme:** SAGE Commons  
**Product surface:** Lantern Fleet Admin  
**Primary users:** nontechnical fleet operators and authorized support engineers  
**Depends on:** final SAGE Commons Lightbearer Validator topology, public proposal contract, and supported Lantern packaging/update contract
**Canonical terminology:** [`../TERMINOLOGY.md`](../TERMINOLOGY.md)

## 1. Product summary

Lantern Fleet Admin is the operator-facing control surface for preparing, identifying, observing, maintaining, and troubleshooting a fleet of SAGE Lantern devices. It should let an authorized person move a newly assembled ROCK-based unit from an unpersonalized base image to a uniquely identified, supportable Lantern without needing to understand Linux services, SAGE consensus internals, or radio configuration.

The product manages the **device lifecycle**. It does not silently acquire SAGE Root authority or copy personal knowledge. Every Lantern is a signed proposer/publisher and query client for appropriate hyperlocal public Commons knowledge; Lanterns are never public-chain consensus validators.

The fleet product has two operational presentations over shared signed contracts:

- the **Foundation fleet view** governs regions, cluster assignments, release policy, field delegations and aggregate evidence; and
- the offline-capable **Field Operations Console** runs on a SAGE Lamplighter operator's laptop for explicitly assigned village clusters, bundle seeding, LoRa topology, provisioning/update assistance and later receipt reconciliation.

The detailed Foundation → field laptop → cluster → Lantern authority and UX contract is defined in `../distribution/DATA_DONKEY_OPERATIONS_AND_UX.md`. Neither presentation is an alias for SAGE Admin or personal CEREBRUM Root.

## 2. Problem

The bench beta starts with two hand-built devices, but cloning Unit A to Unit B already creates risks that do not scale:

- duplicated SAGE, TLS, radio, or operating-system identities;
- inconsistent hardware and software inventories;
- unknown model, binary, firmware, and app-version combinations;
- updates with no verified backup or rollback path;
- support staff seeing either too little diagnostic evidence or too much private content;
- offline devices being mistaken for failed, revoked, or fully updated devices; and
- a fleet-management action being confused with governed SAGE authority.

The admin experience must turn those risks into explicit, reviewable workflows.

## 3. Goals

The first release shall:

1. provision a new or freshly cloned Lantern with unique device, SAGE-agent, and radio identities;
2. maintain an accurate inventory of hardware, firmware, software, models, roles, and ownership state;
3. show health and last-known state without pretending an offline device is current;
4. stage, approve, roll out, pause, and roll back software/model updates;
5. collect bounded, privacy-preserving bring-up and acceptance evidence;
6. make privileged actions understandable and auditable to a nontechnical operator;
7. preserve useful local operation during loss of cloud, fleet-admin, public-network, or radio connectivity;
8. keep device administration, personal SAGE governance, and public-network governance as distinct authority planes; and
9. make each Lantern's signed public proposal queue, publication result, and public-query synchronization state legible without exposing its private personal-node content.

## 4. Non-goals

The first release will not:

- browse, edit, export, or centrally back up a user's private SAGE memories;
- store CEREBRUM Root credentials, validator private keys, radio private identity, or reusable enrollment secrets in the fleet database;
- expose loopback-only CEREBRUM recovery and Root controls remotely;
- enroll any Lantern as a public-network consensus validator;
- define the final public SAGE Commons consensus topology or replace its governance;
- promise continuous management of an offline or intermittently connected device;
- perform unattended consensus app-version changes without the SAGE upgrade preconditions; or
- treat reimaging as an acceptable routine recovery method for a lived-in SAGE node.

## 5. Authority and product boundaries

| Plane | Governs | Authority holder | Fleet Admin behavior |
|---|---|---|---|
| Device management | Hardware record, OS image, installed packages, models, local services, diagnostics | Fleet operator under an explicit organization policy | May inventory, stage changes, request evidence, and coordinate updates |
| Personal SAGE | Local agents, domains, memories, Access Groups, Root handover, local federation choices | Machine-local CEREBRUM Root and authorized local Admins | Shows bounded readiness/status only; deep-links to local CEREBRUM for governed actions |
| Public SAGE Commons | Signed proposals/publications, queries, public knowledge policy, Lightbearer Validator consensus and network releases | Lantern user for eligible proposal intent; public-network governance and independently operated always-on servers for acceptance/consensus | Tracks signed proposal/query synchronization; never provisions validator keys or voting power to a Lantern |
| MeshCore transport | Radio configuration and constrained message transport | Per-device operator and approved transport policy | May configure/test transport; never treats a radio identity as SAGE authority |

The Fleet Admin backend must use a dedicated, least-privileged device-management identity. A dashboard login or support role is not a SAGE Root credential. Privilege is checked again on the Lantern for every mutation; a central UI indication is not authorization.

## 6. Provisional deployment model

Each Lantern is currently expected to run a full personal SAGE node if ROCK 5B+ performance, thermal, and power evidence supports it. That personal node is sovereign and continues operating locally without Fleet Admin. Every Lantern also acts as a signed proposer/publisher and query client for eligible hyperlocal public Commons knowledge. When disconnected, it retains signed proposals in a bounded local queue and clearly distinguishes `draft`, `queued`, `submitted`, `accepted`, `rejected`, `expired`, and `conflicted` states.

Lanterns never join the public CometBFT validator set and never receive public-chain consensus keys or voting power. Public consensus is operated by stable, always-on, independently governed **Lightbearer Validators**. The exact initial server count, hosting distribution, admission policy and disaster-recovery topology remain architecture decisions, but they do not change the Lantern product role. Lightbearer status without validator admission grants no vote.

## 7. Core user journeys

### 7.1 Prepare a base image

An authorized release engineer creates a signed, versioned image manifest containing the operating-system build, SAGE release, supported consensus app-version range, Lantern services, model packages, firmware expectations, checksums, and compatibility notes. The base image contains no live unit identity or user data.

### 7.2 Provision a newly assembled or cloned Lantern

1. The operator selects **Add Lantern** and scans a local setup code shown by the device.
2. Both sides show a short comparison code and the operator confirms physical possession.
3. Fleet Admin records immutable manufacturing/assembly facts and assigns a human-friendly label such as `Lantern A`.
4. The device generates unique keys locally. Private keys never leave the device.
5. The workflow proves that prohibited clone state is absent before activation, including copied `~/.sage`, Root/Admin keys, receive tokens, idempotency/message journals, TLS identity, and radio identity.
6. The device self-registers its ordinary SAGE agent identity and remains pending until approved locally through CEREBRUM.
7. The operator configures a distinct radio identity and verifies the expected Heltec companion.
8. Hardware tests and the five-loop local acceptance check run or are queued.
9. The device becomes **Ready** only when all mandatory gates pass. Otherwise the UI shows the exact incomplete step and a safe recovery action.

Reprovisioning an already lived-in node must stop at a destructive-action warning. The supported SAGE full-backup and restore process preserves consensus data; a SQLite-only copy or blind reimage is not a backup.

### 7.3 Monitor the fleet

The home screen answers four questions without requiring logs:

- Which Lanterns need attention?
- What changed recently?
- Which devices are safe to update?
- What was the last verified state of devices that are offline?

Each status card must show `Online`, `Offline since …`, `Degraded`, `Update staged`, `Restart required`, `Recovery required`, or `Unknown`; it must not collapse these states into a red/green dot.

### 7.4 Roll out or roll back an update

1. The operator selects a signed release and reviews plain-language impact, compatibility, download size, restart need, and recovery prerequisites.
2. Fleet Admin performs a read-only preflight and confirms a stopped-node `backup --full` when the SAGE data plane will change.
3. The operator deploys first to a named canary Lantern.
4. The device verifies checksums/signatures, available storage, power conditions, supported release/app-version path, and required peripheral/model compatibility.
5. The rollout pauses until the canary completes health, local voice-loop, and recovery checks.
6. The operator expands the rollout in bounded waves or stops it.
7. A failed binary/service update restores the previously verified compatible package set. A consensus app-version activation is not described as a simple binary rollback; the UI follows the SAGE governed upgrade and recovery contract.

## 8. Functional requirements

### FR-1 — Fleet inventory

Store and display, with provenance and last-observed time:

- immutable fleet ID and mutable operator label;
- hardware serials/revisions where available;
- ROCK model, RAM, NVMe, cooler, display, audio board, hub, Heltec and power components;
- OS image, kernel, bootloader, SAGE release, SAGE consensus app version, Lantern service bundle, model versions, audio/radio firmware;
- per-device ordinary SAGE public identity and radio public identity;
- public proposer/publisher identity and queue state, where distinct from the personal-node identity;
- personal-node status and public proposer/query-client synchronization state;
- owner/site assignment, lifecycle state, warranty/repair notes, and tags; and
- current configuration revision and drift from the approved profile.

Secrets and private user content are excluded.

### FR-2 — Identity-safe provisioning

Provisioning shall be idempotent, resumable, and fail closed. It shall generate identities on-device, detect duplicate public identities across the fleet, require fresh identities after cloning, and retain an auditable receipt for each completed gate. The base image shall be continuously testable for forbidden identity material.

### FR-3 — Health and degraded-state reporting

The device agent shall report bounded telemetry including boot/session uptime, storage pressure, memory pressure, temperature/throttling, power-source observations when available, service state, peripheral presence, last local voice-loop result, MeshCore link/test state, SAGE `/health` and `/ready` state, release/app versions, upgrade state, and clock confidence.

`/ready` status `degraded` must remain distinguishable from unavailable. `/health` does not expose a version, so version inventory requires an authenticated local source. A stale report is labeled **Last known**, never **Current**.

### FR-4 — Software, model, and firmware catalog

Every deployable artifact shall have a signed manifest, checksum, architecture, size, license/source, compatibility range, release channel, rollback partner, and known-risk notes. Model packages are independently versioned from the SAGE binary and Lantern application.

The UI and API keep five evidence planes separate: (1) installed SAGE release/source/app-version/chain-role/API capabilities; (2) Commons TUF-style pack catalogue/target metadata; (3) device-signed command/execution receipts; (4) stopped-node full-backup evidence; and (5) state-sync evidence for its supported empty-node bootstrap purpose. Success in one plane MUST NOT be rendered as verification of another.

### FR-5 — Controlled rollout

Support preview, canary, bounded-wave, and manual-per-device rollout policies. An operator can schedule, pause, resume, or cancel pending work. The device may defer installation because of low power, high temperature, insufficient storage, active voice use, missing backup, unsupported upgrade lineage, or unavailable recovery package.

### FR-6 — Recovery and rollback

Before a state-affecting SAGE upgrade, the UI shall guide the operator through the current supported full backup and preflight. It shall record backup metadata without uploading the secret-bearing archive by default. Recovery actions shall preserve the existing tree or data set, clearly state their blast radius, and require re-verification of identity and public-network role afterward.

### FR-7 — Evidence

Attach structured evidence to provisioning, acceptance, updates, rollbacks, and repairs. Evidence includes timestamps, unit ID, release/config manifest, test result, correlated session IDs, stage timings, thermal/power observations, retry/duplicate behavior, sanitized logs, operator acknowledgement, and file hashes. Raw private audio and memory content are opt-in, time-bounded diagnostic attachments.

### FR-8 — Roles and approvals

Initial fleet roles:

- **Viewer:** read inventory, last-known health, rollout status, and sanitized evidence.
- **Technician:** run diagnostics, update physical inventory, and prepare a provisioning session; cannot approve identity or release changes.
- **Fleet operator:** approve device enrollment, configuration, and bounded rollouts.
- **Release manager:** publish signed manifests and define compatibility/rollback policy; cannot obtain personal SAGE authority.
- **Auditor:** read immutable receipts and export sanitized audit records.

High-impact actions require explicit confirmation and, where policy demands, a second qualified approver. No fleet role automatically maps to SAGE Root, SAGE Admin, or public-network governor.

### FR-9 — Offline and degraded operation

The Lantern continues local PTT, local inference, personal SAGE access, and locally queued operations when Fleet Admin is unavailable. Management commands are signed, expiry-bounded, monotonic, and replay-protected. The device rejects expired or superseded commands after reconnection.

Fleet Admin queues only actions explicitly safe to delay. It shall not queue destructive recovery, Root handover, or irreversible governance actions for later surprise execution. Conflicts present the device's current revision and require operator reconciliation.

### FR-10 — Auditability

Every mutation records actor, role, target, intent, prior and resulting revision, device acknowledgement, outcome, and evidence link. Audit events are append-only from the product perspective and use stable correlation IDs across control plane and device logs. A central success state requires a signed device receipt, not merely command delivery.

### FR-11 — Hyperlocal public proposal and query status

Every Lantern shall be able to prepare and sign an eligible public knowledge proposal while offline, queue it locally, and submit it when an approved transport to the public Commons is available. A proposal record shall include, subject to the public schema and privacy policy:

- stable proposal identifier and schema version;
- proposing public identity and signature;
- provenance/source class and any supporting evidence reference;
- location at the least precise level permitted by consent and policy;
- observation/event time and proposal creation time;
- freshness window and explicit expiry/TTL;
- confidence and the method or basis used to derive it;
- content hash and bounded payload metadata;
- retry count, last attempt, next eligible attempt, and duplicate/idempotency key; and
- public-network receipt, decision, reason, and committed reference when available.

Fleet Admin shall show aggregate queue and synchronization state without routinely ingesting proposal content. It shall distinguish local signing, transport delivery, network receipt, public acceptance, consensus commitment where applicable, and later expiry/supersession. A successful radio or HTTP send is not a successful public publication.

Queries of public Commons knowledge shall expose the last successful synchronization time, covered geographic/topic scope, freshness/expiry of cached results, and whether results are live, cached, stale, or unavailable. Offline queries may use valid cached public knowledge but must not present expired knowledge as current.

### FR-12 — Validator exclusion boundary

Fleet Admin shall never generate, import, escrow, rotate, recover, or display public-chain validator private keys for a Lantern; assign voting power; amend the validator set; or present a proposer as a validator candidate. Lightbearer Validator operations belong to the separately governed public-network administration surface. If validator health is summarized in Fleet Admin for context, it is read-only and visibly outside the Lantern fleet boundary.

## 9. Nontechnical UX requirements

- Use task language such as **Prepare device**, **Check device**, **Install update**, and **Restore previous software**, with technical detail behind **Show details**.
- Explain consequences before confirmation: what stops, whether local voice remains available, whether a restart is needed, and how recovery works.
- Never use `validator`, `node`, `agent`, `identity`, and `device` interchangeably.
- Show a short reason and one recommended next action for every blocked or degraded state.
- Use both words and icons/colors for status, with accessible focus order and screen-reader labels.
- Keep advanced logs, hashes, versions, and consensus details available without making them prerequisites for routine operation.
- Provide a printable/downloadable handoff summary for field technicians that contains no secrets.

## 10. Minimum data model

| Record | Purpose |
|---|---|
| `device` | Fleet ID, label, hardware profile, lifecycle and ownership/site metadata |
| `device_identity_attestation` | Public identity fingerprints, generation event and duplicate check; never private keys |
| `observed_state` | Timestamped health, versions, peripherals, power/thermal and connectivity state |
| `desired_profile` | Approved OS/service/model/firmware configuration and policy revision |
| `artifact_manifest` | Signed release metadata, checksums, compatibility and rollback pairing |
| `operation` | Provision, diagnose, update, rollback or repair state machine |
| `evidence_bundle` | Sanitized measurements, logs, hashes, test results and retention class |
| `audit_event` | Actor, decision, revisions, device receipt and outcome |
| `public_proposal_receipt` | Proposal ID, provenance summary, location precision class, timing/freshness/expiry, confidence, queue state and public receipt; content excluded by default |
| `public_query_sync` | Last successful sync, query/cache scope, freshness, expiry and transport state |

## 11. Security and privacy requirements

- Device-to-admin communication is mutually authenticated, encrypted, rotation-capable, and explicitly paired.
- Device-generated private identities remain hardware-local or in the device's protected SAGE storage.
- Enrollment tokens are single-use and short-lived; comparison-code confirmation proves intended physical pairing.
- The server stores the minimum telemetry needed for support and fleet safety, with visible retention policy.
- Remote command allowlists, rate limits, anti-replay counters, and signed receipts apply even to authorized operators.
- Personal memory content, prompts, transcripts, raw audio, private keys, reusable SAGE tokens, and full backups are excluded from routine collection.
- Lost/stolen status revokes fleet-management access without pretending to revoke sovereign data or public-network authority that requires a separate governance action.

## 12. Acceptance criteria

The first fleet-admin beta is accepted when the following are demonstrated with two Lanterns and one clean base image:

1. Unit B can be cloned from the base, provisioned, interrupted midway, resumed, and completed without inheriting any Unit A SAGE, TLS, token, journal, OS, or radio identity.
2. A deliberate duplicate public identity is detected and activation is blocked before the device is marked Ready.
3. A nontechnical operator can identify both devices, their hardware differences, SAGE release, consensus app version, model set, firmware, health, last contact, and configuration drift without using a shell.
4. Removing Fleet Admin connectivity does not break the demonstrated offline PTT voice loop or local personal SAGE node; the console changes from current state to an honest last-known state.
5. A canary software/model rollout can be staged, preflighted, installed, verified, paused, expanded to Unit B, and audited.
6. An induced service/package failure returns the canary to the declared compatible software set and preserves its personal SAGE data and identity.
7. A simulated unsafe SAGE upgrade is blocked when the full backup, preflight, compatibility, power, storage, or recovery prerequisites are absent.
8. Viewer and Technician accounts cannot approve enrollment, publish releases, start rollouts, access secrets, or perform local SAGE governance.
9. Evidence from provisioning, five offline voice loops, a MeshCore exchange, update, and recovery can be exported with stable correlation IDs and no default inclusion of raw audio, memory content, or keys.
10. The interface represents personal-node health and public proposal/query synchronization separately and offers no Lantern validator-enrollment path.
11. Both Lanterns can sign proposals offline, retain them across reboot, submit them idempotently after reconnection, and show distinct transport, receipt, acceptance/rejection, commitment, and expiry states.
12. A proposal missing required provenance, permitted location precision, event time, freshness/expiry, confidence, or signature is blocked locally with a plain-language reason.
13. Cached public-query results show covered scope and freshness; expired results cannot be presented as current.
14. Inspection of the provisioned units and fleet database confirms that neither Lantern contains public-chain validator keys, voting power, or validator-role state.

## 13. Dependencies and open architecture questions

These questions must be decided with the SAGE product/architecture owners before implementation beyond the fleet beta:

| ID | Question | Why it matters |
|---|---|---|
| A-001 | What exact protocol and privacy boundary connects a Lantern's personal SAGE to its required signed public proposer/publisher and query-client role: federation, a dedicated artifact API, or a combination? | Defines trust, data flow, keys, availability and UX terminology |
| A-002 | Are the proposed four cloud Lightbearer Validators the initial CometBFT quorum or should the independently operated validator class use another count/distribution? | Determines quorum safety, hosting independence, failure assumptions and cost |
| A-003 | What read-only public-network health, if any, should a Lantern fleet operator see, and which separate console governs server validators? | Prevents device administration from becoming validator administration |
| A-004 | What is the canonical public proposal envelope, including provenance, permitted location precision, event time, freshness/expiry, confidence, validation result and privacy filter? | Prevents private-memory leakage and establishes attribution |
| A-005 | What device-management API and least-privileged local service will SAGE officially support? | Prevents Fleet Admin from scraping internals or holding Root authority |
| A-006 | Which release combinations are supported on ARM64 ROCK 5B+, and what is the signed supply-chain/rollback format? | Required before unattended or fleet-wide updates |
| A-007 | Who operates Fleet Admin, where is its control plane hosted, and can a user opt out or self-host? | Defines tenancy, privacy, availability and incident response |
| A-008 | Which actions require local physical confirmation or two-person approval? | Establishes the remote-management threat boundary |
| A-009 | Which proposer identity signs public proposals, how is user consent represented, and is it cryptographically separate from the personal-node agent and all server-validator credentials? | Prevents role confusion and limits credential blast radius |
| A-010 | What are the queue limits, retry/backoff, conflict, supersession, deduplication and receipt semantics for offline public proposals? | Required for honest sync state and intermittent connectivity |

## 14. Delivery slices

### Slice 0 — Bench inventory and evidence

Use the existing Lantern evidence scripts and documentation to define the observed-state schema. No remote mutation.

### Slice 1 — Local provisioning assistant

Run on the bench LAN, create unique identities, inventory components, execute acceptance checks, and export a support bundle. This slice does not require a cloud control plane.

### Slice 2 — Read-only fleet view

Add mutually authenticated check-ins, last-known health, version/drift display, and evidence history.

### Slice 3 — Public proposer/query observability

Add privacy-bounded offline proposal queue status, receipts, freshness/expiry, and public-query cache/synchronization state. Keep content device-local by default.

### Slice 4 — Signed update orchestration

Add artifact catalog, preflight, canary/waves, deferral, verification, and package-level rollback.

### Slice 5 — Public-network context

Optionally add read-only public-network availability and proposal-ingress health after the Lightbearer Validator topology is defined. Keep validator administration outside Lantern Fleet Admin.

## 15. Source contracts consulted

- `docs/lantern/PRD.md`
- `docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md`
- `docs/lantern/DECISIONS.md`
- SAGE `docs/ADMIN_BOOTSTRAP.md`
- SAGE `docs/UPGRADING.md`
- SAGE `docs/ARCHITECTURE.md`
- SAGE `docs/FEDERATION.md`
- SAGE `docs/reference/concepts/rbac-orgs-federation.md`
- SAGE `docs/reference/app-v25-upgrade-recovery.md`
- SAGE `docs/reference/rest-api.md`

Where those sources disagree with this provisional product document about current SAGE behavior, the current SAGE implementation and reference documentation take precedence.
