#!/usr/bin/env python3
"""Build isolated contact-kind candidates atop the resident/private-policy base."""

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
BASE_MANIFEST = '49d78fd38adc97aa60930b58a48aacb2b2f7b74ec4a199d45b5096d27fd2cb90'
PATCH = '200120eb6201077c6c5f01a96e34079fcb16831fa987c8c94c3949929f091b63'
CANDIDATE = '5260678d0642d1300a2af0492922e8f2fd1893d3ab0ae1668a90418c4a50a5a9'
BASE_NATIVE = {
    'internal/store/workflow_journal.go': '1bd8385aac663259e4ef4eb4944b42be3deb65e85b53ad87767df0fbb1200fec',
    'api/rest/workflow_journal_handler.go': '1c6a334031613a2f2ef722a221aaa01f9811091e17f62b545f9b1af1a847ea43'}
BASE_SDK = {'src/sage_sdk/client.py': '77bb9e40570861b75ffa66a1b159a4f402064a3768e6c43a5dda69e0e42a117f',
    'src/sage_sdk/models.py': '6eae2fad6575cdc0af486479a5961b3e29f1a7b603241624c3cc7637fa8a7a9c'}


def require(value):
    if value is not True:
        raise ValueError('candidate_binding_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), ROOT/'scripts'/f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def merge(base, additions, expected_base):
    require(set(additions) == set(expected_base))
    require(all(sha(base[name]) == digest for name, digest in expected_base.items()))
    return {**base, **additions}


def build(args):
    native_builder, sdk_builder = load('build-sage-release'), load('build-sdk-release')
    read = native_builder.source_bytes
    base_raw = read(args.base_manifest)
    require(sha(base_raw) == BASE_MANIFEST and sha(read(args.contact/'sage.patch')) == PATCH
        and sha(read(args.contact/'CANDIDATE_SHA256SUMS')) == CANDIDATE)
    audit_raw = read(args.public_audit)
    require(sha(audit_raw) == args.public_audit_sha256)
    audit = json.loads(audit_raw)
    require(audit['wheel_sha256'] == '514f6219ddef855c419bd5f3c69c44f957cc42a8059c7e5fc8caa20d9fd570e7')
    reviewed = audit['required_native_sdk_source_sha256']
    base_manifest = json.loads(base_raw)
    base = {}
    for name, digest in base_manifest['source_sha256'].items():
        native_builder.relative_input(name)
        raw = read(args.base_source/name)
        require(sha(raw) == digest)
        base[name] = raw
    replacements = {name: read(args.contact/'sage'/name) for name in BASE_NATIVE}
    for name, raw in replacements.items():
        require(sha(raw) == reviewed['sage/'+name])
    native = merge(base, replacements, BASE_NATIVE)
    sdk = sdk_builder.snapshot(args.sdk_base)
    sdk_replacements = {name: read(args.contact/'sage/sdk/python'/name) for name in BASE_SDK}
    for name, raw in sdk_replacements.items():
        require(sha(raw) == reviewed['sage/sdk/python/'+name])
    sdk = merge(sdk, sdk_replacements, BASE_SDK)
    require(not args.work.exists() and not args.work.is_symlink())
    args.work.mkdir(mode=0o755)
    for directory, payloads in (('native-source', native), ('sdk-source', sdk)):
        for name, raw in payloads.items():
            path = args.work/directory/name
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('xb') as stream:
                stream.write(raw)
            path.chmod(0o644)
    evidence = dict(schema='sage.lantern.contact-policy-build.v1', base_manifest_sha256=BASE_MANIFEST,
        contact_patch_sha256=PATCH, contact_candidate_sha256=CANDIDATE, public_audit_sha256=args.public_audit_sha256,
        native_source_sha256={name: sha(raw) for name, raw in native.items()},
        sdk_source_sha256={name: sha(raw) for name, raw in sdk.items()},
        changed_native_paths=sorted(BASE_NATIVE), unchanged_private_policy_and_resident_sources=True,
        deployed=False, accepted=False)
    (args.work/'SOURCE-INPUTS.json').write_text(json.dumps(evidence, sort_keys=True, indent=2)+'\n')
    sdk_wheels = [sdk_builder.build(args.work/'sdk-source', args.work/f'sdk-{suffix}') for suffix in ('a', 'b')]
    require(sdk_wheels[0].read_bytes() == sdk_wheels[1].read_bytes())
    original_environment = native_builder.environment
    native_builder.environment = lambda: dict(original_environment(), GOCACHE=str(args.work/'go-cache'))
    manifests = [native_builder.build(args.work/'native-source', args.work/f'native-{suffix}',
        '20260908-contact-private-policy', 1788825600) for suffix in ('a', 'b')]
    require(manifests[0]['binary_sha256'] == manifests[1]['binary_sha256'])
    evidence.update(sdk_wheel_sha256=sha(sdk_wheels[0].read_bytes()),
        native_binary_sha256=manifests[0]['binary_sha256'],
        native_manifest_sha256=sha(read(args.work/'native-a/manifest.json')), identical_builds=True)
    (args.work/'BUILD-AUDIT.json').write_text(json.dumps(evidence, sort_keys=True, indent=2)+'\n')
    print(json.dumps({key: value for key, value in evidence.items() if not key.endswith('source_sha256')}, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('base-source', 'base-manifest', 'contact', 'sdk-base', 'public-audit', 'work'):
        parser.add_argument('--'+name, type=Path, required=True)
    parser.add_argument('--public-audit-sha256', required=True)
    build(parser.parse_args())
