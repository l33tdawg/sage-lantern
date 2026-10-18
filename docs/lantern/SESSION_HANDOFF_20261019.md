# Lantern / SAGE fresh-session handoff — 19 October 2026

This is the current handoff for a fresh session. The SAGE task index points
here for detail; it does not replace the individual task records.

## Unit C is live

| Service | State |
|---|---|
| `sage-lantern-llm.service` | active, port 8081, V81 `ac7021b6…`, 16k/8192 |
| `sage-lantern-voice-loop.service` | active |
| `gpsd` | active on the L76K `/dev/ttyS6` |
| `sage-lantern-gnss-status.service` | active, loopback 8090 |
| `sage-lantern-maps.service` | active, loopback 8091, `/packs.json` |

GNSS bridge `status_bridge.py` `e414bdf6…`; map server `serve_pack.py`
`de2f1e56…`; Maps page `maps.html` `2c5ad4d8…`. The local Penang pack is
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

The antenna is still **mode 1, 0 satellites, HDOP 25.5**. No fix or position
claim exists. The privacy toggle is off by default; `/status?position=1`
returns no coordinates without a fresh mode 2/3 fix.

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

14 assigned open tasks remain:

- **4c25d4a3** — Lantern/SAGE desktop mode: physical panel, touch, CEREBRUM
  and landscape acceptance still open.
- **a65d8a51** — GNSS/maps: bridge, live UI and Penang pack shipped; antenna
  fix, physical panel and map-shipping tier decision remain.
- **122f430a** — Meshticulum: other session; 1 MB rerun needs a fresh window.
- **d410e922** — Lantern 3B: V81 shipped; weights next step needs independent
  preference labels.
- **21647cae** — C-native qualification: protected backend, lifecycle,
  consent/session, D-022 and D/E/F gates remain.
- **131d5018** — Conversation product: installed owner-session UI, private
  SAGE, ASR/voice, router and streaming acceptance remain.
- **7c72ccdf** — Mail/domain: owner password-manager, contact, redirect and
  Cloudflare decisions remain.
- **92680e1c** — G/H hardware: panel adapter, 5V lead, IMX415 modules and
  connector checks remain.
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

1. Connect/qualify the GNSS antenna and capture a real mode 2/3 fix; then
   physically verify the 1560×720 panel, touch/rotation, CEREBRUM, Sky View and
   Return to Portrait.
2. Build the Klang Valley metro pack (z13/14) and the Singapore/Johor pair
   through the installed pack manager; then rehearse an SD/USB sideload.
3. Continue C-native and conversation-product acceptance.
4. Resume weights only with independent preference labels or a different base.

## Guardrails

- No Google tiles or Google offline cache; OSM/ODbL/OpenMapTiles attribution.
- Never expose gpsd or the map server on the LAN; both are loopback-only.
- Never return coordinates without a fresh mode 2/3 fix; no replay/no-fix
  position claims.
- Keep the `sage-lantern` monorepo private; the model-only repo is the public
  boundary.
- Do not deploy a model adapter unless it clears the open panel and the full
  168-prompt regression.
