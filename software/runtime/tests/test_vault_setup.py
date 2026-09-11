import base64
import hashlib
import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sage_lantern.vault_setup import (
    CONFIRM, ENABLE, LEDGER, RECOVERY, REVOKE, SIGNATURE_HEADERS,
    LoopbackVaultTransport, RootFileSigner, VaultResponse, VaultSetup, VaultSetupError,
    _TransportFailure,
)


PIN = "12345678"
KEY = base64.b64encode(b"x" * 32).decode()
COOKIE = [("Set-Cookie", "sage_session=fixture-token; Path=/; HttpOnly; SameSite=Strict")]


def status_payload(enabled=False, existing=False, confirmed=False):
    payload = {"enabled": enabled, "recovery_backup_confirmed": confirmed}
    if existing or enabled:
        payload.update(algorithm="AES-256-GCM", kdf="Argon2id", vault_path="/private/vault.key")
    return payload


def response(payload, status=200, headers=None):
    return VaultResponse(status, json.dumps(payload).encode(), headers or [])


class Signer:
    def __init__(self):
        self.calls = []

    def sign_request(self, method, path, body):
        self.calls.append((method, path, body))
        return {
            "X-Agent-ID": "a" * 64,
            "X-Signature": hashlib.sha256(method.encode() + path.encode() + body).hexdigest(),
            "X-Timestamp": "123",
            "X-Nonce": f"{len(self.calls):016x}",
        }


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path, body, headers):
        self.calls.append((method, path, body, headers))
        assert headers["X-Signature"] == hashlib.sha256(method.encode() + path.encode() + body).hexdigest()
        assert not any(key.lower() in {"origin", "sec-fetch-site", "authorization", "x-forwarded-for"} for key in headers)
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def client(responses, allowed=True):
    signer, transport = Signer(), Transport(responses)
    return VaultSetup(signer, transport, enable_allowed=lambda: allowed), signer, transport


@pytest.mark.parametrize("enabled,existing,allowed", [(False, False, True), (False, False, False), (False, True, True), (True, True, True)])
def test_status_exact_sanitized_schema(enabled, existing, allowed):
    setup, signer, transport = client([response(status_payload(enabled, existing))], allowed)
    result = setup.status()
    assert result == {
        "enabled": enabled, "recovery_backup_confirmed": False,
        "has_existing_vault": existing, "can_initialize": allowed and not enabled and not existing,
    }
    assert signer.calls == [("GET", LEDGER, b"")]
    assert "vault.key" not in repr(result)


def test_enable_signed_exact_body_and_revokes_cookie():
    setup, signer, transport = client([
        response(status_payload()), response({"ok": True, "recovery_key": KEY}, headers=COOKIE),
        response(status_payload(True)), response({"ok": True, "locked": True}),
    ])
    result = setup.enable(PIN, PIN)
    assert result == {"enabled": True, "recovery_backup_confirmed": False, "has_existing_vault": True, "can_initialize": False, "recovery_key": KEY}
    assert signer.calls == [("GET", LEDGER, b""), ("POST", ENABLE, b'{"passphrase":"12345678"}'), ("GET", LEDGER, b""), ("POST", REVOKE, b"")]
    assert transport.calls[-1][3]["Cookie"] == "sage_session=fixture-token"
    assert all("Cookie" not in call[3] for call in transport.calls[:-1])
    assert len({call[3]["X-Nonce"] for call in transport.calls}) == 4


@pytest.mark.parametrize("pin", ["1234", "1234567", "1" * 13, "１２３４５６７８", "١٢٣٤٥٦٧٨", "1234567 ", "1234567\n", 12345678, None])
def test_invalid_pins_make_no_requests(pin):
    setup, signer, transport = client([])
    for action in (lambda: setup.enable(pin, pin), lambda: setup.recovery(pin), lambda: setup.confirm(pin)):
        with pytest.raises(VaultSetupError):
            action()
    assert transport.calls == [] and signer.calls == []


def test_confirmation_mismatch_and_trusted_policy_block_enable():
    setup, _, transport = client([], allowed=False)
    with pytest.raises(VaultSetupError, match="mismatch"):
        setup.enable(PIN, "87654321")
    with pytest.raises(VaultSetupError, match="not_allowed"):
        setup.enable(PIN, PIN)
    assert transport.calls == []


@pytest.mark.parametrize("enabled", [False, True])
def test_existing_vault_never_reenabled_or_reset(enabled):
    setup, _, transport = client([response(status_payload(enabled, existing=True))])
    with pytest.raises(VaultSetupError, match="already_exists"):
        setup.enable(PIN, PIN)
    assert [call[1] for call in transport.calls] == [LEDGER]


@pytest.mark.parametrize("payload", [{}, {"enabled": True, "recovery_backup_confirmed": False}, {"enabled": 0, "recovery_backup_confirmed": False}, {"enabled": False, "recovery_backup_confirmed": False, "unknown": 1}, {**status_payload(False, True), "vault_path": ""}])
def test_malformed_status_fails_closed(payload):
    setup, _, transport = client([response(payload)])
    with pytest.raises(VaultSetupError, match="invalid_vault_status"):
        setup.enable(PIN, PIN)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("raw", [b"[]", b'{"enabled":false,"enabled":true}', b"\xff", b"x" * 8193, b"NaN"])
def test_malformed_json_rejected(raw):
    setup, _, _ = client([VaultResponse(200, raw)])
    with pytest.raises(VaultSetupError, match="invalid_vault_response"):
        setup.status()


def test_recovery_requires_pin_verified_by_sage():
    setup, _, transport = client([response(status_payload(True)), response({"recovery_key": KEY})])
    assert setup.recovery(PIN) == {"recovery_key": KEY}
    assert transport.calls[1][:3] == ("POST", RECOVERY, b'{"passphrase":"12345678"}')


def test_confirm_verifies_pin_before_acknowledgement():
    setup, _, transport = client([
        response(status_payload(True)), response({"recovery_key": KEY}),
        response({"ok": True, "recovery_backup_confirmed": True}), response(status_payload(True, confirmed=True)),
    ])
    result = setup.confirm(PIN)
    assert result["recovery_backup_confirmed"] is True
    assert "recovery_key" not in result
    assert [call[1] for call in transport.calls] == [LEDGER, RECOVERY, CONFIRM, LEDGER]
    assert transport.calls[2][2] == b""


@pytest.mark.parametrize("operation", ["confirm", "recovery"])
def test_wrong_pin_never_confirms_or_leaks_body(operation):
    setup, _, transport = client([response(status_payload(True)), response({"error": PIN + KEY}, status=401)])
    with pytest.raises(VaultSetupError) as error:
        getattr(setup, operation)(PIN)
    assert PIN not in str(error.value) and KEY not in str(error.value)
    assert CONFIRM not in [call[1] for call in transport.calls]


@pytest.mark.parametrize("operation", ["confirm", "recovery"])
def test_unencrypted_recovery_denied(operation):
    setup, _, transport = client([response(status_payload())])
    with pytest.raises(VaultSetupError, match="not_enabled"):
        getattr(setup, operation)(PIN)
    assert len(transport.calls) == 1


@pytest.mark.parametrize("key", [None, "bad", base64.b64encode(b"short").decode(), "!" * 44, base64.b64encode(b"x" * 33).decode()])
def test_invalid_recovery_key_cannot_confirm(key):
    setup, _, transport = client([response(status_payload(True)), response({"recovery_key": key})])
    with pytest.raises(VaultSetupError, match="invalid_recovery_key"):
        setup.confirm(PIN)
    assert CONFIRM not in [call[1] for call in transport.calls]


@pytest.mark.parametrize("failure", [TimeoutError(PIN + KEY), response({"error": PIN}, status=500), response({"ok": True, "recovery_key": "bad"}, headers=COOKIE)])
def test_partial_enable_reconciles_without_retry(failure):
    responses = [response(status_payload()), failure, response(status_payload(True))]
    if isinstance(failure, VaultResponse) and failure.headers:
        responses.append(response({"ok": True, "locked": True}))
    setup, _, transport = client(responses)
    with pytest.raises(VaultSetupError, match="uncertain") as error:
        setup.enable(PIN, PIN)
    assert error.value.reconciled_status["enabled"] is True
    assert [call[1] for call in transport.calls].count(ENABLE) == 1
    assert PIN not in str(error.value) and KEY not in str(error.value)


def test_handover_rejection_fails_closed():
    setup, _, transport = client([
        response(status_payload()), response({"error": "stale root"}, status=403),
        response({}, status=403),
    ])
    with pytest.raises(VaultSetupError, match="uncertain") as error:
        setup.enable(PIN, PIN)
    assert error.value.reconciled_status is None
    assert [call[1] for call in transport.calls].count(ENABLE) == 1


def test_timeout_after_headers_still_revokes_cookie():
    setup, _, transport = client([
        response(status_payload()), _TransportFailure(COOKIE), response(status_payload(True)),
        response({"ok": True, "locked": True}),
    ])
    with pytest.raises(VaultSetupError, match="uncertain"):
        setup.enable(PIN, PIN)
    assert transport.calls[-1][1] == REVOKE


def test_cookie_revocation_failure_never_returns_recovery_key():
    setup, _, _ = client([
        response(status_payload()), response({"ok": True, "recovery_key": KEY}, headers=COOKIE),
        response(status_payload(True)), TimeoutError("failure"),
    ])
    with pytest.raises(VaultSetupError, match="session_revocation_failed"):
        setup.enable(PIN, PIN)


def test_cookie_on_failed_status_is_revoked():
    setup, _, transport = client([response({}, status=403, headers=COOKIE), response({"ok": True, "locked": True})])
    with pytest.raises(VaultSetupError):
        setup.status()
    assert transport.calls[-1][1] == REVOKE


@pytest.mark.parametrize("method,path", [("GET", "http://evil/"), ("POST", LEDGER + "/disable"), ("POST", LEDGER + "/recover"), ("GET", LEDGER + "?extra=1")])
def test_transport_and_signer_reject_unlisted_routes(tmp_path, method, path):
    with pytest.raises(VaultSetupError, match="not_allowed"):
        LoopbackVaultTransport()(method, path, b"", {})
    with pytest.raises(VaultSetupError, match="not_allowed"):
        RootFileSigner(tmp_path / "absent").sign_request(method, path, b"")


def test_transport_fixed_host_bounded_read_no_redirect():
    calls = []

    class Connection:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("127.0.0.1", 8080, 3)

        def request(self, method, path, body, headers):
            calls.append((method, path, body, headers))

        def getresponse(self):
            return SimpleNamespace(status=302, getheaders=lambda: [("Location", "https://evil/")], read=self.read)

        def read(self, size):
            assert size == 8193
            return b"{}"

        def close(self):
            calls.append("closed")

    with patch("sage_lantern.vault_setup.http.client.HTTPConnection", Connection):
        result = LoopbackVaultTransport(3)("GET", LEDGER, b"", {})
    assert result.status == 302 and len(calls) == 2 and calls[-1] == "closed"


def test_signer_validates_seed_then_uses_sdk_from_file(tmp_path, monkeypatch):
    seed = tmp_path / "fixture.seed"
    seed.write_bytes(b"x" * 32)
    seed.chmod(0o600)
    calls = []

    class Identity:
        @classmethod
        def from_file(cls, path):
            assert path.startswith("/proc/self/fd/")
            assert os.fstat(int(path.rsplit("/", 1)[1])).st_size == 32
            calls.append(path)
            return cls()

        def sign_request(self, method, path, body):
            calls.append((method, path, body))
            return {"signed": "fixture"}

    monkeypatch.setitem(sys.modules, "sage_sdk", SimpleNamespace(AgentIdentity=Identity))
    signer = RootFileSigner(seed)
    assert signer.sign_request("POST", ENABLE, b'{"passphrase":"12345678"}') == {"signed": "fixture"}
    assert calls[-1] == ("POST", ENABLE, b'{"passphrase":"12345678"}')


@pytest.mark.parametrize("kind", ["missing", "short", "long", "permissions", "symlink", "hardlink", "directory"])
def test_signer_rejects_unsafe_seed_without_sdk_read(tmp_path, monkeypatch, kind):
    seed = tmp_path / "fixture.seed"
    if kind == "directory":
        seed.mkdir(mode=0o600)
    elif kind != "missing":
        seed.write_bytes(b"x" * (31 if kind == "short" else 33 if kind == "long" else 32))
        seed.chmod(0o644 if kind == "permissions" else 0o600)
        if kind == "symlink":
            target = tmp_path / "target"
            seed.rename(target)
            seed.symlink_to(target)
        elif kind == "hardlink":
            os.link(seed, tmp_path / "copy")
    identity = SimpleNamespace(from_file=lambda path: pytest.fail("unsafe key reached SDK"))
    monkeypatch.setitem(sys.modules, "sage_sdk", SimpleNamespace(AgentIdentity=identity))
    with pytest.raises(VaultSetupError):
        RootFileSigner(seed).sign_request("GET", LEDGER, b"")


def test_signer_cannot_inject_browser_or_cookie_headers():
    setup, signer, transport = client([])
    signer.sign_request = lambda *args: {**{key: "fixture" for key in SIGNATURE_HEADERS}, "Origin": "http://127.0.0.1:8080"}
    with pytest.raises(VaultSetupError, match="invalid_signer_headers"):
        setup.status()
    assert transport.calls == []
