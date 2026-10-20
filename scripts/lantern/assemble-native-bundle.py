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
import re


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
GNOME_RELATIVE = "usr/local/lib/sage-lantern/gnome-pin/"
GNOME_POLICY = "/usr/local/lib/sage-lantern/gnome-pin/"
UNITS = (
    "sage-lantern-early-boot-admission.service",
    "sage-lantern-guarded-session.service",
)
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


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


def _validate_policy_set(capture, launch, native, native_files, library_sha):
    try:
        capture_value = json.loads(capture.read_bytes().decode("utf-8"))
        launch_value = json.loads(launch.read_bytes().decode("utf-8"))
        native_value = json.loads(native.read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("policy_decode") from None
    capture_fields = {
        "schema", "contract_version", "machine_id", "boot_id", "capture_id",
        "generation", "authority_file", "source_directory", "capture_directory",
        "consumer_sha256", "python_sha256", "payload_digests",
    }
    if (type(capture_value) is not dict or set(capture_value) != capture_fields
            or capture_value.get("contract_version") != 3
            or not _MACHINE.fullmatch(str(capture_value.get("machine_id", "")))
            or not _UUID.fullmatch(str(capture_value.get("boot_id", "")))
            or not _UUID.fullmatch(str(capture_value.get("capture_id", "")))
            or type(capture_value.get("generation")) is not int
            or capture_value["generation"] < 1
            or type(capture_value.get("payload_digests")) is not dict
            or type(capture_value.get("consumer_sha256")) is not str
            or not _DIGEST.fullmatch(capture_value["consumer_sha256"])
            or type(capture_value.get("python_sha256")) is not str
            or not _DIGEST.fullmatch(capture_value["python_sha256"])):
        raise ValueError("capture_policy_invalid")
    native_files = dict(native_files)
    native_files["/usr/lib/gnome-shell/libshell-18.so"] = library_sha
    if (type(native_value) is not dict
            or set(native_value) != {
                "schema", "uid", "overlay", "executable_sha256", "files",
                "native_socket", "broker_socket", "broker_uid"}
            or native_value.get("schema") != POLICIES["native-policy.json"]
            or native_value.get("uid") != 1000
            or native_value.get("overlay")
            != "/org/gnome/shell=/usr/local/lib/sage-lantern/gnome-pin/overlay"
            or native_value.get("native_socket")
            != "/run/sage-lantern-desktop/native.sock"
            or native_value.get("broker_socket")
            != "/run/sage-lantern-desktop-auth/control.sock"
            or type(native_value.get("broker_uid")) is not int
            or native_value["broker_uid"] <= 0
            or native_value.get("files") != native_files
            or not _DIGEST.fullmatch(str(native_value.get("executable_sha256")))):
        raise ValueError("native_policy_invalid")
    native_digest = hashlib.sha256(native.read_bytes()).hexdigest()
    expected_files = {
        "/etc/sage-lantern/desktop-native-policy.json": native_digest,
        **native_files,
    }
    if (type(launch_value) is not dict
            or set(launch_value) != {
                "schema", "uid", "gid", "account", "home", "native_policy",
                "native_policy_sha256", "files", "activation_file"}
            or launch_value.get("schema")
            != POLICIES["guarded-launch-policy.json"]
            or launch_value.get("uid") != 1000
            or launch_value.get("gid") != 1000
            or launch_value.get("account") != "sage"
            or launch_value.get("home") != "/home/sage"
            or launch_value.get("native_policy")
            != "/etc/sage-lantern/desktop-native-policy.json"
            or launch_value.get("native_policy_sha256") != native_digest
            or launch_value.get("files") != expected_files
            or launch_value.get("activation_file")
            != "/etc/sage-lantern/desktop-pin-activation.json"):
        raise ValueError("guarded_launch_policy_invalid")


def assemble(args):
    output = args.output
    if not output.is_absolute() or output.exists():
        raise ValueError("output_must_be_absolute_and_new")
    source_commit = args.source_commit
    if type(source_commit) is not str or len(source_commit) != 40:
        raise ValueError("source_commit_invalid")
    broker = _regular(args.broker)
    bpf = _regular(args.object)
    library = _regular(args.gnome_library)
    gnome = Path(args.gnome_candidate)
    if not gnome.is_absolute() or not (gnome / "manifest.json").is_file():
        raise ValueError("gnome_candidate_invalid")
    try:
        gnome_manifest = json.loads(
            (gnome / "manifest.json").read_bytes().decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ValueError("gnome_manifest_decode") from None
    if (type(gnome_manifest) is not dict
            or gnome_manifest.get("schema")
            != "sage.lantern.gnome-pin-candidate.v1"):
        raise ValueError("gnome_manifest_schema")
    declared = gnome_manifest.get("files")
    if type(declared) is not dict or not declared:
        raise ValueError("gnome_manifest_files_invalid")
    overlay_root = gnome / "overlay"
    found = {
        path.relative_to(overlay_root).as_posix()
        for path in overlay_root.rglob("*")
        if path.is_file()
    }
    if found != set(declared):
        raise ValueError("gnome_overlay_set_mismatch")
    for relative, digest in declared.items():
        if hashlib.sha256((overlay_root / relative).read_bytes()).hexdigest() != digest:
            raise ValueError("gnome_overlay_digest_mismatch")
    if (gnome_manifest.get("gnome_library_sha256")
            != hashlib.sha256(library.read_bytes()).hexdigest()):
        raise ValueError("gnome_library_digest_mismatch")
    capture = _policy(args.capture_policy, POLICIES["capture-policy.json"])
    launch = _policy(
        args.guarded_launch_policy, POLICIES["guarded-launch-policy.json"])
    native = _policy(args.native_policy, POLICIES["native-policy.json"])
    native_files = {
        GNOME_POLICY + "overlay/" + relative: digest
        for relative, digest in declared.items()
    }
    native_files[GNOME_POLICY + "manifest.json"] = hashlib.sha256(
        (gnome / "manifest.json").read_bytes()).hexdigest()
    _validate_policy_set(
        capture, launch, native, native_files,
        hashlib.sha256(library.read_bytes()).hexdigest())
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
            "usr/lib/gnome-shell/libshell-18.so": (library, 0o644),
        }
        for source in sorted(gnome.rglob("*")):
            if source.is_file():
                relative = source.relative_to(gnome).as_posix()
                targets[GNOME_RELATIVE + relative] = (source, 0o644)
        for name, source in LIBS.items():
            targets["usr/local/libexec/" + name] = (SOURCE / source, 0o644)
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
    parser.add_argument("--gnome-candidate", type=Path, required=True)
    parser.add_argument("--gnome-library", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        print(json.dumps(assemble(args), sort_keys=True))
    except (ValueError, OSError) as error:
        print(f"assembly_refused: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
