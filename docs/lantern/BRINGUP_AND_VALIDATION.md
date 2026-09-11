# SAGE Lantern Bring-up and Validation Plan

This document is both the test plan and the evidence index. Do not turn a checkbox into a pass without recording the requested evidence.

Use `scripts/capture-lantern-evidence.sh evidence/<test-id>` at the start of a major platform run to capture a consistent, non-secret system snapshot.

## Test record template

Copy this block for each meaningful run:

```text
Test ID:
Date/time:
Unit: A | B
Operator:
Hardware revisions:
Power source:
OS/image version:
Application commit/version:
SAGE version/commit:
SAGE agent identity:
Radio identity:
Wiring/config reference:
Steps:
Observed result:
Measurements:
Evidence links:
Result: PASS | FAIL | BLOCKED
Follow-up:
```

## Safety and handling gates

- [ ] Work on a non-conductive surface.
- [ ] Confirm cooler contact and fan connection before sustained compute load.
- [ ] Inspect headers and cable orientation before applying power.
- [ ] Do not hot-plug unknown GPIO wiring.
- [ ] Do not power one rail from two sources simultaneously unless the module documentation explicitly allows it.
- [ ] Keep the LoRa antenna attached before intentional radio transmission.
- [ ] Confirm the selected radio configuration is appropriate for the operating region before over-the-air testing.

## Stage 0 — Inventory

- [ ] Every received item is reconciled against `HARDWARE_BOM.md`.
- [ ] Exact revisions and included accessories are recorded.
- [ ] Unit A and Unit B parts are labeled without permanently modifying them.
- [ ] Missing dependencies are listed.

Evidence:

```text
Pending.
```

## Stage 1 — Unit A platform

### 1.1 Physical assembly

- [ ] Install the Unit A NVMe.
- [ ] Install the 6240B V2 cooler and connect its fan.
- [ ] Photograph the assembly before first power.

### 1.2 First boot

- [ ] Boot from the 65 W bench PSU.
- [ ] Confirm the expected OS and storage capacity.
- [ ] Confirm network access needed for initial provisioning.
- [ ] Confirm fan operation and collect idle temperature.
- [ ] Reboot five times from a cold/off state.

Pass condition: five consecutive boots without unexplained reset, storage error, or thermal fault.

### 1.3 Sustained platform test

- [ ] Run a representative sustained CPU/NPU/model workload.
- [ ] Record temperature, throttling indication, wall/PD power if available, and stability.

Pass condition: no crash or uncontrolled shutdown. Initial numeric thermal limits remain provisional until the hardware and sensor reporting are verified.

## Stage 2 — Display and touch

- [ ] Connect HDMI video.
- [ ] Connect the display's touch/data path.
- [ ] Verify native resolution and orientation.
- [ ] Verify touch coordinates after rotation.
- [ ] Verify five minutes of continuous UI operation without disconnects.

Pass condition: readable UI and accurate touch across the usable screen.

## Stage 3 — Audio

- [ ] Record exact audio-board connection and firmware.
- [ ] Confirm the host can select or communicate with the board.
- [ ] Capture speech at near and normal handheld distances.
- [ ] Play a known response through the speaker path.
- [ ] Test capture while the device is playing audio.
- [ ] Record artifacts, echo behavior, gain, and noise observations.

Pass condition: intelligible capture and playback suitable for the end-to-end voice test.

## Stage 4 — Local voice and SAGE

- [ ] Disable or make unavailable cloud/internet inference for the acceptance run.
- [ ] Pre-seed one exact SAGE context item and record its identifier/hash.
- [ ] Show visible states for listening, transcribing, thinking, speaking, and error.
- [ ] Complete one local STT run.
- [ ] Complete one local agent/LLM response.
- [ ] Demonstrate SAGE context affecting or supporting the response.
- [ ] Complete local TTS playback.
- [ ] Record per-stage timings.
- [ ] Correlate one session identifier across UI, capture, STT, agent, SAGE, TTS, and logs.
- [ ] Demonstrate clean process restart and peripheral disconnect/reconnect recovery.
- [ ] Repeat five times.

Pass condition: five consecutive complete interactions with cloud inference unavailable, the expected SAGE context item evidenced, no unexplained reset, and recovery evidence recorded.

## Stage 5 — Controls

### PTT

- [ ] Map the electrical input safely.
- [ ] Press/hold begins or enables capture.
- [ ] Release ends capture and submits.
- [ ] Debounce behavior is acceptable.

### Rotary encoder

- [ ] Clockwise events register once per intended step.
- [ ] Counter-clockwise events register once per intended step.
- [ ] Press events register.
- [ ] UI navigation remains usable during a voice response.

Pass condition: ten operations of each input type without a missed or unintended product action.

## Stage 6 — Heltec and MeshCore

- [ ] Record exact Heltec board revision and radio variant.
- [ ] Flash the selected compatible MeshCore companion firmware.
- [ ] Confirm USB data enumeration on the ROCK.
- [ ] Record the serial/device identity and permissions/configuration.
- [ ] Send and receive a local test payload.
- [ ] Measure the maximum reliable payload size.
- [ ] Record framing, fragmentation, retry, timeout, and duplicate behavior.
- [ ] Record reboot and USB reconnect behavior.

Pass condition: the host can reliably address its companion after reboot.

## Stage 7 — Unit B replication

- [ ] Reproduce the proven Unit A image/configuration.
- [ ] Remove any identity-bearing state from the clone before first network/radio use.
- [ ] Provision a unique ordinary Ed25519 SAGE identity.
- [ ] Provision a separate unique radio identity.
- [ ] Confirm `~/.sage`, Root/Admin keys, receive tokens, idempotency state, message journals, and radio identity were not cloned.
- [ ] Repeat Stages 1–6 at the appropriate smoke-test depth.
- [ ] Record every intentional difference from Unit A.

Pass condition: Unit B independently completes the voice loop and MeshCore companion test.

## Stage 8 — Two-device acceptance demonstration

- [ ] Unit A creates a bounded, attributable payload.
- [ ] Envelope uses the agreed version and includes message ID, source, destination/audience, sequence/time, type, bounded body, TTL, and integrity metadata.
- [ ] Unit A sends it to Unit B through MeshCore.
- [ ] Unit B treats the payload as untrusted and validates size, version, expiry, source authorization, type, integrity, and duplicate state.
- [ ] Unit B rejects at least one deliberately malformed or duplicate test envelope before agent exposure.
- [ ] Unit B exposes the received result to its SAGE-facing flow.
- [ ] The UI makes source, delivery state, and failure visible.
- [ ] Unit B returns a bounded acknowledgement or reply.
- [ ] Repeat in the opposite direction.
- [ ] Record timing, encoded size, retries, duplicates, and correlated application/radio logs.

Pass condition: one successful validated exchange and acknowledgement in each direction, one safe rejection test, and logs correlating application, SAGE, and radio events.

## Stage 9 — Portable power

- [ ] Repeat cold boot on the UGREEN power bank.
- [ ] Run five voice interactions.
- [ ] Run the two-device exchange.
- [ ] Record starting/ending state of charge, elapsed time, resets, cable, and selected output port.
- [ ] Run a longer representative workload and record observed runtime.

Pass condition: the complete beta demonstration runs without a power-related reset.

## Stage 10 — Enclosure inputs

Measure only after the functional stack is stable:

- [ ] component length, width, and thickness;
- [ ] connector and cable keep-out volumes;
- [ ] cooler airflow inlet/outlet needs;
- [ ] antenna keep-out and orientation;
- [ ] mic ports and speaker cavity needs;
- [ ] display active area and mounting points;
- [ ] PTT travel and encoder/wheel clearance;
- [ ] battery removal/service path;
- [ ] assembled mass and center-of-gravity observations.

Output: an enclosure requirements drawing/table, followed by the first CAD concept.

## Known defects and blockers

| ID | Unit | Description | Severity | Owner | Status | Evidence |
|---|---|---|---|---|---|---|
| — | — | None recorded yet. | — | — | — | — |
