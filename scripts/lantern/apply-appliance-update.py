#!/usr/bin/env python3
"""Inspect a bounded UI artifact update; production apply remains queue-blocked.

No installer, candidate code, build, migration, restart or recovery is executed.
The copy engine is not full matched-release activation. Its production entry
requires all preflight blockers cleared; no queue-authority API exists yet.
"""

import argparse
from dataclasses import asdict
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import stat
import sys
import types
import uuid


ROOT = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location('apply_stage', ROOT / 'scripts/lantern/stage-appliance-update.py')
STAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STAGE)
MANIFEST = STAGE.helper('apply_manifest', 'scripts/appliance-release-manifest.py')
APP = 'apps/lantern-fleet-admin/'
DESTINATION = Path('/opt/sage-lantern/fleet-admin')
RUNNER = Path('/usr/local/libexec/sage-lantern-idle-suspend')
MAX_COPY = 32 * 1024 * 1024
MAX_CHANGES = 128
require = STAGE.require


def trusted_directory(path):
    for component in (path, *path.parents):
        info = component.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, os.geteuid()}
                and (not info.st_mode & 0o022 or info.st_uid == 0 and info.st_mode & stat.S_ISVTX),
                'untrusted artifact directory')


def observed_file(path, expected, *, capture=False):
    trusted_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as stream:
        before = os.fstat(stream.fileno())
        require(before.st_uid == os.geteuid() and stat.S_ISREG(before.st_mode)
                and before.st_nlink == 1 and before.st_size == expected['bytes']
                and stat.S_IMODE(before.st_mode) == expected['mode'], 'installed artifact metadata mismatch')
        digest, count, chunks = hashlib.sha256(), 0, []
        while chunk := stream.read(1024 * 1024):
            count += len(chunk)
            require(count <= expected['bytes'], 'artifact grew')
            digest.update(chunk)
            if capture:
                require(count <= MAX_COPY, 'copy exceeds mutation bound')
                chunks.append(chunk)
        require(count == expected['bytes'] and digest.hexdigest() == expected['sha256'], 'installed artifact hash mismatch')
        identity = MANIFEST.identity(before)
        require(identity == MANIFEST.identity(os.fstat(stream.fileno())) == MANIFEST.identity(path.lstat()),
                'artifact changed during observation')
    return identity, b''.join(chunks)


def entries(manifest):
    result = {}
    for entry in manifest['files']:
        name = MANIFEST.safe_name(entry['path'])
        require(name not in result, 'duplicate artifact')
        result[name] = entry
    return result


def inspect_binding(args):
    directory = STAGE.lexical_path(args.stage)
    status = STAGE.inspect_stage(directory)
    require(status['complete'] and status['rollback_available'], 'complete candidate and rollback stage required')
    seal, digest = STAGE.read_metadata(directory / 'stage.json', 0o444)
    binding = seal['binding']
    require(binding['unit_id'] == args.unit_id
            and binding['candidate_manifest_sha256'] == args.candidate_manifest_sha256
            and binding['rollback_manifest_sha256'] == args.rollback_manifest_sha256,
            'reviewed staging binding mismatch')
    candidate, _ = STAGE.read_metadata(directory / 'candidate/manifest.json', 0o444)
    rollback, _ = STAGE.read_metadata(directory / 'rollback/manifest.json', 0o444)
    return directory, binding, digest, entries(candidate), entries(rollback)


def inspect_installed(expected, *, destination=DESTINATION):
    trusted_directory(destination)
    found, identities = set(), {}
    count = 0
    for parent, children, names in os.walk(destination, followlinks=False):
        trusted_directory(Path(parent))
        count += len(children) + len(names)
        require(count <= MANIFEST.MAX_FILES * 2, 'installed tree exceeds bound')
        for child in children:
            trusted_directory(Path(parent) / child)
        for name in names:
            path = Path(parent) / name
            relative = path.relative_to(destination).as_posix()
            require(relative in expected, 'untracked installed UI artifact; preserve and reconcile')
            identities[relative] = observed_file(path, expected[relative])[0]
            found.add(relative)
    require(found == set(expected), 'installed UI baseline incomplete')
    return identities


def preflight(args, *, destination=DESTINATION):
    directory, binding, seal_digest, candidate, rollback = inspect_binding(args)
    require(set(candidate) == set(rollback), 'artifact additions/removals unsupported')
    changes = []
    for name in sorted(candidate):
        if candidate[name] == rollback[name] or name == 'release/destinations.json':
            continue
        require(name.startswith(APP) and candidate[name].get('role') == rollback[name].get('role') == 'ui',
                'changed non-UI artifact requires separate reviewed full-release apply')
        relative = name.removeprefix(APP)
        require(relative in {'package.json', 'package-lock.json'}
                or relative.startswith(('dist/', 'public/', 'node_modules/')), 'unsupported UI destination')
        require(candidate[name]['bytes'] <= MAX_COPY, 'artifact exceeds bounded copy size')
        changes.append(dict(path=relative, before=rollback[name], after=candidate[name]))
    require(1 <= len(changes) <= MAX_CHANGES, 'one to 128 existing UI artifact changes required')
    baseline = {name.removeprefix(APP): entry for name, entry in rollback.items() if entry.get('role') == 'ui'}
    require(baseline and all(name.startswith(APP) for name, entry in rollback.items() if entry.get('role') == 'ui'),
            'invalid UI baseline inventory')
    identities = inspect_installed(baseline, destination=destination)
    return dict(schema='sage.lantern.update-apply-plan.v1', binding=binding, stage_sha256=seal_digest,
                scope='existing-ui-artifacts-only', installed_ui_baseline_verified=True,
                installed_baseline_verified=False, changes=changes, baseline=baseline,
                identities=identities, queue_status='not_inspected',
                blockers=['all_backend_queue_authority_unavailable', 'full_installed_baseline_not_verified'],
                activation_authorized=False, ready=False)


class ApplyJournal:
    def __init__(self, directory, binding):
        self.directory, self.binding = directory, binding
        self.revision, self.previous = 0, None
        directory.mkdir(mode=0o700)
        STAGE.sync_directory(directory.parent)

    def append(self, phase, details):
        require(phase in {'apply_started', 'drain_prepared', 'copy_intent', 'copy_done', 'artifacts_copied_fenced'},
                'unsupported apply journal phase')
        record = dict(schema='sage.lantern.update-apply-journal.v1', revision=self.revision + 1,
                      previous_sha256=self.previous, binding=self.binding, phase=phase, details=details)
        raw = STAGE.canonical(record)
        STAGE.write_new(self.directory / f'{self.revision + 1:06d}.json', raw)
        self.revision += 1
        self.previous = hashlib.sha256(raw).hexdigest()


def inspect_apply(directory):
    directory = STAGE.safe_root(directory)
    names = sorted(path.name for path in directory.iterdir())
    require(1 <= len(names) <= 2 * MAX_CHANGES + 3
            and names == [f'{index:06d}.json' for index in range(1, len(names) + 1)], 'invalid apply journal sequence')
    previous, binding, pending, copied, planned = None, None, None, [], []
    for index, name in enumerate(names, 1):
        record, digest = STAGE.read_metadata(directory / name, 0o600)
        require(set(record) == {'schema', 'revision', 'previous_sha256', 'binding', 'phase', 'details'}
                and record['schema'] == 'sage.lantern.update-apply-journal.v1'
                and type(record['revision']) is int and record['revision'] == index
                and record['previous_sha256'] == previous, 'apply journal chain mismatch')
        binding = record['binding'] if binding is None else binding
        require(record['binding'] == binding, 'apply journal binding changed')
        phase, details = record['phase'], record['details']
        if index == 1:
            require(phase == 'apply_started', 'missing apply start')
            require(binding == dict(details['binding'], stage_sha256=details['stage_sha256'])
                    and details['activation_authorized'] is False and details['ready'] is False,
                    'apply plan binding mismatch')
            planned = details['changes']
            require(1 <= len(planned) <= MAX_CHANGES, 'invalid planned copy count')
        elif index == 2:
            require(phase == 'drain_prepared', 'missing native admission record')
            require(details['candidate_sha256'] == binding['candidate_manifest_sha256']
                    and details['rollback_sha256'] == binding['rollback_manifest_sha256'], 'drain manifest binding mismatch')
        elif phase == 'copy_intent':
            require(pending is None and details['path'] not in copied, 'overlapping copy intent')
            require(len(copied) < len(planned) and set(details) == {'path', 'temporary', 'before', 'after'}
                    and {key: details[key] for key in ('path', 'before', 'after')} == planned[len(copied)]
                    and details['temporary'].startswith('.sage-update-')
                    and str(uuid.UUID(details['temporary'].removeprefix('.sage-update-'))) == details['temporary'].removeprefix('.sage-update-'),
                    'copy intent differs from bound plan')
            pending = details
        elif phase == 'copy_done':
            require(pending is not None and details == pending, 'copy completion without matching intent')
            copied.append(details['path'])
            pending = None
        else:
            require(phase == 'artifacts_copied_fenced' and index == len(names) and pending is None
                    and len(copied) == len(planned)
                    and details == {'copied': copied, 'activation_authorized': False}, 'invalid apply completion')
        previous = digest
    return dict(binding=binding, last_phase=phase, pending_intent=pending, recorded_copies=copied,
                reconciliation_required=True, activation_authorized=False, ready=False)


def load_installed_runner(expected):
    trusted_directory(RUNNER.parent)
    info = RUNNER.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
            and stat.S_IMODE(info.st_mode) == 0o755, 'trusted installed suspend runner required; bootstrap separately')
    _, raw = observed_file(RUNNER, dict(expected, mode=0o755), capture=True)
    module = types.ModuleType('installed_update_suspend')
    module.__file__ = str(RUNNER)
    exec(compile(raw, str(RUNNER), 'exec'), module.__dict__)
    return module


def _copy_admitted(args, plan, journal, *, destination=DESTINATION):
    """Internal composition, not an admission bypass or public activation API.

    Caller must enforce production blockers. Tests mock native acquisition only;
    this engine cannot establish queue coverage or whole-appliance compatibility.
    """
    require(plan['blockers'] == [], 'production admission blockers remain')
    from sage_lantern.deployment_drain import DeploymentDrain, verify_root_broker

    pinned = dict(plan['binding'])
    hashes = dict(candidate_sha256=pinned['candidate_manifest_sha256'], rollback_sha256=pinned['rollback_manifest_sha256'])

    def verify_binding(candidate_sha256, rollback_sha256):
        _, binding, digest, _, _ = inspect_binding(args)
        require(binding == pinned and digest == plan['stage_sha256']
                and candidate_sha256 == hashes['candidate_sha256'] and rollback_sha256 == hashes['rollback_sha256'],
                'apply stage binding changed')
        return True

    _, _, _, _, rollback = inspect_binding(args)
    runner = load_installed_runner(rollback['scripts/lantern/lantern-idle-suspend.py'])
    authority = DeploymentDrain.acquire(runner, **hashes, verify_binding=verify_binding, verify_broker=verify_root_broker)
    try:
        evidence = authority.prepare()
        journal.append('drain_prepared', asdict(evidence))
        expected = dict(plan['baseline'])
        require(inspect_installed(expected, destination=destination) == plan['identities'], 'installed baseline changed before apply')

        def mutate(lease):
            copied = []
            def current():
                require(lease.assert_current(**hashes) is True, 'live deployment authority unavailable')
            for change in plan['changes']:
                relative = change['path']
                target = destination / relative
                temporary = target.with_name('.sage-update-' + str(uuid.uuid4()))
                details = dict(path=relative, temporary=temporary.name, before=change['before'], after=change['after'])
                current()
                observed_file(target, expected[relative])
                source = Path(args.stage) / 'candidate/payload' / change['after']['path']
                sealed = dict(change['after'], mode=change['after']['mode'] & ~0o222)
                _, raw = observed_file(source, sealed, capture=True)
                journal.append('copy_intent', details)
                current()
                STAGE.write_new(temporary, raw, change['after']['mode'])
                current()
                observed_file(target, expected[relative])
                observed_file(temporary, change['after'])
                current()
                os.replace(temporary, target)
                STAGE.sync_directory(target.parent)
                observed_file(target, change['after'])
                current()
                journal.append('copy_done', details)
                expected[relative] = change['after']
                copied.append(relative)
            inspect_installed(expected, destination=destination)
            return copied

        copied = authority.run(mutate)
        journal.append('artifacts_copied_fenced', dict(copied=copied, activation_authorized=False))
        return dict(artifacts_copied=copied, activation_authorized=False, ready=False, reconciliation_required=True)
    finally:
        authority.close()


def apply_update(args):
    require(os.geteuid() == 0 and sys.platform == 'linux', 'root Linux required')
    plan = preflight(args)
    require(not plan['blockers'], 'apply unavailable: ' + ', '.join(plan['blockers']))
    directory = STAGE.safe_root(args.stage)
    trusted_directory(directory)
    descriptor = os.open(directory / 'apply.lock', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
        STAGE.sync_directory(directory)
        plan = preflight(args)
        require(not plan['blockers'], 'apply admission changed')
        journal = ApplyJournal(directory / 'apply-journal', dict(plan['binding'], stage_sha256=plan['stage_sha256']))
        journal.append('apply_started', plan)
        return _copy_admitted(args, plan, journal)
    finally:
        os.close(descriptor)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--inspect', action='store_true')
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--inspect-journal')
    parser.add_argument('--stage')
    parser.add_argument('--unit-id', choices=('unit-a', 'unit-b', 'unit-c', 'unit-d', 'unit-e', 'unit-f'))
    parser.add_argument('--candidate-manifest-sha256')
    parser.add_argument('--rollback-manifest-sha256')
    args = parser.parse_args(argv)
    try:
        if args.inspect_journal:
            require(not any((args.stage, args.unit_id, args.candidate_manifest_sha256, args.rollback_manifest_sha256)),
                    'journal inspection takes no apply inputs')
            result = inspect_apply(args.inspect_journal)
        else:
            require(all((args.stage, args.unit_id, args.candidate_manifest_sha256, args.rollback_manifest_sha256)), 'explicit reviewed bindings required')
            result = preflight(args) if args.inspect else apply_update(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ValueError, OSError, KeyError, TypeError, RuntimeError, ImportError):
        print('Apply unavailable or uncertain. Preserve artifacts/journals/fences; no automatic restart or rollback. '
              'All-backend queue authority and full installed baseline remain unavailable; old broker/guards need separate reviewed bootstrap.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
