"""Static checks for the Maps GNSS status integration."""
from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MAPS = ROOT / "software/gnome/desktop/maps.html"


class MapsGnssUiTests(unittest.TestCase):
    def setUp(self):
        self.html = MAPS.read_text(encoding="utf-8")

    def test_status_bridge_and_polling(self):
        self.assertIn("http://127.0.0.1:8090/status", self.html)
        self.assertIn("http://127.0.0.1:8091/packs.json", self.html)
        self.assertIn("setInterval(poll, 2000)", self.html)
        self.assertIn("?position=1", self.html)
        self.assertIn("vendor/maplibre-gl.js", self.html)
        self.assertIn("vendor/pmtiles.js", self.html)
        self.assertIn("pmtiles://", self.html)
        self.assertIn("OpenStreetMap contributors", self.html)
        self.assertIn("OpenStreetMap pack installed locally", self.html)

    def test_world_base_is_vendored(self):
        world = ROOT / "software/gnome/desktop/world"
        for name in ("land", "countries", "lakes", "rivers"):
            path = world / f"{name}.js"
            self.assertTrue(path.is_file(), name)
            self.assertIn(f"world/{name}.js", self.html)
            self.assertIn(f"window.SAGE_WORLD.{name}", path.read_text(encoding="utf-8"))
        self.assertIn("window.SAGE_WORLD", self.html)
        self.assertIn("Natural Earth", self.html)

    def test_installed_map_is_not_called_illustrative(self):
        self.assertNotIn("The map panel is illustrative", self.html)
        self.assertNotIn("Showing the illustrative map", self.html)

    def test_privacy_toggle_is_off_by_default(self):
        match = re.search(r'<input[^>]*id="gnss-position"[^>]*>', self.html)
        self.assertIsNotNone(match)
        self.assertNotIn("checked", match.group(0))

    def test_required_status_fields_and_fallback(self):
        for ident in ("gnss-state", "gnss-mode", "gnss-sats", "gnss-hdop",
                      "gnss-accuracy", "gnss-age", "gnss-bridge",
                      "gnss-position-text", "gnss-marker", "map-fallback",
                      "live-map"):
            self.assertIn(f'id="{ident}"', self.html)
        self.assertIn("No GNSS fix", self.html)
        self.assertIn("ILLUSTRATIVE LOCATION PANEL", self.html)

    def test_no_google_tile_or_script_requests(self):
        self.assertNotRegex(self.html, r"maps\.googleapis|mt\d\.google|google\.com/maps/vt")
        self.assertNotIn("google.maps", self.html)

    def test_no_coordinates_requested_when_privacy_is_off(self):
        self.assertIn(
            'toggle.checked ? statusUrl + "?position=1" : statusUrl', self.html)
        self.assertIn('positionText.textContent = "Live position:', self.html)


if __name__ == "__main__":
    unittest.main()
