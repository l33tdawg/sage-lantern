#!/usr/bin/env python3
"""Stage a pinned B policy correction for review; never install or restart.

The baseline contains local configuration and remains private. Native rewrites
must preserve explicit transport policy before this candidate can be admitted.
No identity, receipt, encryption, quota or embedding setting is regenerated.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys

import yaml


SPEC = importlib.util.spec_from_file_location('private_policy_inspector',
    Path(__file__).with_name('inspect-fresh-sage-policy.py'))
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)
CURRENT = 'dc1c2a230e427401bc0ba5714588e4f1725b6b4a49d3cef288f047133d0eea9b'
INITIALIZED = 'bb59112deef341ae869433a565acd7ec0815a081369bb61e684b315051897214'
CHAIN = 'sage-personal-twcvr4higeb6u3khrhzc2mz734'
ROOT = '4b3e13dc7a2f115b01566a3d8a1e92a6813dcc7437f8d2b3b4ae5005658acc8f'


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def directory(path, owners):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts, 'absolute_directory_required')
    descriptor = os.open('/', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name in path.parts[1:]:
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            require(info.st_uid in owners and not info.st_mode & 0o022, 'untrusted_directory_owner')
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def corrected(raw, initialized, current_hash=CURRENT):
    require(current_hash == CURRENT and digest(raw) == current_hash, 'current_policy_pin_mismatch')
    require(digest(initialized) == INITIALIZED, 'initialized_pin_mismatch')
    receipt = json.loads(initialized)
    require(receipt.get('schema') == 'sage.lantern.initialized.v1'
        and receipt.get('unit_id') == 'unit-b' and receipt.get('root_id') == ROOT
        and receipt.get('chain_id') == CHAIN, 'independent_b_receipt_required')
    before = inspector.parse(raw)
    require(before.get('chain_id') == CHAIN, 'independent_b_chain_required')
    require(before.get('encryption', {}).get('enabled') is True
        and before.get('private_media', {}).get('enabled') is True, 'encrypted_private_storage_required')
    require(before.get('embedding', {}).get('provider') == 'hash'
        and before['embedding'].get('dimension') == 768, 'embedding_baseline_changed')
    require(before.get('federation', {}).get('enabled') is False
        and before.get('quorum', {}).get('enabled') is False
        and before.get('voter', {}).get('enabled') is True, 'baseline_policy_changed')
    after = inspector.parse(raw)
    after['federation'].update(p2p_enabled=False, p2p_relay_addrs=[], p2p_force_private=False)
    after['voter']['required'] = True
    inspector.public_policy(after)
    candidate = yaml.safe_dump(after, sort_keys=False).encode()
    require(inspector.parse(candidate) == after, 'candidate_roundtrip_failed')
    return candidate


def read_private(path):
    path = Path(path).absolute()
    require(str(path) in ('/var/lib/sage-lantern/sage-personal/config.yaml',
        '/etc/sage-lantern/initialized-public.json'), 'fixed_b_input_required')
    owner = 994 if path.name == 'config.yaml' else 0
    parent = directory(path.parent, {0, owner})
    try:
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    except BaseException:
        os.close(parent)
        raise
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_uid == owner
            and stat.S_IMODE(before.st_mode) == 0o600 and 0 < before.st_size <= 65536,
            'unsafe_input_file')
        raw = os.read(descriptor, 65537)
        require(len(raw) == before.st_size
            and inspector.identity(before) == inspector.identity(os.fstat(descriptor))
            and inspector.identity(before) == inspector.identity(os.stat(path.name, dir_fd=parent, follow_symlinks=False)), 'input_changed')
        return raw
    finally:
        os.close(descriptor)
        os.close(parent)


def stage(output, baseline, candidate):
    output = Path(output).absolute()
    parent = directory(output.parent, {0})
    try:
        os.mkdir(output.name, mode=0o700, dir_fd=parent)
        os.fsync(parent)
        destination = os.open(output.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    finally:
        os.close(parent)
    report = dict(schema='sage.lantern.b-private-policy-candidate.v1', unit_id='unit-b',
        baseline_sha256=digest(baseline), candidate_sha256=digest(candidate),
        initialized_sha256=INITIALIZED, installed=False, restart_requested=False,
        native_roundtrip_patch_required=True, owner_unlock_required_after_restart=True)
    try:
        for name, raw in {'before.yaml': baseline, 'candidate.yaml': candidate,
            'review.json': (json.dumps(report, sort_keys=True) + '\n').encode()}.items():
            descriptor = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=destination)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        os.fsync(destination)
    finally:
        os.close(destination)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--current-policy', required=True, type=Path)
    parser.add_argument('--initialized-public', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux', 'root_linux_staging_only')
    baseline = read_private(args.current_policy)
    initialized = read_private(args.initialized_public)
    candidate = corrected(baseline, initialized)
    require(read_private(args.current_policy) == baseline
        and read_private(args.initialized_public) == initialized, 'inputs_changed')
    print(json.dumps(stage(args.output, baseline, candidate), sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B policy preparation refused; preserve any partial staging directory.') from None
