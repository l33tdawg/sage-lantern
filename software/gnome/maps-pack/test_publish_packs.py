"""Tests for the public map-pack catalog builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pack_manager
import publish_packs


def make_pack(root: Path, name="penang.pmtiles", **overrides):
    payload = b"PMTiles" + b"x" * 64
    pmtiles = root / name
    pmtiles.write_bytes(payload)
    manifest = {
        "schema": pack_manager.SCHEMA,
        "name": overrides.pop("display_name", "Penang"),
        "bbox": [100.2, 5.2, 100.5, 5.5],
        "zoom": [0, 13],
        "tiles": 90,
        "bytes": len(payload),
        "sha256": pack_manager.sha256(pmtiles),
        "attribution": "© OpenMapTiles © OpenStreetMap contributors",
        "license": "ODbL 1.0; OpenMapTiles CC-BY",
    }
    manifest.update(overrides)
    pack_manager.manifest_path_for(pmtiles).write_text(
        __import__("json").dumps(manifest), encoding="utf-8")
    return pmtiles


class CatalogTests(unittest.TestCase):
    def test_catalog_shape_and_https_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_pack(Path(tmp))
            catalog = publish_packs.build_catalog(
                tmp, "https://example.test/releases/maps")
            publish_packs.validate_catalog(catalog)
            entry = catalog["packs"][0]
            self.assertEqual(entry["id"], "penang")
            self.assertTrue(entry["download_url"].startswith("https://"))
            self.assertTrue(entry["manifest_url"].endswith(
                "penang.pmtiles.manifest.json"))

    def test_rejects_http_urls(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_pack(Path(tmp))
            catalog = publish_packs.build_catalog(
                tmp, "http://example.test/releases/maps")
            with self.assertRaises(pack_manager.PackError):
                publish_packs.validate_catalog(catalog)

    def test_rejects_missing_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "orphan.pmtiles").write_bytes(b"PMTiles")
            with self.assertRaises(pack_manager.PackError):
                publish_packs.build_catalog(tmp, "https://example.test/")

    def test_rejects_duplicate_ids(self):
        catalog = {
            "schema": publish_packs.CATALOG_SCHEMA,
            "packs": [
                {"id": "x", "name": "X", "bbox": [0, 0, 1, 1],
                 "zoom": [0, 1], "bytes": 1, "sha256": "0" * 64,
                 "download_url": "https://e.test/x.pmtiles",
                 "manifest_url": "https://e.test/x.json"},
                {"id": "x", "name": "X2", "bbox": [0, 0, 1, 1],
                 "zoom": [0, 1], "bytes": 1, "sha256": "0" * 64,
                 "download_url": "https://e.test/y.pmtiles",
                 "manifest_url": "https://e.test/y.json"},
            ],
        }
        with self.assertRaises(pack_manager.PackError):
            publish_packs.validate_catalog(catalog)


if __name__ == "__main__":
    unittest.main()
