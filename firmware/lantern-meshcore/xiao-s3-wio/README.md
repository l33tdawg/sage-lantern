# Lantern XIAO ESP32S3 + Wio-SX1262 USB candidate

Status: compiled candidate for C–F, not a commissioned radio image. No hardware has
been flashed or tested. Production host firmware admission is unchanged.

The exact upstream pin already supplies `Xiao_S3_WIO_companion_radio_usb`.
That environment compiles an SSD1306 display. This separate environment inherits
only the board base and builds a headless USB companion for the B2B Wio-SX1262
kit. It retains 350 contacts, 40 channels and the XIAO upstream offline queue of
256 entries. It does not add BLE or Wi-Fi host interfaces.

Built 2026-09-27 with PlatformIO 6.2.0: RAM 145,728 bytes (44.5%); flash
617,205 bytes (18.5% of app0). App binary is 617,616 bytes, SHA-256
`3cb041fa3105a0f9da4cd7b6d00dbf557c57b11b38f30a65652b92b56193de18`.
Build logs, resolved dependencies, partition decode and both app-only/merged
images are in `work/xiao-20260927/`. The ELF repeat table was checked as
`{917375,917375}` and the candidate version was found intact in the binary.

## Prepare and build

Use a fresh checkout; the preparation script rejects a different commit or a
dirty tree. A second run refuses with exit 2 and leaves all checkout files
unchanged; it never resets, cleans or discards a checkout. PlatformIO Core 6.2.0 is the tested orchestration version; the project
pins Espressif32 6.11.0 and RadioLib to
`6d8934836678d8894e3d556550475b37dce3e2b6`. Other upstream library constraints
remain ranges, so this recipe is repeatable source preparation, not a claim of
bit-for-bit reproducibility across future dependency resolutions.

```sh
git clone https://github.com/meshcore-dev/MeshCore.git /tmp/meshcore-xiao
git -C /tmp/meshcore-xiao checkout d92964352441e53b93e8667b802e04f6e072b39e
python3 firmware/lantern-meshcore/xiao-s3-wio/prepare.py /tmp/meshcore-xiao
pio run -d /tmp/meshcore-xiao -e lantern_xiao_s3_wio_usb
pio run -d /tmp/meshcore-xiao -e lantern_xiao_s3_wio_usb -t mergebin
```

`pinned-r5.patch` folds in packet RSSI capture, contact response atomicity,
917.375 MHz client-repeat allowance and Lantern's r5 rolling relay budget.
The atomicity patch needs one port at this old pin: initialize
`_deferred_push_count` beside `_iter_started` without the newer `_cli_rescue`
context. Relay cap defaults to factor 9 (10% rolling window); repeat remains off.
The historical Heltec environment/version edits remain in the consolidated
patch; this candidate selects its own XIAO environment and firmware identity.

Candidate version is `v1.17.1-r5-xiao1` (16 ASCII bytes). The companion response
allocates 20 bytes and `strzcpy` reserves the last for NUL, so the text limit is
19 bytes. This candidate deliberately has a distinct identity from the deployed
Heltec r5 image. Do not reuse the Heltec image digest or host admission entry.

## Hardware and image boundaries

| Item | XIAO + Wio-SX1262 |
| --- | --- |
| MCU/board | ESP32-S3, `seeed_xiao_esp32s3`, 240 MHz default |
| Host interface | Native USB CDC; `ENABLE_USB_INTERFACE`, `ARDUINO_USB_MODE=1`, `ARDUINO_USB_CDC_ON_BOOT=1`; companion serial begins at 115200 |
| SPI | SCLK GPIO7, MISO GPIO8, MOSI GPIO9, NSS GPIO41 |
| LoRa control | DIO1 GPIO39, reset GPIO42, busy GPIO40, RXEN GPIO38 |
| RF configuration | SX1262; DIO2 RF switch; DIO3 TCXO 1.8 V; current limit 140 mA; RX boosted gain |
| Other pins | Button GPIO21, status LED GPIO48; I2C D4/D5 |
| Flash/PSRAM | 8 MB flash, QIO/OPI memory type, PSRAM enabled by board definition |
| Headless build | No `DISPLAY_CLASS`, no display/UI task sources |

The Heltec V4 uses different GPIOs and external PA control, 16 MB flash and
QIO/QSPI memory type. A Heltec image is not a XIAO image. XIAO's source default
LoRa TX power is 22 dBm, whereas Heltec's 10 dBm chip setting is coupled to its
external amplifier. Do not carry chip-power assumptions across boards.

This candidate defaults a blank board to the commissioned Lantern channel:
917.375 MHz, 62.5 kHz bandwidth, SF8, CR5. `build_unflags` removes upstream's
869.618 MHz definition before the candidate supplies 917.375; the actual
`MyMesh.cpp` compiler command was checked and contains exactly the intended
frequency, with no inherited 869.618 definition. Existing stored preferences
still load after constructor defaults and override them. Repeat defaults off.

The XIAO upstream TX power default remains **22 dBm**; this is a chip setting,
not a measured radiated-power result or a newly approved power target. Bench
commissioning must read back frequency, bandwidth, SF, CR and TX power, settle
the appropriate power setting, and verify it survives power removal. No regional
compliance conclusion is made by this build.

The selected `default_8MB.csv` layout is:

| Partition | Offset | Size |
| --- | --- | --- |
| nvs | `0x9000` | `0x5000` |
| otadata | `0xe000` | `0x2000` |
| app0 | `0x10000` | `0x330000` |
| app1 | `0x340000` | `0x330000` |
| spiffs | `0x670000` | `0x180000` |
| coredump | `0x7f0000` | `0x10000` |

`firmware.bin` is app-only. A factory provisioning image needs matching
bootloader and partition table; `mergebin` produces the combined image.
An existing radio update must inspect its actual table and preserve identity,
settings and OTA state. The Heltec app0 size `0x640000` and SPIFFS offset
`0xc90000` must not be used here. Seeed explicitly documents settings loss when
the SPIFFS partition is missing; a successful handshake alone is insufficient.

## Remaining acceptance

1. One kit: native USB enumeration and companion handshake with the exact
   candidate version; no display dependency or USB reconnect/reset loop.
2. Verify actual flash/partition map; record unique public identity; configure
   commissioned RF profile and prove settings/identity survive power removal.
3. Contacts list, advertisements, one acknowledged TX and one RX with an A/B
   reference unit; verify RSSI/SNR vary plausibly and confirm host audio readiness.
4. Check power, temperature and reconnect behavior; only then approve a common
   image/digest and host admission for the remaining C–F units.

ESP-NOW is a separate gate. The pinned XIAO variant already has a repeater bridge
and the ESP32-S3 supplies Wi-Fi, making a companion bridge plausible at source
level. Our companion bridge, hardware-start/defaults fix and lane-selection
patches are not a clean drop-in on this r5 tree: the constructor, `NodePrefs`
and `CommonCLI` seams conflict. This headless USB candidate contains no bridge.
Port those changes together under another version, then compile and bench-test
both lanes, including traffic with LoRa blocked. Earlier Heltec lane results do
not validate this board.

## Primary references

- [Pinned XIAO environments and pins](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/variants/xiao_s3_wio/platformio.ini)
- [Pinned XIAO target](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/variants/xiao_s3_wio/target.cpp)
- [Pinned companion USB setup](https://github.com/meshcore-dev/MeshCore/blob/d92964352441e53b93e8667b802e04f6e072b39e/examples/companion_radio/main.cpp)
- [PlatformIO board definition, Espressif32 6.11.0](https://github.com/platformio/platform-espressif32/blob/v6.11.0/boards/seeed_xiao_esp32s3.json)
- [Seeed kit MeshCore guide, B2B connection and partition warning](https://wiki.seeedstudio.com/get_started_with_esp32s3_meshcore/)
