# Build a Lantern

This guide is for someone assembling and provisioning a C-F Lantern from the
documented parts. It is a development build, not a consumer unboxing. Some
steps are verified on Unit C; others are still drafts.

Read [the hardware overview](../../hardware/README.md) first. It carries the
current part status and the safety notes.

## What you need

- The C-F parts in the [bill of materials](../../hardware/BILL_OF_MATERIALS.md).
- A fixed 5 V/4 A power path. Do not expect the Orange Pi Type-C port to
  negotiate USB-C PD.
- A microSD card for the base image and an NVMe drive for the root filesystem.
- Wired Ethernet for the first setup, plus a USB keyboard and mouse for desktop
  mode.
- A workstation with `ssh`, `scp` and the repository checked out.
- The unit-specific identity material generated during provisioning. Never
  copy keys, vaults or message journals from another unit.

## Stages

| Stage | What it produces | Current state |
| --- | --- | --- |
| Inventory | Every part checked against the BOM | Draft procedure |
| Assembly | Panel, compute, radio, audio, camera and power fitted | Bench build on C, physical acceptance open |
| Base image | Bootable SD image and NVMe root | Verified on C |
| Provisioning | Fresh host and SAGE identities, protected storage, owner setup | Source paths exist, full fresh installer still open |
| Runtime | Answer runtime, voice loop, storage supervisor | Installed on C |
| Model | RKLLM token engine, model, guards | V81 installed on C, 2B rollback retained |
| UI | Touchscreen app and fleet-admin console | Installed on C |
| Maps and GNSS | Local pack server, world base, gpsd bridge | Installed on C, antenna fix open |
| Acceptance | Typed, spoken, camera, touch, sleep/wake and radio checks | Partly open |

## What "done" means

Source-complete means the code and tests exist. Installed means the files are on
the unit. Measured means a run produced a result. Physically accepted means a
person exercised the actual hardware. These are separate states. A service that
starts is not a proven conversation, and a rendered page is not a proven touch
panel.

## Guides

- [Assembly](ASSEMBLY.md): the physical build sequence.
- [Software stack](SOFTWARE.md): install order, checks and rollback.
- [First boot](../boot/FIRST_BOOT.md): owner setup and service checks.
- [Recovery](../boot/RECOVERY.md): what to do when a service or update fails.
- [Bring-up and validation](../lantern/BRINGUP_AND_VALIDATION.md): the original
  stage-by-stage test plan.
- [SD and NVMe setup](../lantern/CF_SD_NVME_SETUP.md): the verified image and
  migration path for C.

## Safety

- Check voltage and polarity before connecting power.
- Do not hot-plug the panel, camera or radio.
- Do not connect a speaker until the audio board revision and impedance are
  confirmed.
- Keep the unit on a non-conductive bench for the first power-on.
