#!/usr/bin/env python3
"""Guarded Orange Pi 5 Plus SD-root -> blank NVMe migration.

Default is inspection only. --apply requires the serial and size from inspection.
Uses the reviewed Armbian installer, leaves SPI alone, and retains SD boot.
Never use a commissioned unit's SD as another unit's provisioning image.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time

INSTALLER_HASHES = {
    '/usr/lib/armbian-config/config.functions.sh': '988795753fee66dbd6158a9678278388641183f7567ebb26db79701859930f6d',
    '/usr/lib/armbian-config/config.system.sh': 'a077922f3b75e200a45aa97c7b7e0f9e5a53efb4bf6855a85c1415d2f042598a',
    '/usr/bin/armbian-install': 'e43a9f416b8f66b37a260074474e5bb627565ab2d9863f60a068858b097ea746',
}
ENV = dict(os.environ, PATH='/usr/sbin:/usr/bin:/sbin:/bin', LC_ALL='C')

def run(*args):
    return subprocess.check_output(args, text=True, env=ENV).strip()

def require(ok, message):
    if not ok:
        raise ValueError(message)

def validate(info, serial=None, size=None):
    require(any(x in info['compatible'] for x in ('rockchip,rk3588-orangepi-5-plus', 'xunlong,orangepi-5-plus')), 'Not an Orange Pi 5 Plus')
    require(re.fullmatch(r'/dev/mmcblk\d+p\d+', info['root']) is not None, 'Root must still be on SD; refusing a repeat migration')
    disk = info['disk']
    require(re.fullmatch(r'/dev/nvme\d+n\d+', disk['path']) is not None and disk['type'] == 'disk', 'Target must be a whole NVMe disk')
    require(not disk.get('children') and not disk.get('fstype') and not disk.get('pttype'), 'Target already has partitions or signatures; refusing to erase it')
    require(not any(disk.get('mountpoints') or []) and disk['ro'] is False, 'Target is mounted or read-only')
    require(not info['signatures'] and not info['holders'] and disk['path'] not in info['swaps'], 'Target is in use or has disk signatures')
    require(bool(disk['serial'].strip()) and disk['size'] >= 64_000_000_000, 'Missing serial or insufficient capacity')
    if serial is not None:
        require(disk['serial'].strip() == serial and disk['size'] == size, 'Target identity changed')
    require(info['installer_matches'], 'Installer version differs from reviewed version; review before use')

def inspect(target):
    require(re.fullmatch(r'/dev/nvme\d+n\d+', target) is not None, 'Invalid target')
    disks = json.loads(run('lsblk', '-bJ', '-o', 'PATH,TYPE,SIZE,MODEL,SERIAL,FSTYPE,PTTYPE,MOUNTPOINTS,RO', target))['blockdevices']
    require(len(disks) == 1, 'Ambiguous target')
    return {
        'compatible': Path('/proc/device-tree/compatible').read_bytes().decode().strip('\0').split('\0'),
        'root': run('findmnt', '-no', 'SOURCE', '/'),
        'disk': disks[0],
        'signatures': json.loads(run('wipefs', '--no-act', '--json', target))['signatures'],
        'holders': [p.name for p in Path('/sys/class/block', Path(target).name, 'holders').iterdir()],
        'swaps': [line.split()[0] for line in run('swapon', '--show', '--noheadings', '--raw', '--output', 'NAME').splitlines() if line.strip()],
        'installer_matches': all(Path(p).is_file() and hashlib.sha256(Path(p).read_bytes()).hexdigest() == h for p, h in INSTALLER_HASHES.items()),
    }

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--target', default='/dev/nvme0n1')
    ap.add_argument('--expect-serial')
    ap.add_argument('--expect-bytes', type=int)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    require(os.geteuid() == 0, 'Run as root')
    info = inspect(args.target)
    validate(info)
    print(json.dumps(info, indent=2), flush=True)
    if not args.apply:
        print('Inspection only. To migrate, supply --apply --expect-serial SERIAL --expect-bytes SIZE.')
        return
    require(args.expect_serial is not None and args.expect_bytes is not None, 'Apply requires explicit expected serial and size')
    validate(inspect(args.target), args.expect_serial, args.expect_bytes)
    require(not Path('/var/lib/sage-lantern').exists(), 'Migrate before creating Lantern private identities')
    require(not run('dpkg', '--audit'), 'Package installation is incomplete')
    busy = subprocess.run(['fuser', '/var/lib/dpkg/lock', '/var/lib/dpkg/lock-frontend'], env=ENV,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    require(busy.returncode == 1, 'A package manager is active, or lock inspection failed')
    backup = Path('/var/lib/lantern-base-provision') / time.strftime('%Y%m%d-%H%M%S')
    backup.mkdir(parents=True, mode=0o700)
    bootenv = Path('/boot/armbianEnv.txt')
    original = bootenv.read_bytes()
    (backup / 'armbianEnv.sd-root.txt').write_bytes(original)
    (backup / 'inspection.json').write_text(json.dumps(info, indent=2) + '\n')
    shutil.copy2('/etc/fstab', backup / 'fstab.sd-root')
    print('Migrating OS to NVMe; SD boot stays in place. Backup: ' + str(backup), flush=True)
    try:
        with (backup / 'installer.log').open('w') as log:
            subprocess.run(['systemd-inhibit', '--what=sleep:shutdown', '--mode=block', '--who=Lantern setup',
                            '--why=Copying OS to NVMe', '/usr/bin/armbian-install', '--target', args.target,
                            '--boot', 'sd', '--fs', 'ext4', '--yes'], check=True, env=ENV, stdout=log, stderr=subprocess.STDOUT)
        part = args.target + 'p1'
        uuid = run('blkid', '-s', 'UUID', '-o', 'value', part)
        require(('rootdev=UUID=' + uuid) in bootenv.read_text().splitlines(), 'Boot root UUID does not match NVMe')
        with tempfile.TemporaryDirectory(prefix='lantern-nvme-', dir='/mnt') as mount:
            subprocess.run(['mount', part, mount], check=True, env=ENV)
            try:
                dest = Path(mount)
                fstab = (dest / 'etc/fstab').read_text()
                require('UUID=' + uuid in fstab and '/media/boot-media/boot' in fstab, 'NVMe fstab missing root or SD boot mapping')
                # Same-machine identity and accounts must survive migration unchanged.
                for rel in ('etc/machine-id', 'etc/passwd', 'etc/shadow', 'etc/ssh/ssh_host_ed25519_key', 'etc/os-release'):
                    require((dest / rel).read_bytes() == (Path('/') / rel).read_bytes(), 'Copied file differs: ' + rel)
                (dest / 'var/lib/lantern-base-provision').mkdir(exist_ok=True)
                shutil.copytree(backup, dest / 'var/lib/lantern-base-provision' / backup.name, dirs_exist_ok=True)
                report = {'state': 'copied-awaiting-reboot', 'root_uuid': uuid, 'target': args.target,
                          'serial': args.expect_serial, 'boot': 'SD required', 'backup': str(backup)}
                (backup / 'result.json').write_text(json.dumps(report, indent=2) + '\n')
                (dest / 'var/lib/lantern-base-provision/result.json').write_text(json.dumps(report, indent=2) + '\n')
            finally:
                subprocess.run(['umount', mount], check=True, env=ENV)
        os.sync()
        print(json.dumps(report, indent=2))
        print('Copy verified. Reboot, keep SD inserted, then verify findmnt / and findmnt /boot. No automatic reboot.')
    except BaseException:
        bootenv.write_bytes(original)
        os.sync()
        print('Migration did not verify; SD boot root restored. Target retained for inspection, never automatically erased again.', file=sys.stderr)
        raise

if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as exc:
        sys.exit(str(exc))
