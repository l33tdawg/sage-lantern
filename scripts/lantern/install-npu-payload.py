#!/usr/bin/env python3
"""Build and install the reviewed RKLLM payload offline on a fresh ARM64 target.

No services are started. Every input is pinned before the exclusive destination
claim. An interrupted installation remains present for reviewed recovery.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile


SPEC = importlib.util.spec_from_file_location('npu_appliance_profile', Path(__file__).parents[1] / 'stage-appliance-release.py')
PROFILE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROFILE)
require = PROFILE.require


def reviewed_inputs(payload):
    lock = PROFILE.read_json(payload / PROFILE.LOCK)
    require(lock.get('schema') == 'sage.lantern.stack-lock.v1'
            and lock.get('target') == dict(os='linux', architecture='arm64', python='3.11'),
            'unexpected NPU bundle target')
    locked = {}
    for entry in lock['files']:
        require(entry['path'] not in locked, 'duplicate locked input')
        locked[entry['path']] = entry
    inputs = {}
    for name, expected in {**PROFILE.NPU_SOURCE_PINS, **PROFILE.NPU_ARTIFACT_PINS}.items():
        details, identity = PROFILE.MANIFEST.file_hash(payload / name)
        require(details['sha256'] == expected, 'unreviewed NPU input: ' + name)
        if name in PROFILE.NPU_ARTIFACT_PINS:
            require(name in locked and all(locked[name][key] == details[key] for key in ('sha256', 'bytes')),
                    'NPU input differs from stack lock')
        inputs[name] = (details, identity)
    return inputs


def payload_targets(payload):
    """Relative destination -> (payload source, mode) for every copied file."""
    models = PROFILE.NPU_ROOT + 'models/'
    return {
        'lib/librkllmrt.so': (payload / PROFILE.NPU_ROOT / 'lib/librkllmrt.so', 0o644),
        'lib/librknnrt.so': (payload / PROFILE.NPU_ROOT / 'lib/librknnrt.so', 0o644),
        'libexec/rkllm_server.py': (payload / 'software/npu/rkllm_server.py', 0o644),
        'libexec/vlm_server.py': (payload / 'software/npu/vlm_server.py', 0o644),
        'models/lantern-native5-2b-w8a8-opt0-rk3588.rkllm':
            (payload / models / 'lantern-native5-2b-w8a8-opt0-rk3588.rkllm', 0o644),
        'models/qwen3.5-0.8b_vision_rk3588.rknn':
            (payload / models / 'qwen3.5-0.8b_vision_rk3588.rknn', 0o644),
        'models/qwen3.5-0.8b_w8a8_rk3588.rkllm':
            (payload / models / 'qwen3.5-0.8b_w8a8_rk3588.rkllm', 0o644),
    }


def build_engines(payload, *, run=subprocess.run):
    """Compile both engines offline; returns their bytes plus the exact commands."""
    library = payload / PROFILE.NPU_ROOT / 'lib/librkllmrt.so'
    header = payload / PROFILE.NPU_ROOT / 'include'
    source = payload / 'software/npu/rkllm_engine.cpp'
    vision_source = payload / 'software/npu/vlm_engine.cpp'
    with tempfile.TemporaryDirectory(prefix='lantern-rkllm-build-') as temporary:
        environment = dict(PATH='/usr/bin:/bin', HOME='/nonexistent', LANG='C', LC_ALL='C')
        compiler = run(['/usr/bin/c++', '--version'], check=True, capture_output=True,
                       text=True, timeout=10, env=environment).stdout
        engine = Path(temporary).resolve() / 'rkllm_engine'
        command = ['/usr/bin/c++', '-O2', '-std=c++17', '-I', str(header), str(source),
                   '-L', str(library.parent), '-lrkllmrt',
                   '-Wl,-rpath,/opt/sage-lantern/npu/lib', '-o', str(engine)]
        run(command, check=True, timeout=300, env=environment)
        # The vision engine decodes and resizes photos, so it needs the target's
        # OpenCV 4 development package (base prerequisite: libopencv-dev).
        opencv_cflags = run(['/usr/bin/pkg-config', '--cflags', 'opencv4'], check=True,
                            capture_output=True, text=True, timeout=10,
                            env=environment).stdout.split()
        opencv_libs = run(['/usr/bin/pkg-config', '--libs', 'opencv4'], check=True,
                          capture_output=True, text=True, timeout=10,
                          env=environment).stdout.split()
        vision_engine = Path(temporary).resolve() / 'vlm_engine'
        vision_command = ['/usr/bin/c++', '-O2', '-std=c++17', '-I', str(header),
                          *opencv_cflags, str(vision_source), '-o', str(vision_engine),
                          '-L', str(library.parent), '-lrknnrt', '-lrkllmrt',
                          *opencv_libs,
                          '-Wl,-rpath,/opt/sage-lantern/npu/lib']
        run(vision_command, check=True, timeout=300, env=environment)
        # Refuse host-architecture or accidental script output before installing.
        raw = engine.read_bytes()
        require(len(raw) >= 64 and raw[:6] == b'\x7fELF\x02\x01'
                and int.from_bytes(raw[18:20], 'little') == 183, 'engine is not ELF64 AArch64')
        vision_raw = vision_engine.read_bytes()
        require(len(vision_raw) >= 64 and vision_raw[:6] == b'\x7fELF\x02\x01'
                and int.from_bytes(vision_raw[18:20], 'little') == 183,
                'vision engine is not ELF64 AArch64')
        return dict(compiler=compiler, command=command, vision_command=vision_command,
                    engines={'bin/rkllm_engine': (raw, 0o755), 'bin/vlm_engine': (vision_raw, 0o755)})


def publish(target, data, mode):
    """Write a destination file atomically with the reviewed owner and mode."""
    target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    target.parent.chmod(0o755)
    os.chown(target.parent, 0, 0)
    descriptor, temporary = tempfile.mkstemp(prefix='.npu-', dir=target.parent)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(data)
        os.fchmod(stream.fileno(), mode)
        os.fchown(stream.fileno(), 0, 0)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, target)
    parent = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)


def build_and_install(payload, destination, *, run=subprocess.run):
    payload, destination = Path(payload), Path(destination)
    PROFILE.MANIFEST.no_links(payload)
    PROFILE.MANIFEST.no_links(destination)
    require(not destination.exists(), 'NPU payload already exists; no overwrite or retry')
    before = reviewed_inputs(payload)
    # Build outside the claimed destination; failure before publication leaves
    # the appliance's outer installation claim intact and never runs inference.
    built = build_engines(payload, run=run)
    require(reviewed_inputs(payload) == before, 'NPU inputs changed during build')
    copies = dict(payload_targets(payload))
    copies.update({name: (data, mode) for name, (data, mode) in built['engines'].items()})
    destination.mkdir(mode=0o755)
    destination.chmod(0o755)
    os.chown(destination, 0, 0)
    for name, (content, mode) in copies.items():
        target = destination / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        target.parent.chmod(0o755)
        os.chown(target.parent, 0, 0)
        if isinstance(content, Path):
            with content.open('rb') as source_stream, target.open('xb') as target_stream:
                shutil.copyfileobj(source_stream, target_stream, length=1024 * 1024)
                os.fchmod(target_stream.fileno(), mode)
                os.fchown(target_stream.fileno(), 0, 0)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            digest = PROFILE.MANIFEST.file_hash(content)[0]['sha256']
        else:
            with target.open('xb') as target_stream:
                target_stream.write(content)
                os.fchmod(target_stream.fileno(), mode)
                os.fchown(target_stream.fileno(), 0, 0)
                target_stream.flush()
                os.fsync(target_stream.fileno())
            digest = hashlib.sha256(content).hexdigest()
        require(PROFILE.MANIFEST.file_hash(target)[0]['sha256'] == digest, 'installed NPU file differs')
    require(reviewed_inputs(payload) == before, 'NPU inputs changed during installation')
    receipt = dict(schema='sage.lantern.npu-build.v1', target='linux-arm64',
        compiler=built['compiler'].strip(), command=built['command'], vision_command=built['vision_command'],
        input_sha256={name: details['sha256'] for name, (details, _) in before.items()},
        engine_sha256=hashlib.sha256(built['engines']['bin/rkllm_engine'][0]).hexdigest(),
        vision_engine_sha256=hashlib.sha256(built['engines']['bin/vlm_engine'][0]).hexdigest(),
        inference_validated=False)
    receipt_path = destination / 'build-provenance.json'
    with receipt_path.open('x') as stream:
        json.dump(receipt, stream, sort_keys=True)
        stream.write('\n')
        os.fchmod(stream.fileno(), 0o644)
        os.fchown(stream.fileno(), 0, 0)
        stream.flush()
        os.fsync(stream.fileno())
    return receipt


def successor_install(payload, destination, predecessor_root, *, accept_replaced=False, run=subprocess.run):
    """Extend an existing reviewed NPU destination with this payload's files.

    Every file this install replaces must already match the installed release's
    recorded digest for that destination, this payload's bytes, or the recorded
    on-unit build of the two engines; anything else refuses unless the operator
    passes accept_replaced, which is recorded in the new provenance.
    """
    payload, destination, predecessor_root = Path(payload), Path(destination), Path(predecessor_root)
    PROFILE.MANIFEST.no_links(payload)
    PROFILE.MANIFEST.no_links(destination)
    require(destination.is_dir(), 'successor NPU install requires an existing destination')
    before = reviewed_inputs(payload)
    raw_manifest = (predecessor_root / 'manifest.json').read_bytes()
    predecessor = json.loads(raw_manifest)
    require(type(predecessor) is dict and type(predecessor.get('release_id')) is str,
            'invalid predecessor release manifest')
    rows = json.loads((predecessor_root / 'payload/release/destinations.json').read_text())['files']
    recorded = {row['destination']: row['sha256'] for row in rows
                if type(row) is dict and row.get('destination') and type(row.get('sha256')) is str}
    provenance_path = destination / 'build-provenance.json'
    provenance_raw = provenance_path.read_bytes()
    provenance = json.loads(provenance_raw)
    engines = {'bin/rkllm_engine': provenance.get('engine_sha256'),
               'bin/vlm_engine': provenance.get('vision_engine_sha256')}
    require(type(provenance) is dict
            and all(value is None or (type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value))
                    for value in engines.values()),
            'invalid NPU build provenance')
    targets = payload_targets(payload)
    for relative, (source_path, _) in targets.items():
        target = destination / relative
        if not target.is_file():
            continue
        current = PROFILE.MANIFEST.file_hash(target)[0]['sha256']
        if current in (PROFILE.MANIFEST.file_hash(source_path)[0]['sha256'], recorded.get(str(target))):
            continue
        require(accept_replaced, 'unverifiable installed NPU file would be replaced: ' + relative)
    built = build_engines(payload, run=run)
    require(reviewed_inputs(payload) == before, 'NPU inputs changed during build')
    replaced = []
    for relative, (data, _) in built['engines'].items():
        target = destination / relative
        if not target.is_file():
            continue
        current = PROFILE.MANIFEST.file_hash(target)[0]['sha256']
        if current == hashlib.sha256(data).hexdigest():
            continue
        if engines.get(relative) is not None and current == engines[relative]:
            continue
        require(accept_replaced, 'unverifiable installed NPU file would be replaced: ' + relative)
    for relative, (source_path, _) in targets.items():
        target = destination / relative
        if target.is_file() and PROFILE.MANIFEST.file_hash(target)[0]['sha256'] not in (
                PROFILE.MANIFEST.file_hash(source_path)[0]['sha256'], recorded.get(str(target))):
            replaced.append(str(target))
    for relative, (data, _) in built['engines'].items():
        target = destination / relative
        if target.is_file() and PROFILE.MANIFEST.file_hash(target)[0]['sha256'] not in (
                hashlib.sha256(data).hexdigest(), engines.get(relative)):
            replaced.append(str(target))
    superseded = destination / ('build-provenance.predecessor-' + predecessor['release_id'] + '.json')
    if not superseded.exists():
        with superseded.open('xb') as stream:
            stream.write(provenance_raw)
            os.fchmod(stream.fileno(), 0o644)
            os.fchown(stream.fileno(), 0, 0)
            stream.flush()
            os.fsync(stream.fileno())
    for relative, (source_path, mode) in targets.items():
        publish(destination / relative, source_path.read_bytes(), mode)
    for relative, (data, mode) in built['engines'].items():
        publish(destination / relative, data, mode)
    require(reviewed_inputs(payload) == before, 'NPU inputs changed during installation')
    receipt = dict(schema='sage.lantern.npu-build.v1', target='linux-arm64',
        compiler=built['compiler'].strip(), command=built['command'], vision_command=built['vision_command'],
        input_sha256={name: details['sha256'] for name, (details, _) in before.items()},
        engine_sha256=hashlib.sha256(built['engines']['bin/rkllm_engine'][0]).hexdigest(),
        vision_engine_sha256=hashlib.sha256(built['engines']['bin/vlm_engine'][0]).hexdigest(),
        predecessor_release_id=predecessor['release_id'],
        predecessor_manifest_sha256=hashlib.sha256(raw_manifest).hexdigest(),
        replaced_unverifiable_npu_targets=sorted(replaced),
        inference_validated=False)
    publish(provenance_path, (json.dumps(receipt, sort_keys=True) + '\n').encode(), 0o644)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload', type=Path, required=True)
    parser.add_argument('--predecessor-release-root',
                        help='On-unit release tree of the installed release; required when the NPU payload already exists')
    parser.add_argument('--accept-replaced', action='store_true',
                        help='Successor install only: allow replacing NPU files the installed release cannot account for; refused by default')
    args = parser.parse_args()
    require(os.geteuid() == 0 and platform.system() == 'Linux' and platform.machine() == 'aarch64',
            'root on Linux aarch64 required')
    destination = Path('/opt/sage-lantern/npu')
    if args.predecessor_release_root:
        successor_install(args.payload.absolute(), destination, Path(args.predecessor_release_root),
                          accept_replaced=args.accept_replaced)
    else:
        build_and_install(args.payload.absolute(), destination)


if __name__ == '__main__':
    main()
