# Open-source and licensing policy

**Status:** Candidate policy  
**Default project licence:** Apache License 2.0  
**Applies to:** SAGE Commons, SAGE Lantern, Knowledge Point, SAGE Lamplighter, SAGE Lightbearer and project-authored management surfaces

## 1. Default rule

Project-authored source code, documentation source, schemas, protocol definitions, tests, deployment recipes and original design assets are Apache-2.0 unless a file clearly states otherwise. Source files use `SPDX-License-Identifier: Apache-2.0` where their format permits. Generated object forms carry or link the corresponding licence and NOTICE.

## 2. What Apache-2.0 does not relicense

Upstream dependencies, operating-system packages, model weights, tokenizers, voices, fonts, datasets, maps, firmware, hardware reference files and contributed media retain their own licences. Downloading, bundling, chunking, signing or distributing them in a SAGE pack does not make them Apache-2.0. A pack manifest MUST identify every artifact's upstream source, exact version/hash, licence identifier/text, redistribution terms, attribution, geographic/use restrictions and whether commercial distribution or derivative creation is allowed.

A build MUST fail closed when a required artifact has an unknown, incompatible or non-redistributable licence. User-supplied local artifacts may be referenced without being redistributed, but the UI must state that distinction.

## 3. Release obligations

Every release produces:

- source archive and reproducible build instructions;
- SPDX or CycloneDX SBOM;
- dependency/model/data licence inventory;
- generated NOTICE and third-party attribution bundle;
- signed provenance and checksums;
- source offer or source links required by reciprocal dependencies;
- security contact, supported-version statement and known limitations; and
- machine-readable mapping from release artifacts to requirements and test evidence.

## 4. Contribution provenance

The initial contribution mechanism is Developer Certificate of Origin sign-off. Bots and generated contributions identify their human submitter/reviewer. Imported code or text requires provenance and licence compatibility; copying from an unverified source is prohibited. The permanent Foundation may adopt a CLA only through a public governance decision that preserves contributor and downstream rights.

## 5. Protocol and reference implementation

Normative specifications and interoperability fixtures are Apache-2.0. Patents known to be essential to an adopted protocol must be disclosed through the RFC process. The project prefers royalty-free standards and formats implementable independently. Branding and certification marks are governed separately from copyright and MUST NOT be used to make compatible forks impossible.

## 6. Data and public knowledge

Software licensing and knowledge licensing are separate. A public proposal MUST carry its content licence and provenance. The Commons validator policy may accept only approved open/public-domain licences for globally redistributed knowledge channels. Private user memories are not licensed to the project by running the software. Opting to publish a sanitized derivative grants only the licence explicitly shown and accepted in the publication flow.

## 7. Compliance gates

No public release, model pack, Commons Snapshot Pack, regional knowledge pack or SAGE Lamplighter load is publishable until automated scanning and a human release review confirm licence completeness. Exceptions require a time-bounded RFC decision, named owner, legal rationale, user-visible limitation and removal plan.
