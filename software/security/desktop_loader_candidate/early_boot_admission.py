"""Read-only early-boot admission gate for the native launch authority."""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat


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


def _receipt_path(path):
    path = Path(path)
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise AdmissionRefused("receipt_path_invalid")
    parent = path.parent
    try:
        info = parent.lstat()
    except OSError:
        raise AdmissionRefused("receipt_directory_unavailable") from None
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise AdmissionRefused("receipt_directory_not_private")
    return path


def _receipt_payload(receipt):
    if (type(receipt) is not AdmissionReceipt
            or receipt.runtime_authorized is not False):
        raise AdmissionRefused("receipt_invalid")
    return {
        "schema": SCHEMA,
        "boot_id": receipt.boot_id,
        "uid": receipt.uid,
        "generation": receipt.generation,
        "scanned_pids": receipt.scanned_pids,
        "digest": receipt.digest,
        "runtime_authorized": False,
    }


def write_receipt(receipt, path):
    """Atomically persist one admission receipt in a private root directory."""
    path = _receipt_path(path)
    raw = json.dumps(_receipt_payload(receipt), sort_keys=True,
                     separators=(",", ":")).encode("utf-8") + b"\n"
    if len(raw) > 4096:
        raise AdmissionRefused("receipt_too_large")
    temporary = path.parent / (
        f".admission.{os.getpid()}.{secrets.token_hex(6)}.tmp")
    descriptor = None
    try:
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600)
        offset = 0
        while offset < len(raw):
            written = os.write(descriptor, raw[offset:])
            if written <= 0:
                raise AdmissionRefused("receipt_write_failed")
            offset += written
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return receipt


def read_receipt(path, *, expected_uid=1000, generation=1, root="/"):
    """Read and validate a receipt written by the early-boot barrier."""
    path = _receipt_path(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise AdmissionRefused("receipt_unavailable") from None
    try:
        before = os.fstat(descriptor)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_nlink != 1 or before.st_size < 1
                or before.st_size > 4096):
            raise AdmissionRefused("receipt_identity_invalid")
        raw = os.read(descriptor, 4097)
        after = os.fstat(descriptor)
        if (len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size,
                    before.st_mtime_ns, before.st_ctime_ns)
                != (after.st_dev, after.st_ino, after.st_size,
                    after.st_mtime_ns, after.st_ctime_ns)):
            raise AdmissionRefused("receipt_changed")
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise AdmissionRefused("receipt_decode") from None
    if type(value) is not dict or set(value) != {
            "schema", "boot_id", "uid", "generation", "scanned_pids",
            "digest", "runtime_authorized"}:
        raise AdmissionRefused("receipt_schema_invalid")
    if (value["schema"] != SCHEMA or value["runtime_authorized"] is not False
            or type(value["boot_id"]) is not str
            or type(value["uid"]) is not int or value["uid"] < 0
            or type(value["generation"]) is not int
            or value["generation"] < 1
            or value["uid"] != expected_uid
            or value["generation"] != generation
            or value["boot_id"] != current_boot_id(root)
            or type(value["scanned_pids"]) is not int
            or value["scanned_pids"] < 0
            or type(value["digest"]) is not str
            or len(value["digest"]) != 64):
        raise AdmissionRefused("receipt_binding_mismatch")
    expected = hashlib.sha256(json.dumps({
        "schema": SCHEMA,
        "boot_id": value["boot_id"],
        "uid": value["uid"],
        "generation": value["generation"],
    }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if value["digest"] != expected:
        raise AdmissionRefused("receipt_digest_mismatch")
    return AdmissionReceipt(
        value["boot_id"], value["uid"], value["generation"],
        value["scanned_pids"], value["digest"])


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path("/"))
    parser.add_argument("--expected-uid", type=int, default=1000)
    parser.add_argument("--generation", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        receipt = observe(
            args.root, expected_uid=args.expected_uid,
            generation=args.generation)
        write_receipt(receipt, args.receipt)
    except (AdmissionRefused, OSError) as error:
        print(json.dumps(dict(admitted=False, reason=str(error)),
                         sort_keys=True))
        return 1
    print(json.dumps(dict(admitted=True, boot_id=receipt.boot_id,
                          generation=receipt.generation,
                          scanned_pids=receipt.scanned_pids,
                          runtime_authorized=False), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
