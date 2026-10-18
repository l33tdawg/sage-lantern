#!/usr/bin/env python3
"""Finish Orange Pi 5 Plus migration: SPI bootloader, kernel and OS on NVMe.

Run after cf-nvme-setup has migrated root and rebooted onto NVMe. Never formats
NVMe. Backs up SPI and verifies all 16 MB after the native bootloader-only flash.
Power off and remove SD afterwards; successful SD-free cold boot is a separate
acceptance step. --apply is required for changes.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

IMAGE = Path('/usr/lib/linux-u-boot-vendor-orangepi5-plus/rkspi_loader.img')
IMAGE_SHA = '9caff588143668fe20a26bd358c2b3ebbdc991486d640d2871461a6dd6ad813b'
HOOK_SHA = 'eb4fd39f73e1126ae15b05552abd747cf37b0ca279abfefc94ae81e23a1e7c6a'
SPI_BYTES = 16777216
BACKUP = Path('/var/lib/lantern-base-provision/spi-nvme')

def require(ok, message):
    if not ok:
        raise ValueError(message)

def run(*args):
    return subprocess.check_output(args, text=True).strip()

def sha(data):
    return hashlib.sha256(data).hexdigest()

def local_fstab(text, root_uuid):
    rows = []
    have_root = False
    for line in text.splitlines():
        fields = line.split()
        if fields and not line.lstrip().startswith('#'):
            if len(fields) > 1 and fields[1] == '/':
                require(fields[0] == 'UUID=' + root_uuid, 'Unexpected root fstab entry')
                have_root = True
            if len(fields) > 1 and fields[1] in ('/boot', '/media/boot-media'):
                require((fields[1] == '/boot' and fields[0] == '/media/boot-media/boot') or
                        (fields[1] == '/media/boot-media' and fields[0].startswith('UUID=')),
                        'Unexpected boot mount; inspect manually')
                continue
        rows.append(line)
    require(have_root, 'Root fstab entry missing')
    return '\n'.join(rows) + '\n'

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--sd-root', required=True)
    ap.add_argument('--expect-serial', required=True)
    ap.add_argument('--apply', action='store_true')
    args = ap.parse_args()
    require(os.geteuid() == 0, 'Run as root')
    require(re.fullmatch(r'/dev/mmcblk\d+p\d+', args.sd_root), 'Expected SD partition')
    compatible = Path('/proc/device-tree/compatible').read_bytes().decode().split('\0')
    require(any(s in compatible for s in ('rockchip,rk3588-orangepi-5-plus','xunlong,orangepi-5-plus')), 'Wrong board')
    root = run('findmnt', '-no', 'SOURCE', '/')
    require(re.fullmatch(r'/dev/nvme\d+n\d+p1', root), 'Must already be running from NVMe partition 1')
    disk = root[:-2]
    info = json.loads(run('lsblk', '-bJ', '-o', 'PATH,SERIAL,PTTYPE,SIZE', disk))['blockdevices'][0]
    require(info['serial'].strip() == args.expect_serial and info['pttype'] == 'gpt', 'Wrong NVMe identity or partition table')
    require(Path('/sys/class/mtd/mtd0/size').read_text().strip() == str(SPI_BYTES), 'Wrong SPI capacity')
    require(Path('/sys/class/mtd/mtd0/name').read_text().strip() == 'loader', 'Wrong SPI partition')
    image = IMAGE.read_bytes()
    require(len(image) == SPI_BYTES and sha(image) == IMAGE_SHA, 'SPI image differs from reviewed vendor image')
    require(sha(Path('/usr/lib/u-boot/platform_install.sh').read_bytes()) == HOOK_SHA, 'Bootloader hook changed')
    require(list(IMAGE.parent.glob('rkspi_loader*.img')) == [IMAGE] and
            not list(IMAGE.parent.glob('u-boot-rockchip-spi*.bin')), 'Ambiguous SPI image selection')
    uuid = run('blkid', '-s', 'UUID', '-o', 'value', root)
    fstab = Path('/etc/fstab').read_text()
    updated = local_fstab(fstab, uuid)
    current_spi = Path('/dev/mtd0').read_bytes()
    require(len(current_spi) == SPI_BYTES, 'Short SPI read')
    print(json.dumps({'root':root,'root_uuid':uuid,'nvme_serial':args.expect_serial,
                      'spi_before_sha256':sha(current_spi),'spi_image_sha256':IMAGE_SHA,
                      'already_flashed': current_spi == image, 'apply':args.apply},indent=2),flush=True)
    if not args.apply:
        return
    BACKUP.mkdir(parents=True, mode=0o700, exist_ok=True)
    backup = BACKUP / 'spi-before.bin'
    require(not backup.exists(), 'Previous SPI attempt exists; inspect its state before retrying')
    backup.write_bytes(current_spi)
    (BACKUP / 'fstab.before').write_text(fstab)
    (BACKUP / 'spi-before.sha256').write_text(sha(current_spi) + '\n')
    os.sync()
    # A non-recursive bind exposes NVMe's underlying /boot even when the SD
    # boot directory is currently mounted over /boot.
    with tempfile.TemporaryDirectory(prefix='lantern-spi-') as temp:
        sd = Path(temp) / 'sd'; nvme = Path(temp) / 'nvme'
        sd.mkdir(); nvme.mkdir()
        subprocess.run(['mount','-o','ro,noload',args.sd_root,str(sd)],check=True)
        try:
            subprocess.run(['mount','--bind','/',str(nvme)],check=True)
            try:
                source = sd / 'boot'; dest = nvme / 'boot'
                kernel = 'vmlinuz-' + os.uname().release
                require((source / kernel).is_file() and (source / 'boot.scr').is_file(), 'SD lacks current kernel or boot script')
                dest.mkdir(exist_ok=True)
                require(not list(dest.iterdir()), 'NVMe /boot is not empty; inspect before overwriting')
                subprocess.run(['rsync','-aHAX',str(source)+'/',str(dest)+'/'],check=True)
                for name in (kernel,'boot.scr','initrd.img-' + os.uname().release):
                    require(sha((source/name).read_bytes()) == sha((dest/name).read_bytes()), 'Boot copy mismatch: '+name)
                env = dest / 'armbianEnv.txt'
                lines = [s for s in env.read_text().splitlines() if not s.startswith(('rootdev=','rootfstype='))]
                env.write_text('\n'.join(lines)+f'\nrootdev=UUID={uuid}\nrootfstype=ext4\n')
            finally:
                subprocess.run(['umount',str(nvme)],check=True)
        finally:
            subprocess.run(['umount',str(sd)],check=True)
    os.sync()
    # Use only the native bootloader subcommand; the full mtd installer would
    # repartition the live NVMe and is expressly not used here.
    with (BACKUP / 'flash.log').open('w') as log:
        subprocess.run(['systemd-inhibit','--what=sleep:shutdown','--mode=block',
                        '--why=Writing SPI bootloader','armbian-install','bootloader',
                        '--target','mtd','--yes'],check=True,stdout=log,stderr=subprocess.STDOUT)
    os.sync()
    subprocess.run(['blockdev','--flushbufs','/dev/mtdblock0'],check=True)
    readback = Path('/dev/mtd0').read_bytes()
    require(readback == image, 'SPI readback mismatch; do not power off, inspect flash.log')
    # Publish fstab only after boot files and SPI have verified.
    Path('/etc/fstab').write_text(updated)
    subprocess.run(['systemctl','daemon-reload'],check=True)
    for target in ('/boot','/media/boot-media'):
        if subprocess.run(['mountpoint','-q',target]).returncode == 0:
            subprocess.run(['umount',target],check=True)
    require(run('findmnt','-no','SOURCE','-T','/boot') == root, '/boot does not reside on NVMe')
    report = dict(state='SPI verified; awaiting SD-free cold boot', root=root, root_uuid=uuid,
                  spi_sha256=sha(readback), bytes_verified=len(readback), backup=str(BACKUP))
    (BACKUP / 'result.json').write_text(json.dumps(report,indent=2)+'\n')
    os.sync()
    print(json.dumps(report,indent=2))

if __name__ == '__main__':
    main()
