#!/usr/bin/env python3
"""Build and stage the C-F console lane: the served app plus the question loop.

The kiosk console on C-F is the built Vinext bundle at
`/opt/sage-lantern/fleet-admin` (dist plus the installed dependencies), not a
checkout. A UI fix therefore needs the same three steps the release installer
performs, in the same order: build the app from the reviewed source tree, freeze
the result as a staged bundle with per-file digests, and install it with
`deploy-staged-console-bundle-local.py` anchored on the unit's installed
release. This script performs the first two and never touches a unit.

The bundle carries the app UI files the canonical installer treats as the
runnable UI set (`dist/`, `public/`, `package.json`, `package-lock.json`), the
installed question loop (`scripts/lantern/unit-a-voice-loop.py` ->
`/usr/local/libexec/sage-lantern-voice-loop`) and, unless `--no-question-gate`,
the reviewed local question-lane drop-in that lets the question service run on
a unit with no radio and no working audio. Dependencies are deliberately NOT
staged: a change to `package.json`/`package-lock.json` is refused by the
deployer's dependency gate and belongs to a release cut.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'apps/lantern-fleet-admin'
APP_DESTINATION = '/opt/sage-lantern/fleet-admin'
VOICE_SOURCE = ROOT / 'scripts/lantern/unit-a-voice-loop.py'
VOICE_DESTINATION = '/usr/local/libexec/sage-lantern-voice-loop'
QUESTION_GATE = '/etc/systemd/system/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
# Reviewed local question-lane gate: a unit with no radio and no working capture
# path still runs the question service. The empty assignments reset the mesh
# start condition and the audio start condition; capture stays per-session and a
# failed capture is reported to the user instead of blocking the device. The
# reviewed bytes live once, in the release tree, and are staged from there.
QUESTION_GATE_SOURCE = ROOT / 'software/systemd/sage-lantern-voice-loop.service.d/60-local-question-lane.conf'
APP_INPUTS = ('app', 'lib', 'public', 'package.json', 'package-lock.json', 'next.config.ts', 'vite.config.ts')
MAX_FILES = 4096
MAX_FILE = 16 * 1024 * 1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path, *, limit=MAX_FILE):
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        data = bytearray()
        while True:
            chunk = os.read(descriptor, limit + 1 - len(data))
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > limit:
                raise ValueError('file_too_large: ' + str(path))
        return bytes(data)
    finally:
        os.close(descriptor)


def require(value, reason):
    if not value:
        raise SystemExit(reason)


def source_files():
    """The reviewed source the bundle is built from, in a stable order."""
    files = []
    for name in APP_INPUTS:
        path = APP / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(item for item in path.rglob('*') if item.is_file()))
        else:
            raise SystemExit('missing app input: ' + name)
    return files


def source_inventory():
    return {str(path.relative_to(APP)): digest(read_regular(path)) for path in source_files()}


def app_payload():
    """The runnable UI set the canonical installer accepts, minus node_modules."""
    entries = []
    for name in ('dist', 'public'):
        directory = APP / name
        require(directory.is_dir(), 'app_build_missing: ' + name)
        entries.extend(sorted(path for path in directory.rglob('*') if path.is_file()))
    for name in ('package.json', 'package-lock.json'):
        require((APP / name).is_file(), 'app_build_missing: ' + name)
        entries.append(APP / name)
    return entries


def stage_app(entries, payload):
    records = []
    for path in entries:
        relative = path.relative_to(APP)
        data = read_regular(path)
        index = len(records)
        (payload / f'{index:03d}').write_bytes(data)
        records.append(dict(artifact=f'payload/{index:03d}',
                            destination=f'{APP_DESTINATION}/{relative.as_posix()}',
                            release_path=f'apps/lantern-fleet-admin/{relative.as_posix()}',
                            mode=0o644, size=len(data), sha256=digest(data)))
    return records


def stage_file(source, destination, payload, records, mode, *, release_path=None):
    data = read_regular(source)
    index = len(records)
    (payload / f'{index:03d}').write_bytes(data)
    records.append(dict(artifact=f'payload/{index:03d}', destination=destination,
                        release_path=release_path, mode=mode, size=len(data), sha256=digest(data)))


def build(environment):
    """One build from the reviewed tree; the vendored toolchain is installed."""
    npm = shutil.which('npm')
    require(npm is not None, 'npm_unavailable')
    result = subprocess.run([npm, 'run', 'build'], cwd=APP, env=environment,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=900)
    if result.returncode != 0:
        sys.stderr.write(result.stdout[-4000:])
        raise SystemExit('app_build_failed')


REQUIRED_ANCHOR_PATHS = (
    'apps/lantern-fleet-admin/dist/server/index.js',
    'apps/lantern-fleet-admin/package.json',
    'apps/lantern-fleet-admin/package-lock.json',
    'scripts/lantern/unit-a-voice-loop.py',
)


def build_anchor(payload_root, anchor, records):
    """Copy the release payload's copy of every destination into one tree.

    The deployer verifies each replaced destination against this tree (D-017).
    A destination the payload does not carry is recorded as absent: a freshly
    built asset is new by definition, while a destination that both exists on
    the unit and has no anchor is still refused by the deployer by name. The
    stable destinations must be there, so a wrong `--anchor-from` cannot mint an
    empty anchor that would refuse everything.
    """
    copied = {}
    absent = []
    for item in records:
        release_path = item.get('release_path')
        if release_path is None:
            continue
        source = payload_root / release_path
        if not source.is_file():
            absent.append(release_path)
            continue
        target = anchor / release_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(read_regular(source))
        copied[release_path] = digest(target.read_bytes())
    missing = [name for name in REQUIRED_ANCHOR_PATHS if name not in copied]
    if missing:
        raise SystemExit('anchor_source_missing: ' + missing[0])
    return copied, sorted(absent)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', type=Path, required=True,
                        help='directory for the staged bundle (refused when it exists)')
    parser.add_argument('--no-build', action='store_true', help='stage the existing dist/ unchanged')
    parser.add_argument('--no-question-gate', action='store_true',
                        help='leave the installed voice-loop start conditions untouched')
    parser.add_argument('--bundle-id', help='recorded bundle identity (default: console-<UTC stamp>)')
    parser.add_argument('--anchor-from', type=Path,
                        help='release payload tree to freeze the D-017 anchor from (staged as <stage>/anchor)')
    parser.add_argument('--anchor-out', type=Path,
                        help='anchor directory (default: the stage/<anchor> directory itself)')
    args = parser.parse_args(argv)
    start = time.monotonic()
    stage = args.stage.resolve()
    require(not stage.exists(), 'stage_exists')
    inventory = source_inventory()
    if not args.no_build:
        environment = dict(os.environ)
        environment.pop('LANTERN_DEVICE_TOKEN', None)
        build(environment)
    entries = app_payload()
    require(len(entries) + 2 <= MAX_FILES, 'bundle_file_limit')
    stamp = time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())
    bundle_id = args.bundle_id or f'console-{stamp}'
    stage.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix='.console-bundle.', dir=stage.parent))
    try:
        payload = temporary / 'payload'
        payload.mkdir(mode=0o755)
        records = stage_app(entries, payload)
        stage_file(VOICE_SOURCE, VOICE_DESTINATION, payload, records, 0o755,
                   release_path='scripts/lantern/unit-a-voice-loop.py')
        activation = {'local_question_lane': not args.no_question_gate}
        if not args.no_question_gate:
            require(QUESTION_GATE_SOURCE.is_file(), 'question_gate_source_missing')
            index = len(records)
            data = read_regular(QUESTION_GATE_SOURCE)
            (payload / f'{index:03d}').write_bytes(data)
            records.append(dict(artifact=f'payload/{index:03d}', destination=QUESTION_GATE,
                                release_path=None, mode=0o644, size=len(data), sha256=digest(data)))
        build_id = read_regular(APP / 'dist/server/BUILD_ID').decode().strip()
        anchor = None
        if args.anchor_from is not None:
            anchor_root = Path(args.anchor_out) if args.anchor_out is not None else temporary / 'anchor'
            anchor_root.mkdir(mode=0o755, parents=True, exist_ok=True)
            anchor_files, anchor_absent = build_anchor(Path(args.anchor_from), anchor_root, records)
            anchor = dict(payload=str(args.anchor_from), files=len(anchor_files),
                          absent=len(anchor_absent), absent_paths=anchor_absent,
                          sha256=digest(json.dumps(anchor_files, sort_keys=True).encode()))
        manifest = dict(schema='sage.lantern.console-bundle.v1', bundle_id=bundle_id, created_at=stamp,
                        mode='stage-only', activation=activation, app_build_id=build_id,
                        source_sha256=digest(json.dumps(inventory, sort_keys=True).encode()),
                        source_files=len(inventory), files=records,
                        anchor=anchor)
        (temporary / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True, indent=2) + '\n')
        for entry in records:
            require(digest(read_regular(temporary / entry['artifact'])) == entry['sha256'],
                    'staged_payload_changed')
        os.replace(temporary, stage)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary, ignore_errors=True)
    print(json.dumps(dict(staged=True, bundle_id=bundle_id, stage=str(stage),
                          files=len(records), bytes=sum(entry['size'] for entry in records),
                          app_build_id=build_id, question_gate=not args.no_question_gate,
                          seconds=round(time.monotonic() - start, 2)), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
