"""Plan and build a bounded OpenStreetMap PMTiles pack for Lantern."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_MAX_BYTES = 4 * 1024**3
DEFAULT_MIN_FREE = 6 * 1024**3
DEFAULT_BYTES_PER_TILE = 12_000
ATTRIBUTION = "© OpenStreetMap contributors, ODbL 1.0"


def parse_bbox(value):
    parts = [float(item) for item in value.split(",")]
    if len(parts) != 4:
        raise ValueError("bbox must be minlon,minlat,maxlon,maxlat")
    minlon, minlat, maxlon, maxlat = parts
    if not (-180 <= minlon < maxlon <= 180 and -85.0511 <= minlat < maxlat <= 85.0511):
        raise ValueError("bbox out of range or inverted")
    return tuple(parts)


def parse_zoom(value):
    match = value.split("-")
    if len(match) not in (1, 2):
        raise ValueError("zoom must be N or N-M")
    low = int(match[0])
    high = int(match[-1])
    if not (0 <= low <= high <= 22):
        raise ValueError("zoom must be 0..22 and ordered")
    return low, high


def _lon_tile(lon, zoom):
    return int((lon + 180.0) / 360.0 * 2**zoom)


def _lat_tile(lat, zoom):
    lat = math.radians(lat)
    return int((1.0 - math.asinh(math.tan(lat)) / math.pi) / 2.0 * 2**zoom)


def tile_count(bbox, zoom_range):
    minlon, minlat, maxlon, maxlat = bbox
    low, high = zoom_range
    total = 0
    for zoom in range(low, high + 1):
        x0, x1 = _lon_tile(minlon, zoom), _lon_tile(maxlon, zoom)
        y0, y1 = _lat_tile(maxlat, zoom), _lat_tile(minlat, zoom)
        total += (x1 - x0 + 1) * (y1 - y0 + 1)
    return total


def plan_pack(bbox, zoom_range, bytes_per_tile=DEFAULT_BYTES_PER_TILE,
              max_bytes=DEFAULT_MAX_BYTES, free_bytes=None,
              min_free_bytes=DEFAULT_MIN_FREE):
    tiles = tile_count(bbox, zoom_range)
    estimated = tiles * bytes_per_tile
    if estimated > max_bytes:
        raise ValueError(f"estimated {estimated} bytes exceeds cap {max_bytes}")
    if free_bytes is not None and free_bytes - estimated < min_free_bytes:
        raise ValueError(
            f"estimated pack leaves less than {min_free_bytes} bytes free")
    return {
        "bbox": list(bbox),
        "zoom": list(zoom_range),
        "tiles": tiles,
        "bytes_per_tile": bytes_per_tile,
        "estimated_bytes": estimated,
        "max_bytes": max_bytes,
        "free_bytes": free_bytes,
        "min_free_bytes": min_free_bytes,
    }


def require_tools(names=("osmium", "tilemaker", "pmtiles")):
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise ValueError("missing required tools: " + ", ".join(missing))
    return {name: shutil.which(name) for name in names}


def tool_version(name):
    try:
        result = subprocess.run([name, "--version"], capture_output=True, text=True,
                                timeout=10, check=False)
    except OSError:
        return None
    text = (result.stdout or result.stderr or "").strip().splitlines()
    return text[0] if text else None


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_manifest(output, plan, name, source, source_date, tools):
    return {
        "schema": "sage.lantern.offline-map-pack.v1",
        "name": name,
        "bbox": plan["bbox"],
        "zoom": plan["zoom"],
        "tiles": plan["tiles"],
        "bytes": output.stat().st_size,
        "sha256": sha256(output),
        "source": source,
        "source_date": source_date,
        "tools": tools,
        "attribution": ATTRIBUTION,
        "license": "ODbL 1.0",
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }


def build(args):
    bbox = parse_bbox(args.bbox)
    zoom = parse_zoom(args.zoom)
    output = Path(args.output).resolve()
    if output.exists() and not args.force:
        raise SystemExit("output exists; use --force to replace")
    if not output.parent.is_dir():
        raise SystemExit("output parent directory does not exist")
    free = shutil.disk_usage(output.parent).free
    plan = plan_pack(bbox, zoom, args.bytes_per_tile, args.max_bytes, free,
                     args.min_free_bytes)
    print(json.dumps({"plan": plan}, indent=2))
    if args.plan:
        return
    tools = require_tools()
    input_osm = Path(args.input_osm).resolve(strict=True)
    with tempfile.TemporaryDirectory(prefix="lantern-map-", dir=output.parent) as tmp:
        extract = Path(tmp) / "extract.osm.pbf"
        mbtiles = Path(tmp) / "map.mbtiles"
        subprocess.run([
            tools["osmium"], "extract", "--overwrite", "--bbox",
            ",".join(str(v) for v in bbox), input_osm, "-o", extract,
        ], check=True)
        subprocess.run([
            tools["tilemaker"], "--input", extract, "--output", mbtiles,
        ], check=True)
        subprocess.run([tools["pmtiles"], "convert", mbtiles, output], check=True)
    versions = {name: tool_version(name) for name in tools}
    manifest = build_manifest(output, plan, args.name, args.source,
                              args.source_date, versions)
    manifest_path = output.with_suffix(output.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps({"manifest": str(manifest_path), "bytes": manifest["bytes"],
                      "sha256": manifest["sha256"]}, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True,
                        help="minlon,minlat,maxlon,maxlat")
    parser.add_argument("--zoom", required=True, help="N or N-M")
    parser.add_argument("--name", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--input-osm", help="local .osm.pbf for --build")
    parser.add_argument("--source", default="OpenStreetMap")
    parser.add_argument("--source-date", default="")
    parser.add_argument("--bytes-per-tile", type=int, default=DEFAULT_BYTES_PER_TILE)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--min-free-bytes", type=int, default=DEFAULT_MIN_FREE)
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    if not (args.plan or args.build):
        parser.error("choose --plan or --build")
    if args.build and not args.input_osm:
        parser.error("--build requires --input-osm")
    build(args)


if __name__ == "__main__":
    main()
