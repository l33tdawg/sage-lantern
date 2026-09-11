#!/usr/bin/env python3
"""Verify exact canonical/sidecar trees immediately before and after packaging.

Reject contamination; never filter caches or relax the manifest allowlist.
Failed archives are retained but no success hash is emitted. Use new output.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tarfile

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def exact_files(root, expected):
    observed = set()
    for path in root.rglob('*'):
        info = path.lstat()
        require(not path.is_symlink(), 'linked_package_entry')
        if stat.S_ISREG(info.st_mode):
            require(info.st_nlink == 1, 'hardlinked_package_entry')
            observed.add(path.relative_to(root).as_posix())
        else:
            require(stat.S_ISDIR(info.st_mode), 'nonregular_package_entry')
            relative = path.relative_to(root).as_posix()
            require(any(name.startswith(relative + '/') for name in expected), 'extra_package_directory')
    require(observed == set(expected), 'extra_or_missing_package_files')


def package(work, output, checksum):
    spec = importlib.util.spec_from_file_location('package_manifest', ROOT / 'scripts/appliance-release-manifest.py')
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    canonical = work / 'canonical'
    manifest_path = canonical / 'manifest.json'
    raw = verifier.metadata_bytes(manifest_path)[0]
    require(hashlib.sha256(raw).hexdigest() == checksum, 'manifest_hash_mismatch')
    manifest = verifier.document(raw)
    payload_names = {row['path'] for row in manifest['files']}
    sidecar = work / 'startup-sidecar'
    sidecar_raw = verifier.metadata_bytes(sidecar / 'manifest.json')[0]
    sidecar_manifest = verifier.document(sidecar_raw)
    require(sidecar_manifest['canonical_manifest_sha256'] == checksum, 'sidecar_release_mismatch')
    candidate_raw = verifier.metadata_bytes(work / 'candidate.json')[0]
    require(verifier.document(candidate_raw)['manifest_sha256'] == checksum, 'candidate_release_mismatch')
    def verify():
        exact_files(canonical / 'payload', payload_names)
        exact_files(canonical, {'manifest.json', 'selection.json'} | {'payload/' + name for name in payload_names})
        verifier.verify(canonical / 'payload', manifest_path)
        exact_files(sidecar, {'manifest.json', *sidecar_manifest['files']})
        for name, digest in sidecar_manifest['files'].items():
            verifier.safe_name(name)
            require(verifier.file_hash(sidecar / name)[0]['sha256'] == digest, 'sidecar_file_changed')
        require(verifier.metadata_bytes(manifest_path)[0] == raw
                and verifier.metadata_bytes(sidecar / 'manifest.json')[0] == sidecar_raw
                and verifier.metadata_bytes(work / 'candidate.json')[0] == candidate_raw, 'metadata_changed')
    verify()
    with output.open('xb') as stream:
        with tarfile.open(fileobj=stream, mode='w') as archive:
            for name in ('canonical', 'startup-sidecar', 'candidate.json'):
                archive.add(work / name, arcname=name, recursive=True)
    verify()
    with output.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return dict(archive=str(output), archive_sha256=digest, manifest_sha256=checksum, installed=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest-sha256', required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.work, args.output, args.manifest_sha256), sort_keys=True))


if __name__ == '__main__':
    main()
