#!/usr/bin/env python3
"""Separately authorized whole-NVMe image writer; default is read-only.

Never updates SPI, repartitions, grows filesystems, reboots, or clones identities.
Power/suspend and vendor boot readiness are explicit operator acknowledgements,
not facts inferred by this script. Retained intent forbids automatic retry.
"""

import argparse
import fcntl
import hashlib
import json
import lzma
import os
from pathlib import Path
import stat
import struct
import types


STATE = Path('/var/lib/sage-lantern-nvme-write')
BLKGETSIZE64 = 0x80081272
BLKFLSBUF = 0x1261


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute_path_required')
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe_parent')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 1048576, 'unsafe_file')
        raw = os.read(descriptor, 1048577)
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_size', 'st_nlink', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
            for after in (os.fstat(descriptor), path.lstat()) for key in fields), 'file_changed')
        return raw
    finally:
        os.close(descriptor)


def load_helper(path, checksum):
    raw = trusted(path)
    require(hashlib.sha256(raw).hexdigest() == checksum, 'helper_hash_mismatch')
    module = types.ModuleType('reviewed_nvme_preflight')
    module.__file__ = str(path)
    exec(compile(raw, str(path), 'exec'), module.__dict__)
    return module


def authorize(args, expected):
    require(args.confirm_erase == expected['target_serial'], 'exact_serial_erase_authorization_required')
    require(args.boot_reviewed is True and args.power_and_suspend_ready is True, 'operator_prerequisites_not_acknowledged')


class Journal:
    def __init__(self, expected, checksum):
        for parent in STATE.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022, 'unsafe_state_parent')
        if not os.path.lexists(STATE):
            STATE.mkdir(mode=0o700)
            sync_directory(STATE.parent)
        info = STATE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700, 'unsafe_state')
        name = hashlib.sha256(expected['target_serial'].encode()).hexdigest() + '.jsonl'
        self.descriptor = os.open(STATE / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.append('intent', dict(expected_sha256=checksum, target=expected['target'], serial=expected['target_serial']))
        sync_directory(STATE)

    def append(self, phase, details):
        with os.fdopen(os.dup(self.descriptor), 'ab') as stream:
            stream.write((json.dumps(dict(phase=phase, details=details), sort_keys=True) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())

    def close(self):
        os.close(self.descriptor)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_image(image_fd, target_fd, expected):
    os.lseek(image_fd, 0, os.SEEK_SET)
    os.lseek(target_fd, 0, os.SEEK_SET)
    total = 0
    digest = hashlib.sha256()
    with os.fdopen(os.dup(image_fd), 'rb') as stream, lzma.LZMAFile(stream) as decoded:
        while chunk := decoded.read(1048576):
            total += len(chunk)
            require(total <= expected['image_bytes'], 'image_grew_during_write')
            digest.update(chunk)
            remaining = memoryview(chunk)
            while remaining:
                written = os.write(target_fd, remaining)
                require(written > 0, 'short_write')
                remaining = remaining[written:]
    require(total == expected['image_bytes'] and digest.hexdigest() == expected['image_sha256'], 'written_source_mismatch')
    os.fsync(target_fd)


def readback(target_fd, expected):
    os.lseek(target_fd, 0, os.SEEK_SET)
    digest = hashlib.sha256()
    remaining = expected['image_bytes']
    while remaining:
        chunk = os.read(target_fd, min(1048576, remaining))
        require(bool(chunk), 'short_readback')
        digest.update(chunk)
        remaining -= len(chunk)
    require(digest.hexdigest() == expected['image_sha256'], 'readback_mismatch')
    return digest.hexdigest()


def apply(helper, image_path, expected, baseline, journal):
    image_fd = os.open(image_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    target_fd = None
    try:
        image_before = os.fstat(image_fd)
        helper.verify_image(image_path, expected)
        require(helper.identity(image_before) == helper.identity(Path(image_path).lstat()), 'image_replaced')
        current = helper.snapshot(expected)
        helper.validate_snapshot(expected, current)
        require(current == baseline, 'device_changed_before_claim')
        target_fd = os.open(expected['target'], os.O_RDWR | os.O_EXCL | os.O_NOFOLLOW)
        target = os.fstat(target_fd)
        require(stat.S_ISBLK(target.st_mode) and target.st_rdev == Path(expected['target']).lstat().st_rdev
                and f'{os.major(target.st_rdev)}:{os.minor(target.st_rdev)}' == baseline['device_number'], 'claimed_device_mismatch')
        size = struct.unpack('Q', fcntl.ioctl(target_fd, BLKGETSIZE64, bytes(8)))[0]
        require(size == expected['target_bytes'], 'claimed_capacity_mismatch')
        current = helper.snapshot(expected)
        helper.validate_snapshot(expected, current)
        require(current == baseline, 'device_changed_after_claim')
        journal.append('write_authorized', dict(image_bytes=expected['image_bytes'], exclusive_claim=True))
        write_image(image_fd, target_fd, expected)
        require(helper.identity(image_before) == helper.identity(os.fstat(image_fd))
                == helper.identity(Path(image_path).lstat()), 'image_changed_during_write')
        fcntl.ioctl(target_fd, BLKFLSBUF)
        checksum = readback(target_fd, expected)
        journal.append('readback_verified', dict(sha256=checksum, bytes=expected['image_bytes'], boot_verified=False))
        return checksum
    except BaseException:
        try:
            journal.append('outcome_unknown', dict(possible_partial_write=True, automatic_retry=False))
        except BaseException:
            pass
        raise
    finally:
        if target_fd is not None:
            os.close(target_fd)
        os.close(image_fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--preflight-helper', type=Path, required=True)
    parser.add_argument('--preflight-sha256', required=True)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--image', type=Path, required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--confirm-erase')
    parser.add_argument('--boot-reviewed', action='store_true')
    parser.add_argument('--power-and-suspend-ready', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0, 'root_required')
    helper = load_helper(args.preflight_helper, args.preflight_sha256)
    raw = trusted(args.expected)
    require(hashlib.sha256(raw).hexdigest() == args.expected_sha256, 'expected_hash_mismatch')
    expected = helper.decode(raw)
    helper.validate_expected(expected)
    baseline = helper.snapshot(expected)
    helper.validate_snapshot(expected, baseline)
    if not args.apply:
        verified = helper.verify_image(args.image, expected)
        require(helper.snapshot(expected) == baseline, 'device_changed_during_preflight')
        print(json.dumps(dict(dry_run=True, image=verified, write_authorized=False)))
        return
    authorize(args, expected)
    journal = Journal(expected, args.expected_sha256)
    try:
        checksum = apply(helper, args.image, expected, baseline, journal)
        print(json.dumps(dict(readback_verified=True, sha256=checksum, firmware_changed=False, boot_verified=False)))
    finally:
        journal.close()


if __name__ == '__main__':
    main()
