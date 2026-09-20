#!/usr/bin/env python3
"""Generate and inspect Foundation pack signing keys.

    pack-signing-key.py generate --out ~/sage-foundation/pack-signing-1.key --note "Foundation pack key 1"
    pack-signing-key.py entry    --key ~/sage-foundation/pack-signing-1.key
    pack-signing-key.py check    --key ~/backups/pack-signing-1.key --trust-root pack-trust-root.json

The custody model is the one used for an Apple Developer ID: one organisation
key, held by the Foundation, generated once, backed up out of band, and never in
the repository. The difference is that a field unit is offline, so there is no
notarisation service to call - the trust anchor ships with the verifier as
``pack-trust-root.json``, and a pack is trusted because it is signed by a key in
that file, not because an authority answered a request.

Which means: **this tool never writes a private key inside the repository**, and
the trust root is edited by hand under review so that adding a signer is a
reviewed commit rather than a local side effect.

The key file is 32 bytes of lowercase hex in a 0600 file (the same shape
``load_signing_key`` accepts, alongside PEM). Back it up twice in separate
places; ``entry`` and ``check`` read a backup and prove it still matches the
public key the trust root records.
"""

from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('knowledge_pack', Path(__file__).with_name('knowledge_pack.py'))
knowledge_pack = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(knowledge_pack)

DEFAULT_TRUST_ROOT = Path(__file__).with_name('pack-trust-root.json')


def require(condition, message):
    if not condition:
        raise knowledge_pack.PackFormatError(message)


def resolve_outside_repo(path: Path) -> Path:
    """A signing key must never live in the repository, not even in a work branch."""
    resolved = path.expanduser().resolve()
    require(not resolved.is_relative_to(REPO_ROOT.resolve()),
            f"{resolved} is inside the repository; a signing key belongs outside it "
            "(the repo is the trust root, not the key store)")
    return resolved


def write_private_key(path: Path, payload: bytes, *, force: bool) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        require(force, f"{path} already exists; pass --force only to replace a key you have "
                       "already backed up and retired")
        path.unlink()
        descriptor = os.open(path, flags, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def entry_for(private_key, *, note: str, created_at: str, status: str) -> dict:
    public_hex = knowledge_pack.public_key_hex(private_key)
    return {"key_id": knowledge_pack.key_id_for(public_hex), "algorithm": "ed25519",
            "public_key_hex": public_hex, "status": status, "created_at": created_at, "note": note}


def generate(args) -> dict:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ed25519

    destination = resolve_outside_repo(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    key = ed25519.Ed25519PrivateKey.generate()
    raw = key.private_bytes(encoding=serialization.Encoding.Raw,
                            format=serialization.PrivateFormat.Raw,
                            encryption_algorithm=serialization.NoEncryption())
    write_private_key(destination, raw.hex().encode("ascii") + b"\n", force=args.force)
    entry = entry_for(key, note=args.note, created_at=args.created_at, status=args.status)
    return {"key_file": str(destination), "mode": oct(destination.stat().st_mode & 0o777),
            "trust_root_entry": entry, "trust_root": str(args.trust_root),
            "next": "paste trust_root_entry into the trust root under review, commit it, then "
                    "sign packs with --signing-key <key_file>"}


def entry(args) -> dict:
    key = knowledge_pack.load_signing_key(resolve_outside_repo(args.key))
    return {"key_file": str(args.key), "trust_root_entry":
            entry_for(key, note=args.note, created_at=args.created_at, status=args.status)}


def check(args) -> dict:
    key = knowledge_pack.load_signing_key(args.key)
    root = knowledge_pack.load_trust_root(args.trust_root)
    public_hex = knowledge_pack.public_key_hex(key)
    identifier = knowledge_pack.key_id_for(public_hex)
    found = [item for item in root["keys"] if item["key_id"] == identifier]
    return {"key_file": str(args.key), "key_id": identifier, "public_key_hex": public_hex,
            "in_trust_root": bool(found), "status": found[0]["status"] if found else None,
            "active_keys": knowledge_pack.active_key_ids(root)}


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def common(subparser, *, key_required=True):
        if key_required:
            subparser.add_argument("--key", type=Path, required=True)
        subparser.add_argument("--created-at", default=None,
                               help="RFC3339 UTC instant; defaults to now")
        subparser.add_argument("--status", choices=knowledge_pack.KEY_STATUSES, default="active")
        subparser.add_argument("--note", default="")
        subparser.add_argument("--trust-root", type=Path, default=DEFAULT_TRUST_ROOT)

    generate_parser = subparsers.add_parser("generate", allow_abbrev=False)
    generate_parser.add_argument("--out", type=Path, required=True)
    generate_parser.add_argument("--force", action="store_true")
    common(generate_parser, key_required=False)
    generate_parser.set_defaults(handler=generate)

    entry_parser = subparsers.add_parser("entry", allow_abbrev=False)
    common(entry_parser)
    entry_parser.set_defaults(handler=entry)

    check_parser = subparsers.add_parser("check", allow_abbrev=False)
    common(check_parser)
    check_parser.set_defaults(handler=check)

    args = parser.parse_args()
    args.created_at = args.created_at or dt.datetime.now(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(json.dumps(args.handler(args), sort_keys=True))


if __name__ == "__main__":
    try:
        main()
    except knowledge_pack.PackFormatError as error:
        print(f"pack-signing-key: {error}", file=sys.stderr)
        sys.exit(2)
