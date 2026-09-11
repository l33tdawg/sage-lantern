#!/usr/bin/env python3
"""Plan-first Debian ARM64 Plymouth installation; never edits boot arguments."""

import argparse
import configparser
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile


THEME = "sage-lantern"
FILES = ("sage-lantern.plymouth", "sage-lantern.script")
SOURCE = Path(__file__).resolve().parents[2] / "software/plymouth/sage-lantern"
ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "LC_ALL": "C"}


class InstallError(RuntimeError):
    pass


def checked(path, *, directory=False, trusted=False):
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink():
            raise InstallError("symlink rejected")
        if trusted and ancestor.exists():
            info = ancestor.stat()
            if info.st_uid != 0 or info.st_mode & 0o022:
                raise InstallError("untrusted target ownership or permissions")
    info = path.lstat()
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise InstallError("directory required")
    elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InstallError("single-link regular file required")
    return info


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def run(command):
    return subprocess.run(command, check=True, text=True, capture_output=True,
                          timeout=300, env=ENV).stdout.strip()


def boot_status(root):
    path = root / 'proc/cmdline'
    try:
        checked(path)
        raw = path.read_bytes()
        if len(raw) > 16384:
            raise ValueError()
        tokens = raw.decode('ascii').split()
    except (OSError, ValueError):
        return dict(current_kernel='unknown', next_boot='unknown', rendering='unverified')
    disabled = any(token in {'plymouth.enable=0', 'plymouth.enable=false', 'rd.plymouth=0',
                            'rd.plymouth=false', 'nosplash'} for token in tokens)
    switches = [token for token in tokens if token.startswith(('plymouth.enable=', 'rd.plymouth='))]
    supported = all(token in {'plymouth.enable=0', 'plymouth.enable=1', 'plymouth.enable=false',
                             'plymouth.enable=true', 'rd.plymouth=0', 'rd.plymouth=1',
                             'rd.plymouth=false', 'rd.plymouth=true'} for token in switches)
    if not supported:
        return dict(current_kernel='unsupported', cmdline_sha256=hashlib.sha256(raw).hexdigest(),
                    next_boot='unknown', rendering='unverified')
    return dict(current_kernel='disabled' if disabled else 'requested' if 'splash' in tokens else 'not_requested',
                cmdline_sha256=hashlib.sha256(raw).hexdigest(), next_boot='unknown', rendering='unverified')


def verify_initramfs(plan, *, runner=run):
    initrd = Path(plan['initrd'])
    checked(initrd, trusted=initrd.parent == Path('/boot'))
    before = digest(initrd)
    with tempfile.TemporaryDirectory(prefix='lantern-initrd-check-') as temporary:
        destination = Path(temporary).resolve() / 'extracted'
        runner([plan['extractor'], str(initrd), str(destination)])
        checked(destination, directory=True)
        main = destination / 'main'
        if os.path.lexists(main):
            checked(main, directory=True)
            layout = main
            if any(os.path.lexists(destination / relative) for relative in plan['initramfs_files']):
                raise InstallError('rebuilt initramfs mixed extraction layouts')
        else:
            layout = destination
        for relative, expected in plan['initramfs_files'].items():
            member = layout / relative
            ancestor = layout
            for component in Path(relative).parts[:-1]:
                ancestor = ancestor / component
                checked(ancestor, directory=True)
            checked(member)
            if digest(member) != expected:
                raise InstallError('rebuilt initramfs member bytes mismatch')
        if digest(initrd) != before:
            raise InstallError('rebuilt initramfs changed during verification')
    return before


def inspect(source=SOURCE, root=Path("/"), *, runner=run, kernel=None,
            system=None, machine=None):
    if (system or platform.system()) != "Linux" or (machine or platform.machine()) != "aarch64":
        raise InstallError("requires Linux aarch64; no host simulation is applied")
    kernel = kernel or platform.release()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]{0,127}", kernel):
        raise InstallError("invalid running kernel")
    trusted = root == Path("/")
    def target(relative):
        return root / relative
    checked(source, directory=True)
    if {path.name for path in source.iterdir()} != {*FILES, "README.md"}:
        raise InstallError("unexpected source inventory")
    checked(source / "README.md")
    sources = {}
    for name in FILES:
        path = source / name
        if checked(path).st_size > 65536:
            raise InstallError("theme source too large")
        sources[name] = digest(path)
    manifest = hashlib.sha256(json.dumps(sources, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    tools = {}
    for name, relative in (("selector", "usr/sbin/plymouth-set-default-theme"),
                           ("initramfs", "usr/sbin/update-initramfs"), ("daemon", "usr/sbin/plymouthd"),
                           ("extractor", "usr/bin/unmkinitramfs")):
        path = target(relative)
        if checked(path, trusted=trusted).st_mode & 0o111 == 0:
            raise InstallError("required tool is not executable")
        tools[name] = str(path)
    for relative in ("usr/share/plymouth/themes", "etc/plymouth", "boot", "var/lib",
                     f"usr/lib/modules/{kernel}"):
        checked(target(relative), directory=True, trusted=trusted)
    checked(target("usr/share/initramfs-tools/hooks/plymouth"), trusted=trusted)
    checked(target("usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), trusted=trusted)
    checked(target("usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"), trusted=trusted)
    plugin = target("usr/lib/aarch64-linux-gnu/plymouth/script.so")
    checked(plugin, trusted=trusted)
    label = target("usr/lib/aarch64-linux-gnu/plymouth/label.so")
    checked(label, trusted=trusted)
    initrd = target(f"boot/initrd.img-{kernel}")
    size = checked(initrd, trusted=trusted).st_size
    if not 0 < size <= 512 * 1024 * 1024:
        raise InstallError("unsupported initramfs size")
    checked(target(f"boot/vmlinuz-{kernel}"), trusted=trusted)
    config = target("etc/plymouth/plymouthd.conf")
    config_exists = config.exists() or config.is_symlink()
    if config_exists and checked(config, trusted=trusted).st_size > 65536:
        raise InstallError("configuration too large")
    destination = target("usr/share/plymouth/themes/sage-lantern")
    backup = target("var/lib/sage-lantern-boot-theme-backup")
    for path in (destination, backup):
        if path.exists() or path.is_symlink():
            raise InstallError("existing theme or recovery backup requires manual review")
    for path in (target("boot"), target("var/lib")):
        if shutil.disk_usage(path).free < size * 2 + 64 * 1024 * 1024:
            raise InstallError("insufficient initramfs recovery space")
    previous = runner([tools["selector"]])
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", previous) or previous == THEME:
        raise InstallError("unsupported previous default")
    checked(target(f"usr/share/plymouth/themes/{previous}/{previous}.plymouth"), trusted=trusted)
    plan = {"mode": "plan-only", "theme": THEME, "source_manifest_sha256": manifest,
            "source_files": sources, "previous_default": previous, "kernel": kernel,
            "destination": str(destination), "backup": str(backup), "initrd": str(initrd),
            "config": str(config), "config_existed": config_exists, "initrd_sha256": digest(initrd),
            "config_sha256": digest(config) if config_exists else None,
            "commands": [[tools["selector"], THEME], [tools["initramfs"], "-u", "-k", kernel]],
            "recovery_commands": [[tools["selector"], previous], [tools["initramfs"], "-u", "-k", kernel]],
            "reboot": False, "bootloader_or_cmdline_changes": False}
    plan['boot_configuration'] = boot_status(root)
    plan['extractor'] = tools['extractor']
    plan['initramfs_files'] = {
        'usr/share/plymouth/themes/sage-lantern/' + name: value for name, value in sources.items()}
    for relative in ('usr/sbin/plymouthd', 'usr/lib/aarch64-linux-gnu/plymouth/script.so',
                     'usr/lib/aarch64-linux-gnu/plymouth/label.so',
                     'usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
                     'usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'):
        plan['initramfs_files'][relative] = digest(target(relative))
    plan["plan_sha256"] = hashlib.sha256(json.dumps(plan, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return plan


def write_new(path, content, mode=0o600):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def apply_plan(plan, source=SOURCE, *, runner=run):
    """Internal executor; CLI rechecks preflight/digest as root before calling."""
    if plan['boot_configuration']['current_kernel'] != 'requested':
        raise InstallError('current kernel does not verifiably request Plymouth; no boot argument edits')
    contents = {}
    for name in FILES:
        checked(source / name)
        contents[name] = (source / name).read_bytes()
        if hashlib.sha256(contents[name]).hexdigest() != plan["source_files"][name]:
            raise InstallError("source changed after review")
    backup = Path(plan["backup"])
    destination = Path(plan["destination"])
    for path in (backup, destination):
        if path.exists() or path.is_symlink():
            raise InstallError("destination changed after preflight")
    initrd = Path(plan["initrd"])
    checked(initrd)
    if digest(initrd) != plan["initrd_sha256"]:
        raise InstallError("initramfs changed after preflight")
    config = Path(plan["config"])
    config_content = None
    if plan["config_existed"]:
        checked(config)
        config_content = config.read_bytes()
        if hashlib.sha256(config_content).hexdigest() != plan["config_sha256"]:
            raise InstallError("configuration changed after preflight")
    elif config.exists() or config.is_symlink():
        raise InstallError("configuration appeared after preflight")
    backup.mkdir(mode=0o700)
    sync_directory(backup.parent)
    write_new(backup / "recovery.json", json.dumps(plan, sort_keys=True, indent=2).encode())
    with initrd.open("rb") as original:
        descriptor = os.open(backup / "initrd.previous", os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, "wb") as copy:
            shutil.copyfileobj(original, copy, 1024 * 1024)
            copy.flush()
            os.fsync(copy.fileno())
    if digest(backup / "initrd.previous") != plan["initrd_sha256"]:
        raise InstallError("initramfs backup verification failed")
    if config_content is not None:
        write_new(backup / "plymouthd.conf.previous", config_content)
    sync_directory(backup)
    destination.mkdir(mode=0o755)
    for name, content in contents.items():
        write_new(destination / name, content, 0o644)
    sync_directory(destination)
    sync_directory(destination.parent)
    try:
        for command in plan["commands"]:
            runner(command)
        if runner([plan["commands"][0][0]]) != THEME:
            raise InstallError("default theme verification failed")
        checked(config)
        selected = configparser.ConfigParser(interpolation=None)
        selected.read_string(config.read_text())
        if selected.get('Daemon', 'Theme', fallback=None) != THEME:
            raise InstallError('selected theme configuration not explicit')
        verification = dict(plan, initramfs_files=dict(plan['initramfs_files']))
        verification['initramfs_files']['etc/plymouth/plymouthd.conf'] = digest(config)
        rebuilt_sha256 = verify_initramfs(verification, runner=runner)
        with initrd.open('rb') as stream:
            os.fsync(stream.fileno())
        sync_directory(initrd.parent)
    except (OSError, ValueError, configparser.Error, subprocess.SubprocessError, InstallError):
        raise InstallError("activation incomplete; preserve backup, recover previous default/initramfs before reboot") from None
    write_new(backup / 'verified-initramfs.json', json.dumps(dict(sha256=rebuilt_sha256,
        files=verification['initramfs_files'], next_boot='unknown', rendering='unverified'), sort_keys=True).encode())
    write_new(backup / "APPLIED", b"Theme selected and initramfs members verified; next-boot and visual acceptance pending.\n")
    sync_directory(backup)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-manifest")
    parser.add_argument("--expected-plan")
    args = parser.parse_args(argv)
    try:
        if args.apply and os.geteuid() != 0:
            raise InstallError("--apply requires root")
        plan = inspect()
        if args.apply:
            if args.expected_manifest != plan["source_manifest_sha256"]:
                raise InstallError("--apply requires the reviewed --expected-manifest digest")
            if args.expected_plan != plan["plan_sha256"]:
                raise InstallError("--apply requires the reviewed --expected-plan digest")
            apply_plan(plan)
            plan["mode"] = "applied-visual-acceptance-pending"
        print(json.dumps(plan, sort_keys=True, indent=2))
        return 0
    except (OSError, ValueError, InstallError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, InstallError) else type(error).__name__
        print(f"Boot theme refused or incomplete: {reason}. No reboot. Review recovery state if applying.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
