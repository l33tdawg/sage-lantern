#!/usr/bin/env python3
"""OpenAI-compatible photo-question front end for the Lantern NPU vision model.

Serves the same subset of the llama.cpp HTTP API that rkllm_server.py serves, so the
appliance runtime can point its photo lane at the NPU instead of the CPU 4B:

  GET  /health                 -> {"status": "ok"}
  GET  /v1/models              -> model list
  POST /v1/chat/completions    -> chat completion (stream=true supported)

Image parts arrive as OpenAI-style content lists with a data: URI (what
LlamaCppHTTP sends). Each request becomes one vlm_engine call: decode the image to
a private temp file, render the transcript, place the "<image>" marker where the
image part sat, and let the engine substitute the vision tokens. The model pair
(vision .rknn + .rkllm) must be a matched conversion: same toolkit generation as
the rkllm runtime, same image-token count. Text-only requests run the same LLM
without an image.
"""
import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ASSISTANT_PREFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
MAX_BODY_BYTES = 48 * 1024 * 1024
MAX_IMAGE_BYTES = 16 * 1024 * 1024
ENGINE_MARKER = "@@RKLLM_JSON@@ "
IMAGE_MARKER = "<image>"


class EngineError(RuntimeError):
    pass


class Engine:
    """Resident vlm_engine subprocess with JSON-lines request/response."""

    def __init__(self, binary, vision_model, llm_model, context, cpus, max_new_tokens,
                 vision_cores):
        self.command = [binary, vision_model, llm_model, str(context), str(cpus),
                        str(max_new_tokens), str(vision_cores)]
        self.lock = threading.Lock()
        self.busy = False
        self.process = None
        self.ready = {}
        self._start()

    def _start(self):
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=sys.stderr, text=True, bufsize=1)
        message = self._read_message(timeout=300)
        if message.get("event") != "ready":
            raise EngineError(f"engine init failed: {message}")
        self.ready = message

    def _read_message(self, timeout=None):
        # The RKLLM runtime logs to stdout; only marker-prefixed lines are protocol.
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if deadline is not None and time.monotonic() > deadline:
                raise EngineError("engine init timed out")
            line = self.process.stdout.readline()
            if not line:
                raise EngineError("engine exited")
            if line.startswith(ENGINE_MARKER):
                return json.loads(line[len(ENGINE_MARKER):])

    def stream(self, prompt, max_new_tokens, want_tokens, image_path=None):
        """Yield engine events for one request, ending with the 'done' event."""
        payload = {"id": uuid.uuid4().hex[:12], "prompt": prompt,
                   "max_new_tokens": int(max_new_tokens), "stream": bool(want_tokens)}
        if image_path:
            payload["image"] = image_path
        request = json.dumps(payload, ensure_ascii=False)
        with self.lock:
            self.busy = True
            try:
                if self.process.poll() is not None:
                    self._start()
                try:
                    self.process.stdin.write(request + "\n")
                    self.process.stdin.flush()
                except (BrokenPipeError, OSError) as exc:
                    raise EngineError(f"engine pipe failed: {exc}") from exc
                while True:
                    message = self._read_message()
                    yield message
                    if message.get("event") == "done":
                        return
            finally:
                self.busy = False

    def run(self, prompt, max_new_tokens, image_path=None):
        result = None
        for message in self.stream(prompt, max_new_tokens, want_tokens=False,
                                   image_path=image_path):
            if message.get("event") == "done":
                result = message
        if result is None or not result.get("ok"):
            raise EngineError(f"engine error: {(result or {}).get('error') or result}")
        return result


def _decode_data_uri(url):
    """Decode an OpenAI data: URI image part into raw bytes plus a file suffix."""
    if not isinstance(url, str) or not url.startswith("data:"):
        raise ValueError("image parts must be data: URIs")
    header, _, payload = url.partition(",")
    if not payload or ";base64" not in header:
        raise ValueError("image part is not base64 data")
    mime = header[5:].split(";", 1)[0].strip().lower()
    suffix = {"image/jpeg": ".jpg", "image/jpg": ".jpg", "image/png": ".png",
              "image/webp": ".webp", "image/bmp": ".bmp"}.get(mime)
    if not suffix:
        raise ValueError(f"unsupported image type {mime!r}")
    payload = "".join(payload.split())
    payload += "=" * (-len(payload) % 4)
    raw = base64.b64decode(payload, validate=True)
    if not raw or len(raw) > MAX_IMAGE_BYTES:
        raise ValueError("image part is empty or oversized")
    return raw, suffix


def render_chat(messages):
    """Render OpenAI-style messages into the Qwen transcript, returning the prompt
    text plus an optional decoded image (raw bytes, suffix)."""
    if not messages:
        raise ValueError("messages must not be empty")
    parts = []
    image = None
    for message in messages:
        role = message.get("role")
        if role not in ("system", "user", "assistant"):
            raise ValueError(f"unsupported role {role!r}")
        content = message.get("content")
        if isinstance(content, str):
            if not content:
                raise ValueError(f"empty {role} content")
            parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
            continue
        if not isinstance(content, list):
            raise ValueError(f"unsupported {role} content")
        if role != "user":
            raise ValueError("only user messages may carry image parts")
        rendered = []
        for part in content:
            kind = part.get("type")
            if kind == "text":
                text = part.get("text") or ""
                if text:
                    rendered.append(text)
            elif kind in ("image_url", "image"):
                source = part.get("image_url") or {}
                url = source.get("url") if isinstance(source, dict) else source
                raw, suffix = _decode_data_uri(url)
                if image is not None:
                    # One image per request: the engine substitutes exactly one
                    # embedding block for one "<image>" marker, so a second part
                    # would silently misalign the prompt. Fail closed.
                    raise ValueError("only one image per request is supported")
                image = (raw, suffix)
                rendered.append(IMAGE_MARKER)
            else:
                raise ValueError(f"unsupported content part {kind!r}")
        if not rendered:
            raise ValueError("empty user content")
        parts.append(f"<|im_start|>{role}\n{''.join(rendered)}<|im_end|>\n")
    parts.append(ASSISTANT_PREFIX)
    return "".join(parts), image


def completion_payload(model, text, result, created):
    usage = {
        "prompt_tokens": int(result.get("prefill_tokens") or 0),
        "completion_tokens": int(result.get("generate_tokens") or 0),
        "total_tokens": int(result.get("prefill_tokens") or 0)
        + int(result.get("generate_tokens") or 0),
    }
    timings = {
        "vision_ms": float(result.get("vision_ms") or 0.0),
        "prompt_n": usage["prompt_tokens"],
        "prompt_ms": float(result.get("prefill_ms") or 0.0),
        "prompt_per_token_ms": (float(result.get("prefill_ms") or 0.0)
                                / max(1, usage["prompt_tokens"])),
        "predicted_n": usage["completion_tokens"],
        "predicted_ms": float(result.get("generate_ms") or 0.0),
        "predicted_per_token_ms": (float(result.get("generate_ms") or 0.0)
                                   / max(1, usage["completion_tokens"])),
    }
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "usage": usage,
        "timings": timings,
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "lantern-vlm/1.0"
    engine = None
    model_name = "lantern-qwen35-08b-vision"

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in ("/health", "/health/"):
            self._json(200, {"status": "ok"})
        elif self.path in ("/v1/models", "/v1/models/"):
            self._json(200, {
                "object": "list",
                "data": [{"id": self.model_name, "object": "model",
                          "created": int(time.time()), "owned_by": "lantern"}],
            })
        elif self.path == "/props":
            self._json(200, {"default_generation_settings": {},
                             "model_path": self.model_name,
                             "n_ctx": (self.engine.ready.get("max_context")
                                       if self.engine else None)})
        elif self.path in ("/slots", "/slots/"):
            # llama.cpp-shaped idle-slot view: the appliance's suspend/drain guards
            # poll this to know the model is quiescent. The engine serialises one
            # request at a time, so slot 0's only states are busy/idle.
            self._json(200, [{"id": 0, "is_processing": bool(self.engine.busy),
                              "n_ctx": (self.engine.ready.get("max_context")
                                        if self.engine else None)}])
        else:
            self._json(404, {"error": "not found"})

    def _read_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            raise ValueError("missing or oversized body")
        return json.loads(self.rfile.read(length))

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._read_body()
            prompt, image = render_chat(body.get("messages") or [])
        except Exception as exc:
            self._json(400, {"error": {"message": f"bad request: {exc}"}})
            return

        max_new_tokens = int(body.get("max_tokens") or 192)
        started = time.time()
        tmp_path = None
        try:
            if image is not None:
                raw, suffix = image
                with tempfile.NamedTemporaryFile(prefix="lantern-vlm-", suffix=suffix,
                                                 delete=False) as handle:
                    handle.write(raw)
                    tmp_path = handle.name

            if not body.get("stream"):
                try:
                    result = self.engine.run(prompt, max_new_tokens, image_path=tmp_path)
                except EngineError as exc:
                    self._json(503, {"error": {"message": f"engine unavailable: {exc}"}})
                    return
                text = (result.get("text") or "").strip()
                self._json(200, completion_payload(self.model_name, text, result,
                                                   int(started)))
                return

            # Streamed answer: deltas go out as the engine emits tokens.
            chunk = {"id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                     "object": "chat.completion.chunk", "created": int(started),
                     "model": self.model_name}
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            writable = {"value": True}

            def event(data):
                if not writable["value"]:
                    return
                try:
                    self.wfile.write(
                        f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8"))
                except (BrokenPipeError, ConnectionResetError, OSError):
                    # Client went away; keep draining the engine so the protocol stays in sync.
                    writable["value"] = False

            event({**chunk, "choices": [{"index": 0, "delta": {"role": "assistant",
                                                               "content": ""},
                                         "finish_reason": None}]})
            final = None
            try:
                for message in self.engine.stream(prompt, max_new_tokens,
                                                  want_tokens=True, image_path=tmp_path):
                    if message.get("event") == "token":
                        piece = message.get("text") or ""
                        if piece:
                            event({**chunk, "choices": [{"index": 0,
                                                         "delta": {"content": piece},
                                                         "finish_reason": None}]})
                    elif message.get("event") == "done":
                        final = message
            except EngineError as exc:
                sys.stderr.write(f"engine failed mid-stream: {exc}\n")
            if final is not None and final.get("ok"):
                payload = completion_payload(self.model_name,
                                             (final.get("text") or "").strip(), final,
                                             int(started))
                event({**chunk, "choices": [{"index": 0, "delta": {},
                                             "finish_reason": "stop"}],
                       "usage": payload["usage"], "timings": payload["timings"]})
                event({**chunk, "choices": [], "usage": payload["usage"],
                       "timings": payload["timings"]})
            if writable["value"]:
                try:
                    self.wfile.write(b"data: [DONE]\n\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            self.close_connection = True
        finally:
            if tmp_path:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--vision-model", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8082)
    parser.add_argument("--context", type=int, default=4096)
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--vision-cores", type=int, default=3)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--model-name", default="lantern-qwen35-08b-vision")
    args = parser.parse_args()

    engine = Engine(args.engine, args.vision_model, args.model, args.context,
                    args.cpus, args.max_new_tokens, args.vision_cores)
    Handler.engine = engine
    Handler.model_name = args.model_name
    print(f"vlm_server ready: init_ms={engine.ready.get('init_ms'):.0f} "
          f"vision_init_ms={engine.ready.get('vision_init_ms'):.0f} "
          f"image_token={engine.ready.get('image_token')} "
          f"model={args.model} port={args.port}", file=sys.stderr, flush=True)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        engine.process.terminate()


if __name__ == "__main__":
    main()
