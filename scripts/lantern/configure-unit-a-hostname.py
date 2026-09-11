#!/usr/bin/env python3
"""Assign a prepared Lantern a stable, recoverable hostname for mDNS."""

from __future__ import annotations

import os
import argparse
import json
import re
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path


HOSTNAME = "sage-lantern-a"
UNIT_METADATA = Path("/etc/sage-lantern/unit.env")
HOSTNAME_FILE = Path("/etc/hostname")
HOSTS_FILE = Path("/etc/hosts")
BACKUP_DIR = Path("/var/lib/sage-lantern/recovery/network")


def atomic_write(path: Path, text: str) -> None:
    source_stat = path.stat()
    descriptor, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp_name, source_stat.st_uid, source_stat.st_gid)
        os.chmod(temp_name, stat.S_IMODE(source_stat.st_mode))
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def hostname_for_unit(unit_id: str) -> str:
    if (not isinstance(unit_id, str)
            or re.fullmatch(r"unit-[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", unit_id) is None):
        raise ValueError("Unit ID must be a lowercase DNS-safe unit- identifier")
    hostname = "sage-lantern-" + unit_id[5:]
    if len(hostname) > 63:
        raise ValueError("Unit hostname exceeds the DNS label limit")
    return hostname


def prepared_unit(path: Path) -> str:
    with path.open("rb") as stream:
        raw = stream.read(257)
    if len(raw) > 256:
        raise ValueError("Prepared metadata is oversized")
    fields = {}
    for line in raw.decode("ascii").splitlines():
        name, separator, value = line.partition("=")
        if not separator or name in fields:
            raise ValueError("Invalid prepared metadata")
        fields[name] = value
    if (set(fields) != {"UNIT_ID", "PROVISIONING_SCHEMA"}
            or fields["PROVISIONING_SCHEMA"] != "sage-lantern-unit-v1"):
        raise ValueError("Expected prepared Lantern unit metadata")
    hostname_for_unit(fields["UNIT_ID"])
    return fields["UNIT_ID"]


def update_hosts(text: str, hostname: str = HOSTNAME) -> str:
    lines = text.splitlines()
    found = False
    result: list[str] = []
    for line in lines:
        fields = line.split()
        if fields and fields[0] == "127.0.1.1":
            result.append(f"127.0.1.1\t{hostname}")
            found = True
        else:
            result.append(line)
    if not found:
        result.append(f"127.0.1.1\t{hostname}")
    return "\n".join(result).rstrip() + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit-id", help="Must match the prepared unit; otherwise derive it")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--metadata-file", type=Path, default=UNIT_METADATA,
                        help="Alternate metadata is allowed only with --dry-run")
    args = parser.parse_args(argv)
    if not args.dry_run and args.metadata_file != UNIT_METADATA:
        parser.error("Alternate metadata requires --dry-run")
    try:
        unit_id = prepared_unit(args.metadata_file)
        if args.unit_id is not None and args.unit_id != unit_id:
            raise ValueError("Requested unit does not match prepared metadata")
        hostname = hostname_for_unit(unit_id)
    except (OSError, UnicodeError, ValueError) as error:
        parser.error(str(error))
    if args.dry_run:
        print(json.dumps({"dry_run": True, "unit_id": unit_id, "hostname": hostname,
                          "writes": [str(HOSTNAME_FILE), str(HOSTS_FILE)],
                          "backup_directory": str(BACKUP_DIR)}, sort_keys=True))
        return 0
    if os.geteuid() != 0:
        raise SystemExit("Run this configurator with sudo")
    BACKUP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for source, name in ((HOSTNAME_FILE, f"hostname.before-{unit_id}"), (HOSTS_FILE, f"hosts.before-{unit_id}")):
        backup = BACKUP_DIR / name
        if not backup.exists():
            shutil.copy2(source, backup)
            backup.chmod(0o600)

    atomic_write(HOSTNAME_FILE, f"{hostname}\n")
    atomic_write(HOSTS_FILE, update_hosts(HOSTS_FILE.read_text(encoding="utf-8"), hostname))
    subprocess.run(["hostnamectl", "set-hostname", hostname], check=True)
    subprocess.run(["systemctl", "try-restart", "avahi-daemon.service"], check=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
