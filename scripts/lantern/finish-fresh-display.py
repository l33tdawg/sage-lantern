#!/usr/bin/env python3
"""Final display-only continuation; no auth/status/camera restart or install.

Expected JSON has camera_diagnostic and camera_checkpoint {path,sha256} pins.
Default runs the real camera checkpoint diagnostic only. --apply exclusively
journals display guard removal/start. Failures log frames, never locals/messages.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import sys
import time
import types


STATE = Path('/var/lib/sage-lantern-display-restore')
DISPLAY = 'sage-lantern-display-power.service'
DIAGNOSTIC_SHA = '8dea60b4ac35efd5389e7cbc9d55afb250eb964370698c7738ea4fc248a25644'


def require(value):
    if not value:
        raise ValueError('display_restore_refused')


def read(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and info.st_nlink == 1
                and not info.st_mode & 0o022 and 0 < info.st_size <= 262144)
        raw = os.read(descriptor, 262145)
        def identity(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                    value.st_nlink, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) == info.st_size and identity(info) == identity(os.fstat(descriptor))
                and identity(info) == identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def pinned(item):
    require(set(item) == {'path', 'sha256'})
    raw = read(item['path'])
    require(hashlib.sha256(raw).hexdigest() == item['sha256'])
    return raw


def decode(raw):
    def unique(items):
        result = {}
        for key, value in items:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def load(item):
    raw = pinned(item)
    module = types.ModuleType('display_restore_pinned')
    module.__file__ = item['path']
    exec(compile(raw, item['path'], 'exec'), module.__dict__)
    return module


def inspect(expected):
    require(set(expected) == {'camera_diagnostic', 'camera_checkpoint'}
            and expected['camera_diagnostic']['sha256'] == DIAGNOSTIC_SHA)
    diagnostic = load(expected['camera_diagnostic'])
    backend = diagnostic.diagnose(types.SimpleNamespace(expected=expected['camera_checkpoint']['path'],
        expected_sha256=expected['camera_checkpoint']['sha256']))
    camera = decode(pinned(expected['camera_checkpoint']))
    require(camera['finish_helper']['sha256'] == diagnostic.FINISH_SHA)
    finish = load(camera['finish_helper'])
    current = backend.current
    def checked():
        for item in expected.values():
            pinned(item)
        current()
    backend.current = checked
    backend.current()
    require(DISPLAY not in backend.running and DISPLAY not in backend.released)
    return backend, finish


def transition(backend, finish, record):
    backend.current()
    record('release_display_intent')
    backend.release(DISPLAY, record)
    backend.current()
    record('release_display_done')
    record('start_display_intent')
    finish.bounded_start(backend, DISPLAY)
    backend.current()
    record('start_display_done')
    return dict(display_restored=True, existing_services_restarted=False, native_changed=False,
                voice_activated=False, vault_keys_resident=True, cryptographic_lock_verified=False,
                audio_readiness_claimed=False)


def failure(error):
    frames = []
    trace = error.__traceback__
    while trace is not None:
        frames.append(dict(file=trace.tb_frame.f_code.co_filename, line=trace.tb_lineno,
                           function=trace.tb_frame.f_code.co_name))
        trace = trace.tb_next
    print(json.dumps(dict(failed=True, error_type=type(error).__name__, frames=frames,
                          automatic_retry=False)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    def expired(signum, frame):
        raise TimeoutError('display_restore_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        raw = pinned(dict(path=args.expected, sha256=args.expected_sha256))
        backend, finish = inspect(decode(raw))
        if not args.apply:
            print(json.dumps(dict(checkpoint_verified=True, mutation_authorized=False,
                                  vault_keys_resident=True, cryptographic_lock_verified=False)))
            return
        STATE.mkdir(mode=0o700)
        backend.runtime.sync(STATE.parent)
        backend.runtime.durable(STATE / 'expected.json', raw)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            backend.runtime.durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(
                phase=phase, expected_sha256=args.expected_sha256)).encode())
            print(json.dumps(dict(phase=phase,
                deadline_remaining_seconds=backend.observer.deadline - time.monotonic())), flush=True)
        print(json.dumps(transition(backend, finish, record)))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        failure(error)
        raise SystemExit(2) from None
