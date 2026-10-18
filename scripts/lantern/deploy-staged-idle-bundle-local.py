#!/usr/bin/env python3
"""Install a staged idle-suspend bundle; NEVER enable, start or wake anything.

The companion installers (`install-idle-suspend-local.py` for A/B,
`install-cf-idle-suspend-local.py` for C-F) build and stage a frozen bundle and
stop there. This is the parent deployer they describe: it verifies the staged
payload against the frozen plan, backs up every file it overwrites, refuses to
replace a destination that neither matches the predecessor payload nor is
explicitly accepted, installs atomically with fsync, reloads systemd and writes
a receipt. It does not create `/etc/sage-lantern-idle-suspend.enabled`, does not
enable or start the idle unit, does not touch a model lane and does not sleep.

D-017 rules apply to the files this install overwrites: either the destination
is byte-identical to the predecessor payload (`--predecessor`, the release tree
the unit is anchored on) or the operator accepts it by name, in which case the
observed digest is recorded under `replaced_unverifiable_targets`. A refusal
names its gate. The mesh voice lane is refused by name: on a mesh-managed unit
the broker owns the voice ExecStart and this bundle would displace it.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import tempfile


MAX_FILE = 2 * 1024 * 1024
MAX_FILES = 64
VOICE_DROPIN = "/etc/systemd/system/sage-lantern-voice-loop.service.d/90-sage-lantern-sleep.conf"
MESH_CONFIG = "/etc/sage-lantern/mesh-runtime.json"
MESH_ENVIRONMENT = "SAGE_LANTERN_MESH_BROKER=1"
SYSTEMCTL = "/usr/bin/systemctl"


class Refused(Exception):
    def __init__(self, gate):
        super().__init__(gate)
        self.gate = gate


def read_regular(path, *, limit=MAX_FILE):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise Refused("destination_not_a_regular_file") from error
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
            raise Refused("destination_not_a_regular_file")
        data = bytearray()
        while True:
            chunk = os.read(descriptor, limit + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise Refused("file_limit")
        return bytes(data), metadata
    finally:
        os.close(descriptor)


def trusted_directory(root, destination, *, owner):
    for component in reversed(destination.parents):
        if str(component) == "/":
            continue
        path = (root / str(component).lstrip("/")) if str(root) != "/" else component
        metadata = path.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or metadata.st_uid != owner
                or metadata.st_mode & 0o022):
            raise Refused("untrusted_parent_directory")


def prepare_parent(root, destination, *, owner):
    """Create a missing reviewed drop-in directory; never invent a random parent."""
    target = (root / destination.lstrip("/")) if str(root) != "/" else Path(destination)
    parent = target.parent
    if not parent.exists() and not parent.is_symlink():
        if not str(parent).startswith(("/etc/systemd/system/", str(root) + "/etc/systemd/system/")):
            raise Refused("destination_parent_missing")
        parent.mkdir(mode=0o755)
        sync_dir(parent.parent)
    trusted_directory(root, Path(destination), owner=owner)


def install_file(root, destination, data, mode, *, owner):
    target = (root / destination.lstrip("/")) if str(root) != "/" else Path(destination)
    directory = target.parent
    descriptor, temporary = tempfile.mkstemp(prefix=".idle-deploy-", dir=directory)
    try:
        os.fchmod(descriptor, mode)
        os.write(descriptor, data)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(temporary, target)
        sync_dir(directory)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


def sync_dir(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def backup_file(backup_root, index, destination, data, mode):
    name = f"{index:03d}-" + re.sub(r"[^A-Za-z0-9._-]", "_", destination.lstrip("/"))
    descriptor = None
    for suffix in range(16):
        target = backup_root / (name if suffix == 0 else f"{name}.{suffix}")
        try:
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
            break
        except FileExistsError:
            continue
    if descriptor is None:
        raise Refused("backup_name_exhausted")
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    return target


def prepare_backup_root(backup_root, *, owner):
    if backup_root.exists() or backup_root.is_symlink():
        metadata = backup_root.lstat()
        if (not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) != 0o700):
            raise Refused("untrusted_backup_root")
        return
    backup_root.mkdir(mode=0o700, parents=True)


def mesh_managed(root, run):
    """The mesh runtime owns the voice ExecStart only when it is actually installed.

    The switch is the same signal the controller reads
    (`/etc/sage-lantern/mesh-runtime.json`). A dangling
    `SAGE_LANTERN_MESH_BROKER=1` from the fleet installer without that config
    means no broker can drain, so the reviewed non-mesh drop-in replaces it and
    the observation is recorded for the operator.
    """
    if (root / MESH_CONFIG.lstrip("/")).exists() or (root / MESH_CONFIG.lstrip("/")).is_symlink():
        return True, "configured"
    if str(root) != "/":
        return False, "unknown"
    result = run([SYSTEMCTL, "show", "sage-lantern-voice-loop.service", "--property=Environment"])
    environment = result.stdout.decode("utf-8", errors="strict")
    return False, "dangling_mesh_environment" if MESH_ENVIRONMENT in environment else "absent"


def deploy(stage, manifest_path, backup_root, *, root=Path("/"), predecessor=None,
           accepted=(), owner=0, run=None):
    if run is None:
        def run(argv):
            return subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                  stderr=subprocess.DEVNULL, check=False)
    stage = Path(stage)
    root = Path(root)
    plan = json.loads(read_regular(manifest_path)[0].decode("utf-8"))
    if (type(plan) is not dict or plan.get("activation") is not False
            or type(plan.get("files")) is not list or not 1 <= len(plan["files"]) <= MAX_FILES):
        raise Refused("manifest_invalid")
    if plan.get("missing"):
        raise Refused("manifest_incomplete")
    accepted = {str(Path(item)) for item in accepted}
    payload_directory = stage / "payload"
    if not payload_directory.is_dir() or not (stage / "manifest.json").is_file():
        raise Refused("stage_incomplete")
    if read_regular(stage / "manifest.json")[0] != read_regular(manifest_path)[0]:
        raise Refused("stage_manifest_mismatch")
    mesh, mesh_state = mesh_managed(root, run)
    if VOICE_DROPIN in {item["destination"] for item in plan["files"]} and mesh:
        raise Refused("mesh_voice_lane_owns_execstart")
    backup_root = Path(backup_root)
    prepare_backup_root(backup_root, owner=owner)
    receipt = {"version": 1, "scope": plan.get("scope"), "activated": False,
               "manifest_sha256": hashlib.sha256(read_regular(manifest_path)[0]).hexdigest(),
               "voice_environment": mesh_state,
               "files": [], "replaced_unverifiable_targets": [], "backups": []}
    for index, item in enumerate(plan["files"]):
        destination = item["destination"]
        if type(destination) is not str or not destination.startswith("/") or ".." in Path(destination).parts:
            raise Refused("unsafe_destination")
        if item.get("artifact") != f"payload/{index:03d}":
            raise Refused("unsafe_artifact")
        data = read_regular(stage / item["artifact"])[0]
        if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
            raise Refused("payload_does_not_match_manifest")
        prepare_parent(root, destination, owner=owner)
        target = (root / destination.lstrip("/")) if str(root) != "/" else Path(destination)
        before = None
        if target.exists() or target.is_symlink():
            current, metadata = read_regular(target)
            if metadata.st_uid != owner or stat.S_IMODE(metadata.st_mode) not in (0o644, 0o755, 0o600):
                raise Refused("untrusted_destination")
            before = hashlib.sha256(current).hexdigest()
            if current != data:
                anchor = None
                if predecessor is not None:
                    candidate = Path(predecessor) / destination.lstrip("/")
                    if candidate.exists() and not candidate.is_symlink():
                        anchor = hashlib.sha256(read_regular(candidate)[0]).hexdigest()
                if anchor != before and destination not in accepted:
                    raise Refused("destination_differs_from_predecessor")
                if anchor != before:
                    receipt["replaced_unverifiable_targets"].append(
                        {"destination": destination, "observed_sha256": before})
            receipt["backups"].append(str(backup_file(backup_root, index, destination, current,
                                                       stat.S_IMODE(metadata.st_mode))))
            mode = stat.S_IMODE(metadata.st_mode)
        else:
            mode = item["mode"]
        install_file(root, destination, data, mode, owner=owner)
        receipt["files"].append({"destination": destination, "mode": mode,
                                 "sha256": hashlib.sha256(data).hexdigest(), "replaced_sha256": before})
    unit_paths = [item["destination"] for item in plan["files"]
                  if item["destination"].startswith("/etc/systemd/system/")]
    if unit_paths:
        result = run([SYSTEMCTL, "daemon-reload"])
        receipt["daemon_reload"] = result.returncode == 0
        if result.returncode != 0:
            raise Refused("daemon_reload_failed")
    (backup_root / "receipt.json").write_text(json.dumps(receipt, sort_keys=True, indent=2) + "\n")
    sync_dir(backup_root)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backup-root", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path,
                        help="Frozen predecessor payload tree the unit is anchored on")
    parser.add_argument("--accept-replaced", action="append", default=[],
                        help="Destination whose current content the operator accepts replacing")
    args = parser.parse_args()
    try:
        if os.geteuid() != 0 or platform.system() != "Linux":
            raise Refused("root_linux_required")
        receipt = deploy(args.stage, args.manifest, args.backup_root,
                         predecessor=args.predecessor, accepted=args.accept_replaced)
        print(json.dumps({"deployed": True, "activated": False, "receipt": str(args.backup_root / "receipt.json"),
                          "files": len(receipt["files"]),
                          "replaced_unverifiable_targets": len(receipt["replaced_unverifiable_targets"])}))
        return 0
    except Refused as error:
        print(json.dumps({"error": error.gate, "activated": False}))
        return 1
    except Exception as error:
        print(json.dumps({"error": f"deploy_failed:{type(error).__name__}", "activated": False,
                          "detail": str(error)[:200]}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
