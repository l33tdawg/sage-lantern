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
python3 pack_manager.py install /var/tmp/klang-valley.pmtiles \
  --manifest /var/tmp/klang-valley.pmtiles.manifest.json \
  --root /opt/sage-lantern/maps
```

`install` verifies the PMTiles magic, byte count and SHA-256 before and after
the copy, enforces the free-space floor and renames the pair into place. It
refuses to overwrite an existing pack; `--replace` first moves the old pair to
`<root>/.backups/<UTC timestamp>/`. No service restart is needed.

Inventory, verify and retire:

```sh
python3 pack_manager.py list --root /opt/sage-lantern/maps
python3 pack_manager.py verify /opt/sage-lantern/maps/penang.pmtiles
python3 pack_manager.py remove penang.pmtiles --root /opt/sage-lantern/maps --yes
```

Fetch a published pack by ID:

```sh
python3 pack_manager.py fetch \
  --catalog https://raw.githubusercontent.com/l33tdawg/sage-lantern/main/maps/catalog.json \
  --id malaysia-z11 \
  --root /opt/sage-lantern/maps
```

Build the public catalog from verified local packs:

```sh
python3 publish_packs.py \
  --root work/lantern-maps-20261019/public \
  --base-url https://github.com/l33tdawg/sage-lantern/releases/download/maps-20261019/ \
  --output maps/catalog.json
```

The loopback server exposes `GET /packs.json`, which the Maps page uses to pick
an installed pack and show its name, size and zoom range.

Keep the pack and manifest together. Do not overwrite or delete a verified pack
outside the manager's backup path.

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
