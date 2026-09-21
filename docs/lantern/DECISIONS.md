# SAGE Lantern Hardware Decision Log

This log records the decisions that define the beta. Add a dated entry when a choice changes; do not silently rewrite history.

## D-001 — Two complete beta units

**Decision:** Build Unit A to completion, then reproduce it as Unit B.  
**Reason:** The product must prove device-to-device communication, but debugging two changing builds simultaneously would multiply uncertainty.

## D-002 — ROCK 5B+ as beta compute

**Decision:** Use the two already-owned ROCK 5B+ boards.  
**Consequence:** The first enclosure will be larger and thicker than a Rabbit R1, and active cooling is mandatory.

## D-003 — Rabbit R1 as reference, not platform

**Decision:** Use one Rabbit R1 for teardown, ergonomics, mechanical study, and design inspiration.  
**Consequence:** The beta must not depend on successfully reusing the Rabbit display, wheel, camera, or mainboard.

## D-004 — Waveshare modular interaction stack

**Decision:** Use the Waveshare ESP32-S3 audio board, 3.5-inch HDMI touch display, and USB hub for the beta.  
**Reason:** Purchasable modules reduce custom electronics work during functional validation.

## D-005 — Heltec V4 as MeshCore companion

**Decision:** Use one Heltec WiFi LoRa 32 V4 per ROCK, connected as a companion rather than a raw LoRa HAT.  
**Consequence:** MeshCore firmware and the host companion interface must be validated; raw Raspberry Pi HAT integration is out of scope.

## D-006 — Off-the-shelf power for beta

**Decision:** Use 65 W USB-C PD supplies on the bench and 130 W/20,000 mAh USB-C PD power banks for portable tests.  
**Consequence:** No custom internal battery/BMS work occurs before real power measurements.

## D-007 — Simple physical controls

**Decision:** Use one momentary PTT button and one KY-040 encoder with click per unit.  
**Consequence:** A custom R1-style wheel may be printed later, but is not a dependency for electrical bring-up.

## D-008 — Enclosure follows measurement

**Decision:** Do not start detailed enclosure CAD until the functioning stack is assembled and measured.  
**Reason:** Cable bends, connector access, airflow, antenna placement, acoustic volumes, and power-bank dimensions cannot be safely guessed.

## D-009 — Product naming architecture

**Decision:** Keep SAGE Commons as the overall mission, governed public knowledge network and ecosystem; name the portable device product SAGE Lantern; use Knowledge Point for a physical/institutional station, SAGE Lamplighter for the offline super-seed/field system, and SAGE Lightbearer for a stable public-service node/profile.  
**Consequence:** Device-specific requirements and artifacts use SAGE Lantern. Public-chain, governance, funding, pilot and ecosystem materials retain SAGE Commons. A Knowledge Point may host isolated Lightbearer profiles, but the facility name grants no validator authority. Every validator is a separately governed Lightbearer Validator. See [`../TERMINOLOGY.md`](../TERMINOLOGY.md).

**Superseded in part (2026-09-21):** the first clause — "SAGE Commons as the overall mission, governed public knowledge network and ecosystem" — is corrected by D-014. The remaining naming assignments (SAGE Lantern, Knowledge Point, SAGE Lamplighter, SAGE Lightbearer) stand.

## D-010 — One personal SAGE node per Lantern

**Decision:** Plan for each SAGE Lantern to run its own full local personal SAGE node. A Knowledge Point is an optional persistence and synchronisation peer, not a runtime dependency for the local voice loop.
**Qualification:** Confirm the decision with measured ROCK 5B+ latency, memory, thermal, and portable-power evidence. Client-only operation is a documented fallback if the full node cannot meet the beta's stability requirements.

## D-011 — Two-part end-to-end acceptance demo

**Decision:** The first end-to-end proof consists of five offline voice loops on Unit A followed by a bounded, attributable artifact exchange from Unit A to Unit B and back again over MeshCore.
**Consequence:** A speaking prototype or a raw radio ping alone is insufficient. The evidence must correlate application, SAGE, and transport states and include recovery, timing, size, retry, and duplicate behavior.

## D-012 — Separate per-unit identities and constrained transport

**Decision:** Each unit receives a unique ordinary Ed25519 SAGE identity and a separate unique radio identity after the base image is cloned. MeshCore is an untrusted constrained transport adapter, not an authority source.
**Consequence:** Never clone `~/.sage`, Root/Admin keys, receive tokens, idempotency state, or radio identity. Use canonical `/v1/messages` semantics at the SAGE boundary and validate every radio-carried envelope before agent exposure.

## D-013 — Lanterns propose; stable servers validate

**Decision:** Every Lantern may query the public SAGE Commons and submit signed hyperlocal-knowledge proposals, but Lanterns are not validators on the public Commons chain. Public consensus runs on a governed set of stable, always-on server validators.
**Reason:** Intermittent handheld connectivity must not reduce public-chain liveness or place consensus keys on portable devices. Proposing knowledge and validating consensus are separate roles.
**Consequence:** A Lantern queues proposals while offline and submits them when connected with provenance, locality, observation time, confidence, and freshness/expiry metadata. It receives no public validator key or voting power.

## D-014 — SAGE Commons is the fabric; the Lantern is its first deployment

**Decision:** SAGE Commons is the fabric: the core mesh technology and governed public network — MeshCore for transport plus SAGE running the public chain, with the contracts that make them one interoperable, community-owned commons — that community networks, services and devices join. SAGE Lantern is the first deployment on that fabric: each Lantern holds its own sovereign private SAGE (its own private chain) and connects to the Commons as a proposer/query client, never as a public validator.
**Reason:** Owner direction, 2026-09-21, resolving a live conflict in the written material: the repo described the Commons as the mission/programme surrounding the device, while the owner describes it as the fabric — core mesh technology aimed at a community-owned network layer of the "internet 2.0" kind — on which the Lantern is the first deployment. A statistic, a deck page or a public sentence built on the wrong one of those two shapes is wrong in public.
**Consequence:** The Commons is a technical and network object (transport, public chain, protocols, contracts), not a marketing umbrella. The mission belongs to the initiative and the proposed Foundation; the Foundation stewards the fabric but does not own it. "Shared knowledge network" describes what the fabric carries, not what it is. Device-specific requirements and artifacts continue to use SAGE Lantern.
**Affects:** [`../TERMINOLOGY.md`](../TERMINOLOGY.md) (SAGE Commons row and the new "Commons fabric" section), [`../MASTER_TECHNICAL_PRD.md`](../MASTER_TECHNICAL_PRD.md) §3 taxonomy row, `CONTRIBUTING.md`. Public website copy is owned by the marketing agent and is flagged to it, not edited here.

## Open decisions

| ID | Decision needed | Evidence required | Owner | Target phase |
|---|---|---|---|---|
| O-001 | Exact audio-board host protocol and firmware role | Enumeration, board documentation, capture/playback test | Hardware + Mynah | Unit A audio |
| O-002 | PTT and encoder electrical interface | Safe GPIO mapping and debounce test | Hardware | Unit A controls |
| O-003 | Local STT/LLM/TTS stack and performance target | Measured timings and thermals on ROCK 5B+ | SAGE software + hardware evidence | Unit A voice |
| O-004 | MeshCore host protocol limits | Verified Heltec firmware/API, payload ceiling, fragmentation, retry, duplicate, and two-node evidence | Hardware evidence, then SAGE software | Radio |
| O-005 | Portable runtime target | Measured representative load and power-bank behavior | Product + hardware | Portable beta |
| O-006 | Enclosure architecture | Full measured stack, thermal and antenna evidence | Hardware/product design | After acceptance |
| O-007 | Whether wake word is required for this beta | PTT usability and audio-frontend capability | Product | After stable PTT |
