# Hardware reconciliation, 19 October 2026

This records what can be confirmed from the live unit and vendor documentation,
and what still needs a physical check. It does not freeze a harness or a final
bill of materials.

## Unit C, read live

Read over SSH on 19 October 2026:

| Item | Observation |
|---|---|
| Board | Orange Pi 5 Plus |
| CPU | 8 cores |
| Memory | 15 GiB visible |
| Root storage | Netac NVMe SSD 256 GB, single partition mounted at `/` |
| Audio | `Sage Commons SAGE Lantern Audio` on USB |
| Camera | Logitech C270 on USB |
| GNSS | `/dev/ttyS6` present |
| I2C | buses 0, 1, 3, 6, 7, 9, 10, 11 present |
| Display | three DRM connectors disconnected, one unknown |

The USB audio and C270 are the bench path. They do not qualify the I2S
microphone or the CSI camera for D-F.

## Board count

| Units | Board | Status |
|---|---|---|
| A and B | Radxa ROCK 5B, 8 GB | Owned and running |
| C | Orange Pi 5 Plus, 16 GB | Live and confirmed |
| D, E and F | Orange Pi 5 Plus, 16 GB | Procurement records conflate an earlier pair with A/B. Count the physical boards before saying four are present. |
| G and H | Radxa ROCK 5B+, 12 GB | Owner-reported arrived by 9 October. No electrical bring-up. |

## ROCK 5B+ DSI path

The Radxa hardware-interface page lists 41 numbered DSI positions for the
ROCK 5B/5B+ connector, including ground and backlight pins. The Radxa Display 8
HD accessory page separately specifies a 39-pin board-side to 40-pin panel-side
cable (AC012) for the 5B. The difference is not resolved by documentation
alone, which is why earlier notes disagree between 39, 40 and 41.

The important mismatch is the panel, not the count. AC012 is a 39-to-40 cable
for the Radxa Display 8 HD. The Waveshare 6.25-inch DSI panel is a 22-pin
(Pi 5) or 15-pin (Pi 4 class) panel. AC012 does not connect to it.

Before ordering or printing a panel mount:

1. Count the contacts on the actual ROCK 5B+ DSI connector.
2. Confirm the Waveshare panel's fitted FPC width, pitch and contact count.
3. Identify or design a 39/41-pin to 22-pin adapter with the correct lane
   mapping, backlight and touch signals.
4. Confirm the panel's 5 V/GND lead and the touch I2C bus.
5. Bring up the panel and touch on one G/H board before the other.

Radxa sources:

- [ROCK 5B/5B+ hardware interface](https://docs.radxa.com/en/rock5/rock5b/hardware-design/hardware-interface)
- [Radxa Display 8 HD usage and AC012](https://docs.radxa.com/en/accessories/display/lcd-8-hd/lcd-8-hd-usage)

## Orange Pi 5 Plus CSI path

The Orange Pi 5 Plus has one 4-lane MIPI CSI connector. Third-party adapter
documentation describes it as a 30-pin board-to-board mezzanine, not the 15-pin
Raspberry Pi camera connector in the older note. The vendor wiki confirms the
4-lane CSI interface but does not publish a contact count on the pages checked.

The four IMX519 modules are 22-pin Raspberry Pi-style cameras. A passive ribbon
is not enough. The adapter must remap the 30-pin Orange Pi CSI signals to the
22-pin camera pinout and level-shift the camera I2C from 1.8 V to 3.3 V. A
third-party adapter has been bench-tested with IMX519 on Orange Pi 5 boards, but
that is vendor evidence, not a result on this unit.

Before the camera is claimed working:

1. Identify the exact CSI connector on the physical Orange Pi 5 Plus.
2. Obtain the 30-pin to 22-pin adapter with I2C level shifting.
3. Verify the IMX519 ribbon orientation.
4. Confirm the kernel driver and device-tree overlay for IMX519 on the
   installed Armbian kernel.
5. Capture a still image and a short stream on C before repeating on D-F.

Sources:

- [Orange Pi 5 Plus product page](https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-5-plus.html)
- [Orange Pi 5 Plus wiki](https://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_5_Plus)
- [Tachyn Labs Orange Pi 5 camera adapter](https://tachynlabs.com/product/orange-pi-5-camera-adapter/)

## G/H bring-up checklist

1. Photograph and inventory both ROCK 5B+ boards and heatsinks.
2. Confirm the delivered board revision and the 40-pin, CSI and DSI connectors.
3. Boot from a known image over serial or HDMI before fitting the panel.
4. Fit the panel adapter and confirm backlight, lanes and touch on one board.
5. Fit the IMX415 camera and confirm capture with the vendor overlay.
6. Fit the radio HAT and confirm the 40-pin mapping.
7. Measure idle and loaded power with the intended supply.
8. Only then repeat on the second board.

## Open items

- Physical contact count on the ROCK 5B+ DSI connector.
- Correct adapter for the Waveshare 22-pin DSI panel.
- 30-pin to 22-pin CSI adapter with I2C level shifting.
- IMX519 driver and overlay on the installed Orange Pi kernel.
- Physical count of the Orange Pi boards.
- G/H electrical bring-up and thermal measurements.
