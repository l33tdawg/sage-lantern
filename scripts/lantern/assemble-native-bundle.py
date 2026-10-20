#!/usr/bin/env python3
"""Assemble a review-only native tree from built and explicitly supplied inputs."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "software/security/desktop_loader_candidate"
SYSTEMD = ROOT / "software/systemd"
SCHEMA = "sage.lantern.desktop-native-bundle.v1"
POLICIES = {
    "capture-policy.json": "sage.lantern.root-gnome-capture-policy.v1",
    "guarded-launch-policy.json": "sage.lantern.guarded-launch-policy.v1",
    "native-policy.json": "sage.lantern.desktop-native-policy.v1",
}
LIBS = {
    "capture-selection-cli.py": "capture-selection-cli.py",
    "early_boot_admission.py": "early_boot_admission.py",
    "frozen_gnome_consumer.py": "frozen_gnome_consumer.py",
    "launch_selection.py": "launch_selection.py",
    "launch_settlement_recorder.py": "launch_settlement_recorder.py",
    "lifecycle_settlement.py": "lifecycle_settlement.py",
    "linux_probe.py": "linux_probe.py",
    "native_probe.py": "native_probe.py",
    "settlement_anchor.py": "settlement_anchor.py",
    "settlement_journal.py": "settlement_journal.py",
    "settlement_owner.py": "settlement_owner.py",
}
UNITS = (
    "sage-lantern-early-boot-admission.service",
    "sage-lantern-guarded-session.service",
)


def _module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    value = importlib.util.module_from_spec(spec)
    sys.modules[name] = value
    spec.loader.exec_module(value)
    return value


def _regular(path):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError:
        raise ValueError(f"input_missing:{path}") from None
    if path.is_symlink() or not path.is_file() or info.st_size < 1:
        raise ValueError(f"input_invalid:{path}")
    return path


def _policy(path, schema):
    path = _regular(path)
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError(f"policy_decode:{path}") from None
    if type(value) is not dict or value.get("schema") != schema:
        raise ValueError(f"policy_schema:{path}")
    return path


def assemble(args):
    output = args.output
    if not output.is_absolute() or output.exists():
        raise ValueError("output_must_be_absolute_and_new")
    source_commit = args.source_commit
    if type(source_commit) is not str or len(source_commit) != 40:
        raise ValueError("source_commit_invalid")
    broker = _regular(args.broker)
    bpf = _regular(args.object)
    capture = _policy(args.capture_policy, POLICIES["capture-policy.json"])
    launch = _policy(
        args.guarded_launch_policy, POLICIES["guarded-launch-policy.json"])
    native = _policy(args.native_policy, POLICIES["native-policy.json"])
    object_sections = _module(
        "native_bundle_object_sections",
        ROOT / "scripts/lantern/stage-desktop-confinement.py").object_sections(
            bpf.read_bytes())
    if len(object_sections) != 13:
        raise ValueError("bpf_hook_set_mismatch")
    output.mkdir(mode=0o700)
    try:
        targets = {
            "bin/sage-lantern-desktop-loader": (broker, 0o755),
            "bpf/desktop_guard.bpf.o": (bpf, 0o644),
            "etc/capture-policy.json": (capture, 0o600),
            "etc/guarded-launch-policy.json": (launch, 0o600),
            "etc/native-policy.json": (native, 0o600),
        }
        for name, source in LIBS.items():
            targets["libexec/" + name] = (SOURCE / source, 0o644)
        for name in UNITS:
            targets["lib/systemd/system/" + name] = (
                SYSTEMD / name, 0o644)
        for relative, (source, mode) in sorted(targets.items()):
            _regular(source)
            destination = output / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            destination.chmod(mode)
        module = _module(
            "native_bundle_assembled",
            SOURCE / "native_bundle.py")
        value = module.build(
            output, source_commit, expected_uid=os.geteuid())
        module.write(output, value)
        module.verify(output, expected_uid=os.geteuid())
    except BaseException:
        # Keep the partial tree for inspection; do not delete user evidence.
        raise
    return {
        "schema": "sage.lantern.native-bundle-assembly.v1",
        "review_only": True,
        "signed": False,
        "installed": False,
        "source_commit": source_commit,
        "files": len(value["files"]),
        "bpf_sections": object_sections,
        "manifest_sha256": hashlib.sha256(
            (output / module.MANIFEST).read_bytes()).hexdigest(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--broker", type=Path, required=True)
    parser.add_argument("--object", type=Path, required=True)
    parser.add_argument("--capture-policy", type=Path, required=True)
    parser.add_argument("--guarded-launch-policy", type=Path, required=True)
    parser.add_argument("--native-policy", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(assemble(args), sort_keys=True))
    except (ValueError, OSError) as error:
        print(f"assembly_refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
