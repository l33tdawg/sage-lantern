"""Tests for the bounded OSM/PMTiles planner."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_pack


class PlannerTests(unittest.TestCase):
    def test_parse_bbox_and_zoom(self):
        self.assertEqual(build_pack.parse_bbox("101.5,3.0,101.8,3.3"),
                         (101.5, 3.0, 101.8, 3.3))
        self.assertEqual(build_pack.parse_zoom("10-12"), (10, 12))
        with self.assertRaises(ValueError):
            build_pack.parse_bbox("101.8,3.3,101.5,3.0")

    def test_tile_count_grows_with_zoom(self):
        bbox = (101.5, 3.0, 101.8, 3.3)
        self.assertEqual(build_pack.tile_count(bbox, (0, 0)), 1)
        self.assertGreater(build_pack.tile_count(bbox, (10, 12)),
                           build_pack.tile_count(bbox, (10, 10)))

    def test_budget_rejection(self):
        with self.assertRaises(ValueError):
            build_pack.plan_pack((101.5, 3.0, 101.8, 3.3), (12, 12),
                                 max_bytes=1)

    def test_disk_floor(self):
        with self.assertRaises(ValueError):
            build_pack.plan_pack((101.5, 3.0, 101.8, 3.3), (12, 12),
                                 bytes_per_tile=1_000_000,
                                 max_bytes=10 * 1024**3,
                                 free_bytes=6 * 1024**3 + 1,
                                 min_free_bytes=6 * 1024**3)

    def test_missing_tools_fail_closed(self):
        with patch("build_pack.shutil.which", return_value=None):
            with self.assertRaises(ValueError):
                build_pack.require_tools()


class ManifestTests(unittest.TestCase):
    def test_manifest_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "map.pmtiles"
            output.write_bytes(b"PMTiles-test")
            plan = build_pack.plan_pack((101.5, 3.0, 101.8, 3.3), (10, 11))
            manifest = build_pack.build_manifest(
                output, plan, "Penang test", "OpenStreetMap", "2026-10-19",
                {"pmtiles": "pmtiles 1.0"})
            self.assertEqual(manifest["schema"], "sage.lantern.offline-map-pack.v1")
            self.assertEqual(manifest["license"], "ODbL 1.0")
            self.assertIn("OpenStreetMap", manifest["attribution"])
            self.assertEqual(manifest["sha256"], build_pack.sha256(output))
            self.assertEqual(manifest["bytes"], len(b"PMTiles-test"))


class GuardrailTests(unittest.TestCase):
    def test_no_google_tiles(self):
        root = Path(__file__).resolve().parent
        text = (root / "build_pack.py").read_text(encoding="utf-8").lower()
        self.assertNotIn("google", text)
        self.assertIn("openstreetmap", text)


if __name__ == "__main__":
    unittest.main()
