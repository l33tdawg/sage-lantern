#!/usr/bin/env python3
"""Absent-only audio guard/ALSA file preparation, never microphone commissioning.

Pin installed audio_readiness.py and both derived content hashes. Constants are
read with a restricted AST evaluator, never by executing that module. Default
inspects; --apply retains an exclusive attempt directory. No service reload,
start, capture, readiness marker, overwrite, retry, or recovery is performed.
"""

import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys


STATE = Path('/var/lib/sage-lantern-audio-config-install')
VOICE = 'sage-lantern-voice-loop.service'
BROKER = 'sage-lantern-mesh-runtime.service'
FIELDS = ('LoadState', 'ActiveState', 'MainPID', 'Job', 'ControlGroup', 'FragmentPath',
          'DropInPaths', 'NeedDaemonReload')


def require(value):
    if not value:
        raise ValueError('audio_config_refused')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def parents(path):
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)


def read(path, limit=262144):
    path = Path(path)
    parents(path)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= limit)
        raw = os.read(descriptor, limit + 1)
        def signature(value):
            return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                    value.st_size, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns)
        require(len(raw) <= limit and signature(before) == signature(os.fstat(descriptor))
                and signature(before) == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def constants(raw):
    names = ('VOICE', 'BROKER', 'PYTHON', 'GUARD_PATH', 'ALSA_PATH', 'ALSA_CONFIG', 'GUARD')
    values = {}
    def evaluate(node):
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return node.value
        if isinstance(node, ast.Name) and node.id in values:
            return values[node.id]
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = evaluate(node.left), evaluate(node.right)
            require(type(left) is str and type(right) is str)
            return left + right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and len(node.args) == 1 and not node.keywords:
            value = evaluate(node.args[0])
            if node.func.id == 'Path' and type(value) is str:
                return Path(value)
            if node.func.id == 'str' and isinstance(value, Path):
                return str(value)
        raise ValueError('unsupported_audio_constant')
    for node in ast.parse(raw).body:
        if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id in names for target in node.targets):
            require(len(node.targets) == 1 and isinstance(node.targets[0], ast.Name))
            name = node.targets[0].id
            require(name not in values)
            values[name] = evaluate(node.value)
    require(set(values) == set(names) and values['VOICE'] == VOICE and values['BROKER'] == BROKER
            and values['PYTHON'] == '/opt/sage-lantern/venv/bin/python'
            and values['GUARD_PATH'] == Path('/etc/systemd/system/' + VOICE + '.d/50-fresh-audio-validation.conf')
            and values['ALSA_PATH'] == Path('/etc/sage-lantern/audio-capture-alsa.conf'))
    require(values['GUARD'] == '[Service]\nEnvironment=ALSA_CONFIG_PATH=' + str(values['ALSA_PATH'])
            + '\nExecCondition=+' + values['PYTHON'] + ' -m sage_lantern.audio_readiness --admit\n')
    return values


def show(service):
    require(service in (VOICE, BROKER))
    command = ['/usr/bin/systemctl', 'show', '--no-pager', '--all']
    for field in FIELDS:
        command.extend(['-p', field])
    result = subprocess.run(command + [service], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, timeout=5, check=False, env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
    require(result.returncode == 0 and len(result.stdout) <= 32768)
    value = {}
    for line in result.stdout.decode().splitlines():
        key, separator, content = line.partition('=')
        require(separator and key in FIELDS and key not in value)
        value[key] = content
    require(set(value) == set(FIELDS) and value['LoadState'] == 'loaded'
            and value['ActiveState'] == 'inactive' and value['MainPID'] == '0' and value['Job'] in ('', '0')
            and value['ControlGroup'] in ('', '/system.slice/' + service)
            and value['FragmentPath'] == '/etc/systemd/system/' + service
            and value['NeedDaemonReload'] in ('yes', 'no'))
    root = Path('/sys/fs/cgroup/system.slice') / service
    parents(root)
    if os.path.lexists(root):
        require(stat.S_ISDIR(root.lstat().st_mode) and root.lstat().st_uid == 0)
        events = read(root / 'cgroup.events', 4096).decode().splitlines()
        require(sum(line == 'populated 0' for line in events) == 1
                and not any(line.startswith('populated ') and line != 'populated 0' for line in events)
                and read(root / 'cgroup.procs', 4096).strip() == b'')
    return value


def sync(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def durable(path, raw, mode):
    parents(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(descriptor, 'wb') as stream:
        os.fchown(stream.fileno(), 0, 0)
        os.fchmod(stream.fileno(), mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync(path.parent)


class Install:
    def __init__(self, args):
        self.args = args
        require(re.fullmatch(r'/opt/sage-lantern/venv/lib/python3\.[0-9]+/site-packages/sage_lantern/audio_readiness\.py', args.source))
        require(all(re.fullmatch('[0-9a-f]{64}', value) for value in (args.source_sha256, args.guard_sha256, args.alsa_sha256)))
        raw = read(args.source)
        require(digest(raw) == args.source_sha256)
        value = constants(raw)
        self.guard_path = value['GUARD_PATH']
        self.outputs = {value['ALSA_PATH']: value['ALSA_CONFIG'].encode(), value['GUARD_PATH']: value['GUARD'].encode()}
        require(digest(value['GUARD'].encode()) == args.guard_sha256 and digest(value['ALSA_CONFIG'].encode()) == args.alsa_sha256)
        self.installed = set()
        self.services, self.files = {}, {}
        for service in (VOICE, BROKER):
            observed = show(service)
            require(observed['NeedDaemonReload'] == 'no')
            paths = [observed['FragmentPath'], *observed['DropInPaths'].split()]
            require(len(paths) == len(set(paths)) and len(paths) <= 32)
            for path in paths:
                self.files[path] = digest(read(path))
            self.services[service] = observed
        self.current()

    def current(self):
        require(digest(read(self.args.source)) == self.args.source_sha256)
        for path, expected in self.files.items():
            require(digest(read(path)) == expected)
        for path, raw in self.outputs.items():
            parents(path)
            if path in self.installed:
                require(read(path) == raw and stat.S_IMODE(path.lstat().st_mode) == 0o644
                        and path.lstat().st_gid == 0)
            else:
                require(not os.path.lexists(path))
        for service, before in self.services.items():
            for _ in range(2):
                value = show(service)
                require(all(value[key] == before[key] for key in FIELDS
                            if key not in ('NeedDaemonReload', 'DropInPaths')))
                dropins = value['DropInPaths'].split()
                if (service == VOICE and self.guard_path in self.installed
                        and str(self.guard_path) in dropins):
                    require(dropins.count(str(self.guard_path)) == 1)
                    dropins.remove(str(self.guard_path))
                require(dropins == before['DropInPaths'].split())


def transition(backend, record):
    for path, raw in backend.outputs.items():
        backend.current()
        record('install_intent', str(path))
        durable(path, raw, 0o644)
        backend.installed.add(path)
        backend.current()
        record('install_done', str(path))
    return dict(files_installed=True, daemon_reload_required=True, services_started=False,
                capture_attempted=False, commissioning_complete=False, readiness_marker_created=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('source', 'source-sha256', 'guard-sha256', 'alsa-sha256'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    def expired(signum, frame):
        raise TimeoutError('audio_config_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(90)
    try:
        backend = Install(args)
        if not args.apply:
            print(json.dumps(dict(inspected=True, mutation_authorized=False, capture_attempted=False)))
            return
        parents(STATE)
        STATE.mkdir(mode=0o700)
        sync(STATE.parent)
        durable(STATE / 'expected.json', json.dumps(dict(inputs=vars(args), services=backend.services,
            preserved_files=backend.files), sort_keys=True).encode(), 0o600)
        counter = 0
        def record(phase, path):
            nonlocal counter
            counter += 1
            durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(phase=phase, path=path,
                source_sha256=args.source_sha256, guard_sha256=args.guard_sha256,
                alsa_sha256=args.alsa_sha256)).encode(), 0o600)
        print(json.dumps(transition(backend, record)))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Audio config refused/incomplete; retain attempt record and any files. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
