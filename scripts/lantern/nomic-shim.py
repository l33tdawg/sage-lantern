#!/usr/bin/env python3
"""Minimal Ollama-compatible embedding shim in front of llama.cpp.

The SAGE node's embedding provider speaks Ollama (`/api/embeddings`,
`/api/embed`, `/api/tags`); llama.cpp serves `/v1/embeddings`. This translates
between them so the unit gets nomic-embed-text without an ollama daemon.

    nomic-shim.py --upstream http://127.0.0.1:8083 --port 8084
"""
import argparse
import json
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM = "http://127.0.0.1:8083"
MODEL = "nomic-embed-text-v1.5"


def embed(texts):
    body = json.dumps({"model": MODEL, "input": texts}).encode("utf-8")
    request = urllib.request.Request(UPSTREAM + "/v1/embeddings", data=body,
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)
    data = sorted(payload["data"], key=lambda row: row.get("index", 0))
    return [row["embedding"] for row in data]


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        import sys
        sys.stderr.write("shim: " + fmt % args + "\n")

    def _send(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/", "/api/version"):
            self._send(200, {"version": "0.0.0-nomic-shim"})
        elif self.path == "/api/tags":
            self._send(200, {"models": [{"name": MODEL, "model": MODEL}]})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        import sys
        sys.stderr.write(f"shim: POST {self.path} {self.headers.get('Content-Length')}\n")
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send(400, {"error": "invalid json"})
            return
        prompt = payload.get("prompt")
        inputs = payload.get("input", prompt)
        if inputs is None or not isinstance(inputs, (str, list)):
            self._send(400, {"error": "missing input"})
            return
        try:
            vectors = embed(inputs)
        except (urllib.error.URLError, OSError, ValueError, KeyError) as error:
            self._send(503, {"error": f"embedding backend unavailable: {type(error).__name__}"})
            return
        if self.path == "/v1/embeddings":
            self._send(200, {"object": "list", "model": MODEL,
                             "data": [{"object": "embedding", "index": index, "embedding": vector}
                                      for index, vector in enumerate(vectors)],
                             "usage": {"prompt_tokens": 0, "total_tokens": 0}})
        elif self.path == "/api/embeddings":
            self._send(200, {"embedding": vectors[0]})
        else:
            self._send(200, {"model": MODEL, "embeddings": vectors})


def main():
    global UPSTREAM
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument("--upstream", default=UPSTREAM)
    parser.add_argument("--port", type=int, default=8084)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    UPSTREAM = args.upstream.rstrip("/")
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
