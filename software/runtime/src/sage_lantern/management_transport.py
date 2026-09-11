from __future__ import annotations

import http.client
import json
import ssl
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlsplit

from .management import SignedObservation


@dataclass(frozen=True)
class MTLSObservationConfig:
    endpoint: str
    ca_certificate: Path | None
    client_certificate: Path
    client_private_key: Path
    timeout_seconds: float = 10.0
    max_response_bytes: int = 65_536

    def validate(self) -> None:
        parsed = urlsplit(self.endpoint)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("management endpoint must be an absolute HTTPS URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("management endpoint must not contain credentials, query, or fragment")
        if not parsed.path or parsed.path == "/":
            raise ValueError("management endpoint must include an observation ingest path")
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("management timeout must be between 0 and 60 seconds")
        if not 1 <= self.max_response_bytes <= 1_048_576:
            raise ValueError("max_response_bytes is outside the supported range")
        paths = {
            "client certificate": self.client_certificate,
            "client private key": self.client_private_key,
        }
        if self.ca_certificate is not None:
            paths["server CA certificate"] = self.ca_certificate
        for label, path in paths.items():
            if not path.is_file():
                raise ValueError(f"{label} does not exist: {path}")
        if self.client_private_key.stat().st_mode & 0o007:
            raise ValueError("client private key must not be accessible to other users")


@dataclass(frozen=True)
class IngestAcknowledgement:
    accepted: bool
    device_id: str
    sequence: int
    state_hash: str
    ingested_at: str


class Response(Protocol):
    status: int

    def read(self, amount: int | None = None) -> bytes: ...


class Connection(Protocol):
    def request(self, method: str, url: str, body: bytes, headers: dict[str, str]) -> None: ...

    def getresponse(self) -> Response: ...

    def close(self) -> None: ...


class MTLSObservationClient:
    def __init__(
        self,
        config: MTLSObservationConfig,
        *,
        context: ssl.SSLContext | None = None,
        connection_factory: Callable[..., Connection] = http.client.HTTPSConnection,
    ) -> None:
        config.validate()
        self.config = config
        self.parsed = urlsplit(config.endpoint)
        if context is None:
            context = ssl.create_default_context(
                cafile=str(config.ca_certificate) if config.ca_certificate is not None else None,
            )
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(config.client_certificate), str(config.client_private_key))
        self.context = context
        self.connection_factory = connection_factory

    def send(self, signed: SignedObservation) -> IngestAcknowledgement:
        body = signed.to_json().encode("utf-8")
        observation = signed.observation
        connection = self.connection_factory(
            self.parsed.hostname,
            self.parsed.port or 443,
            timeout=self.config.timeout_seconds,
            context=self.context,
        )
        try:
            connection.request(
                "POST",
                self.parsed.path,
                body,
                {
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Content-Length": str(len(body)),
                    "Idempotency-Key": f"{observation.device_id}:{observation.sequence}:{observation.state_hash}",
                },
            )
            response = connection.getresponse()
            response_body = response.read(self.config.max_response_bytes + 1)
        finally:
            connection.close()

        if len(response_body) > self.config.max_response_bytes:
            raise ValueError("control-plane response exceeds configured limit")
        if response.status != 202:
            raise ValueError(f"control plane rejected observation with HTTP {response.status}")
        try:
            value = json.loads(response_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError("control-plane acknowledgement is not valid JSON") from exc
        expected = {"accepted", "device_id", "sequence", "state_hash", "ingested_at"}
        if not isinstance(value, dict) or set(value) != expected or value["accepted"] is not True:
            raise ValueError("control-plane acknowledgement fields are invalid")
        if (
            not isinstance(value["device_id"], str)
            or isinstance(value["sequence"], bool)
            or not isinstance(value["sequence"], int)
            or not isinstance(value["state_hash"], str)
            or not isinstance(value["ingested_at"], str)
        ):
            raise ValueError("control-plane acknowledgement field types are invalid")
        try:
            ingested_at = datetime.fromisoformat(value["ingested_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("control-plane acknowledgement timestamp is invalid") from exc
        if ingested_at.tzinfo is None:
            raise ValueError("control-plane acknowledgement timestamp must include a timezone")
        acknowledgement = IngestAcknowledgement(**value)
        if (
            acknowledgement.device_id != observation.device_id
            or acknowledgement.sequence != observation.sequence
            or acknowledgement.state_hash != observation.state_hash
        ):
            raise ValueError("control-plane acknowledgement does not match observation")
        return acknowledgement
