# Documentation and specification governance

**Status:** Candidate normative process  
**Purpose:** Keep a 3–5 year multi-team programme coherent, auditable and implementable.

## 1. Source-of-truth hierarchy

When documents conflict, use this order:

1. ratified external law, licence obligation and adopted network governance;
2. accepted RFCs and security advisories;
3. [`MASTER_TECHNICAL_PRD.md`](MASTER_TECHNICAL_PRD.md) for programme-wide requirements and boundaries;
4. subsystem normative specifications linked by the master PRD;
5. ADRs for implementation-local decisions;
6. requirements/evidence registers and runbooks;
7. presentations, generated PDFs, historical drafts and informal discussion.

No lower-level document may silently override a higher-level contract. A discovered conflict is recorded immediately and resolved through the required review class.

## 2. Document states

`IDEA → DRAFT → REVIEW → CANDIDATE → NORMATIVE → DEPRECATED → SUPERSEDED`

- **IDEA:** exploration; no implementation reliance.
- **DRAFT:** structured work; material gaps allowed and named.
- **REVIEW:** ready for named product/security/engineering review.
- **CANDIDATE:** internally consistent and usable for prototypes; unresolved evidence gates remain explicit.
- **NORMATIVE:** approved contract with version and owners.
- **DEPRECATED:** still readable but no new reliance.
- **SUPERSEDED:** replacement named; retained for history.

Every normative/candidate document states status, version, owners, reviewers, last-updated date, scope, dependencies and supersedes/superseded-by links.

## 3. Requirement language and identifiers

`MUST`, `MUST NOT`, `SHOULD`, `SHOULD NOT` and `MAY` use RFC 2119/8174 meanings only when uppercase. Each testable normative requirement receives a stable ID. Prefixes are `SYS`, `SAGE`, `LAN`, `COM`, `PUB`, `KPT`, `LBR`, `DST`, `FLE`, `UX`, `SEC`, `PRV`, `OPS`, `OBS`, `API`, `DAT`, `OSS` and `GOV`. IDs are never reused after deletion.

Each requirement records source, rationale, owner, target milestone, dependencies, acceptance method, evidence URI, status and change history. “Implemented” without evidence is not “verified.”

## 4. Change classes

| Class | Examples | Required record/review |
|---|---|---|
| Editorial | typo, link, non-semantic clarity | normal review |
| Local design | replaceable internal choice | ADR + subsystem owner |
| Product contract | UX, API, schema, lifecycle change | RFC + product/engineering/test review |
| Trust/security/privacy | keys, authority, consent, data flow | RFC + threat model + independent security/privacy review |
| Network/governance | consensus, validator, public policy | RFC + formal network-governance approval |
| Breaking | incompatible protocol/data/upgrade | RFC + migration, rollback, compatibility window and operator notice |

## 5. RFC minimum contents

Problem; scope/non-goals; users; terminology; current evidence; proposed contract; alternatives; authority/data/trust flows; protocol/schema; failure and abuse cases; privacy/security threat model; compatibility/migration/rollback; observability; test plan; deployment; open-source/licence impact; cost/capacity; unresolved questions; decision and dissent log.

## 6. ADR minimum contents

Context; decision; status; consequences; rejected alternatives; requirements affected; rollback/revisit trigger; date/owners.

## 7. Review and freeze rules

- Product owner confirms intent and user harm boundaries.
- Engineering owner confirms implementability and dependencies.
- Security/privacy owner reviews every new authority or data path.
- Operations owner confirms recovery and observability.
- Test owner maps acceptance criteria to executable or witnessed evidence.
- Public APIs, wire formats and pack schemas require interoperability fixtures before NORMATIVE.
- A frozen milestone records exact document versions and requirement baseline; later changes are explicit deltas.

Unknowns are written as `OPEN-<area>-NNN` with owner, decision deadline, blocked milestone and safe default. `TBD` without ownership is prohibited in candidate/normative documents.

## 8. Evidence and implementation drift

Evidence is immutable or content-addressed where practical and records hardware/software versions, configuration, time, operator/test identity and result. CI checks links, schemas, requirement coverage, licence inventory and stale open decisions. Quarterly architecture reconciliation compares deployed behavior to normative documents. Emergency deviations are time-bounded, audited and followed by an RFC or rollback.

## 9. Publication and accessibility

Canonical sources remain reviewable text whenever possible. Generated DOCX/PDF/decks are outputs, not the only source. Diagrams include text descriptions. Public documentation distinguishes facts, approved decisions, proposals, assumptions and measured evidence. Sensitive operational detail may be access-controlled, but its existence, owner and review status remain visible.
