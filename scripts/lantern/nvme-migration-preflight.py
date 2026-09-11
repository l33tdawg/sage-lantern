#!/usr/bin/env python3
"""Read-only SD-to-NVMe base OS migration preflight; no apply/format/SPI mode.

An empty signature table does not prove absence of user data. This report is
not erase authorization. A separately reviewed writer must revalidate identity,
obtain exclusive device access, retain intent and verify exact-byte readback.
"""

import argparse
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import stat
import subprocess


ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C', 'LC_ALL': 'C'}


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def validate_expected(value):
    require(type(value) is dict and set(value) == {'schema', 'board_compatible', 'sd_root', 'sd_serial',
        'target', 'target_model', 'target_serial', 'target_bytes', 'image_sha512', 'image_sha256', 'image_bytes'}, 'expected_schema')
    require(value['schema'] == 'sage.lantern.nvme-preflight.v1', 'expected_schema')
    require(value['board_compatible'] == 'radxa,rock-5b-plus', 'unsupported_board')
    require(type(value['sd_root']) is str and re.fullmatch('/dev/mmcblk[0-9]+p[0-9]+', value['sd_root']) is not None, 'sd_root')
    require(type(value['target']) is str and re.fullmatch('/dev/nvme[0-9]+n[0-9]+', value['target']) is not None, 'target_whole_nvme')
    for key in ('sd_serial', 'target_model', 'target_serial'):
        require(type(value[key]) is str and 1 <= len(value[key]) <= 128 and value[key].strip() == value[key], key)
    for key in ('target_bytes', 'image_bytes'):
        require(type(value[key]) is int and value[key] > 0 and value[key] % 512 == 0, key)
    require(value['image_bytes'] < value['target_bytes'], 'image_exceeds_target')
    for key, length in (('image_sha512', 128), ('image_sha256', 64)):
        require(type(value[key]) is str and re.fullmatch('[a-f0-9]{' + str(length) + '}', value[key]) is not None, key)


def validate_snapshot(expected, snapshot):
    validate_expected(expected)
    require(expected['board_compatible'] in snapshot['compatible'], 'board_mismatch')
    require(snapshot['root'] == expected['sd_root'] and snapshot['sd_serial'] == expected['sd_serial'], 'sd_identity_mismatch')
    disks = snapshot['lsblk']['blockdevices']
    require(type(disks) is list and len(disks) == 1, 'ambiguous_target')
    disk = disks[0]
    require(disk['path'] == expected['target'] and disk['type'] == 'disk'
            and disk['model'].strip() == expected['target_model']
            and disk['serial'].strip() == expected['target_serial']
            and type(disk['size']) is int and disk['size'] == expected['target_bytes'], 'nvme_identity_mismatch')
    require(disk['ro'] is False and not disk.get('children') and disk['fstype'] is None
            and disk['pttype'] is None and disk['mountpoints'] in (None, [], [None]), 'target_not_unused_whole_disk')
    require(snapshot['holders'] == [] and snapshot['signatures'] == [], 'target_signatures_or_holders')
    require(expected['target'] not in snapshot['swap_devices'], 'target_swap')
    require(snapshot['device_number'] == disk['maj:min'], 'device_number_mismatch')


def command(args):
    result = subprocess.run(args, check=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=30, env=ENV)
    require(len(result.stdout) <= 1048576, 'command_output_limit')
    return result.stdout.decode()


def snapshot(expected):
    target = expected['target']
    sd_name = re.fullmatch('/dev/(mmcblk[0-9]+)p[0-9]+', expected['sd_root']).group(1)
    info = Path(target).lstat()
    require(stat.S_ISBLK(info.st_mode), 'target_not_real_block_device')
    target_name = Path(target).name
    return dict(
        compatible=Path('/proc/device-tree/compatible').read_bytes().decode().strip('\0').split('\0'),
        root=command(['/usr/bin/findmnt', '-n', '-o', 'SOURCE', '/']).strip(),
        sd_serial=Path('/sys/class/block', sd_name, 'device/serial').read_text().strip(),
        lsblk=decode(command(['/usr/bin/lsblk', '--json', '--bytes', '--paths', '--output',
            'PATH,TYPE,SIZE,MODEL,SERIAL,RO,FSTYPE,PTTYPE,MOUNTPOINTS,MAJ:MIN', target])),
        holders=sorted(path.name for path in Path('/sys/class/block', target_name, 'holders').iterdir()),
        signatures=decode(command(['/usr/sbin/wipefs', '--no-act', '--json', target]))['signatures'],
        swap_devices=command(['/usr/sbin/swapon', '--show', '--noheadings', '--raw', '--output', 'NAME']).splitlines(),
        device_number=f'{os.major(info.st_rdev)}:{os.minor(info.st_rdev)}')


def identity(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid',
        'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def verify_image(path, expected):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size > 0, 'image_not_regular')
        compressed = hashlib.sha512()
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            while chunk := stream.read(1048576):
                compressed.update(chunk)
        require(compressed.hexdigest() == expected['image_sha512'], 'compressed_hash_mismatch')
        os.lseek(descriptor, 0, os.SEEK_SET)
        digest = hashlib.sha256()
        size = 0
        with os.fdopen(os.dup(descriptor), 'rb') as stream, lzma.LZMAFile(stream) as decoded:
            while chunk := decoded.read(1048576):
                size += len(chunk)
                require(size <= expected['image_bytes'], 'decompressed_image_too_large')
                digest.update(chunk)
        require(size == expected['image_bytes'] and digest.hexdigest() == expected['image_sha256'], 'decompressed_image_mismatch')
        require(identity(before) == identity(os.fstat(descriptor)) == identity(Path(path).lstat()), 'image_changed')
        return dict(compressed_sha512=compressed.hexdigest(), decompressed_sha256=digest.hexdigest(), bytes=size)
    finally:
        os.close(descriptor)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--verify-image', action='store_true', help='Read and hash full compressed and decompressed image')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'root_required_for_read_only_device_inspection')
    raw = args.expected.read_bytes()
    require(len(raw) <= 16384, 'expected_too_large')
    expected = decode(raw)
    validate_expected(expected)
    before = snapshot(expected)
    validate_snapshot(expected, before)
    require(args.image.is_file() and not args.image.is_symlink(), 'image_missing_or_symlink')
    image = verify_image(args.image, expected) if args.verify_image else None
    after = snapshot(expected)
    validate_snapshot(expected, after)
    require(before == after, 'device_state_changed')
    print(json.dumps(dict(schema='sage.lantern.nvme-preflight-result.v1', dry_run=True,
        expected_sha256=hashlib.sha256(raw).hexdigest(), observed=after, image=image,
        write_authorized=False, firmware_changed=False, absence_of_user_data_verified=False,
        remaining=['independent_vendor_SPI_boot_review', 'stable_power_and_suspend_inhibition',
                   'explicit_exact_serial_erase_authorization', 'exclusive_writer_and_readback',
                   'NVMe_root_boot_and_capacity_acceptance', 'independent_unit_identity_bootstrap']), sort_keys=True))


if __name__ == '__main__':
    main()
