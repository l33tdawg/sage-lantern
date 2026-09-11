#!/usr/bin/env python3
"""Explicit same-device operator approval of an existing private voice identity."""

import argparse
import json
import os
from pathlib import Path
import runpy
import stat
import sys
import time


def seed_bytes(path: Path) -> bytes:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
        raise ValueError(f"Identity must be a private regular file: {path}")
    seed = path.read_bytes()
    if len(seed) != 32:
        raise ValueError("Expected an existing 32-byte SDK identity seed")
    return seed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--sage-home", type=Path, required=True)
    parser.add_argument("--operator-key", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--clearance", type=int, choices=range(4), required=True)
    parser.add_argument("--approve", action="store_true", required=True)
    args = parser.parse_args()
    from sage_sdk import AgentIdentity, SageClient
    from sage_sdk.exceptions import SageNotFoundError
    from sage_lantern.config import RuntimeConfig

    config = RuntimeConfig.load(args.config)
    helper = Path(__file__).with_name("verify-local-voice.py")
    if not helper.exists():
        helper = Path("/usr/local/libexec/sage-lantern-verify-local-voice")
    runpy.run_path(str(helper), run_name="voice_approval_validation")["require_loopback"](config.sage_base_url)
    if not config.sage_identity_path or not config.sage_domain or config.static_context:
        raise ValueError("An existing voice identity and explicit private home domain are required")
    voice_seed = seed_bytes(config.sage_identity_path)
    seed_bytes(args.operator_key)
    voice = AgentIdentity.from_file(config.sage_identity_path)
    operator = AgentIdentity.from_file(args.operator_key)
    if voice.agent_id == operator.agent_id:
        raise ValueError("Voice and operator identities must be distinct")
    client = SageClient(base_url=config.sage_base_url, identity=operator)
    access_path = "/v1/dashboard/network/access"
    access = client._request("GET", access_path).json()
    if access.get("root", {}).get("credential_id") != operator.agent_id:
        raise ValueError("Operator key is not this private node's current Root credential")
    voice_client = SageClient(base_url=config.sage_base_url, identity=voice)
    try:
        voice_client.get_agent(voice.agent_id)
    except SageNotFoundError:
        voice_client.register_agent(name=args.name, role="member", provider="sage-lantern")
    inventory = args.sage_home / "identities"
    inventory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if inventory.is_symlink() or inventory.stat().st_mode & 0o077:
        raise ValueError("SAGE identity inventory must be a private directory")
    target = inventory / f"{voice.agent_id}.key"
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        if seed_bytes(target) != voice_seed:
            raise ValueError("Refusing to overwrite a different local identity")
    else:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(voice_seed)
            stream.flush()
            os.fsync(stream.fileno())
    expected = {"role": "member", "profile": "companion", "home_domain": config.sage_domain,
                "clearance": args.clearance, "capabilities": 15}
    for attempt in range(15):
        access = client._request("GET", access_path).json()
        row = next((item for item in access.get("agents", []) if item.get("agent_id") == voice.agent_id), None)
        if row and row.get("local_key_available"):
            break
        time.sleep(1)
    else:
        raise RuntimeError("Local target consent key is not visible; approval not attempted")
    if row.get("enrollment_active"):
        if any(row.get(key) != value for key, value in expected.items()):
            raise ValueError("Existing enrollment differs; explicit policy review required")
    else:
        result = client._request("PUT", f"{access_path}/agents/{voice.agent_id}/policy", json=expected).json()
        if result.get("ok") is not True:
            raise RuntimeError("Approval was not confirmed")
    for attempt in range(15):
        access = client._request("GET", access_path).json()
        row = next((item for item in access.get("agents", []) if item.get("agent_id") == voice.agent_id), {})
        if row.get("enrollment_active") and all(row.get(key) == value for key, value in expected.items()):
            print(json.dumps({"agent_id": voice.agent_id, "enrollment": "approved", **expected}, sort_keys=True))
            return 0
        time.sleep(1)
    raise RuntimeError("Approval not observed in committed access state")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Private voice approval failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
