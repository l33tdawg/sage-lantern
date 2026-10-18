"""Verify, install, inventory and retire Lantern PMTiles map packs.

Every pack is a ``.pmtiles`` file plus a sibling ``.manifest.json`` that binds
the file name, bbox, zoom range, byte count and SHA-256.  Installs are verified
before and after the copy, written through a temporary file and renamed into
place.  Replacing or removing a pack moves the old pair into a timestamped
backup directory instead of deleting it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = "sage.lantern.offline-map-pack.v1"
PMTILES_MAGIC = b"PMTiles"
DEFAULT_MIN_FREE = 6 * 1024**3
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.pmtiles$")
REQUIRED = ("schema", "name", "bbox", "zoom", "tiles", "bytes", "sha256",
            "attribution", "license")


class PackError(ValueError):
    """A pack or manifest failed validation or an operation was refused."""


def sha256(path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024**2), b""):
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(pmtiles) -> Path:
    pmtiles = Path(pmtiles)
    return pmtiles.with_name(pmtiles.name + ".manifest.json")


def load_manifest(path) -> dict:
    path = Path(path)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise PackError(f"cannot read manifest {path}: {error}") from error
    if not isinstance(manifest, dict):
        raise PackError(f"manifest {path} is not a JSON object")
    verify_manifest(manifest, path)
    return manifest


def verify_manifest(manifest: dict, source="manifest") -> None:
    missing = [key for key in REQUIRED if key not in manifest]
    if missing:
        raise PackError(f"{source}: missing fields: {', '.join(missing)}")
    if manifest["schema"] != SCHEMA:
        raise PackError(f"{source}: unsupported schema {manifest['schema']!r}")
    bbox = manifest["bbox"]
    if (not isinstance(bbox, list) or len(bbox) != 4
            or not all(isinstance(value, (int, float)) for value in bbox)
            or not (-180 <= bbox[0] < bbox[2] <= 180)
            or not (-90 <= bbox[1] < bbox[3] <= 90)):
        raise PackError(f"{source}: bbox must be minlon,minlat,maxlon,maxlat")
    zoom = manifest["zoom"]
    if (not isinstance(zoom, list) or len(zoom) != 2
            or not all(isinstance(value, int) for value in zoom)
            or not 0 <= zoom[0] <= zoom[1] <= 22):
        raise PackError(f"{source}: zoom must be [low, high] within 0..22")
    if not isinstance(manifest["tiles"], int) or manifest["tiles"] < 1:
        raise PackError(f"{source}: tiles must be a positive integer")
    if not isinstance(manifest["bytes"], int) or manifest["bytes"] < 1:
        raise PackError(f"{source}: bytes must be a positive integer")
    digest = manifest["sha256"]
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise PackError(f"{source}: sha256 must be 64 lowercase hex characters")
    if not manifest["attribution"] or "openstreetmap" not in str(
            manifest["attribution"]).lower():
        raise PackError(f"{source}: attribution must credit OpenStreetMap")
    if "odbl" not in str(manifest["license"]).lower():
        raise PackError(f"{source}: license must record ODbL")


def verify_pack(pmtiles, manifest=None) -> dict:
    """Return a verification report; raise PackError on any mismatch."""
    pmtiles = Path(pmtiles)
    manifest_path = Path(manifest) if manifest else manifest_path_for(pmtiles)
    data = load_manifest(manifest_path)
    if not pmtiles.is_file():
        raise PackError(f"missing pack file: {pmtiles}")
    with pmtiles.open("rb") as stream:
        magic = stream.read(len(PMTILES_MAGIC))
    if magic != PMTILES_MAGIC:
        raise PackError(f"{pmtiles.name}: not a PMTiles archive")
    size = pmtiles.stat().st_size
    if size != data["bytes"]:
        raise PackError(
            f"{pmtiles.name}: {size} bytes on disk, manifest says {data['bytes']}")
    digest = sha256(pmtiles)
    if digest != data["sha256"]:
        raise PackError(f"{pmtiles.name}: SHA-256 mismatch")
    return {
        "ok": True,
        "name": data["name"],
        "file": pmtiles.name,
        "bytes": size,
        "sha256": digest,
        "bbox": data["bbox"],
        "zoom": data["zoom"],
        "tiles": data["tiles"],
        "manifest": str(manifest_path),
    }


def list_packs(root) -> dict:
    root = Path(root)
    packs = []
    manifests = set()
    for pmtiles in sorted(root.glob("*.pmtiles")):
        manifest = manifest_path_for(pmtiles)
        manifests.add(manifest.name)
        entry = {"file": pmtiles.name, "bytes": pmtiles.stat().st_size}
        if not manifest.is_file():
            entry.update(status="missing-manifest")
            packs.append(entry)
            continue
        try:
            report = verify_pack(pmtiles, manifest)
        except PackError as error:
            entry.update(status="invalid", error=str(error))
        else:
            entry.update(status="ok", name=report["name"],
                         bbox=report["bbox"], zoom=report["zoom"],
                         tiles=report["tiles"])
            entry.pop("bytes")
            entry["bytes"] = report["bytes"]
        packs.append(entry)
    orphans = sorted(name for name in
                     (path.name for path in root.glob("*.pmtiles.manifest.json"))
                     if name not in manifests)
    ok = [entry for entry in packs if entry["status"] == "ok"]
    return {
        "root": str(root),
        "count": len(packs),
        "ok_count": len(ok),
        "total_bytes": sum(entry["bytes"] for entry in ok),
        "packs": packs,
        "orphan_manifests": orphans,
    }


def _backup_dir(root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = root / ".backups" / stamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _copy_verified(source: Path, target: Path, expected: str) -> None:
    temp = target.with_name("." + target.name + f".tmp-{os.getpid()}")
    try:
        with source.open("rb") as reader, temp.open("xb") as writer:
            shutil.copyfileobj(reader, writer, 8 * 1024**2)
            writer.flush()
            os.fsync(writer.fileno())
        digest = sha256(temp)
        if digest != expected:
            raise PackError(f"{source.name}: copy failed SHA-256 verification")
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


def _write_json_atomic(path: Path, data: dict) -> None:
    temp = path.with_name("." + path.name + f".tmp-{os.getpid()}")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def install(source_pmtiles, source_manifest, root, replace=False,
            min_free_bytes=DEFAULT_MIN_FREE) -> dict:
    source_pmtiles = Path(source_pmtiles)
    if not NAME_RE.fullmatch(source_pmtiles.name):
        raise PackError("pack file name must be a flat *.pmtiles name")
    if not source_pmtiles.is_file():
        raise PackError(f"missing pack file: {source_pmtiles}")
    report = verify_pack(source_pmtiles, source_manifest)
    manifest = load_manifest(source_manifest)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / source_pmtiles.name
    target_manifest = manifest_path_for(target)
    existing = target.exists() or target_manifest.exists()
    if existing and not replace:
        raise PackError(f"{target.name} is already installed; use --replace")
    need = report["bytes"]
    free = shutil.disk_usage(root).free
    if free - need < min_free_bytes:
        raise PackError(
            f"install needs {need} bytes and must leave {min_free_bytes} free; "
            f"only {free} bytes are available")
    backup = None
    if existing:
        backup = _backup_dir(root)
        for path in (target, target_manifest):
            if path.exists():
                os.replace(path, backup / path.name)
    try:
        _copy_verified(source_pmtiles, target, report["sha256"])
        _write_json_atomic(target_manifest, manifest)
    except Exception:
        if not target_manifest.exists() and not target.exists() and backup:
            for path in backup.iterdir():
                os.replace(path, root / path.name)
        raise
    return {
        "installed": str(target),
        "manifest": str(target_manifest),
        "name": report["name"],
        "bytes": report["bytes"],
        "sha256": report["sha256"],
        "replaced": backup is not None,
        "backup": str(backup) if backup else None,
    }


def remove(root, name, confirmed=False) -> dict:
    if not confirmed:
        raise PackError("removal requires --yes")
    root = Path(root)
    if not NAME_RE.fullmatch(name):
        raise PackError("name must be a flat *.pmtiles name")
    target = root / name
    if not target.is_file():
        raise PackError(f"not installed: {name}")
    backup = _backup_dir(root)
    moved = []
    for path in (target, manifest_path_for(target)):
        if path.exists():
            os.replace(path, backup / path.name)
            moved.append(path.name)
    return {"removed": name, "backup": str(backup), "files": moved}


def _print(data, as_json):
    if as_json:
        print(json.dumps(data, indent=2))
    else:
        print(json.dumps(data, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    listing = sub.add_parser("list", help="inventory installed packs")
    listing.add_argument("--root", default="/opt/sage-lantern/maps")

    verify = sub.add_parser("verify", help="verify a pack and its manifest")
    verify.add_argument("pack")
    verify.add_argument("--manifest")

    install_parser = sub.add_parser("install", help="verify and install a pack")
    install_parser.add_argument("pack")
    install_parser.add_argument("--manifest", required=True)
    install_parser.add_argument("--root", default="/opt/sage-lantern/maps")
    install_parser.add_argument("--replace", action="store_true")
    install_parser.add_argument("--min-free-bytes", type=int,
                                default=DEFAULT_MIN_FREE)

    remove_parser = sub.add_parser("remove", help="move a pack to backup")
    remove_parser.add_argument("name")
    remove_parser.add_argument("--root", default="/opt/sage-lantern/maps")
    remove_parser.add_argument("--yes", action="store_true")

    for command in (listing, verify, install_parser, remove_parser):
        command.add_argument("--json", action="store_true")

    args = parser.parse_args(argv)
    try:
        if args.command == "list":
            result = list_packs(args.root)
        elif args.command == "verify":
            result = verify_pack(args.pack, args.manifest)
        elif args.command == "install":
            result = install(args.pack, args.manifest, args.root, args.replace,
                             args.min_free_bytes)
        else:
            result = remove(args.root, args.name, args.yes)
    except PackError as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2),
              file=sys.stderr)
        return 2
    _print(result, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
