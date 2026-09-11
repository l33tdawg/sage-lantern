# Programme open decisions and risk register

**Status:** Living  
**Safe rule:** Until decided, choose the least-authority, least-data, reversible behavior.

## Open decisions

| ID | Decision required | Owner/reviewers | Needed by | Safe interim default |
|---|---|---|---|---|
| OPEN-GOV-001 | Legal Foundation form, board/community powers, appeals and conflict policy | Founders/legal/community | Before external alpha | Maintainers cannot assert Foundation/network powers |
| OPEN-GOV-002 | Validator admission/removal, voting-power and emergency governance | Network governance/SAGE/security | G2 testnet freeze | Permissioned lab validators; no public claim |
| OPEN-GOV-003 | DCO versus CLA after legal review | Foundation/legal/contributors | First public release | DCO sign-off |
| OPEN-GOV-004 | Federation membership, community representation, admission and exit; country/state/community participation | Founders/legal/community | Before first formal affiliate agreement | No affiliate status, exclusive territory or automatic board seat |
| OPEN-GOV-005 | Separation of donor, member and validator power; related-operator/control disclosures | Network governance/security/community | Before federation RFC adoption or public validator admission, whichever comes first | Existing validator rules; no authority inferred from donations or server count |
| OPEN-GOV-006 | Global/local grants, shared costs, assets/IP and any commercial ownership relationships | Foundation finance/legal/local operators | Before first inter-entity funding or asset agreement | No automatic entitlement, transfer, dividend or validator reward promise |
| OPEN-GOV-007 | Access, agency, participation and shared-benefit charter; appeals, accountability and revision | Founders/community/legal/security | Before federation charter adoption | IDEA-stage principles; current private-data and governance boundaries remain |
| OPEN-COM-001 | Exact initial validator count/operators/failure domains/cloud regions | Network/SRE/security | G2 | Four is planning hypothesis only |
| OPEN-COM-002 | Canonical challenge/correction/supersession semantics beyond v11.18.4 | SAGE product/network | G2 schema freeze | Use only supported canonical statuses; label publisher metadata |
| OPEN-COM-003 | Normative deterministic export schema, canonical ordering and exact proof/attestation language | SAGE core/export/security | G2 export freeze | `SAGE-EXPORT` unavailable; do not publish Commons Snapshot Packs |
| OPEN-COM-004 | Machine-readable SAGE capability-evidence schema and signing/verification contract (`SAGE-001c`) | SAGE core/Lantern/release | G2 capability freeze | Manually pin verified v11.18.4; disable dependent automation |
| OPEN-COM-005 | Public query authentication, anonymous-access decision, abuse/privacy limits and cache boundary | Commons query/SAGE/security/privacy | G2 query freeze | Registered authenticated eligible SAGE identity only |
| OPEN-PUB-001 | Accepted public knowledge licences by channel/domain | Governance/legal/curation | G2 | Public domain or explicitly approved permissive licence only |
| OPEN-PUB-002 | Intake quotas, batching, appeals and anti-Sybil mechanism | Intake/security/community | G2 load test | Conservative per-key/device/domain quotas |
| OPEN-PUB-003 | Human/automated moderation model per risk domain | Curation/safety/governance | G3 | Reject unsupported high-risk domains |
| OPEN-PUB-004 | Exact `SAGE-PROP-a/b/c` proposer/intake authorship, envelope idempotency, ambiguous-submit reconciliation and optional asynchronous derivative flow | Publication/SAGE/intake/security | G2 publication freeze | Lantern reconnects for one fresh submit; ambiguous results require reconciliation, never blind retry |
| OPEN-DST-001 | Canonical pack encoding, chunking, compression and POUF | Distribution/API/security | G2 interop freeze | Prototype format is non-normative |
| OPEN-DST-002 | Offline expiry/grace policy per pack/capability class | Security/operations/field | G2 | No grace for security/runtime/model activation |
| OPEN-DST-003 | Whether Return Pouch proceeds and its custody/legal model | Product/privacy/security | G4 earliest | Disabled/download-only |
| OPEN-FLE-001 | Field authority issuer and regional delegation governance | Foundation/field/security | G3 | Lab-only issuer |
| OPEN-FLE-002 | Fleet state split between one Fleet SAGE and operational stores | Fleet/SAGE/security | G2 architecture freeze | Selected audit/workflow in SAGE; bulk state outside |
| OPEN-FLE-003 | Self-hosting/opt-out/tenant model for fleet administration | Product/privacy/ops | G3 | Owner can operate without fleet connection |
| OPEN-KPT-001 | Knowledge Point deployment profiles, hosted Lightbearer combinations and validator qualification | Product/network/hardware/security | G2 | Cache/seed and query/export profiles only; no SAGE Observer role and no validator unless separately governed as a Lightbearer Validator |
| OPEN-LAN-001 | Production compute/power/enclosure/radio architecture | Hardware/product | G4 design freeze | Bench beta is evidence rig only |
| OPEN-LAN-002 | Local model portfolio, language coverage and safety policy | AI/product/community | G3 | Pinned beta model; explicit limitations |
| OPEN-LAN-003 | Exact display/touch connector, mode and stable OS device interface | Lantern hardware/runtime | G1 | Enumerate Unit A; do not hard-code guessed paths |
| OPEN-LAN-004 | PTT/wheel GPIO pins, active level, debounce and electrical intermediary | Lantern hardware/runtime | G1 | Bench-only reversible wiring; no enclosure freeze |
| OPEN-LAN-005 | Selected MeshCore firmware, host identity, baud, framing and payload limits | Radio/SAGE integration | Stage 4 / G1 | Treat radio as untrusted unavailable peripheral |
| OPEN-LAN-006 | Audio format, rate, channel/AEC path and buffering | Audio/runtime | G1 | Measure Unit A; preserve raw evidence outside private user data |
| OPEN-PRV-001 | Jurisdictional privacy roles, retention and user-right workflows | Legal/privacy/product | Before real-user alpha | Data minimization/local-only; no central personal telemetry |
| OPEN-OPS-001 | Production SLOs, support ownership and incident coverage | SRE/Foundation/network | G4 | Lab targets only |
| OPEN-OSS-001 | Trademark/certification policy compatible with forks | Foundation/legal/community | G4 | Apache rights unaffected; no certification claim |

## Programme risk register

Scale: likelihood/impact `L`, `M`, `H`, `C` (critical).

| ID | Risk | L/I | Leading indicator | Primary mitigation | Owner |
|---|---|---:|---|---|---|
| R-001 | Private source leaks through proposal, logs or telemetry | M/C | unexpected identifiers/content in fixtures | derivative-only flow, redaction, negative leak tests | Privacy/publication |
| R-002 | Foundation or operator authority expands into user/validator planes | M/C | shared credentials or broad dashboards | explicit planes, target checks, independent governance | Architecture/security |
| R-003 | Correlated validators lose safety/liveness | M/C | shared region/provider/operator/version | failure-domain policy and quorum rehearsals | Network SRE |
| R-004 | Malicious/stale knowledge causes harm | H/H | provenance/age hidden; high-risk answers | visible evidence, domain policy, challenge/correction, escalation | Safety/curation |
| R-005 | Model/data licence blocks redistribution | H/H | unknown/restricted artifacts in bundle | automated inventory/fail-closed release gate | Legal/release |
| R-006 | Supply-chain pack or image compromise | M/C | unverifiable build/signing anomaly | threshold trust, reproducibility, SBOM, revocation drills | Release security |
| R-007 | Offline clocks prevent safe expiry/revocation | H/H | rollback/unknown clock prevalence | monotonic floors, compact revocations, bounded/no grace | Device security |
| R-008 | Hardware cannot sustain local stack | M/H | thermal throttling, brownout, latency | Unit A evidence, resource arbitration, production redesign | Hardware/runtime |
| R-009 | Proposal spam overwhelms intake/moderation | H/H | backlog/duplicate/rejection surge | quotas, batching, admission, quarantine, capacity tests | Intake |
| R-010 | SAGE Lamplighter field economics are unsustainable | M/H | journey cost and stale coverage | route simulation, peer seeding, regional partners | Field operations |
| R-011 | Core SAGE changes invalidate adapters/contracts | M/H | version drift/conformance failures | pinned baseline, compatibility RFCs, upstream review | SAGE integration |
| R-012 | Documentation diverges from code/deployment | H/H | unlinked changes, stale evidence | traceability CI, release freeze, quarterly reconciliation | Docs/QA |
| R-013 | Local-language UX/model quality excludes target users | H/H | fallback language and task-failure rate | community co-design, language test corpus, accessibility | Product/community |
| R-014 | Lost field device enables unauthorized mutations | M/H | unrevoked capability/use anomaly | device binding, bounds, monotonic floors, revocation | Fleet security |
| R-015 | Governance capture or unresolved disputes fracture network | M/C | concentration, opaque decisions | open RFCs, independent operators, appeals and forkability | Governance |

Every risk review records residual risk, contingency trigger and retirement evidence. New material risks receive IDs; rows are never silently deleted.

Federation design context: [owner-endorsed exploration, 9 September 2026](roadmap/FOUNDATION_FEDERATION_DIRECTION_20260909.md). These decisions remain open; the discussion does not grant operational authority.
