"""Disabled root-filesystem consumer for the accepted GNOME evidence contract.

Library only: no CLI, collector, installer, socket, subprocess or launch call.
The existing native prepare callback remains EOPNOTSUPP. A positive result is
capture eligibility, never runtime authority. Caller/runtime admission and the
complete native asset, BTF, environment, session and early-boot barriers still
need their own qualification.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import re
import stat
import sys
import types


SCHEMA = "sage.lantern.root-gnome-capture-policy.v1"
AUTHORITY_SCHEMA = "sage.lantern.root-gnome-capture-authority.v1"
CURRENT_SCHEMA = "sage.lantern.root-gnome-current-capture.v1"
# Selected by the authenticated bootstrap/store, never by a caller's policy.
CURRENT_RECORD = "/var/lib/sage-lantern-desktop/gnome-capture/current.json"
CONTRACT_ID = "sage.lantern.gnome-boundary.frozen-evidence"
SOURCE_PINS = {
    "__init__.py": "b612afaace1e67718d50a14da4b83d513a2bf216ff2f82c3c64b5fbc23b28a22",
    "analysis.py": "febaf091e10d18437fe86d6a1e7ade2a7bbd52f05660ac6d0d8cf8c50d1aa7bb",
    "evidence_contract.py": "aabd440e77a0aed5827c6b8cfa6836b991b5cf109b25c5912251322d929fff6a",
}
PAYLOAD_NAMES = (
    "contract", "contract_version", "captured_at", "capture_provenance",
    "unit_search_path", "unit_path_proposal", "verified_unit_path", "paths",
    "required_units", "manager_mutations", "extension_inventory",
    "dbus_inventory", "session_selection", "analysis_view_digest",
)
POLICY_FIELDS = {
    "schema", "contract_version", "machine_id", "boot_id", "capture_id",
    "generation", "authority_file", "source_directory", "capture_directory",
    "consumer_sha256", "python_sha256", "payload_digests",
}
AUTHORITY_FIELDS = {
    "schema", "machine_id", "boot_id", "capture_id", "generation", "policy_sha256",
}
CURRENT_FIELDS = {
    "schema", "state", "machine_id", "boot_id", "capture_id", "generation",
    "policy_path", "policy_sha256",
}
_PREFIX = "_lantern_root_gnome_review"
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_MACHINE = re.compile(r"[0-9a-f]{32}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


class CaptureRefusal(ValueError):
    pass


@dataclass(frozen=True)
class CaptureDecision:
    evidence_consumable: bool
    reasons: tuple[str, ...]
    policy_sha256: str | None = None
    analysis_view_digest: str | None = None
    unit_path: str | None = None
    capture_id: str | None = None
    generation: int | None = None
    runtime_authorized: bool = False


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise CaptureRefusal("duplicate_json_key")
        value[key] = item
    return value


def _constant(_value):
    raise CaptureRefusal("invalid_json_constant")


def _json(data, *, exact=True):
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_unique,
                       parse_constant=_constant)
    if exact and canonical(value) != data:
        raise CaptureRefusal("noncanonical_json")
    return value


def _path(value):
    if (type(value) is not str or not value.startswith("/") or "\x00" in value
            or value == "/" or any(p in ("", ".", "..") for p in value.split("/")[1:])):
        raise CaptureRefusal("noncanonical_path")
    return value


def _trusted(info, *, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022
            or (not directory and (info.st_mode & 0o7000 or info.st_nlink != 1))):
        raise CaptureRefusal("untrusted_file_ancestry")


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _open(path):
    """Walk every ancestor by FD; reject symlinks and kiosk-writable components."""
    _path(path)
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW
    directory = os.open("/", flags | os.O_DIRECTORY)
    try:
        _trusted(os.fstat(directory), directory=True)
        parts = path.split("/")[1:]
        for part in parts[:-1]:
            child = os.open(part, flags | os.O_DIRECTORY, dir_fd=directory)
            try:
                _trusted(os.fstat(child), directory=True)
            except BaseException:
                os.close(child)
                raise
            os.close(directory)
            directory = child
        result = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=directory)
        try:
            _trusted(os.fstat(result))
        except BaseException:
            os.close(result)
            raise
        return result
    finally:
        os.close(directory)


def _read(fd, limit):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise CaptureRefusal("payload_size_exceeded")
    return data


class _HeldFiles:
    """Own exact bytes and descriptors, then invalidate on replacement/change."""
    def __init__(self):
        self.files = []

    def take(self, path, limit):
        fd = _open(path)
        try:
            before = _stamp(os.fstat(fd))
            data = _read(fd, limit)
            if _stamp(os.fstat(fd)) != before:
                raise CaptureRefusal("file_changed_during_read")
        except BaseException:
            os.close(fd)
            raise
        self.files.append((path, fd, before, data, limit))
        return data

    def recheck(self, only_path=None):
        for path, fd, before, held, limit in self.files:
            if only_path is not None and path != only_path:
                continue
            if _stamp(os.fstat(fd)) != before:
                raise CaptureRefusal("held_file_changed")
            current = _open(path)
            try:
                if _stamp(os.fstat(current)) != before or _read(current, limit) != held:
                    raise CaptureRefusal("file_identity_or_bytes_changed")
                if _stamp(os.fstat(current)) != before:
                    raise CaptureRefusal("file_changed_during_recheck")
            finally:
                os.close(current)

    def close(self):
        for _path_name, fd, _before, _data, _limit in self.files:
            os.close(fd)
        self.files.clear()


def _profile():
    if sys.platform != "linux" or os.getuid() != 0 or os.geteuid() != 0:
        raise CaptureRefusal("root_linux_runtime_required")
    if not sys.flags.isolated or not sys.flags.no_site:
        raise CaptureRefusal("isolated_no_site_runtime_required")


def _identity(held):
    machine = held.take("/etc/machine-id", 128).decode("ascii").strip()
    boot = held.take("/proc/sys/kernel/random/boot_id", 128).decode("ascii").strip()
    uid_map = held.take(f"/proc/{os.getpid()}/uid_map", 256).decode("ascii").split()
    if uid_map != ["0", "0", "4294967295"]:
        raise CaptureRefusal("initial_uid_mapping_required")
    if not _MACHINE.fullmatch(machine) or not _UUID.fullmatch(boot):
        raise CaptureRefusal("platform_identity_invalid")
    return machine, boot


def _policy(value, machine, boot):
    if type(value) is not dict or set(value) != POLICY_FIELDS:
        raise CaptureRefusal("policy_schema_invalid")
    if (value["schema"] != SCHEMA or type(value["contract_version"]) is not int
            or value["contract_version"] != 3):
        raise CaptureRefusal("policy_version_invalid")
    if value["machine_id"] != machine or value["boot_id"] != boot:
        raise CaptureRefusal("platform_identity_mismatch")
    if (type(value["capture_id"]) is not str or not _UUID.fullmatch(value["capture_id"])
            or type(value["generation"]) is not int or not 1 <= value["generation"] < 2**64):
        raise CaptureRefusal("capture_identity_invalid")
    for name in ("authority_file", "source_directory", "capture_directory"):
        _path(value[name])
    hashes = value["payload_digests"]
    if type(hashes) is not dict or set(hashes) != set(PAYLOAD_NAMES):
        raise CaptureRefusal("capture_section_set_invalid")
    for item in [value["consumer_sha256"], value["python_sha256"], *hashes.values()]:
        if type(item) is not str or not _HEX.fullmatch(item):
            raise CaptureRefusal("policy_digest_invalid")


def _authority(value, policy, policy_digest):
    if type(value) is not dict or set(value) != AUTHORITY_FIELDS:
        raise CaptureRefusal("authority_schema_invalid")
    if (value["schema"] != AUTHORITY_SCHEMA or type(value["generation"]) is not int
            or value["policy_sha256"] != policy_digest
            or any(value[name] != policy[name] for name in
                   ("machine_id", "boot_id", "capture_id", "generation"))):
        raise CaptureRefusal("authority_epoch_or_policy_mismatch")


def _current(value, policy, policy_path, policy_digest):
    """Bind eligibility to the exact independently selected active record.

    The future producer must publish this protected record and enforce durable
    monotonic updates. This reader neither publishes it nor prevents rollback
    of the store itself, and the decision never carries native launch authority.
    """
    if type(value) is not dict or set(value) != CURRENT_FIELDS:
        raise CaptureRefusal("current_record_schema_invalid")
    if (value["schema"] != CURRENT_SCHEMA or type(value["state"]) is not str
            or value["state"] not in ("active", "revoked")
            or type(value["generation"]) is not int
            or not 1 <= value["generation"] < 2**64
            or type(value["capture_id"]) is not str
            or not _UUID.fullmatch(value["capture_id"])
            or type(value["policy_sha256"]) is not str
            or not _HEX.fullmatch(value["policy_sha256"])):
        raise CaptureRefusal("current_record_value_invalid")
    _path(value["policy_path"])
    if value["state"] != "active":
        raise CaptureRefusal("current_capture_revoked")
    if (value["policy_path"] != policy_path or value["policy_sha256"] != policy_digest
            or any(value[name] != policy[name] for name in
                   ("machine_id", "boot_id", "capture_id", "generation"))):
        raise CaptureRefusal("capture_is_not_current")


def _load_contract(sources):
    """Execute only the three digest-pinned held code payloads, never a reread."""
    names = [_PREFIX, _PREFIX + ".analysis", _PREFIX + ".evidence_contract"]
    if any(name in sys.modules for name in names):
        raise CaptureRefusal("module_namespace_not_clean")
    made = {}
    try:
        package = types.ModuleType(_PREFIX)
        package.__path__ = ()
        package.__package__ = _PREFIX
        sys.modules[_PREFIX] = package
        made[_PREFIX] = package
        exec(compile(sources["__init__.py"], "<held-gnome-init>", "exec"), package.__dict__)
        for leaf in ("analysis", "evidence_contract"):
            name = _PREFIX + "." + leaf
            module = types.ModuleType(name)
            module.__package__ = _PREFIX
            sys.modules[name] = module
            made[name] = module
            setattr(package, leaf, module)
            exec(compile(sources[leaf + ".py"], "<held-gnome-" + leaf + ">", "exec"),
                 module.__dict__)
        return package.evidence_contract, made
    except BaseException:
        _unload(made)
        raise


def _unload(made):
    for name, module in made.items():
        if sys.modules.get(name) is module:
            del sys.modules[name]


def review_capture(policy_path):
    """Review one held root capture; this never authorizes native preparation.

    The authority and canonical current record must be maintained by the future
    trusted root producer. Caller-selected old records cannot establish currency.
    This library creates neither that producer nor its protected directories.
    Root ownership is the local depot boundary, not cryptographic remote trust.
    The isolated interpreter's complete dependencies and boot admission remain
    separate runtime qualification requirements.
    """
    held = _HeldFiles()
    made = {}
    try:
        _profile()
        machine, boot = _identity(held)
        policy_bytes = held.take(policy_path, 32768)
        policy_digest = digest(policy_bytes)
        policy = _json(policy_bytes)
        _policy(policy, machine, boot)
        authority = _json(held.take(policy["authority_file"], 4096))
        _authority(authority, policy, policy_digest)
        current_bytes = held.take(CURRENT_RECORD, 4096)
        _current(_json(current_bytes), policy, policy_path, policy_digest)
        if digest(held.take(os.path.abspath(__file__), 1024 * 1024)) != policy["consumer_sha256"]:
            raise CaptureRefusal("consumer_code_mismatch")
        if digest(held.take(sys.executable, 64 * 1024 * 1024)) != policy["python_sha256"]:
            raise CaptureRefusal("interpreter_code_mismatch")
        sources = {}
        for name, expected in SOURCE_PINS.items():
            data = held.take(policy["source_directory"] + "/" + name, 4 * 1024 * 1024)
            if digest(data) != expected:
                raise CaptureRefusal("analysis_code_mismatch")
            sources[name] = data
        payloads = {}
        for name in PAYLOAD_NAMES:
            data = held.take(policy["capture_directory"] + "/" + name + ".json",
                             8 * 1024 * 1024)
            if digest(data) != policy["payload_digests"][name]:
                raise CaptureRefusal("capture_digest_mismatch")
            payloads[name] = data
            if sum(map(len, payloads.values())) > 8 * 1024 * 1024:
                raise CaptureRefusal("capture_size_exceeded")
        contract, made = _load_contract(sources)
        if (contract.CONTRACT_ID != CONTRACT_ID or contract.CONTRACT_VERSION != 3
                or set(contract.REQUIRED_SECTIONS) != set(PAYLOAD_NAMES) | {
                    "section_digests", "section_payloads"}):
            raise CaptureRefusal("pinned_contract_mismatch")
        bundle = {name: _json(data, exact=False) for name, data in payloads.items()}
        bundle.update(section_payloads=payloads, section_digests=policy["payload_digests"])
        verification = contract.verify_bundle(bundle)
        if not verification.consumable:
            return CaptureDecision(False, verification.reasons)
        view = contract.view_from_verified_sections(bundle)
        view_digest = contract.canonical_view_digest(view)
        verdict = contract.admit(bundle, view=view, view_digest=view_digest)
        if not verdict.consumable:
            return CaptureDecision(False, verdict.reasons)
        held.recheck()
        # Revisit currency last: other held-file checks do not extend its lease.
        held.recheck(CURRENT_RECORD)
        return CaptureDecision(True, (), policy_digest, view_digest,
                               contract.derived_unit_path(view), policy["capture_id"],
                               policy["generation"])
    except CaptureRefusal as error:
        return CaptureDecision(False, (str(error),))
    except (OSError, ValueError, TypeError, KeyError, RecursionError):
        return CaptureDecision(False, ("capture_io_or_decode_refused",))
    finally:
        _unload(made)
        held.close()
