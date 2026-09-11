#!/usr/bin/env python3
"""Read-only release integrity and explicit native/runtime selection gate.

Run from the reviewed source tree. This never approves hardware, boot recovery,
identity, or a sidecar policy and never modifies a lock or release manifest.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
NATIVE = 'software/bundle/linux-arm64/bin/sage-gui'
RUNTIME = 'software/bundle/python-wheels/sage_lantern_runtime-0.2.0-py3-none-any.whl'


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def selection(manifest, native_sha256, runtime_sha256):
    for checksum in (native_sha256, runtime_sha256):
        require(type(checksum) is str and re.fullmatch('[0-9a-f]{64}', checksum) is not None, 'reviewed_sha256_required')
    files = manifest['files']
    names = [row['path'] for row in files]
    require(len(names) == len(set(names)), 'duplicate_manifest_path')
    entries = {row['path']: row for row in files}
    require(NATIVE in entries and entries[NATIVE]['sha256'] == native_sha256, 'native_selection_mismatch')
    require(RUNTIME in entries and entries[RUNTIME]['sha256'] == runtime_sha256, 'runtime_selection_mismatch')
    return dict(native_sha256=native_sha256, runtime_sha256=runtime_sha256,
        identity_copy_allowed=False, hardware_accepted=False, automatic_startup_accepted=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    parser.add_argument('--native-sha256', required=True)
    parser.add_argument('--runtime-sha256', required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location('reviewed_release_verifier', ROOT / 'scripts/appliance-release-manifest.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    path = args.output / 'manifest.json'
    raw = verifier.metadata_bytes(path)[0]
    require(hashlib.sha256(raw).hexdigest() == args.manifest_sha256, 'manifest_hash_mismatch')
    result = selection(json.loads(raw), args.native_sha256, args.runtime_sha256)
    verifier.verify(args.output / 'payload', path)
    require(verifier.metadata_bytes(path)[0] == raw, 'manifest_changed')
    print(json.dumps(dict(result, integrity_verified=True, installed=False), sort_keys=True))


if __name__ == '__main__':
    main()
