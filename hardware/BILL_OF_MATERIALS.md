# Lantern bill of materials

This is the public status summary as of 19 October 2026. It comes from the
hardware workstream's build records. Those records are more recent than
[`HARDWARE_BOM.md`](../docs/lantern/HARDWARE_BOM.md), which remains the A/B
bring-up history.

No prices, supplier contacts or order-account details are published. A status
of "arrived" means the owner reported arrival; it is not an electrical or
mechanical qualification.

Status terms: **owned** means in hand, **ordered** means paid but not confirmed
received, **selected** means the choice is made but purchasing is not confirmed,
and **unknown** means the records do not settle it.

## Compute

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 2 | Radxa ROCK 5B, 8 GB | Owned | The A/B test boards. The earlier note that A/B were Orange Pi was corrected. |
| 4 | Orange Pi 5 Plus, 16 GB | Planned | Two extra boards were ordered in September, but the records conflate the original pair with A/B. Count the physical boards before describing four as received. |
| 2 | Radxa ROCK 5B+, 12 GB | Ordered 30 September, owner-reported arrived by 9 October | Electrical bring-up and qualification are not established. |

## Display

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 4 | Waveshare 6.25-inch HDMI capacitive touch panel, 720x1560, SKU 26757 | In hand | The C-F bench panel. It is not the final product panel. |
| 2 | Waveshare 6.25-inch DSI LCD (B), SKU 35000 | Ordered | G/H panel. Last merchant state was awaiting shipment on 30 September. Arrival and working display are unknown. |
| 2 | Radxa FPC adapter cable AC012 | Identified, likely wrong for this panel | AC012 is a 39-pin to 40-pin cable for the Radxa Display 8 HD. The Waveshare 6.25-inch DSI panel is 22-pin or 15-pin. The correct adapter is unresolved. |
| 2 | 15-pin 1.0 mm FPC and PH2.0 four-pin power lead, about 200 mm | Part of the panel package | The recorded plan uses the AC012 DSI path and the included lead to header 5 V/GND. This is a proposed route, not a proven harness. |

## Radio and GNSS

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 4 | Waveshare SX1262 LoRaWAN HAT, 915 MHz and GNSS, SKU 24654 | In hand | Count and unit assignment still want a bench check. |
| 6 | Seeed XIAO ESP32S3 with Wio-SX1262 Meshtastic kit, MPN 102010611 | Ordered | Covers C-H. Receipt, flashing and in-shell RF qualification are unknown. |
| 2 | Heltec LoRa 32 V4 bench modules | Owned | Older bench inventory. The six-unit Heltec order was cancelled. Do not treat the Heltec pin map as qualified for the XIAO. |
| 4 | 915 MHz SMA-male whip antenna | Owned | Confirm fit and connector reach. |
| 1 | TAN1216Q23 GNSS SMA-male helix antenna | Owned | Three more GNSS antennas are unresolved. Active, passive and bias matching still needs confirmation. |

## Camera

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 4 | 16 MP IMX519 camera module | Bought, last recorded in transit | The Orange Pi CSI connector is a 30-pin mezzanine, and IMX519 is a 22-pin camera. A 30-to-22 adapter with I2C level shifting is required. Receipt and integration are unknown. |
| 1 per G/H unit | Radxa Camera 4K, IMX415 | Selected | Conditional on a real ROCK 5B+ bring-up. Quantity ordered or received is unknown. |

A selected camera or a kernel overlay reference is not proof of capture.

## Audio and controls

| Qty | Part | Status | Notes |
|---:|---|---|---|
| 4 | DIYMORE ICS-43434 I2S microphone module, 16.5 x 12.3 x 1.6 mm | Ordered | Arrival and capture qualification are unknown. |
| 10 | RS PRO PAS6D1M1CES4-5 push-button switch, stock 2099089 | Ordered 1 October | Confirmed as PTT switches. Delivery count and debounce qualification are unknown. |
| 0 | Speaker | Not specified | Do not fit a generic speaker until the audio path is confirmed. |
| 0 | Rotary encoder | Unknown | Model and procurement are unresolved. |

The selected microphone mechanical approach is an internal sealed can with a
short acoustic tube. The generator currently has `MIC_FITTED=False`. Do not
describe the old bezel chamber as fitted hardware.

## Power, cooling and storage

| Qty | Part | Status | Notes |
|---:|---|---|---|
| Product power | External USB-C, bring your own | Direction | The Orange Pi needs a fixed 5 V at 4 A or more. No battery or charge pigtail ships. |
| 3 | Fixed 5 V bench adapter for C-F | Paid and ordered, last state To Ship | Two are WNB-0504000 5 V/4 A. The third model needs confirmation. Receipt and load tests are unknown. |
| 4 | Nitecore NB10000 Gen 2 | Historical bench stock | 5 V/3 A, external only. Not a qualified 20 W product supply. |
| 2 | Radxa heatsink 6240B, MPN AE013B | Ordered 9 October | Delivered revision and mounted thermal clearance are unknown. |
| 4 | Aluminium case and 30 mm fan kit | Receipt-confirmed stock | Compatibility with the later housing revision must be checked. |
| 4 | Netac 256 GB NVMe, M.2 2280 | Owned | C-F storage. Exact series and installed state are unknown. |
| 2 | Netac 512 GB NVMe, M.2 2280 | Owned | A/B bench inventory. |

## Antennas, cables and small parts

- Four 15 cm left-to-left HDMI cables, one 20 cm flat HDMI fit-test cable,
  right-angle adapters and short USB-A to USB-C leads are in stock.
- Existing cable stock is not proof of the final plug direction, service loop
  or internal panel route.
- Heat inserts, screws and nuts are recorded in older receipt records. The old
  screw-fastened bezel quantities are not the current snap-bezel BOM.

## Printable parts

The mechanical workstream has real printable files and print results. They are
not copied into this repository yet. The list and status are in
[`stl/README.md`](stl/README.md).

## Builder gates still open

- Count the actual ROCK DSI connector. The records disagree between 39, 40 and
  41 pins. Verify the AC012 contacts, pitch, lane route and the panel overlay.
- Identify the Orange Pi CSI connector and net map. The earlier 15-pin
  Raspberry Pi assumption was withdrawn.
- Bring up the IMX415 on the actual ROCK 5B+ with the matching ribbon and
  overlay, and demonstrate capture.
- Verify the primary HAT 40-pin mapping, the 2x20 female ribbon, pigtail reach,
  GNSS bias and XIAO-specific firmware.
- Measure the PTT face, thickness, terminals and wires. The current 20 x 20 x
  2.5 mm face is an assumption.
- Verify cooler, NVMe, plug and cable clearances, board retention, bezel
  closure and loaded stand stability.

None of this establishes a finished product, a runtime figure, a thermal limit
or a field result.
