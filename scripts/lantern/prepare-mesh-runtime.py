#!/usr/bin/env python3
"""Offline root-owned mesh configuration preparation; never activates services."""

import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys


class PreparationError(RuntimeError):
    pass


def require(value):
    if not value:
        raise PreparationError("preparation_refused")


def directory(path, owner):
    path = Path(path)
    require(path.is_absolute() and str(path) == os.path.normpath(str(path)))
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            following = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
            info = os.fstat(descriptor)
            require(info.st_uid == owner and not info.st_mode & 0o022)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def read(path, owner):
    path = Path(path)
    parent = directory(path.parent, owner)
    descriptor = None
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner
                and before.st_nlink == 1 and stat.S_IMODE(before.st_mode) in (0o600, 0o640, 0o644)
                and 0 < before.st_size <= 16384)
        data = bytearray()
        while len(data) <= 16384:
            chunk = os.read(descriptor, 16385 - len(data))
            if not chunk:
                break
            data.extend(chunk)
        after = os.fstat(descriptor)
        require(len(data) == before.st_size and (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                == (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        return bytes(data)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def pairs(entries):
    result = {}
    for key, value in entries:
        require(key not in result)
        result[key] = value
    return result


def document(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                      parse_constant=lambda value: require(False))


def environment(raw):
    entries = []
    for line in raw.decode("ascii").splitlines():
        require(re.fullmatch(r"[A-Z][A-Z0-9_]*=[A-Za-z0-9:._-]+", line) is not None)
        entries.append(line.split("=", 1))
    return pairs(entries)


def fingerprint(public):
    return "sha256:" + hashlib.sha256(bytes.fromhex(public)).hexdigest()


def validate(manifest, inventory_raw, provisioned_raw, metadata_raw, radio):
    from sage_lantern.mesh_daemon import validate_config
    from sage_lantern.mesh_serial_owner import RadioBinding

    validate_config(manifest)
    inventory = environment(inventory_raw)
    provisioned = environment(provisioned_raw)
    metadata = environment(metadata_raw)
    fingerprints = {"PERSONAL_GENESIS_FINGERPRINT", "COMET_NODE_FINGERPRINT",
                    "PERSONAL_VALIDATOR_FINGERPRINT", "VOICE_IDENTITY_FINGERPRINT",
                    "PUBLIC_PROPOSER_FINGERPRINT", "TLS_FEDERATION_FINGERPRINT",
                    "RADIO_IDENTITY_FINGERPRINT", "RECOVERY_EVIDENCE_FINGERPRINT"}
    attestations = {"IDENTITY_UNIQUENESS_ATTESTED", "RECOVERY_TEST_ATTESTED",
                    "NO_CLONED_SECRETS_ATTESTED", "NO_PUBLIC_VALIDATOR_KEY_ATTESTED"}
    require(set(inventory) == fingerprints | attestations | {"INVENTORY_SCHEMA", "UNIT_ID", "PERSONAL_CHAIN_ID"})
    require(inventory["INVENTORY_SCHEMA"] == "sage-lantern-identity-v1")
    require(all(re.fullmatch(r"sha256:[0-9a-f]{64}", inventory[key]) for key in fingerprints))
    require(all(inventory[key] == "PASS" for key in attestations))
    require(metadata == {"UNIT_ID": manifest["unit_id"], "PROVISIONING_SCHEMA": "sage-lantern-unit-v1"})
    require(provisioned == {"UNIT_ID": manifest["unit_id"],
                           "INVENTORY_SHA256": hashlib.sha256(inventory_raw).hexdigest()})
    require(inventory["UNIT_ID"] == manifest["unit_id"] and inventory["PERSONAL_CHAIN_ID"] == manifest["chain_id"])
    require(inventory["VOICE_IDENTITY_FINGERPRINT"] == fingerprint(manifest["agent_id"]))
    if "public_mesh" in manifest:
        require(inventory["PUBLIC_PROPOSER_FINGERPRINT"] == fingerprint(manifest["public_mesh"]["author"]))
    require(manifest["agent_id"] != "00" * 32 and manifest["radio"]["public_key"] != "00" * 32)
    binding = asdict(RadioBinding.from_inventory(radio, unit_id=manifest["unit_id"],
                        expected_fingerprint=inventory["RADIO_IDENTITY_FINGERPRINT"]))
    binding.pop("unit_id")
    binding["public_key"] = binding["public_key"].hex()
    require(binding == manifest["radio"])
    require(not manifest["receive_while_locked"] or manifest["messaging_enabled"])
    require(not manifest["dispatch_while_locked"] or manifest["dispatch_enabled"])
    for peer in manifest["peers"]:
        require(peer["agent_id"] != "00" * 32 and peer["radio_public_key"] != "00" * 32)
        require((peer["chain_id"], peer["agent_id"]) != (manifest["chain_id"], manifest["agent_id"]))
    return manifest


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("ascii")


def write(directory_fd, name, content, mode):
    descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=directory_fd)
    try:
        os.fchmod(descriptor, mode)
        remaining = memoryview(content)
        while remaining:
            count = os.write(descriptor, remaining)
            require(count > 0)
            remaining = remaining[count:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.fsync(directory_fd)


def prepare(*, manifest, inventory, provisioned, unit_metadata, radio_inventory, output_dir, owner=0):
    require(os.geteuid() == owner)
    from sage_lantern.mesh_daemon import device_policy

    inputs = {name: read(path, owner) for name, path in (
        ("manifest", manifest), ("inventory", inventory), ("provisioned", provisioned),
        ("unit_metadata", unit_metadata), ("radio_inventory", radio_inventory))}
    config = validate(document(inputs["manifest"]), inputs["inventory"], inputs["provisioned"],
                      inputs["unit_metadata"], document(inputs["radio_inventory"]))
    artifacts = {"mesh-runtime.json": encoded(config), "20-radio.conf": device_policy(config).encode("ascii")}
    require(len(artifacts["mesh-runtime.json"]) <= 16384)
    output = Path(output_dir)
    parent = directory(output.parent, owner)
    target = None
    try:
        os.mkdir(output.name, 0o700, dir_fd=parent)
        os.fsync(parent)
        target = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        os.fchmod(target, 0o700)
        for name, content in artifacts.items():
            write(target, name, content, 0o600 if name.endswith("json") else 0o644)
        receipt = {"schema": "sage.lantern.mesh-preparation.v1", "unit_id": config["unit_id"],
                   "artifacts": {name: hashlib.sha256(content).hexdigest() for name, content in artifacts.items()},
                   "inputs": {name: hashlib.sha256(content).hexdigest() for name, content in inputs.items()}}
        write(target, "PREPARED.json", encoded(receipt), 0o600)
        os.fsync(parent)
    finally:
        if target is not None:
            os.close(target)
        os.close(parent)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ("manifest", "inventory", "provisioned", "unit-metadata", "radio-inventory", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        prepare(**vars(args))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, ImportError):
        print("Mesh preparation refused; preserve any partial bundle for review.", file=sys.stderr)
        return 1
    print("Prepared only: no installation, service activation, keys or grants. Installer verification remains required.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
