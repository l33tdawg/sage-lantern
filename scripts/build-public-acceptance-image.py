#!/usr/bin/env python3
"""Snapshot/cross-build public-test binaries offline; never invoke Docker or targets."""

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import struct
import tempfile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('release_snapshot', ROOT / 'scripts/build-sage-release.py')
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
TARGETS = {
    'sage-gui': ('./cmd/sage-gui', ''),
    'v119-governance-fixture': ('./cmd/v119-governance-fixture', 'v119testfixture'),
    'cometbft': ('./cmd/cometbft', ''),
}
COMET_VERSION = 'v0.38.23'
COMET_COMMIT = 'feb2aea4dc271d612129afc958cb844713ec792b'


def environment(arch):
    if arch not in ('arm64', 'amd64'):
        raise ValueError('unsupported architecture')
    env = RELEASE.environment()
    env['GOARCH'] = arch
    if arch == 'amd64':
        env.pop('GOARM64', None)
        env['GOAMD64'] = 'v1'
    return env


def graph(source, go, env, target):
    package, tags = TARGETS[target]
    command = [go, 'list', '-mod=readonly', '-deps', '-json']
    if tags:
        command += ['-tags=' + tags]
    packages = list(RELEASE.json_stream(RELEASE.run(command + [package], source, env, timeout=300)))
    if not packages or len(packages) > 10000:
        raise ValueError('invalid dependency graph')
    local, dependencies, modules = {}, {}, {}

    def capture(path, name):
        data = RELEASE.source_bytes(path)
        if path.is_relative_to(source):
            local[str(RELEASE.relative_input(str(path.relative_to(source))))] = data
        else:
            dependencies[name] = RELEASE.digest(data)

    for name in ('go.mod', 'go.sum'):
        capture(source / name, name)
    for record in packages:
        if record.get('Error') or record.get('DepsErrors') or record.get('CgoFiles'):
            raise ValueError('incomplete or CGO-dependent graph')
        if record['ImportPath'] == 'unsafe':
            continue
        directory = Path(record['Dir'])
        for field in RELEASE.INPUT_FIELDS:
            for name in record.get(field, []):
                capture(directory / RELEASE.relative_input(name), record['ImportPath'] + '/' + name)
        module = record.get('Module')
        if module:
            replacement = module.get('Replace', module)
            modules[module['Path']] = {key: replacement[key] for key in ('Path', 'Version', 'Sum', 'GoModSum') if key in replacement}
            if replacement.get('GoMod'):
                modfile = Path(replacement['GoMod'])
                capture(modfile, 'module:' + module['Path'] + '/go.mod')
                if modfile.is_relative_to(source) and modfile.with_name('go.sum').exists():
                    capture(modfile.with_name('go.sum'), 'unused')
            if replacement.get('Dir') and not replacement.get('Version') and not Path(replacement['Dir']).is_relative_to(source):
                raise ValueError('replacement outside snapshot root')
    if package.removeprefix('./') + '/main.go' not in local:
        raise ValueError('target source absent')
    return local, dependencies, modules


def verify_static_elf(path, arch):
    data = RELEASE.source_bytes(path)
    if len(data) < 64 or data[:7] != b'\x7fELF\x02\x01\x01':
        raise ValueError('not ELF64 little endian')
    if int.from_bytes(data[18:20], 'little') != {'arm64': 183, 'amd64': 62}[arch]:
        raise ValueError('wrong ELF architecture')
    offset = int.from_bytes(data[32:40], 'little')
    size, count = struct.unpack_from('<HH', data, 54)
    if not 56 <= size <= 256 or not 1 <= count <= 256 or offset + count * size > len(data):
        raise ValueError('invalid ELF program headers')
    for index in range(count):
        kind = struct.unpack_from('<I', data, offset + index * size)[0]
        if kind in (2, 3):
            raise ValueError('dynamic ELF cannot use scratch runtime')


def dockerfile():
    return b'''FROM scratch
COPY sage-gui v119-governance-fixture cometbft /app/
COPY manifest.json /app/build-manifest.json
ENV PATH=/app SAGE_HOME=/fixture HOME=/fixture REST_ADDR=:8080
WORKDIR /app
EXPOSE 8080
ENTRYPOINT ["/app/sage-gui"]
CMD ["serve"]
'''


def build_command(go, target, flags):
    package, tags = TARGETS[target]
    command = [go, 'build', '-mod=readonly', '-buildvcs=false', '-trimpath', '-p=2', '-ldflags', flags]
    if tags:
        command += ['-tags=' + tags]
    return command + ['-o', '../../context/' + target, package]


def tool_hashes(go, toolchain):
    result = {'go': RELEASE.digest(RELEASE.source_bytes(Path(go)))}
    for name in ('compile', 'asm', 'link'):
        result[name] = RELEASE.digest(RELEASE.source_bytes(Path(toolchain['GOTOOLDIR']) / name))
    for path in sorted((Path(toolchain['GOROOT']) / 'pkg/include').glob('*.h')):
        result['include/' + path.name] = RELEASE.digest(RELEASE.source_bytes(path))
    return result


def prepare(source, output, arch, epoch, compile_binaries=False, expected_inventory=None, expected_toolchain=None):
    source, output = Path(source).absolute(), Path(output).absolute() if output else None
    if output is not None:
        if output.exists() or source == output or source in output.parents or not output.parent.is_dir():
            raise ValueError('output must be new, outside source, with an existing parent')
        for path in (output, *output.parents):
            if path.is_symlink():
                raise ValueError('symlink output path')
    go_path = shutil.which('go')
    if not go_path:
        raise ValueError('local Go toolchain unavailable')
    go = str(Path(go_path).resolve())
    env = environment(arch)
    with tempfile.TemporaryDirectory(prefix='public-image-telemetry-') as telemetry:
        Path(telemetry, 'mode').write_text('off\n')
        env['TEST_TELEMETRY_DIR'] = telemetry
        env['GOCACHE'] = str(Path(telemetry) / 'go-build')
        toolchain = json.loads(RELEASE.run([go, 'env', '-json', 'GOVERSION', 'GOROOT', 'GOTOOLDIR',
                                            'GOMODCACHE', 'GOTELEMETRY'], source, env))
        if toolchain['GOTELEMETRY'] != 'off':
            raise ValueError('telemetry must be off')
        toolchain_hashes = tool_hashes(go, toolchain)
        toolchain_digest = RELEASE.digest(RELEASE.encoded(toolchain_hashes))
        if expected_toolchain is not None and expected_toolchain != toolchain_digest:
            raise ValueError('reviewed toolchain pin mismatch')
        comet = Path(toolchain['GOMODCACHE']) / ('github.com/cometbft/cometbft@' + COMET_VERSION)
        roots = {name: comet if name == 'cometbft' else source for name in TARGETS}
        snapshots, inventory = {}, {}
        for name, root in roots.items():
            try:
                snapshots[name] = graph(root, go, env, name)
            except Exception as error:
                raise ValueError('offline dependency snapshot failed for ' + name + ': ' + str(error)) from None
            inventory[name] = RELEASE.source_manifest(snapshots[name])
        inventory_hash = RELEASE.digest(RELEASE.encoded(inventory))
        if expected_inventory is not None and expected_inventory != inventory_hash:
            raise ValueError('reviewed source/dependency inventory pin mismatch')
        manifest = dict(schema='sage.lantern.public-image-build.v1', inventory=inventory,
            inventory_sha256=inventory_hash, architecture=arch, source_date_epoch=epoch,
            go_version=toolchain['GOVERSION'], toolchain_sha256=toolchain_hashes,
            toolchain_inventory_sha256=toolchain_digest,
            comet_generator_version=COMET_VERSION, comet_generator_upstream_commit_reference=COMET_COMMIT,
            comet_generator_scope='upstream cached CLI only; running consensus uses native SAGE overlays',
            targets_executed=False, docker_executed=False, compiled=False)
        if output is None:
            return manifest
        with tempfile.TemporaryDirectory(prefix='public-image-build-', dir=output.parent) as temporary:
            stage = Path(temporary)
            context = stage / 'context'
            context.mkdir(mode=0o700)
            for name, snapshot in snapshots.items():
                root = stage / 'source' / name
                for relative, data in snapshot[0].items():
                    destination = root / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    destination.write_bytes(data)
                    destination.chmod(0o644)
                if RELEASE.source_manifest(graph(root, go, env, name)) != inventory[name]:
                    raise ValueError('staged dependency graph differs: ' + name)
            if compile_binaries:
                date = datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
                artifacts = {}
                for name in TARGETS:
                    root = stage / 'source' / name
                    flags = '-buildid= -s -w'
                    if name == 'sage-gui':
                        flags += f' -X main.version=public-test.{inventory_hash[:16]} -X main.commit=source-{inventory_hash} -X main.date={date}'
                    elif name == 'cometbft':
                        flags += ' -X github.com/cometbft/cometbft/version.TMGitCommitHash=' + COMET_COMMIT
                    command = build_command(go, name, flags)
                    RELEASE.run(command, root, env, timeout=1800)
                    binary = context / name
                    verify_static_elf(binary, arch)
                    proof = RELEASE.run([go, 'version', '-m', str(binary)], root, env)
                    for field in ('GOOS=linux', 'GOARCH=' + arch, 'CGO_ENABLED=0'):
                        if field not in proof:
                            raise ValueError('missing binary build metadata: ' + name)
                    if ('v119testfixture' in proof) != (name == 'v119-governance-fixture'):
                        raise ValueError('native/helper build tag separation failed')
                    binary.chmod(0o555)
                    artifacts[name] = dict(sha256=RELEASE.digest(RELEASE.source_bytes(binary)),
                        command=['go', *command[1:]], build_info=proof.replace(str(binary), name))
                manifest.update(compiled=True, artifacts=artifacts)
            for name, root in roots.items():
                if RELEASE.source_manifest(graph(root, go, env, name)) != inventory[name] or \
                        RELEASE.source_manifest(graph(stage / 'source' / name, go, env, name)) != inventory[name]:
                    raise ValueError('source/cache changed during preparation: ' + name)
            if tool_hashes(go, toolchain) != toolchain_hashes:
                raise ValueError('toolchain changed during preparation')
            (context / 'Dockerfile').write_bytes(dockerfile())
            (context / '.dockerignore').write_text('*\n!Dockerfile\n!sage-gui\n!v119-governance-fixture\n!cometbft\n!manifest.json\n')
            (context / 'manifest.json').write_bytes(RELEASE.encoded(manifest))
            (stage / 'manifest.json').write_bytes(RELEASE.encoded(manifest))
            for path in context.iterdir():
                os.utime(path, (epoch, epoch))
            stage.rename(output)
        return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['preflight', 'stage', 'compile'], default='preflight')
    parser.add_argument('--source', type=Path, default=ROOT.parent / 'sage')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--arch', choices=['arm64', 'amd64'], default='arm64')
    parser.add_argument('--source-date-epoch', type=int, default=0)
    parser.add_argument('--reviewed-build', action='store_true')
    parser.add_argument('--expected-inventory')
    parser.add_argument('--expected-toolchain')
    args = parser.parse_args(argv)
    if args.phase == 'compile' and not args.reviewed_build:
        parser.error('compile requires --reviewed-build; no Docker command is ever executed')
    for value in (args.expected_inventory, args.expected_toolchain):
        if value is not None and re.fullmatch(r'[0-9a-f]{64}', value) is None:
            parser.error('review pins must be 64 lowercase hex characters')
    if args.phase == 'compile' and (args.expected_inventory is None or args.expected_toolchain is None):
        parser.error('compile requires reviewed --expected-inventory and --expected-toolchain pins')
    if (args.phase != 'preflight') != (args.output is not None):
        parser.error('stage/compile require --output; preflight does not accept output')
    if not 0 <= args.source_date_epoch <= 253402300799:
        parser.error('invalid source date epoch')
    try:
        result = prepare(args.source, args.output, args.arch, args.source_date_epoch, args.phase == 'compile',
                         args.expected_inventory, args.expected_toolchain)
        print(json.dumps({key: result[key] for key in ('inventory_sha256', 'go_version', 'architecture', 'compiled',
                                                      'targets_executed', 'docker_executed', 'toolchain_inventory_sha256')}, sort_keys=True))
        return 0
    except (ValueError, RuntimeError, OSError) as error:
        print(json.dumps({'prepared': False, 'error': str(error).replace(str(Path.home()), '<home>')[:16000]}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
