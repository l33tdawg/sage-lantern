#!/usr/bin/env python3
"""Configure recoverable GDM automatic login into the Lantern-only session."""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
from pathlib import Path


CONFIG = Path("/etc/gdm3/daemon.conf")
BACKUP = Path("/var/lib/sage-lantern/recovery/kiosk/gdm3-daemon.conf.before-kiosk")
ACCOUNTS_CONFIG = Path("/var/lib/AccountsService/users/radxa")
ACCOUNTS_BACKUP = Path(
    "/var/lib/sage-lantern/recovery/kiosk/accountsservice-radxa.before-kiosk"
)
DMRC = Path("/home/radxa/.dmrc")
DMRC_BACKUP = Path("/var/lib/sage-lantern/recovery/kiosk/radxa-dmrc.before-kiosk")


def configure_daemon(text: str) -> str:
    lines = text.splitlines()
    try:
        daemon_index = next(index for index, line in enumerate(lines) if line.strip() == "[daemon]")
    except StopIteration as exc:
        raise SystemExit("Refusing to edit GDM config without a [daemon] section") from exc

    section_end = next(
        (index for index in range(daemon_index + 1, len(lines)) if lines[index].strip().startswith("[")),
        len(lines),
    )
    keys = {"AutomaticLoginEnable": "true", "AutomaticLogin": "radxa"}
    filtered = [
        line
        for line in lines[daemon_index + 1 : section_end]
        if line.lstrip("# ").split("=", 1)[0].strip() not in keys
    ]
    replacement = [lines[daemon_index], *(f"{key}={value}" for key, value in keys.items()), *filtered]
    result = [*lines[:daemon_index], *replacement, *lines[section_end:]]
    return "\n".join(result).rstrip() + "\n"


def configure_accounts(text: str) -> str:
    lines = text.splitlines()
    if "[User]" not in lines:
        raise SystemExit("Refusing to edit AccountsService config without [User]")
    result = []
    seen_session = False
    seen_xsession = False
    for line in lines:
        if line.startswith("Session="):
            result.append("Session=sage-lantern")
            seen_session = True
        elif line.startswith("XSession="):
            result.append("XSession=sage-lantern")
            seen_xsession = True
        else:
            result.append(line)
    user_index = result.index("[User]") + 1
    if not seen_session:
        result.insert(user_index, "Session=sage-lantern")
        user_index += 1
    if not seen_xsession:
        result.insert(user_index, "XSession=sage-lantern")
    return "\n".join(result).rstrip() + "\n"


def atomic_replace(path: Path, text: str, uid: int, gid: int, mode: int) -> None:
    descriptor, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temp_name, uid, gid)
        os.chmod(temp_name, mode)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def main() -> int:
    if os.geteuid() != 0:
        raise SystemExit("Run this configurator with sudo")
    original = CONFIG.read_text(encoding="utf-8")
    BACKUP.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not BACKUP.exists():
        shutil.copy2(CONFIG, BACKUP)
        BACKUP.chmod(0o600)

    updated = configure_daemon(original)
    if updated != original:
        source_stat = CONFIG.stat()
        atomic_replace(
            CONFIG,
            updated,
            source_stat.st_uid,
            source_stat.st_gid,
            stat.S_IMODE(source_stat.st_mode),
        )

    accounts_original = ACCOUNTS_CONFIG.read_text(encoding="utf-8")
    if not ACCOUNTS_BACKUP.exists():
        shutil.copy2(ACCOUNTS_CONFIG, ACCOUNTS_BACKUP)
        ACCOUNTS_BACKUP.chmod(0o600)
    accounts_updated = configure_accounts(accounts_original)
    if accounts_updated != accounts_original:
        accounts_stat = ACCOUNTS_CONFIG.stat()
        atomic_replace(
            ACCOUNTS_CONFIG,
            accounts_updated,
            accounts_stat.st_uid,
            accounts_stat.st_gid,
            stat.S_IMODE(accounts_stat.st_mode),
        )

    if DMRC.exists() and not DMRC_BACKUP.exists():
        shutil.copy2(DMRC, DMRC_BACKUP)
        DMRC_BACKUP.chmod(0o600)
    atomic_replace(DMRC, "[Desktop]\nSession=sage-lantern\n", 1000, 1000, 0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
