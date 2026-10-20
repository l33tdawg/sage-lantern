"""Exact source manifest for the fitted C-native launch bundle.

The manifest records content and metadata; it does not sign itself or install
anything. A protected installer must verify it before staging any file.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


SCHEMA = "sage.lantern.desktop-native-bundle.v1"
MANIFEST = "manifest.json"
SOURCE_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")

REQUIRED = {
    "bin/sage-lantern-desktop-loader": 0o755,
    "bpf/desktop_guard.bpf.o": 0o644,
    "etc/capture-policy.json": 0o600,
    "etc/guarded-launch-policy.json": 0o600,
    "etc/native-policy.json": 0o600,
    "usr/lib/gnome-shell/libshell-18.so": 0o644,
    "usr/local/lib/sage-lantern/gnome-pin/manifest.json": 0o644,
    "usr/local/lib/sage-lantern/gnome-pin/overlay/ui/init.js": 0o644,
    "usr/local/lib/sage-lantern/gnome-pin/overlay/ui/lanternGuardState.js": 0o644,
    "usr/local/lib/sage-lantern/gnome-pin/overlay/ui/lanternPinGuard.js": 0o644,
    "usr/local/lib/sage-lantern/gnome-pin/overlay/ui/main.js": 0o644,
    "lib/systemd/system/sage-lantern-early-boot-admission.service": 0o644,
    "lib/systemd/system/sage-lantern-guarded-session.service": 0o644,
    "usr/local/libexec/capture-selection-cli.py": 0o644,
    "usr/local/libexec/early_boot_admission.py": 0o644,
    "usr/local/libexec/frozen_gnome_consumer.py": 0o644,
    "usr/local/libexec/launch_selection.py": 0o644,
    "usr/local/libexec/launch_settlement_recorder.py": 0o644,
    "usr/local/libexec/lifecycle_settlement.py": 0o644,
    "usr/local/libexec/linux_probe.py": 0o644,
    "usr/local/libexec/native_probe.py": 0o644,
    "usr/local/libexec/settlement_anchor.py": 0o644,
    "usr/local/libexec/settlement_journal.py": 0o644,
    "usr/local/libexec/settlement_owner.py": 0o644,
}


class BundleRefused(ValueError):
    pass


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8") + b"\n"


def _relative(path):
    if (type(path) is not str or not path or path.startswith("/")
            or "\\" in path or "//" in path or path.endswith("/")):
        raise BundleRefused("bundle_path_invalid")
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise BundleRefused("bundle_path_invalid")
    return parts


def _file(root, relative, mode, expected_uid):
    _relative(relative)
    path = root / relative
    try:
        info = path.lstat()
    except OSError:
        raise BundleRefused(f"bundle_file_missing:{relative}") from None
    if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != mode:
        raise BundleRefused(f"bundle_file_mode:{relative}")
    if info.st_uid != expected_uid or info.st_size < 1 or info.st_size > 64 * 1024 * 1024:
        raise BundleRefused(f"bundle_file_identity:{relative}")
    raw = path.read_bytes()
    if len(raw) != info.st_size:
        raise BundleRefused(f"bundle_file_changed:{relative}")
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size": len(raw),
        "mode": mode,
    }


def scan(root, expected_uid=None):
    """Hash the exact required set and reject any untracked regular file."""
    root = Path(root)
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    if not root.is_absolute() or not root.is_dir():
        raise BundleRefused("bundle_root_invalid")
    files = {}
    for relative, mode in sorted(REQUIRED.items()):
        files[relative] = _file(root, relative, mode, expected_uid)
    found = set()
    for path in root.rglob("*"):
        if path.is_symlink():
            raise BundleRefused("bundle_symlink_present")
        if path.is_file():
            found.add(path.relative_to(root).as_posix())
    if found not in (set(REQUIRED), set(REQUIRED) | {MANIFEST}):
        raise BundleRefused("bundle_file_set_mismatch")
    return files


def build(root, source_commit, *, expected_uid=None):
    if type(source_commit) is not str or not SOURCE_COMMIT.fullmatch(source_commit):
        raise BundleRefused("source_commit_invalid")
    return {
        "schema": SCHEMA,
        "source_commit": source_commit,
        "files": scan(root, expected_uid),
    }


def write(root, value):
    root = Path(root)
    _manifest(value)
    payload = canonical(value)
    temporary = root / f".{MANIFEST}.{os.getpid()}.{secrets.token_hex(6)}.tmp"
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, root / MANIFEST)
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read(root):
    try:
        raw = (Path(root) / MANIFEST).read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        raise BundleRefused("bundle_manifest_invalid") from None
    return _manifest(value)


def verify(root, expected_uid=None):
    value = read(root)
    if scan(root, expected_uid) != value["files"]:
        raise BundleRefused("bundle_manifest_mismatch")
    return value


def _manifest(value):
    if (type(value) is not dict or set(value) != {
            "schema", "source_commit", "files"}
            or value["schema"] != SCHEMA
            or type(value["source_commit"]) is not str
            or not SOURCE_COMMIT.fullmatch(value["source_commit"])
            or type(value["files"]) is not dict
            or set(value["files"]) != set(REQUIRED)):
        raise BundleRefused("bundle_manifest_schema_invalid")
    for relative, record in value["files"].items():
        _relative(relative)
        if (type(record) is not dict or set(record) != {
                "sha256", "size", "mode"}
                or type(record["sha256"]) is not str
                or not _DIGEST.fullmatch(record["sha256"])
                or type(record["size"]) is not int or record["size"] < 1
                or record["mode"] != REQUIRED[relative]):
            raise BundleRefused("bundle_manifest_file_invalid")
    return value
