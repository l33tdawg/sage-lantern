"""Tests for the loopback gpsd status bridge; no device or socket bind required."""
from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import status_bridge


def iso(seconds_ago=0):
    stamp = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return stamp.isoformat().replace("+00:00", "Z")


def tpv(mode=3, seconds_ago=0):
    return {"class": "TPV", "mode": mode, "time": iso(seconds_ago),
            "lat": 3.139, "lon": 101.6869, "alt": 12.4, "eph": 4.2,
            "speed": 0.3, "track": 88.0}


def sky(used=7, visible=12, hdop=0.9):
    sats = [{"used": index < used, "PRN": index + 1} for index in range(visible)]
    return {"class": "SKY", "hdop": hdop, "satellites": sats}


class FakeSocket:
    def __init__(self, payload):
        self.payload = payload
        self.sent = b""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def sendall(self, data):
        self.sent += data

    def settimeout(self, value):
        self.timeout = value

    def recv(self, size):
        data, self.payload = self.payload[:size], self.payload[size:]
        return data


class FakeReader:
    def __init__(self, tpv_row, sky_row):
        self.tpv, self.sky = tpv_row, sky_row

    def read(self):
        return self.tpv, self.sky


class StatusTests(unittest.TestCase):
    def test_mode_one_is_not_a_fix(self):
        result = status_bridge.build_status(tpv(1), sky(), include_position=True)
        self.assertFalse(result["has_fix"])
        self.assertNotIn("latitude", result)

    def test_mode_three_is_a_fix_and_position_is_opt_in(self):
        default = status_bridge.build_status(tpv(3), sky())
        self.assertTrue(default["has_fix"])
        self.assertEqual(default["satellites_used"], 7)
        self.assertEqual(default["satellites_visible"], 12)
        self.assertNotIn("latitude", default)
        located = status_bridge.build_status(tpv(3), sky(), include_position=True)
        self.assertAlmostEqual(located["latitude"], 3.139)
        self.assertAlmostEqual(located["longitude"], 101.6869)

    def test_stale_fix_is_revoked(self):
        result = status_bridge.build_status(tpv(3, seconds_ago=30), sky(),
                                            include_position=True)
        self.assertFalse(result["has_fix"])
        self.assertTrue(result["stale"])
        self.assertNotIn("latitude", result)

    def test_missing_time_is_not_a_fix(self):
        row = tpv(3)
        row.pop("time")
        result = status_bridge.build_status(row, sky())
        self.assertFalse(result["has_fix"])
        self.assertTrue(result["stale"])

    def test_gpsd_reader_parses_tpv_and_sky(self):
        payload = (json.dumps(tpv(3)) + "\n" + json.dumps(sky()) + "\n").encode()
        with patch("status_bridge.socket.create_connection",
                   return_value=FakeSocket(payload)):
            tpv_row, sky_row = status_bridge.GpsdReader().read()
        self.assertEqual(tpv_row["mode"], 3)
        self.assertEqual(sky_row["hdop"], 0.9)

    def test_gpsd_reader_unavailable_returns_none(self):
        with patch("status_bridge.socket.create_connection", side_effect=OSError):
            tpv_row, sky_row = status_bridge.GpsdReader().read()
        self.assertIsNone(tpv_row)
        self.assertIsNone(sky_row)

    def test_gpsd_reader_ignores_malformed_lines(self):
        payload = b"not-json\n" + json.dumps(tpv(3)).encode() + b"\n"
        with patch("status_bridge.socket.create_connection",
                   return_value=FakeSocket(payload)):
            tpv_row, sky_row = status_bridge.GpsdReader().read()
        self.assertEqual(tpv_row["mode"], 3)
        self.assertIsNone(sky_row)


class RouteTests(unittest.TestCase):
    def setUp(self):
        self.bridge = status_bridge.Bridge(FakeReader(tpv(3), sky()))

    def test_health_and_status(self):
        code, headers, payload = status_bridge.route("/health", self.bridge)
        self.assertEqual(code, 200)
        self.assertEqual(payload, {"status": "ok"})
        code, headers, payload = status_bridge.route(
            "/status", self.bridge, origin="file://")
        self.assertEqual(code, 200)
        self.assertTrue(payload["has_fix"])
        self.assertNotIn("latitude", payload)
        self.assertEqual(headers["Access-Control-Allow-Origin"], "file://")
        _, _, payload = status_bridge.route("/status?position=1", self.bridge)
        self.assertIn("latitude", payload)

    def test_options_headers_are_pure(self):
        headers = status_bridge.cors_headers("file://")
        self.assertEqual(headers["Access-Control-Allow-Methods"], "GET, OPTIONS")
        self.assertEqual(headers["Access-Control-Allow-Origin"], "file://")

    def test_unknown_path_is_404(self):
        code, _, payload = status_bridge.route("/nope", self.bridge)
        self.assertEqual(code, 404)
        self.assertEqual(payload, {"error": "not found"})


if __name__ == "__main__":
    unittest.main()
