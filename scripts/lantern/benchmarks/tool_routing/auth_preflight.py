"""Read-only real SAGE signer preflight; never issues a Lantern owner session.

Only two fixed GET routes are permitted. No registration, memory retrieval,
private write, PIN, cookie or runtime activation is available. The operator pins
the existing identity and home domain independently of the server response.
Loopback is a locality restriction, not private-node authentication/attestation.
"""
import argparse
import http.client
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time
from urllib.parse import urlsplit

import httpx


ROUTES = frozenset({"/v1/agent/me", "/v1/agent/me/domains"})
MAX_RESPONSE = 65536


class ProbeRefused(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise ProbeRefused("authentication_preflight_refused")


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def load_existing_identity(path):
    """No-follow read of exactly one existing private seed; never creates keys."""
    from sage_sdk import AgentIdentity
    path = Path(path)
    require(path.is_absolute() and ".." not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}
                and not info.st_mode & 0o022)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == os.geteuid()
                and stat.S_IMODE(before.st_mode) == 0o600
                and before.st_nlink == 1 and before.st_size == 32)
        seed = os.read(fd, 33)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mode", "st_uid", "st_nlink", "st_mtime_ns", "st_ctime_ns")
        require(len(seed) == 32 and all(getattr(before, k) == getattr(after, k)
                == getattr(path.lstat(), k) for k in fields))
        return AgentIdentity.from_seed(seed)
    finally:
        os.close(fd)


class ReadOnlyAuthTransport(httpx.BaseTransport):
    def __init__(self, endpoint, *, seconds=15, clock=time.monotonic):
        require(type(endpoint) is str and re.fullmatch(r"http://127\.0\.0\.1:[0-9]{1,5}", endpoint))
        port = urlsplit(endpoint).port
        require(1 <= port <= 65535 and type(seconds) in (int, float)
                and math.isfinite(seconds) and 0 < seconds <= 20)
        self.endpoint, self.port, self.clock = endpoint, port, clock
        self.deadline = clock() + seconds
        self.responses, self.profile_auth, self.request_count = {}, None, 0

    def remaining(self):
        left = self.deadline - self.clock()
        require(left > 0)
        return left

    def handle_request(self, request):
        require(request.method == "GET" and not request.content
                and str(request.url).startswith(self.endpoint + "/")
                and str(request.url.copy_with(path="/", query=None)).rstrip("/") == self.endpoint
                and request.url.raw_path.decode("ascii") in ROUTES
                and self.request_count < 12)
        self.request_count += 1
        path = request.url.raw_path.decode("ascii")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=self.remaining())
        response = None
        stopped, watcher = threading.Event(), None
        try:
            connection.connect()
            sock = connection.sock
            sock.settimeout(self.remaining())
            # Socket timeouts alone reset on each byte. Shutdown also interrupts
            # a peer that drips headers/body forever inside the HTTP parser.
            def watch():
                if not stopped.wait(max(0, self.deadline - self.clock())):
                    try:
                        sock.shutdown(2)
                    except OSError:
                        pass
            watcher = threading.Thread(target=watch, daemon=True, name="read-only-auth-deadline")
            watcher.start()
            headers = dict(request.headers)
            headers.update({"Connection": "close", "Accept-Encoding": "identity"})
            connection.request("GET", path, headers=headers)
            sock.settimeout(self.remaining())
            response = connection.getresponse()
            self.remaining()
            require(response.status in {200, 401, 403, 404}
                    and not response.headers.get_all("Location")
                    and not response.headers.get_all("Set-Cookie")
                    and not response.headers.get_all("Content-Encoding"))
            lengths = response.headers.get_all("Content-Length") or []
            transfers = response.headers.get_all("Transfer-Encoding") or []
            require(not (lengths and transfers) and transfers in ([], ["chunked"]))
            require(len(lengths) <= 1 and (not lengths or
                    lengths[0].isascii() and lengths[0].isdigit()
                    and len(lengths[0]) <= 8 and int(lengths[0]) <= MAX_RESPONSE))
            raw = bytearray()
            while True:
                sock.settimeout(self.remaining())
                chunk = response.read1(min(16384, MAX_RESPONSE + 1 - len(raw)))
                if not chunk:
                    break
                raw.extend(chunk)
                require(len(raw) <= MAX_RESPONSE)
                if response.isclosed():
                    break
            self.remaining()
            require(not lengths or len(raw) == int(lengths[0]))
            if response.status == 200:
                require(response.headers.get_all("Content-Type") == ["application/json"])
                value = json.loads(bytes(raw), object_pairs_hook=unique,
                    parse_constant=lambda _: require(False))
                require(type(value) is dict)
                self.responses[path] = value
                if path == "/v1/agent/me" and self.profile_auth is None:
                    self.profile_auth = dict(request.headers)
            return httpx.Response(response.status, content=bytes(raw), request=request)
        except ProbeRefused:
            raise
        except Exception:
            raise ProbeRefused("authentication_preflight_refused") from None
        finally:
            stopped.set()
            if response is not None:
                response.close()
            connection.close()
            if watcher is not None:
                watcher.join(0.25)
                require(not watcher.is_alive())


def inspect_signer(identity, *, endpoint, expected_agent_id, expected_home_domain):
    """Exercise the real SDK and protocol without returning authorization objects."""
    from sage_sdk import SageClient
    require(type(expected_agent_id) is str and re.fullmatch(r"[0-9a-f]{64}", expected_agent_id)
            and identity.agent_id == expected_agent_id
            and type(expected_home_domain) is str
            and re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", expected_home_domain)
            and expected_home_domain not in {"general", "self", "meta"}
            and not expected_home_domain.startswith(("sage-", "scope:")))
    transport = ReadOnlyAuthTransport(endpoint)
    with SageClient(base_url=endpoint, identity=identity, trust_env=False) as client:
        client._client.close()
        client._client = httpx.Client(base_url=endpoint, transport=transport,
            follow_redirects=False, trust_env=False)
        client.get_profile()
        client.domain_access_sample()
        p, g = transport.responses["/v1/agent/me"], transport.responses["/v1/agent/me/domains"]
        require(p.get("agent_id") == expected_agent_id and p.get("home_domain") == expected_home_domain
                and p.get("role") in {"member", "manager"}
                and p.get("enrollment_status") == p.get("registration_status") == "active"
                and p.get("approval_required") is False and p.get("can_read") is True
                and type(p.get("can_write")) is bool and type(p.get("clearance")) is int
                and 1 <= p["clearance"] <= 4 and p.get("access_scope") == "home_domain")
        for name in ("owned_domains", "readable_domains", "writable_domains"):
            require(type(g.get(name)) is list and all(type(v) is str for v in g[name]))
        require(expected_home_domain in g["owned_domains"] and expected_home_domain in g["readable_domains"]
                and (expected_home_domain in g["writable_domains"]) is p["can_write"]
                and type(g.get("truncated")) is bool)
        controls = {}
        def denied(label, headers):
            status = client._client.get("/v1/agent/me", headers=headers).status_code
            controls[label] = status
            require(status == 401)
        denied("unsigned", {})
        forged = identity.sign_request("GET", "/v1/agent/me")
        forged["X-Signature"] = "0" * 128
        denied("forged_signature", forged)
        other = identity.sign_request("GET", "/v1/agent/me")
        other["X-Agent-ID"] = "0" * 64
        denied("changed_identity", other)
        denied("replayed_request", transport.profile_auth)
        denied("expired_request", identity.sign_request("GET", "/v1/agent/me", timestamp=int(time.time()) - 600))
        denied("changed_path", identity.sign_request("GET", "/v1/agent/me/domains"))
        client.get_profile()
        require(transport.responses["/v1/agent/me"] == p)
        return dict(schema="lantern.sage-signer-preflight.v1", endpoint=endpoint,
            agent_id=expected_agent_id, home_domain=expected_home_domain,
            signer_authentication_verified=True, negative_controls=controls,
            clearance=p["clearance"], home_read_access=True, home_write_access=p["can_write"],
            domain_sample_truncated=g["truncated"], request_count=transport.request_count,
            lantern_owner_session_verified=False, private_node_policy_verified=False,
            actions_enabled=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--identity-file", required=True)
    parser.add_argument("--expected-agent-id", required=True)
    parser.add_argument("--expected-home-domain", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect_signer(load_existing_identity(args.identity_file), endpoint=args.endpoint,
            expected_agent_id=args.expected_agent_id, expected_home_domain=args.expected_home_domain)
        print(json.dumps(result, sort_keys=True))
        return 0
    except Exception:
        # Exceptions can carry server data, headers or credential paths.
        print('{"schema":"lantern.sage-signer-preflight.v1","status":"refused","actions_enabled":false}')
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
