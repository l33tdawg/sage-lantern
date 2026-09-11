#!/usr/bin/env python3
"""Offline, exclusive maintenance venv assembly; no appliance/service changes.

Inputs pin the system interpreter and every wheel. Default only inspects.
Apply creates a new root-only staging directory, never overwrites or retries an
attempt, installs exact local wheels with no dependency resolution, checks pip
dependencies, and seals the venv read-only. No installed venv, identity, device
configuration, service, capture or network operation is used as a build input.
Failure preserves the incomplete directory; only complete.json admits use.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import uuid


ROOT = Path('/var/lib/sage-lantern-maintenance-envs')


def require(value):
    if not value:
        raise ValueError('maintenance_environment_refused')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def unique(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def signature(info):
    return tuple(getattr(info, field) for field in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= 128 * 1024**2)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(128 * 1024**2 + 1)
        require(len(raw) == info.st_size and signature(info) == signature(os.fstat(descriptor))
                == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'}
            and re.fullmatch('[0-9a-f]{64}', pin['sha256']))
    raw = trusted(pin['path'])
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'])
    return raw


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


def inspect(expected):
    require(type(expected) is dict and set(expected) == {'schema', 'attempt', 'python', 'wheels'}
            and expected['schema'] == 'sage.lantern.maintenance-environment.v1'
            and str(uuid.UUID(expected['attempt'])) == expected['attempt']
            and expected['python']['path'] == '/usr/bin/python3.11'
            and type(expected['wheels']) is list and 2 <= len(expected['wheels']) <= 64)
    pinned(expected['python'])
    names = set()
    for pin in expected['wheels']:
        name = Path(pin['path']).name
        require(re.fullmatch('[A-Za-z0-9_.+-]+\\.whl', name) and name not in names)
        names.add(name)
        pinned(pin)
    require(any(name.startswith('sage_lantern_runtime-') for name in names)
            and any(name.startswith('sage_agent_sdk-') for name in names))
    root = ROOT / expected['attempt']
    require(not os.path.lexists(root))
    return root


def inventory(root, *, seal=False):
    root_info = root.lstat()
    require(stat.S_ISDIR(root_info.st_mode) and root_info.st_uid == 0)
    pending = [root]
    directories = []
    result = {}
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(len(result) < 40000 and info.st_dev == root_info.st_dev and info.st_uid == 0)
        relative = str(path.relative_to(root))
        if stat.S_ISDIR(info.st_mode):
            require(not path.is_mount())
            directories.append(path)
            pending.extend(path.iterdir())
            result[relative] = dict(kind='directory')
        elif stat.S_ISLNK(info.st_mode):
            target = os.readlink(path)
            require(relative == 'lib64' and target == 'lib'
                    and stat.S_ISDIR((root / 'lib').lstat().st_mode))
            result[relative] = dict(kind='link', target=target)
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1)
            if seal:
                os.chmod(path, 0o555 if info.st_mode & 0o111 else 0o444)
            mode = stat.S_IMODE(path.lstat().st_mode)
            require(mode in (0o444, 0o555))
            raw = trusted(path) if path.stat().st_size else b''
            result[relative] = dict(kind='file', mode=mode, size=len(raw), sha256=hashlib.sha256(raw).hexdigest())
    for directory in reversed(directories):
        if seal:
            os.chmod(directory, 0o555)
        require(stat.S_IMODE(directory.lstat().st_mode) == 0o555)
        sync(directory)
    return result


def assemble(expected):
    root = inspect(expected)
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
        sync(ROOT.parent)
    info = ROOT.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    root.mkdir(mode=0o700)
    sync(ROOT)
    write(root / 'intent.json', canonical(expected))
    venv = root / 'venv'
    environment = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C',
        PIP_CONFIG_FILE='/dev/null', PIP_DISABLE_PIP_VERSION_CHECK='1', PYTHONDONTWRITEBYTECODE='1')
    def run(arguments):
        subprocess.run(arguments, env=environment, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=120, check=True)
    run([expected['python']['path'], '-I', '-B', '-m', 'venv', '--copies', str(venv)])
    for pin in [expected['python'], *expected['wheels']]:
        pinned(pin)
    run([str(venv / 'bin/python'), '-I', '-B', '-m', 'pip', 'install', '--no-index',
         '--no-deps', '--no-cache-dir', '--no-compile', *[pin['path'] for pin in expected['wheels']]])
    run([str(venv / 'bin/python'), '-I', '-B', '-m', 'pip', 'check'])
    for pin in [expected['python'], *expected['wheels']]:
        pinned(pin)
    manifest = inventory(venv, seal=True)
    require(inventory(venv) == manifest)
    raw = canonical(manifest)
    write(root / 'inventory.json', raw)
    result = dict(schema='sage.lantern.maintenance-environment-complete.v1',
        expected_sha256=hashlib.sha256(canonical(expected)).hexdigest(),
        inventory_sha256=hashlib.sha256(raw).hexdigest(), venv=str(venv), files=len(manifest))
    write(root / 'complete.json', canonical(result))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    expected = json.loads(pinned(dict(path=args.expected, sha256=args.expected_sha256)), object_pairs_hook=unique)
    if args.apply:
        print(json.dumps(assemble(expected), sort_keys=True))
    else:
        print(json.dumps(dict(inspected=True, path=str(inspect(expected)), environment_created=False)))


if __name__ == '__main__':
    main()
