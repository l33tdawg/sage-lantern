#!/usr/bin/env python3
"""Emit a public-only identity manifest for a personal SAGE node.

The helper reads local private-key containers only to derive and cross-check
their Ed25519 public keys. It never prints or writes private material.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path

from nacl.signing import SigningKey


SCHEMA = "sage.lantern.personal-public-identity.v1"


def sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def ed25519_public(private: bytes, label: str) -> bytes:
    if len(private) == 32:
        seed = private
        embedded_public = None
    elif len(private) == 64:
        seed = private[:32]
        embedded_public = private[32:]
    else:
        raise ValueError(f"{label} must contain a 32-byte seed or 64-byte Ed25519 key")
    public = bytes(SigningKey(seed).verify_key)
    if embedded_public is not None and embedded_public != public:
        raise ValueError(f"{label} embedded public key does not match its seed")
    return public


def json_key(path: Path, field: str, label: str) -> tuple[str, bytes]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    key = payload[field]
    key_type = str(key.get("type", ""))
    raw = base64.b64decode(key["value"], validate=True)
    return key_type, raw


def public_payload(public_key: bytes) -> dict[str, str]:
    return {"algorithm": "ed25519", "public_key_hex": public_key.hex()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sage-home", required=True, type=Path)
    parser.add_argument("--voice-key", type=Path)
    args = parser.parse_args()

    home = args.sage_home.resolve()
    comet = home / "data/cometbft/config"
    genesis_path = comet / "genesis.json"
    genesis_bytes = genesis_path.read_bytes()
    genesis = json.loads(genesis_bytes)

    node_type, node_private = json_key(comet / "node_key.json", "priv_key", "Comet node key")
    if "Ed25519" not in node_type:
        raise ValueError(f"Comet node key is not Ed25519: {node_type}")
    node_public = ed25519_public(node_private, "Comet node key")

    validator_file = json.loads((comet / "priv_validator_key.json").read_text(encoding="utf-8"))
    validator_type = str(validator_file["priv_key"].get("type", ""))
    if "Ed25519" not in validator_type:
        raise ValueError(f"personal validator key is not Ed25519: {validator_type}")
    validator_private = base64.b64decode(validator_file["priv_key"]["value"], validate=True)
    validator_public = ed25519_public(validator_private, "personal validator key")
    recorded_validator_public = base64.b64decode(
        validator_file["pub_key"]["value"], validate=True
    )
    if validator_public != recorded_validator_public:
        raise ValueError("personal validator public key does not match its private key")

    genesis_validators = genesis.get("validators", [])
    if len(genesis_validators) != 1:
        raise ValueError("personal genesis must contain exactly one validator")
    genesis_validator_public = base64.b64decode(
        genesis_validators[0]["pub_key"]["value"], validate=True
    )
    if validator_public != genesis_validator_public:
        raise ValueError("personal validator public key does not match genesis")

    transport_private = (home / "agent.key").read_bytes()
    transport_public = ed25519_public(transport_private, "SAGE transport key")
    transport_agent_id = transport_public.hex()

    node_identity = public_payload(node_public)
    validator_identity = public_payload(validator_public)
    tls_federation_identity = {
        "ca_certificate_fingerprint": sha256((home / "certs/ca.crt").read_bytes()),
        "node_certificate_fingerprint": sha256((home / "certs/node.crt").read_bytes()),
        "transport_agent_id": transport_agent_id,
    }

    result = {
        "schema": SCHEMA,
        "personal_chain_id": genesis["chain_id"],
        "personal_genesis_fingerprint": sha256(genesis_bytes),
        "comet_node": node_identity,
        "comet_node_fingerprint": sha256(canonical(node_identity)),
        "personal_validator": validator_identity,
        "personal_validator_fingerprint": sha256(canonical(validator_identity)),
        "tls_federation": tls_federation_identity,
        "tls_federation_fingerprint": sha256(canonical(tls_federation_identity)),
    }
    if args.voice_key is not None:
        voice_public = ed25519_public(args.voice_key.read_bytes(), "Lantern voice identity")
        voice_identity = public_payload(voice_public)
        result["voice_identity"] = voice_identity
        result["voice_identity_fingerprint"] = sha256(canonical(voice_identity))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
