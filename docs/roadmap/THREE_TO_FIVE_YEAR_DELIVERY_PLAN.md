# SAGE Commons three-to-five-year delivery plan

**Status:** CANDIDATE; gates are binding, dates are planning ranges  
**Principle:** Evidence and safety control progression, not calendar pressure.

## Workstreams

1. Governance, open-source stewardship and community.
2. Lantern hardware, manufacturing and local AI UX.
3. Personal/public publication and Commons intake.
4. Public network, independently governed Lightbearer Validators, query/export and governance.
5. Knowledge Point facilities, Lightbearer profiles and community curation.
6. Signed offline distribution, SAGE Lamplighter and local swarm.
7. Foundation, Public Network, Field Operations and Lantern management UX.
8. Security, privacy, safety, reliability and compliance.
9. Developer platform, schemas, conformance and independent implementations.

## Phase 0 — Engineering foundation (months 0–6)

Deliver the master PRD/document system and [`SAGE_V12_DEPENDENCY_CROSSWALK.md`](../SAGE_V12_DEPENDENCY_CROSSWALK.md); freeze product/authority vocabulary; reconcile every dependency with one capability state; establish Apache-2.0/DCO/licence gates; build threat models; inventory protocols; benchmark ROCK hardware; integrate Unit A then Unit B; prove sterile images, offline voice and recovery; define testnet and pack schema RFCs.

**Exit G0:** owners and review process exist; critical open decisions have deadlines; no known contradiction in authority/data boundaries; repository licence and third-party policy are enforceable.

**Exit G1:** two reproducible Lantern betas demonstrate private offline loop, measured resource envelope, safe identity generation, backup/recovery and hardware evidence.

## Phase 1 — Integrated laboratory system (months 6–18)

Build proposer envelope/queue, enrollment, intake/quarantine, fresh SAGE submission adapter, receipts, authenticated public query/read model, signed pack repository/client, local chunk swarm, Device Agent/capabilities, initial consoles, a Knowledge Point-hosted Lightbearer Cache/Seed and Query/Export profile, a simulated Lightbearer Validator testnet and comprehensive conformance/fault/security tests. The deterministic Commons Snapshot Pack/export lane is conditional on `SAGE-EXPORT` (`required-sage-v12`); no long-lived SAGE Observer role/process is planned or implied.

Define API schemas before multiple clients emerge. Operate no real-user public promise. Exercise floods, corrupt packs, power loss, clock rollback, validator loss and key compromise.

**Exit G2:** one end-to-end lab path runs from private derivation through public commit and authenticated query; negative authority tests pass; validator and release keys are separated; APIs/packs have versioned fixtures; licence/provenance gates pass. Offline Commons Snapshot Pack export passes only if `SAGE-EXPORT` evidence exists; absence blocks that lane, not the rest of the beta/MVP.

## Phase 2 — Closed field alpha (months 18–30)

Co-design with a small number of consented communities. Localize voice/UX; deploy Knowledge Points hosting explicitly non-validator Community Lightbearer profiles; run assigned-cluster Field Consoles and SAGE Lamplighter routes; measure journey economics, update freshness, LoRa topology, device reliability, proposal quality, moderation burden and comprehension of privacy/provenance. Establish privacy/legal basis, support channels, incident drills and independent security review.

Alpha data collection is opt-in, minimized and reviewable. High-risk public domains remain disabled until their governance/safety process exists.

**Exit G3:** multiple disconnected clusters operate without cross-cluster authority leakage; users correctly distinguish private/public/cache states; field updates/recovery succeed under realistic conditions; serious incidents have demonstrated containment; product value and operating costs are evidenced.

## Phase 3 — Regional beta and production engineering (months 30–42)

Redesign production Lantern hardware; prepare certification/manufacturing/repair; mature Knowledge Point facility requirements and isolated Lightbearer profiles; establish independent Lightbearer Validator operators/failure domains; harden query/intake/export and transparency; automate reproducible releases/SBOM/advisories; formalize Foundation/network governance; expand language and curation programmes; decide Return Pouch; publish developer SDK/conformance suite.

**Exit G4:** regional beta meets published reliability/security/privacy/accessibility targets; independent audit findings are closed or explicitly accepted; release/validator compromise drills pass; supply/service/repair model is credible.

## Phase 4 — Public network launch (months 42–54)

Launch only after governance and incident response can sustain the network. Publish validator admission/upgrade/emergency policy, production SLOs, support commitments, security disclosure, public transparency, licence/provenance policy and compatibility guarantees. Use staged capacity/region growth and reversible feature flags.

**Exit G5:** public network has independent operators, measured quorum margin, durable intake/query/export, supported Lantern release, accountable moderation/correction and 24/7 severity-appropriate incident capability.

## Phase 5 — Open ecosystem scale (months 54–60+)

Support independent hardware/software implementations, regional governance and publishers, third-party pack/model tooling, sustainable contributor/maintainer pathways, interoperability events, research partnerships and long-term archival/migration. Reduce dependence on any founding organization.

**Exit G6:** at least two independent compatible implementations or operators pass conformance; governance and release processes continue without founder-only knowledge; communities can self-host appropriate planes without surrendering interoperability.

## Cross-phase review cadence

- Monthly: requirements, risks, decisions, interface/schema drift.
- Quarterly: architecture/security/privacy reconciliation and cost/capacity update.
- Per release: SBOM/licence/provenance, threat delta, compatibility/migration, evidence map.
- Per field campaign: consent, safeguarding, operator delegation, route/update plan, incident readiness and post-campaign review.
- Annually: mission outcomes, governance concentration, sustainability, accessibility/language coverage and whether any subsystem should be stopped or redesigned.

## Auditable gate contract

A gate is `PASS`, `CONDITIONAL PASS` or `FAIL`. The accountable approvers sign the content-addressed digest of an unsigned RFC 8785 manifest using detached attestations stored under `evidence/gates/<gate>/<attempt-id>/` as defined by [`GATE_EVIDENCE_CONTRACT.md`](GATE_EVIDENCE_CONTRACT.md). It lists requirement IDs, exact artifact hashes/locations, measured thresholds, exceptions, approver identities, decision time and next review. `PASS` requires every mandatory row below. `CONDITIONAL PASS` is allowed only for a named independent lane whose absence cannot weaken a frozen invariant or expose real users; it records owner, expiry, containment and the blocked capability. `FAIL` prohibits the next maturity claim.

Waivers are exceptional RFC decisions, never informal approval. A waiver names requirement/evidence, reason, residual risk, compensating control, accountable owner, affected deployments, expiry and rollback. Privacy consent, private/public separation, validator-key separation, cryptographic verification and user-safety stop conditions are non-waivable.

| Gate | Required traceability baseline | Accountable approvers | Evidence location and reproducible threshold |
|---|---|---|---|
| G0 | `SYS-001..012`, `GOV-001..002`, `OSS-001..002`, all atomic crosswalk IDs classified | Product owner, architecture, security/privacy, open-source governance | `evidence/gates/G0/`; 100% baseline requirements have owner/source/gate/evidence; 100% atomic SAGE dependencies have exactly one state; zero unresolved authority contradiction or unowned critical decision |
| G1 | `LAN-001..005`, `SAGE-001a`, `SAGE-001b`, `SAGE-ROLES`, `SAGE-MSG`, `SAGE-REC-a`, `SEC-003`, `OSS-001..002` | Lantern product, hardware/runtime, SAGE integration, QA/security | `evidence/gates/G1/`; both units reproducibly provisioned; required offline loops/restart/recovery and resource tests pass; zero cloned live identity/secret; licence gate passes |
| G2 | `PUB-001..006`, `COM-001..004`, `DST-001..007`, `FLE-001..006`, `API-001`, and applicable atomic `SAGE-*` IDs | Commons product, SAGE core, network, distribution/fleet security, QA | `evidence/gates/G2/`; all mandatory conformance/negative/fault suites pass; zero cross-plane escalation; `SAGE-EXPORT` may be an isolated conditional lane with Snapshot Pack disabled |
| G3 | `UX-001..002`, field-relevant `FLE`, `DST`, `PUB`, `SEC-001`, `OPS-001` | Community/product, privacy/safety, field operations, independent security, pilot lead | `evidence/gates/G3/`; approved study thresholds and sample are frozen before data collection; zero severe unresolved privacy/safety incident; every cluster isolation/update/recovery scenario passes |
| G4 | `LAN-006`, `SEC-001..004`, `OPS-001..002`, `COM-001..003`, `KPT-001`, `LBR-001` | Foundation/network governance, independent security auditor, hardware/compliance, SRE, community representative | `evidence/gates/G4/`; published beta SLO thresholds met for agreed observation window; critical/high audit findings closed or formally risk-accepted; compromise, crypto-migration, disaster-recovery and decommission drills meet their frozen RPO/RTO/continuity targets |
| G5 | All production-scoped IDs VERIFIED | Network governance, Foundation release authority, privacy/safety, SRE incident commander, independent operator representatives | `evidence/gates/G5/`; 100% production requirements have reviewed evidence; production SLO/quorum/RPO/RTO thresholds met; no expired waiver; signed reproducible release and 24/7 severity-appropriate response demonstrated |
| G6 | `GOV-001..002`, `OSS-001..002`, `API-001`, ecosystem/interoperability extensions | Community governance, independent implementers/operators, Foundation stewardship | `evidence/gates/G6/`; at least two organizationally independent implementations or operators pass the same public conformance suite; no founder-only release/governance/key ceremony dependency |

## Programme metrics

Metrics are decision aids, never permission to invade privacy. Track offline task success, latency/power/thermal, device recovery, update freshness/verification, proposal consent comprehension, durable receipt latency, moderation backlog, public provenance coverage, validator independence/quorum, incident detection/recovery, cluster support cost, language/accessibility success, contributor/operator diversity, reproducibility and requirement evidence coverage.

Do not optimize raw proposal count, collected memories, daily engagement or telemetry volume. Those metrics create incentives directly opposed to private-by-default local ownership.
