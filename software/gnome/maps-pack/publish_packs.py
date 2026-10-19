"""Build the public map-pack catalog from verified local packs.

The catalog is the contract between the public download host and a Lantern
unit: every entry carries the pack ID, bounds, zoom range, byte count, SHA-256
and HTTPS URLs for the pack and its manifest.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
import pack_manager

CATALOG_SCHEMA = pack_manager.CATALOG_SCHEMA
validate_catalog = pack_manager.validate_catalog


def build_catalog(root, base_url) -> dict:
    root = Path(root)
    base = base_url.rstrip("/") + "/"
    packs = []
    for pmtiles in sorted(root.glob("*.pmtiles")):
        manifest_path = pack_manager.manifest_path_for(pmtiles)
        if not manifest_path.is_file():
            raise pack_manager.PackError(
                f"{pmtiles.name}: missing manifest")
        report = pack_manager.verify_pack(pmtiles, manifest_path)
        manifest = pack_manager.load_manifest(manifest_path)
        pack_id = pmtiles.name[:-len(".pmtiles")]
        packs.append({
            "id": pack_id,
            "name": report["name"],
            "bbox": report["bbox"],
            "zoom": report["zoom"],
            "tiles": report["tiles"],
            "bytes": report["bytes"],
            "sha256": report["sha256"],
            "download_url": base + pmtiles.name,
            "manifest_url": base + manifest_path.name,
            "attribution": manifest["attribution"],
            "license": manifest["license"],
        })
    if not packs:
        raise pack_manager.PackError(f"no packs found under {root}")
    return {
        "schema": CATALOG_SCHEMA,
        "updated_at": datetime.now(timezone.utc).isoformat().replace(
            "+00:00", "Z"),
        "packs": packs,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="work/lantern-maps-20261019")
    parser.add_argument("--base-url", required=True,
                        help="release download URL prefix ending in /")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        catalog = build_catalog(args.root, args.base_url)
        validate_catalog(catalog)
    except pack_manager.PackError as error:
        print(json.dumps({"ok": False, "error": str(error)}), file=sys.stderr)
        return 2
    Path(args.output).write_text(json.dumps(catalog, indent=2) + "\n",
                                 encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "packs": len(catalog["packs"]),
        "bytes": sum(pack["bytes"] for pack in catalog["packs"]),
        "output": args.output,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
