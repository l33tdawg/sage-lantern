# ESP-NOW companion radio for the Heltec V4 (Lantern's radio board)

Status 2026-09-13: **builds, not flashed, not measured.**

## What it is

MeshCore's companion radio with `ESPNOWRadio` as the mesh transport instead of
the SX1262. Same host protocol over USB, same mesh protocol, same push codes and
same ACK behaviour - only the physical layer changes, from LoRa on 917.375 MHz to
802.11-derived ESP-NOW on 2.4 GHz.

## Why

Lantern's confirmed throughput on LoRa is 89 B/s at the commissioned profile and
410 B/s at SF7/BW500 (measured 2026-09-13). That is 3.3 kbps at best: fine for
messages, hopeless for pages, attachments or anything desktop-mode.

Transmit power does not fix it - power buys range, not rate, and the board's PA
is already at ~22 dBm. Rate comes from bandwidth and modulation. ESP-NOW is the
cheapest large step available because it is **already in the firmware twice
over**:

* `variants/generic_espnow/` - ESP-NOW as the mesh radio, with companion builds.
* `src/helpers/bridges/ESPNowBridge.*` - mesh packets over ESP-NOW with LoRa
  retained, enabled for heltec_v4 (`-D WITH_ESPNOW_BRIDGE=1`).

This variant is the first of those, ported to our actual board. Expected:
GPRS-class or better (hundreds of kbps) over a room-and-garden distance, against
3.3 kbps today. **Unmeasured - do not quote a number until it is run.**

## Build

Drop this directory into a MeshCore checkout as `variants/heltec_v4_espnow/` and:

```
pio run -e heltec_v4_espnow_companion_radio_usb
```

Built 2026-09-13 on the pinned `d9296435` tree: RAM 104,000 B (5.0%), flash
645,921 B (9.9%). The LoRa companion on the same tree is RAM 104,904 / flash
650,469, so this fits the same partitions.

If the built firmware is to be used with the production runtime, it must carry a
version string the host admits (`meshcore_transport.SUPPORTED_FIRMWARE`) -
`build.sh` derives it as `${FIRMWARE_VERSION}-${COMMIT_HASH}`, so build with the
same pair as the deployed release or the host will refuse it at handshake.

## What it costs, and what it does not solve

* **Range.** 2.4 GHz is tens of metres indoors, a couple of hundred outside.
  This is a local lane, not a village link. That is exactly why upstream's
  design is a *bridge*: fast lane local, LoRa long.
* **No LoRa at all.** This build replaces the radio; it does not add to it.
  Upstream has ESP-NOW as a mesh radio (companion) and as a bridge (repeater),
  but **no companion that speaks LoRa and ESP-NOW together** - that two-tier
  build is the missing piece for Lantern, and it is the natural upstream PR.
* **Both ends.** ESP-NOW is peer-to-peer, so a measurement needs both units
  flashed, and both are out of LoRa service until flashed back.
* **Identity.** An app-only flash should preserve the DataStore identity (the r2
  flash did, verified by unchanged public key at the time), but that is an
  assumption for *this* build, not a measurement. Verify the public key after
  flashing before trusting it.

## Plan if it is taken further

1. Back up both units' radio regions as the packet-RSSI section of the parent
   README describes.
2. Flash unit A only. Verify: boots, reports the expected identity, host
   handshake accepted, audio readiness intact.
3. Flash B, then measure with
   `work/messages-ab-20260913/meshcore_throughput.py send <peer> 25 128`
   (radio-agnostic: it uses `send_text` and observes mesh ACKs) and compare
   against the 410 B/s LoRa fast-lane number.
4. Flash both back to the commissioned LoRa build and re-verify the profile,
   identity and services.

## Flashing notes from the first attempt (2026-09-13)

Nothing was written to either radio: the app-region read hung in esptool's
connect phase, so the attempt stopped before any write. What was learned:

* **The units have no esptool and no network.** Stage the Mac's
  `~/.platformio/packages/tool-esptoolpy` (4.5.1) **and** pyserial 3.5 into
  `/tmp/lantern-esptool` on each unit, and run it as
  `sudo env PYTHONPATH=/tmp/lantern-esptool python3 -m esptool ...`. esptool
  needs pyserial, and the units' Pythons do not have it.
* **esptool 4.x and 5.x disagree on syntax.** 4.5.1 wants `read_flash`,
  `write_flash` and `--after no_reset` (underscores); the reviewed
  `scripts/lantern/backup-b-esp-flash.py` targets 5.3.1 and uses hyphens. Using
  the wrong form fails at argument parsing, which looks like a connection error.
* **This board needs `--before usb_reset --no-stub`.** The default reset path is
  for a real UART and the stub upload hangs over the ESP32-S3's built-in
  USB-Serial-JTAG. With that combination `flash_id` connects and reports
  ESP32-S3 rev v0.2, 16 MB flash. Without it, the command sits in "Connecting..."
  indefinitely and writes no output file.
* **Partition tables read cleanly on both units and are identical**: `nvs`
  @0x9000, `otadata` @0xe000, `app0` @0x10000 size 0x640000, `app1` @0x650000,
  `spiffs` @0xc90000, `coredump` @0xff0000. An app-only write to 0x10000
  therefore cannot touch the identity, the OTA selector or the settings store -
  which is what makes this experiment reversible.
* **The 6.5 MB app-region read is unnecessary for safety.** What has to survive
  is the metadata (untouched), and both units' firmware is rebuildable: the r2
  restore image builds from this tree with
  `PLATFORMIO_BUILD_FLAGS="-DFIRMWARE_VERSION='\"v1.17.1-d929643-r2\"'"` so the
  host still admits it. Prefer a pre-built restore image over a slow read.
* **Build artifacts are not durable.** Rebuilding any env in a tree prunes the
  other envs' `.pio/build` directories here, so build the ESP-NOW image
  immediately before flashing it rather than relying on an earlier build.
