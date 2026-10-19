# SAGE Lantern documentation

This tree covers the Lantern device and the services that support it. Start with the document that matches your question.

## What works today

- [Delivery backlog](lantern/DELIVERY_BACKLOG.md): the current acceptance gate, verified state and open work.
- [3B model production](lantern/3B_MODEL_PRODUCTION.md): what V81 runs on Unit C, the measured timings, guards and known limits.
- [Unit A acceptance evidence](lantern/UNIT_A_ACCEPTANCE_EVIDENCE.md): the first prototype build and its offline voice, timing and recovery evidence.

## Installing or provisioning a unit

- [Desktop mode](lantern/DESKTOP_MODE.md): portrait kiosk, landscape desktop and the D-022 login gate.
- [First install setup](lantern/FIRST_INSTALL_SETUP.md): network, account and recovery setup.
- [SSH management](lantern/SSH_MANAGEMENT.md): operator access and key rotation. No private keys live here.
- [Hardware and software contract](lantern/HARDWARE_SOFTWARE_CONTRACT.md): where the ownership boundary sits.

## Hardware and build

- [Hardware overview](../hardware/README.md): device roles, current status and safety notes.
- [Bill of materials](../hardware/BILL_OF_MATERIALS.md): parts, quantities and what is still pending.
- [Printable parts](../hardware/stl/README.md): STL folder and print-file conventions. Nothing is published yet.
- [Build guide](build/README.md): the full build path and what it assumes.
- [Assembly](build/ASSEMBLY.md): physical build sequence for the C-F unit.
- [Software stack](build/SOFTWARE.md): install order, checks and rollback.

## First boot and recovery

- [Boot overview](boot/README.md): the boot path and what is verified.
- [First boot](boot/FIRST_BOOT.md): owner setup, networking and service checks.
- [Recovery](boot/RECOVERY.md): rollback, service recovery and evidence preservation.

## Maps

- [Offline map packs](../maps/README.md): catalog, pack sizes and install commands.
- [Map coverage decision](lantern/MAP_COVERAGE.md): tiers, disk budget and public distribution.

## Protocol and architecture

- [MeshCore transport protocol](lantern/MESHCORE_TRANSPORT_PROTOCOL.md) and [host protocol test plan](lantern/MESHCORE_HOST_PROTOCOL_TEST_PLAN.md).
- [Master technical PRD](MASTER_TECHNICAL_PRD.md), [terminology](TERMINOLOGY.md) and [documentation governance](DOCUMENTATION_GOVERNANCE.md).
- [Open source policy](OPEN_SOURCE_POLICY.md) and [governance](../GOVERNANCE.md).

## Project family

- [SAGE core](https://github.com/l33tdawg/sage/): agent memory, governance and core infrastructure.
- [Meshticulum](https://github.com/l33tdawg/meshticulum): transport, reliable flow and verified resources.
- [Lantern 3B](https://github.com/l33tdawg/lantern-3b): model weights and model card.

The [Foundation site](https://sage-foundation.net/) is the public home for the programme and contact details.

## Foundation and mission

- [SAGE Foundation](https://sage-foundation.net/foundation/): stewardship, ownership boundaries and the people behind it.
- [SAGE Commons](https://sage-foundation.net/commons/): how the Foundation, the commons and community networks relate.
- [Lantern hardware](https://sage-foundation.net/hardware/): the device concept and programme.
- [Meshticulum alpha](https://github.com/l33tdawg/meshticulum/releases/tag/v1.0.0-alpha): transport SDK, firmware source build and proof records.
