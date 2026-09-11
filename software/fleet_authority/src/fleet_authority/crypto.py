from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ed25519, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, allow_nan=False).encode()


def b64decode(value: str, expected: int, label: str) -> bytes:
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{label} must be canonical base64") from exc
    if len(raw) != expected or base64.b64encode(raw).decode() != value:
        raise ValueError(f"{label} has an invalid encoding or length")
    return raw


def fingerprint(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def pem_certificate_fingerprint(cert: x509.Certificate) -> str:
    return fingerprint(cert.public_bytes(serialization.Encoding.DER))


def _write_private(path: Path, raw: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, raw)
        os.fsync(fd)
    finally:
        os.close(fd)


@dataclass(frozen=True)
class AuthorityKeys:
    ca_key: rsa.RSAPrivateKey
    ca_cert: x509.Certificate
    command_key: ed25519.Ed25519PrivateKey

    @classmethod
    def load_or_create(cls, data_dir: Path) -> "AuthorityKeys":
        data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data_dir, 0o700)
        ca_key_path, ca_cert_path = data_dir / "fleet-ca.key", data_dir / "fleet-ca.crt"
        command_path = data_dir / "fleet-command-authority.key"
        if ca_key_path.exists() != ca_cert_path.exists():
            raise RuntimeError("incomplete Fleet CA key material")
        if ca_key_path.exists():
            ca_key = serialization.load_pem_private_key(ca_key_path.read_bytes(), password=None)
            ca_cert = x509.load_pem_x509_certificate(ca_cert_path.read_bytes())
            if not isinstance(ca_key, rsa.RSAPrivateKey) or ca_cert.public_key().public_numbers() != ca_key.public_key().public_numbers():
                raise RuntimeError("Fleet CA certificate does not match its key")
        else:
            ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
            now = datetime.now(UTC)
            subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SAGE Fleet Device CA")])
            ca_cert = (
                x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(ca_key.public_key())
                .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(minutes=5))
                .not_valid_after(now + timedelta(days=3650)).add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
                .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
                .sign(ca_key, hashes.SHA256())
            )
            _write_private(ca_key_path, ca_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
            ca_cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM)); os.chmod(ca_cert_path, 0o644)
        if command_path.exists():
            command_key = serialization.load_pem_private_key(command_path.read_bytes(), password=None)
            if not isinstance(command_key, ed25519.Ed25519PrivateKey):
                raise RuntimeError("command-authority key is not Ed25519")
        else:
            command_key = ed25519.Ed25519PrivateKey.generate()
            _write_private(command_path, command_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        return cls(ca_key, ca_cert, command_key)

    @property
    def command_authority(self) -> dict[str, str]:
        raw = self.command_key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        return {"algorithm": "Ed25519", "public_key": base64.b64encode(raw).decode(), "fingerprint": fingerprint(raw)}

    @property
    def ca_pem(self) -> str:
        return self.ca_cert.public_bytes(serialization.Encoding.PEM).decode()

    def parse_csr(self, pem: str, management_raw: bytes) -> tuple[x509.CertificateSigningRequest, str, str, str]:
        try:
            csr = x509.load_pem_x509_csr(pem.encode())
        except (ValueError, TypeError) as exc:
            raise ValueError("tls_csr_pem must be a valid PEM CSR") from exc
        if not csr.is_signature_valid:
            raise ValueError("TLS CSR signature is invalid")
        spki = csr.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        if isinstance(csr.public_key(), ed25519.Ed25519PublicKey):
            tls_raw = csr.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            if tls_raw == management_raw:
                raise ValueError("TLS key must be separate from the device-management identity")
        csr_fingerprint = fingerprint(csr.public_bytes(serialization.Encoding.DER))
        return csr, csr_fingerprint, fingerprint(spki), csr.public_bytes(serialization.Encoding.PEM).decode()

    def issue_client_certificate(self, csr_pem: str, device_id: str, days: int) -> x509.Certificate:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
        now = datetime.now(UTC)
        uri_device = re.sub(r"[^a-zA-Z0-9._~-]", "-", device_id)
        return (
            x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, device_id)]))
            .issuer_name(self.ca_cert.subject).public_key(csr.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=days))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), critical=True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.UniformResourceIdentifier(f"urn:sage:fleet-device:{uri_device}")]), critical=False)
            .sign(self.ca_key, hashes.SHA256())
        )
