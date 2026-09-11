#!/usr/bin/env python3
"""Read-only diagnosis of the exact R2 resume checkpoint after status start.

Run installed Python -I -B. Never invokes main/start/release/install on either
maintenance helper. A single constructor current() call is deferred in memory;
the original method is restored before adopting independently pinned status
identity and running real process/current validation. No readiness authority.
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


RESUME_SHA = 'b1ee5d0873a1bf0b930bac3cc6f42cdba7abea68e46c9c8d60475baa3a2f4ab6'
STATUS = 'sage-lantern-status.service'


def require(value):
    if not value:
        raise ValueError('diagnostic_refused')


def source(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size < 262144)
        raw = os.read(descriptor, 262144)
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                    value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) == info.st_size and identity(info) == identity(os.fstat(descriptor))
                and identity(info) == identity(path.lstat()))
        require(hashlib.sha256(raw).hexdigest() == RESUME_SHA)
        return raw
    finally:
        os.close(descriptor)


def report(label, action):
    try:
        action()
        print(json.dumps(dict(check=label, passed=True, mutation_authorized=False)))
        return True
    except Exception as error:
        frames = []
        trace = error.__traceback__
        while trace is not None:
            frames.append(dict(file=trace.tb_frame.f_code.co_filename, line=trace.tb_lineno,
                               function=trace.tb_frame.f_code.co_name))
            trace = trace.tb_next
        print(json.dumps(dict(check=label, passed=False, error_type=type(error).__name__, frames=frames,
                              mutation_authorized=False)))
        return False


def diagnose(args):
    raw = source(args.resume_helper)
    resume = types.ModuleType('frozen_resume_diagnostic')
    resume.__file__ = args.resume_helper
    exec(compile(raw, args.resume_helper, 'exec'), resume.__dict__)
    checkpoint_raw = resume.pinned(dict(path=args.checkpoint, sha256=args.checkpoint_sha256))
    checkpoint = json.loads(checkpoint_raw)
    helper_raw = resume.pinned(checkpoint['original_helper'])
    require(hashlib.sha256(helper_raw).hexdigest() == resume.ORIGINAL_SHA)
    base = types.ModuleType('frozen_restore_diagnostic')
    base.__file__ = checkpoint['original_helper']['path']
    exec(compile(helper_raw, base.__file__, 'exec'), base.__dict__)
    checkpoint = base.decode(checkpoint_raw)
    require(resume.read(resume.STATE / 'checkpoint.json') == checkpoint_raw)
    phases = ['release_' + STATUS + '_intent',
        'remove_' + STATUS + '_zzzz-fresh-runtime-update.conf_intent',
        'remove_' + STATUS + '_zzzz-fresh-runtime-update.conf_done',
        'remove_' + STATUS + '_zzzz-fresh-runtime-r7-update.conf_intent',
        'remove_' + STATUS + '_zzzz-fresh-runtime-r7-update.conf_done',
        'release_' + STATUS + '_done', 'start_' + STATUS + '_intent']
    names = {f'{index:02d}-{phase}.json': phase for index, phase in enumerate(phases, 1)}
    require({entry.name for entry in resume.STATE.iterdir()} == set(names) | {'checkpoint.json'})
    journal = {}
    for name, phase in names.items():
        content = resume.read(resume.STATE / name)
        require(base.decode(content) == dict(phase=phase, checkpoint_sha256=args.checkpoint_sha256))
        journal[name] = hashlib.sha256(content).hexdigest()
    print(json.dumps(dict(check='resume_journal', sha256=journal, mutation_authorized=False)))
    factory = resume.resume_type
    kind = factory(base, checkpoint)
    original_current = kind.current
    deferred = []
    def defer(backend):
        deferred.append(True)
        require(len(deferred) == 1)
    kind.current = defer
    resume.resume_type = lambda loaded_base, loaded_checkpoint: kind
    try:
        backend = resume.build(base, checkpoint)
    finally:
        kind.current = original_current
        resume.resume_type = factory
    require(deferred == [True])
    observed = base.snapshot(backend.show(STATUS), STATUS)
    require(observed['MainPID'] == str(args.status_pid) and observed['InvocationID'] == args.status_invocation)
    backend.running[STATUS] = observed
    backend.ticks[STATUS] = args.status_start_ticks
    backend.released.add(STATUS)
    for path, raw, state in backend.guards(STATUS):
        require(not os.path.lexists(path))
        backend.removed.add(path)
    def process_check():
        process = backend.process(STATUS, backend.show(STATUS))
        require(process.pid == args.status_pid and process.start_ticks == args.status_start_ticks)
        require(base.snapshot(backend.show(STATUS), STATUS) == observed)
        print(json.dumps(dict(check='status_identity', pid=process.pid, start_ticks=process.start_ticks,
            uid=process.uid, invocation=observed['InvocationID'], executable_and_argv_verified=True,
            executable_inode_verified=True, cgroup_verified=True, mutation_authorized=False)))
    process_ok = report('status_process', process_check)
    state_ok = report('real_current', backend.current)
    for name, digest in journal.items():
        require(hashlib.sha256(resume.read(resume.STATE / name)).hexdigest() == digest)
    require(process_ok and state_ok)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--resume-helper', required=True)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--status-pid', required=True, type=int)
    parser.add_argument('--status-start-ticks', required=True, type=int)
    parser.add_argument('--status-invocation', required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and args.status_pid > 0 and args.status_start_ticks > 0)
    def expired(signum, frame):
        raise TimeoutError('diagnostic_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        if not report('diagnostic', lambda: diagnose(args)):
            raise SystemExit(2)
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
