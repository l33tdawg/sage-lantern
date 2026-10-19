# Offline maps for SAGE Lantern

Lantern ships with a world overview that works without a network. Detailed
streets arrive as separate packs, because a planet of street-level data does
not fit on the device. Each pack covers one country or city, carries a
manifest, and is checked against its SHA-256 before installation.

| Pack | ID | Coverage | Zoom | Size |
|---|---|---|---|---|
| Penang | `penang` | `100.20,5.20,100.50,5.50` | 0-13 | 2,766,338 B |
| Malaysia | `malaysia-z11` | `99.6,0.85,119.3,7.4` | 0-11 | 18,793,137 B |
| Klang Valley | `klang-valley-z13` | `101.30,2.90,101.90,3.50` | 0-13 | 10,755,117 B |
| Singapore | `singapore-z12` | `103.60,1.15,104.05,1.44` | 0-12 | 2,778,893 B |
| Johor Bahru | `johor-bahru-z13` | `103.55,1.44,103.95,1.78` | 0-13 | 3,953,605 B |

The machine-readable list is [catalog.json](catalog.json). It holds the byte
count, hash and download URLs for every pack.

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

An RF-only hop is the last resort. At the measured 2 kB/s, a 10 MB pack takes
about 90 minutes on that hop, and the transfer has to be scheduled so it does
not block other traffic. Meshticulum can make that reliable and resumable. It
cannot make the radio faster.

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

Map data © OpenStreetMap contributors, available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/).
Vector tiles are produced with [OpenMapTiles](https://openmaptiles.org/),
© OpenMapTiles, CC-BY 4.0. The world overview uses Natural Earth 1:110m, which
is public domain.

Keep the attribution visible in the map view. These packs contain no Google
tiles or cached Google data.

## Limits

- Country packs stop at z11 or z12. City packs stop at z13. There is no z15
  street detail.
- A pack covers its bounding box. Panning outside it falls back to the world
  overview.
- Catalog and manifest signatures are not implemented yet. The current check is
  SHA-256 from the catalog plus transport trust. Signed metadata belongs with
  the trust-root work.
