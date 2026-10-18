#!/usr/bin/env python3
"""Check or install Lantern's OS core-dump policy without restarting Lantern.

Default is read-only. --apply requires root on a commissioned ARM64 Lantern
and an unused backup directory. Existing crash artifacts are never opened,
copied, deleted or uploaded. Running sessions need later restart qualification.
"""

import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import pwd
import re
import stat
import subprocess
import tempfile


SCHEMA = "sage.lantern.crash-privacy.v1"
MAX_CONFIG = 64 * 1024
SERVICES = tuple("sage-lantern-" + name + ".service" for name in (
    "audio-capture", "camera", "device-auth", "device-agent", "display-power",
    "embed", "embed-shim", "fleet-admin", "gateway-evidence", "idle-suspend",
    "llm", "llm-cpu", "mesh-runtime", "observation-status", "personal-sage",
    "ptt", "runtime", "startup", "status", "voice-loop", "vlm"))
COLLECTORS = (
    "apport.service", "apport-autoreport.service", "apport-autoreport.path",
    "apport-autoreport.timer", "apport-forward.socket", "apport-forward@.service",
    "apport-coredump-hook@.service", "whoopsie.service", "whoopsie.path",
    "systemd-coredump.socket", "systemd-coredump@.service",
)
SYSCTLS = {"kernel.core_uses_pid": "0", "fs.suid_dumpable": "0",
           "kernel.core_pattern": ""}
POLICY_FILES = {
    "/etc/sysctl.d/99-zz-sage-lantern-crash-privacy.conf":
        "# No kernel core file or piped memory collector.\n"
        "kernel.core_uses_pid = 0\nfs.suid_dumpable = 0\nkernel.core_pattern =\n",
    "/etc/systemd/coredump.conf.d/90-sage-lantern-crash-privacy.conf":
        "[Coredump]\nStorage=none\nProcessSizeMax=0\n",
    "/etc/systemd/system.conf.d/90-sage-lantern-crash-privacy.conf":
        "[Manager]\nDefaultLimitCORE=0:0\n",
    "/etc/systemd/user.conf.d/90-sage-lantern-crash-privacy.conf":
        "[Manager]\nDefaultLimitCORE=0:0\n",
    "/etc/systemd/system/user@.service.d/90-sage-lantern-crash-privacy.conf":
        "[Service]\nLimitCORE=0:0\n",
    "/etc/security/limits.d/99-zz-sage-lantern-crash-privacy.conf":
        "# New PAM sessions; root needs explicit rules.\n"
        "* soft core 0\n* hard core 0\nroot soft core 0\nroot hard core 0\n",
}
for _unit in SERVICES:
    POLICY_FILES[f"/etc/systemd/system/{_unit}.d/90-sage-lantern-crash-privacy.conf"] = (
        "[Service]\nLimitCORE=0:0\n")


def command(arguments):
    environment = dict(os.environ, LC_ALL="C", SYSTEMD_COLORS="0")
    result = subprocess.run(arguments, env=environment, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=20, check=False)
    if len(result.stdout) > MAX_CONFIG:
        raise ValueError("command_output_limit")
    return result.returncode, result.stdout.decode("utf-8", errors="strict").strip()


def require_command(arguments, run):
    if run(arguments)[0] != 0:
        raise ValueError("policy_command_failed")


def safe_path(path):
    for parent in reversed(path.parents):
        try:
            info = parent.lstat()
        except FileNotFoundError:
            continue
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 0
                or info.st_mode & 0o022):
            raise ValueError("unsafe_parent")
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_nlink != 1 or info.st_mode & 0o022
            or info.st_size > MAX_CONFIG):
        raise ValueError("unsafe_config")
    return info


def read_config(path):
    info = safe_path(path)
    if info is None:
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (
                info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns):
            raise ValueError("config_changed")
        data = os.read(fd, MAX_CONFIG + 1)
        if len(data) > MAX_CONFIG:
            raise ValueError("config_limit")
        return data
    finally:
        os.close(fd)


def apport_disabled(data):
    text = (data or b"").decode("utf-8")
    matches = list(re.finditer(r"(?m)^[ \t]*enabled[ \t]*=.*$", text))
    if len(matches) > 1:
        raise ValueError("ambiguous_apport_config")
    if matches:
        match = matches[0]
        text = text[:match.start()] + "enabled=0" + text[match.end():]
    else:
        text = text.rstrip("\n") + "\nenabled=0\n"
    return text.encode("utf-8")


def desired_files(root, services=SERVICES):
    files = {name: text.encode() for name, text in POLICY_FILES.items()}
    for unit in services:
        files[f"/etc/systemd/system/{unit}.d/90-sage-lantern-crash-privacy.conf"] = (
            b"[Service]\nLimitCORE=0:0\n")
    name = "/etc/default/apport"
    files[name] = apport_disabled(read_config(root / name.lstrip("/")))
    return files


def installed_services(run):
    rc, text = run(["/usr/bin/systemctl", "list-unit-files", "--no-legend", "--no-pager",
                    "sage-lantern-*.service"])
    if rc:
        raise ValueError("service_inventory_unavailable")
    names = [line.split()[0] for line in text.splitlines() if line.strip()]
    if any(re.fullmatch(r"sage-lantern-[a-z0-9-]+\.service", name) is None for name in names):
        raise ValueError("service_inventory_invalid")
    return tuple(sorted(set(SERVICES) | set(names)))


def collector_instance(name):
    for unit in COLLECTORS:
        if name == unit:
            return True
        if "@." in unit:
            prefix, suffix = unit.split("@", 1)
            if name.startswith(prefix + "@") and name.endswith(suffix):
                return True
    return False


def loaded_collectors(run):
    rc, text = run(["/usr/bin/systemctl", "list-units", "--all", "--plain",
                    "--no-legend", "--no-pager", "apport*", "whoopsie*", "systemd-coredump*"])
    if rc:
        raise ValueError("collector_inventory_unavailable")
    rows = [line.split() for line in text.splitlines()]
    if any(len(row) < 4 for row in rows):
        raise ValueError("collector_inventory_invalid")
    return [row[0] for row in rows if row[2] not in ("inactive", "failed")
            and collector_instance(row[0])]


def properties(unit, run):
    if "@." in unit:
        rc, text = run(["/usr/bin/systemctl", "is-enabled", unit])
        if text not in {"masked", "static", "disabled", "enabled", "indirect"}:
            # A template may be absent on this image. Do not fabricate a
            # successful mask when is-enabled cannot identify its state.
            return dict(LoadState="not-found", ActiveState="inactive", template=True)
        return dict(LoadState="masked" if text == "masked" else "loaded",
                    UnitFileState=text, template=True)
    rc, text = run(["/usr/bin/systemctl", "show", unit, "--no-pager",
                    "-p", "LoadState", "-p", "ActiveState", "-p", "UnitFileState",
                    "-p", "LimitCORE", "-p", "LimitCORESoft"])
    values = dict(line.split("=", 1) for line in text.splitlines() if "=" in line)
    # systemctl show may return nonzero for a masked/missing unit. Its state
    # must still be explicit; absence is never a passing policy result.
    if rc and values.get("LoadState") not in {"masked", "not-found"}:
        raise ValueError("unit_state_unavailable")
    return values


def persistent_sysctls(run):
    rc, text = run(["/usr/lib/systemd/systemd-sysctl", "--cat-config", "--no-pager"])
    if rc:
        raise ValueError("sysctl_config_unavailable")
    selected = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(("#", ";")) or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name = name.strip().lstrip("-").replace("/", ".")
        if name in SYSCTLS:
            selected[name] = value.strip()
    return selected


def artifact_counts(directory):
    """lstat only: no dump contents, filenames, command lines or environments."""
    result = dict(files=0, bytes=0, other_entries=0, available=True)
    try:
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode):
            result["available"] = False
            return result
        with os.scandir(directory) as entries:
            for entry in entries:
                if result["files"] + result["other_entries"] >= 4096:
                    result["available"] = False
                    break
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISREG(info.st_mode):
                    result["files"] += 1
                    result["bytes"] += info.st_size
                else:
                    result["other_entries"] += 1
    except FileNotFoundError:
        pass
    except OSError:
        result["available"] = False
    return result


def process_limits(proc, accounts):
    result = {name: dict(observed=0, zero_soft_and_hard=0, pending=0, unavailable=0)
              for name in accounts}
    paths = list(proc.iterdir())
    if len(paths) > 16384:
        raise ValueError("process_inventory_limit")
    for path in paths:
        if not path.name.isdecimal():
            continue
        try:
            # /proc directory ownership can become root when a process is
            # non-dumpable. Use the status real/effective UID metadata instead.
            uid_rows = [line.split() for line in (path / "status").read_text().splitlines()
                        if line.startswith("Uid:")]
            if len(uid_rows) != 1 or len(uid_rows[0]) != 5:
                raise ValueError("process_uid_invalid")
            uids = {int(uid) for uid in uid_rows[0][1:]}
            names = [name for name, uid in accounts.items() if uid in uids]
        except FileNotFoundError:
            continue
        except (OSError, ValueError):
            for row in result.values():
                row["unavailable"] += 1
            continue
        for name in names:
            row = result[name]
            try:
                lines = (path / "limits").read_text().splitlines()
                limits = [line.split() for line in lines if line.startswith("Max core file size")]
                if len(limits) != 1 or len(limits[0]) != 7:
                    raise ValueError("limits_invalid")
                row["observed"] += 1
                if limits[0][4:6] == ["0", "0"]:
                    row["zero_soft_and_hard"] += 1
                else:
                    row["pending"] += 1
            except FileNotFoundError:
                # A process that exited during the bounded observation is gone.
                continue
            except (OSError, ValueError):
                row["unavailable"] += 1
    return result


def check(root, run, accounts):
    failures = []
    services = installed_services(run)
    files = desired_files(root, services)
    for name, data in files.items():
        if read_config(root / name.lstrip("/")) != data:
            failures.append(name)
    sysctls = {}
    for name, expected in SYSCTLS.items():
        try:
            observed = (root / "proc/sys" / name.replace(".", "/")).read_text().strip()
        except OSError:
            observed = None
        sysctls[name] = dict(expected=expected, observed=observed)
        if observed != expected:
            failures.append(name)
    collectors = {}
    for unit in COLLECTORS:
        value = properties(unit, run)
        collectors[unit] = value
        if (value.get("LoadState") != "masked"
                or (not value.get("template") and value.get("ActiveState") not in {"inactive", "failed"})):
            failures.append(unit)
    active = loaded_collectors(run)
    failures.extend(active)
    persistent = persistent_sysctls(run)
    for name, value in SYSCTLS.items():
        if persistent.get(name) != value:
            failures.append(name + ":boot_config")
    service_limits = {}
    user_managers = tuple(f"user@{uid}.service" for uid in sorted(set(accounts.values())))
    for unit in services + user_managers:
        value = properties(unit, run)
        if value.get("LoadState") == "not-found":
            continue
        service_limits[unit] = value
        if value.get("LimitCORE") != "0" or value.get("LimitCORESoft") != "0":
            failures.append(unit + ":core_limits")
    processes = process_limits(root / "proc", accounts)
    artifacts = {name: artifact_counts(root / name.lstrip("/")) for name in
                 ("/var/crash", "/var/lib/systemd/coredump")}
    pending = [name for name, row in processes.items()
               if row["pending"] or row["unavailable"] or not row["observed"]]
    return dict(schema=SCHEMA, checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                configuration_ok=not failures, failures=sorted(set(failures)), sysctls=sysctls,
                collectors=collectors, persistent_sysctls=persistent,
                service_limits=service_limits, processes=processes,
                session_restart_pending=pending, existing_artifacts=artifacts,
                acceptance_complete=False,
                remaining_gates=["qualified-power restart and fresh desktop/SSH session checks",
                                 "synthetic OS crash canary on the installed vendor kernel",
                                 "browser/application minidump provenance and retention",
                                 "owner-approved existing artifact disposition"])


def durable_write(path, data, mode):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    safe_path(path)
    fd, temporary = tempfile.mkstemp(prefix=".crash-privacy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            os.fchmod(handle.fileno(), mode)
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(root, backup, run):
    files = desired_files(root, installed_services(run))
    originals = {}
    original_modes = {}
    for name, data in files.items():
        path = root / name.lstrip("/")
        originals[name] = read_config(path)
        original_modes[name] = stat.S_IMODE(path.lstat().st_mode) if originals[name] is not None else None
        if name != "/etc/default/apport" and originals[name] not in (None, data):
            raise ValueError("managed_policy_conflict")
    masks = {}
    for unit in COLLECTORS:
        path = root / "etc/systemd/system" / unit
        safe_path(path.parent / ".crash-privacy-parent-check")
        if path.is_symlink() and os.readlink(path) == "/dev/null":
            masks[unit] = "already_masked"
        elif path.exists() or path.is_symlink():
            raise ValueError("collector_local_override")
        else:
            masks[unit] = "absent"
    if backup.exists() or backup.is_symlink():
        raise ValueError("backup_exists")
    safe_path(backup)
    active = loaded_collectors(run)
    before = {unit: properties(unit, run) for unit in COLLECTORS}
    sysctls_before = {name: (root / "proc/sys" / name.replace(".", "/")).read_text().strip()
                      for name in SYSCTLS}
    backup.mkdir(mode=0o700)
    for index, (name, data) in enumerate(originals.items()):
        if data is not None:
            durable_write(backup / f"{index:03d}.conf", data, 0o600)
    receipt = dict(schema=SCHEMA, status="prepared", masks_before=masks,
                   collectors_before=before, active_before=active,
                   sysctls_before=sysctls_before,
                   files=[dict(path=name, backup=f"{index:03d}.conf" if originals[name] is not None else None,
                               original_mode=original_modes[name],
                               sha256=hashlib.sha256(data).hexdigest())
                          for index, (name, data) in enumerate(files.items())],
                   old_artifacts="preserved_unopened", lantern_services_restarted=False)
    receipt_path = backup / "receipt.json"
    durable_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode(), 0o600)
    # Stop existing reporters first, including concrete template instances.
    # Apport's ExecStop resets core_pattern, so write sysctls AFTER the stop.
    for unit in active:
        require_command(["/usr/bin/systemctl", "stop", unit], run)
    for name, data in files.items():
        durable_write(root / name.lstrip("/"), data, 0o644)
    for unit in COLLECTORS:
        if masks[unit] == "absent":
            path = root / "etc/systemd/system" / unit
            os.symlink("/dev/null", path)
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    require_command(["/usr/bin/systemctl", "daemon-reload"], run)
    # Avoid sysctl --system: do not reapply unrelated platform settings.
    for name, value in SYSCTLS.items():
        (root / "proc/sys" / name.replace(".", "/")).write_text(value + "\n")
    receipt["status"] = "applied_pending_verification"
    durable_write(receipt_path, (json.dumps(receipt, indent=2) + "\n").encode(), 0o600)
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
            print(json.dumps(dict(schema=SCHEMA, mode="plan", collector_masks=COLLECTORS,
                                  files=POLICY_FILES, apport="preserve other settings; enabled=0",
                                  restarts="crash collectors only; Lantern/session restarts deferred"), indent=2))
            return 0
        if platform.system() != "Linux" or os.geteuid() != 0:
            raise ValueError("root_linux_required")
        root = Path("/")
        if args.apply:
            if platform.machine() not in {"aarch64", "arm64"}:
                raise ValueError("arm64_lantern_required")
            installed = read_config(root / "etc/sage-lantern/appliance-installed.json")
            if not installed or not json.loads(installed).get("release_id", "").startswith("lantern-"):
                raise ValueError("commissioned_lantern_required")
            if args.backup_dir is None or args.backup_dir.parent != Path("/var/backups"):
                raise ValueError("new_var_backups_directory_required")
            receipt = apply(root, args.backup_dir, command)
        else:
            receipt = None
        accounts = {name: pwd.getpwnam(name).pw_uid for name in ("sage", "sage-lantern")}
        report = check(root, command, accounts)
        report["mode"] = "apply" if args.apply else "check"
        if receipt:
            report["backup_receipt"] = str(receipt)
            saved = json.loads(read_config(receipt))
            saved["status"] = "configuration_verified" if report["configuration_ok"] else "verification_failed"
            durable_write(receipt, (json.dumps(saved, indent=2) + "\n").encode(), 0o600)
        print(json.dumps(report, sort_keys=True, indent=2))
        return 0 if report["configuration_ok"] else 1
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        # Never emit an exception containing a config, journal or command payload.
        reason = str(error) if isinstance(error, ValueError) else "policy_operation_refused"
        if re.fullmatch(r"[a-z_]{1,64}", reason) is None:
            reason = "policy_operation_refused"
        print(json.dumps(dict(schema=SCHEMA, configuration_ok=False, error=reason,
                              inspect_backup_receipt=bool(args.apply))))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
