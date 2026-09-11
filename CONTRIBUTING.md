# Contributing to SAGE Commons

SAGE Commons is being developed in the open under Apache-2.0. Contributions to code, documentation, schemas, tests and project-authored design assets are welcome.

## Before contributing

1. Read [`docs/MASTER_TECHNICAL_PRD.md`](docs/MASTER_TECHNICAL_PRD.md) and [`docs/DOCUMENTATION_GOVERNANCE.md`](docs/DOCUMENTATION_GOVERNANCE.md).
2. Check [`docs/OPEN_DECISIONS.md`](docs/OPEN_DECISIONS.md) before treating an unresolved choice as settled.
3. Keep [`docs/TERMINOLOGY.md`](docs/TERMINOLOGY.md) intact: SAGE Commons is the mission/network/programme; SAGE Lantern is the portable device; Knowledge Point is the physical station; SAGE Lamplighter is the offline super-seed; SAGE Lightbearer is a stable public-service profile whose validator authority is separately governed.
4. Never weaken private-by-default operation, user publication consent, personal/public authority separation, validator independence, cryptographic verification or honest offline status reporting.

## Change expectations

- Link every material change to one or more requirement IDs from [`docs/REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md).
- Add or update acceptance evidence and tests with implementation changes.
- Use an ADR for a durable local design decision and an RFC for a cross-subsystem, protocol, security, data, governance or compatibility change.
- Preserve user-authored work and document migrations; do not silently rewrite canonical data.
- Add SPDX identifiers to new source files where the file format permits: `SPDX-License-Identifier: Apache-2.0`.
- Record every non-Apache dependency, model, voice, dataset or asset in the dependency and attribution inventory before distribution.

## Contribution sign-off

Until the Foundation adopts a different contributor agreement, contributions use Developer Certificate of Origin sign-off. A commit sign-off means the contributor has the right to submit the work under the repository licence. The legal/governance working group MUST review this policy before the first public release.

## Security and privacy

Do not place keys, personal memories, raw audio, transcripts, private prompts, personal identifiers, validator homes or production credentials in issues, tests, fixtures or evidence bundles. Report suspected vulnerabilities using [`SECURITY.md`](SECURITY.md).
