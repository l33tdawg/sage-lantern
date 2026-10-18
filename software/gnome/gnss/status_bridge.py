"""Loopback-only gpsd status bridge for the Lantern desktop Maps page."""
from __future__ import annotations

import argparse
import json
import socket
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

STALE_SECONDS = 10.0
MAX_GPSD_BYTES = 256 * 1024


def _parse_time(value):
    if not isinstance(value, str) or not value:
        return None
    text = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


class GpsdReader:
    def __init__(self, host="127.0.0.1", port=2947, timeout=2.0):
        self.host = host
        self.port = port
        self.timeout = timeout

    def read(self):
        """Return (tpv, sky) from one gpsd query, or (None, None)."""
        try:
            with socket.create_connection((self.host, self.port),
                                          timeout=self.timeout) as sock:
                sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
                sock.settimeout(self.timeout)
                raw = b""
                while len(raw) < MAX_GPSD_BYTES:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    raw += chunk
                    compact = raw.replace(b" ", b"")
                    if b'"class":"TPV"' in compact and b'"class":"SKY"' in compact:
                        break
        except OSError:
            return None, None
        tpv = sky = None
        for line in raw.splitlines():
            try:
                item = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(item, dict) and item.get("class") == "TPV":
                tpv = item
            elif isinstance(item, dict) and item.get("class") == "SKY":
                sky = item
        return tpv, sky


def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def build_status(tpv, sky, now=None, include_position=False):
    now = time.time() if now is None else now
    tpv = tpv if isinstance(tpv, dict) else {}
    sky = sky if isinstance(sky, dict) else {}
    mode = tpv.get("mode")
    mode = int(mode) if isinstance(mode, int) else 0
    timestamp = _parse_time(tpv.get("time"))
    age = None if timestamp is None else max(0.0, now - timestamp.timestamp())
    stale = age is None or age > STALE_SECONDS
    has_fix = mode in (2, 3) and not stale
    satellites = sky.get("satellites") if isinstance(sky.get("satellites"), list) else []
    used = sum(1 for item in satellites
               if isinstance(item, dict) and item.get("used") is True)
    status = {
        "ok": bool(tpv or sky),
        "has_fix": has_fix,
        "mode": mode,
        "satellites_used": used,
        "satellites_visible": len(satellites),
        "hdop": _number(sky.get("hdop")),
        "accuracy_m": _number(tpv.get("eph")),
        "age_seconds": None if age is None else round(age, 1),
        "stale": stale,
        "last_update": timestamp.isoformat().replace("+00:00", "Z") if timestamp else None,
    }
    if include_position and has_fix:
        status.update({
            "latitude": _number(tpv.get("lat")),
            "longitude": _number(tpv.get("lon")),
            "altitude_m": _number(tpv.get("alt")),
            "speed_mps": _number(tpv.get("speed")),
            "track_deg": _number(tpv.get("track")),
        })
    return status


def cors_headers(origin=None):
    return {
        "Access-Control-Allow-Origin": origin or "*",
        "Access-Control-Allow-Methods": "GET, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }


def route(path, bridge, origin=None):
    """Return (status_code, headers, payload) without touching a socket."""
    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-store",
        **cors_headers(origin),
    }
    parsed = urlsplit(path)
    if parsed.path == "/health":
        return 200, headers, {"status": "ok"}
    if parsed.path != "/status":
        return 404, headers, {"error": "not found"}
    query = parse_qs(parsed.query)
    include_position = query.get("position", ["0"])[0] in {"1", "true", "yes"}
    return 200, headers, bridge.status(include_position)


class Bridge:
    def __init__(self, reader):
        self.reader = reader
        self.lock = threading.Lock()

    def status(self, include_position=False):
        with self.lock:
            tpv, sky = self.reader.read()
        return build_status(tpv, sky, include_position=include_position)


class Handler(BaseHTTPRequestHandler):
    server_version = "LanternGnssStatus/1.0"

    def _send(self, code, headers, payload):
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        self.send_response(204)
        for name, value in cors_headers(self.headers.get("Origin")).items():
            self.send_header(name, value)
        self.end_headers()

    def do_GET(self):
        code, headers, payload = route(
            self.path, self.server.bridge, self.headers.get("Origin"))
        self._send(code, headers, payload)

    def log_message(self, fmt, *args):
        return


def serve(host, port, reader):
    server = ThreadingHTTPServer((host, port), Handler)
    server.bridge = Bridge(reader)
    print(json.dumps({"event": "listening", "host": host, "port": port}), flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--gpsd-host", default="127.0.0.1")
    parser.add_argument("--gpsd-port", type=int, default=2947)
    args = parser.parse_args()
    serve(args.host, args.port, GpsdReader(args.gpsd_host, args.gpsd_port))


if __name__ == "__main__":
    main()
