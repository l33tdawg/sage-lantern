#!/usr/bin/env python3
"""Separate the sage kiosk account from privileged SSH administration.

Default: read-only check. --apply requires a direct root SSH session on a
commissioned ARM64 Lantern and a new backup directory. Passwords, keys and
running sessions are untouched. Existing sessions retain cached groups until
they are replaced. This is not the whole-desktop authentication implementation.
"""

import argparse
import grp
import json
import os
from pathlib import Path
import platform
import pwd
import stat
import subprocess
import tempfile


ACCOUNT = "sage"
DENIED_GROUPS = {"disk", "sudo", "docker", "adm", "systemd-journal"}
RULE = Path("/etc/sudoers.d/zz-sage-lantern-kiosk")
RULE_BYTES = b"# Kiosk account; privileged administration uses the separate root SSH lane.\nsage ALL=(ALL:ALL) !ALL\n"
OLD_RULE = Path("/etc/sudoers.d/armbian-firstlogin-psd")
OLD_RULE_TEXT = "sage ALL=(ALL) NOPASSWD: /usr/bin/psd-overlay-helper"
SCHEMA = "sage.lantern.kiosk-privileges.v1"


def run(args):
    result = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=20, check=False)
    return result.returncode


def require_command(args, runner):
    if runner(args) != 0:
        raise ValueError("privilege_command_failed")


def safe(path):
    for parent in path.parents:
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError("unsafe_parent")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != 0
            or info.st_mode & 0o022 or info.st_size > 65536):
        raise ValueError("unsafe_file")
    return info


def read(path):
    info = safe(path)
    if info is None:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        data = os.read(fd, 65537)
        if len(data) != info.st_size:
            raise ValueError("file_changed")
        return data
    finally:
        os.close(fd)


def write(path, data, mode):
    safe(path)
    fd, name = tempfile.mkstemp(prefix=".sage-kiosk-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def groups():
    account = pwd.getpwnam(ACCOUNT)
    if account.pw_uid != 1000:
        raise ValueError("unexpected_kiosk_uid")
    return sorted(grp.getgrgid(gid).gr_name for gid in os.getgrouplist(ACCOUNT, account.pw_gid))


def session_groups(proc, denied_gids):
    counts = dict(observed=0, pending=0, unavailable=0)
    paths = list(proc.iterdir())
    if len(paths) > 16384:
        raise ValueError("process_inventory_limit")
    for path in paths:
        if not path.name.isdecimal():
            continue
        try:
            rows = dict(line.split(":", 1) for line in (path / "status").read_text().splitlines()
                        if ":" in line)
            uid = rows.get("Uid", "").split()
            if len(uid) != 4:
                raise ValueError("invalid_process_metadata")
            if "1000" not in uid:
                continue
            gids = {int(value) for value in rows["Groups"].split()}
            counts["observed"] += 1
            counts["pending"] += bool(gids & denied_gids)
        except FileNotFoundError:
            continue
        except (OSError, ValueError, KeyError):
            counts["unavailable"] += 1
    return counts


def check(root, runner=run):
    membership = groups()
    failures = sorted(set(membership) & DENIED_GROUPS)
    if read(root / RULE.relative_to("/")) != RULE_BYTES:
        failures.append("sudo_deny_rule")
    old = read(root / OLD_RULE.relative_to("/"))
    if old is not None:
        failures.append("legacy_psd_rule")
    if runner(["/usr/sbin/visudo", "-cf", str(root / "etc/sudoers")]) != 0:
        failures.append("sudoers_invalid")
    # Query effective permission; do not execute a privileged command.
    if runner(["/usr/bin/sudo", "-n", "-l", "-U", ACCOUNT,
               "-u", "root", "/usr/bin/true"]) == 0:
        failures.append("sudo_probe_admitted")
    denied_gids = set()
    for name in DENIED_GROUPS:
        try:
            denied_gids.add(grp.getgrnam(name).gr_gid)
        except KeyError:
            pass
    processes = session_groups(root / "proc", denied_gids)
    return dict(schema=SCHEMA, configuration_ok=not failures, failures=failures,
                kiosk_groups=membership, running_sessions=processes,
                fresh_session_required=bool(processes["pending"] or processes["unavailable"]
                                            or not processes["observed"]),
                desktop_authentication_accepted=False)


def apply(root, backup, runner=run):
    membership = groups()
    rule = root / RULE.relative_to("/")
    old_rule = root / OLD_RULE.relative_to("/")
    before_rule, old_bytes = read(rule), read(old_rule)
    if before_rule not in (None, RULE_BYTES):
        raise ValueError("managed_rule_conflict")
    if old_bytes is not None and old_bytes.decode().strip() != OLD_RULE_TEXT:
        raise ValueError("legacy_rule_unreviewed")
    helper = root / "usr/bin/psd-overlay-helper"
    if old_bytes is not None and (helper.exists() or helper.is_symlink()):
        raise ValueError("live_psd_dependency_requires_review")
    if backup.exists() or backup.is_symlink():
        raise ValueError("backup_exists")
    safe(backup)
    require_command(["/usr/sbin/visudo", "-cf", str(root / "etc/sudoers")], runner)
    # Validate our standalone sudoers syntax before any account mutation.
    fd, temporary = tempfile.mkstemp(prefix=".sage-kiosk-review-", dir=rule.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(RULE_BYTES)
            os.fchmod(stream.fileno(), 0o440)
        require_command(["/usr/sbin/visudo", "-cf", temporary], runner)
    finally:
        os.unlink(temporary)
    backup.mkdir(mode=0o700)
    for name, data in (("sudo-deny.before", before_rule), ("legacy-psd.before", old_bytes)):
        if data is not None:
            write(backup / name, data, 0o600)
    removed = sorted(set(membership) & DENIED_GROUPS)
    receipt = dict(schema=SCHEMA, status="prepared", groups_before=membership,
                   groups_removed=removed, root_management_untouched=True,
                   old_rule_present=old_bytes is not None, deny_rule_present=before_rule is not None,
                   old_rule_mode=stat.S_IMODE(old_rule.lstat().st_mode) if old_bytes is not None else None,
                   deny_rule_mode=stat.S_IMODE(rule.lstat().st_mode) if before_rule is not None else None,
                   running_sessions_restarted=False, passwords_and_keys_untouched=True)
    receipt_path = backup / "receipt.json"
    write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode(), 0o600)
    write(rule, RULE_BYTES, 0o440)
    require_command(["/usr/sbin/visudo", "-cf", str(root / "etc/sudoers")], runner)
    if old_bytes is not None:
        old_rule.unlink()
        directory = os.open(old_rule.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    for group in removed:
        require_command(["/usr/bin/gpasswd", "--delete", ACCOUNT, group], runner)
    after = groups()
    if set(after) & DENIED_GROUPS or not (set(membership) - DENIED_GROUPS) <= set(after):
        raise ValueError("group_readback_failed")
    receipt["status"] = "applied_pending_verification"
    write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode(), 0o600)
    return receipt_path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--apply", action="store_true")
    modes.add_argument("--plan", action="store_true")
    modes.add_argument("--check", action="store_true")
    parser.add_argument("--backup-dir", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.plan:
            print(json.dumps(dict(schema=SCHEMA, remove_groups=sorted(DENIED_GROUPS),
                                  sudo_rule=RULE_BYTES.decode(), legacy_psd="remove only reviewed rule with absent helper",
                                  root_management="unchanged", session_restart=False), indent=2))
            return 0
        if platform.system() != "Linux" or os.geteuid() != 0:
            raise ValueError("root_linux_required")
        root = Path("/")
        receipt = None
        if args.apply:
            if (os.getuid() != 0 or not os.environ.get("SSH_CONNECTION")
                    or os.environ.get("SUDO_USER")):
                raise ValueError("direct_root_ssh_required")
            if platform.machine() not in {"aarch64", "arm64"}:
                raise ValueError("arm64_lantern_required")
            installed = read(root / "etc/sage-lantern/appliance-installed.json")
            if not installed or not json.loads(installed).get("release_id", "").startswith("lantern-"):
                raise ValueError("commissioned_lantern_required")
            if args.backup_dir is None or args.backup_dir.parent != Path("/var/backups"):
                raise ValueError("new_var_backups_directory_required")
            receipt = apply(root, args.backup_dir)
        result = check(root)
        if receipt:
            result["backup_receipt"] = str(receipt)
            saved = json.loads(read(receipt))
            saved["status"] = "configuration_verified" if result["configuration_ok"] else "verification_failed"
            write(receipt, (json.dumps(saved, indent=2) + "\n").encode(), 0o600)
        print(json.dumps(result, indent=2))
        return 0 if result["configuration_ok"] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        reason = str(error)
        if not reason.replace("_", "").isalpha() or len(reason) > 64:
            reason = "privilege_operation_refused"
        print(json.dumps(dict(schema=SCHEMA, configuration_ok=False, error=reason)))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
