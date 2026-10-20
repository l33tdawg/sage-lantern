"""Read-only early-boot admission gate for the native launch authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import os


SCHEMA = "sage.lantern.early-boot-admission.v1"
_UUID = re.compile(
    r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")
_UID = re.compile(r"Uid:\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)\s+([0-9]+)")


class AdmissionRefused(ValueError):
    pass


@dataclass(frozen=True)
class AdmissionReceipt:
    boot_id: str
    uid: int
    generation: int
    scanned_pids: int
    digest: str
    runtime_authorized: bool = False


def _read(root, relative, limit=4096):
    path = root / relative
    try:
        info = path.lstat()
    except OSError:
        return None
    if not path.is_file() or path.is_symlink() or info.st_size > limit:
        return None
    return path.read_bytes()


def _policy(value, boot_id, uid):
    if (type(value) is not dict or set(value) != {
            "schema", "boot_id", "uid", "generation"}
            or value["schema"] != SCHEMA or value["boot_id"] != boot_id
            or value["uid"] != uid or type(value["generation"]) is not int
            or value["generation"] < 1):
        raise AdmissionRefused("early_boot_policy_mismatch")
    return value


def _uid_map(root):
    raw = _read(root, "proc/self/uid_map", 1024)
    if raw is None:
        raise AdmissionRefused("uid_map_missing")
    fields = raw.decode("ascii", "strict").split()
    if fields != ["0", "0", "4294967295"]:
        raise AdmissionRefused("initial_uid_mapping_required")


def _uid_of(root, pid):
    raw = _read(root, f"proc/{pid}/status", 8192)
    if raw is None:
        return None
    match = _UID.search(raw.decode("utf-8", "replace"))
    if match is None:
        return None
    return int(match.group(1))


def observe(root="/", *, expected_uid=1000, generation=1):
    """Return a receipt only when no user process exists in this boot."""
    root = Path(root)
    boot_raw = _read(root, "proc/sys/kernel/random/boot_id", 128)
    if boot_raw is None:
        raise AdmissionRefused("boot_identity_missing")
    boot_id = boot_raw.decode("ascii", "replace").strip()
    if not _UUID.fullmatch(boot_id):
        raise AdmissionRefused("boot_identity_invalid")
    _uid_map(root)
    policy = {
        "schema": SCHEMA,
        "boot_id": boot_id,
        "uid": expected_uid,
        "generation": generation,
    }
    _policy(policy, boot_id, expected_uid)
    scanned = 0
    for entry in (root / "proc").iterdir():
        if not entry.name.isdigit():
            continue
        scanned += 1
        if _uid_of(root, entry.name) == expected_uid:
            raise AdmissionRefused("user_process_already_running")
    digest = hashlib.sha256(json.dumps(
        policy, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return AdmissionReceipt(
        boot_id, expected_uid, generation, scanned, digest)


def current_boot_id(root="/"):
    raw = _read(Path(root), "proc/sys/kernel/random/boot_id", 128)
    if raw is None:
        raise AdmissionRefused("boot_identity_missing")
    value = raw.decode("ascii", "replace").strip()
    if not _UUID.fullmatch(value):
        raise AdmissionRefused("boot_identity_invalid")
    return value


def receipt_current(receipt, root="/"):
    return (type(receipt) is AdmissionReceipt
            and receipt.runtime_authorized is False
            and receipt.boot_id == current_boot_id(root))
