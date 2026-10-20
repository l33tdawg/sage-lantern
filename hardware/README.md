# Lantern hardware

This folder is the public starting point for the Lantern hardware programme. It
describes the device roles, the parts that are settled, and the work that is
still open. It is not a claim that the fleet has been built.

The repository's [`HARDWARE_BOM.md`](../docs/lantern/HARDWARE_BOM.md) is a dated
record of the A/B bring-up and early orders. The hardware workstream keeps the
current fleet bill of materials and build status in its own working records.
The summary here is compiled from the repository docs and marks anything it
cannot confirm.

## Device roles

| Units | Role | Hardware target | Current state |
| --- | --- | --- | --- |
| **A and B** | Lamplighter, administration, public knowledge cache and repeater support | Radxa ROCK 5B, 8 GB, one LoRa companion, dashboard screen | Built and running. Public pack seeds and the Lamplighter dashboard are installed. Private personal SAGE stores were removed with owner approval. |
| **Orange Pi 5 Plus bench units** | Bench and acceptance work for the end-user Lantern | Orange Pi 5 Plus, 16 GB, 256 GB NVMe, HDMI 6.25-inch 720x1560 panel with USB touch, SX1262/GNSS HAT | Two units are retained under D-028; C is commissioned and running. The earlier C–F four-unit label remains historical until the physical count is reconciled. The HDMI panel is bench hardware, not the product panel. |
| **ROCK 5B+ product fleet (four units)** | Main end-user Lantern fleet | Four ROCK 5B+ boards: two 12 GB in hand and two 16 GB ordered. Two 6.25-inch DSI panels in hand and two ordered; four AC005 adapters and four IMX415 cameras ordered. | Board and panel bring-up has not started. DSI overlay, camera, thermal, power and physical acceptance remain open. |

The role map is maintained in [`DEVICE_ROLES.md`](../docs/lantern/DEVICE_ROLES.md).
It controls allocation when older notes disagree.

## What is settled

- D-028 sets two Orange Pi 5 Plus bench/development units. The procurement
  records conflate an earlier pair with A/B, so count the physical boards
  before claiming a larger bench fleet.
- The Orange Pi bench units need a fixed 5 V/4 A power path. The Orange Pi
  Type-C input does not negotiate USB-C PD.
- The four ROCK 5B+ product units can take a common PD charger. Power is
  bring-your-own under D-015.
- The 6.25-inch 720x1560 HDMI panel is bench-only. The ROCK product path uses
  the 6.25-inch DSI panel with the AC005 adapter.
- The product radio baseline is one LoRa radio per unit. Meshticulum carries
  messages and resource transfers over that single half-duplex radio. Extra
  radios are bench comparison inventory, not a product requirement.
- The GNSS receiver is the L76K on the Waveshare SX1262/GNSS HAT. Its UART is
  UART6_M1 on header pins 8 and 10. UART2 is the debug console.

## What is still open

- First-article prints and a measured tolerance report for the remaining parts.
  The ROCK 5B+ tub, firmer bezel and back cover have been printed and accepted
  as mechanical trials. The gasket and V2 collar trials are still open. See
  [`stl/`](stl/README.md).
- Physical connector/pin and lane checks for the AC005 DSI path, the panel
  power lead, the DSI overlay and the camera FPC path.
- The Orange Pi camera experiment. Four IMX519 modules were bought, but the
  Orange Pi CSI path remains unverified and is not the product camera path.
- ROCK 5B+ camera capture with the ordered IMX415 modules and the matching
  ribbon and overlay.
- The microphone, PTT switch and acoustic path. The I2S modules are ordered,
  the PTT switches are ordered, and the current generator does not have the
  microphone fitted.
- Physical confirmation of connector pitch and pin count on the real boards.
- Meter tests of the Orange Pi bench power adapters before any unit runs heavy
  model loads.
- A real GNSS fix, touch mapping, panel rotation and sleep/wake acceptance on
  the physical unit.

## Safety and serviceability

- Do not feed a PD charger directly into the Orange Pi 5 Plus Type-C port and
  expect negotiation. Use a fixed 5 V/4 A supply, a PD trigger with a suitable
  buck, or a bench supply at 5.1 V/5 A.
- Check polarity and voltage before connecting the panel, audio board or radio.
- Do not connect a speaker until the board revision, connector polarity and
  speaker impedance are confirmed.
- The product ships without a battery or charging pigtail. Power is an external
  USB-C supply chosen by the owner.
- The enclosure is meant to be opened. Keep fasteners, connectors and the
  storage device reachable.
- Treat every measurement as unit-specific until it is repeated on the second
  board profile.

## Related documents

- [Bill of materials](BILL_OF_MATERIALS.md)
- [Printable parts](stl/README.md)
- [Hardware reconciliation, 19 October 2026](RECONCILIATION_20261019.md)
- [Physical verification checklist](PHYSICAL_CHECKLIST.md)
- [Build guide](../docs/build/README.md)
- [Bring-up and validation](../docs/lantern/BRINGUP_AND_VALIDATION.md)
- [Internal panel and camera research](../docs/lantern/CF_INTERNAL_PANEL_CAMERA_RESEARCH.md)
- [Compute requirement](../docs/lantern/FINAL_COMPUTE_REQUIREMENT.md)
- [Hardware and software contract](../docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md)
