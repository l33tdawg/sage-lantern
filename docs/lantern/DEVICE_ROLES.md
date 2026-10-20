# Device roles — fleet plan updated 19 October 2026

This is the current device-role map under D-028. It supersedes older C–H
four-unit and dual-radio plans. Historical bench evidence remains dated; it does
not describe the current service configuration.

| Units | Use cases | Hardware target | Current software state |
| --- | --- | --- | --- |
| **A and B** | **Lamplighter, administration, public knowledge cache/delivery, repeater/relay testing** | **Attached dashboard screens**; existing Radxa ROCK 5B, 8 GB (owner-confirmed 2026-09-29 — the base model, not the 5B+); **one LoRa chip per unit** (Heltec USB companion); existing WiFi/IP bench connection | Public pack seeds running on both; private native SAGE stores wiped; legacy endpoint services stopped and inhibited. Pack dashboard and scoped SSH admin are installed; managed bench/product onboarding and RF repeater/LoRa pack integration remain open. |
| **Orange Pi 5 Plus bench units** | **Bench and acceptance work** for the end-user Lantern — private SAGE, local AI and user interface | **Orange Pi 5 Plus**, 16 GB / 256 GB NVMe; one LoRa radio for the Meshticulum baseline, GPS and WiFi via the Orange Pi; **HDMI 6.25-inch 720×1560 panel with USB touch — bench-only, not the product panel** | Two units are retained under D-028; C is commissioned and running. The earlier C–F four-unit lettering remains historical until the physical count is reconciled. Display/touch results here are bench evidence only. |
| **ROCK 5B+ product fleet (four units)** | **Main end-user Lantern fleet** | **Four ROCK 5B+**: two 12 GB boards in hand and two 16 GB boards ordered (RS order 1000144782, ETA 23 October). Two 6.25-inch DSI panels in hand and two ordered; four AC005 adapters and four IMX415 product cameras ordered. The ROCK USB-C input negotiates PD, and the platform carries onboard WiFi/Bluetooth (D-015: bring-your-own power). | Board and panel bring-up has not started. AC005 routing, DSI overlays, camera capture, thermal path, power and physical panel/touch acceptance remain open. |

**A/B use their existing screens for Lamplighter administration.** The owner’s
latest clarification supersedes the earlier headless interpretation. Their web
dashboard distributes the approved public pack, shows transfer progress and
pause/resume, and monitors A/B health, connected USB hardware and cached LAN
neighbours. It is available on both screens and from the management computer.
SSH and `sage-lamplighter-admin` remain available. The Orange Pi bench units
retain their separate end-user display, touch, voice and personal-SAGE
acceptance requirements.

One LoRa radio per device is the product baseline. Meshticulum carries messages
and resource transfers over that single half-duplex radio. Earlier dual-radio
comparison plans are historical bench work, not a product requirement.
Meshticulum is Lantern's own open protocol drawing on MeshCore and Reticulum
ideas; upstream network interoperability is not required.
GPS and WiFi remain separate peripheral work. Exact module revisions, wiring, USB/SPI
paths, firmware, GNSS receiver and WiFi interface/driver must be recorded from
each unit. The XIAO/Wio companion and SX1262/GNSS HAT have separate integration
requirements; do not assume identical host protocols or interchangeable images.
“WiFi via Orange Pi” does not establish an onboard adapter or an ESP-NOW bridge.

The single LoRa chip must schedule receive, transmit and any supported relay
role; it is half-duplex and does not provide independent simultaneous channels.
A role named “repeater” is not evidence that forwarding is already commissioned.
Heltec r7 currently passed USB contact-read qualification only.

Administration on A/B includes the installed scoped SSH operator CLI
(`sage-lamplighter-admin`); see [the operator runbook](AB_OPERATOR_RUNBOOK.md). It does
not grant publisher signing, personal-Root or public validator authority. The
old endpoint Fleet Admin UI is stopped; the separate Lamplighter dashboard
runs on A at port 18791. Bench and ROCK managed-device onboarding remains future
work. Verified public downloads never automatically enter a user's personal
SAGE. The bench units and planned ROCK fleet do not form a four-validator public
consensus network; the independent Lightbearer lab remains separate.

## A/B readiness and data boundary

Both `sage-lamplighter-seed.service` instances are enabled and running. Current
bench catalogue addresses are A `http://192.168.50.103:18090/catalogue.json` and
B `http://192.168.50.96:18090/catalogue.json`. Address/subnet bindings need updating
if the bench network changes.

Both serve the signed 5,686-chunk public world-facts-lite v3 pack from
`/var/lib/sage-lamplighter-lab/`. WiFi resume, multi-source fetch, re-seeding and
seed-outage fallback passed. This is an HTTP chunk lab with BitTorrent-style
retrieval, not the BitTorrent wire protocol. LoRa pack transfer, RF repeater
forwarding, range/coexistence and four actual ROCK product receivers remain
unqualified.

With explicit owner approval, `/var/lib/sage-lantern/sage-personal` was deleted
on A and B, including their native test memories, vault keys and agent identities.
All installed legacy `sage-lantern-*.service` units are stopped and inhibited by
`/etc/sage-lamplighter-lab/enabled`. The separate seed service was restarted and
verified after deletion. This was a scoped native-store deletion, not a whole-disk
sanitization: other retired endpoint files and historical bench evidence remain.
SSH, radio firmware and the public pack caches remain in use.

No more A/B personal-endpoint/model/UI acceptance is required before starting
the ROCK product fleet. Use A/B for support-system tests as the Orange Pi and
ROCK units are brought up; validate the actual user experience, the single-radio
baseline, GPS/WiFi and fresh identities on real end-user hardware.

See [Lamplighter lab](LAMPLIGHTER_AB_LAB.md), [current handoff](SESSION_HANDOFF_20261019.md),
[unit provisioning](REAL_UNIT_PROVISIONING.md) and
[peripheral bindings](CF_PERIPHERAL_BINDINGS.md).
