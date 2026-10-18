"""Loopback range-capable server for the installed PMTiles pack."""
from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

MAX_RANGE = 8 * 1024 * 1024


def parse_range(header, size):
    """Return (start, end) inclusive, or None for a full-file response."""
    if not header:
        return None
    if not header.startswith("bytes="):
        raise ValueError("unsupported range")
    spec = header[6:].split(",", 1)[0].strip()
    if "-" not in spec:
        raise ValueError("malformed range")
    left, right = spec.split("-", 1)
    if left == "":
        length = int(right)
        if length <= 0:
            raise ValueError("empty suffix")
        start = max(0, size - length)
        end = size - 1
    else:
        start = int(left)
        end = int(right) if right else size - 1
    if start < 0 or end < start or start >= size:
        raise ValueError("range outside file")
    end = min(end, size - 1)
    if end - start + 1 > MAX_RANGE:
        end = start + MAX_RANGE - 1
    return start, end


def resolve_file(root, path):
    name = unquote(urlsplit(path).path).lstrip("/")
    if not name or "/" in name or name.startswith("."):
        raise ValueError("invalid filename")
    candidate = (Path(root) / name).resolve()
    if candidate.parent != Path(root).resolve() or not candidate.is_file():
        raise ValueError("not found")
    return candidate


class Handler(BaseHTTPRequestHandler):
    server_version = "LanternMapPack/1.0"

    def _cors(self):
        origin = self.headers.get("Origin")
        self.send_header("Access-Control-Allow-Origin", origin or "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Range, Content-Type")
        self.send_header("Access-Control-Expose-Headers",
                         "Content-Length, Content-Range, Accept-Ranges")
        self.send_header("Vary", "Origin")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        self._serve(False)

    def do_GET(self):
        self._serve(True)

    def _serve(self, body):
        if urlsplit(self.path).path == "/health":
            raw = b'{"status":"ok"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self._cors()
            self.end_headers()
            if body:
                self.wfile.write(raw)
            return
        try:
            path = resolve_file(self.server.root, self.path)
            size = path.stat().st_size
            span = parse_range(self.headers.get("Range"), size)
        except ValueError:
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        start, end = span if span is not None else (0, size - 1)
        length = end - start + 1
        self.send_response(206 if span is not None else 200)
        self.send_header("Content-Type", "application/vnd.pmtiles")
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if span is not None:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self._cors()
        self.end_headers()
        if body:
            with path.open("rb") as stream:
                stream.seek(start)
                remaining = length
                while remaining:
                    chunk = stream.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

    def log_message(self, fmt, *args):
        return


def serve(root, host, port):
    server = ThreadingHTTPServer((host, port), Handler)
    server.root = Path(root).resolve()
    print(json.dumps({"event": "listening", "host": host, "port": port,
                      "root": str(server.root)}), flush=True)
    server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default="/opt/sage-lantern/maps")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8091)
    args = parser.parse_args()
    serve(args.root, args.host, args.port)


if __name__ == "__main__":
    main()
