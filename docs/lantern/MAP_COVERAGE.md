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
   on both sides.
5. **Johor Bahru** — metro tier, z0–13, paired with Singapore.

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

## Guardrails

- No Google tiles or Google offline cache.
- Keep OSM/ODbL and OpenMapTiles attribution visible; the world base credits
  Natural Earth as public domain.
- The map server and gpsd stay loopback-only.
- Do not delete a verified pack; replace or remove it through the manager's
  backup path.
- Do not describe the world base as street-level coverage, and do not describe
  a country pack as metro detail.
