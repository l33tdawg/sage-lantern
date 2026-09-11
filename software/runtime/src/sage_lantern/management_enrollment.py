from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import re
import ssl
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import quote, urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from .management import DeviceManagementIdentity, canonical_json


ENROLLMENT_PROOF_SCHEMA = "sage.fleet.enrollment.proof.v1"
MAX_RESPONSE_BYTES = 262_144


def _atomic_write(path: Path, data: bytes, mode: int) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(temporary, path)
    os.chmod(path, mode)


def _secure_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)


@dataclass(frozen=True)
class TLSIdentity:
    private_key_path: Path
    csr_path: Path
    csr_pem: str
    csr_fingerprint: str

    @classmethod
    def load_or_create(cls, root: Path, management_fingerprint: str) -> "TLSIdentity":
        _secure_root(root)
        key_path = root / "management-tls.key"
        csr_path = root / "management-tls.csr.pem"
        if key_path.exists() != csr_path.exists():
            raise ValueError("incomplete TLS identity; key and CSR must both exist")
        if key_path.exists():
            if key_path.stat().st_mode & 0o077:
                raise ValueError("TLS private key must be owner-only")
            private_key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            csr = x509.load_pem_x509_csr(csr_path.read_bytes())
            if not csr.is_signature_valid:
                raise ValueError("TLS CSR signature is invalid")
            if private_key.public_key().public_numbers() != csr.public_key().public_numbers():
                raise ValueError("TLS CSR does not match private key")
        else:
            private_key = ec.generate_private_key(ec.SECP256R1())
            csr = (
                x509.CertificateSigningRequestBuilder()
                .subject_name(
                    x509.Name(
                        [x509.NameAttribute(NameOID.COMMON_NAME, "sage-fleet-" + management_fingerprint[7:60])]
                    )
                )
                .sign(private_key, hashes.SHA256())
            )
            _atomic_write(
                key_path,
                private_key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                ),
                0o600,
            )
            _atomic_write(csr_path, csr.public_bytes(serialization.Encoding.PEM), 0o600)
        csr_pem = csr.public_bytes(serialization.Encoding.PEM).decode("ascii")
        csr_fingerprint = "sha256:" + hashlib.sha256(csr.public_bytes(serialization.Encoding.DER)).hexdigest()
        return cls(key_path, csr_path, csr_pem, csr_fingerprint)


@dataclass(frozen=True)
class CommandAuthority:
    algorithm: str
    public_key: str
    fingerprint: str

    @classmethod
    def from_value(cls, value: object) -> "CommandAuthority":
        if not isinstance(value, dict) or set(value) != {"algorithm", "public_key", "fingerprint"}:
            raise ValueError("command authority fields are invalid")
        authority = cls(**value)
        if authority.algorithm != "Ed25519":
            raise ValueError("command authority algorithm must be Ed25519")
        try:
            raw = base64.b64decode(authority.public_key, validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("command authority public key must be base64") from exc
        if len(raw) != 32:
            raise ValueError("command authority public key must be 32 bytes")
        expected = "sha256:" + hashlib.sha256(raw).hexdigest()
        if authority.fingerprint != expected:
            raise ValueError("command authority fingerprint does not match public key")
        return authority


@dataclass(frozen=True)
class PendingEnrollment:
    authority_url: str
    enrollment_id: str
    expected_device_id: str
    comparison_code: str
    proof_challenge: str
    management_fingerprint: str
    tls_csr_fingerprint: str
    command_authority: Mapping[str, str]


@dataclass(frozen=True)
class EnrolledDeviceConfig:
    device_id: str
    observation_endpoint: str
    client_certificate: str
    ca_certificate: str
    certificate_fingerprint: str
    command_authority_fingerprint: str


class Response(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...


class Connection(Protocol):
    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None: ...
    def getresponse(self) -> Response: ...
    def close(self) -> None: ...


class EnrollmentClient:
    def __init__(
        self,
        *,
        authority_url: str,
        state_root: Path,
        bootstrap_ca: Path | None = None,
        timeout_seconds: float = 10.0,
        context: ssl.SSLContext | None = None,
        connection_factory: Callable[..., Connection] = http.client.HTTPSConnection,
    ) -> None:
        parsed = urlsplit(authority_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("fleet authority URL must be absolute HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("fleet authority URL must not contain credentials, query, or fragment")
        if parsed.path not in ("", "/"):
            raise ValueError("fleet authority URL must be an origin without a path")
        if timeout_seconds <= 0 or timeout_seconds > 60:
            raise ValueError("enrollment timeout must be between 0 and 60 seconds")
        _secure_root(state_root)
        self.authority_url = authority_url.rstrip("/")
        self.parsed = parsed
        self.state_root = state_root
        self.timeout_seconds = timeout_seconds
        self.context = context or ssl.create_default_context(cafile=str(bootstrap_ca) if bootstrap_ca else None)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.connection_factory = connection_factory

    def _post(self, path: str, value: Mapping[str, Any], expected_status: int) -> dict[str, Any]:
        body = canonical_json(value).encode("utf-8")
        connection = self.connection_factory(
            self.parsed.hostname,
            self.parsed.port or 443,
            timeout=self.timeout_seconds,
            context=self.context,
        )
        try:
            connection.request(
                "POST", path, body,
                {"Accept": "application/json", "Content-Type": "application/json", "Content-Length": str(len(body))},
            )
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
        finally:
            connection.close()
        if len(response_body) > MAX_RESPONSE_BYTES:
            raise ValueError("enrollment response exceeds configured limit")
        if response.status != expected_status:
            raise ValueError(f"fleet authority rejected enrollment request with HTTP {response.status}")
        try:
            result = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("enrollment response is not valid JSON") from exc
        if not isinstance(result, dict):
            raise ValueError("enrollment response must be an object")
        return result

    def claim(
        self,
        *,
        enrollment_id: str,
        setup_secret: str,
        expected_device_id: str,
        identity: DeviceManagementIdentity,
        tls_identity: TLSIdentity,
    ) -> PendingEnrollment:
        if not re.fullmatch(
            r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", enrollment_id
        ):
            raise ValueError("enrollment_id must be a canonical lowercase UUID")
        if not setup_secret or not expected_device_id:
            raise ValueError("enrollment_id, setup_secret, and expected_device_id are required")
        result = self._post(
            f"/v1/enrollments/{quote(enrollment_id, safe='')}/claim",
            {
                "setup_secret": setup_secret,
                "management_public_key": identity.public_key,
                "management_fingerprint": identity.fingerprint,
                "tls_csr_pem": tls_identity.csr_pem,
            },
            200,
        )
        expected = {"enrollment_id", "status", "comparison_code", "proof_challenge", "command_authority"}
        if set(result) != expected or result.get("enrollment_id") != enrollment_id or result.get("status") != "claimed":
            raise ValueError("claim response fields are invalid")
        for name in ("comparison_code", "proof_challenge"):
            if not isinstance(result[name], str) or not result[name]:
                raise ValueError(f"claim response {name} is invalid")
        if not re.fullmatch(r"[0-9]{6}", result["comparison_code"]):
            raise ValueError("claim response comparison_code must contain six ASCII digits")
        authority = CommandAuthority.from_value(result["command_authority"])
        pending = PendingEnrollment(
            authority_url=self.authority_url,
            enrollment_id=enrollment_id,
            expected_device_id=expected_device_id,
            comparison_code=result["comparison_code"],
            proof_challenge=result["proof_challenge"],
            management_fingerprint=identity.fingerprint,
            tls_csr_fingerprint=tls_identity.csr_fingerprint,
            command_authority=asdict(authority),
        )
        _atomic_write(self.state_root / "enrollment-pending.json", canonical_json(asdict(pending)).encode(), 0o600)
        return pending

    def complete(self, *, identity: DeviceManagementIdentity, tls_identity: TLSIdentity) -> EnrolledDeviceConfig:
        pending_path = self.state_root / "enrollment-pending.json"
        if not pending_path.is_file():
            raise ValueError("no claimed enrollment is pending")
        pending_value = json.loads(pending_path.read_text(encoding="utf-8"))
        if not isinstance(pending_value, dict) or set(pending_value) != set(PendingEnrollment.__dataclass_fields__):
            raise ValueError("pending enrollment state is invalid")
        pending = PendingEnrollment(**pending_value)
        if pending.authority_url != self.authority_url:
            raise ValueError("pending enrollment belongs to a different authority")
        if pending.management_fingerprint != identity.fingerprint:
            raise ValueError("pending enrollment belongs to a different management identity")
        if pending.tls_csr_fingerprint != tls_identity.csr_fingerprint:
            raise ValueError("pending enrollment belongs to a different TLS CSR")
        proof = {
            "schema": ENROLLMENT_PROOF_SCHEMA,
            "enrollment_id": pending.enrollment_id,
            "device_id": pending.expected_device_id,
            "management_fingerprint": pending.management_fingerprint,
            "tls_csr_fingerprint": pending.tls_csr_fingerprint,
            "challenge": pending.proof_challenge,
        }
        result = self._post(
            f"/v1/enrollments/{quote(pending.enrollment_id, safe='')}/complete",
            {"proof_signature": identity.sign(proof)},
            200,
        )
        expected = {
            "device_id", "status", "client_certificate_pem", "ca_certificate_pem",
            "certificate_fingerprint", "command_authority",
        }
        if set(result) != expected or result.get("status") != "issued":
            raise ValueError("enrollment completion response fields are invalid")
        if result.get("device_id") != pending.expected_device_id:
            raise ValueError("enrollment completion returned a different device")
        issued_authority = CommandAuthority.from_value(result["command_authority"])
        if asdict(issued_authority) != dict(pending.command_authority):
            raise ValueError("command authority changed during enrollment")
        for name in ("client_certificate_pem", "ca_certificate_pem", "certificate_fingerprint"):
            if not isinstance(result[name], str) or not result[name]:
                raise ValueError(f"enrollment completion {name} is invalid")
        try:
            certificate = x509.load_pem_x509_certificate(result["client_certificate_pem"].encode("ascii"))
            ca_certificate = x509.load_pem_x509_certificate(result["ca_certificate_pem"].encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("issued certificate data is invalid") from exc
        if certificate.public_key().public_numbers() != serialization.load_pem_private_key(
            tls_identity.private_key_path.read_bytes(), password=None
        ).public_key().public_numbers():
            raise ValueError("issued client certificate does not match local TLS key")
        try:
            basic_constraints = ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints).value
            if not basic_constraints.ca:
                raise ValueError("issued trust certificate is not a CA")
            certificate.verify_directly_issued_by(ca_certificate)
        except (ValueError, x509.ExtensionNotFound) as exc:
            raise ValueError("issued client certificate is not signed by the supplied CA") from exc
        expected_cert_fingerprint = "sha256:" + certificate.fingerprint(hashes.SHA256()).hex()
        if result["certificate_fingerprint"] != expected_cert_fingerprint:
            raise ValueError("issued certificate fingerprint is invalid")
        # Parsing and issuer verification above are deliberate: never persist arbitrary text as a trust root.
        cert_path = self.state_root / "management-tls.crt.pem"
        ca_path = self.state_root / "fleet-authority-ca.crt.pem"
        authority_path = self.state_root / "command-authority.json"
        _atomic_write(cert_path, result["client_certificate_pem"].encode("ascii"), 0o600)
        _atomic_write(ca_path, result["ca_certificate_pem"].encode("ascii"), 0o600)
        _atomic_write(authority_path, canonical_json(asdict(issued_authority)).encode(), 0o600)
        config = EnrolledDeviceConfig(
            device_id=result["device_id"],
            observation_endpoint=f"{self.authority_url}/v1/device/observations",
            client_certificate=str(cert_path),
            ca_certificate=str(ca_path),
            certificate_fingerprint=result["certificate_fingerprint"],
            command_authority_fingerprint=issued_authority.fingerprint,
        )
        _atomic_write(self.state_root / "device-agent.json", canonical_json(asdict(config)).encode(), 0o600)
        pending_path.unlink()
        return config


def load_enrolled_config(state_root: Path) -> tuple[EnrolledDeviceConfig, CommandAuthority]:
    config_path = state_root / "device-agent.json"
    pin_path = state_root / "command-authority.json"
    try:
        config_value = json.loads(config_path.read_text(encoding="utf-8"))
        pin_value = json.loads(pin_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("enrolled Device Agent configuration is missing or invalid") from exc
    if not isinstance(config_value, dict) or set(config_value) != set(EnrolledDeviceConfig.__dataclass_fields__):
        raise ValueError("enrolled Device Agent configuration fields are invalid")
    config = EnrolledDeviceConfig(**config_value)
    authority = CommandAuthority.from_value(pin_value)
    if authority.fingerprint != config.command_authority_fingerprint:
        raise ValueError("command authority pin does not match Device Agent configuration")
    expected_paths = {
        "client certificate": (Path(config.client_certificate), state_root / "management-tls.crt.pem"),
        "CA certificate": (Path(config.ca_certificate), state_root / "fleet-authority-ca.crt.pem"),
    }
    for label, (configured, expected) in expected_paths.items():
        if configured.resolve() != expected.resolve():
            raise ValueError(f"configured {label} path is outside Device Agent state")
    return config, authority
