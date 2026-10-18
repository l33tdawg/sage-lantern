"""Bounded local SDK HTTP transport, no retries, redirects, proxies or DNS.

TCP loopback pinning is NOT server authentication. A separate trusted verifier
must attest the owner-controlled node endpoint, chain and private storage policy.
The access predicate must be thread-safe/nonblocking; an undrained watcher fails
closed. In-flight cancellation cannot undo a write already received by the node.
"""
import http.client
import json
import math
import re
import select
import socket
import threading
import time
from urllib.parse import urlsplit

import httpx

from action_session import ActionRefused


MAX_BODY = 262144


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate_json_field")
        value[key] = item
    return value


def _strict_fields(value):
    # Preserve wire types before the SDK's Pydantic models can coerce them.
    if type(value) is list:
        for item in value:
            _strict_fields(item)
    elif type(value) is dict:
        for key, item in value.items():
            if key in {"classification", "clearance", "committed_height"} and type(item) is not int:
                raise ValueError("invalid_security_integer")
            if key in {"can_read", "can_write", "approval_required", "committed", "projection_confirmed", "retryable"} and type(item) is not bool:
                raise ValueError("invalid_security_boolean")
            _strict_fields(item)


class SupervisedLoopbackTransport(httpx.BaseTransport):
    def __init__(self, endpoint, *, clock=time.monotonic):
        if type(endpoint) is not str or not re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", endpoint):
            raise ActionRefused("fixed_loopback_endpoint_required")
        port = urlsplit(endpoint).port
        if not 1 <= port <= 65535:
            raise ActionRefused("fixed_loopback_endpoint_required")
        self.endpoint, self.port, self.clock = endpoint, port, clock
        self._local = threading.local()

    def run(self, callback, *, deadline, access_check):
        if (getattr(self._local, "context", None) is not None
                or type(deadline) not in (int, float) or not math.isfinite(deadline)
                or deadline <= self.clock() or deadline - self.clock() > 30):
            raise ActionRefused("bounded_transport_deadline_required")
        if not callable(getattr(access_check, "dispatch_step", None)):
            raise ActionRefused("serialized_dispatch_authority_required")
        stopped, aborted = threading.Event(), threading.Event()
        gate = threading.Lock()
        state = {"connection": None, "socket": None}

        def check():
            if aborted.is_set() or self.clock() >= deadline or access_check() is not True:
                raise ActionRefused("transport_revoked_or_expired")

        def abort():
            aborted.set()
            with gate:
                connection = state["connection"]
                sock = state["socket"] or (connection.sock if connection is not None else None)
                if sock is not None:
                    try:
                        sock.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass

        def watch():
            while not stopped.wait(0.02):
                try:
                    check()
                except Exception:
                    abort()
                    return

        check()
        self._local.context = (deadline, check, gate, state, access_check.dispatch_step)
        watcher = threading.Thread(target=watch, daemon=True, name="disabled-action-transport")
        watcher.start()
        try:
            result = callback()
            check()
            return result
        finally:
            stopped.set()
            watcher.join(0.25)
            self._local.context = None
            if watcher.is_alive():
                abort()
                raise ActionRefused("transport_watch_not_drained")

    def handle_request(self, request):
        context = getattr(self._local, "context", None)
        if context is None:
            raise ActionRefused("transport_outside_guard")
        deadline, check, gate, state, dispatch_step = context
        check()
        if (str(request.url.copy_with(path="/", query=None)).rstrip("/") != self.endpoint
                or request.method not in {"GET", "POST"} or len(request.content) > 32768):
            raise ActionRefused("transport_endpoint_or_request_mismatch")
        path = request.url.raw_path.decode("ascii")
        if not path.startswith("/v1/"):
            raise ActionRefused("transport_path_mismatch")
        headers = dict(request.headers)
        if headers.get("host") != "127.0.0.1:" + str(self.port):
            raise ActionRefused("transport_host_mismatch")
        headers["Connection"] = "close"
        headers["Accept-Encoding"] = "identity"
        connection = http.client.HTTPConnection("127.0.0.1", self.port,
                                                 timeout=max(0.001, deadline - self.clock()))
        with gate:
            state["connection"] = connection
        response = None
        try:
            check()
            connection.connect()
            check()
            sock = connection.sock
            with gate:
                state["socket"] = sock
            sock.settimeout(max(0.001, deadline - self.clock()))

            def guarded_send(data):
                # HTTPConnection sends headers and body through this primitive.
                # No credential or action byte can start after revocation wins
                # the shared authority gate. Backpressure waits outside it.
                if type(data) not in (bytes, bytearray, memoryview):
                    raise ActionRefused("bounded_transport_bytes_required")
                pending = memoryview(data)
                sock.setblocking(False)
                try:
                    while pending:
                        check()
                        try:
                            sent = dispatch_step(lambda: sock.send(pending[:16384]))
                        except BlockingIOError:
                            check()
                            select.select([], [sock], [], min(0.02, max(0, deadline - self.clock())))
                            continue
                        if sent <= 0:
                            raise ActionRefused("transport_send_unknown")
                        pending = pending[sent:]
                finally:
                    sock.settimeout(max(0.001, deadline - self.clock()))

            connection.send = guarded_send
            connection.request(request.method, path, body=request.content, headers=headers)
            check()
            sock.settimeout(max(0.001, deadline - self.clock()))
            response = connection.getresponse()
            check()
            lengths = response.headers.get_all("Content-Length") or []
            if (not 200 <= response.status < 300
                    or response.headers.get_all("Content-Type") != ["application/json"]
                    or response.headers.get_all("Content-Encoding")
                    or len(lengths) > 1
                    or (lengths and (not lengths[0].isascii() or not lengths[0].isdigit()
                                     or len(lengths[0]) > 8 or int(lengths[0]) > MAX_BODY))):
                raise ActionRefused("transport_response_refused")
            raw = bytearray()
            while True:
                check()
                sock.settimeout(max(0.001, deadline - self.clock()))
                chunk = response.read1(min(16384, MAX_BODY + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                if len(raw) > MAX_BODY:
                    raise ActionRefused("transport_response_oversize")
                if response.isclosed():
                    break
            check()
            if lengths and len(raw) != int(lengths[0]):
                raise ActionRefused("transport_response_truncated")
            value = json.loads(bytes(raw), object_pairs_hook=_unique,
                               parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            _strict_fields(value)
            return httpx.Response(response.status, headers=response.headers.items(), content=bytes(raw), request=request)
        except ActionRefused:
            raise
        except Exception:
            raise ActionRefused("transport_outcome_unknown") from None
        finally:
            if response is not None:
                response.close()
            connection.close()
            with gate:
                state["connection"] = None
                state["socket"] = None
