# Lantern offline map pack

Bounded OpenStreetMap → PMTiles pipeline. The pack is local, attributed and
size-checked before it is built. No Google tiles or offline Google cache.

## Plan first

```sh
python3 software/gnome/maps-pack/build_pack.py \
  --bbox 101.30,2.90,101.90,3.50 \
  --zoom 9-13 \
  --name "Klang Valley" \
  --output /var/tmp/klang-valley.pmtiles \
  --plan
```

Defaults: 12 KB per tile, 4 GiB maximum pack, 6 GiB minimum free disk after
the build. The plan rejects an over-budget region or a pack that would breach
the free-space floor.

## Build

Requires local `osmium`, `tilemaker` and `pmtiles` binaries plus a local
`.osm.pbf` input. This command never downloads data:

```sh
python3 software/gnome/maps-pack/build_pack.py \
  --bbox 101.30,2.90,101.90,3.50 \
  --zoom 9-13 \
  --name "Klang Valley" \
  --output /var/tmp/klang-valley.pmtiles \
  --input-osm /var/tmp/region.osm.pbf \
  --source "Geofabrik Malaysia" \
  --source-date 2026-10-19 \
  --build
```

The manifest is written next to the pack as `<name>.pmtiles.manifest.json` and
records bbox, zoom, tile count, bytes, SHA-256, source, source date, tool
versions, attribution and ODbL licence.

## Install

```sh
sudo install -D -m 0644 /var/tmp/klang-valley.pmtiles \
  /opt/sage-lantern/maps/klang-valley.pmtiles
sudo install -D -m 0644 /var/tmp/klang-valley.pmtiles.manifest.json \
  /opt/sage-lantern/maps/klang-valley.pmtiles.manifest.json
```

Keep the pack and manifest together. Do not overwrite a verified pack without a
reviewed backup.

## Guardrails

- OpenStreetMap data © OpenStreetMap contributors, ODbL 1.0.
- No Google tiles, no Google caching, no redistribution of Google map data.
- No directory globs; the output path is explicit.
- The planner refuses an over-budget pack or one that would leave less than the
  configured free-space floor.

## Tests

```sh
.venv/bin/pytest software/gnome/maps-pack/test_build_pack.py -q
```
