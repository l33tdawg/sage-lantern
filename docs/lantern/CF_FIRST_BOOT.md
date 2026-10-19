# C–F first boot inventory

**Current roles:** C–F are real user Lanterns on Orange Pi 5 Plus with dual LoRa, GPS and WiFi via the Pi. A/B are single-LoRa Lamplighter/admin/knowledge/repeater support units. See [the canonical role map](DEVICE_ROLES.md). Bare-board inventory does not remove peripherals from the final target.

The current local software kit is **r3**, under
`work/cf-package-20260927-r3/release/`. Its README provides the collector,
archive verification and first-unit handoff. See the adjacent `REPORT.md` for
the corrected SDK, approved portrait UI, updated runtime/startup helpers and
validation evidence. This supersedes r2 as the candidate for review; it does
not establish installation or hardware acceptance. Earlier exports remain
unchanged.

The owner began unboxing on 27 September 2026. C's first boot, IP and SSH
account are still pending; no board or peripheral has been qualified by this
software session. Work starts with four units, C–F; six total C–H is the December
milestone. Delivery tracking is not physical receipt, fit, enumeration or power
acceptance. Flint owns the current hardware bill of materials.

Flint's latest handoff expects a bare-board first boot with display/touch and
NVMe, without microphone, camera, HAT or second radio. Their absence is expected
inventory, not a failed board prerequisite. His corrected order report says the
pack-to-Pi lead is ordered; actual receipt and power suitability remain bench
checks. Board revision, USB-C power/data roles and current RK3588 pinmux still
need evidence. A pinmux dump describes kernel state, not electrical compatibility
or a verified physical header net map. The collector does not run `gpioinfo`,
mount debugfs or change GPIO/pinmux settings.

`scripts/lantern/cf-first-boot.py` is ready to run when the first Orange Pi 5 Plus
boots and its IP/account are known. It produces a read-only JSON inventory, not
an installation or commissioning result. It does not use SSH, request passwords,
install packages, open raw disks, mount filesystems, start services, capture
media, transmit RF or inspect SAGE/SSH identities, tokens or vaults.

## Run on the unit

Prerequisites for the collector are Python 3.8+ and access to the script, as an
ordinary login user. No Python packages or root privileges are required. For a
complete inventory the OS should supply `uname`, `lsblk`, `findmnt`, `ip`,
`lsusb`, `systemctl`, `/proc`, `/sys` and `/etc/os-release`. Missing commands,
permissions or files are reported as **unknown**; the collector never installs
them. Python/Node appliance checks use exactly `/usr/bin/python3` and
`/usr/bin/node`, regardless of the operator's shell PATH.

After the script has been copied to the unit, run from its directory:

```sh
python3 cf-first-boot.py --unit-id unit-c > unit-c-first-boot.json
```

With the repository already present, run from its root instead:

```sh
python3 scripts/lantern/cf-first-boot.py --unit-id unit-c > unit-c-first-boot.json
```

Use the physical unit's label (`unit-c`, `unit-d`, `unit-e`, or `unit-f`). The
label is an operator assertion, not hardware identity or proof of ownership.
The shell redirection writes the report only; the collector itself writes no
files. When reviewing remotely, use the user's supplied account/IP and their
existing SSH access; this script has no credential or connection parameters.
Hardware serials and network addresses are deliberately included in the report;
keep it with project bench evidence rather than publishing it automatically.

Exit 0 means a report was produced, including reports with failed/unknown
checks. Exit 2 means CLI/fixture/report generation failed. Inspect
`failed_checks`, `unknown_checks`, `checks` and `evidence`, not just the exit
code. Each external command has a five-second timeout.

## What the report establishes

- Exact board compatible/model, OS-visible RAM, OS and kernel architecture.
- Enumerated disk/partition paths, byte capacity, model, serial, read-only flag
  and actual root source/filesystem. No empty-disk, erase or bootloader claim.
- NPU device/driver/module names and DRM render nodes; presence alone does not
  qualify RKLLM, the kernel driver ABI or inference.
- Exact Python/Node versions, systemd tool version and network addresses. An IP
  address does not prove SSH or external reachability.
- USB VID/PID descriptions plus audio, video, serial and input node names.
  Disconnected or unboxed peripherals can legitimately be absent. The collector
  neither assigns hardware roles from those names nor marks peripherals ready.
- I2S/CSI platform-driver names, selected camera-sensor driver names, media and
  V4L subdevices, onboard UART, SPI and PPS names, plus kernel ALSA cards/PCM
  inventory. A registered driver does not prove that a matching device is bound,
  wired, correctly configured or working. No capture, GPIO, SPI or UART I/O runs.
- Exact `/usr/bin/c++` and CMake version output, Python `venv`/`ensurepip`/`ssl`
  discovery, and the NPU service's existing `render`/`video` groups. The isolated
  Python check disables bytecode writes. Group member names are omitted. A
  missing group is a failed prerequisite; an unavailable probe remains unknown.
  These checks do not build code, create a virtual environment, install packages,
  grant permissions or prove that target wheel imports work.

`pass` in an evidence row means that probe was readable and parsed. `pass` in a
check means only the stated criterion passed. `fail` means an observed value
missed a criterion; `unknown` means unavailable, unparseable or not tested.
`ready`, `provisioned` and `write_authorized` are always false.

The expected baseline is Orange Pi 5 Plus (`xunlong,orangepi-5-plus`), Linux
ARM64, 16 GB RAM tier (at least 14 GiB visible after firmware reservations), an
NVMe disk with at least 250 decimal GB, Debian/derived Bookworm/12, Python 3.11
exactly (the offline wheelhouse requirement) and Node 22.13+. These are software
preflight expectations, not procurement verification.
A different OS or reported board binding needs review; do not change evidence
merely to make a check pass. NVMe presence passes its capacity check even while
booting SD; use the separate root-source evidence to see where `/` actually is.

## Next software gates

1. **Board/image qualification.** Review the actual image/kernel, NVMe serial,
   boot firmware, display/touch, power and cooling. Existing
   `nvme-migration-preflight.py` accepts only Radxa ROCK 5B Plus; this collector
   does not relax it or authorize writes. Use no A/B disk image, serial or SPI
   script as an Orange Pi default.
2. **C–F unit bindings and release.** The fresh provision runner and related
   protected-config, app, audio, mesh, startup, display and update gates now
   admit exact A–F identities in source. Their tests preserve cross-unit
   mismatch refusals. The native SAGE initializer also needs the matching new
   binary; the stack now pins 11.23.27 and installs the root-owned
   `/etc/sage/provisioning-profile.yaml` before identity initialization.
   Source expansion and a compiled candidate still require a complete matched
   appliance release and native acceptance. The candidate profile now packages
   NPU 2B, CPU 4B vision and local embeddings. The vision planner pins both NPU
   and CPU service definitions and targets only the CPU vision drop-in. Inventory success
   cannot bypass those checks. Historical A/B migration/recovery scripts remain
   bound to their original units and are not C–F deployment tools.
3. **Independent identity.** Install sterile software, then generate each
   unit's private chain/Root/application/public and radio identities locally.
   Compare public inventories across the fleet. Never copy a provisioned SAGE
   home, vault, host keys, tokens, journals or machine identity from A/B.
4. **Peripheral contracts.** Flint identifies an I2S microphone (not yet ordered)
   and CSI IMX519 camera. Existing USB audio commissioning and USB-by-id camera
   installation do not qualify those devices. Confirm exact carrier/connector,
   pin mapping, drivers, companion radio choice and GNSS receiver/interface.
   Flint now reports six XIAO ESP32S3 + Wio-SX1262 kits ordered; arrival,
   physical revision and USB enumeration are still pending. The XIAO/Wio image
   remains an unflashed candidate, and historical Heltec order records do not
   establish the parts actually received.
   A GNSS antenna is not a receiver. Radios and camera delivery/fit are not
   firmware or capture acceptance.
5. **Commission C, then D–F.** Complete owner vault setup, protected storage,
   audio, display/touch, camera as available, radio and cross-unit messaging;
   then cold-boot and release evidence. NPU/model changes need measured thermal,
   memory and response qualification. No first-boot report substitutes for it.

## Offline verification

```sh
python3 -m unittest discover -s scripts/tests -p 'test_cf_first_boot.py' -v
```

The CLI also accepts a synthetic fixture, runs no live probes, and stamps the
result `source: fixture`:

```sh
python3 scripts/lantern/cf-first-boot.py --unit-id unit-c --fixture /path/to/fixture.json
```

Fixture shape:

```json
{
  "schema": "sage.lantern.cf-first-boot-fixture.v1",
  "probes": {
    "board_compatible": {
      "status": "pass",
      "value": "xunlong,orangepi-5-plus\u0000rockchip,rk3588\u0000"
    },
    "node": {"status": "unknown"}
  }
}
```

Omitted probes become unknown. File/command probe values are strings; enumeration
probe values are lists of names. Accepted probe names are the fixed `FILES`,
`COMMANDS` and `GLOBS` keys in the script. Fixtures cannot provide commands or
paths to execute/read. Tests cover wrong boards, missing probes/tools,
permissions/timeouts, undersized/absent hardware, old Node, unqualified OS,
malformed output, omission of unrelated fields and separation of enumeration
from commissioning.
