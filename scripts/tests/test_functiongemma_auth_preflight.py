"""Synthetic protocol adversaries for the read-only probe, not owner acceptance."""
import hashlib
import importlib
import json
import os
from pathlib import Path
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
from nacl.signing import VerifyKey
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/lantern/benchmarks/tool_routing"))
from auth_preflight import ProbeRefused, ReadOnlyAuthTransport, inspect_signer, load_existing_identity


@pytest.fixture
def sdk(monkeypatch):
    source = ROOT.parent / "sage/sdk/python/src"
    monkeypatch.syspath_prepend(str(source))
    saved = {k: v for k, v in sys.modules.items() if k == "sage_sdk" or k.startswith("sage_sdk.")}
    for name in saved:
        del sys.modules[name]
    yield importlib.import_module("sage_sdk")
    for name in list(sys.modules):
        if name == "sage_sdk" or name.startswith("sage_sdk."):
            del sys.modules[name]
    sys.modules.update(saved)


@pytest.fixture
def node(sdk):
    identity = sdk.AgentIdentity.from_seed(bytes(range(32)))
    profile = dict(agent_id=identity.agent_id, poe_weight=1.0, vote_count=0,
        role="member", home_domain="test-home", enrollment_status="active",
        registration_status="active", approval_required=False, clearance=1,
        can_read=True, can_write=True, access_scope="home_domain")
    grants = dict(domains=["test-home"], owned_domains=["test-home"],
        readable_domains=["test-home"], writable_domains=["test-home"],
        truncated=False, scope="caller")
    state = dict(profile=profile, grants=grants, seen=set(), requests=[], invalid_accepted=False,
                 duplicate=False, redirect=False, drip_headers=False, drip_body=False)

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        def log_message(self, *args):
            pass
        def do_GET(self):
            state["requests"].append(("GET", self.path))
            status = 200
            try:
                agent, signature = self.headers["X-Agent-ID"], self.headers["X-Signature"]
                timestamp, nonce = self.headers["X-Timestamp"], self.headers["X-Nonce"]
                assert agent == identity.agent_id and abs(time.time() - int(timestamp)) < 300
                assert signature not in state["seen"]
                message = hashlib.sha256(b"GET " + self.path.encode() + b"\n").digest()
                message += struct.pack(">q", int(timestamp)) + bytes.fromhex(nonce)
                VerifyKey(bytes.fromhex(agent)).verify(message, bytes.fromhex(signature))
                state["seen"].add(signature)
            except Exception:
                status = 200 if state["invalid_accepted"] else 401
            body = state["grants"] if self.path.endswith("/domains") else state["profile"]
            raw = json.dumps(body if status == 200 else {"error": "denied"}).encode()
            if state["duplicate"] and status == 200:
                raw = b'{"agent_id":"duplicate",' + raw[1:]
            if state["drip_headers"] or state["drip_body"]:
                wire = b'HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: 100\r\n\r\n'
                try:
                    if state["drip_body"]:
                        self.wfile.write(wire)
                        wire = b'x' * 100
                    for byte in wire:
                        self.wfile.write(bytes([byte]))
                        time.sleep(0.02)
                except OSError:
                    pass
                return
            self.send_response(302 if state["redirect"] else status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            if state["redirect"]:
                self.send_header("Location", "https://example.invalid/")
            self.end_headers()
            self.wfile.write(raw)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield identity, "http://127.0.0.1:" + str(server.server_port), state
    server.shutdown()
    server.server_close()
    thread.join(2)


def probe(node, **kwargs):
    identity, endpoint, _ = node
    return inspect_signer(identity, endpoint=endpoint, expected_agent_id=identity.agent_id,
                          expected_home_domain="test-home", **kwargs)


def test_real_sdk_signatures_and_controls_in_synthetic_node(node):
    result = probe(node)
    assert result["signer_authentication_verified"] is True
    assert set(result["negative_controls"].values()) == {401}
    assert result["request_count"] == 9
    assert result["actions_enabled"] is result["lantern_owner_session_verified"] is result["private_node_policy_verified"] is False
    assert all(method == "GET" and path in {"/v1/agent/me", "/v1/agent/me/domains"}
               for method, path in node[2]["requests"])


@pytest.mark.parametrize("field,value", [("can_read", 1), ("can_write", "true"),
    ("approval_required", 0), ("clearance", True), ("clearance", "1"),
    ("home_domain", "other-home"), ("role", "admin"),
    ("access_scope", "global"), ("enrollment_status", "pending_review")])
def test_wire_security_fields_cannot_be_coerced_or_misbound(node, field, value):
    node[2]["profile"][field] = value
    with pytest.raises((ProbeRefused, ValueError)):
        probe(node)
    assert len(node[2]["requests"]) <= 2


@pytest.mark.parametrize("mode", ["duplicate", "redirect", "invalid_accepted"])
def test_malformed_wire_and_missing_auth_enforcement_refuse(node, mode):
    node[2][mode] = True
    with pytest.raises(ProbeRefused):
        probe(node)


def test_home_domain_grant_sample_is_not_assumed_complete(node):
    node[2]["grants"].update(owned_domains=[], truncated=True)
    with pytest.raises(ProbeRefused):
        probe(node)


def test_expected_identity_mismatch_refuses_before_network(node):
    identity, endpoint, state = node
    with pytest.raises(ProbeRefused):
        inspect_signer(identity, endpoint=endpoint, expected_agent_id="a" * 64,
                       expected_home_domain="test-home")
    assert state["requests"] == []


@pytest.mark.parametrize("method,path,body", [("POST", "/v1/memory", b'{}'),
    ("GET", "/v1/memory", b''), ("GET", "/v1/agent/me?scope=all", b''),
    ("GET", "/v1/agent/me", b'{}'), ("PUT", "/v1/agent/me", b'')])
def test_transport_cannot_dispatch_writes_queries_or_other_routes(method, path, body):
    transport = ReadOnlyAuthTransport("http://127.0.0.1:1")
    with pytest.raises(ProbeRefused):
        transport.handle_request(httpx.Request(method, transport.endpoint + path, content=body))
    assert transport.request_count == 0


@pytest.mark.parametrize("endpoint", ["https://127.0.0.1:8080", "http://localhost:8080",
    "http://127.0.0.2:8080", "http://127.0.0.1:0", "http://127.0.0.1:65536",
    "http://127.0.0.1:8080/", "http://127.0.0.1:8080?x=1"])
def test_endpoint_is_fixed_numeric_loopback(endpoint):
    with pytest.raises((ProbeRefused, ValueError)):
        ReadOnlyAuthTransport(endpoint)


def test_total_deadline_never_resets_between_requests():
    now = [100.0]
    transport = ReadOnlyAuthTransport("http://127.0.0.1:1", seconds=2, clock=lambda: now[0])
    now[0] += 1
    assert transport.remaining() == 1
    now[0] += 1
    with pytest.raises(ProbeRefused):
        transport.remaining()


@pytest.mark.parametrize("mode", ["drip_headers", "drip_body"])
def test_total_deadline_interrupts_slow_http_parser(node, mode):
    identity, endpoint, state = node
    state[mode] = True
    transport = ReadOnlyAuthTransport(endpoint, seconds=0.1)
    started = time.monotonic()
    with pytest.raises((ProbeRefused, OSError, httpx.HTTPError)):
        transport.handle_request(httpx.Request("GET", endpoint + "/v1/agent/me",
            headers=identity.sign_request("GET", "/v1/agent/me")))
    assert time.monotonic() - started < 0.75


@pytest.mark.parametrize("unsafe", ["mode", "length", "symlink", "hardlink"])
def test_existing_key_must_be_exact_private_regular_file(sdk, tmp_path, unsafe):
    directory = tmp_path.resolve() / "private"
    directory.mkdir(mode=0o700)
    key = directory / "agent.key"
    key.write_bytes(bytes(range(32)))
    key.chmod(0o600)
    if unsafe == "mode":
        key.chmod(0o644)
    elif unsafe == "length":
        key.write_bytes(bytes(range(33)))
    elif unsafe == "symlink":
        (directory / "link").symlink_to(key)
        key = directory / "link"
    else:
        os.link(key, directory / "hardlink")
    with pytest.raises((ProbeRefused, OSError)):
        load_existing_identity(key)


def test_missing_key_never_creates_identity(sdk, tmp_path):
    path = tmp_path.resolve() / "does-not-exist.key"
    with pytest.raises((ProbeRefused, OSError)):
        load_existing_identity(path)
    assert not path.exists()
