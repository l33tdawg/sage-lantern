# C–F SD provisioning and NVMe root

This process is for the Orange Pi 5 Plus. SD is the initial installer and recovery
medium. First migrate the operating system to NVMe, then install the board's
SPI bootloader and move the kernel/boot files onto NVMe. After a verified cold
boot without SD, the card is no longer required. A/B's Radxa image is incompatible.

The initial SD-boot/NVMe-root test below was an intermediate step. The owner
explicitly requested completing SD-free SPI/NVMe boot; see the completion
procedure below. Do not treat an intermediate NVMe root alone as SD-free boot.

## Unit C result — 28 September 2026

Update: the SPI loader was subsequently written and all 16,777,216 bytes read
back identically to the reviewed vendor image. `/boot` now lives directly on
NVMe and fstab no longer mounts SD. The owner removed SD and powered C on.
**SD-free cold boot is verified:** SSH returned at 192.168.50.104, the boot ID
changed to `eb77a3f7-8e09-422c-a58d-6344c85efbd4`, `/` and `/boot` resolve to
`/dev/nvme0n1p1`, no MMC device is enumerated, and no system units failed.
The filesystem has 234 GiB total and 190 GiB available.

*(Address note, 14 Oct 2026: the `192.168.50.104` in this paragraph, and the
second mention in the block below, is the address this boot came back on. It no
longer answers; the unit is at `192.168.50.2` (`lantern-c.local`). Both are left
exactly as written, because they record what was observed at the time — only live
addressing has changed.)*
Evidence: `work/unit-c-boot-20260928/spi-nvme-apply.log` and
`spi-nvme-coldboot-verification.txt`. NVMe PARTUUID is
`01b9c09e-bb12-4809-81c4-22ea3e9a111f`; this SPI loader leaves `ubootpart`
empty. Do not require a populated `ubootpart` for this verified boot path.

The owner requested C's NVMe setup and a repeatable process for D/E/F.
C successfully rebooted with `/` on `/dev/nvme0n1p1` and `/boot` bound from
`/dev/mmcblk1p1[/boot]`. The ext4 root showed 234 GiB total, 6.8 GiB used and
215 GiB available before staging the D/E/F image files. Ethernet returned at
`192.168.50.104`; the hostname is now `lantern-c`. Root and sage accounts were
preserved. System-level failed-unit count was zero. This is storage validation,
not touchscreen, peripheral, inference or full Lantern acceptance.

The target is the 256,060,514,304-byte Netac NVMe SSD, serial
`AA20260814256G089241`. Its new root UUID is
`d930650a-ced3-47d5-911a-c6a0b05cec24`. The SD UUID is
`711b054e-2429-49e9-bcd4-2f26583514ae`.

Evidence is in `work/unit-c-boot-20260928/nvme-migration.log` and
`nvme-postboot-check.txt`. C's remote backup directory is
`/var/lib/lantern-base-provision/20260928-174922`, including the original
`armbianEnv.sd-root.txt`, original fstab, device inventory and installer log.

The installed Ubuntu session/theme is selected for sage's next login. The base
remains Armbian 26.8.2 / Ubuntu 26.04 with vendor kernel 6.1.115, rather than
a generic Ubuntu ARM ISO. Existing Lantern release r3 requires Python 3.11 and
the Debian 12 baseline. A matched r5 candidate now adds an explicit C–F
Ubuntu profile with the observed board/kernel and isolated Python 3.11.16.
The dedicated runtime and target platform checks passed; base installation and independent identity/protected configuration completed.
This does not qualify the full app, peripherals or lifecycle.
See `work/cf-package-20260928-r5/README.md`. Existing compatibility and
fresh-install checks remain enforced.

## Prepare a fresh SD image

Prepared images now exist on C in `/var/lib/lantern-images/`:

| Unit | File | SHA256 |
| --- | --- | --- |
| D | `lantern-d-bootstrap.img` | `b10d570cb50493ca457a4a725d48732ecf49c77466c8b7cacb52e27b36301284` |
| E | `lantern-e-bootstrap.img` | `3bde0340cb48546b3521f87060b8e7c1b6b710e247534592c335aa50de40f904` |
| F | `lantern-f-bootstrap.img` | `e9c6130603279ee80bea4b72c7be006ed1bd477a2169adc16da0f39e06b82596` |

Each is 6,501,171,200 bytes, with a `.sha256` sidecar, the initial root/sage
login configured, Ethernet DHCP and its own hostname. Passwords are omitted
here. The images remain on C, not downloaded to the Mac or written to D/E/F
cards. C has about 190 GiB available after staging the source and all three
outputs. D's real generated image was inspected read-only: account config,
fresh-key setup, empty machine ID, required groups, SSH includes, and the
systemd first-boot service validation all passed. Physical D/E/F first boots
are still untested.

The images were subsequently updated to include `lantern-enable-nvme-boot` and
unique filesystem/GPT identities. All three passed a read-only `e2fsck` and
first-boot systemd unit validation after that update; the table above contains
the updated checksums. The local manifest is
`work/unit-c-boot-20260928/bootstrap-images.json`.

Run `scripts/lantern/prepare-cf-sd.py` on a Linux host as root. C can build the
images on its NVMe. The adjacent `cf-sd-firstboot.py` and `cf-nvme-setup.py` files
and `cf-enable-nvme-boot.py` must accompany it. Preparation edits an image file;
it does not write an SD. The builder now assigns fresh filesystem and GPT IDs
to each image and refuses to attach the pristine image while that same source
UUID is mounted on the host, avoiding duplicate device aliases.

Use only this verified pristine image:

```
Armbian_26.8.2_Orangepi5-plus_resolute_vendor_6.1.115_gnome_desktop.img
bytes: 6501171200
SHA256: 4cdec691799521f88ada6d4e5888c067360b03297cb0fe2b351b2090e021e221
```

The `.img.xz` SHA256 is
`6a9ba39ac476bcfe8f02623a0efed70e8ad4bb8f3c74d71754164d0f91cb3006`.
Source: [Armbian Orange Pi 5 Plus image](https://dl.armbian.com/orangepi5-plus/Resolute_vendor_gnome).
The download may change over time; the builder deliberately requires the
recorded image hash, not whatever the URL serves later.

On C, the pristine image and tools are under `/var/lib/lantern-images/`:

```sh
sudo python3 /var/lib/lantern-images/tools/prepare-cf-sd.py \
  --source /var/lib/lantern-images/Armbian_26.8.2_Orangepi5-plus_resolute_vendor_6.1.115_gnome_desktop.img \
  --output /var/lib/lantern-images/lantern-d-bootstrap.img --unit d
```

Repeat with `e` and `f`. The tool prompts for the initial root and regular-user
password without echoing it. The default regular username is `sage`; change it
with `--username`. Only a salted password hash is embedded. Do not commit
passwords or configured image contents. Outputs have mode 0600 and a SHA256
sidecar. A failed build remains `.incomplete`, not a finished image.

The default network setting is the image's Ethernet DHCP. Each unit receives
its own hostname (`lantern-d`, `lantern-e`, `lantern-f`) and fresh SSH host keys
and machine ID. With mDNS supported on the client/LAN, connect as
`ssh sage@lantern-d.local`; otherwise use its DHCP address from the router.
For stable numeric addresses, router DHCP reservations avoid hardcoded clashes.

If a static address is needed, supply an unused `--address IP/PREFIX`,
`--gateway`, `--mac EXACT_ETHERNET_MAC` and `--dns ADDRESS`. The exact NIC MAC is
required because the board has two Ethernet ports. No IP is guessed or copied
from C. The SD image must be written separately for each unit; do not copy a
booted C disk, its machine ID, SSH host keys, or any Lantern private state.

Write the prepared image to the intended removable SD with a verified image
writer, checking the card identity and resulting image checksum first. On macOS,
ext4 cannot be edited with normal filesystem tools; prepare the image on Linux
and then transfer/flash the resulting `.img`.

## First boot and NVMe migration

Insert the corresponding SD, fit the blank NVMe, connect Ethernet and power on.
The one-shot service creates the accounts before SSH starts, removes the
interactive Armbian first-login prompt only after success, and deletes its
password-hash configuration after completion. Hardware first-boot validation on
D/E/F remains required; an image build alone is not that validation.

After SSH login:

```sh
sudo lantern-nvme-setup
```

This only inspects the board and disk. Read the reported serial and byte count,
then explicitly apply to that disk:

```sh
sudo lantern-nvme-setup --apply --expect-serial SERIAL_FROM_REPORT --expect-bytes BYTES_FROM_REPORT
sudo reboot
```

The script checks board identity, SD root, exact NVMe identity, no partitions,
no filesystem signatures, no mounts/holders/swap use, and the reviewed Armbian
installer hashes. It refuses a second migration or a disk with existing data
structures. It checks package state, saves boot settings, calls the native
installer's SD-boot mode, verifies root UUID, boot mapping and copied accounts,
and restores the original SD boot target if its verification fails. It never
automatically retries a partial disk write.

After reboot, verify:

```sh
findmnt /
findmnt /boot
df -h /
systemctl --failed
```

`/` must be NVMe; `/boot` must resolve to the SD's boot directory. A successful
installer exit before reboot is insufficient. Keep SD inserted until the next
phase moves those boot files and installs the SPI loader.

## Complete SD-free SPI/NVMe boot

After the first reboot has confirmed NVMe root, run the second guarded command:

```sh
sudo lantern-enable-nvme-boot --sd-root /dev/mmcblk1p1 --expect-serial SERIAL_FROM_REPORT
sudo lantern-enable-nvme-boot --sd-root /dev/mmcblk1p1 --expect-serial SERIAL_FROM_REPORT --apply
```

Use the actual SD root partition from that unit's first-stage inventory. The
first command inspects only. The apply command checks the exact board, NVMe
serial, GPT layout, 16 MB SPI capacity, installed vendor image and bootloader
hook hashes. It backs up SPI, copies the current kernel/initramfs/DTBs/boot
script to NVMe, calls **only** `armbian-install bootloader --target mtd --yes`,
then compares all 16 MB of SPI readback with the source. It removes the SD
mounts from fstab only after that verification. It does not format NVMe.

The reviewed SPI image SHA256 is
`9caff588143668fe20a26bd358c2b3ebbdc991486d640d2871461a6dd6ad813b`.
C's original SPI backup SHA256 is
`42ee0945e199fc4a0ba61167afd200d47992613ea900b6b477f5accd9e46d2bc`;
an independent copy is saved on the Mac at
`work/unit-c-boot-20260928/spi-before.bin`. The script's on-board backups and
report are in `/var/lib/lantern-base-provision/spi-nvme/`.

After successful readback, shut down cleanly, remove SD, then power on. Verify
that SSH returns with a new boot ID, `/` **and** `/boot` resolve to NVMe,
the kernel root UUID matches NVMe, and the SD device is absent. Record
`ubootpart` if present; C's reviewed SPI loader leaves it empty. Only then is
SD-free boot accepted. Never use the full `--boot mtd` system installer against
the already-running NVMe: it would repartition the target.

For C's rollback, its original SD root remains intact. Restore the backed-up
`armbianEnv.sd-root.txt` to the SD's `/boot/armbianEnv.txt`, then reboot. This
returns to the earlier SD system; later NVMe changes will not be present there.
If networking does not return after a migration, inspect the display/serial
console before modifying storage further.

## Validation

`python3 -m unittest scripts.tests.test_cf_nvme_setup scripts.tests.test_prepare_cf_sd scripts.tests.test_cf_enable_nvme_boot`
covers refusal of wrong boards/disks, mounted or previously formatted targets,
installer drift, invalid static networking, private-state reuse, and the fresh
image's SSH/account setup ordering. C provides the real SD-to-NVMe reboot test.
Per-unit SD images still need first boot on their own physical boards.
