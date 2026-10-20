# Hardware reconciliation, 19 October 2026

This records what can be confirmed from the live unit and vendor documentation,
and what still needs a physical check. It does not freeze a harness or a final
bill of materials.

## Unit C, read live

Read over SSH on 19 October 2026:

The repeatable collector is
[`scripts/lantern/hardware_inventory.py`](../scripts/lantern/hardware_inventory.py).
It reads procfs and sysfs only and omits serial numbers by default. The Unit C
output is saved as
[`docs/lantern/evidence/HARDWARE_INVENTORY_C_20261019.json`](../docs/lantern/evidence/HARDWARE_INVENTORY_C_20261019.json).

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
| USB inventory | two Genesys hubs, the SAGE Lantern Audio device and a Logitech C270; the remaining entries are host controllers |

The USB audio and C270 are the bench path. They do not qualify the I2S
microphone or the CSI camera for the other Orange Pi bench unit or the ROCK
product fleet.

Run the collector on each new unit before fitting peripherals:

```sh
python3 scripts/lantern/hardware_inventory.py > evidence/<unit>-inventory.json
```

Keep serial numbers out of public evidence. Use `--include-serials` only for a
private unit record.

## Board count

| Units | Board | Status |
|---|---|---|
| A and B | Radxa ROCK 5B, 8 GB | Owned and running |
| C | Orange Pi 5 Plus, 16 GB | Live and confirmed |
| Orange Pi bench | Orange Pi 5 Plus, 16 GB | D-028 retains two bench/development units. Procurement records conflate an earlier pair with A/B, so count the physical boards. |
| ROCK product fleet | Radxa ROCK 5B+, two 12 GB and two 16 GB | The 12 GB pair is in hand; the 16 GB pair was ordered on 19 October. No electrical bring-up. |

## ROCK 5B+ DSI path

The Radxa hardware-interface page lists 41 numbered DSI positions for the
ROCK 5B/5B+ connector, including ground and backlight pins. The Radxa Display 8
HD accessory page separately specifies a 39-pin board-side to 40-pin panel-side
cable (AC012) for the 5B. The difference is not resolved by documentation
alone, which is why earlier notes disagree between 39, 40 and 41.

The important mismatch is the panel, not the count. AC012 is a 39-to-40 cable
for the Radxa Display 8 HD and does not connect to the Waveshare panel. The
selected path is four **AC005** adapters (39-pin 0.3 mm to 15-pin 1.0 mm),
ordered from RS Malaysia with the two additional coolers.

Before installing the panel or printing its final mount:

1. Count the contacts on the actual ROCK 5B+ DSI connector.
2. Confirm the Waveshare panel's fitted FPC width, pitch and contact count.
3. Verify the AC005 contact, pitch, lane, backlight and touch mapping on the
   physical connector.
4. Confirm the panel's 5 V/GND lead and the touch I2C bus.
5. Bring up the panel and touch on one ROCK board before the others.

Radxa sources:

- [ROCK 5B/5B+ hardware interface](https://docs.radxa.com/en/rock5/rock5b/hardware-design/hardware-interface)
- [Radxa Display 8 HD usage and AC012](https://docs.radxa.com/en/accessories/display/lcd-8-hd/lcd-8-hd-usage)

## Orange Pi 5 Plus CSI path

The Orange Pi 5 Plus has one 4-lane MIPI CSI connector. Third-party adapter
documentation describes it as a 30-pin board-to-board mezzanine, not the 15-pin
Raspberry Pi camera connector in the older note. The vendor wiki confirms the
4-lane CSI interface but does not publish a contact count on the pages checked.

The four IMX519 modules are 22-pin Raspberry Pi-style cameras retained for the
Orange Pi bench experiment. They are not the ROCK product camera; four IMX415
modules were ordered for that path. A passive ribbon is not enough. A bench
Orange Pi adapter must remap the 30-pin CSI signals to the 22-pin camera pinout
and level-shift the camera I2C from 1.8 V to 3.3 V. A third-party adapter has
been bench-tested with IMX519 on Orange Pi 5 boards, but that is vendor
evidence, not a result on this unit.

Before the camera is claimed working:

1. Identify the exact CSI connector on the physical Orange Pi 5 Plus.
2. Obtain the 30-pin to 22-pin adapter with I2C level shifting.
3. Verify the IMX519 ribbon orientation.
4. Confirm the kernel driver and device-tree overlay for IMX519 on the
   installed Armbian kernel.
5. Capture a still image and a short stream on C before repeating on the other
   Orange Pi bench unit.

Sources:

- [Orange Pi 5 Plus product page](https://www.orangepi.org/html/hardWare/computerAndMicrocontrollers/details/Orange-Pi-5-plus.html)
- [Orange Pi 5 Plus wiki](https://www.orangepi.org/orangepiwiki/index.php/Orange_Pi_5_Plus)
- [Tachyn Labs Orange Pi 5 camera adapter](https://tachynlabs.com/product/orange-pi-5-camera-adapter/)

## Four-ROCK bring-up checklist

1. Photograph and inventory the four ROCK 5B+ boards and heatsinks as they
   arrive.
2. Confirm the delivered board revision and the 40-pin, CSI and DSI connectors.
3. Boot from a known image over serial or HDMI before fitting the panel.
4. Fit the panel adapter and confirm backlight, lanes and touch on one board.
5. Fit the IMX415 camera and confirm capture with the vendor overlay.
6. Fit the radio HAT and confirm the 40-pin mapping.
7. Measure idle and loaded power with the intended supply.
8. Only then repeat on the second board.

## Open items

- Physical contact count on the ROCK 5B+ DSI connector.
- Physical AC005/DSI connector, lane and touch check.
- 30-pin to 22-pin CSI adapter with I2C level shifting.
- IMX519 driver and overlay on the installed Orange Pi kernel.
- Physical count of the Orange Pi boards.
- Four-ROCK electrical bring-up, panel/touch and thermal measurements.
