#!/usr/bin/env python3
"""Stage sealed matched update artifacts only. No activation or drain authority."""

import argparse
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid


ROOT = Path(__file__).resolve().parents[2]
LIMIT = 32 * 1024 * 1024


def require(value, reason):
    if not value:
        raise ValueError(reason)


def helper(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False) + '\n').encode()


def lexical_path(value):
    require(isinstance(value, (str, Path)), 'path required')
    require('\x00' not in str(value) and '..' not in Path(value).parts, 'parent traversal forbidden')
    return Path(value).absolute()


def strict_json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, 'duplicate update metadata')
            result[key] = value
        return result
    def invalid(value):
        raise ValueError('noninteger update metadata number')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=invalid, parse_float=invalid)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_new(path, raw, mode=0o600):
    require(len(raw) <= LIMIT, 'update metadata exceeds bound')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def checked_output(output, digest, *, current):
    require(type(digest) is str and re.fullmatch(r'[0-9a-f]{64}', digest), 'reviewed manifest SHA256 required')
    output = lexical_path(output)
    verifier = helper('update_manifest', 'scripts/appliance-release-manifest.py')
    raw = verifier.metadata_bytes(output / 'manifest.json')[0]
    require(hashlib.sha256(raw).hexdigest() == digest, 'manifest hash mismatch')
    pinned = strict_json(raw)
    if current:
        helper('update_phase', 'scripts/lantern/provision-fresh-appliance.py').verify_release(
            argparse.Namespace(output=str(output), manifest_sha256=digest))
    manifest = verifier.verify(output / 'payload', output / 'manifest.json')
    require(canonical(manifest) == canonical(pinned), 'verified manifest differs from pinned bytes')
    stage = helper('update_stage', 'scripts/stage-appliance-release.py')
    helper('update_profile', 'scripts/lantern/install-fleet-appliance-local.py').canonical_profile(output / 'payload', manifest, stage)
    selection = verifier.metadata_bytes(output / 'selection.json')[0]
    require(canonical(strict_json(selection)) == canonical(pinned['selection']), 'selection mismatch')
    require(canonical(manifest) == canonical(pinned)
            and verifier.metadata_bytes(output / 'manifest.json')[0] == raw, 'manifest changed during verification')
    return output, pinned, raw, selection


def safe_root(value):
    path = lexical_path(value)
    for forbidden in ('/etc', '/opt', '/usr', '/run', '/var/lib', '/var/run', '/private/etc', '/private/var/lib', '/private/var/run'):
        require(path != Path(forbidden) and Path(forbidden) not in path.parents, 'live installation path forbidden')
    require(not any(part in {'.sage', '.ssh', 'sage-personal', 'device-lock', 'identity'} for part in path.parts), 'private state path forbidden')
    for component in (path, *path.parents):
        require(not component.is_symlink(), 'linked update staging path')
    info = path.stat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700,
            'existing owned 0700 staging root required')
    return path


class UpdateJournal:
    def __init__(self, directory, binding):
        self.directory = directory
        directory.mkdir(mode=0o700)
        sync_directory(directory.parent)
        self.binding, self.revision, self.previous = binding, 0, None

    def append(self, phase, details):
        require(phase in {'staging_started', 'candidate_sealed', 'rollback_sealed', 'staged_no_authority'}, 'invalid staging phase')
        record = dict(schema='sage.lantern.update-journal.v1', revision=self.revision + 1,
                      previous_sha256=self.previous, binding=self.binding, phase=phase, details=details)
        raw = canonical(record)
        write_new(self.directory / f'{self.revision + 1:06d}.json', raw)
        self.revision += 1
        self.previous = hashlib.sha256(raw).hexdigest()


def copy_artifacts(checked, destination):
    source, manifest, raw, selection = checked
    source, destination = lexical_path(source), lexical_path(destination)
    require(canonical(manifest) == canonical(strict_json(raw)), 'copy inventory differs from pinned manifest')
    require(canonical(strict_json(selection)) == canonical(manifest['selection']), 'copy selection differs from pinned manifest')
    destination.mkdir(mode=0o700)
    payload = destination / 'payload'
    payload.mkdir(mode=0o755)
    expected = {}
    for entry in manifest['files']:
        source_path = source / 'payload' / entry['path']
        target = payload / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(source_path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, 'rb') as reader:
            before = os.fstat(reader.fileno())
            require(stat.S_ISREG(before.st_mode) and before.st_nlink == 1 and before.st_size == entry['bytes'], 'source artifact changed')
            output = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, entry['mode'] & ~0o222)
            digest, count = hashlib.sha256(), 0
            with os.fdopen(output, 'wb') as writer:
                os.fchmod(writer.fileno(), entry['mode'] & ~0o222)
                while chunk := reader.read(1024 * 1024):
                    count += len(chunk)
                    require(count <= entry['bytes'], 'source artifact grew')
                    digest.update(chunk)
                    writer.write(chunk)
                require(count == entry['bytes'] and digest.hexdigest() == entry['sha256'], 'artifact changed while staging')
                after = os.fstat(reader.fileno())
                require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                        (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'source artifact changed during copy')
                writer.flush()
                os.fsync(writer.fileno())
        expected['payload/' + entry['path']] = dict(bytes=entry['bytes'], sha256=entry['sha256'], mode=entry['mode'] & ~0o222)
    for name, data in (('manifest.json', raw), ('selection.json', selection)):
        write_new(destination / name, data, 0o444)
        expected[name] = dict(bytes=len(data), sha256=hashlib.sha256(data).hexdigest(), mode=0o444)
    for directory, _, _ in os.walk(payload, topdown=False):
        os.chmod(directory, 0o555)
        sync_directory(Path(directory))
    os.chmod(destination, 0o500)
    sync_directory(destination)
    sync_directory(destination.parent)
    return expected


def verify_sealed(directory, expected):
    found = set()
    for parent, children, names in os.walk(directory, followlinks=False):
        info = Path(parent).lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and not info.st_mode & 0o222, 'unsealed directory')
        require(not any((Path(parent) / name).is_symlink() for name in children), 'linked staged directory')
        for name in names:
            path = Path(parent) / name
            relative = path.relative_to(directory).as_posix()
            require(relative in expected, 'unexpected staged artifact')
            record = expected[relative]
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(descriptor, 'rb') as stream:
                info = os.fstat(stream.fileno())
                require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                        and stat.S_IMODE(info.st_mode) == record['mode'] and info.st_size == record['bytes'], 'unsafe staged artifact')
                digest = hashlib.sha256()
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                require(digest.hexdigest() == record['sha256'], 'staged artifact hash mismatch')
            found.add(relative)
    require(found == set(expected), 'missing staged artifact')
    manifest, manifest_digest = read_metadata(directory / 'manifest.json', 0o444)
    selection, selection_digest = read_metadata(directory / 'selection.json', 0o444)
    require(manifest_digest == expected['manifest.json']['sha256'] and selection_digest == expected['selection.json']['sha256'],
            'sealed metadata changed during verification')
    require(type(manifest) is dict and type(manifest.get('files')) is list
            and canonical(selection) == canonical(manifest.get('selection')), 'sealed manifest selection mismatch')
    inventory = {}
    for entry in manifest['files']:
        require(type(entry) is dict and type(entry.get('path')) is str and type(entry.get('mode')) is int
                and entry['mode'] in {0o644, 0o755} and type(entry.get('bytes')) is int and entry['bytes'] >= 0
                and type(entry.get('sha256')) is str and re.fullmatch(r'[0-9a-f]{64}', entry['sha256']), 'invalid sealed inventory')
        name = 'payload/' + entry['path']
        require(name not in inventory, 'duplicate sealed inventory path')
        inventory[name] = dict(bytes=entry['bytes'], sha256=entry['sha256'], mode=entry['mode'] & ~0o222)
    require(set(expected) == {'manifest.json', 'selection.json'} | set(inventory)
            and all(expected[name] == value for name, value in inventory.items()), 'sealed inventory differs from manifest')


def read_metadata(path, mode):
    require(not any(component.is_symlink() for component in (path, *path.parents)), 'linked update metadata')
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == os.geteuid()
                and stat.S_IMODE(info.st_mode) == mode and info.st_size <= LIMIT, 'unsafe update metadata')
        raw = stream.read(LIMIT + 1)
        require(len(raw) == info.st_size, 'update metadata changed')
    return strict_json(raw), hashlib.sha256(raw).hexdigest()


def inspect_stage(directory):
    directory = safe_root(directory)
    journal = directory / 'journal'
    names = sorted(path.name for path in journal.iterdir())
    require(1 <= len(names) <= 4 and names == [f'{index:06d}.json' for index in range(1, len(names) + 1)], 'incomplete journal sequence')
    previous, binding, phases = None, None, []
    for index, name in enumerate(names, 1):
        record, digest = read_metadata(journal / name, 0o600)
        require(type(record) is dict and set(record) == {'schema', 'revision', 'previous_sha256', 'binding', 'phase', 'details'}
                and record['schema'] == 'sage.lantern.update-journal.v1' and type(record['revision']) is int
                and record['revision'] == index and record['previous_sha256'] == previous, 'journal chain mismatch')
        binding = record['binding'] if binding is None else binding
        require(record['binding'] == binding, 'journal update binding changed')
        phases.append(record['phase'])
        previous = digest
    require(type(binding) is dict and set(binding) == {'transaction_id', 'unit_id', 'candidate_manifest_sha256', 'rollback_manifest_sha256'}
            and binding['unit_id'] in {'unit-a', 'unit-b'} and type(binding['transaction_id']) is str
            and str(uuid.UUID(binding['transaction_id'])) == binding['transaction_id'] == directory.name,
            'invalid staging transaction binding')
    require(type(binding['candidate_manifest_sha256']) is str and re.fullmatch(r'[0-9a-f]{64}', binding['candidate_manifest_sha256'])
            and (binding['rollback_manifest_sha256'] is None or (type(binding['rollback_manifest_sha256']) is str
            and re.fullmatch(r'[0-9a-f]{64}', binding['rollback_manifest_sha256']))), 'invalid bound manifest hash')
    sequence = ['staging_started', 'candidate_sealed'] + (['rollback_sealed'] if binding['rollback_manifest_sha256'] else []) + ['staged_no_authority']
    require(phases == sequence[:len(phases)], 'invalid journal transition')
    if phases[-1] != 'staged_no_authority':
        return dict(complete=False, phase=phases[-1], activation_authorized=False, reconciliation_required=True)
    seal, seal_digest = read_metadata(directory / 'stage.json', 0o444)
    require(type(seal) is dict and set(seal) == {'schema', 'binding', 'candidate', 'rollback', 'rollback_artifacts_sha256',
            'activation_authorized', 'aggregate_authority', 'unknown_queues', 'installed_baseline_verified', 'ready'}
            and seal['schema'] == 'sage.lantern.update-stage.v1' and seal['binding'] == binding
            and record['details'] == {'stage_sha256': seal_digest, 'activation_authorized': False}
            and seal['activation_authorized'] is False and seal['aggregate_authority'] == 'not_integrated'
            and seal['installed_baseline_verified'] is False and seal['unknown_queues'] == 'not_inspected' and seal['ready'] is False,
            'unsupported update authorization')
    require(seal['candidate']['manifest.json']['sha256'] == binding['candidate_manifest_sha256'], 'candidate manifest binding mismatch')
    verify_sealed(directory / 'candidate', seal['candidate'])
    rollback, rollback_digest = read_metadata(directory / 'rollback-artifacts.json', 0o444)
    require(rollback_digest == seal['rollback_artifacts_sha256'] and rollback['binding'] == binding
            and rollback['files'] == seal['rollback'] and rollback['installed_baseline_verified'] is False
            and rollback['restore_state'] is False and rollback['automatic_rollback'] is False, 'rollback manifest mismatch')
    if seal['rollback'] is not None:
        require(seal['rollback']['manifest.json']['sha256'] == binding['rollback_manifest_sha256'] and rollback['available'] is True,
                'rollback manifest binding mismatch')
        verify_sealed(directory / 'rollback', seal['rollback'])
    else:
        require(binding['rollback_manifest_sha256'] is None and rollback['available'] is False, 'missing rollback artifacts')
    return dict(complete=True, phase='staged_no_authority', activation_authorized=False, ready=False,
                rollback_available=seal['rollback'] is not None, installed_baseline_verified=False)


def stage_update(args):
    require(args.unit_id in {'unit-a', 'unit-b'}, 'explicit unit required')
    require(bool(args.rollback_output) == bool(args.rollback_manifest_sha256), 'rollback output/hash must be paired')
    for value in (args.output, args.stage_root, *([args.rollback_output] if args.rollback_output else [])):
        lexical_path(value)
    candidate = checked_output(args.output, args.manifest_sha256, current=True)
    rollback = checked_output(args.rollback_output, args.rollback_manifest_sha256, current=False) if args.rollback_output else None
    root = safe_root(args.stage_root)
    for selected in (candidate, rollback):
        if selected:
            require(root != selected[0] and selected[0] not in root.parents and root not in selected[0].parents, 'staging and source trees overlap')
    transaction = str(uuid.uuid4())
    destination = root / transaction
    destination.mkdir(mode=0o700)
    sync_directory(root)
    descriptor = os.open(destination / 'owner.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        binding = dict(transaction_id=transaction, unit_id=args.unit_id, candidate_manifest_sha256=args.manifest_sha256,
                       rollback_manifest_sha256=args.rollback_manifest_sha256)
        journal = UpdateJournal(destination / 'journal', binding)
        journal.append('staging_started', dict(activation_authorized=False))
        candidate_files = copy_artifacts(candidate, destination / 'candidate')
        verify_sealed(destination / 'candidate', candidate_files)
        checked_output(args.output, args.manifest_sha256, current=True)
        journal.append('candidate_sealed', dict(manifest_sha256=args.manifest_sha256))
        rollback_files = None
        if rollback:
            rollback_files = copy_artifacts(rollback, destination / 'rollback')
            verify_sealed(destination / 'rollback', rollback_files)
            checked_output(args.rollback_output, args.rollback_manifest_sha256, current=False)
            journal.append('rollback_sealed', dict(manifest_sha256=args.rollback_manifest_sha256))
        rollback_manifest = dict(schema='sage.lantern.rollback-artifacts.v1', binding=binding, available=rollback is not None,
                                 installed_baseline_verified=False, files=rollback_files,
                                 restore_state=False, automatic_rollback=False)
        write_new(destination / 'rollback-artifacts.json', canonical(rollback_manifest), 0o444)
        seal = dict(schema='sage.lantern.update-stage.v1', binding=binding, candidate=candidate_files, rollback=rollback_files,
                    rollback_artifacts_sha256=hashlib.sha256(canonical(rollback_manifest)).hexdigest(),
                    activation_authorized=False, aggregate_authority='not_integrated', unknown_queues='not_inspected',
                    installed_baseline_verified=False, ready=False)
        write_new(destination / 'stage.json', canonical(seal), 0o444)
        journal.append('staged_no_authority', dict(stage_sha256=hashlib.sha256(canonical(seal)).hexdigest(), activation_authorized=False))
        return dict(transaction_id=transaction, stage=str(destination), staged=True, activation_authorized=False, ready=False)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inspect', help='Read-only rehash and journal verification of an existing staging transaction')
    parser.add_argument('--output')
    parser.add_argument('--manifest-sha256')
    parser.add_argument('--stage-root')
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b'))
    parser.add_argument('--rollback-output')
    parser.add_argument('--rollback-manifest-sha256')
    args = parser.parse_args(argv)
    try:
        if args.inspect:
            require(not any((args.output, args.manifest_sha256, args.stage_root, args.unit_id, args.rollback_output, args.rollback_manifest_sha256)), 'inspect cannot stage')
            result = inspect_stage(args.inspect)
        else:
            require(all((args.output, args.manifest_sha256, args.stage_root, args.unit_id)), 'stage inputs required')
            result = stage_update(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError):
        print('Update staging incomplete. Preserve journal/partial artifacts; activation is unavailable.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
