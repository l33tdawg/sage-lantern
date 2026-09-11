#!/usr/bin/env python3
"""Explicit continuation of archive_done followed by retained-failure guards.

No coordinator retry, historical drain assertion, recapture, or ledger writes.
Original records and archived supervisor stay unchanged. The pinned original
helper owns remaining startup and failure containment. Default is read-only;
apply creates a separate exclusive per-boot journal. No automatic retry.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import types
import uuid


ROOT = Path('/var/lib/sage-lantern-cross-boot-audio-continuation')
ORIGINAL_HELPER_SHA = 'ed7af4161460e2e72e20a38c4394e12a15fb432b485a2a442e7d7e12a32958e0'
PHASES = ('inhibit_intent', 'inhibit_done', 'archive_intent', 'archive_done',
          'retain_failure_guards_intent', 'retain_failure_guards_done')
FILES = {f'{index:02d}-{phase}.json' for index, phase in enumerate(PHASES, 1)} | {
    'expected.json', 'adapter-policy.json', 'prior-lifetime.json'}


def require(value):
    if not value:
        raise ValueError('cross_boot_continuation_refused')


def unique(items):
    result = {}
    for name, value in items:
        require(name not in result)
        result[name] = value
    return result


def read(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
                for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = read(pin['path'])
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'])
    return raw


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def load(pin):
    raw = pinned(pin)
    module = types.ModuleType('original_cross_boot_helper')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def original_helper(policy):
    require(policy['helper']['sha256'] == ORIGINAL_HELPER_SHA)
    helper = load(policy['helper'])
    require(not hasattr(helper, 'sleep_idle_batch'))
    return helper


def validate_checkpoint(checkpoint):
    require(type(checkpoint) is dict and set(checkpoint) == {
        'schema', 'boot_id', 'access_epoch', 'original_files', 'coordinator_journal'})
    require(checkpoint['schema'] == 'sage.lantern.archive-complete-continuation.v1'
            and str(uuid.UUID(checkpoint['boot_id'])) == checkpoint['boot_id'])
    require(type(checkpoint['access_epoch']) is str and len(checkpoint['access_epoch']) == 32
            and all(value in '0123456789abcdef' for value in checkpoint['access_epoch']))
    require(type(checkpoint['original_files']) is dict and set(checkpoint['original_files']) == FILES)
    for digest in checkpoint['original_files'].values():
        require(type(digest) is str and len(digest) == 64
                and all(value in '0123456789abcdef' for value in digest))
    require(checkpoint['coordinator_journal']['path'] ==
            '/var/lib/sage-lantern-startup/' + checkpoint['boot_id'] + '.jsonl')


def originals(helper, checkpoint):
    state = helper.ATTEMPTS / checkpoint['boot_id']
    require({path.name for path in state.iterdir()} == FILES | {'supervisor'})
    values = {name: pinned(dict(path=str(state / name), sha256=digest))
              for name, digest in checkpoint['original_files'].items()}
    expected_sha = checkpoint['original_files']['expected.json']
    for index, phase in enumerate(PHASES, 1):
        row = decode(values[f'{index:02d}-{phase}.json'])
        require(set(row) == {'phase', 'expected_sha256', 'historical_outcomes', 'details'}
                and row['phase'] == phase and row['expected_sha256'] == expected_sha
                and row['historical_outcomes'] == 'unknown')
        if phase == 'retain_failure_guards_done':
            require(row['details']['guards_retained'] is True
                    and row['details']['containment_verified'] is False
                    and row['details']['producer_outcome'] == 'unverified'
                    and row['details']['historical_outcomes'] == 'unknown')
    journal = [decode(line) for line in pinned(checkpoint['coordinator_journal']).splitlines()]
    require([row['phase'] for row in journal] == ['intent', 'prior_lifetime_verified',
            'full_lifecycle_intent', 'full_lifecycle_outcome_unknown'])
    return values


def remaining(backend, record):
    backend.reconcile()
    record('model_intent')
    backend.model()
    backend.current()
    record('model_done')
    backend.reconcile()
    record('release_intent')
    backend.release()
    record('release_done')
    backend.reconcile()
    backend.owner.fresh()
    record('bootstrap_intent')
    backend.bootstrap_attempted = True
    result = backend.owner.start()
    record('bootstrap_done', dict(result, lifetime=backend.completed()))
    return result


def publish_latest(helper, checkpoint, checkpoint_raw, continuation, latest_sha256, apply):
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    from sage_lantern.device_access import access_epoch
    state = helper.ATTEMPTS / checkpoint['boot_id']
    for name, digest in checkpoint['original_files'].items():
        pinned(dict(path=str(state / name), sha256=digest))
    expected_raw = pinned(dict(path=str(state / 'expected.json'),
        sha256=checkpoint['original_files']['expected.json']))
    expected = decode(expected_raw)
    require(read(continuation / 'checkpoint.json') == checkpoint_raw)
    done = decode(read(continuation / '06-bootstrap_done.json'))
    completion = decode(read(continuation / 'complete.json'))
    lifetime_path = state / 'new-lifetime.json'
    lifetime_raw = read(lifetime_path)
    document = decode(lifetime_raw)
    require(done['phase'] == 'bootstrap_done' and done['checkpoint_sha256'] == helper.sha(checkpoint_raw)
            and done['historical_outcomes'] == 'unknown'
            and done['details']['lifetime'] == completion['lifetime'])
    require(completion['schema'] == 'sage.lantern.archive-complete-continuation-result.v1'
            and completion['boot_id'] == checkpoint['boot_id']
            and completion['original_expected_sha256'] == helper.sha(expected_raw)
            and completion['historical_outcomes'] == 'unknown' and completion['old_drain_verified'] is False)
    require(document['schema'] == 'sage.lantern.voice-bootstrap.v1' and document['phase'] == 'complete'
            and document['boot_id'] == checkpoint['boot_id'] and document['inventory'] == expected['inventory']
            and document['evidence'] == completion['lifetime']['broker']
            and document['evidence']['vault_epoch'] == expected['storage_epoch']
            and completion['lifetime']['lifetime_sha256'] == helper.sha(lifetime_raw))
    directory = Path('/var/lib/sage-lantern-startup')
    latest = directory / 'latest.json'
    previous = pinned(dict(path=str(latest), sha256=latest_sha256))
    require(pinned(decode(previous)) == read(state / 'prior-lifetime.json'))
    owner = ColdBrokerBootstrap(expected['inventory'])
    def current():
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == checkpoint['boot_id']
                and access_epoch() == checkpoint['access_epoch'])
        owner.inputs()
        coordinator = owner.system._show('sage-lantern-startup.service', owner.system.clock() + 5,
                                         ('ActiveState', 'MainPID', 'Job'))
        require(coordinator['ActiveState'] == 'failed' and coordinator['MainPID'] == '0'
                and owner.system._no_job(coordinator['Job']))
        require(owner.active() == document['evidence'] and read(lifetime_path) == lifetime_raw)
    current()
    require(not os.path.lexists(continuation / 'publish-latest-intent.json')
            and not os.path.lexists(continuation / 'publish-latest-done.json'))
    pin = dict(path=str(lifetime_path), sha256=helper.sha(lifetime_raw))
    if not apply:
        return dict(latest_publish_preflight=True, mutation_authorized=False, lifetime=pin)
    helper.write(continuation / 'publish-latest-intent.json', helper.canonical(dict(
        previous_sha256=latest_sha256, lifetime=pin, checkpoint_sha256=helper.sha(checkpoint_raw))))
    helper.write(continuation / 'previous-latest.json', previous)
    temporary = directory / (checkpoint['boot_id'] + '-continuation-next.json')
    helper.write(temporary, helper.canonical(pin))
    current()
    require(read(latest) == previous)
    os.replace(temporary, latest)
    helper.sync(directory)
    require(decode(read(latest)) == pin)
    helper.write(continuation / 'publish-latest-done.json', helper.canonical(pin))
    return dict(latest_published=True, lifetime=pin)


def reconstruct(helper, checkpoint, continuation):
    from sage_lantern import audio_readiness
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    from sage_lantern.device_access import access_epoch
    values = originals(helper, checkpoint)
    expected = decode(values['expected.json'])
    require(set(expected) == {'schema', 'boot_id', 'tree', 'inventory', 'runtime_sources', 'files',
        'commission_sha256', 'storage_epoch', 'workflow_sha256', 'actor', 'historical_outcomes',
        'old_boot_attribution', 'model_argv'} and expected['schema'] == 'sage.lantern.cross-boot-audio.v1'
        and expected['boot_id'] == checkpoint['boot_id'] and expected['historical_outcomes'] == 'unknown'
        and expected['old_boot_attribution'] == 'unverified')
    class Recovery(helper.Recovery):
        def current(self):
            require(access_epoch() == checkpoint['access_epoch'])
            originals(helper, checkpoint)
            coordinator = self.system._show('sage-lantern-startup.service', self.system.clock() + 5,
                                           ('ActiveState', 'MainPID', 'Job'))
            require(coordinator['ActiveState'] == 'failed' and coordinator['MainPID'] == '0'
                    and self.system._no_job(coordinator['Job']))
            super().current()
            require(access_epoch() == checkpoint['access_epoch'])

        def completed(self):
            require(access_epoch() == checkpoint['access_epoch'])
            evidence = self.owner.active()
            require(evidence['vault_epoch'] == expected['storage_epoch'])
            result = super().completed()
            require(result['broker'] == evidence and access_epoch() == checkpoint['access_epoch'])
            helper.write(continuation / 'complete.json', helper.canonical(dict(
                schema='sage.lantern.archive-complete-continuation-result.v1', boot_id=self.boot,
                original_expected_sha256=checkpoint['original_files']['expected.json'],
                historical_outcomes='unknown', old_drain_verified=False, lifetime=result)))
            return result

    backend = Recovery.__new__(Recovery)
    backend.expected = expected
    backend.boot = checkpoint['boot_id']
    backend.state = helper.ATTEMPTS / backend.boot
    backend.archived = True
    backend.guards = {helper.BROKER, helper.VOICE}
    backend.locks = []
    backend.model_started = False
    backend.model_identity = None
    backend.file_pins = {}
    backend.recorder_pin = None
    backend.audio = audio_readiness
    backend.owner = ColdBrokerBootstrap(expected['inventory'])
    backend.system = backend.owner.system
    backend.verify_sources()
    receipt = audio_readiness.commissioned_receipt()
    require(helper.sha(helper.canonical(receipt)) == expected['commission_sha256'])
    for collection in (expected['runtime_sources'], expected['files'], expected['inventory'], receipt['binding']['inputs']):
        for path, digest in collection.items():
            require(helper.file_hash(path) == digest)
            backend.file_pins[path] = helper.metadata(Path(path).lstat())
            if path == '/usr/bin/arecord':
                backend.recorder_pin = helper.recorder_binding()
    backend.current()
    return backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--publish-latest', action='store_true')
    parser.add_argument('--latest-sha256')
    args = parser.parse_args(argv)
    require(args.publish_latest == (args.latest_sha256 is not None))
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    def interrupted(signum, frame):
        raise InterruptedError('continuation_interrupted')
    for signum in (signal.SIGALRM, signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, interrupted)
    signal.alarm(300)
    backend = None
    try:
        raw = pinned(dict(path=args.checkpoint, sha256=args.checkpoint_sha256))
        checkpoint = decode(raw)
        validate_checkpoint(checkpoint)
        state = Path('/var/lib/sage-lantern-cross-boot-audio') / checkpoint['boot_id']
        policy = decode(pinned(dict(path=str(state / 'adapter-policy.json'),
            sha256=checkpoint['original_files']['adapter-policy.json'])))
        helper = original_helper(policy)
        require(set(policy) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
                and policy['schema'] == 'sage.lantern.startup-audio-policy.v1')
        baseline = decode(pinned(policy['baseline']))
        expected = decode(pinned(dict(path=str(state / 'expected.json'),
            sha256=checkpoint['original_files']['expected.json'])))
        for name in ('inventory', 'runtime_sources', 'files', 'commission_sha256', 'actor', 'model_argv'):
            require(expected[name] == baseline[name])
        site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
        collector = load(policy['collector'])
        collector.runtime_sources(helper, Path(policy['runtime_wheel']['path']), site)
        pinned(policy['runtime_wheel'])
        continuation = ROOT / checkpoint['boot_id']
        if args.publish_latest:
            print(json.dumps(publish_latest(helper, checkpoint, raw, continuation, args.latest_sha256, args.apply)))
            return
        require(not os.path.lexists(continuation))
        backend = reconstruct(helper, checkpoint, continuation)
        backend.lock()
        backend.reconcile()
        if not args.apply:
            print(json.dumps(dict(preflight_verified=True, mutation_authorized=False)))
            return
        helper.parents(ROOT)
        if not ROOT.exists():
            ROOT.mkdir(mode=0o700)
            helper.sync(ROOT.parent)
        info = ROOT.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        continuation.mkdir(mode=0o700)
        helper.sync(ROOT)
        helper.write(continuation / 'checkpoint.json', raw)
        sequence = 0
        def record(phase, details=None):
            nonlocal sequence
            sequence += 1
            helper.write(continuation / f'{sequence:02d}-{phase}.json', helper.canonical(dict(
                phase=phase, checkpoint_sha256=args.checkpoint_sha256, details=details,
                historical_outcomes='unknown')))
        transition = types.FunctionType(helper.transition.__code__, dict(helper.__dict__, execute=remaining))
        result = transition(backend, record)
        print(json.dumps(dict(result, old_drain_verified=False, historical_outcomes='unknown',
                              completion_path=str(continuation / 'complete.json'))))
    finally:
        if backend is not None:
            backend.close()
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, recovery_required=True, automatic_retry=False)))
        raise SystemExit(2) from None
