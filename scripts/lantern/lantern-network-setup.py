#!/usr/bin/env python3
"""Staged, root-operated network setup on a fixed SSH-tunnel loopback endpoint.

Never install as an unattended service. The capability is printed only to the
controlling terminal and stored hashed in memory. Finish/expiry blocks new work
and late responses, but cannot undo an already-admitted network change. A lost
connect response requires status reconciliation, not a claim of rollback.
The trusted adapter must bound subprocess/network operations and never log Wi-Fi
credentials. This broker neither implements shell commands nor SAGE/PIN access.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import secrets
import signal
import socket
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
import stat
import threading
import time


ADDRESS = ("127.0.0.1", 3195)
HOST = "127.0.0.1:3195"
ORIGIN = "http://127.0.0.1:3100"
METADATA = Path("/etc/sage-lantern/unit.env")
ROUTES = frozenset({"/status", "/scan", "/connect", "/finish"})
MAX_BODY = 4096
MAX_RESPONSE = 32768
TTL_SECONDS = 900
MAX_WORKERS = 8
READ_TIMEOUT = 2


class SetupError(Exception):
    def __init__(self, status, code):
        self.status = status
        self.code = code
        super().__init__(code)


def unique_object(pairs):
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError
        result[name] = value
    return result


def invalid_constant(value):
    raise ValueError


def valid_unit(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value) is not None


def valid_interface(value):
    return type(value) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,14}", value) is not None


def valid_ssid(value):
    if type(value) is not str or any(ord(character) < 32 or ord(character) == 127 for character in value):
        return False
    try:
        return 1 <= len(value.encode("utf-8")) <= 32
    except UnicodeError:
        return False


def validate_connection(value):
    if type(value) is not dict or set(value) != {"ssid", "password", "security"}:
        raise SetupError(400, "invalid_request")
    ssid, password, security = value["ssid"], value["password"], value["security"]
    if not valid_ssid(ssid) or type(password) is not str or type(security) is not str:
        raise SetupError(400, "invalid_request")
    if security == "open":
        valid = password == ""
    elif security == "wpa-psk":
        valid = (8 <= len(password) <= 63 and all(32 <= ord(character) <= 126 for character in password)
                 or re.fullmatch(r"[0-9a-fA-F]{64}", password) is not None)
    else:
        raise SetupError(400, "unsupported_security")
    if not valid:
        raise SetupError(400, "invalid_request")


def read_prepared_unit(path=METADATA):
    """Read bounded root-owned metadata through non-symlink directory handles."""
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe_metadata")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) & 0o022:
                raise ValueError("unsafe_metadata")
        source = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptor)
        try:
            info = os.fstat(source)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) not in (0o600, 0o640, 0o644) or info.st_size > 256):
                raise ValueError("unsafe_metadata")
            raw = os.read(source, 257)
        finally:
            os.close(source)
    finally:
        os.close(descriptor)
    if len(raw) > 256:
        raise ValueError("invalid_metadata")
    fields = {}
    for line in raw.decode("ascii").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name in fields:
            raise ValueError("invalid_metadata")
        fields[name] = value
    if (set(fields) != {"UNIT_ID", "PROVISIONING_SCHEMA"}
            or fields["PROVISIONING_SCHEMA"] != "sage-lantern-unit-v1" or not valid_unit(fields["UNIT_ID"])):
        raise ValueError("invalid_metadata")
    return fields["UNIT_ID"]


class NetworkSetupBroker:
    def __init__(self, *, unit_id, interface, adapter, token, clock=time.monotonic):
        if (not valid_unit(unit_id) or not valid_interface(interface) or type(token) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None or not callable(clock)):
            raise ValueError("invalid_configuration")
        self.unit_id = unit_id
        self.interface = interface
        self.adapter = adapter
        self.clock = clock
        self._mutex = threading.Lock()
        self._operation = threading.Lock()
        self._digest = hashlib.sha256(token.encode("ascii")).digest()
        self._last_time = self._time()
        self._deadline = self._last_time + TTL_SECONDS

    def _time(self):
        value = self.clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError
        return value

    def _valid(self, token):
        try:
            now = self._time()
            if now < self._last_time or now >= self._deadline:
                self._digest = None
            self._last_time = now
        except Exception:
            self._digest = None
        return (self._digest is not None and type(token) is str
                and re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is not None
                and hmac.compare_digest(self._digest, hashlib.sha256(token.encode("ascii")).digest()))

    def require_token(self, token):
        with self._mutex:
            if not self._valid(token):
                raise SetupError(401, "unauthorized")

    def close(self):
        with self._mutex:
            self._digest = None

    def request(self, path, token, payload):
        self.require_token(token)
        if path not in ROUTES:
            raise SetupError(404, "not_found")
        if path == "/connect":
            validate_connection(payload)
        elif type(payload) is not dict or payload:
            raise SetupError(400, "invalid_request")
        if path == "/finish":
            with self._mutex:
                if not self._valid(token):
                    raise SetupError(401, "unauthorized")
                self._digest = None
            return {"finished": True}
        if not self._operation.acquire(blocking=False):
            raise SetupError(409, "busy")
        try:
            self.require_token(token)
            try:
                if path == "/status":
                    result = self.adapter.status()
                elif path == "/scan":
                    result = {"networks": self.adapter.scan()}
                else:
                    result = self.adapter.connect(**payload)
                result = self._validate_result(path, result)
                if len(json.dumps(result, ensure_ascii=True, separators=(",", ":")).encode("ascii")) > MAX_RESPONSE:
                    raise ValueError
            except Exception:
                raise SetupError(503, "network_unavailable") from None
            self.require_token(token)
            return result
        finally:
            self._operation.release()

    def _validate_result(self, path, result):
        if type(result) is not dict:
            raise ValueError
        if path == "/scan":
            if set(result) != {"networks"} or type(result["networks"]) is not list or len(result["networks"]) > 128:
                raise ValueError
            networks = []
            for network in result["networks"]:
                if (type(network) is not dict or set(network) != {"ssid", "signal", "security"}
                        or not valid_ssid(network["ssid"]) or type(network["signal"]) is not int
                        or not 0 <= network["signal"] <= 100 or type(network["security"]) is not str
                        or network["security"] not in {"open", "wpa-psk", "unsupported"}):
                    raise ValueError
                networks.append(dict(network))
            return {"networks": networks}
        fields = {"connected", "ssid"}
        if (set(result) != fields or type(result["connected"]) is not bool
                or (result["ssid"] is not None and not valid_ssid(result["ssid"]))
                or (result["connected"] and result["ssid"] is None)):
            raise ValueError
        if path == "/status":
            return {"unit_id": self.unit_id, "interface": self.interface, **result}
        return dict(result)


class NetworkSetupHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server_version = "LanternNetworkSetup"
    sys_version = ""

    def setup(self):
        self.request.settimeout(READ_TIMEOUT)
        self.cors = False
        super().setup()

    def handle(self):
        self.read_timer = threading.Timer(READ_TIMEOUT, self._end_read)
        self.read_timer.daemon = True
        self.read_timer.start()
        try:
            super().handle()
        finally:
            self.read_timer.cancel()

    def _end_read(self):
        try:
            self.request.shutdown(socket.SHUT_RD)
        except OSError:
            pass

    def log_message(self, format, *args):
        pass

    def _reply(self, status, payload=None, *, preflight=False):
        self.read_timer.cancel()
        self.close_connection = True
        raw = b"" if payload is None else json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if self.cors:
            self.send_header("Access-Control-Allow-Origin", ORIGIN)
            self.send_header("Vary", "Origin")
            if preflight:
                self.send_header("Access-Control-Allow-Methods", "POST")
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Lantern-Setup")
        self.end_headers()
        self.wfile.write(raw)

    def send_error(self, code, message=None, explain=None):
        self._reply(code, {"error": "invalid_request"})

    def _validate(self):
        names = [name.lower() for name in self.headers]
        if len(names) != len(set(names)) or sum(len(name) + len(value) for name, value in self.headers.items()) > 8192:
            raise SetupError(400, "invalid_request")
        if self.headers.get("Host") != HOST or self.headers.get("Origin") != ORIGIN:
            raise SetupError(403, "forbidden")
        self.cors = True
        if any(name in {"cookie", "authorization", "proxy-authorization", "forwarded", "transfer-encoding", "expect", "upgrade"}
               or name.startswith("x-forwarded-") for name in names):
            raise SetupError(400, "invalid_request")
        if self.path not in ROUTES:
            raise SetupError(404, "not_found")
        if self.command not in ("POST", "OPTIONS"):
            raise SetupError(405, "method_not_allowed")
        if self.command == "OPTIONS":
            requested = [value.strip().lower() for value in self.headers.get("Access-Control-Request-Headers", "").split(",")]
            if (self.headers.get("Access-Control-Request-Method") != "POST"
                    or sorted(requested) != ["content-type", "x-lantern-setup"]
                    or self.headers.get("Content-Length") not in (None, "0")):
                raise SetupError(403, "forbidden")
            return None, 0
        token = self.headers.get("X-Lantern-Setup")
        self.server.broker.require_token(token)
        if self.headers.get("Content-Type") != "application/json":
            raise SetupError(415, "invalid_request")
        length = self.headers.get("Content-Length", "")
        if re.fullmatch(r"(?:0|[1-9][0-9]{0,3})", length) is None:
            raise SetupError(400, "invalid_request")
        if int(length) > MAX_BODY:
            raise SetupError(413, "body_too_large")
        return token, int(length)

    def _dispatch(self):
        try:
            token, length = self._validate()
            if self.command == "OPTIONS":
                self._reply(204, preflight=True)
                return
            raw = self.rfile.read(length)
            self.read_timer.cancel()
            if len(raw) != length:
                raise SetupError(400, "invalid_request")
            try:
                payload = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
            except (ValueError, UnicodeError, RecursionError):
                raise SetupError(400, "invalid_request") from None
            result = self.server.broker.request(self.path, token, payload)
            if self.path != "/finish":
                self.server.broker.require_token(token)
            self._reply(200, result)
        except SetupError as error:
            self._reply(error.status, {"error": error.code})
        except TimeoutError:
            self._reply(408, {"error": "request_timeout"})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception:
            self._reply(503, {"error": "network_unavailable"})

    do_POST = _dispatch
    do_OPTIONS = _dispatch
    do_GET = _dispatch
    do_PUT = _dispatch
    do_DELETE = _dispatch
    do_HEAD = _dispatch


class NetworkSetupServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = MAX_WORKERS

    def __init__(self, broker, *, bind_and_activate=True):
        self.broker = broker
        self.slots = threading.BoundedSemaphore(MAX_WORKERS)
        super().__init__(ADDRESS, NetworkSetupHandler, bind_and_activate=bind_and_activate)
        self.timeout = 0.25

    def verify_request(self, request, client_address):
        return client_address[0] == ADDRESS[0]

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()

    def handle_error(self, request, client_address):
        pass

    def server_close(self):
        self.broker.close()
        super().server_close()


def terminal_token(token):
    descriptor = os.open("/dev/tty", os.O_WRONLY | os.O_NOFOLLOW | os.O_NOCTTY)
    try:
        if not os.isatty(descriptor):
            raise ValueError("terminal_required")
        with os.fdopen(descriptor, "w", closefd=False) as stream:
            stream.write("Lantern network setup token (15 minutes): " + token + "\n")
            stream.flush()
    finally:
        os.close(descriptor)


def make_adapter(unit_id, interface):
    from sage_lantern.network_setup import NetworkSetup
    return NetworkSetup(interface=interface)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve", action="store_true", required=True)
    parser.add_argument("--unit-id", required=True)
    parser.add_argument("--interface", required=True)
    args = parser.parse_args(argv)
    if os.geteuid() != 0 or not valid_unit(args.unit_id) or not valid_interface(args.interface):
        parser.error("Root and explicit valid unit/interface are required.")
    server = None
    broker = None
    handlers = {}
    stopped = threading.Event()
    try:
        if read_prepared_unit() != args.unit_id:
            raise ValueError("unit_mismatch")
        adapter = make_adapter(args.unit_id, args.interface)
        token = secrets.token_urlsafe(32)
        broker = NetworkSetupBroker(unit_id=args.unit_id, interface=args.interface, adapter=adapter, token=token)
        server = NetworkSetupServer(broker)
        terminal_token(token)
        token = None
        for signum in (signal.SIGTERM, signal.SIGINT):
            handlers[signum] = signal.signal(signum, lambda received, frame: stopped.set())
        while not stopped.is_set():
            server.handle_request()
    except Exception:
        parser.exit(1, "Network setup unavailable. No device-ready assertion made.\n")
    finally:
        if broker is not None:
            broker.close()
        if server is not None:
            server.server_close()
        for signum, handler in handlers.items():
            signal.signal(signum, handler)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
