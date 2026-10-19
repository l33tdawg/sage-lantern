# Lantern / SAGE fresh-session handoff — 19 October 2026

This is the current handoff for a fresh session. The SAGE task index points
here for detail; it does not replace the individual task records.

## Pick up here

The public surface is now in place. `l33tdawg/sage-lantern` is public, with the
README header, Foundation mission, project-family table, hardware/build/boot
docs, maps catalog and release assets. The profile README and the GitHub Pages
hub both link to this repository. The product repo links to `lantern-3b` and the
model repo links back.

The SAGE provisioning-profile migration is installed and live on Unit C as
release `lantern-cf-20261019-sage112327-r24`, manifest SHA-256
`7795632965ce28ab1bc42fab90a249e43c798da6496cf5be4094c9da6b5d3e15`.
SAGE is now **11.23.27**, binary SHA-256
`7f0660ec4cc09ba21290eb29950ccaad6afb84c7bda6179c1d27771b420edb7a`.
`/etc/sage/provisioning-profile.yaml` is root-owned mode 0600. The service keeps
`NoNewPrivileges=true` and receives only `CAP_DAC_READ_SEARCH` so the non-root
account can load that policy profile. All SAGE listeners are loopback-only.
Evidence and rollback paths are in
`work/sage-provisioning-profile-migration-20261019/`.

The PR stack is settled:

- PR 1 and PR 2 are superseded by PR 3 and should close.
- PR 3, `codex/transport-qualification-20261014`, now includes main through
  merge `99b5e15`. The storage-epoch conflict is resolved by D-027:
  `verified_epoch()` stays uncached for the six native forward-secrecy fences,
  while `admission_epoch()` carries main's one-second reuse for capture leases
  and hot admission checks.
- Native proof for the merge: 180 storage/daemon tests, 34 fence tests and 298
  forward native owner/provider/relay/bound-pair tests. Remaining integration
  failures are sandbox IP-bind and external-service limits.

Hardware and device waits:

**Fleet plan update (19 October, owner):** the main Lantern fleet is four
ROCK 5B+ units — the two 12 GB boards in hand plus **two more 16 GB boards** —
and two Orange Pi 5 Plus bench/development units. Six is a planning number,
not a requirement. The Orange Pi units no longer need internal DSI/CSI
routing, internal 5 V panel power, final enclosure fit or final
panel/touch/thermal acceptance. The two ROCK 5B 8 GB boards remain the A/B
Meshticulum relay units. D-028 records this. The four AC005 adapters already
ordered are exactly right; remaining ROCK-unit gaps are two 16 GB boards,
two coolers, two more DSI panels (four total) and four **256 GB** NVMe drives.
The owner directed four **Radxa Camera 4K IMX415** modules for the ROCK units;
they are in the Arace cart at RM205 each (RM820 subtotal, checkout pending).
The IMX519-7B modules remain bench/experimental only. LoRa HAT procurement is
deferred — spare HATs are in hand and they are cheap/readily available later.

- The product radio baseline is one LoRa radio per unit. Meshticulum carries
  messages and resource transfers over that single half-duplex radio. Extra
  radios are bench comparison inventory only.
- Flint has the open questions on the correct DSI panel adapter, the physical
  Orange Pi count and the 30-to-22 IMX519 CSI adapter.
- The GNSS antenna is still disconnected. No fix exists. The physical panel,
  touch, CEREBRUM, Sky View and sleep/wake checks still need a person at C.

The settlement-owner source milestone is complete in
`software/security/desktop_loader_candidate/`. Next is wiring it into the actual
launch authority and repeating the native fault replay on C. Nothing is
installed or accepted.

## Unit C is live

| Service | State |
|---|---|
| `sage-lantern-llm.service` | active, port 8081, V81 `ac7021b6…`, 16k/8192 |
| `sage-lantern-personal-sage.service` | active, SAGE 11.23.27, profile-gated, loopback-only |
| `sage-lantern-voice-loop.service` | active |
| `gpsd` | active on the L76K `/dev/ttyS6` |
| `sage-lantern-gnss-status.service` | active, loopback 8090 |
| `sage-lantern-maps.service` | active, loopback 8091, `/packs.json` |

GNSS bridge `status_bridge.py` `e414bdf6…`; map server `serve_pack.py`
`de2f1e56…`; Maps page `maps.html` `c9cb51ca…`. The local Penang pack is
`/opt/sage-lantern/maps/penang.pmtiles` — 90 tiles, 2,766,338 bytes,
SHA-256 `15d9a3ec…`, with a manifest. MapLibre GL 4.7.1 and PMTiles JS 3.2.1
are vendored locally; no Google tiles.

The Maps page now opens on a **Natural Earth 1:110m world base** (land,
countries, lakes, rivers; 424 KB of script-wrapped data under
`/usr/share/sage-lantern/desktop/world/`) and overlays the installed regional
pack from zoom 7. The base is script-wrapped because the unit launches Maps as
a `file://` app, where Chromium refuses `fetch()` of local GeoJSON. The page
reads `/packs.json` for the pack name, size and zoom range, so installing a
pack needs no page or service restart. `pack_manager.py` `2670501c…` is
installed at `/opt/sage-lantern/maps-pack/` for verified list/install/remove
with timestamped backups.

The **Malaysia country pack** is built and installed: z0–11, 5,970 tiles,
18,793,137 bytes, SHA-256 `6764fe59…`, Planetiler 0.10.2, Geofabrik source
SHA-256 `bc65fdec…`. The pack manager selected it for a Kuala Lumpur view and
the page rendered at 1560×720 on C. Penang remains the preferred pack when the
view is inside its smaller bbox.

The **Klang Valley metro pack** is built and installed: z0–13, 282 planned
tiles, 10,755,117 bytes, SHA-256 `a87385b5…`. A Kuala Lumpur view at z12
selected Klang Valley over Malaysia and rendered street detail. Unit C's map
directory now holds five packs: Penang, Malaysia, Klang Valley, Singapore
(z0–12, 2,778,893 bytes) and Johor Bahru (z0–13, 3,953,605 bytes). Pack
payloads total 39,047,090 bytes; the directory is 43,004,621 bytes including
manifests and one replace backup. Singapore at z11 and Johor Bahru at z12 both
selected their own packs and rendered on C.

The sideload path was rehearsed from a staging directory: free-space refusal,
corrupt-pack refusal, no-overwrite refusal, install, and replace-with-backup
all behaved, and `/packs.json` updated without a service restart (PID
unchanged). Evidence: `work/lantern-maps-20261019/SIDELOAD_REHEARSAL_20261019.json`.
Physical SD/USB media is still untested.

The `sage-lantern` repository is now public. The five packs are published as
release assets under tag `maps-20261019`, with a catalog at `maps/catalog.json`.
`pack_manager.py fetch --catalog <raw URL> --id <pack>` downloads, verifies and
installs a pack. The catalog is small enough to advertise over a radio link;
the bytes should take HTTPS, a LAN peer or removable media. A Meshticulum node
with an uplink can carry the transfer over IP.

Public documentation added on 19 October: `hardware/` (overview, BOM and the
STL folder), `docs/build/` (build, assembly and software), `docs/boot/` (boot,
first boot and recovery), `docs/index.md`, and `assets/` for the README
visuals. Wick reviewed the map copy: use "Lantern Offline Maps", decimal MB,
bounding-box coverage, checksum not signature, and HTTPS/LAN/USB before radio.
Flint supplied the current hardware state: the ROCK 5B+ tub finished 263/263,
the firmer bezel 199/199, and the back cover 89/89; the V2 collar and gasket
trials are still open. C-F Orange Pi physical count needs reconciliation, and
the G/H boards were owner-reported arrived by 9 October but are not brought up.

Hardware reconciliation on 19 October found two adapter problems:
`hardware/RECONCILIATION_20261019.md`. Radxa's 5B/5B+ interface page lists 41
numbered DSI positions while the 8HD accessory page specifies a 39-pin
board-side cable. AC012 is 39-to-40 for the Radxa 8HD, not for the Waveshare
22-pin panel. The correct panel adapter is **AC005** (39P 0.3 mm to 15P
1.0 mm). Camera update (19 October): the owner initially identified a 16 MP
autofocus IMX519 (label `IMX519-7B`; Waveshare `IMX519-78` family) in hand,
but after reviewing the ROCK driver risk directed four **Radxa Camera 4K
IMX415** modules for the four ROCK units. Four are in the Arace cart at RM205
each (RM820 subtotal, checkout pending). The IMX519-7B modules remain
bench/experimental only; they are a Pi libcamera module with no official ROCK
5B+ driver/overlay. The Orange Pi 5 Plus CSI connector is a 30-pin mezzanine
and the IMX519 is a 22-pin camera, so C-F bench use also needs a 30-to-22
adapter with I2C level shifting. Unit C is confirmed as an Orange Pi 5
Plus with 8 cores, 15 GiB, a Netac 256 GB NVMe, USB audio, a C270 and an open
GNSS receiver.

C-native: the disabled settlement contract is now in
`software/security/desktop_loader_candidate/lifecycle_settlement.py`, with
11 source tests. It binds settlement to the launch UUID and exact owned
resources, requires a terminal pidfd or child observation, and returns
`uncertain` for missing, extra, non-terminal or digest-mismatched evidence.
`settlement_journal.py` adds a one-record-per-obligation CAS journal with
idempotent attempts and settlements, readback and unknown-write recovery,
covered by 10 more tests. `FileJournalBackend` adds a private mode-0700
file-backed store with atomic replace and fsync. `native_probe.py` defines the
future probe interface, and `settlement_fault_replay.py` produced 11 cases in
`docs/lantern/evidence/SETTLEMENT_FAULT_REPLAY_20261019.json`. Nothing is
installed on C. `linux_probe.py` implements the procfs probe (pidfd signal 0,
cgroup.events, unix socket table, mountinfo, descriptor state) against a
synthetic root, and an end-to-end probe-to-journal settlement passes. A
disposable `sleep` child was probed on C: proof true, journal `attempted` to
`settled` at revision 1, evidence
`docs/lantern/evidence/LINUX_PROBE_CHILD_C_20261019.json`. Integration with the
actual launch authority and native fault replays remain open.
`settlement_owner.py` is the disabled owner facade: it binds a release, records
the attempt and settles through the probe and journal. It has no service
wiring.

The hardware inventory is now repeatable with
`scripts/lantern/hardware_inventory.py`; the Unit C output is at
`docs/lantern/evidence/HARDWARE_INVENTORY_C_20261019.json`. The bench fields to
fill for the board count, DSI/CSI contacts and G/H bring-up are in
`hardware/PHYSICAL_CHECKLIST.md`.

The product radio baseline is **one LoRa radio per unit**. Meshticulum carries
messages and resource transfers over that single half-duplex radio. Earlier
dual-radio plans and the XIAO/Wio kits are bench comparison inventory, not a
second radio installed by default. The public README, DEVICE_ROLES, HARDWARE_BOM
and bill of materials were corrected on 19 October.

Rechecked on C at 2026-10-19 17:53 MYT: gpsd is active on `/dev/ttyS6`
(u-blox, 9600 baud) and the status bridge returns **mode 1, 0 satellites,
HDOP 25.5**, with raw NMEA carrying `$GPTXT ... ANTENNA OPEN`. The owned
1 x TAN1216Q23 GNSS SMA-male helix is not fitted. No fix or position claim
exists. The privacy toggle is off by default; `/status?position=1` returns no
coordinates without a fresh mode 2/3 fix.

Physical C acceptance still needs a person at the unit: connect the GNSS
helix, place C with sky view, unlock the `sage` session on tty2, then check
the 1560x720 landscape panel at all four corners and centre, Back/drawer,
CEREBRUM, Maps, GNSS Sky View, Return to Portrait/D-022, and one sleep/wake
and reboot cycle. The compositor currently sits at the GDM greeter on tty1;
the `sage` Wayland session on tty2 is online but inactive. Every DRM
connector now reports disconnected (`card0-HDMI-A-1`, `card0-HDMI-A-2` and
`card0-DP-1`), so the panel must be physically attached and powered before
acceptance can start. No USB touch device enumerates on C either, so check the
panel's USB power/touch lead and the panel itself before replacing the HDMI
cable.

## Map shipping design

Measured with Planetiler:

| Pack | Coverage | Size |
|---|---|---:|
| Penang | `100.20,5.20,100.50,5.50`, z0–13 | 2.6 MB / 90 tiles |
| Malaysia | `99.6,0.85,119.3,7.4`, z0–12 | 44,260,241 bytes / 21,023 tiles |

The decision is recorded in `docs/lantern/MAP_COVERAGE.md`: tiered packs, not
global street-level.

1. Global low-zoom basemap (z0–6/7), whole world.
2. Country packs at z0–11/12; Malaysia is only 42 MB.
3. Regional/metro detail at z13–15 for home regions.
4. SD/USB/companion sideload packs through `pack_manager.py`, with manifest,
   SHA-256, free-space check, atomic install and backup-first replace/remove.

Reserve 32 GB in the image, ship the world base plus priority countries, and
let the rest be installed. ODbL/OpenMapTiles attribution must remain visible.

## Disk and cleanup

Unit C is at **98G used / 125G free / 44%**. Cleanup removed old release
payloads r4–r15 and stale `/var/tmp` rook/lantern scratch. Kept r16/r17, the
15G finetune workspace, the 26G D/E/F bootstrap images, and release
metadata/checksums at `/var/backups/lantern-release-metadata-20261019`.
Evidence: `docs/lantern/evidence/CLEANUP_20261019.json`; commit `8bb6547`.

## Lantern 3B

V81 is live on C with the grounded composer (`grounded_longform.py`
`103da465…`) and cadence/label/status guards (`8b226022…`). The grounded
composer passes 20/20 on the sealed panel; tests are 95 passed + 20 subtests.
Public repo/release: `l33tdawg/lantern-3b`, bundle `c58c70ed…`, 773,583,555
bytes.

Weights are not improved yet. V81.2 SFT scored 1/12 on the fresh open-ended
panel, 63/64 sealed and 20/24 fresh64. Preference arms A/B scored 1/12 and
0/12 open, 61/64 and 63/64 sealed, 17/24 and 19/24 fresh64. No adapter was
deployed. The next weights attempt needs independent human/teacher preference
labels or a different base, not more self-generated pairs.

## Task board

14 assigned open tasks remain: one index record, **267c5475** (replacing
14fab869), and 13 engineering workstreams. The inbox is clear — 0 pending
requests, 0 task notices and 0 unfinished claims; reply watermark
`2026-10-19T01:38:14.498Z`.

- **4c25d4a3** — Lantern/SAGE desktop mode: physical panel, touch, CEREBRUM
  and landscape acceptance still open.
- **a65d8a51** — GNSS/maps: bridge, live Maps UI and the five-pack catalog
  shipped; antenna fix, physical panel and map-shipping tier decision remain.
- **122f430a** — Meshticulum: other session; 1 MB rerun needs a fresh window.
- **d410e922** — Lantern 3B: V81 research preview live on C with guards;
  weights next step needs independent preference labels.
- **21647cae** — C-native qualification: protected backend, lifecycle,
  consent/session, D-022 and D/E/F gates remain.
- **131d5018** — Conversation product: installed owner-session UI, private
  SAGE, ASR/voice, router and streaming acceptance remain.
- **7c72ccdf** — Mail/domain: owner password-manager, contact, redirect and
  Cloudflare decisions remain.
- **92680e1c** — G/H hardware: AC005/panel path, 5V lead, and the four IMX415
  cameras in the Arace cart (checkout pending); IMX519-7B is bench-only.
- **178dfca7** — Public knowledge plane: OPEN-COM-003 export schema, public
  domain/cache and content scale remain.
- **30741772** — Trust root: signed root-update channel and rotation drill.
- **7f87281f** — Launch: measured demo, evidence, licence/secret review,
  signing custody and campaign gates.
- **cd2312c4** — Messaging/discovery: C–F authenticated delivery, status
  channel and White Pages.
- **d8cca61b** — Fleet operations: inventory/audit, certificate lifecycle,
  backups/monitoring and reviewed remote commands.

## Next order

1. ~~Review, commit and push the r24 SAGE provisioning-profile, installer and
   source change.~~ Done: `5b1a8f1` on `main`.
2. Close PR 1 and PR 2 as superseded, then review the settled PR 3 merge
   (`99b5e15`). The `verified_epoch` contract is D-027.
3. Connect/qualify the GNSS antenna and capture a real mode 2/3 fix; then
   physically verify the 1560×720 panel, touch/rotation, CEREBRUM, Sky View and
   Return to Portrait. Run one physical SD/USB map install when a card is
   available.
4. Wire the settlement owner into the native launch authority and repeat the
   native fault replay on C.
5. Resolve the panel/CSI adapters and physical board count with Flint; bring up
   one G/H unit.
6. Continue conversation-product acceptance.
7. Resume weights only with independent preference labels or a different base.
8. Run the Meshticulum native/proper-bundle 1 MB and mixed-path qualification
   in a fresh bounded radio window; close PR 37 as superseded by PR 38.

## Guardrails

- No Google tiles or Google offline cache; OSM/ODbL/OpenMapTiles attribution.
- Never expose gpsd or the map server on the LAN; both are loopback-only.
- Never return coordinates without a fresh mode 2/3 fix; no replay/no-fix
  position claims.
- The public `sage-lantern` repository is now the product's public boundary.
  Keep owner credentials, private notes and internal-only material out of it.
- Do not deploy a model adapter unless it clears the open panel and the full
  168-prompt regression.
