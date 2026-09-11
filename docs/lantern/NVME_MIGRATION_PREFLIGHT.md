# SD to NVMe base OS migration gates

This is a fresh OS-image installation, not a clone of Unit A or a copy of the
currently running SD filesystem. Do not copy appliance identities or keys.
`scripts/lantern/unit-a-nvme-migrate.sh` is historical reference only: it masks
sleep before checking identity, contains A's serial, and flashes SPI as well as
NVMe. Do not run it on B.

## Current B observations, not authorization to erase

Parent reports ROCK 5B Plus at `.96`, SD root `/dev/mmcblk1p3`, SD serial
`0x128338a1`, and NVMe `/dev/nvme0n1`, Netac 512GB, serial
`AA20260312512G069617`. A's serial ends `069623` and must not match B.
An address or device pathname alone does not identify a physical unit.
Exact NVMe byte capacity and model spelling still need current confirmation.
No filesystem in `lsblk` does not prove absence of recoverable user data.
Parent subsequently confirmed no `wipefs` signatures and the image at
`/home/radxa/rock-5b-plus_bookworm_kde_r7.output_512.img.xz`, compressed size
1,428,295,552 bytes. Existence and size do not substitute for image hashes.
Parent has now verified B's compressed image SHA-512 read-only, matching the
previously vetted image:

```text
3e4ddd8eb88dd9d8b2232e69212f45913f21cf8ebb30953f4f637b60876b250722beae744021502b49cf4de900f341878b6fa4025fcfdab38713d9f90b088ebb
```

Parent subsequently reports the explicitly authorized B NVMe image write and
exact-byte readback passed: 7,726,932,992 bytes, decompressed/readback SHA-256
`063bc9500c8406fc86d4926eddefc938e4a114b6720251f635f13089bc871024`.
The separately authorized vendor SPI operation is in progress; this audit does
not claim its completion. Owner SD removal and verified NVMe root boot remain
pending. These are parent-reported target results, not device operations by this
source-only audit agent.

## Read-only prerequisites

- Boot B from SD; confirm board compatible string and SD root/serial.
- Use Linux Python 3 with lzma support and util-linux `findmnt`, `lsblk`,
  `wipefs`, `swapon` at the paths used by the helper. Run inspection as root.
- Obtain the intended board-compatible OS image separately. Review its origin,
  compressed SHA-512, decompressed SHA-256 and exact decompressed byte length.
  Do not populate reviewed hashes from an untrusted download merely to pass.
- Fill an expected JSON document with the exact fields below. Nulls deliberately
  fail validation until the missing reviewed observations are provided.

```json
{
  "schema": "sage.lantern.nvme-preflight.v1",
  "board_compatible": "radxa,rock-5b-plus",
  "sd_root": "/dev/mmcblk1p3",
  "sd_serial": "0x128338a1",
  "target": "/dev/nvme0n1",
  "target_model": null,
  "target_serial": "AA20260312512G069617",
  "target_bytes": null,
  "image_sha512": "3e4ddd8eb88dd9d8b2232e69212f45913f21cf8ebb30953f4f637b60876b250722beae744021502b49cf4de900f341878b6fa4025fcfdab38713d9f90b088ebb",
  "image_sha256": "063bc9500c8406fc86d4926eddefc938e4a114b6720251f635f13089bc871024",
  "image_bytes": 7726932992
}
```

First inspect current hardware without decompressing the image:

```sh
sudo python3 nvme-migration-preflight.py --expected unit-b-nvme.json --image /path/to/reviewed.img.xz
```

Then perform the full read-only image verification:

```sh
sudo python3 nvme-migration-preflight.py --expected unit-b-nvme.json --image /path/to/reviewed.img.xz --verify-image
```

The helper checks identity and usage before and after verification; rejects
partitions, signatures, mounts, swap and holders; and performs no service,
firmware, disk-write or formatting actions. It has no apply mode. A successful
report is prerequisite evidence, not a reusable future write permit.

## Separate remaining migration steps

1. Review B's installed vendor SPI bootloader and whether it already supports
   the intended NVMe boot. If an update is required, separately authorize and
   back up B's actual SPI, retain the backup hash, and verify vendor-specific
   readback. No automatic SPI update is supplied by this helper.
2. Confirm stable power and separately arrange bounded suspend inhibition.
3. Obtain explicit erase authorization for B's exact serial. The separate
   `scripts/lantern/nvme-image-write.py` rechecks pins immediately, claims
   exclusive block-device access, retains a serial-specific write intent and
   reports possible partial-write failure without automatic retry. The
   preflight does not exclude arbitrary processes holding raw disk descriptors.
4. Write the reviewed decompressed image and verify its exact byte-range hash
   from NVMe. The writer flushes the device cache before exact-range readback;
   it does not grow partitions, change SPI or reboot. Its Linux block-device
   execution is not yet accepted on hardware; tests use regular temporary files.
5. Shut down cleanly, select NVMe boot, then independently confirm `/` is on
   B's NVMe, not SD. Verify partition/filesystem growth and available capacity.
6. Only then bootstrap B, configure Wi-Fi through external admin, and perform
   independent device naming, passcode and key setup. A's acceptance is not B's.

## Separate writer interface (not executed by this audit)

Stage reviewed scripts and expected JSON in a root-owned directory, not a
user-writable home. The image itself may remain at the confirmed path; it is
fully hashed and its identity checked before and after writing. Root-owned pins
must specify hashes of the reviewed helper and expected JSON.

```sh
sudo python3 nvme-image-write.py \
  --preflight-helper /root/reviewed/nvme-migration-preflight.py \
  --preflight-sha256 REVIEWED_HELPER_SHA256 \
  --expected /root/reviewed/unit-b-nvme.json \
  --expected-sha256 REVIEWED_EXPECTED_SHA256 \
  --image /home/radxa/rock-5b-plus_bookworm_kde_r7.output_512.img.xz
```

This defaults to full **read-only** verification. A future explicitly authorized
write additionally requires `--apply --confirm-erase AA20260312512G069617
--boot-reviewed --power-and-suspend-ready`. These last two flags acknowledge
separate operator checks; the script does not claim to verify boot firmware or
acquire a suspend inhibitor. An existing serial-specific journal refuses another
attempt, even after failure; retain it for manual review, do not blindly delete
or retry. No actual write is authorized by this document or a passing dry run.
