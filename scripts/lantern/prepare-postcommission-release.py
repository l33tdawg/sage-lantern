#!/usr/bin/env python3
"""Freeze new source inputs and report artifact gaps; never build or deploy."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import zipfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
GENERIC = {'scripts/lantern/handoff-fresh-audio-lifetime.py',
    'scripts/lantern/provision-postcommission-startup.py'}


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def load_stage():
    spec = importlib.util.spec_from_file_location('fresh_source_stage', ROOT/'scripts/stage-appliance-release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def runtime_gaps(wheel, sources):
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'duplicate_wheel_member')
        manifest = json.loads(archive.read('sage_lantern/release-manifest.json'))
        recorded = manifest['source_sha256']
        expected = {name.removeprefix('software/runtime/'): digest for name, digest in sources.items()
            if name.startswith('software/runtime/')}
        return sorted(name for name in set(recorded) | set(expected) if recorded.get(name) != expected.get(name))


def public_gaps(audit, sources, wheel_sha):
    require(audit['schema'] == 'sage.lantern.public-share-build-review.v1', 'public_audit_schema')
    gaps = []
    for flag in ('identical_builds', 'ui_dist_built', 'sdk_native_selected'):
        if audit.get(flag) is not True:
            gaps.append(flag)
    if audit['wheel_sha256'] != wheel_sha:
        gaps.append('public_runtime_wheel_mismatch')
    for group in ('source_sha256', 'reviewed_companions'):
        require(type(audit[group]) is dict, 'public_source_inventory')
        for name, digest in audit[group].items():
            if sources.get(name) != digest:
                gaps.append('public_source_mismatch:' + name)
    return sorted(set(gaps))


def prepare(args):
    stage = load_stage()
    verifier = stage.MANIFEST
    audit_raw = verifier.metadata_bytes(args.public_audit)[0]
    require(hashlib.sha256(audit_raw).hexdigest() == args.public_audit_sha256, 'public_audit_pin')
    audit = verifier.document(audit_raw)
    rows = {row['path']: row for row in stage.required_sources()}
    require(GENERIC <= set(rows) and all(rows[name]['operation'] == 'review_only'
        and rows[name]['destination'] is None for name in GENERIC), 'generic_release_entries_missing')
    names = set(rows) | set(stage.module_names(ROOT, 'software/runtime', 'sage_lantern', ('pyproject.toml',)))
    names |= set(audit['source_sha256']) | set(audit['reviewed_companions'])
    names |= {stage.APP + name for name in ('package.json', 'package-lock.json')}
    public_root = ROOT/stage.APP/'public'
    public_files, _ = verifier.tree(public_root)
    names |= {stage.APP + 'public/' + name for name in public_files}
    content, sources = {}, {}
    for name in sorted(names):
        verifier.safe_name(name)
        raw = verifier.metadata_bytes(ROOT/name)[0]
        require(raw is not None, 'source_file_required')
        content[name] = raw
        sources[name] = hashlib.sha256(raw).hexdigest()
    lock_raw = verifier.metadata_bytes(args.artifacts/stage.LOCK)[0]
    lock = verifier.document(lock_raw)
    version = lock['components']['lantern-runtime']['version']
    wheel = args.artifacts/stage.WHEELS/f'sage_lantern_runtime-{version}-py3-none-any.whl'
    wheel_details = verifier.file_hash(wheel)[0]
    gaps = public_gaps(audit, sources, wheel_details['sha256'])
    mismatches = runtime_gaps(wheel, sources)
    if mismatches:
        gaps.append('runtime_source_rebuild_required')
    stage_error = None
    try:
        stage.inputs(ROOT, args.artifacts)
    except (ValueError, OSError, KeyError, TypeError) as error:
        stage_error = type(error).__name__
        gaps.append('canonical_artifact_inputs_not_matched')
    require(not args.work.exists() and not args.work.is_symlink(), 'new_work_required')
    verifier.no_links(args.work)
    args.work.mkdir(mode=0o755)
    for name, raw in content.items():
        path = args.work/'source'/name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        path.chmod((ROOT/name).stat().st_mode & 0o777)
        require(verifier.metadata_bytes(ROOT/name)[0] == raw, 'source_changed')
    require(verifier.metadata_bytes(args.public_audit)[0] == audit_raw
        and verifier.metadata_bytes(args.artifacts/stage.LOCK)[0] == lock_raw
        and verifier.file_hash(wheel)[0] == wheel_details, 'artifact_changed')
    report = dict(schema='sage.lantern.postcommission-release-inputs.v1',
        public_audit_sha256=args.public_audit_sha256, source_sha256=sources,
        artifact_lock_sha256=hashlib.sha256(lock_raw).hexdigest(), runtime_wheel=wheel_details,
        runtime_source_mismatches=mismatches, blockers=sorted(gaps), stage_error_class=stage_error,
        release_manifest_created=False, deployment_ready=False, installed=False,
        next_stage=['python3', '-B', str(args.work/'source/scripts/stage-appliance-release.py'),
            '--source-root', str(args.work/'source'), '--artifact-root', 'NEW_MATCHED_ARTIFACT_ROOT',
            '--output', str(args.work/'canonical'), '--release-id', args.release_id])
    with (args.work/'inputs.json').open('x') as stream:
        json.dump(report, stream, sort_keys=True, indent=2)
        stream.write('\n')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('artifacts', 'public-audit', 'work'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--public-audit-sha256', required=True)
    parser.add_argument('--release-id', required=True)
    result = prepare(parser.parse_args())
    print(json.dumps({key: value for key, value in result.items() if key != 'source_sha256'}, sort_keys=True))


if __name__ == '__main__':
    main()
