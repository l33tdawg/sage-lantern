# Lantern hardware

This folder is the public starting point for the Lantern hardware programme. It
describes the device roles, the parts that are settled, and the work that is
still open. It is not a claim that the fleet has been built.

The repository's [`HARDWARE_BOM.md`](../docs/lantern/HARDWARE_BOM.md) is a dated
record of the A/B bring-up and early orders. The hardware workstream keeps the
live C-F bill of materials and build status in its own working records. The
summary here is compiled from the repository docs and marks anything it cannot
confirm.

## Device roles

| Units | Role | Hardware target | Current state |
| --- | --- | --- | --- |
| **A and B** | Lamplighter, administration, public knowledge cache and repeater support | Radxa ROCK 5B, 8 GB, one LoRa companion, dashboard screen | Built and running. Public pack seeds and the Lamplighter dashboard are installed. Private personal SAGE stores were removed with owner approval. |
| **C, D, E and F** | Bench and acceptance units for the end-user Lantern | Orange Pi 5 Plus, 16 GB, 256 GB NVMe, HDMI 6.25-inch 720x1560 panel with USB touch, SX1262/GNSS HAT | C is commissioned and running. D/E/F first boot and peripheral acceptance remain open. The HDMI panel is bench hardware, not the final product panel. |
| **G and H** | Additional end-user Lanterns | Radxa ROCK 5B+ 12 GB, 6.25-inch panel, IMX415 camera | Two boards bought. Build has not started. C-F qualification comes first. |

The role map is maintained in [`DEVICE_ROLES.md`](../docs/lantern/DEVICE_ROLES.md).
It controls allocation when older notes disagree.

## What is settled

- C-F use the Orange Pi 5 Plus with 16 GB RAM and a 256 GB NVMe.
- C-F need a fixed 5 V/4 A power path. The Orange Pi Type-C input does not
  negotiate USB-C PD.
- G/H use the ROCK 5B+ and can take a common PD charger. Power is
  bring-your-own under D-015.
- The 6.25-inch 720x1560 panel is the current bench panel for C-F and the
  retained panel choice for G/H. The final G/H product path is the ribbon DSI
  panel.
- The radio baseline is one LoRa radio per unit. A second radio is an optional
  comparison, not a requirement before testing.
- The GNSS receiver is the L76K on the Waveshare SX1262/GNSS HAT. Its UART is
  UART6_M1 on header pins 8 and 10. UART2 is the debug console.

## What is still open

- First-article prints and a measured tolerance report. Nothing has been
  printed yet, so there are no STL files to publish. See [`stl/`](stl/).
- The C-F panel adapter, the panel's 5 V lead and the camera FPC path.
- The C-F camera path. Four IMX519 modules were ordered, but the Orange Pi CSI
  path is unverified and the final choice is still open.
- The G/H camera modules and adapters. IMX415 is the decided sensor there.
- Physical confirmation of connector pitch and pin count on the real boards.
- Meter tests of the C-F power adapters before any unit runs heavy model loads.
- A real GNSS fix, touch mapping, panel rotation and sleep/wake acceptance on
  the physical unit.

## Safety and serviceability

- Do not feed a PD charger directly into the Orange Pi 5 Plus Type-C port and
  expect negotiation. Use a fixed 5 V/4 A supply, a PD trigger with a suitable
  buck, or a bench supply at 5.1 V/5 A.
- Check polarity and voltage before connecting the panel, audio board or radio.
- Do not connect a speaker until the board revision, connector polarity and
  speaker impedance are confirmed.
- The enclosure is meant to be opened. Keep fasteners, connectors and the
  storage device reachable.
- Treat every measurement as unit-specific until it is repeated on the second
  board profile.

## Related documents

- [Bill of materials](BILL_OF_MATERIALS.md)
- [Printable parts](stl/README.md)
- [Build guide](../docs/build/README.md)
- [Bring-up and validation](../docs/lantern/BRINGUP_AND_VALIDATION.md)
- [Internal panel and camera research](../docs/lantern/CF_INTERNAL_PANEL_CAMERA_RESEARCH.md)
- [Compute requirement](../docs/lantern/FINAL_COMPUTE_REQUIREMENT.md)
- [Hardware and software contract](../docs/lantern/HARDWARE_SOFTWARE_CONTRACT.md)
