#!/usr/bin/env python3
"""OpenAI-compatible chat front end for the Lantern RKLLM (NPU) engine.

Serves the subset of the llama.cpp HTTP API that sage_lantern.adapters.LlamaCppHTTP
uses, so the appliance runtime can talk to an NPU model on the same contract:

  GET  /health                 -> {"status": "ok"}
  GET  /v1/models              -> model list
  POST /v1/chat/completions    -> chat completion (stream=true supported)

The model is held resident by rkllm_engine (one rkllm_init for the process lifetime);
this server only renders the chat transcript and frames the JSON. Text-only: image
requests are refused with 501 unless --fallback-url is given, in which case they are
proxied verbatim to that llama.cpp endpoint (photo questions stay on the CPU tier).
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ASSISTANT_PREFIX = "<|im_start|>assistant\n<think>\n\n</think>\n\n"
PLAIN_ASSISTANT_PREFIX = "<|im_start|>assistant\n"
MAX_BODY_BYTES = 4 * 1024 * 1024
MAX_PROXY_RESPONSE_BYTES = 1024 * 1024
MAX_PROXY_LINE_BYTES = 65536
ENGINE_MARKER = "@@RKLLM_JSON@@ "
_RESPONSE_GUARDS = None


def response_guards():
    """Load the sibling text-only guards without depending on sys.path."""
    global _RESPONSE_GUARDS
    if _RESPONSE_GUARDS is None:
        path = Path(__file__).with_name("response_guards.py")
        spec = importlib.util.spec_from_file_location("lantern_response_guards", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _RESPONSE_GUARDS = module
    return _RESPONSE_GUARDS


class EngineError(RuntimeError):
    pass


class Engine:
    """Resident rkllm_engine subprocess with JSON-lines request/response."""

    def __init__(self, binary, model, context, cpus, max_new_tokens,
                 tokenizer=None, runtime_path=None):
        self.command = [binary, model, str(context), str(cpus), str(max_new_tokens)]
        if runtime_path:
            self.command.append(str(runtime_path))
        self.context = int(context)
        self.tokenizer = tokenizer
        self.lock = threading.Lock()
        self.busy = False
        self.process = None
        self.ready = {}
        self._start()

    def _start(self):
        self.process = subprocess.Popen(
            self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=sys.stderr, text=True, bufsize=1)
        message = self._read_message(timeout=180)
        if message.get("event") != "ready":
            raise EngineError(f"engine init failed: {message}")
        self.ready = message
        self.input_type = message.get("input_type", "PROMPT")
        if self.input_type == "TOKEN" and self.tokenizer is None:
            raise EngineError("token engine requires --tokenizer-json")

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

    def _request(self, prompt, max_new_tokens, want_tokens):
        request_id = uuid.uuid4().hex[:12]
        if self.input_type == "TOKEN":
            ids = self.tokenizer.encode(prompt, add_special_tokens=False).ids
            if len(ids) + int(max_new_tokens) > self.context:
                raise EngineError("token prompt exceeds context")
            return (
                '{"id":"%s","prompt_utf8_hex":"%s","input_ids":[%s],'
                '"max_new_tokens":%d,"stream":%s}'
                % (request_id, prompt.encode("utf-8").hex(),
                   ",".join(str(token) for token in ids), int(max_new_tokens),
                   "true" if want_tokens else "false")
            )
        return json.dumps(
            {"id": request_id, "prompt": prompt,
             "max_new_tokens": int(max_new_tokens), "stream": bool(want_tokens)},
            ensure_ascii=False)

    def stream(self, prompt, max_new_tokens, want_tokens):
        """Yield engine events for one request, ending with the 'done' event."""
        request = self._request(prompt, max_new_tokens, want_tokens)
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

    def run(self, prompt, max_new_tokens):
        result = None
        for message in self.stream(prompt, max_new_tokens, want_tokens=False):
            if message.get("event") == "done":
                result = message
        if result is None or not result.get("ok"):
            raise EngineError(f"engine error: {(result or {}).get('error') or result}")
        return result


def render_chat(messages, assistant_prefix=ASSISTANT_PREFIX):
    """Render OpenAI-style messages into the Qwen transcript the model was trained on."""
    if not messages:
        raise ValueError("messages must not be empty")
    parts = []
    for message in messages:
        role = message.get("role")
        if role not in ("system", "user", "assistant", "tool"):
            raise ValueError(f"unsupported role {role!r}")
        content = message.get("content")
        if isinstance(content, list):
            raise ValueError("image content is not supported by the NPU endpoint")
        if not isinstance(content, str) or not content:
            raise ValueError(f"empty {role} content")
        parts.append(f"<|im_start|>{role}\n{content}<|im_end|>\n")
    parts.append(assistant_prefix)
    return "".join(parts)


def completion_payload(model, text, result, created):
    usage = {
        "prompt_tokens": int(result.get("prefill_tokens") or 0),
        "completion_tokens": int(result.get("generate_tokens") or 0),
        "total_tokens": int(result.get("prefill_tokens") or 0)
        + int(result.get("generate_tokens") or 0),
    }
    timings = {
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
    server_version = "lantern-rkllm/1.0"
    engine = None
    model_name = "lantern-native5-2b"
    fallback_url = None
    response_guards = False
    assistant_prefix = ASSISTANT_PREFIX

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _stream_text(self, text, started):
        """Emit one deterministic answer through the normal SSE shape."""
        chunk = {"id": f"chatcmpl-{uuid.uuid4().hex[:24]}",
                 "object": "chat.completion.chunk", "created": int(started),
                 "model": self.model_name}
        payload = completion_payload(
            self.model_name, text, {"generate_tokens": len(text.split())}, int(started))
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        events = [
            {**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                   "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {"content": text},
                                   "finish_reason": None}]},
            {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
             "usage": payload["usage"], "timings": payload["timings"]},
        ]
        for event in events:
            self.wfile.write(f"data: {json.dumps(event, ensure_ascii=False)}\n\n".encode("utf-8"))
        self.wfile.write(b"data: [DONE]\n\n")
        self.close_connection = True

    def _json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _post_guard_text(self, messages, text):
        if not self.response_guards:
            return text
        text = response_guards().status_text(messages, text)
        return response_guards().action_policy(messages, text)

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
                             "n_ctx": (self.engine.ready.get("max_context") if self.engine else None)})
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

    def _proxy(self, raw, *, stream=False):
        parts = urlsplit(self.fallback_url)
        path = parts.path.rstrip("/") or "/v1/chat/completions"
        url = urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        request = urllib.request.Request(
            url, data=raw, method="POST",
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=300) as upstream:
            content_type = upstream.headers.get("Content-Type", "application/json")
            if stream:
                if content_type.split(";", 1)[0].strip().lower() != "text/event-stream":
                    raise EngineError("fallback did not return an event stream")
                self.send_response(upstream.status)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                # A sized read waits to fill its buffer. SSE lines become
                # available as soon as the upstream flushes a token; forward
                # their exact bytes, including blank lines and terminal events.
                total = 0
                try:
                    while True:
                        line = upstream.readline(MAX_PROXY_LINE_BYTES + 1)
                        if not line:
                            return
                        total += len(line)
                        if len(line) > MAX_PROXY_LINE_BYTES or total > MAX_PROXY_RESPONSE_BYTES:
                            raise EngineError("fallback stream exceeds transport limit")
                        self.wfile.write(line)
                        self.wfile.flush()
                except (OSError, EngineError):
                    # Headers have already gone out. Close a truncated stream;
                    # never append JSON or invent a successful terminal event.
                    sys.stderr.write("fallback stream interrupted\n")
                return
            body = upstream.read(MAX_PROXY_RESPONSE_BYTES + 1)
            if len(body) > MAX_PROXY_RESPONSE_BYTES:
                raise EngineError("fallback response exceeds transport limit")
            self.send_response(upstream.status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/v1/chat/completions", "/v1/chat/completions/"):
            self._json(404, {"error": "not found"})
            return
        try:
            body = self._read_body()
        except Exception as exc:
            self._json(400, {"error": {"message": f"bad request: {exc}"}})
            return
        if not isinstance(body, dict):
            self._json(400, {"error": {"message": "request body must be an object"}})
            return
        messages = body.get("messages")
        if (not isinstance(messages, list)
                or any(not isinstance(message, dict) for message in messages)):
            self._json(400, {"error": {"message": "messages must be a list of objects"}})
            return
        started = time.time()
        if any(isinstance(m.get("content"), list) for m in messages):
            if self.fallback_url:
                try:
                    self._proxy(json.dumps(body, ensure_ascii=False).encode("utf-8"),
                                stream=body.get("stream") is True)
                except Exception as exc:
                    self._json(502, {"error": {"message": f"fallback failed: {exc}"}})
                return
            self._json(501, {"error": {"message": "NPU endpoint is text-only; "
                                                  "no fallback configured"}})
            return
        if self.response_guards:
            deterministic = response_guards().interval_answer(messages)
            if deterministic is None:
                deterministic = response_guards().private_owner_refusal(messages)
            if deterministic is None:
                deterministic = response_guards().receipt_status_answer(messages)
            if deterministic is not None:
                if body.get("stream"):
                    self._stream_text(deterministic, started)
                else:
                    self._json(200, completion_payload(
                        self.model_name, deterministic,
                        {"generate_tokens": len(deterministic.split())}, int(started)))
                return
        try:
            prompt = render_chat(messages, assistant_prefix=self.assistant_prefix)
        except ValueError as exc:
            self._json(400, {"error": {"message": str(exc)}})
            return
        try:
            max_new_tokens = int(body.get("max_tokens") or 128)
        except (TypeError, ValueError):
            self._json(400, {"error": {"message": "max_tokens must be an integer"}})
            return
        if max_new_tokens <= 0:
            self._json(400, {"error": {"message": "max_tokens must be positive"}})
            return
        if not body.get("stream"):
            try:
                result = self.engine.run(prompt, max_new_tokens)
            except EngineError as exc:
                self._json(503, {"error": {"message": f"engine unavailable: {exc}"}})
                return
            text = (result.get("text") or "").strip()
            text = self._post_guard_text(messages, text)
            self._json(200, completion_payload(self.model_name, text, result, int(started)))
            return

        # Recall answers with receipts may need a deterministic absence guarantee.
        # Buffer only those guarded turns so a stream can never publish prose that
        # the post-guard would have replaced. Ordinary chat still streams token by
        # token.
        if self.response_guards and response_guards().needs_post_guard(messages):
            try:
                result = self.engine.run(prompt, max_new_tokens)
            except EngineError as exc:
                self._json(503, {"error": {"message": f"engine unavailable: {exc}"}})
                return
            text = self._post_guard_text(messages, (result.get("text") or "").strip())
            self._stream_text(text, started)
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

        event({**chunk, "choices": [{"index": 0, "delta": {"role": "assistant", "content": ""},
                                     "finish_reason": None}]})
        final = None
        try:
            for message in self.engine.stream(prompt, max_new_tokens, want_tokens=True):
                if message.get("event") == "token":
                    piece = message.get("text") or ""
                    if piece:
                        event({**chunk, "choices": [{"index": 0, "delta": {"content": piece},
                                                     "finish_reason": None}]})
                elif message.get("event") == "done":
                    final = message
        except EngineError as exc:
            sys.stderr.write(f"engine failed mid-stream: {exc}\n")
        if final is not None and final.get("ok"):
            payload = completion_payload(self.model_name,
                                         (final.get("text") or "").strip(), final,
                                         int(started))
            event({**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                   "usage": payload["usage"], "timings": payload["timings"]})
            event({**chunk, "choices": [], "usage": payload["usage"],
                   "timings": payload["timings"]})
        if writable["value"]:
            try:
                self.wfile.write(b"data: [DONE]\n\n")
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
        self.close_connection = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine", required=True)
    parser.add_argument("--engine-runtime", default=None,
                        help="Absolute runtime .so path for the token engine.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8181)
    parser.add_argument("--context", type=int, default=2048)
    parser.add_argument("--cpus", type=int, default=4)
    parser.add_argument("--max-new-tokens", type=int, default=256)
    parser.add_argument("--model-name", default="lantern-native5-2b")
    parser.add_argument("--assistant-prefix", choices=("think", "plain"), default="think",
                        help="Assistant prompt prefix: plain for V79.6, think for the 2B.")
    parser.add_argument("--response-guards", action="store_true",
                        help="Short-circuit deterministic interval answers and repair "
                             "failed/empty recall status prose; action dispatch stays upstream.")
    parser.add_argument("--fallback-url", default=os.environ.get("RKLLM_FALLBACK_URL"))
    parser.add_argument("--tokenizer-json", default=None,
                        help="Tokenizer JSON for a TOKEN-input engine.")
    args = parser.parse_args()

    tokenizer = None
    if args.tokenizer_json:
        from tokenizers import Tokenizer
        tokenizer = Tokenizer.from_file(args.tokenizer_json)
    engine = Engine(args.engine, args.model, args.context, args.cpus,
                    args.max_new_tokens, tokenizer=tokenizer,
                    runtime_path=args.engine_runtime)
    Handler.engine = engine
    Handler.model_name = args.model_name
    Handler.fallback_url = args.fallback_url
    Handler.response_guards = args.response_guards
    Handler.assistant_prefix = (ASSISTANT_PREFIX if args.assistant_prefix == "think"
                                else PLAIN_ASSISTANT_PREFIX)
    print(f"rkllm_server ready: init_ms={engine.ready.get('init_ms'):.0f} "
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
