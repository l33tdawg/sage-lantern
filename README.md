# SAGE Lantern

Private prototype development: device runtime, touchscreen interface, firmware,
provisioning tools, and SAGE Commons architecture. Implemented, staged, deployed,
and physically accepted are separate milestones; this is not a production-ready
release. See [the Unit A completion gate](docs/lantern/DELIVERY_BACKLOG.md).

This directory collects product, architecture, presentation, and implementation material for SAGE Commons. The `docs/lantern/` package is the working source of truth for the two-unit SAGE Lantern bench beta: a Rabbit R1-inspired, local-first voice appliance built around the Radxa ROCK 5B+.

Project-authored code, documentation, schemas and tests are licensed under [Apache-2.0](LICENSE). Third-party models, data, voices, firmware and other artifacts retain their upstream licences; see [`docs/OPEN_SOURCE_POLICY.md`](docs/OPEN_SOURCE_POLICY.md).

## Naming system

- **SAGE Commons** — the overall mission, governed public knowledge network, community programme, and ecosystem.
- **SAGE Lantern** — the portable, local-first AI device product.
- **Knowledge Point** — the physical/institutional research, publishing, caching, synchronisation and service station.
- **SAGE Lamplighter** — the untrusted offline super-seed and field-service system carrying verified packs.
- **SAGE Lightbearer** — a stable public-service node/profile; validators are independently governed **Lightbearer Validators**.

The precise naming and authority boundaries are in [`docs/TERMINOLOGY.md`](docs/TERMINOLOGY.md).

## SAGE Lantern hardware package

- [`docs/lantern/PRD.md`](docs/lantern/PRD.md) — product intent, scope, requirements, and acceptance criteria.
- [`docs/lantern/HARDWARE_BOM.md`](docs/lantern/HARDWARE_BOM.md) — authoritative bill of materials and arrival status.
- [`docs/lantern/BRINGUP_AND_VALIDATION.md`](docs/lantern/BRINGUP_AND_VALIDATION.md) — staged assembly, test procedure, and evidence checklist.
- [`docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md`](docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md) — ownership boundary and provisional interfaces between the device hardware and SAGE software.
- [`docs/lantern/DECISIONS.md`](docs/lantern/DECISIONS.md) — decisions already made and the open decisions that must be resolved from real hardware.
- [`docs/lantern/UNIT_A_ACCEPTANCE_EVIDENCE.md`](docs/lantern/UNIT_A_ACCEPTANCE_EVIDENCE.md) — canonical Unit A build, offline voice, timing, and recovery evidence record.
- [`docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md`](docs/lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md) — host protocol, payload-limit, fragmentation, retry, duplicate, and rejection test matrix.
- [`docs/lantern/SOFTWARE_STACK.md`](docs/lantern/SOFTWARE_STACK.md) — pinned ARM64 local-AI/SAGE stack, offline bundle, installation, and identity boundary.
- [`docs/lantern/UNIT_A_BOOTSTRAP.md`](docs/lantern/UNIT_A_BOOTSTRAP.md) — Unit A Debian preparation, installation, provisioning, service, smoke-test, and evidence runbook.
- [`docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md`](docs/lantern/DUAL_HOME_AND_PUBLICATION_CONTRACT.md) — normative personal/public separation, proposer manifest, intake, receipts, and no-validator contract.

## Administration and dual-network design

- [`docs/admin/DUAL_NETWORK_ARCHITECTURE_OPTIONS.md`](docs/admin/DUAL_NETWORK_ARCHITECTURE_OPTIONS.md) — private personal nodes, stable public validators, signed proposal intake, and phased topology.
- [`docs/admin/LANTERN_FLEET_ADMIN_PRD.md`](docs/admin/LANTERN_FLEET_ADMIN_PRD.md) — provisioning and fleet-management requirements.
- [`docs/admin/PUBLIC_NETWORK_ADMIN_PRD.md`](docs/admin/PUBLIC_NETWORK_ADMIN_PRD.md) — public Commons operations, governance, proposal, and transparency requirements.
- [`docs/admin/PUBLIC_NETWORK_ADMIN_DESIGN.md`](docs/admin/PUBLIC_NETWORK_ADMIN_DESIGN.md) — proposed information architecture and operator/public workflows.

## Offline distribution

- [`docs/distribution/DATA_DONKEY_ARCHITECTURE.md`](docs/distribution/DATA_DONKEY_ARCHITECTURE.md) — signed knowledge/model/runtime packs, physical store-carry-forward, local swarm distribution, activation, rollback and privacy boundaries.
- [`docs/distribution/DATA_DONKEY_OPERATIONS_AND_UX.md`](docs/distribution/DATA_DONKEY_OPERATIONS_AND_UX.md) — Foundation, field-laptop, village-cluster and Lantern authority, workflows, screens, offline delegation and receipts.
- [`docs/distribution/DECISIONS.md`](docs/distribution/DECISIONS.md) — frozen distribution decisions and unresolved evidence gates.

## Programme documents

- [`docs/TERMINOLOGY.md`](docs/TERMINOLOGY.md) — canonical Commons/Lantern/Knowledge Point/Lamplighter/Lightbearer vocabulary.
- [`docs/MASTER_TECHNICAL_PRD.md`](docs/MASTER_TECHNICAL_PRD.md) — normative 3–5 year programme and system contract.
- [`docs/SAGE_V12_DEPENDENCY_CROSSWALK.md`](docs/SAGE_V12_DEPENDENCY_CROSSWALK.md) — joint core-SAGE capability ownership, availability states, non-promises and evidence gates.
- [`docs/REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) — stable requirement IDs, owners, gates and acceptance evidence.
- [`docs/OPEN_DECISIONS.md`](docs/OPEN_DECISIONS.md) — unresolved choices and programme risks.
- [`docs/roadmap/THREE_TO_FIVE_YEAR_DELIVERY_PLAN.md`](docs/roadmap/THREE_TO_FIVE_YEAR_DELIVERY_PLAN.md) — phased delivery plan and evidence gates.
- [`docs/roadmap/TII_INITIAL_FUNDING_READINESS.md`](docs/roadmap/TII_INITIAL_FUNDING_READINESS.md) — current prototype through TII demonstration, evidence, budget and initial funding request; pre-meeting versus funded-work scope.
- [`docs/roadmap/GATE_EVIDENCE_CONTRACT.md`](docs/roadmap/GATE_EVIDENCE_CONTRACT.md) — reproducible G0–G6 manifests, approvals, thresholds and waiver format.
- [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md) — specification hierarchy, RFC/ADR/change and evidence rules.
- [`docs/OPEN_SOURCE_POLICY.md`](docs/OPEN_SOURCE_POLICY.md) — Apache-2.0 scope, third-party licensing and release compliance.
- [`docs/SAGE_Commons_PRD_Revised.docx`](docs/SAGE_Commons_PRD_Revised.docx) and [`PDF`](docs/SAGE_Commons_PRD_Revised.pdf) — programme PRD, updated to name the portable product **SAGE Lantern**.
- [`docs/SAGE_Commons_Technical_Architecture_Revised.docx`](docs/SAGE_Commons_Technical_Architecture_Revised.docx) and [`PDF`](docs/SAGE_Commons_Technical_Architecture_Revised.pdf) — system architecture with the Commons/Lantern/Knowledge Point boundary made explicit.
- [`docs/SAGE_Foundation_Pitch_Deck_September_2026.pptx`](docs/SAGE_Foundation_Pitch_Deck_September_2026.pptx) and [`PDF`](docs/SAGE_Foundation_Pitch_Deck_September_2026.pdf) — current campaign pitch: A/B prototypes, access to intelligence, US$133.7M ten-year goal and first-stage Foundation/team/pilot plan.
- [`SAGE federation direction`](docs/roadmap/FOUNDATION_FEDERATION_DIRECTION_20260909.md) — proposed global/local responsibilities, community rights and independent network governance; institutional exploration.
- [`Foundation campaign brief`](docs/roadmap/FOUNDATION_CAMPAIGN_BRIEF_20260909.md) — owner direction, founder commitment, January launch, purchase/donation assumptions and open-platform roadmap.
- [`Emad Champions ownership research`](docs/roadmap/EMAD_CHAMPIONS_OWNERSHIP_RESEARCH_20260909.md) — the nominal $1 company valuation, equity versus donations and implications for SAGE.
- [`Foundation jurisdiction and launch research`](docs/roadmap/FOUNDATION_JURISDICTION_AND_LAUNCH_20260909.md) — Malaysia/UK shortlist, fiscal sponsors, payment eligibility and establishment sequence.

Older `DRAFT`, `Revised` and `Refined` pitch-deck files remain as historical snapshots; the September 2026 Foundation deck is current.

## Working rules

1. Build and validate Unit A before cloning the setup to Unit B.
2. Do not mark a component received from an order confirmation alone; record physical receipt in the BOM.
3. Keep measured values and test evidence in the validation document rather than silently replacing assumptions.
4. Do not begin the enclosure CAD until the assembled stack has been measured.
5. A requirement is complete only when its acceptance evidence is recorded.
6. Cross-subsystem, security, privacy, protocol or governance changes require an RFC; local durable implementation choices require an ADR.
7. Presentations and generated PDFs never override the master PRD or normative subsystem specifications.

## Bring-up utility

On a Lantern ROCK running Linux, capture a reproducible platform snapshot with:

```bash
./scripts/capture-lantern-evidence.sh evidence/<test-id>
```

The utility records OS, storage, USB/PCI enumeration, stable device paths, thermal sensors, OS-visible power data, SAGE version, and failed services. It does not read SAGE keys, tokens, message content, or private audio.

## Prepared software bundle

The Linux ARM64 beta stack is cached under `software/`: exact SAGE v11.19.10, llama.cpp, Qwen 3.5 4B, whisper.cpp and model, Piper and voice, the Lantern integration runtime, and an offline Python wheelhouse. Verify it with `python3 scripts/verify-lantern-bundle.py`; installation and per-unit identity rules are in the software-stack document above.

## Current phase

Component arrival plus software pre-staging; Unit A installation and bench bring-up begin when the remaining display, audio, storage, cooling and control hardware is physically available.

Last status baseline: **2026-08-09**.
