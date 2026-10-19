#!/usr/bin/env python3
"""Exclusive static startup installation; no service mutation or lifetime creation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import uuid

from importlib.util import module_from_spec, spec_from_file_location


ROOT = Path(__file__).resolve().parents[2]
DEST = Path('/opt/sage-lantern/libexec')
STATE = Path('/var/lib/sage-lantern-startup-helpers')
ETC = Path('/etc/sage-lantern')
NAMES = ('startup-coordinator.py', 'startup-audio-adapter.py', 'recover-cross-boot-audio.py',
         'collect-cross-boot-audio-pins.py', 'activate-fresh-display.py')


def load(name):
    spec = spec_from_file_location(name.replace('-', '_'), ROOT / 'scripts/lantern' / (name + '.py'))
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load('provision-postcommission-startup')
require = contract.require
trusted = contract.trusted


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    from sage_lantern.suspend_io import unique_object
    return json.loads(raw, object_pairs_hook=unique_object)


def encode(value):
    return (json.dumps(value, sort_keys=True) + '\n').encode()


def safe_directory(path):
    for parent in (path, *path.parents):
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def inactive():
    from sage_lantern.suspend_system import SuspendSystem
    contract.startup_inactive(SuspendSystem(), installed=False)
    for path in ('/etc/systemd/system/sage-lantern-startup.service',
                 '/etc/systemd/system/multi-user.target.wants/sage-lantern-startup.service',
                 '/etc/sage-lantern/startup.json'):
        require(not os.path.lexists(path))


def manifest_bytes(path):
    safe_directory(path.parent)
    before = path.lstat()
    require(before.st_uid == 0 and before.st_gid == 0)
    runner = load('provision-fresh-appliance')
    verifier = runner.helper('static_manifest_reader', 'scripts/appliance-release-manifest.py')
    raw, observed = verifier.metadata_bytes(path)
    after = path.lstat()
    require(verifier.identity(before) == observed == verifier.identity(after)
        and after.st_uid == 0 and after.st_gid == 0)
    return raw


def inspect(args, installed=(), verified_payload=None):
    payload = verified_payload
    if payload is None:
        payload = load('provision-fresh-appliance').verify_release(args)
    manifest_raw = manifest_bytes(payload.parent / 'manifest.json')
    require(digest(manifest_raw) == args.manifest_sha256)
    manifest = decode(manifest_raw)
    entries = {row['path']: row for row in manifest['files']}
    destinations_raw = manifest_bytes(payload / 'release/destinations.json')
    destinations = decode(destinations_raw)
    rows = {row['path']: row for row in destinations['files']}
    require(digest(destinations_raw) ==
            entries['release/destinations.json']['sha256'])
    metadata = trusted(ETC / 'unit.env')
    require(sorted(metadata.decode().splitlines()) == sorted(
        [f'UNIT_ID={args.unit_id}', 'PROVISIONING_SCHEMA=sage-lantern-unit-v1']))
    receipt_raw = trusted(ETC / 'appliance-installed.json')
    binding_raw = trusted(ETC / 'appliance-bindings.json')
    receipt, binding = decode(receipt_raw), decode(binding_raw)
    require(receipt['schema'] == 'sage.lantern.fresh-app-install.v1'
        and receipt['unit_id'] == binding['unit_id'] == args.unit_id
        and receipt['release_id'] == manifest['release_id']
        and receipt['installed'] is True and receipt['lock_required'] is True
        and binding['schema'] == 'sage.lantern.unit-plan.v1')
    inactive()
    safe_directory(DEST if DEST.exists() else DEST.parent)
    require(not DEST.is_symlink())
    files = {}
    for name in NAMES:
        source = 'scripts/lantern/' + name
        target = DEST / name
        row = rows[source]
        require(row['destination'] == str(target) and row['operation'] == 'reviewed_copy'
            and row['destination_mode'] == 0o644)
        raw = trusted(payload / source)
        require(digest(raw) == entries[source]['sha256'])
        if name in installed:
            require(trusted(target) == raw and stat.S_IMODE(target.stat().st_mode) == 0o644)
        else:
            require(not os.path.lexists(target))
        files[source] = dict(destination=str(target), sha256=digest(raw), mode=0o644)
    inactive()
    return dict(schema='sage.lantern.fresh-startup-helpers-plan.v1', unit_id=args.unit_id,
        manifest_sha256=args.manifest_sha256, selection_sha256=digest(manifest_bytes(payload.parent / 'selection.json')),
        unit_sha256=digest(metadata), installed_receipt_sha256=digest(receipt_raw),
        installed_binding_sha256=digest(binding_raw), files=files), payload


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def exclusive(path, raw, mode=0o600):
    safe_directory(path.parent)
    parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        descriptor = os.open(path.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode, dir_fd=parent)
        with os.fdopen(descriptor, 'wb') as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(parent)
    finally:
        os.close(parent)


def apply(args, expected):
    plan, payload = inspect(args)
    require(plan == expected and str(uuid.UUID(args.attempt_id)) == args.attempt_id
        and uuid.UUID(args.attempt_id).int > 0)
    require(not os.path.lexists(STATE))
    safe_directory(STATE.parent)
    STATE.mkdir(mode=0o700)
    sync_directory(STATE.parent)
    exclusive(STATE / 'intent.json', encode(dict(attempt_id=args.attempt_id, plan=plan)))
    installed = []
    try:
        require(inspect(args, verified_payload=payload)[0] == plan)
        if not DEST.exists():
            DEST.mkdir(mode=0o755)
            sync_directory(DEST.parent)
        for name in NAMES:
            require(inspect(args, installed, payload)[0] == plan)
            source = 'scripts/lantern/' + name
            raw = trusted(payload / source)
            require(digest(raw) == plan['files'][source]['sha256'])
            exclusive(DEST / name, raw, 0o644)
            installed.append(name)
        require(inspect(args, installed)[0] == plan)
        result = dict(plan=plan, installed=True, services_started=False, services_enabled=False,
                      commissioning_created=False)
        exclusive(STATE / 'complete.json', encode(result))
        return result
    except BaseException:
        exclusive(STATE / 'failed.json', encode(dict(partial=True, retry_authorized=False,
            installed_names=installed)))
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'), required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--expected')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--attempt-id')
    args = parser.parse_args()
    require(os.geteuid() == 0)
    require(all(bool(value) == args.apply for value in (args.expected, args.expected_sha256, args.attempt_id)))
    def interrupted(signum, frame):
        raise RuntimeError('static_install_interrupted')
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    if args.apply:
        raw = trusted(args.expected)
        require(digest(raw) == args.expected_sha256)
        result = apply(args, decode(raw))
    else:
        result = inspect(args)[0]
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
