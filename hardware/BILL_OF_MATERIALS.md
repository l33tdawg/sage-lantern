# Lantern bill of materials

This is a public summary, not a purchasing list. The hardware workstream holds
the live C-F BOM and build status. Prices and supplier details are left out
unless they are already public.

Status terms: **owned** means already in hand, **bought** means paid for but not
necessarily inspected, **decided** means the choice is made but the part may
not be ordered, **pending** means still unresolved, and **unverified** means the
part is present but has not passed its test.

## A and B: Lamplighter units

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 2 | Radxa ROCK 5B, 8 GB | Owned, unverified in this record | Base model, not the 5B+. |
| 2 | Radxa Heatsink 6240B V2.0 | Owned, unverified | Confirm the V2.0 marking and fit. |
| 2 | Netac 512 GB M.2 2280 NVMe | Owned, unverified | Record the exact model and firmware at bring-up. |
| 2 | Lenovo-compatible 65 W USB-C PD supply | Owned, unverified | Bench power only. |
| 2 | UGREEN 130 W, 20,000 mAh USB-C power bank | Owned, unverified | Bench power only. |
| 2 | Waveshare ESP32-S3-AUDIO-Board | Ordered, receipt not confirmed in the dated BOM | Dual microphone, AEC and speaker frontend. |
| 2 | Waveshare 3.5-inch HDMI LCD (E) | Owned, unverified | Dashboard screen. |
| 2 | Waveshare USB 3.2 Gen1 HUB HAT | Owned, unverified | Used as a cabled hub, not a Pi header stack. |
| 2 | Heltec WiFi LoRa 32 V4, ESP32-S3 and SX1262 | Owned, unverified | Single LoRa companion. Record the regional variant. |
| 2 | 16 mm momentary PTT button | Ordered, receipt not confirmed | Validate the action and wiring. |
| 2 | KY-040 rotary encoder | Ordered, receipt not confirmed | Final wheel may be printed. |
| 1 | Logitech C270 camera | Owned, single-frame capture tested | Prototype camera for Unit A. |
| assorted | Dupont leads, 6-pin cables, USB-A to USB-C data cables | Owned, unverified | Confirm data capability, not just charging. |

The A/B order history and receipt procedure remain in
[`HARDWARE_BOM.md`](../docs/lantern/HARDWARE_BOM.md).

## C, D, E and F: user Lanterns

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 4 | Orange Pi 5 Plus, 16 GB | Bought | 256 GB NVMe per unit. |
| 4 | 256 GB NVMe | Bought | Exact model to be recorded per unit. |
| 4 | Waveshare 6.25-inch 720x1560 HDMI panel with USB touch | Bought | Bench panel, not the final product panel. |
| 4 | Waveshare SX1262 LoRaWAN/GNSS HAT | Bought | Carries the L76K GNSS receiver. |
| 4 | GNSS antenna | Bought | Connected on C. The antenna is open and there is no fix yet. |
| 4 | LoRa antenna, 915 MHz | Bought | Confirm the regional band per unit. |
| 3 | Display or panel adapters | Bought, unverified | Three units were bought. A meter test is required before C runs heavy model loads. |
| 4 | IMX519 camera module | Ordered, not qualified | The Orange Pi CSI path is unverified. A USB camera remains the working prototype path. Final C-F camera choice is open. |
| 4 | Fixed 5 V/4 A power path | Pending per unit | Fixed supply, PD trigger with buck, or bench supply. |
| 4 | Audio capture path | Installed on C | ES8388 platform codec. Physical quiet and noisy capture acceptance remains open. |

The panel and camera research, including the connector problem, is in
[`CF_INTERNAL_PANEL_CAMERA_RESEARCH.md`](../docs/lantern/CF_INTERNAL_PANEL_CAMERA_RESEARCH.md).

## G and H: ROCK 5B+ units

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 2 | Radxa ROCK 5B+ 12 GB | Bought | Amazon order, owner completed. Build not started. |
| 2 | 6.25-inch 720x1560 panel | Decided | Retained from the earlier panel choice. |
| 2 | IMX415 camera | Decided, not confirmed bought | FPC and adapter path pending. |
| 2 | Panel adapter FPC | Pending | Connector pitch and pin count to be confirmed on the physical board. |
| 2 | Panel 5 V lead | Pending | Internal regulator path. |
| 2 | Camera FPC and adapter | Pending | Confirm against the ROCK 5B+ CSI path. |

The G/H power path can use a common USB-C PD charger. That is the reason the
ROCK board was chosen for those units.

## Still missing or unconfirmed

- A current, complete C-F BOM from the hardware workstream, including exact
  SKUs and quantities for adapters and cameras.
- The final enclosure parts, fasteners and print files.
- Meter results for the C-F power adapters.
- Physical connector confirmation for the panel and camera on each board
  revision.
- A real GNSS fix and antenna qualification.

Do not treat this table as a final purchasing decision. When the live BOM
arrives, it replaces this summary rather than sitting beside it as a second
source of truth.
