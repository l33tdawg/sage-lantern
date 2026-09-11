#!/usr/bin/env python3
"""Continue only the reviewed three-moves/reset-broker-intent checkpoint.

Old eca60 journals remain immutable; no old done record is manufactured. The
observed inactive broker is adopted as a bootstrap prerequisite, not reported
as a successful reset command. Default read-only; --apply creates a separate
exclusive attempt. Only voice reset, exact guard release and broker-owned
one-shot bootstrap remain. No moves, recapture, auth/model/native restart.

Checkpoint fields: schema, helper {path,sha256}, expected {path,sha256},
journals {exact seven basenames:sha256}, trees (eca60 initial_trees metadata),
broker_exit (exact EXIT_FIELDS). Original expected and helper hashes are fixed.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import types


STATE = Path('/var/lib/sage-lantern-resident-voice-reset-continuation')
OLD_STATE = Path('/var/lib/sage-lantern-resident-voice-reactivation')
HELPER_SHA = 'eca60cbd9fda897477605ceb12b9df128faeb5f903fa6a6a1e406ecea7a13c26'
EXPECTED_SHA = 'f45e1a98f4e479264d30637cf3ad6611472e3433b00bad86b80b66b4daf33797'
PHASES = (
    'relocate_sage-lantern-mesh-supervisor_intent',
    'relocate_sage-lantern-mesh-supervisor_done',
    'relocate_sage-lantern-voice_intent',
    'relocate_sage-lantern-voice_done',
    'relocate_sage-lantern-voice-bootstrap.json_intent',
    'relocate_sage-lantern-voice-bootstrap.json_done',
    'reset_sage-lantern-mesh-runtime.service_intent',
)
JOURNALS = {f'{index:02d}-{phase}.json': phase for index, phase in enumerate(PHASES, 1)}


def require(value):
    if not value:
        raise ValueError('resident_voice_checkpoint_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


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
        fields = ('st_dev', 'st_ino', 'st_uid', 'st_gid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
                for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def pin(value):
    require(set(value) == {'path', 'sha256'})
    raw = read(value['path'])
    require(sha(raw) == value['sha256'])
    return raw


def validate(checkpoint):
    require(set(checkpoint) == {'schema', 'helper', 'expected', 'journals', 'trees', 'broker_exit'}
            and checkpoint['schema'] == 'sage.lantern.resident-voice-reset-continuation.v1'
            and checkpoint['helper']['sha256'] == HELPER_SHA
            and checkpoint['expected']['sha256'] == EXPECTED_SHA
            and set(checkpoint['journals']) == set(JOURNALS)
            and type(checkpoint['trees']) is dict and bool(checkpoint['trees']))
    require(all(type(value) is str and len(value) == 64 for value in checkpoint['journals'].values()))


def old_journals(base, checkpoint, backend):
    info = OLD_STATE.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    require({entry.name for entry in OLD_STATE.iterdir()} == set(JOURNALS) | {'expected.json', 'containment.json', 'supervisor'})
    require(sha(read(OLD_STATE / 'expected.json')) == EXPECTED_SHA)
    require(sha(read(OLD_STATE / 'containment.json')) == backend.expected['containment']['sha256'])
    for name, phase in JOURNALS.items():
        raw = read(OLD_STATE / name)
        require(sha(raw) == checkpoint['journals'][name])
        require(decode(raw) == dict(phase=phase, expected_sha256=EXPECTED_SHA, details=None))
    require(pin(checkpoint['helper']) == read(base.__file__))
    require(sha(pin(checkpoint['expected'])) == EXPECTED_SHA)


def broker_inactive(base, backend, checkpoint):
    observed = backend.backend.properties(base.BROKER, base.EXIT_FIELDS)
    before = checkpoint['broker_exit']
    require(before['ActiveState'] == 'inactive' and before['SubState'] == 'dead' and before['Result'] == 'success')
    base.exited(observed, before, True)
    require(observed['ActiveState'] == 'inactive'
            and observed['SubState'] == 'dead' and observed['MainPID'] == '0'
            and observed['NRestarts'] == '0' and observed['Result'] == 'success')
    base.exited(observed, backend.expected['exited_services'][base.BROKER], True)
    backend.backend.empty(base.BROKER)
    backend.owner.inactive(base.BROKER)


def construct(base, checkpoint):
    class Continuation(base.Native):
        def current(self):
            old_journals(base, checkpoint, self)
            broker_inactive(base, self, checkpoint)
            super().current()

        def release(self, service):
            require(service in (base.BROKER, base.VOICE) and self.reset == {base.BROKER, base.VOICE}
                    and service not in self.released)
            self.reconcile()
            path = self.recovery.GUARDS[service]
            raw = base.trusted(path)
            require(sha(raw) == self.receipt['guards'][str(path)])
            base.write(STATE / (service + '.guard'), raw)
            self.current()
            path.unlink()
            base.sync(path.parent)
            self.released.add(service)
            self.command(['daemon-reload'])

    backend = Continuation(decode(pin(checkpoint['expected'])))
    require(base.STATE == OLD_STATE and set(backend.destinations) == {base.SUPERVISOR, base.VOICE_RUN, base.BOOTSTRAP})
    backend.moved = set(backend.destinations)
    backend.initial_trees = checkpoint['trees']
    backend.reset = {base.BROKER}
    return backend


def transition(base, backend, record):
    backend.current()
    record('adopt_observed_inactive_broker', {'reset_command_performed': False, 'old_command_outcome': 'unknown'})
    require(backend.expected['exited_services'][base.VOICE]['ActiveState'] == 'failed')
    record('reset_voice_intent')
    backend.reset_service(base.VOICE)
    backend.current()
    record('reset_voice_done')
    for service in (base.BROKER, base.VOICE):
        backend.current()
        record('release_' + service + '_intent')
        backend.release(service)
        backend.current()
        record('release_' + service + '_done')
    backend.reconcile()
    backend.owner.fresh()
    record('new_lifetime_start_intent')
    result = backend.owner.start()
    record('new_lifetime_start_done', result)
    return dict(result, old_reset_command_outcome='unknown', broker_reset_command_performed=False,
                historical_work_disposition='unknown', old_drain_verified=False, automatic_retry=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    raw = pin({'path': args.checkpoint, 'sha256': args.checkpoint_sha256})
    checkpoint = decode(raw)
    validate(checkpoint)
    source = pin(checkpoint['helper'])
    base = types.ModuleType('frozen_resident_reactivation')
    base.__file__ = checkpoint['helper']['path']
    exec(compile(source, base.__file__, 'exec'), base.__dict__)
    backend = construct(base, checkpoint)
    try:
        backend.current()
        lock = backend.location(base.SUPERVISOR / 'owner.lock')
        backend.lock = os.open(lock, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        fcntl.flock(backend.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        backend.reconcile()
        if not args.apply:
            print(json.dumps(dict(checkpoint_verified=True, started=False, old_command_outcome='unknown')))
            return
        base.parents(STATE)
        STATE.mkdir(mode=0o700)
        base.sync(STATE.parent)
        base.write(STATE / 'checkpoint.json', raw)
        sequence = 0
        def record(phase, details=None):
            nonlocal sequence
            sequence += 1
            base.write(STATE / f'{sequence:02d}-{phase}.json', base.canonical(dict(phase=phase,
                checkpoint_sha256=args.checkpoint_sha256, details=details)))
        print(json.dumps(transition(base, backend, record), sort_keys=True))
    finally:
        backend.close()


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, recovery_required=True, automatic_retry=False)))
        raise SystemExit(2) from None
