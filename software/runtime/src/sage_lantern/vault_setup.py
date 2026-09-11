"""Owner-only vault setup component, not a remotely callable authorization layer.

The daemon must enforce owner presence, PIN rate limits and serialized setup.
Neither profile configuration nor session-cookie revocation proves full vault
locking/provisioning readiness. Secrets stay transient; never log return values.
An enable timeout may precede response headers, making an issued session token
unavailable for revocation. Such failures remain uncertain, never successful.
Python secret strings are transient, not guaranteed zeroized memory.
cleanup_confirmed is process-sticky positive completion/cookie-revocation
evidence for the daemon's drain gate. Failed operations conservatively leave it
false even after later success; neither it nor a revoke ACK unloads vault keys.
"""

from __future__ import annotations

import base64
import binascii
import http.client
import json
import os
import re
import stat
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from http.cookies import SimpleCookie
from pathlib import Path


LEDGER = "/v1/dashboard/settings/ledger"
ENABLE = LEDGER + "/enable"
RECOVERY = LEDGER + "/recovery-key"
CONFIRM = RECOVERY + "/confirm"
REVOKE = "/v1/dashboard/auth/lock"
ALLOWED = {("GET", LEDGER), ("POST", ENABLE), ("POST", RECOVERY), ("POST", CONFIRM), ("POST", REVOKE)}
SIGNATURE_HEADERS = {"X-Agent-ID", "X-Signature", "X-Timestamp", "X-Nonce"}


class VaultSetupError(RuntimeError):
    def __init__(self, code: str, *, reconciled_status: dict | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.reconciled_status = reconciled_status


@dataclass(repr=False)
class VaultResponse:
    status: int
    body: bytes
    headers: list[tuple[str, str]] = field(default_factory=list)


class _TransportFailure(Exception):
    def __init__(self, headers):
        super().__init__("vault_transport_failed")
        self.headers = headers


class RootFileSigner:
    """Load only the trusted configured current-Root seed; never create keys.

    SAGE remains authoritative for current Root/Admin identity after handover.
    Linux /proc pins the validated descriptor across SDK from_file's open.
    """

    def __init__(self, seed_path: Path) -> None:
        self.seed_path = Path(seed_path)
        if not self.seed_path.is_absolute():
            raise VaultSetupError("root_key_path_must_be_absolute")

    def sign_request(self, method: str, path: str, body: bytes) -> dict[str, str]:
        if (method, path) not in ALLOWED:
            raise VaultSetupError("vault_route_not_allowed")
        try:
            from sage_sdk import AgentIdentity

            descriptor = os.open(self.seed_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                metadata = os.fstat(descriptor)
                if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size != 32 or metadata.st_nlink != 1 or metadata.st_uid != os.geteuid():
                    raise VaultSetupError("unsafe_root_key")
                identity = AgentIdentity.from_file(f"/proc/self/fd/{descriptor}")
                return identity.sign_request(method, path, body)
            finally:
                os.close(descriptor)
        except VaultSetupError:
            raise
        except Exception:
            raise VaultSetupError("root_signing_unavailable") from None


class LoopbackVaultTransport:
    """Fixed plain-HTTP loopback only; no proxies, redirects or cookie jar."""

    def __init__(self, timeout_seconds: float = 15.0) -> None:
        if not 0 < timeout_seconds <= 60:
            raise ValueError("Invalid vault request timeout")
        self.timeout_seconds = timeout_seconds

    def __call__(self, method: str, path: str, body: bytes, headers: Mapping[str, str]) -> VaultResponse:
        if (method, path) not in ALLOWED:
            raise VaultSetupError("vault_route_not_allowed")
        connection = http.client.HTTPConnection("127.0.0.1", 8080, timeout=self.timeout_seconds)
        response_headers = []
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_headers = response.getheaders()
            raw = response.read(8193)
            return VaultResponse(response.status, raw, response_headers)
        except Exception:
            raise _TransportFailure(response_headers) from None
        finally:
            connection.close()


class VaultSetup:
    def __init__(self, signer, transport=None, *, enable_allowed: Callable[[], bool]) -> None:
        self.signer = signer
        self.transport = transport if transport is not None else LoopbackVaultTransport()
        self.enable_allowed = enable_allowed
        self._lock = threading.Lock()
        self.cleanup_confirmed = True

    def _enable_allowed(self) -> bool:
        try:
            return self.enable_allowed() is True
        except Exception:
            raise VaultSetupError("vault_enable_policy_unavailable") from None

    @staticmethod
    def _pin(pin: str) -> None:
        if type(pin) is not str or re.fullmatch(r"[0-9]{8,12}", pin) is None:
            raise VaultSetupError("pin_must_be_8_to_12_ascii_digits")

    @staticmethod
    def _cookies(headers, cookies: set[str]) -> None:
        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            parsed = SimpleCookie()
            try:
                parsed.load(value)
            except Exception:
                raise VaultSetupError("invalid_vault_cookie") from None
            if "sage_session" in parsed and parsed["sage_session"].value:
                token = parsed["sage_session"].value
                if re.fullmatch(r"[A-Za-z0-9_=-]{1,512}", token) is None:
                    raise VaultSetupError("invalid_vault_cookie")
                cookies.add(token)

    def _request(self, method: str, path: str, payload, cookies: set[str], *, cookie: str | None = None) -> dict:
        if (method, path) not in ALLOWED:
            raise VaultSetupError("vault_route_not_allowed")
        body = b"" if payload is None else json.dumps(payload, separators=(",", ":"), ensure_ascii=True).encode("ascii")
        try:
            signed = self.signer.sign_request(method, path, body)
            if set(signed) != SIGNATURE_HEADERS or any(type(value) is not str or "\r" in value or "\n" in value for value in signed.values()):
                raise VaultSetupError("invalid_signer_headers")
            headers = {**signed, "Content-Type": "application/json"}
            if cookie is not None:
                headers["Cookie"] = "sage_session=" + cookie
            response = self.transport(method, path, body, headers)
        except _TransportFailure as error:
            self._cookies(error.headers, cookies)
            raise VaultSetupError("vault_transport_failed") from None
        except VaultSetupError:
            raise
        except Exception:
            raise VaultSetupError("vault_request_failed") from None
        self._cookies(response.headers, cookies)
        if response.status in (401, 403):
            raise VaultSetupError("vault_authorization_or_pin_rejected")
        if response.status != 200:
            raise VaultSetupError("vault_request_rejected")
        try:
            if type(response.body) is not bytes or len(response.body) > 8192:
                raise ValueError
            payload = json.loads(response.body.decode("utf-8"), object_pairs_hook=self._unique)
            if type(payload) is not dict:
                raise ValueError
            return payload
        except (ValueError, UnicodeError, RecursionError):
            raise VaultSetupError("invalid_vault_response") from None

    @staticmethod
    def _unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result

    def _status(self, cookies: set[str]) -> dict:
        payload = self._request("GET", LEDGER, None, cookies)
        basic = {"enabled", "recovery_backup_confirmed"}
        extended = basic | {"algorithm", "kdf", "vault_path"}
        if set(payload) not in (basic, extended) or type(payload.get("enabled")) is not bool or type(payload.get("recovery_backup_confirmed")) is not bool:
            raise VaultSetupError("invalid_vault_status")
        existing = set(payload) == extended
        if existing:
            if payload["algorithm"] != "AES-256-GCM" or payload["kdf"] != "Argon2id" or type(payload["vault_path"]) is not str or not payload["vault_path"].strip():
                raise VaultSetupError("invalid_vault_status")
        elif payload["enabled"]:
            raise VaultSetupError("invalid_vault_status")
        return {
            "enabled": payload["enabled"],
            "recovery_backup_confirmed": payload["recovery_backup_confirmed"],
            "has_existing_vault": existing,
            "can_initialize": not payload["enabled"] and not existing and self._enable_allowed(),
        }

    @staticmethod
    def _recovery_key(payload: dict) -> str:
        key = payload.get("recovery_key")
        try:
            if type(key) is not str or len(key) != 44:
                raise ValueError
            decoded = base64.b64decode(key, validate=True)
            if len(decoded) != 32 or base64.b64encode(decoded).decode("ascii") != key:
                raise ValueError
        except (ValueError, binascii.Error):
            raise VaultSetupError("invalid_recovery_key") from None
        return key

    def _operate(self, action):
        with self._lock:
            cookies: set[str] = set()
            previous_cleanup = self.cleanup_confirmed
            self.cleanup_confirmed = False
            completed = False
            try:
                result = action(cookies)
                completed = True
                return result
            finally:
                failed = False
                for cookie in tuple(cookies):
                    try:
                        result = self._request("POST", REVOKE, None, set(), cookie=cookie)
                        if result.get("ok") is not True or result.get("locked") is not True:
                            failed = True
                    except Exception:
                        failed = True
                cookies.clear()
                if failed:
                    raise VaultSetupError("session_revocation_failed_reconcile_required") from None
                if completed:
                    self.cleanup_confirmed = previous_cleanup

    def status(self) -> dict:
        return self._operate(self._status)

    def enable(self, pin: str, confirmation: str) -> dict:
        self._pin(pin)
        self._pin(confirmation)
        if pin != confirmation:
            raise VaultSetupError("pin_confirmation_mismatch")

        def action(cookies):
            if not self._enable_allowed():
                raise VaultSetupError("vault_enable_not_allowed")
            current = self._status(cookies)
            if current["enabled"] or current["has_existing_vault"]:
                raise VaultSetupError("vault_already_exists")
            if not current["can_initialize"]:
                raise VaultSetupError("vault_enable_not_allowed")
            try:
                previous_cookies = set(cookies)
                payload = self._request("POST", ENABLE, {"passphrase": pin}, cookies)
                if payload.get("ok") is not True or not cookies.difference(previous_cookies):
                    raise VaultSetupError("invalid_vault_response")
                key = self._recovery_key(payload)
                current = self._status(cookies)
                if not current["enabled"] or not current["has_existing_vault"]:
                    raise VaultSetupError("vault_enable_not_confirmed")
                return {**current, "recovery_key": key}
            except VaultSetupError:
                try:
                    current = self._status(cookies)
                except VaultSetupError:
                    current = None
                raise VaultSetupError("vault_enable_uncertain_reconcile_required", reconciled_status=current) from None

        return self._operate(action)

    def _recover(self, pin: str, cookies: set[str]) -> str:
        current = self._status(cookies)
        if not current["enabled"]:
            raise VaultSetupError("vault_not_enabled")
        return self._recovery_key(self._request("POST", RECOVERY, {"passphrase": pin}, cookies))

    def recovery(self, pin: str) -> dict:
        self._pin(pin)
        return self._operate(lambda cookies: {"recovery_key": self._recover(pin, cookies)})

    def confirm(self, pin: str) -> dict:
        self._pin(pin)

        def action(cookies):
            self._recover(pin, cookies)
            payload = self._request("POST", CONFIRM, None, cookies)
            if payload.get("ok") is not True or payload.get("recovery_backup_confirmed") is not True:
                raise VaultSetupError("vault_confirmation_unverified")
            current = self._status(cookies)
            if not current["enabled"] or not current["recovery_backup_confirmed"]:
                raise VaultSetupError("vault_confirmation_unverified")
            return current

        return self._operate(action)
