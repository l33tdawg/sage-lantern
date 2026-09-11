#!/usr/bin/env python3
"""Read-only camera adoption after the exact seven-record finish checkpoint.

--expected/--expected-sha256 pin JSON with finish_helper {path,sha256},
finish_checkpoint {path,sha256}, finish_journal {filename:sha256}, and camera
{pid,start_ticks,invocation,uid}. Reuses frozen diagnostic and real validators.
No start/release/install, writes, or mutation authority; exception frames only.
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


FINISH_SHA = '80c14d9dd6cdcad0e713a97a8ba3f2973baa239fb5f557beb82d802f61d0b926'
CAMERA = 'sage-lantern-camera.service'


def require(value):
    if not value:
        raise ValueError('camera_diagnostic_refused')


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


def frames(label, error):
    entries = []
    trace = error.__traceback__
    while trace is not None:
        entries.append(dict(file=trace.tb_frame.f_code.co_filename, line=trace.tb_lineno,
                            function=trace.tb_frame.f_code.co_name))
        trace = trace.tb_next
    print(json.dumps(dict(check=label, passed=False, error_type=type(error).__name__, frames=entries,
                          mutation_authorized=False)))


def dynamic_camera_process(backend, camera, value):
    require(set(camera) == {'pid', 'start_ticks', 'invocation', 'uid'}
            and all(type(camera[key]) is int and camera[key] > 0 for key in ('pid', 'start_ticks', 'uid'))
            and re.fullmatch('[0-9a-f]{32}', camera['invocation']))
    canonical = '/system.slice/' + CAMERA
    require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'active'
            and value['MainPID'] == str(camera['pid']) and value['InvocationID'] == camera['invocation']
            and value['User'] == 'sage-lantern-camera' and value['ControlGroup'] == canonical
            and value['Job'] in ('', '0'))
    fields = ('DynamicUser', 'User', 'LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'ControlGroup', 'Job')
    def dynamic_identity():
        arguments = ['show', '--no-pager', '--all']
        for field in fields:
            arguments.extend(['-p', field])
        text = backend.observer.system._command(arguments + [CAMERA], backend.observer.deadline)
        result = {}
        for line in text.splitlines():
            key, separator, content = line.partition('=')
            require(separator and key in fields and key not in result)
            result[key] = content
        require(set(result) == set(fields) and result['DynamicUser'] == 'yes'
                and all(result[key] == value[key] for key in fields if key != 'DynamicUser'))
        return result
    before = dynamic_identity()
    expected = backend.expected['start'][CAMERA]
    def inspect():
        process = backend.preflight.LinuxProcesses().read(camera['pid'])
        require(process.pid == camera['pid'] and process.uid == camera['uid']
                and process.start_ticks == camera['start_ticks']
                and process.executable == expected['executable'] and list(process.argv) == expected['argv']
                and process.state not in ('Z', 'T', 't', 'X'))
        root = Path('/proc', str(camera['pid']))
        require((root / 'cgroup').read_text() == '0::' + canonical + '\n')
        running, installed = (root / 'exe').stat(), Path(expected['executable']).stat()
        require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
        return process
    process = inspect()
    require(dynamic_identity() == before)
    inspect()
    return process


def bind_camera_process(backend, camera):
    original = backend.process
    def process(service, value):
        if service == CAMERA:
            return dynamic_camera_process(backend, camera, value)
        return original(service, value)
    backend.process = process


def diagnose(args):
    raw = read(args.expected)
    require(hashlib.sha256(raw).hexdigest() == args.expected_sha256)
    expected = json.loads(raw)
    require(set(expected) == {'finish_helper', 'finish_checkpoint', 'finish_journal', 'camera'})
    source = read(expected['finish_helper']['path'])
    require(hashlib.sha256(source).hexdigest() == expected['finish_helper']['sha256'] == FINISH_SHA)
    finish = types.ModuleType('frozen_camera_finish')
    finish.__file__ = expected['finish_helper']['path']
    exec(compile(source, finish.__file__, 'exec'), finish.__dict__)
    expected = finish.decode(raw)
    checkpoint_raw = finish.pinned(expected['finish_checkpoint'])
    checkpoint = finish.decode(checkpoint_raw)
    phases = ['release_' + CAMERA + '_intent',
        'remove_' + CAMERA + '_zzzz-fresh-runtime-update.conf_intent',
        'remove_' + CAMERA + '_zzzz-fresh-runtime-update.conf_done',
        'remove_' + CAMERA + '_zzzz-fresh-runtime-r7-update.conf_intent',
        'remove_' + CAMERA + '_zzzz-fresh-runtime-r7-update.conf_done',
        'release_' + CAMERA + '_done', 'start_' + CAMERA + '_intent']
    names = {f'{index:02d}-{phase}.json': phase for index, phase in enumerate(phases, 1)}
    require(set(expected['finish_journal']) == set(names))
    def journal_check():
        require({entry.name for entry in finish.STATE.iterdir()} == set(names) | {'checkpoint.json'})
        finish.pinned(dict(path=str(finish.STATE / 'checkpoint.json'), sha256=expected['finish_checkpoint']['sha256']))
        for name, phase in names.items():
            content = finish.pinned(dict(path=str(finish.STATE / name), sha256=expected['finish_journal'][name]))
            require(finish.decode(content) == dict(phase=phase, checkpoint_sha256=expected['finish_checkpoint']['sha256']))
        require({entry.name for entry in finish.RESUME_STATE.iterdir()}
                == set(checkpoint['resume_journal']) | {'checkpoint.json'})
        for name, digest in checkpoint['resume_journal'].items():
            require(Path(name).name == name and name not in ('', '.', '..'))
            finish.pinned(dict(path=str(finish.RESUME_STATE / name), sha256=digest))
        finish.pinned(dict(path=str(finish.RESUME_STATE / 'checkpoint.json'),
                           sha256=checkpoint['resume_checkpoint']['sha256']))
    journal_check()
    diagnostic_raw = finish.pinned(checkpoint['diagnostic'])
    require(checkpoint['diagnostic']['sha256'] == finish.DIAGNOSTIC_SHA
            and checkpoint['resume_helper']['sha256'] == finish.RESUME_SHA)
    diagnostic = types.ModuleType('frozen_camera_diagnostic')
    diagnostic.__file__ = checkpoint['diagnostic']['path']
    exec(compile(diagnostic_raw, diagnostic.__file__, 'exec'), diagnostic.__dict__)
    original_report = diagnostic.report
    adopted = []
    backends = []
    def report(label, action):
        started = time.monotonic()
        if label == 'real_current':
            backend = action.__self__
            snapshot = backend.show.__func__.__globals__['snapshot']
            camera = expected['camera']
            require(set(camera) == {'pid', 'start_ticks', 'invocation', 'uid'})
            bind_camera_process(backend, camera)
            value = backend.show(CAMERA)
            process = backend.process(CAMERA, value)
            require(process.pid == camera['pid'] and process.start_ticks == camera['start_ticks']
                    and process.uid == camera['uid'] and value['InvocationID'] == camera['invocation'])
            backend.running[CAMERA] = snapshot(value, CAMERA)
            backend.ticks[CAMERA] = camera['start_ticks']
            backend.released.add(CAMERA)
            for path, guard, state in backend.guards(CAMERA):
                require(not os.path.lexists(path))
                backend.removed.add(path)
            adopted.append(True)
            backends.append(backend)
            require(adopted == [True])
            print(json.dumps(dict(check='camera_process', passed=True, pid=process.pid,
                start_ticks=process.start_ticks, uid=process.uid, invocation=value['InvocationID'],
                deadline_remaining_seconds=backend.observer.deadline - time.monotonic(), mutation_authorized=False)))
        result = original_report(label, action)
        print(json.dumps(dict(check=label + '_timing', elapsed_seconds=time.monotonic() - started,
                              mutation_authorized=False)))
        return result
    diagnostic.report = report
    status = checkpoint['status']
    diagnostic.diagnose(types.SimpleNamespace(resume_helper=checkpoint['resume_helper']['path'],
        checkpoint=checkpoint['resume_checkpoint']['path'], checkpoint_sha256=checkpoint['resume_checkpoint']['sha256'],
        status_pid=status['pid'], status_start_ticks=status['start_ticks'], status_invocation=status['invocation']))
    require(adopted == [True])
    journal_check()
    backend = backends[0]
    original_current = backend.current
    def current():
        journal_check()
        require(finish.pinned(expected['finish_checkpoint']) == checkpoint_raw)
        original_current()
    backend.current = current
    backend.finish_snapshot = backend.show.__func__.__globals__['snapshot']
    return backend


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    def expired(signum, frame):
        raise TimeoutError('camera_diagnostic_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        diagnose(args)
    except Exception as error:
        frames('camera_diagnostic', error)
        raise SystemExit(2) from None
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()
