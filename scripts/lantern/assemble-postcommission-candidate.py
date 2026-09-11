#!/usr/bin/env python3
"""Assemble a new reviewed-input candidate; never deploy or reuse a manifest."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
PINS = {
    'runtime': 'bbc7f20f7faaa505455906e5fc270ee97129076f1f5c1caa2c2a93d23a1fc443',
    'runtime_audit': 'b8477b245bf1cfc9dbf6beb7cdec3769e0924dc64c24d89507f5ba36bcddee2b',
    'native': '387aee03d725c13bb064e24572de98096405831e929bc4aea616428ab2107421',
    'native_manifest': '6ec887feb44cef470233425c25c08272230a8e69fa8936c10fb97d189f21ec38',
    'sdk': '05fa8da878fb35906c0e50cfc74adfe713339486a911ba8b1c67e19b9cf9abd7',
    'ui': '08e44968c1cd77e371a59b11c74b817dfac493da8cc294dd1bc11b6cc1bff9db',
    'ui_source': '42d5f1a94ef405ffc9a8a3d744ed6944d1b893f4887851fa6919c84ebd5cf222',
    'base_lock': 'c703205ffbb1441d3c874afc077f04733de54e81899b56c088d44da8211271c3'}
CONTEXT = '5129ad541c7ac7e3ccdaf86cdbe8c7587ca895f7d8058a7ca0467fa7bf2b815b'


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def load_stage():
    spec = importlib.util.spec_from_file_location('combined_stage', ROOT/'scripts/stage-appliance-release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def ui_payload(archive, expected):
    seen = set()
    for member in archive.getmembers():
        name = member.name.removeprefix('./')
        require(member.isdir() or member.isfile(), 'nonregular_ui')
        require(not Path(name).is_absolute() and '..' not in Path(name).parts, 'unsafe_ui_name')
        if member.isdir():
            continue
        require(name and name not in seen and member.size <= 64*1024*1024, 'duplicate_or_oversized_ui')
        seen.add(name)
        raw = archive.extractfile(member).read()
        require(name in expected and hashlib.sha256(raw).hexdigest() == expected[name], 'ui_dist_changed')
        yield name, raw
    require(seen == set(expected), 'incomplete_ui_archive')


def assemble(args):
    stage = load_stage()
    verifier = stage.MANIFEST
    wheel_name = 'sage_lantern_runtime-0.2.0-py3-none-any.whl'
    sdk_name = 'sage_agent_sdk-11.19.10+lantern.20260906-py3-none-any.whl'
    paths = dict(runtime=args.runtime/'build-a'/wheel_name, runtime_audit=args.runtime/'BUILD-AUDIT.json',
        native=args.native/'native-a/sage-gui', native_manifest=args.native/'native-a/manifest.json',
        sdk=args.native/'sdk-a'/sdk_name, ui=args.ui, ui_source=args.ui_source/'SOURCE_SHA256SUMS',
        base_lock=args.base/stage.LOCK)
    for name, path in paths.items():
        verifier.no_links(path)
        require(digest(path) == PINS[name], 'input_pin:'+name)
    audit = verifier.document(paths['runtime_audit'].read_bytes())
    require(audit['all_six_conversation_modules_match_approved'] is True, 'compact_context_not_reviewed')
    source_rows = stage.required_sources()
    content = {}
    for row in source_rows:
        content[row['path']] = verifier.metadata_bytes(ROOT/row['path'])[0]
    runtime_files = {name: value for name, value in audit['source_sha256'].items()
        if name.startswith('software/runtime/')}
    for name, checksum in runtime_files.items():
        verifier.safe_name(name)
        raw = verifier.metadata_bytes(args.runtime/'source'/name)[0]
        require(hashlib.sha256(raw).hexdigest() == checksum, 'runtime_source_pin')
        content[name] = raw
    require(hashlib.sha256(content['software/runtime/src/sage_lantern/conversation_context.py']).hexdigest() == CONTEXT,
        'compact_context_pin')
    for name, checksum in audit['reviewed_companions'].items():
        require(name in content, 'missing_companion_source')
        require(hashlib.sha256(content[name]).hexdigest() == checksum, 'companion_source_pin')
    for line in paths['ui_source'].read_text().splitlines():
        checksum, name = line.split('  ', 1)
        verifier.safe_name(name)
        require(digest(args.ui_source/name) == checksum, 'ui_source_pin')
    for name in ('package.json', 'package-lock.json'):
        raw = verifier.metadata_bytes(args.ui_source/name)[0]
        require(raw == verifier.metadata_bytes(args.base/stage.APP/name)[0], 'ui_dependencies_changed')
        content[stage.APP+name] = raw
    for name in verifier.tree(args.ui_source/'public')[0]:
        content[stage.APP+'public/'+name] = verifier.metadata_bytes(args.ui_source/'public'/name)[0]
    expected_ui = {name: verifier.file_hash(args.ui_source/'dist'/name)[0]['sha256']
        for name in verifier.tree(args.ui_source/'dist')[0]}
    verifier.tree(args.base)
    require(not args.work.exists() and not args.work.is_symlink(), 'new_work_required')
    verifier.no_links(args.work)
    args.work.mkdir(mode=0o755)
    source, artifacts, canonical = [args.work/name for name in ('source', 'artifacts', 'canonical')]
    for name, raw in content.items():
        path = source/name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as stream:
            stream.write(raw)
        origin = ROOT/name
        path.chmod(origin.stat().st_mode & 0o777 if origin.exists() else 0o644)
    subprocess.run(['/bin/cp', '-cR', str(args.base), str(artifacts)], check=True)
    lock = verifier.document((artifacts/stage.LOCK).read_bytes())
    for entry in lock['files']:
        details = verifier.file_hash(artifacts/entry['path'])[0]
        require(details['sha256'] == entry['sha256'] and details['bytes'] == entry['bytes'], 'base_locked_artifact_changed')
    replacements = {stage.BINARY: paths['native'], stage.WHEELS+wheel_name: paths['runtime'], stage.WHEELS+sdk_name: paths['sdk']}
    require(set(replacements) <= {entry['path'] for entry in lock['files']}, 'missing_lock_replacement')
    for name, path in replacements.items():
        shutil.copyfile(path, artifacts/name)
    shutil.copyfile(paths['native_manifest'], artifacts/stage.SAGE_MANIFEST)
    sdk_source = artifacts/'sources/sage-sdk'
    shutil.rmtree(sdk_source)
    shutil.copytree(args.native/'sdk-source', sdk_source)
    for entry in lock['files']:
        if entry['path'] in replacements:
            details = verifier.file_hash(artifacts/entry['path'])[0]
            entry.update(sha256=details['sha256'], bytes=details['bytes'])
    native_manifest = verifier.document(paths['native_manifest'].read_bytes())
    lock['components']['sage'].update(version=native_manifest['version'], source='reviewed contact/private-policy manifest '+PINS['native_manifest'])
    lock['components']['lantern-runtime']['source'] = 'reviewed metrics/public/contact/compact runtime '+PINS['runtime']
    lock['components']['sage-agent-sdk']['source'] = 'reviewed contact SDK '+PINS['sdk']
    (artifacts/stage.LOCK).write_text(json.dumps(lock, sort_keys=True, indent=2)+'\n')
    shutil.rmtree(artifacts/stage.APP/'dist')
    with tarfile.open(paths['ui']) as archive:
        for name, raw in ui_payload(archive, expected_ui):
            path = artifacts/stage.APP/'dist'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
            path.chmod(0o644)
    for name, path in paths.items():
        require(digest(path) == PINS[name], 'input_changed:'+name)
    manifest = stage.build(source, artifacts, canonical, args.release_id)
    report = dict(schema='sage.lantern.combined-candidate.v1', pins=PINS,
        manifest_sha256=digest(canonical/'manifest.json'), release_id=args.release_id,
        source_sha256={name: hashlib.sha256(raw).hexdigest() for name, raw in content.items()},
        deployed=False, independent_review_required=True, physical_accepted=False)
    (args.work/'candidate.json').write_text(json.dumps(report, sort_keys=True, indent=2)+'\n')
    print(json.dumps({key: value for key, value in report.items() if key != 'source_sha256'}, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('base', 'runtime', 'native', 'ui', 'ui-source', 'work'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--release-id', required=True)
    assemble(parser.parse_args())
