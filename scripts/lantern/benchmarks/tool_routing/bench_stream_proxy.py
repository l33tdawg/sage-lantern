#!/usr/bin/env python3
"""Controlled upstream replay: measure proxy delay, never claim model TTFT."""
import argparse
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import statistics
import time
from unittest.mock import Mock, patch
from run import digest, write


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def trial(server, stream):
    # First event at 40ms, terminal event at 440ms. Same exact multilingual
    # bytes in both arms. The old full-body read waits for the terminal event.
    first = 'data: {"choices":[{"delta":{"content":"مرحبا, hello"},"finish_reason":null}]}\n\n'.encode()
    tail = b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
    start = time.perf_counter()
    class Replay(io.BytesIO):
        headers = {"Content-Type":"text/event-stream; charset=utf-8"}
        status = 200
        def read(self, *args):
            time.sleep(max(0, start+.44-time.perf_counter()))
            return super().read(*args)
        def readline(self, *args):
            deadline = start+(.04 if self.tell()<len(first) else .44)
            time.sleep(max(0, deadline-time.perf_counter()))
            return super().readline(*args)
    class Sink(io.BytesIO):
        first_ms = None
        def write(self, raw):
            if self.first_ms is None:
                self.first_ms = (time.perf_counter()-start)*1000
            return super().write(raw)
    h = server.Handler.__new__(server.Handler)
    h.fallback_url = 'http://127.0.0.1:8082'
    h.wfile = Sink()
    for attr in ('send_response', 'send_header', 'end_headers'):
        setattr(h, attr, Mock())
    with patch.object(server.urllib.request, 'urlopen', return_value=Replay(first+tail)):
        if stream:
            h._proxy(b'{}', stream=True)
        else:
            h._proxy(b'{}')
    return {"first_forwarded_ms":h.wfile.first_ms,
            "total_ms":(time.perf_counter()-start)*1000,
            "identical_event_bytes":h.wfile.getvalue() == first+tail,
            "output_sha256":hashlib.sha256(h.wfile.getvalue()).hexdigest()}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--baseline', type=Path, required=True)
    p.add_argument('--candidate', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    write(args.output/'registration.json', {"baseline_sha256":digest(args.baseline),
        "candidate_sha256":digest(args.candidate), "source_sha256":digest(Path(__file__)),
        "trials_per_variant":3, "first_event_ms":40, "terminal_ms":440,
        "scope":"mocked network/upstream, real proxy methods, controlled sleep; not live model/hardware/browser"})
    baseline = load(args.baseline, 'proxy_baseline')
    candidate = load(args.candidate, 'proxy_candidate')
    rows = []
    for repeat in range(3):
        modes = ('baseline','candidate') if repeat%2 == 0 else ('candidate','baseline')
        for mode in modes:
            rows.append({"repeat":repeat, "mode":mode,
                         **trial(candidate if mode=='candidate' else baseline, mode=='candidate')})
    write(args.output/'outputs.json', rows)
    write(args.output/'summary.json', {mode:{"first_forwarded_p50_ms":statistics.median(r['first_forwarded_ms'] for r in rows if r['mode']==mode),
        "total_p50_ms":statistics.median(r['total_ms'] for r in rows if r['mode']==mode),
        "all_event_bytes_identical":all(r['identical_event_bytes'] for r in rows if r['mode']==mode)}
        for mode in ('baseline','candidate')})
    print((args.output/'summary.json').read_text())


if __name__ == '__main__':
    main()
