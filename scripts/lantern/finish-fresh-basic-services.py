#!/usr/bin/env python3
"""Explicit camera/display continuation after verified R2 status startup.

Checkpoint sage.lantern.basic-finish.v1 pins diagnostic, resume_helper and
resume_checkpoint as {path, sha256}; status is {pid, start_ticks, invocation,
uid}; resume_journal maps all seven R2 record filenames to SHA256. The nested
checkpoint explicitly acknowledges key-resident BASIC restoration. Default is
read-only. --apply writes a new exclusive journal; never restarts auth/status.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import sys
import time
import types


STATE = Path('/var/lib/sage-lantern-basic-restore-finish')
RESUME_STATE = Path('/var/lib/sage-lantern-basic-restore-resume')
DIAGNOSTIC_SHA = '6ca68e7805ae7f800738ee28bf7e31fcac7e2e70d6e2b7f0eb253dbbd7476c5a'
RESUME_SHA = 'b1ee5d0873a1bf0b930bac3cc6f42cdba7abea68e46c9c8d60475baa3a2f4ab6'
STATUS = 'sage-lantern-status.service'
REMAINING = ('sage-lantern-camera.service', 'sage-lantern-display-power.service')


def require(value):
    if not value:
        raise ValueError('basic_finish_refused')


def pinned(item):
    require(set(item) == {'path', 'sha256'})
    path = Path(item['path'])
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 262144)
        raw = os.read(descriptor, 262145)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                    info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
                and identity(before) == identity(path.lstat())
                and hashlib.sha256(raw).hexdigest() == item['sha256'])
        return raw
    finally:
        os.close(descriptor)


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def bounded_start(backend, service):
    require(service in REMAINING and service in backend.released and service not in backend.running)
    backend.current()
    backend.observer.system._command(['start', service], backend.observer.deadline)
    first = backend.show(service)
    require(first['ActiveState'] == 'active' and first['Job'] in ('', '0')
            and re.fullmatch('[1-9][0-9]*', first['MainPID'])
            and re.fullmatch('[0-9a-f]{32}', first['InvocationID']))
    pid = int(first['MainPID'])
    first_process = backend.preflight.LinuxProcesses().read(pid)
    deadline = min(backend.observer.deadline, time.monotonic() + 10)
    while True:
        value = backend.show(service)
        require(backend.finish_snapshot(value, service) == backend.finish_snapshot(first, service))
        process = backend.preflight.LinuxProcesses().read(pid)
        require(process.pid == pid and process.start_ticks == first_process.start_ticks
                and process.state not in ('Z', 'T', 't', 'X')
                and Path('/proc', str(pid), 'cgroup').read_text() == '0::/system.slice/' + service + '\n')
        expected = backend.expected['start'][service]
        if process.executable == expected['executable']:
            verified = backend.process(service, value)
            require(verified.start_ticks == first_process.start_ticks)
            backend.running[service] = backend.finish_snapshot(value, service)
            backend.ticks[service] = verified.start_ticks
            backend.current()
            backend.current()
            return
        running = Path('/proc', str(pid), 'exe').stat()
        init = Path('/proc/1/exe').stat()
        require(pid != 1 and (running.st_dev, running.st_ino) == (init.st_dev, init.st_ino)
                and process.executable == os.readlink('/proc/1/exe'))
        backend.locked()
        backend.native()
        require(time.monotonic() < deadline)
        time.sleep(0.1)


def reconstruct(checkpoint):
    require(set(checkpoint) == {'schema', 'diagnostic', 'resume_helper', 'resume_checkpoint', 'status', 'resume_journal'}
            and checkpoint['schema'] == 'sage.lantern.basic-finish.v1'
            and checkpoint['diagnostic']['sha256'] == DIAGNOSTIC_SHA
            and checkpoint['resume_helper']['sha256'] == RESUME_SHA)
    raw = pinned(checkpoint['diagnostic'])
    diagnostic = types.ModuleType('frozen_finish_diagnostic')
    diagnostic.__file__ = checkpoint['diagnostic']['path']
    exec(compile(raw, diagnostic.__file__, 'exec'), diagnostic.__dict__)
    for item in ('resume_helper', 'resume_checkpoint'):
        pinned(checkpoint[item])
    status = checkpoint['status']
    require(set(status) == {'pid', 'start_ticks', 'invocation', 'uid'}
            and all(type(status[key]) is int and status[key] > 0 for key in ('pid', 'start_ticks', 'uid'))
            and re.fullmatch('[0-9a-f]{32}', status['invocation']))
    require(len(checkpoint['resume_journal']) == 7)
    def checkpoint_current():
        info = RESUME_STATE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        for item in ('diagnostic', 'resume_helper', 'resume_checkpoint'):
            pinned(checkpoint[item])
        require({entry.name for entry in RESUME_STATE.iterdir()} == set(checkpoint['resume_journal']) | {'checkpoint.json'})
        for name, digest in checkpoint['resume_journal'].items():
            require(re.fullmatch(r'[0-9]{2}-[A-Za-z0-9_.-]+\.json', name))
            pinned(dict(path=str(RESUME_STATE / name), sha256=digest))
        pinned(dict(path=str(RESUME_STATE / 'checkpoint.json'), sha256=checkpoint['resume_checkpoint']['sha256']))
    checkpoint_current()
    captured = []
    original_report = diagnostic.report
    def capture(label, action):
        if label == 'real_current':
            captured.append(action.__self__)
        return original_report(label, action)
    diagnostic.report = capture
    diagnostic.diagnose(types.SimpleNamespace(resume_helper=checkpoint['resume_helper']['path'],
        checkpoint=checkpoint['resume_checkpoint']['path'], checkpoint_sha256=checkpoint['resume_checkpoint']['sha256'],
        status_pid=status['pid'], status_start_ticks=status['start_ticks'], status_invocation=status['invocation']))
    require(len(captured) == 1)
    backend = captured[0]
    require(backend.process(STATUS, backend.show(STATUS)).uid == status['uid'])
    original_current = backend.current
    nested = decode(pinned(checkpoint['resume_checkpoint']))
    base_raw = pinned(nested['original_helper'])
    require(base_raw == backend.helper_raw)
    base = types.ModuleType('frozen_finish_restore')
    base.__file__ = nested['original_helper']['path']
    exec(compile(base_raw, base.__file__, 'exec'), base.__dict__)
    backend.finish_snapshot = base.snapshot
    def current():
        checkpoint_current()
        require(not any(checkpoint['resume_helper']['path'] in process.argv
            for process in backend.preflight.LinuxProcesses().snapshot().values()))
        original_current()
    backend.current = current
    backend.start = lambda service: bounded_start(backend, service)
    backend.current()
    return backend


def transition(backend, record):
    for service in REMAINING:
        backend.current()
        record('release_' + service + '_intent')
        backend.release(service, record)
        backend.current()
        record('release_' + service + '_done')
        record('start_' + service + '_intent')
        backend.start(service)
        backend.current()
        record('start_' + service + '_done')
    return dict(basic_services_restored=True, auth_restarted=False, status_restarted=False,
                voice_activated=False, native_changed=False, vault_keys_resident=True,
                cryptographic_lock_verified=False, audio_readiness_claimed=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    def expired(signum, frame):
        raise TimeoutError('basic_finish_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        raw = pinned(dict(path=args.checkpoint, sha256=args.checkpoint_sha256))
        backend = reconstruct(decode(raw))
        if not args.apply:
            print(json.dumps(dict(checkpoint_verified=True, mutation_authorized=False,
                                  vault_keys_resident=True, cryptographic_lock_verified=False)))
            return
        STATE.mkdir(mode=0o700)
        backend.runtime.sync(STATE.parent)
        backend.runtime.durable(STATE / 'checkpoint.json', raw)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            backend.runtime.durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(
                phase=phase, checkpoint_sha256=args.checkpoint_sha256)).encode())
        print(json.dumps(transition(backend, record)))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Basic finish refused/incomplete; preserve all journals. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
