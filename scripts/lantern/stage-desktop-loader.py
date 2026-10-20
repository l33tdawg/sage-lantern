#!/usr/bin/env python3
"""Freeze/verify a disabled loader review; never attach or install policy."""
import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FLAGS = ("installed_on_c", "kernel_enforcement_qualified", "alternate_launch_closed",
         "candidate_seat_qualified", "desktop_pin_accepted", "deployable")
SOURCES = (
    *("software/security/desktop_loader_candidate/" + name for name in
      ("loader.h", "loader.c", "linux_backend.h", "linux_backend.c",
       "broker_agent.c", "early_boot_admission.py", "native_bundle.py",
       "inspect.c", "README.md")),
    "software/systemd/sage-lantern-early-boot-admission.service",
    "software/systemd/sage-lantern-guarded-session.service",
    "software/security/desktop_guard_policy.h",
    "scripts/tests/native/desktop_loader_replay.c",
    "scripts/tests/native/desktop_loader_linux_replay.c",
    "scripts/tests/native/desktop_loader_broker_replay.c",
    "scripts/tests/native/desktop_loader_child_identity_replay.c",
    "scripts/tests/test_desktop_loader_candidate.py",
    "scripts/tests/test_native_broker_build.py",
    "scripts/tests/test_stage_desktop_loader.py",
    "scripts/lantern/build-native-broker.sh",
    "scripts/lantern/build-native-broker-in-container.sh",
    "scripts/lantern/build-native-bpf.sh",
    "scripts/lantern/assemble-native-bundle.py",
    "scripts/lantern/native-broker-builder.Dockerfile",
    "scripts/lantern/stage-native-bundle.py",
    "docs/lantern/evidence/NATIVE_BUNDLE_ASSEMBLY_20261020.json",
    "docs/lantern/evidence/NATIVE_BUNDLE_UNIT_C_BTF_20261020.json",
    "docs/lantern/evidence/NATIVE_BOOT_CHAIN_UNIT_C_20261020.json",
    "docs/lantern/evidence/NVME_COUNTER_UNIT_C_20261020.json",
    "scripts/lantern/stage-desktop-loader.py",
    "docs/lantern/DESKTOP_LOADER_CANDIDATE.md",
)
BUILD = ("desktop-loader-inspect", "loader-replay", "linux-replay", "object-inspection.txt",
         "loader-replay.txt", "linux-replay.txt", "build-tools.txt", "verify.sh", "Dockerfile")

def digest(body):
    return hashlib.sha256(body).hexdigest()

def unique(pairs):
    value = {}
    for name, item in pairs:
        if name in value:
            raise ValueError("duplicate_key")
        value[name] = item
    return value

def stage(build, output):
    if output.exists():
        raise FileExistsError(output)
    predecessor = ROOT / "work/c-desktop-confinement-20261002/review-bundle"
    prior = json.loads((predecessor / "manifest.json").read_text(), object_pairs_hook=unique)
    object_body = (predecessor / "object/desktop_guard.bpf.o").read_bytes()
    if digest(object_body) != prior["files"]["object/desktop_guard.bpf.o"]:
        raise ValueError("predecessor_object_changed")
    header = (ROOT / "software/security/desktop_guard_policy.h").read_bytes()
    if digest(header) != prior["files"]["source/software/security/desktop_guard_policy.h"]:
        raise ValueError("predecessor_header_changed")
    payloads = {"source/" + name: (ROOT / name).read_bytes() for name in SOURCES}
    payloads.update({"build/" + name: (build / name).read_bytes() for name in BUILD})
    payloads["object/desktop_guard.bpf.o"] = object_body
    payloads["evidence/predecessor-manifest.json"] = (predecessor / "manifest.json").read_bytes()
    manifest = dict(schema="sage.lantern.desktop-loader-review.v1",
        **{flag: False for flag in FLAGS}, live_changes=[], uid_boundary=1000,
        native_prepare="always EOPNOTSUPP; integration unqualified",
        predecessor_manifest_sha256=digest(payloads["evidence/predecessor-manifest.json"]),
        files={name: digest(body) for name, body in sorted(payloads.items())})
    output.mkdir(parents=True)
    for name, body in payloads.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest

def verify(directory):
    directory = directory.resolve(strict=True)
    value = json.loads((directory / "manifest.json").read_text(), object_pairs_hook=unique)
    if (value.get("schema") != "sage.lantern.desktop-loader-review.v1" or
        any(value.get(flag) is not False for flag in FLAGS) or value.get("live_changes") != [] or
        type(value.get("uid_boundary")) is not int or value["uid_boundary"] != 1000 or
        value.get("native_prepare") != "always EOPNOTSUPP; integration unqualified" or
        type(value.get("files")) is not dict):
        raise ValueError("review_contract_invalid")
    required = {"source/" + name for name in SOURCES} | {"build/" + name for name in BUILD} | {
        "object/desktop_guard.bpf.o", "evidence/predecessor-manifest.json"}
    if set(value["files"]) != required:
        raise ValueError("payload_set_invalid")
    for name, expected in value["files"].items():
        relative = Path(name)
        path = directory / relative
        if (type(expected) is not str or len(expected) != 64 or
            relative.is_absolute() or ".." in relative.parts or str(relative) != name or
            path.is_symlink() or any(parent.is_symlink() for parent in path.parents) or
            digest(path.read_bytes()) != expected):
            raise ValueError("payload_invalid")
    expected_dirs = {str(parent) for name in required for parent in Path(name).parents
                     if str(parent) != "."}
    entries = list(directory.rglob("*"))
    actual = {str(path.relative_to(directory)) for path in entries}
    if any(path.is_symlink() for path in entries) or actual != required | expected_dirs | {"manifest.json"}:
        raise ValueError("unexpected_payload")
    if value["predecessor_manifest_sha256"] != value["files"]["evidence/predecessor-manifest.json"]:
        raise ValueError("predecessor_identity_invalid")
    return len(required)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify is not None:
        count = verify(args.verify)
    elif args.build is not None and args.output is not None:
        count = len(stage(args.build, args.output)["files"])
    else:
        parser.error("use --verify or both --build and --output")
    print(json.dumps(dict(review_payloads=count, deployable=False, kernel_enforcement_qualified=False)))

if __name__ == "__main__":
    main()
