# Product Requirements Document: SAGE Lantern Bench Beta

**Status:** Draft baseline  
**Version:** 0.1  
**Date:** 2026-08-09  
**Programme:** SAGE Commons  
**Device product:** SAGE Lantern  
**Hardware owner:** Codex hardware agent  
**Software stakeholder:** `codex/sage`  
**Voice-bridge stakeholder:** Mynah / SAGE Voice Bridge

## 1. Product summary

SAGE Lantern is the portable, voice-first device product within the SAGE Commons programme. The two-unit bench beta will test whether a local-first assistant can combine speech, local inference, SAGE context, simple physical controls, a touchscreen, and low-bandwidth MeshCore communication in a practical handheld-style appliance.

The beta is inspired by the Rabbit R1 interaction model, not its cloud-dependent architecture. It uses a push-to-talk button, rotary wheel, touchscreen, microphones, and speaker around substantially more capable local compute.

## 2. Problem

SAGE currently needs a convincing physical interface for situations where opening a laptop or using a conventional phone application is undesirable. The product must make voice interaction immediate, preserve local capability where practical, expose SAGE state clearly, and allow two devices to exchange useful information over a LoRa mesh.

## 3. Beta objective

Prove two complete tabletop/portable SAGE Lantern units that can:

1. accept speech through push-to-talk;
2. perform an end-to-end local voice-agent loop;
3. use SAGE context or memory during that loop;
4. present responses through audio and a touchscreen;
5. support wheel-based navigation;
6. exchange a useful message or knowledge artifact between Unit A and Unit B through MeshCore; and
7. queue and submit an attributable hyperlocal-knowledge proposal to the public SAGE Commons when connected; and
8. produce enough thermal, power, dimensional, and usability evidence to design the first integrated enclosure.

## 4. Target user and core scenario

The initial user is a technical beta operator testing a SAGE Lantern node.

Core scenario:

1. The operator holds PTT and speaks.
2. The device captures the utterance and transcribes it locally.
3. The local agent/LLM uses SAGE context to form a response.
4. The response appears on screen and is spoken locally.
5. The operator scrolls or selects with the wheel.
6. When appropriate, the device exchanges a bounded message or artifact with the second unit over MeshCore.

## 5. Product principles

- **Local-first:** core voice interaction should continue without dependence on a cloud inference service.
- **Simple controls:** PTT, wheel/click, touch, and voice are sufficient for the beta.
- **Observable:** failures must be distinguishable across power, audio, inference, SAGE, UI, and radio layers.
- **Two-unit proof:** a single speaking prototype does not satisfy the mesh objective.
- **Measure before miniaturizing:** the enclosure follows the working electronics and measured thermals.
- **Replaceable beta modules:** use purchasable modules and cables before committing to a custom PCB.
- **Sovereign by default:** each Lantern should host its own personal SAGE node when measured platform limits permit; synchronization with a Knowledge Point-hosted Community Lightbearer is optional.
- **Propose, do not validate:** Lanterns query and propose to the public Commons; stable always-on servers own public consensus validation.

## 6. Functional requirements

### FR-1 — Stable compute

Each unit shall boot reliably from its NVMe storage using a known-good 65 W USB-C PD supply and active cooling.

### FR-2 — Touch display

Each unit shall display the beta interface and accept touch input through its 3.5-inch display.

### FR-3 — Audio input and output

Each unit shall capture intelligible speech and play intelligible responses through the selected Waveshare audio frontend. Echo cancellation and noise handling shall be evaluated under realistic speaker playback.

### FR-4 — Push-to-talk

Holding PTT shall begin or enable capture; releasing it shall end capture and submit the utterance. The final electrical mapping remains a bring-up decision.

### FR-5 — Wheel interaction

The rotary encoder shall provide clockwise, counter-clockwise, and press events. The beta interface shall use these for navigation, selection, or scrolling.

### FR-6 — Local voice loop

The system shall complete:

`speech → STT → local agent/LLM + SAGE → response → TTS`

The UI shall show enough state to distinguish listening, transcribing, thinking, speaking, and error conditions.

### FR-7 — MeshCore companion

Each ROCK shall communicate with its Heltec V4 running compatible MeshCore companion firmware. The beta shall not rely on a raw Raspberry Pi LoRa HAT.

### FR-8 — Two-device exchange

Unit A and Unit B shall exchange a bounded, attributable, versioned payload over MeshCore in both directions. The receiver shall validate it as untrusted data before exposing it to its SAGE-facing flow and shall return a bounded acknowledgement or reply.

### FR-9 — Portable beta power

Each unit shall operate from its 130 W/20,000 mAh USB-C PD power bank long enough to run the acceptance demonstration. Runtime and peak behavior shall be measured rather than assumed.

### FR-10 — Diagnostic record

The team shall record software version, wiring, power source, test outcome, and evidence for every acceptance run.

### FR-11 — Public Commons proposal

A Lantern shall queue signed hyperlocal-knowledge proposals while offline and submit them when connected. A proposal shall carry provenance, locality, observation time, confidence, and freshness or expiry metadata. Submission state shall distinguish queued, submitted, accepted, rejected, and expired. The Lantern shall not hold a public-chain validator key or voting power.

## 7. Non-functional requirements

- **Reliability:** five consecutive cold boots and five consecutive voice interactions without unexplained resets for each unit.
- **Responsiveness:** stage timings must be recorded for capture, STT, agent/LLM, SAGE access, and TTS. Numeric targets will be set after the first measured baseline.
- **Thermals:** sustained workloads must not cause crashes or uncontrolled thermal shutdown. Temperatures and throttling observations must be recorded.
- **Privacy:** the default beta path shall not require cloud inference. Any network-dependent path must be visible and intentional.
- **Recoverability:** a failed peripheral or service should not require rebuilding the entire unit image.
- **Maintainability:** Unit B should be reproducible from a documented Unit A image/configuration plus per-device identity settings.
- **Identity isolation:** Unit B must receive new ordinary SAGE and radio identities after cloning; privileged keys, tokens, journals, and identity state must not be copied.
- **Protocol stability:** the SAGE integration boundary shall use canonical `/v1/messages` semantics and record the exact tested SAGE version/commit.

## 8. Beta hardware architecture

Per unit:

- Radxa ROCK 5B+
- 512 GB NVMe SSD
- Radxa 6240B V2 active cooler
- Waveshare ESP32-S3 audio board
- Waveshare 3.5-inch HDMI capacitive-touch display
- Waveshare USB 3.2 hub
- Heltec WiFi LoRa 32 V4 with ESP32-S3 and SX1262
- momentary PTT button
- KY-040 rotary encoder with push-click
- 65 W USB-C PD bench supply
- UGREEN 130 W/20,000 mAh USB-C PD power bank
- required data and jumper cabling

One Rabbit R1 is shared as a teardown and industrial-design reference.

## 9. Scope exclusions for this beta

- production enclosure or industrial design sign-off;
- custom carrier PCB;
- custom battery pack, BMS, or charging electronics;
- camera integration;
- LTE/cellular integration;
- production wake-word tuning;
- regulatory, EMC, RF, battery, drop, ingress, or safety certification;
- mass-production costing or supply-chain qualification;
- reuse of Rabbit R1 electronics as a beta dependency.

## 10. Delivery phases

### Phase 0 — Inventory and inspection

Confirm physical receipt, model/variant, included accessories, and visible damage for every component.

### Phase 1 — Unit A platform

Boot, storage, cooling, display, touch, and stable bench power.

### Phase 2 — Unit A interaction

Audio, local voice loop, SAGE integration, PTT, and wheel.

### Phase 3 — Radio

Flash and connect the Heltec companion; validate local send/receive behavior.

### Phase 4 — Unit B and two-node demonstration

Clone the proven platform, assign distinct identities, and complete the mesh/SAGE exchange.

### Phase 5 — Portable and mechanical evidence

Measure runtime, peak power behavior, thermals, cable clearances, and assembled dimensions. Produce the enclosure requirements.

## 11. Beta acceptance

The SAGE Lantern bench beta is accepted when all of the following are demonstrated and recorded:

- both units pass the stable boot check;
- both displays and touch interfaces work;
- Unit A completes five consecutive voice loops with cloud inference unavailable, one exact pre-seeded SAGE context item, correlated session logs, and recovery evidence;
- Unit B independently completes the voice-loop smoke test with its own identity;
- both PTT buttons and encoders work;
- both Heltec companions are detected and controllable;
- Unit A and Unit B exchange and acknowledge the defined MeshCore/SAGE payload in both directions, safely reject a malformed or duplicate envelope, and record size/timing/retry evidence;
- both units complete the demonstration on portable power;
- measured thermals, runtime, dimensions, wiring, and known defects are documented;
- the evidence is sufficient to begin enclosure CAD without guessing component positions.

## 12. Ownership

The hardware owner maintains the BOM, receipt state, compatibility decisions, wiring, physical bring-up, thermal and power evidence, Rabbit reference teardown, and enclosure inputs.

`codex/sage` owns or coordinates SAGE runtime behavior and will receive focused requests only when a hardware test produces a concrete software integration dependency.

Mynah owns or coordinates the voice-bridge application path where applicable.

No software work is requested merely by publication of this PRD.

## 13. Open product questions

- What local model stack meets acceptable latency on the ROCK 5B+?
- What is the exact host interface and firmware role of the Waveshare audio board?
- What payload ceiling, fragmentation, retry, timeout, and duplicate behavior does the chosen Heltec/MeshCore host path exhibit?
- What power-management behavior is safe when moving between bench PSU and power bank?
- Is an always-listening wake path needed after PTT is stable?
- What enclosure volume and venting are required by measured sustained thermals?
