"""Tests for map pack verification and installation."""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import pack_manager


def make_pack(tmp: Path, name="test.pmtiles", payload=b"PMTiles-test",
              **overrides):
    pmtiles = tmp / name
    pmtiles.write_bytes(payload)
    manifest = {
        "schema": pack_manager.SCHEMA,
        "name": "Test region",
        "bbox": [100.2, 5.2, 100.5, 5.5],
        "zoom": [0, 13],
        "tiles": 90,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "attribution": "© OpenMapTiles © OpenStreetMap contributors",
        "license": "ODbL 1.0; OpenMapTiles CC-BY",
    }
    manifest.update(overrides)
    path = pack_manager.manifest_path_for(pmtiles)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return pmtiles, path


class VerifyTests(unittest.TestCase):
    def test_valid_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp))
            report = pack_manager.verify_pack(pmtiles)
            self.assertTrue(report["ok"])
            self.assertEqual(report["tiles"], 90)

    def test_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp), sha256="0" * 64)
            with self.assertRaises(pack_manager.PackError):
                pack_manager.verify_pack(pmtiles)

    def test_byte_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp), bytes=999)
            with self.assertRaises(pack_manager.PackError):
                pack_manager.verify_pack(pmtiles)

    def test_not_pmtiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp), payload=b"not-a-pack")
            with self.assertRaises(pack_manager.PackError):
                pack_manager.verify_pack(pmtiles)

    def test_missing_attribution(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp), attribution="")
            with self.assertRaises(pack_manager.PackError):
                pack_manager.verify_pack(pmtiles)

    def test_bad_bbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            pmtiles, _ = make_pack(Path(tmp), bbox=[0, 0, -1, 1])
            with self.assertRaises(pack_manager.PackError):
                pack_manager.verify_pack(pmtiles)


class InstallTests(unittest.TestCase):
    def test_install_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            root = Path(tmp) / "maps"
            pmtiles, manifest = make_pack(source)
            result = pack_manager.install(pmtiles, manifest, root,
                                          min_free_bytes=0)
            self.assertTrue((root / "test.pmtiles").is_file())
            self.assertTrue(Path(result["manifest"]).is_file())
            listing = pack_manager.list_packs(root)
            self.assertEqual(listing["ok_count"], 1)
            self.assertEqual(listing["packs"][0]["status"], "ok")

    def test_refuses_overwrite_without_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            root = Path(tmp) / "maps"
            pmtiles, manifest = make_pack(source)
            pack_manager.install(pmtiles, manifest, root, min_free_bytes=0)
            with self.assertRaises(pack_manager.PackError):
                pack_manager.install(pmtiles, manifest, root, min_free_bytes=0)

    def test_replace_keeps_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            root = Path(tmp) / "maps"
            pmtiles, manifest = make_pack(source)
            pack_manager.install(pmtiles, manifest, root, min_free_bytes=0)
            result = pack_manager.install(pmtiles, manifest, root, replace=True,
                                          min_free_bytes=0)
            self.assertTrue(result["replaced"])
            backup = Path(result["backup"])
            self.assertTrue((backup / "test.pmtiles").is_file())
            self.assertTrue((backup / "test.pmtiles.manifest.json").is_file())

    def test_free_space_floor(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            root = Path(tmp) / "maps"
            pmtiles, manifest = make_pack(source)
            with self.assertRaises(pack_manager.PackError):
                pack_manager.install(pmtiles, manifest, root,
                                     min_free_bytes=10**18)

    def test_rejects_path_like_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            pmtiles, manifest = make_pack(source, name="bad name.pmtiles")
            with self.assertRaises(pack_manager.PackError):
                pack_manager.install(pmtiles, manifest, Path(tmp) / "maps",
                                     min_free_bytes=0)

    def test_remove_moves_to_backup(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "src"
            source.mkdir()
            root = Path(tmp) / "maps"
            pmtiles, manifest = make_pack(source)
            pack_manager.install(pmtiles, manifest, root, min_free_bytes=0)
            with self.assertRaises(pack_manager.PackError):
                pack_manager.remove(root, "test.pmtiles")
            result = pack_manager.remove(root, "test.pmtiles", confirmed=True)
            self.assertFalse((root / "test.pmtiles").exists())
            self.assertTrue((Path(result["backup"]) / "test.pmtiles").is_file())


if __name__ == "__main__":
    unittest.main()
