#!/usr/bin/env python3
"""Package dist/client as the release the Natter host activates.

    python3 scripts/build-release.py [--release YYYYMMDD-<12 hex>]

Writes /private/tmp/sage-website-release.tar.gz and refreshes
deploy/release-manifest.json, both of which are uploaded to the host's /tmp and
verified there by deploy/unpack-release.py before install-release.py switches the
live symlink. The release name defaults to today's date plus the current commit
prefix, so a release always names the tree it was built from.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import tarfile


ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist' / 'client'
TARBALL = Path('/private/tmp/sage-website-release.tar.gz')
MANIFEST = ROOT / 'deploy' / 'release-manifest.json'


def sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def release_name(explicit: str | None) -> str:
    if explicit:
        if not re.fullmatch(r'\d{8}-[0-9a-f]{12}', explicit):
            raise SystemExit('release must look like YYYYMMDD-<12 hex>')
        return explicit
    prefix = subprocess.run(['git', 'rev-parse', '--short=12', 'HEAD'], cwd=ROOT,
                            check=True, capture_output=True, text=True).stdout.strip()
    return f"{dt.datetime.now(dt.UTC):%Y%m%d}-{prefix}"


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    parser.add_argument('--release', default=None)
    args = parser.parse_args()
    if not (DIST / 'index.html').is_file():
        raise SystemExit(f'{DIST} has no index.html; run npm run build first')
    release = release_name(args.release)
    files = sorted(path for path in DIST.rglob('*') if path.is_file())
    if not files:
        raise SystemExit('the export is empty')

    manifest_files = {}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for path in files:
            name = path.relative_to(DIST).as_posix()
            payload = path.read_bytes()
            manifest_files[name] = sha256(payload)
            info = tarfile.TarInfo(name)
            info.size, info.mtime, info.mode = len(payload), 0, 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            archive.addfile(info, io.BytesIO(payload))
    raw = gzip.compress(buffer.getvalue(), compresslevel=9, mtime=0)
    TARBALL.write_bytes(raw)
    document = {'release': release, 'files': manifest_files}
    MANIFEST.write_text(json.dumps(document, indent=2, sort_keys=True) + '\n', 'utf-8')
    print(json.dumps({'release': release, 'files': len(manifest_files),
                      'tar_bytes': len(raw), 'tar_sha256': sha256(raw),
                      'tarball': str(TARBALL), 'manifest': str(MANIFEST)}, sort_keys=True))


if __name__ == '__main__':
    main()
