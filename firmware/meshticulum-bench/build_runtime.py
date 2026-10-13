# SPDX-License-Identifier: Apache-2.0
# Extracted for Meshticulum; namespace/path/test adaptation. See docs/EXTRACTION.md.
"""Freeze the reviewed flow plus bench adapters; never grants RF authority."""
import argparse
import gzip
import hashlib
import io
import json
from source_core import core_source
from pathlib import Path
import tarfile
import zipfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / 'build/runtime'
FLOW_PINS = {'flow.py': '9a8d4bb3bea2f69581d80ecd31fda7692c2435fe1363be53564d5281485cb857',
             'flow_port.py': '57596421d916f0c49d61599653b5ab75154d64e5bc806ba1444fa5905ad025ca'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-test-report', type=Path)
    parser.add_argument('--meshticulum-root', type=Path)
    parser.add_argument('--radio-profile', choices=('gfsk125','gfsk250'), default='gfsk125')
    parser.add_argument('--execution-layout', choices=('directory', 'sealed-image'), default='directory')
    parser.add_argument('--output-dir', type=Path, default=WORK)
    args = parser.parse_args()
    source = core_source(args.meshticulum_root)
    work = args.output_dir.resolve()
    work.mkdir(parents=True, exist_ok=True)
    test_report = test_hash = None
    if args.source_test_report:
        report = args.source_test_report.resolve()
        suites = list(ET.fromstring(report.read_bytes()).iter('testsuite'))
        if not suites or not sum(int(s.get('tests', 0)) for s in suites) or any(
                int(s.get('failures', 0)) or int(s.get('errors', 0)) for s in suites):
            raise SystemExit('Source test report is empty or failing.')
        test_report = str(report.relative_to(work))
        test_hash = hashlib.sha256(report.read_bytes()).hexdigest()
    bootstrap = (ROOT / 'firmware/meshticulum-bench/runtime_bootstrap.py').read_bytes()
    files = {'LICENSE': (source / 'LICENSE').read_bytes(), 'NOTICE': (source / 'NOTICE').read_bytes(),
             **{name: (source / name).read_bytes() for name in ('docs/THIRD_PARTY.md', 'docs/EXTRACTION.md',
                 'provenance/UPSTREAM_SOURCE.json', 'provenance/THIRD_PARTY_SOURCE_AUDIT.json')},
             'bench_node.py': (ROOT / 'firmware/meshticulum-bench/bench_node.py').read_bytes(),
             'meshticulum/__init__.py': (source / 'src/meshticulum/__init__.py').read_bytes(),
             'meshticulum/resources/__init__.py': (source / 'src/meshticulum/resources/__init__.py').read_bytes(),
             'meshticulum/resources/resource.py': (source / 'src/meshticulum/resources/resource.py').read_bytes(),
             **{('meshticulum/resources/' + name): (source / 'src/meshticulum/resources' / name).read_bytes()
                for name in ('knowledge_pack.py', 'lamplighter.py', 'swarm.py', 'byte_bundle.py', 'bundle_wire.py')}}
    for package in ('bench', 'contracts', 'scheduler', 'flow', 'ip',
                    'meshticulum_contracts', 'meshticulum_scheduler'):
        for path in sorted((source / 'src/meshticulum' / package).glob('*.py')):
            if path.name == 'flash_app.py': continue
            data = path.read_bytes()
            if package == 'flow' and path.name in FLOW_PINS and hashlib.sha256(data).hexdigest() != FLOW_PINS[path.name]:
                raise SystemExit('Accepted flow source changed; review required before deployment.')
            files[str(path.relative_to(source / 'src'))] = data
    application_source = ROOT / 'software/meshticulum-integration/src'
    for package in ('bench', 'ip'):
        for path in sorted((application_source / 'lantern_meshticulum' / package).glob('*.py')):
            files[str(path.relative_to(application_source))] = path.read_bytes()
    files['lantern_meshticulum/__init__.py'] = (application_source / 'lantern_meshticulum/__init__.py').read_bytes()
    files['provenance/LANTERN_MESHTICULUM_SOURCE_LOCK.json'] = (ROOT / 'software/meshticulum-integration/meshticulum-source-lock.json').read_bytes()
    for name, data in files.items():
        if name.endswith('.py'): compile(data, name, 'exec')
    manifest = {'schema': 'meshticulum.bench-runtime-bundle.v1', 'firmware': 'mtb1-b433927ba6',
        'firmware_by_unit': {'unit-a': 'mtb1-b433927ba6', 'unit-b': 'mtb1-9816f92488'},
        'bootstrap_sha256': hashlib.sha256(bootstrap).hexdigest(),
        'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())},
        'activation': 'Installation only inspects; a separate root-provisioned finite grant is required to arm.',
        'private_sage_imports': False, 'private_services_enabled': False,
        'public_service_formats': ['MR1.request', 'MR1.response'],
        'public_ip_service_formats': ['MR1.request', 'MR1.response'],
        'public_parent_claims_required': True,
        'public_pilot_package_schemas': ['meshticulum.public-pilot-package.v2'],
        'public_debt_snapshot_schemas': ['meshticulum.public-debt-scope.v1'],
        'public_parent_snapshot_schemas': ['meshticulum.public-parent-observation.v1'],
        'radio_mtu_bytes': 255, 'flow_payload_bytes': 193}
    if args.radio_profile=='gfsk250':
        from sys import path as import_path
        import_path[:0]=[str(source/'src'),str(application_source)]
        from lantern_meshticulum.bench.profiles import GFSK250_FIRMWARE
        manifest['firmware']=GFSK250_FIRMWARE
        manifest['firmware_by_unit']['unit-a']=GFSK250_FIRMWARE
        manifest['radio_profile_name']='gfsk250'
    if args.execution_layout == 'sealed-image':
        originals = files
        raw_image = io.BytesIO()
        with zipfile.ZipFile(raw_image, 'w', compression=zipfile.ZIP_STORED, allowZip64=False) as image:
            for name, data in sorted(originals.items()):
                entry = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_STORED; entry.external_attr = 0o100600 << 16
                image.writestr(entry, data)
        image_bytes = raw_image.getvalue()
        if len(image_bytes) > 2000000:
            raise SystemExit('Sealed execution image exceeds its reviewed byte limit.')
        loader = (ROOT / 'firmware/meshticulum-bench/execution_runtime.py').read_bytes()
        compile(loader, 'execution_runtime.py', 'exec')
        manifest['schema'] = 'meshticulum.bench-runtime-bundle.v2'
        manifest['source_files'] = manifest['files']
        files = {name: data for name, data in originals.items()
                 if not name.startswith(('meshticulum/', 'lantern_meshticulum/'))}
        files.update({'execution.zip': image_bytes, 'execution_runtime.py': loader})
        manifest['files'] = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
        manifest['execution_layout'] = dict(schema='sage-lantern.sealed-source-image.v1', image='execution.zip',
            bytes=len(image_bytes), sha256=hashlib.sha256(image_bytes).hexdigest(), loader='execution_runtime.py',
            loader_sha256=hashlib.sha256(loader).hexdigest(),
            source_files_sha256=hashlib.sha256(json.dumps(manifest['source_files'],sort_keys=True,
                separators=(',',':'),allow_nan=False).encode()).hexdigest())
    files['manifest.json'] = json.dumps(manifest, sort_keys=True, indent=2).encode() + b'\n'
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in sorted(files.items()):
            entry = tarfile.TarInfo(name); entry.size = len(data); entry.mode = 0o600
            archive.addfile(entry, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), mtime=0)
    digest = hashlib.sha256(data).hexdigest()
    directory = work / ('runtime-' + digest[:16]); directory.mkdir(exist_ok=True)
    archive = directory / 'runtime.tar.gz'
    if archive.exists(): assert archive.read_bytes() == data
    else: archive.write_bytes(data)
    target = directory / 'runtime_bootstrap.py'
    if target.exists(): assert target.read_bytes() == bootstrap
    else: target.write_bytes(bootstrap)
    record = {'schema': 'meshticulum.runtime-build.v1', 'archive': str(archive), 'sha256': digest,
        'bytes': len(data), 'bootstrap_sha256': hashlib.sha256(bootstrap).hexdigest(),
        'manifest': manifest, 'source_tests': test_report, 'source_tests_sha256': test_hash, 'rf_tests': None,
        'installed': False}
    latest = work / 'RUNTIME_BUNDLE.json'
    if latest.exists():
        prior = json.loads(latest.read_text())
        (work / ('RUNTIME_BUNDLE-' + prior['sha256'][:16] + '.json')).write_bytes(latest.read_bytes())
    for output in (directory / 'BUNDLE.json', latest):
        output.write_text(json.dumps(record, indent=2) + '\n')
    print(json.dumps({k: v for k, v in record.items() if k != 'manifest'}))


if __name__ == '__main__': main()
