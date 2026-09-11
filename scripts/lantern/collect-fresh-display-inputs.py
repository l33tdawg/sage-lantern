#!/usr/bin/env python3
"""Read-only fresh display input collection against an explicitly reviewed inventory."""

import argparse
import hashlib
import os
from pathlib import Path
import signal
import stat
import sys
import types
import uuid


ACTIVATOR_SHA256 = '81142b2354e07c61962d291bd79e3d81571451d8749fd41e8d1fb4d71c3aa542'


def require(value):
    if value is not True:
        raise ValueError('display_collection_refused')


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    def identity(info):
        return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
            'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 16777216)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(16777217)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
            == identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def load_activator(path):
    raw = trusted(path)
    require(hashlib.sha256(raw).hexdigest() == ACTIVATOR_SHA256)
    module = types.ModuleType('reviewed_fresh_display_activation')
    module.__file__ = str(Path(path))
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return module


def collect(helper, inventory, attempt_id):
    require(type(inventory) is dict and set(inventory) == {'schema', 'unit_id', 'files', 'auth_dropins'}
        and inventory['schema'] == 'sage.lantern.display-reviewed-inventory.v1')
    require(inventory['unit_id'] in ('unit-a', 'unit-b'))
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    policy = {'state': 'absent'}
    helper.parents(helper.POLICY)
    if os.path.lexists(helper.POLICY):
        policy = {'state': 'present', 'sha256': helper.sha(helper.read(helper.POLICY))}
    expected = dict(schema='sage.lantern.fresh-display.v1', attempt_id=attempt_id,
        unit_id=inventory['unit_id'], boot_id=boot, files=inventory['files'],
        auth_dropins=inventory['auth_dropins'], policy=policy, session={}, auth_process={})
    backend = helper.Native(expected)
    backend.policy()
    binding = helper.session_public(backend.native.check_x11(
        backend.session.binding_for('fresh', expected['unit_id'])))
    expected['session'] = binding
    expected['auth_process'] = backend.process(helper.AUTH)
    backend.check()
    return dict(schema='sage.lantern.display-collected-inputs.v1', expected=expected,
        expected_sha256=helper.sha(helper.encoded(expected)), nonblanking_preflight=True,
        activated=False, boot_enabled=False, physical_verified=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--activator', required=True)
    parser.add_argument('--inventory', required=True)
    parser.add_argument('--inventory-sha256', required=True)
    parser.add_argument('--attempt-id', required=True)
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux')
    require(str(uuid.UUID(args.attempt_id)) == args.attempt_id and uuid.UUID(args.attempt_id).int > 0)
    def interrupted(signum, frame):
        raise InterruptedError('display_collection_cancelled')
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(number, interrupted)
    signal.alarm(90)
    try:
        helper = load_activator(args.activator)
        raw = helper.pin({'path': args.inventory, 'sha256': args.inventory_sha256})
        result = collect(helper, helper.decode(raw), args.attempt_id)
        require(helper.read(args.inventory) == raw)
        require(hashlib.sha256(trusted(args.activator)).hexdigest() == ACTIVATOR_SHA256)
        print(helper.encoded(result).decode())
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
