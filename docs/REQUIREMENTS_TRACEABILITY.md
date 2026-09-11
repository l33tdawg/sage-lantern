# Requirements traceability register

**Status:** Living programme register  
**Rule:** An item is `VERIFIED` only when its acceptance evidence is linked and reviewed.

Requirement status records whether the Commons/Lantern requirement is accepted or delivered. It does not claim that an underlying SAGE capability exists. SAGE capability availability is recorded separately and exclusively with one dependency state from [`SAGE_V12_DEPENDENCY_CROSSWALK.md`](SAGE_V12_DEPENDENCY_CROSSWALK.md).

## Status vocabulary

`PROPOSED | ACCEPTED | IMPLEMENTING | IMPLEMENTED | VERIFIED | BLOCKED | DEFERRED | SUPERSEDED`

## Baseline register

| ID | Requirement / acceptance statement | Normative source | Owner | Gate | Verification/evidence | Status |
|---|---|---|---|---|---|---|
| SYS-001 | Personal data is private by default | Master §4/§8 | Product/privacy | G0–G1 | Privacy tests and UI review | ACCEPTED |
| SYS-002 | Publication creates a sanitized derivative | Master §4/§10 | Publication | G2 | E2E leak/consent suite | ACCEPTED |
| SYS-003 | Authority planes remain separate | Master §4/§7 | Architecture/security | Every gate | Cross-plane negative tests | ACCEPTED |
| SYS-004 | Lantern can never be a public validator | Master §4/§11 | Commons network | Every gate | Image/key/API conformance | ACCEPTED |
| SYS-005 | Stable independently governed infrastructure validates | Master §4/§11 | Network governance | G2–G5 | Topology/quorum evidence | ACCEPTED |
| SYS-006 | Connectivity/custody does not confer authority | Master §4/§7 | Security | G2–G4 | Abuse-case tests | ACCEPTED |
| SYS-007 | UI labels stale/unknown/receipt states honestly | Master §4/§10/§14 | UX | G2–G4 | State-model usability tests | ACCEPTED |
| SYS-008 | Packs trust signatures/hashes, not carriers | Master §4/§13 | Distribution security | G2–G4 | Adversarial carrier suite | ACCEPTED |
| SYS-009 | Mutating upgrades are recoverable | Master §4/§19 | Runtime/operations | G1–G5 | Fault/rollback evidence | ACCEPTED |
| SYS-010 | Project-authored work defaults Apache-2.0 | Master §4/§21 | Maintainers/legal | G0 onward | Release compliance report | ACCEPTED |
| SYS-011 | Requirements require evidence | Master §4/§20 | QA | Every gate | Traceability audit | ACCEPTED |
| SYS-012 | Core journeys are nontechnical and accessible | Master §4 | UX/accessibility | G1–G5 | Moderated user studies | ACCEPTED |
| LAN-001 | Offline PTT voice works without any external plane | Lantern PRD; Master §9 | Lantern runtime | G1 | Unit A/B witnessed evidence | IMPLEMENTING |
| LAN-002 | One sovereign personal SAGE node per Lantern if ROCK evidence passes | Lantern PRD | Lantern/SAGE | G1 | Thermal/power/load test | IMPLEMENTING |
| LAN-003 | Golden image contains no live unit identity | Software Stack; Master §7 | Image/release | G1 | Image secret/key scan | ACCEPTED |
| LAN-004 | Voice outranks background distribution/update work | Master §9.4 | Runtime | G1–G3 | Resource contention test | ACCEPTED |
| LAN-005 | Failed cache/runtime update preserves personal state | Master §9.5 | Runtime/recovery | G1–G3 | Power-cut/corruption test | ACCEPTED |
| LAN-006 | Production hardware passes thermal/power/EMC/repairability gates | Master §9.1 | Hardware | G4–G5 | Certification/evidence pack | PROPOSED |
| PUB-001 | Public draft shows payload, provenance, licence, locality and redaction | Publication Contract; Master §10 | Publication/UX | G2–G3 | Consent usability/e2e test | ACCEPTED |
| PUB-002 | Proposal queue is encrypted, durable, bounded and idempotent | Publication Contract; Master §10 | Publication runtime | G2 | Power/flood/replay tests | IMPLEMENTING |
| PUB-003 | Intake authenticates, quarantines and durably receipts before submit | Master §10 | Commons intake | G2 | Conformance/failure suite | ACCEPTED |
| PUB-004 | Canonical receipt vocabulary is implemented consistently | Master §10.4 | API/UX | G2 | Schema/UI fixtures | ACCEPTED |
| PUB-005 | Proposal floods are quota/rate/size bounded | Master §10.5 | Intake/security | G2–G3 | Load/abuse test | ACCEPTED |
| PUB-006 | Proposer private key never leaves Lantern | Master §7/§10 | Lantern/security | G2 onward | Key-flow review/negative tests | ACCEPTED |
| COM-001 | Initial validator topology spans independent failure domains | Master §11 | Network governance | G2–G5 | quorum/failure-domain model | PROPOSED |
| COM-002 | Export/query reads verified canonical state without validator key; deterministic export waits for `SAGE-EXPORT` | Master §11/§13 | Network/export | G2 conditional | topology/key-custody audit plus SAGE-EXPORT evidence | ACCEPTED |
| COM-003 | Foundation cannot unilaterally control consensus | Master §7/§11 | Governance | G2–G5 | governance/key ceremony | ACCEPTED |
| COM-004 | Public lifecycle uses exact supported SAGE semantics | Master §11.2 | SAGE adapter | G2 | v11.18.4 conformance | ACCEPTED |
| KPT-001 | Knowledge Point deployments declare facility custody, uptime and physical-security requirements plus every hosted Lightbearer profile | Master §12 | Knowledge Point | G2 | Facility/profile PRD and lab evidence | PROPOSED |
| LBR-001 | Lightbearer profiles declare independent identity, keys, authority, storage, operation and evidence boundaries; only separately governed Lightbearer Validators receive voting power | Terminology; Master §11–§12 | Commons network/security | G2–G5 | topology, key-custody and governance negative tests | ACCEPTED |
| DST-001 | All pack manifests are immutable/content-addressed/signed | Distribution Architecture | Distribution | G2 | schema/conformance fixtures | ACCEPTED |
| DST-002 | Clients prevent rollback/freeze/revoked activation | Distribution Architecture | Distribution security | G2–G4 | attack suite | ACCEPTED |
| DST-003 | Bulk data never travels over LoRa | Distribution Architecture | Radio/distribution | G2 onward | protocol/airtime tests | ACCEPTED |
| DST-004 | Transfer resumes, verifies, stages and atomically activates/rolls back | Distribution Architecture | Distribution runtime | G2–G3 | fault-injection suite | ACCEPTED |
| DST-005 | Snapshot Pack only includes committed/non-deprecated PUBLIC records after `SAGE-EXPORT` exists | Distribution Architecture §13 | Export/publisher | G2 conditional | deterministic v12 export conformance | ACCEPTED |
| DST-006 | Export attestation is never presented as consensus inclusion proof | Distribution Architecture §13 | Export/UX | G2 conditional | v12 proof-capability evidence and claim review | ACCEPTED |
| DST-007 | SAGE Lamplighter remains download-only until Return Pouch RFC | Master §13.5 | Distribution/security | G2–G4 | data-flow/port tests | ACCEPTED |
| FLE-001 | Field capability binds operator+laptop+scope+actions+bounds | Operations/UX §7 | Fleet security | G2–G3 | capability conformance | ACCEPTED |
| FLE-002 | Laptop and Device Agent independently authorize commands | Operations/UX §7 | Fleet/device | G2–G3 | cross-side negative tests | ACCEPTED |
| FLE-003 | Unknown/rollback clock fails closed when validity unprovable | Operations/UX §7 | Fleet security | G2–G3 | clock fault tests | ACCEPTED |
| FLE-004 | Field operator cannot remotely approve local SAGE membership | Operations/UX §5 | Fleet/SAGE | G2 onward | enrollment negative tests | ACCEPTED |
| FLE-005 | Cross-cluster action requires explicit separate delegation | Operations/UX §6 | Fleet security | G3 | Village A/B isolation test | ACCEPTED |
| FLE-006 | Fleet projections never claim physical isolation from one SAGE home | Operations/UX §5.4 | Fleet architecture | G2 | deployment/config review | ACCEPTED |
| UX-001 | Personal/private, public draft and Commons cache are visually distinct | Master §14.4 | UX | G2–G3 | comprehension study | ACCEPTED |
| UX-002 | User can deny/defer field/update action without losing private use | Master §14.4 | UX/device | G3 | offline workflow test | ACCEPTED |
| SEC-001 | Every subsystem has maintained threat/privacy model | Master §16 | Security/privacy | Every gate | review sign-off | PROPOSED |
| SEC-002 | Keys are separated, rotatable and revocable per plane | Master §7/§16 | Security | G2–G5 | ceremonies/compromise drills | ACCEPTED |
| SEC-003 | Diagnostics contain no private content or secrets by default | Master §16/§18 | Observability/privacy | G1 onward | fixture/redaction tests | ACCEPTED |
| SEC-004 | Cryptographic migration preserves trust, offline catch-up and archival verification without downgrade | Master §19.2 | Security/release/network | G4–G5 | dual-sign/verify, rollover, downgrade and compromise drills | ACCEPTED |
| OPS-001 | Critical incident runbooks and exercises precede maturity gate | Master §18 | Operations | G2–G5 | tabletop/chaos records | PROPOSED |
| OPS-002 | Chains, public state, devices, packs, keys and communities have tested disaster recovery and end-of-life/archive paths | Master §19.1–19.2 | Network SRE/operations/community | G4–G5 | RPO/RTO recovery drill and decommission/archive evidence | ACCEPTED |
| OBS-001 | State observations always carry source age/confidence | Master §18 | Observability | G2 onward | API/UI conformance | ACCEPTED |
| API-001 | Production APIs are versioned and have conformance fixtures | Master §15 | API owners | G2 onward | compatibility suite | ACCEPTED |
| OSS-001 | Releases include SBOM, licences, NOTICE, provenance and source | Open Source Policy | Release/legal | G1 onward | release gate | ACCEPTED |
| OSS-002 | Unknown/incompatible artifact licences fail the build | Open Source Policy | Release/legal | G1 onward | licence-policy test | ACCEPTED |
| GOV-001 | Durable cross-system decisions follow RFC process | Documentation Governance | Maintainers | G0 onward | quarterly process audit | ACCEPTED |
| GOV-002 | Requirement IDs are stable and changes are traceable | Documentation Governance | QA/docs | G0 onward | link/coverage CI | ACCEPTED |

## SAGE dependency evidence/fallback mapping

Capability state is defined only in [`SAGE_V12_DEPENDENCY_CROSSWALK.md`](SAGE_V12_DEPENDENCY_CROSSWALK.md). This table uses the same atomic IDs 1:1 and adds no aliases or state column.

| Atomic ID | Evidence owner | Gate | Exact evidence | Fallback/containment if unavailable |
|---|---|---|---|---|
| SAGE-001a | SAGE core + Lantern integration | G1 | Immutable v11.18.4 release/source/app/chain evidence and Unit A/B headless lifecycle run in `evidence/gates/G1/sage-personal-node/` | Block personal-node product claim; continue hardware/audio tests only |
| SAGE-001b | Lantern packaging/runtime | G1 | Reproducible ARM64 package, service supervision, restart, thermal/power and recovery evidence in `evidence/gates/G1/lantern-runtime/` | Run components on bench host; do not claim deployable Lantern runtime |
| SAGE-001c | SAGE core | G2 | Signed machine-readable capability-evidence fixture in `evidence/gates/G2/sage-capabilities/` | Manually pin verified v11.18.4 and disable capability-dependent automation |
| SAGE-ROLES | SAGE core + security | G1/G2 | Root/Member/Manager/Admin and Access Group positive/negative conformance in `evidence/gates/G2/sage-roles/` | Use least-privileged ordinary Member only; disable unclear workflow |
| SAGE-MSG | SAGE core + Lantern integration | G1/G2 | Identity, idempotency, delivery/read/workflow and restart fixtures in `evidence/gates/G2/sage-messages/` | Keep coordination local/queued; do not infer authority or delivery |
| SAGE-PROP-a | SAGE core + Lantern adapter | G2 | Fresh nonce/timestamp signer, author identity and lifecycle evidence in `evidence/gates/G2/publication/sage-submit/` | Keep approved envelopes queued; no public submission claim |
| SAGE-PROP-b | Commons intake + Lantern adapter | G2 | Envelope replay/idempotency, quarantine, timeout and receipt suite in `evidence/gates/G2/publication/intake/` | `NEEDS_ACTION` plus manual safe reconciliation |
| SAGE-PROP-c | Architecture/security | Every gate | Negative retry/idempotency test proving generic request resubmission is unused | Fail ambiguous submits to reconciliation; never blind retry |
| SAGE-HYPER | SAGE governance/core + Commons intake | Post-G2 RFC | Accepted app-version RFC, byte-identical replay, migration and governed activation | Enforce beta schema/policy only in intake/cache; no canonical claim |
| SAGE-QUERY-a | SAGE core + query adapter | G2 | Authenticated eligible-identity and RBAC/classification query suite in `evidence/gates/G2/query/canonical/` | Disable live query; retain clearly stale labelled cache only |
| SAGE-QUERY-b | Commons query/security/privacy | G2/G3 | Abuse, rate, privacy, cache/provenance and authentication/public-access suite in `evidence/gates/G2/query/service/` | Registered authenticated access only; no anonymous service |
| SAGE-EXPORT | SAGE core export + Commons publisher | G2 conditional | Two independent deterministic committed/non-deprecated `PUBLIC=0` exports plus proof-claim review in `evidence/gates/G2/export/` | Omit Commons Snapshot Pack/offline public export; beta continues without it |
| SAGE-FED-a | SAGE core/integration | G2 | Federation Read/Copy identity, policy and authorship conformance in `evidence/gates/G2/federation/` | Do not use federation in Lantern product flows |
| SAGE-FED-b | Architecture/security | Every gate | Negative remote-Write API/configuration tests | No remote Write; use separate approved intake/management contracts |
| SAGE-REC-a | SAGE core + operations | G1/G4 | Stopped-node backup/restore and authorized empty-node state-sync evidence in separate paths | Stop risky migration/recovery; preserve existing node read-only |
| SAGE-REC-b | Distribution/security | Every gate | Format-separation tests proving packs/cache never consume backup/state-sync artifacts | Reject artifact and retain last verified active generation |
| SAGE-PACK | Distribution/release/device | G2/G3 | Pack metadata, activation, consent and execution-receipt conformance in `evidence/gates/G2/packs/` | No activation; retain existing verified runtime/data |
| SAGE-FLEET | Fleet/device/security | G2/G3 | Capability, command, target authorization and receipt suite in `evidence/gates/G2/fleet/` | Local owner operation only; no remote mutation |
| SAGE-MULTI | Architecture/runtime | Every gate | Negative image/process/home scan for second SAGE/Observer dependency | One personal SAGE plus adapter/cache only |
| SAGE-VAL-a | SAGE core/network | G2/G4 | Validator lifecycle, quorum, upgrade and recovery evidence in `evidence/gates/G2/validators/` | Lab network only; no public availability claim |
| SAGE-VAL-b | Commons network/governance | G4/G5 | Independent operator, governance, transparency and incident evidence | Do not launch public network |

## Register extension rules

Subsystem owners add granular requirements before implementation, not after. Parent requirements do not replace interface, security, accessibility, capacity or failure-mode requirements. Tests may satisfy multiple IDs but each ID links the exact assertion/evidence. Superseded requirements retain history and replacement ID.
