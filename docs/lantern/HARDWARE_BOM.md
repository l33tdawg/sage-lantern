# SAGE Lantern Hardware Bill of Materials and Arrival Status

**Current device use cases (2026-09-27):** A/B are single-LoRa Lamplighter/admin/knowledge/repeater support units; C–F are real Orange Pi 5 Plus user Lanterns with one LoRa baseline radio, GPS and WiFi. Earlier dual-radio notes are historical bench work. [Device roles](DEVICE_ROLES.md) controls allocation; this dated BOM remains historical.

> **Superseded for the fleet build (2026-09-21).** This file records ordering and
> arrival state as of 11 September 2026 as seen from this repository. It mixes
> the A/B test-unit build with the early C/D orders and is **not** the live source
> of truth for the enclosure programme. The hardware workstream owns the C–F bill
> of materials and build status in its own `BOM.md` and `BUILD_STATUS.md`, and
> that record supersedes this one for the fleet. Kept here as the A/B bring-up
> record and order history.

**Status date:** 2026-09-11 (exact 6.25-inch display and SX1262 board orders; CARBO 20 identified)  
**Build quantity:** Two beta units  
**Rule:** `Received` means physically reported/confirmed, not merely ordered.

## New enclosure hardware — latest owner confirmation, 2026-09-11

| Qty | Component | Status | Notes |
|---:|---|---|---|
| 4 | 6.25-inch capacitive touch display, 720×1560, optical bonding toughened glass, HDMI, IPS, 5-point touch | Ordered; awaiting delivery | Exact pasted order description supersedes the earlier approximate 6.5-inch wording. Matches Waveshare 6.25inch 720x1560 LCD, SKU 26757. Earlier delivery estimate: next week. |
| 4 | SX1262 LoRaWAN Node Module Expansion Board for Raspberry Pi, with magnetic CB antenna, frequency-band/GNSS options | Ordered; awaiting delivery | Matches Waveshare SX1262 LoRaWAN/GNSS HAT family. Selected frequency band and basic/GNSS option not supplied. Separate from A/B's Heltec USB MeshCore companions. |
| Not stated | NITECORE NB10000 Gen 2, 10,000 mAh USB-C power bank | Owner confirmed 2026-09-29 | **Corrected 2026-09-29: the owner confirms the NB10000 Gen 2.** The earlier "CARBO 20 / 20,000 mAh / 65 W" identification in this row was wrong. Recorded output: 5 V/3 A on USB-A (15 W); USB-C PD is the same ~15–18 W class. Light-duty only — it cannot supply the Orange Pi 5 Plus's 5 V/4 A (20 W), with or without a buck. The 121.9 × 59 × 10.6 mm pack envelope used by the enclosure work matches this pack. |

These additions do not imply four complete electronics sets or four purchased
battery packs. Existing A/B inventory remains below. The old exclusion of raw
SX1262 Raspberry Pi HATs is superseded for the newly confirmed order; SPI/GPIO
host integration and radio software compatibility remain to be qualified.

**Power correction 2026-09-29:** the Lenovo-compatible 65 W USB-C PD PSUs below
are **A/B (Radxa) bench supplies only**. Orange Pi 5 Plus (C–F) power does not
negotiate PD — its Type-C power port is a fixed 5 V input — so C–F need a
5 V/4 A path: a fixed 5 V/4 A USB-C supply, or a 12/20 V PD source (a Lenovo/MacBook
brick — add a PD trigger unless the buck negotiates — or the UGREEN 130 W/20,000 mAh
pack) through a 5 V/≥4 A buck converter, or
a bench PSU at 5.1 V/5 A (power port or 40-pin 5 V pins, vendor wiki §2.17).
See the D-006 amendment in [DECISIONS.md](DECISIONS.md) and
[C_RESETS_20260929.md](../../work/unit-c-boot-20260928/C_RESETS_20260929.md).
The hardware workstream's own `BOM.md` / `BUILD_STATUS.md` remains the live C–F
record.

Primary references checked 2026-09-11: [display](https://www.waveshare.com/product/6.25inch-720x1560-lcd.htm), [display dimensions](https://www.waveshare.com/wiki/6.25inch_720x1560_LCD), [radio options](https://www.waveshare.com/product/raspberry-pi/sx1262-lorawan-hat.htm), [CARBO 20](https://www.nitecore.com/product/carbo20).
Full corrected handoff: [hardware list](../../work/hardware-list-ableman-20260911/HARDWARE_LIST.md).

## Core components

| Qty | Component | Intended allocation | Status | Notes |
|---:|---|---|---|---|
| 2 | Radxa ROCK 5B+ | One per unit | Received — untested | Already owned before this procurement cycle; receipt reconfirmed 2026-08-24. |
| 2 | Radxa Heatsink 6240B V2.0 | One per unit | Received — untested | Confirm V2.0 marking/fit. |
| 2 | 512 GB Netac M.2 2280 NVMe SSD | One per unit | Received — untested | Record exact model and firmware during bring-up. |
| 2 | Lenovo-compatible 65 W USB-C PD PSU, 20 V/3.25 A | One per unit | Received — untested | Bench power. Verify cable and PD negotiation before use. |
| 2 | UGREEN 130 W, 20,000 mAh USB-C PD power bank | One per unit | Received — untested | Receipt reconfirmed 2026-08-24. |

## Interaction and peripheral components

| Qty | Component | Intended allocation | Status | Notes |
|---:|---|---|---|---|
| 2 | Waveshare ESP32-S3-AUDIO-Board | One per unit | Ordered; receipt unconfirmed | Dual-mic/AEC/speaker frontend. Confirm included accessories and host interface. |
| 2 | Waveshare 3.5-inch HDMI LCD (E) | One per unit | Received — untested | Capacitive touch. Inventory included video/touch cables before bring-up. |
| 2 | Waveshare USB 3.2 Gen1 HUB HAT | One per unit | Received — untested | Use as cabled USB hub; do not assume Pi-header compatibility. |
| 2 | Heltec WiFi LoRa 32 V4, ESP32-S3 + SX1262 | One per unit | Received — untested | MeshCore companion role. Record exact regional variant. |
| 2 | 16 mm, 2-pin, momentary, no-LED PTT button | One per unit | Ordered; receipt unconfirmed | Validate mechanical action and GPIO wiring. |
| 2 | KY-040 rotary encoder with push-click | One per unit | Ordered; receipt unconfirmed | Final R1-style wheel can be printed for the shaft. |
| 1 | Rabbit R1 reference/teardown unit | Shared | Delayed | Mechanical and interaction reference only; reported delayed 2026-08-24. |

## Camera prototype addition — 2026-09-05

| Qty | Component | Intended allocation | Status | Notes |
|---:|---|---|---|---|
| 1 | Logitech Webcam C270, USB `046d:0825` | Unit A photo-and-question prototype | Received — single-frame capture tested | Existing camera connected 2026-09-05. Operator-authorized V4L2 MJPEG capture produced a 37,590-byte JPEG with 1280×720 header. Full decode, focus/image quality, USB load, reconnect and vision inference remain untested. |

Do not duplicate this camera for Units 3/4 until the camera-to-answer workflow
and enclosure fit are validated. Reserve camera placement in the
[enclosure study](UNIT_3_4_ENCLOSURE_STUDY.md); final module, dimensions, cable,
cost and required accessories remain TBD. A camera's optional microphone must
not displace the working voice capture device automatically.

## Wiring and cables

| Qty | Component | Status | Notes |
|---:|---|---|---|
| 1 pack | 40-wire male-to-female Dupont, 10 cm | Received — untested | Shared integration stock. |
| 1 pack | 40-wire female-to-female Dupont, 10 cm | Received — untested | Shared integration stock. |
| 1 pack | 40-wire male-to-male Dupont | Received — untested | Shared integration stock. |
| 4 | 6-pin female-to-female 2.54 mm cable assemblies | Received — untested | Two spares beyond one per unit. |
| 4 | USB-A to USB-C data cables | Received — untested | Confirm data capability; do not assume from charging alone. |

## Explicitly excluded or deferred

- Earlier raw SX1262 HAT exclusion applied to the A/B baseline; superseded by the four new LoRaWAN expansion-board orders above.
- Generic JST assortment — deferred until exact connector families are identified.
- Separate rotary knobs — deferred in favor of an eventual custom wheel.
- Separate speaker — not a distinct procurement item in the current audio-board plan.
- Logic analyser and USB-TTL adapter — earlier suggestions, not part of the final confirmed purchase baseline.
- PalChat/WV01 modules — superseded by the Waveshare audio-board decision.

## Receipt procedure

For each delivery:

1. Photograph the shipping label and all contents.
2. Match the exact model and variant against this BOM.
3. Record serial number or board revision where present.
4. Record included cables, antennas, fasteners, and adapters.
5. Inspect for damage or bent headers.
6. Change status to `Received — untested`.
7. After a basic power/enumeration test, change status to `Received — tested` and link the evidence in `BRINGUP_AND_VALIDATION.md`.

## Status vocabulary

- **Planned — not ordered/received** — intended test item; exact selection and procurement remain unconfirmed.
- **Ordered; receipt unconfirmed** — purchased but not physically confirmed.
- **Delayed** — ordered but delivery has been delayed; revised arrival date may be unknown.
- **Received — untested** — physically present but not electrically tested.
- **Received — tested** — passed its defined smoke test.
- **Blocked** — cannot be tested because a dependency is missing.
- **Rejected/RMA** — wrong, damaged, or failed component.
