# Lantern offline map coverage and shipping budget

Decision record, 19 October 2026. This sets the pack tiers, the initial
coverage list and the install contract. It does not claim that any unmeasured
pack is built.

## Decision

Ship a **tiered** offline map, not a global street-level dataset. A planet-wide
street network does not fit the unit's disk, and nobody needs z15 detail for a
continent they are not standing in. Four tiers cover the product:

| Tier | Coverage | Zoom | Where it lives |
|---|---|---|---|
| 0 | Whole world | z0–6 | Vendored Natural Earth 110m GeoJSON, always on the image |
| 1 | Country | z0–11/12 | PMTiles pack, installed at provisioning |
| 2 | Region / metro | z13–14 | PMTiles pack, installed per unit |
| 3 | Deep detail | z15+ | Sideload from SD/USB; never assumed present |

Tier 0 is not a PMTiles pack. It is four small GeoJSON files under
`software/gnome/desktop/world/` (land, countries, lakes, rivers; about 0.4 MB
total) from Natural Earth 1:110m, which is public domain. That gives every unit
a recognisable globe even before a single region is installed, and it keeps the
world view independent of the regional pack's bbox.

## Measured sizes

| Pack | Coverage | Zoom | Bytes | Tiles |
|---|---|---|---:|---:|
| Penang | `100.20,5.20,100.50,5.50` | 0–13 | 2,766,338 | 90 |
| Malaysia | `99.6,0.85,119.3,7.4` | 0–11 | 18,793,137 | 5,970 |
| Malaysia | `99.6,0.85,119.3,7.4` | 0–12 | 44,260,241 | 21,023 |
| Klang Valley | `101.30,2.90,101.90,3.50` | 0–13 | 10,755,117 | 282 |
| Singapore | `103.60,1.15,104.05,1.44` | 0–12 | 2,778,893 | 49 |
| Johor Bahru | `103.55,1.44,103.95,1.78` | 0–13 | 3,953,605 | 138 |
| World base | global | 0–6 | about 425,000 | n/a (GeoJSON) |

The z0–11 Malaysia build is the country-tier pack: 18.8 MB for 5,970 tiles,
verified and installed on C on 19 October 2026. The z0–12 build (44.3 MB) is
retained as a comparison; it roughly doubles every zoom-12 tile that a country
pack rarely needs.

## Budget

- Reserve **32 GB** in the image for map packs and sideload staging.
- Enforce a **6 GiB free-space floor** after every install
  (`pack_manager.py install --min-free-bytes`).
- Cap a single pack at 4 GiB at build time (`build_pack.py --max-bytes`).
- Keep the world base in the image; never make it a removable pack, because the
  map must still render when no region is installed.

## Initial coverage list

The owner's working region first, then the places a demo or neighbour will
plausibly ask for:

1. **Malaysia** — country tier, z0–11. Build from the current Geofabrik
   Malaysia–Singapore–Brunei extract. Built and installed 19 October 2026.
2. **Penang** — metro tier, z0–13. Already built and installed.
3. **Klang Valley** — metro tier, z0–13 (Kuala Lumpur, Putrajaya, Selangor
   core). Built and installed 19 October 2026.
4. **Singapore** — country/city tier, z0–12, so the Johor crossing has context
   on both sides. Built and installed 19 October 2026.
5. **Johor Bahru** — metro tier, z0–13, paired with Singapore. Built and
   installed 19 October 2026.

Everything else is sideloaded on request. A unit is not shipped with a
continent it has no owner for.

## Pack contract

Every pack is a flat pair in the served root:

```
<name>.pmtiles
<name>.pmtiles.manifest.json
```

The manifest records the schema, name, bbox, zoom range, tile count, byte
count, SHA-256, source, source date, tool versions, attribution and licence.
`pack_manager.py` is the only supported install/remove path:

- `list` inventories packs, flags missing or invalid manifests and orphan
  manifests.
- `verify` checks the PMTiles magic, byte count and SHA-256.
- `install` verifies the source, checks free space, copies through a temporary
  file, re-verifies the hash and renames atomically. An existing pack is
  refused unless `--replace` is passed, and `--replace` moves the old pair into
  `.backups/<UTC timestamp>/` first.
- `remove` also moves the pair into a timestamped backup; it never deletes.

The loopback server exposes `GET /packs.json` with the installed pack metadata.
The Maps page reads it, picks a pack whose bbox contains the default centre,
and falls back to the smallest installed pack. Installing a pack needs no
service restart; reloading the page picks it up.

## Update path

1. Build the pack on the Mac with Planetiler or the `build_pack.py` pipeline.
2. Run `pack_manager.py verify` on the build output.
3. Copy the pack and manifest to the unit (SSH or SD/USB).
4. Run `pack_manager.py install` on the unit.
5. Reload Maps and confirm the pack name, size and attribution.

Signing and automatic delivery are **not** part of this increment. Manifest
authenticity is currently a transport/operator responsibility; the signed
trust-root channel is task `30741772`.

## Public distribution

The `sage-lantern` repository is public as of 19 October 2026. Map packs ship as
release assets with a catalog at `maps/catalog.json`. A unit can install one by
ID:

```sh
python3 software/gnome/maps-pack/pack_manager.py fetch \
  --catalog https://raw.githubusercontent.com/l33tdawg/sage-lantern/main/maps/catalog.json \
  --id malaysia-z11
```

The fetcher downloads the manifest and pack, verifies both against the catalog,
and installs through the same checked path as a local file. It refuses plain
HTTP, an unknown ID, a size above the catalog entry, a manifest that disagrees
with the catalog, and any hash mismatch.

Transport choice matters more than the catalog format:

- **HTTPS** is the default when the unit has internet.
- **A LAN peer** that already holds the pack is faster than a fresh download.
- **Removable media** carries the same pair when the unit is offline.
- **Meshticulum** can route the transfer through a node with an uplink, so the
  bytes take an IP path. The catalog itself is small enough for a radio link.
  An RF-only hop is the last resort. Around 90 minutes for a 10 MB pack is a
  planning extrapolation from the measured 2 kB/s, not an accepted map-transfer
  result, and it has to be scheduled around other traffic.

The catalog and manifests are not signed yet. The SHA-256 check catches
corruption and a swapped asset; it does not establish who published it. Signed
metadata belongs with the trust-root work.

## Sideload rehearsal

On 19 October 2026 the install path was rehearsed on C from a staging directory
standing in for removable media:

- a 1 TB free-space floor refused the install with exit 2 and left no file;
- a corrupted `broken.pmtiles` was refused as not a PMTiles archive;
- both Singapore and Johor Bahru installed from staging and verified;
- reinstalling Singapore without `--replace` was refused;
- reinstalling Johor Bahru with `--replace` moved the old pair to
  `.backups/20261019T000531Z/`;
- `/packs.json` showed all five packs live with the same service PID, so no
  restart was needed.

This proves the software path, not physical SD/USB media. A real card or stick
still needs one pass on the unit.

## Guardrails

- No Google tiles or Google offline cache.
- Keep OSM/ODbL and OpenMapTiles attribution visible; the world base credits
  Natural Earth as public domain.
- The map server and gpsd stay loopback-only.
- Do not delete a verified pack; replace or remove it through the manager's
  backup path.
- Do not describe the world base as street-level coverage, and do not describe
  a country pack as metro detail.
