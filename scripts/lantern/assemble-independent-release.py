#!/usr/bin/env python3
"""Local sterile candidate assembly; no installation, identity or device access.

Preserves frozen inputs. Reconstructs matching runtime source from the pinned
wheel, selects resident native and reviewed UI, then delegates canonical staging.
Only a new working directory is allowed; partial output is retained on failure.
"""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import zipfile


ROOT = Path(__file__).resolve().parents[2]
NATIVE = '11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c'
RUNTIME = '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'
MANIFEST = 'dba0a00a8e7af7a5e7e5f73c715568add219b2675feab885829863512b26ca0c'


def require(value, reason):
    if value is not True:
        raise ValueError(reason)


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def safe_name(name):
    require(type(name) is str and name and not Path(name).is_absolute()
            and '..' not in Path(name).parts and str(Path(name)) == name, 'unsafe_member')
    return name


def write_new(path, raw):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as stream:
        stream.write(raw)


def runtime_source(wheel, source):
    require(digest(wheel) == RUNTIME, 'runtime_pin')
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), 'duplicate_wheel_member')
        manifest = json.loads(archive.read('sage_lantern/release-manifest.json'))
        for name, checksum in manifest['source_sha256'].items():
            safe_name(name)
            if name == 'pyproject.toml':
                raw = (ROOT / 'software/runtime/pyproject.toml').read_bytes()
            else:
                require(name.startswith('src/sage_lantern/') and name.endswith('.py'), 'unexpected_runtime_source')
                raw = archive.read(name.removeprefix('src/'))
            require(hashlib.sha256(raw).hexdigest() == checksum, 'runtime_source_pin')
            write_new(source / 'software/runtime' / name, raw)


def ui_members(archive):
    members = archive.getmembers()
    names = [member.name for member in members]
    require(len(names) == len(set(names)) and 'manifest.json' in names, 'duplicate_or_missing_ui_manifest')
    require(all(member.isfile() for member in members), 'ui_nonregular_member')
    manifest = json.load(archive.extractfile('manifest.json'))
    require(manifest['schema'] == 'sage.lantern.runtime-ui-refresh.v1', 'ui_schema')
    expected = {'manifest.json'} | {'dist/' + safe_name(name) for name in manifest['after']}
    require(set(names) == expected, 'ui_inventory')
    for member in members:
        safe_name(member.name)
        require(member.size <= 64 * 1024 * 1024, 'ui_member_size')
    return manifest


def select_native(lock, manifest):
    require(manifest['binary_sha256'] == NATIVE
            and manifest['version'] == 'lantern-local.20260907-fresh-v27-resident-vault', 'native_manifest_selection')
    lock['components']['sage']['version'] = manifest['version']
    lock['components']['sage']['source'] = 'reviewed resident-key ARM64 build; binary manifest sha256=' + MANIFEST


def assemble(args):
    require(digest(args.native / 'sage-gui') == NATIVE and digest(args.native / 'manifest.json') == MANIFEST, 'native_pin')
    require(digest(args.runtime_wheel) == RUNTIME and digest(args.ui) == args.ui_sha256, 'artifact_pin')
    require(not args.work.exists(), 'work_must_be_new')
    spec = importlib.util.spec_from_file_location('candidate_stage', ROOT / 'scripts/stage-appliance-release.py')
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    args.work.mkdir(mode=0o755)
    source, artifacts, output = (args.work / name for name in ('source', 'artifacts', 'canonical'))
    source.mkdir()
    for row in stage.required_sources():
        path = ROOT / row['path']
        require(path.is_file() and not path.is_symlink(), 'source_not_regular')
        raw = path.read_bytes()
        write_new(source / row['path'], raw)
        (source / row['path']).chmod(path.stat().st_mode & 0o777)
        require(path.read_bytes() == raw, 'source_changed')
    runtime_source(args.runtime_wheel, source)
    app = 'apps/lantern-fleet-admin'
    for name in ('package.json', 'package-lock.json'):
        write_new(source / app / name, (ROOT / app / name).read_bytes())
    shutil.copytree(ROOT / app / 'public', source / app / 'public')
    stage.MANIFEST.tree(args.artifacts)
    subprocess.run(['/bin/cp', '-cR', str(args.artifacts), str(artifacts)], check=True)
    lock_path = artifacts / stage.LOCK
    lock = json.loads(lock_path.read_bytes())
    for entry in lock['files']:
        require(digest(artifacts / entry['path']) == entry['sha256']
                and (artifacts / entry['path']).stat().st_size == entry['bytes'], 'base_artifact_pin')
    replacements = {stage.BINARY: args.native / 'sage-gui', stage.SAGE_MANIFEST: args.native / 'manifest.json',
        stage.WHEELS + args.runtime_wheel.name: args.runtime_wheel}
    for name, path in replacements.items():
        shutil.copyfile(path, artifacts / name)
    for entry in lock['files']:
        if entry['path'] in replacements:
            path = artifacts / entry['path']
            entry.update(sha256=digest(path), bytes=path.stat().st_size)
    select_native(lock, json.loads((args.native / 'manifest.json').read_bytes()))
    lock['components']['lantern-runtime']['source'] = 'reviewed exact R7 wheel/source closure; sha256=' + RUNTIME
    lock_path.write_text(json.dumps(lock, sort_keys=True, indent=2) + '\n')
    with tarfile.open(args.ui) as archive:
        manifest = ui_members(archive)
        for name, checksum in manifest['unchanged'].items():
            require(name in ('package.json', 'package-lock.json')
                    and digest(artifacts / app / name) == checksum, 'ui_dependency_pin')
        require(set(manifest['unchanged']) == {'package.json', 'package-lock.json'}, 'ui_dependency_closure')
        shutil.rmtree(artifacts / app / 'dist')
        for name, checksum in manifest['after'].items():
            raw = archive.extractfile('dist/' + name).read()
            require(hashlib.sha256(raw).hexdigest() == checksum, 'ui_file_pin')
            write_new(artifacts / app / 'dist' / name, raw)
    require(digest(args.ui) == args.ui_sha256, 'ui_changed')
    stage.build(source, artifacts, output, args.release_id)
    result = dict(schema='sage.lantern.independent-candidate.v1', output=str(output),
        manifest_sha256=digest(output / 'manifest.json'), native_sha256=NATIVE, runtime_sha256=RUNTIME,
        ui_sha256=args.ui_sha256, identity_copied=False, installed=False, accepted=False,
        startup_sidecar_included=False)
    write_new(args.work / 'candidate.json', json.dumps(result, sort_keys=True, indent=2).encode() + b'\n')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('artifacts', 'native', 'runtime-wheel', 'ui', 'work'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--ui-sha256', required=True)
    parser.add_argument('--release-id', required=True)
    args = parser.parse_args()
    print(json.dumps(assemble(args), sort_keys=True))


if __name__ == '__main__':
    main()
