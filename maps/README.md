# Offline maps for SAGE Lantern

**Lantern Offline Maps** provides regional OpenStreetMap packs for SAGE Lantern.
The device ships with a world overview that works without a network. Detailed
streets arrive as separate packs, because a planet of street-level data does
not fit on the device. Each pack covers a selected regional area, carries a
manifest, and is checked against its SHA-256 before installation.

| Pack | ID | Bounding box | Zoom | Size |
|---|---|---|---|---|
| Penang | `penang` | `100.20,5.20,100.50,5.50` | 0-13 | 2.77 MB |
| Malaysia | `malaysia-z11` | `99.6,0.85,119.3,7.4` | 0-11 | 18.79 MB |
| Klang Valley | `klang-valley-z13` | `101.30,2.90,101.90,3.50` | 0-13 | 10.76 MB |
| Singapore | `singapore-z12` | `103.60,1.15,104.05,1.44` | 0-12 | 2.78 MB |
| Johor Bahru | `johor-bahru-z13` | `103.55,1.44,103.95,1.78` | 0-13 | 3.95 MB |

The machine-readable list is [catalog.json](catalog.json). It holds the exact
byte count, hash and download URLs for every pack. The bounding box is the pack
coverage; it is not a claim of complete administrative coverage.

## Install a downloaded pack

Download the `.pmtiles` file and its matching `.pmtiles.manifest.json` from the
release. Then run this from the repository root:

```sh
python3 software/gnome/maps-pack/pack_manager.py install malaysia-z11.pmtiles \
  --manifest malaysia-z11.pmtiles.manifest.json
```

The installer checks the PMTiles header, byte count and hash. It refuses to
overwrite an installed pack unless you pass `--replace`, and a replace first
moves the old pair into a timestamped backup directory.

## Fetch by ID

```sh
python3 software/gnome/maps-pack/pack_manager.py fetch \
  --catalog https://raw.githubusercontent.com/l33tdawg/sage-lantern/main/maps/catalog.json \
  --id singapore-z12
```

`fetch` downloads the manifest and pack, verifies both against the catalog,
and installs them through the same checked path.

## Choosing a transport

The catalog is small. The packs are not. Send the catalog over whatever link is
cheap, then move the bytes over the best path available:

1. Direct HTTPS download when the unit has internet.
2. A LAN peer that already holds the pack.
3. Removable media when the unit is offline.
4. A Meshticulum network with an uplink. One node fetches the pack and serves it
   to the others over IP.

An RF-only hop is the last resort. Around 90 minutes for a 10 MB pack is a
planning extrapolation from a finite measured rate, not an accepted map-transfer
result. The transfer has to be scheduled so it does not block other traffic.
Meshticulum can make it reliable and resumable. It cannot make the radio faster.

## Build another region

The pipeline lives in `software/gnome/maps-pack/`. It takes a local `.osm.pbf`,
a bounding box and a zoom range, then writes a PMTiles pack and a manifest.

```sh
python3 software/gnome/maps-pack/build_pack.py \
  --bbox 101.30,2.90,101.90,3.50 \
  --zoom 0-13 \
  --name "Klang Valley" \
  --output /var/tmp/klang-valley.pmtiles \
  --input-osm /var/tmp/malaysia.osm.pbf \
  --build
```

Keep packs bounded. Malaysia at z0-11 is 18.8 MB. The same country at z0-12 is
44.3 MB, and a full planet of street detail will not fit on the unit.

## Attribution and licence

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright),
licensed under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
These packs use the [OpenMapTiles](https://openmaptiles.org/) schema; its
cartographic design is licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). The Natural Earth
world overview is public domain.

Keep "© OpenMapTiles © OpenStreetMap contributors" visible in the map view and
with map screenshots. These packs contain no Google tiles or cached Google data.

## Limits

- Country packs stop at z11 or z12. City packs stop at z13. There is no z15
  street detail.
- A pack covers its bounding box. Panning outside it falls back to the world
  overview.
- Catalog and manifest signatures are not implemented yet. The current check is
  a checksum against metadata from the catalog plus transport trust. It is not
  publisher authentication. Signed metadata belongs with the trust-root work.
