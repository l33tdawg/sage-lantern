#!/usr/bin/env python3
"""Owned logind sleep inhibition across B's unchanged first-commissioning route.

Default is read-only. Apply claims one attempt before acquiring inhibition.
Only four exact provisioning masks are removed, never generic unmask/revert.
The resident holder has no expiry; release requires reviewed live completion.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import stat
import struct
import subprocess
import sys
import time
import types


STATE = Path('/var/lib/sage-lantern-b-provisioning-sleep-compatible-v2')
FAILED_STATE = Path('/var/lib/sage-lantern-b-provisioning-sleep')
RECONCILIATION = Path('/var/lib/sage-lantern-b-provisioning-sleep-failed-option')
FAILED_HELPER_SHA = '4caed65883e70906d7f16c2efdeb7f8cb61405f1e578add117223d5727b43fd4'
FAILED_FILES = {
    'expected.json': 'ab150110f3197c53ece467d0afdf2ab5ab6949252049c9063417d2ba90f58f6e',
    'intent.json': '9cbd95ec34f03687d993f045bc089b85dd79ac6446217482d309ab0a3141f52c',
    'spawned.json': '643b124acb47d6c945c22c4a6214049dea7d3abb57c2852efd5d124a89240d8c'}
MASK_ROOT = Path('/run/systemd/system')
HANDOFF_SHA = '3ac5ae9b542620176190511ab733b9dbed4affe9a7e1548b640298732d193930'
PYTHON = '/opt/sage-lantern/venv/bin/python'
INHIBIT = '/usr/bin/systemd-inhibit'
INHIBIT_SHA = '83ac2de39661b704cfa3995604635da7f14e1f28ce232bc8d26dc44cf7d8803e'
INHIBIT_HELP_SHA = '3a80232e32fdca0f1f440e0c195001ac58b0bfa47e0bcc431f4cac7698285ddc'
BUSCTL = '/usr/bin/busctl'
WHO = 'sage-lantern-b-provisioning-sleep'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}
TARGETS = ('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target')


def require(value):
    if value is not True:
        raise RuntimeError('b_provisioning_sleep_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def signature(info):
    return tuple(getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def trusted(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
            and not before.st_mode & 0o022 and 0 < before.st_size <= 2097152)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(2097153)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
            == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def dependency(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'} and pin['sha256'] == HANDOFF_SHA)
    raw = trusted(pin['path'])
    require(sha(raw) == HANDOFF_SHA)
    module = types.ModuleType('reviewed_b_sleep_handoff')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def inhibitors(raw):
    fields = shlex.split(raw)
    require(len(fields) >= 2 and fields[0] == 'a(ssssuu)' and fields[1].isdigit())
    count = int(fields[1])
    require(count <= 256 and len(fields) == 2 + 6 * count)
    rows = []
    for offset in range(2, len(fields), 6):
        what, who, why, mode, uid, pid = fields[offset:offset + 6]
        require(uid.isdigit() and pid.isdigit() and 0 <= int(uid) < 2 ** 32 and 0 < int(pid) < 2 ** 32
            and mode in ('block', 'delay'))
        rows.append([what, who, why, mode, int(uid), int(pid)])
    require(len({tuple(row) for row in rows}) == len(rows))
    return sorted(rows)


def owned(rows, pin, why):
    matches = [row for row in rows if row[1] == WHO]
    require(matches == [['sleep', WHO, why, 'block', 0, pin['wrapper']['pid']]])
    return [row for row in rows if row[1] != WHO]


def receive(connection):
    raw = b''
    while b'\n' not in raw:
        part = connection.recv(8193 - len(raw))
        require(bool(part))
        raw += part
        require(len(raw) <= 8192)
    require(raw.endswith(b'\n') and raw.count(b'\n') == 1)
    return raw[:-1]


def remove_masks(backend):
    backend.before_removal()
    backend.archive_masks()
    for unit in TARGETS:
        backend.before_removal()
        backend.record('unlink-' + unit, {'unit': unit})
        backend.unlink(unit)
        backend.reload()
        backend.before_removal()
    backend.ordinary_sleep_idle()
    backend.record('ready', {'masks_removed': True, 'inhibitor_held': True, 'services_started': False})


def release_holder(backend, pin):
    backend.record('release-request', pin)
    backend.release_check(pin)
    backend.owner_check()
    backend.record('release-authorized', {'released': False, 'completion': pin})


class Environment:
    def __init__(self, expected, raw):
        fields = {'schema', 'boot_id', 'self_sha256', 'handoff',
            'coordinator', 'policy_expected', 'systemd_inhibit_sha256', 'python_sha256', 'busctl_sha256',
            'vendor_targets', 'unit_path', 'other_inhibitors_sha256', 'mask_origin'}
        if type(expected) is dict and 'recovery_evidence' in expected:
            fields.add('recovery_evidence')
        require(type(expected) is dict and set(expected) == fields)
        self.helper = dependency(expected['handoff'])
        require(expected['schema'] == 'sage.lantern.b-provisioning-sleep.v1'
            and expected['mask_origin'] == 'parent-provisioning-only-v1'
            and expected['coordinator']['sha256'] == self.helper.POLICY_SHA
            and expected['self_sha256'] == sha(trusted(Path(__file__).absolute()))
            and set(expected['vendor_targets']) == set(TARGETS))
        self.expected, self.raw = expected, raw
        self.code = self.helper.load(expected['coordinator'])
        self.policy_raw = self.helper.pinned(expected['policy_expected'])
        self.policy = self.code.Backend(self.helper.decode(self.policy_raw))
        require(expected['boot_id'] == self.policy.expected['boot_id']
            and self.code.absent_idle_pin(self.code.IDLE, self.policy.expected['services'][self.code.IDLE]))
        self.removed = set()
        self.why = 'reviewed-b-mask-transition-' + sha(raw)
        self.owner = None
        self.masks = None

    def integrity(self):
        require(sha(trusted(Path(__file__).absolute())) == self.expected['self_sha256']
            and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        self.helper.pinned(self.expected['coordinator'])
        self.helper.pinned(self.expected['handoff'])
        require(self.helper.pinned(self.expected['policy_expected']) == self.policy_raw)
        for filename, key in ((INHIBIT, 'systemd_inhibit_sha256'), (PYTHON, 'python_sha256'), (BUSCTL, 'busctl_sha256')):
            target = Path(filename).resolve(strict=True)
            self.helper.parents(target)
            info = target.lstat()
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
            with target.open('rb') as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == self.expected[key])

    def bus(self, service, member, *, property_name=False):
        self.integrity()
        name, path, interface = ('org.freedesktop.login1', '/org/freedesktop/login1', 'org.freedesktop.login1.Manager')
        if service == 'systemd':
            name, path, interface = ('org.freedesktop.systemd1', '/org/freedesktop/systemd1', 'org.freedesktop.systemd1.Manager')
        arguments = [BUSCTL, '--system', '--no-pager', 'get-property' if property_name else 'call',
            name, path, interface, member]
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, env=ENV, timeout=5, check=True)
        require(len(result.stdout) <= 65536)
        return result.stdout.decode().strip()

    def rows(self):
        return inhibitors(self.bus('logind', 'ListInhibitors'))

    def process(self, pid):
        require(type(pid) is int and pid > 1)
        root = Path('/proc') / str(pid)
        before = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        status = (root / 'status').read_text()
        ids = next(line.split()[1:] for line in status.splitlines() if line.startswith('Uid:'))
        require(ids == ['0'] * 4 and before[0] not in ('Z', 'X'))
        with (root / 'exe').open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        argv = (root / 'cmdline').read_bytes()
        require(0 < len(argv) <= 65536 and argv.endswith(b'\0'))
        after = (root / 'stat').read_text().rsplit(')', 1)[1].split()
        require(before[19] == after[19] and before[1] == after[1])
        return dict(pid=pid, start_ticks=int(before[19]), parent=int(before[1]),
            image_sha256=digest, argv=argv[:-1].decode().split('\0'))

    def holder_argv(self):
        return [PYTHON, '-I', '-B', str(Path(__file__).absolute()), '--expected', str(STATE / 'expected.json'),
            '--expected-sha256', sha(self.raw), '--holder']

    def wrapper_argv(self):
        return [INHIBIT, '--what=sleep', '--mode=block', '--who=' + WHO, '--why=' + self.why,
            *self.holder_argv()]

    def executable_contract(self):
        require(self.expected['systemd_inhibit_sha256'] == INHIBIT_SHA)
        self.integrity()
        result = subprocess.run([INHIBIT, '--help'], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=5, check=True)
        require(len(result.stdout) == 842 and sha(result.stdout) == INHIBIT_HELP_SHA)
        help_text = result.stdout.decode()
        require(all(option in help_text for option in ('--what=', '--who=', '--why=', '--mode=')))
        version = subprocess.run([INHIBIT, '--version'], stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=5, check=True)
        require(0 < len(version.stdout) <= 65536 and version.stdout.startswith(b'systemd '))
        self.integrity()

    def identify_owner(self):
        wrapper, holder = self.process(os.getppid()), self.process(os.getpid())
        require(wrapper['argv'] == self.wrapper_argv() and holder['argv'] == self.holder_argv()
            and wrapper['image_sha256'] == self.expected['systemd_inhibit_sha256']
            and holder['image_sha256'] == self.expected['python_sha256'] and holder['parent'] == wrapper['pid'])
        wrapper.pop('parent')
        self.owner = dict(wrapper=wrapper, holder=holder, boot_id=self.expected['boot_id'], expected_sha256=sha(self.raw))
        self.owner_check()
        self.record('owner', self.owner)

    def owner_check(self):
        if self.owner is None:
            self.owner = self.helper.decode(trusted(STATE / 'owner.json'))
        require(self.owner['boot_id'] == self.expected['boot_id'] and self.owner['expected_sha256'] == sha(self.raw))
        for name in ('wrapper', 'holder'):
            observed = self.process(self.owner[name]['pid'])
            if name == 'wrapper':
                observed.pop('parent')
            require(observed == self.owner[name])
        return owned(self.rows(), self.owner, self.why)

    def idle(self):
        require(self.bus('systemd', 'ListJobs') == 'a(usssoo) 0')
        require(self.bus('logind', 'PreparingForSleep', property_name=True) == 'b false'
            and self.bus('logind', 'PreparingForShutdown', property_name=True) == 'b false')
        self.policy.absent_idle(self.policy.properties(self.code.IDLE))
        self.policy.system._sleep_idle(time.monotonic() + 30)
        for unit in self.code.SLEEP_UNITS:
            if unit.endswith('.service'):
                value = self.policy.system._show(unit, time.monotonic() + 5, ('MainPID', 'ControlPID'))
                require(value == {'MainPID': '0', 'ControlPID': '0'})
                self.policy.system._empty_tree(self.policy.system._cgroup('/system.slice/' + unit), time.monotonic() + 5)
        for count, path in enumerate(Path('/proc').iterdir()):
            require(count < 65536)
            if not path.name.isdigit():
                continue
            try:
                raw = (path / 'cmdline').read_bytes()
                require(len(raw) <= 65536)
                parts = raw.split(b'\0')
                names = {part.rsplit(b'/', 1)[-1] for part in parts}
                require(not names & {b'systemd-sleep', b'rtcwake', b'pm-suspend', b'pm-hibernate'})
                require(not (b'systemctl' in names and names &
                    {b'suspend', b'hibernate', b'hybrid-sleep', b'suspend-then-hibernate'}))
            except (FileNotFoundError, ProcessLookupError):
                continue

    def mask_state(self):
        manager = self.policy.system._command(['show', '--no-pager', '--property=UnitPath'], time.monotonic() + 5)
        require(manager == self.expected['unit_path'])
        paths = manager.strip().removeprefix('UnitPath=').split()
        require(manager.startswith('UnitPath=') and 0 < len(paths) <= 64)
        result = {}
        for unit in TARGETS:
            vendor = self.expected['vendor_targets'][unit]
            require(vendor['path'] == '/usr/lib/systemd/system/' + unit)
            self.helper.pinned(vendor)
            path = MASK_ROOT / unit
            for directory in paths:
                require(self.code.missing(Path(directory) / (unit + '.d')))
                if directory not in ('/run/systemd/system', '/lib/systemd/system', '/usr/lib/systemd/system'):
                    require(self.code.missing(Path(directory) / unit))
            if unit in self.removed:
                require(not os.path.lexists(path))
                value = self.policy.system._show(unit, time.monotonic() + 5,
                    ('LoadState', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload'))
                require(value['FragmentPath'] in ('/usr/lib/systemd/system/' + unit, '/lib/systemd/system/' + unit)
                    and Path(value['FragmentPath']).resolve(strict=True) == Path(vendor['path']).resolve(strict=True)
                    and {name: value[name] for name in ('LoadState', 'DropInPaths', 'NeedDaemonReload')} ==
                    dict(LoadState='loaded', DropInPaths='', NeedDaemonReload='no'))
            else:
                link, parents = self.code.runtime_mask_snapshot(unit, paths)
                result[unit] = list(link)
                if self.masks is not None:
                    require(result[unit] == self.masks[unit])
        return result

    def guarded_policy(self):
        if 'recovery_evidence' in self.expected:
            self.helper.recovered_policy_contained(self.expected['recovery_evidence'], self.policy,
                self.code, self.expected['policy_expected'])
            return
        require(self.helper.decode(trusted(self.policy.state / 'transition.jsonl').splitlines()[-1]) ==
            {'phase': 'complete_locked_guards_retained'})
        self.policy.deadline = time.monotonic() + 120
        health = self.policy.check((self.code.AUTH, self.code.UI, self.code.IDLE),
            guarded=True, changed=True, completed=True)
        receipt = self.helper.decode(trusted(self.policy.state / 'complete.json'))
        require(receipt['native_boot'] == health['boot_id'] and receipt['boot_id'] == self.expected['boot_id'])

    def inspect(self):
        self.integrity()
        self.executable_contract()
        self.guarded_policy()
        self.idle()
        self.masks = self.mask_state()
        rows = self.rows()
        require(not any(row[1] == WHO for row in rows)
            and sha(self.helper.encoded(rows)) == self.expected['other_inhibitors_sha256'])
        require(self.mask_state() == self.masks)

    def before_removal(self):
        self.owner_check()
        require(sha(self.helper.encoded(self.owner_check())) == self.expected['other_inhibitors_sha256'])
        self.guarded_policy()
        self.idle()
        self.mask_state()
        self.owner_check()

    def record(self, name, value):
        require('/' not in name)
        self.helper.write(STATE / (name + '.json'), self.helper.encoded(value))

    def archive_masks(self):
        self.masks = self.mask_state()
        require(set(self.masks) == set(TARGETS))
        self.record('mask-archive', self.masks)
        archive = STATE / 'masks'
        archive.mkdir(mode=0o700)
        self.helper.sync(STATE)
        for unit in TARGETS:
            require(list(signature((MASK_ROOT / unit).lstat())) == self.masks[unit]
                and os.readlink(MASK_ROOT / unit) == '/dev/null')
            os.symlink('/dev/null', archive / unit)
            self.helper.sync(archive)

    def unlink(self, unit):
        require(unit in TARGETS and unit not in self.removed)
        self.owner_check()
        path = MASK_ROOT / unit
        require(list(signature(path.lstat())) == self.masks[unit] and os.readlink(path) == '/dev/null')
        path.unlink()
        self.removed.add(unit)
        self.helper.sync(MASK_ROOT)

    def reload(self):
        self.code.systemctl('daemon-reload')

    def ordinary_sleep_idle(self):
        self.owner_check()
        self.policy.preflight.SuspendSystem()._sleep_idle(time.monotonic() + 30)
        self.idle()
        self.mask_state()
        self.owner_check()

    def status(self):
        self.owner_check()
        require(self.helper.decode(trusted(STATE / 'ready.json')) ==
            dict(masks_removed=True, inhibitor_held=True, services_started=False))
        require(not os.path.lexists(STATE / 'failure.json') and not os.path.lexists(STATE / 'remask-attempt.json'))
        self.removed = set(TARGETS)
        self.ordinary_sleep_idle()
        return dict(inhibitor_verified=True, masks_removed=True, released=False)

    def release_check(self, pin):
        self.status()
        document = self.helper.decode(self.helper.pinned(pin))
        require(set(document) == {'schema', 'transition_sha256', 'native_expected', 'native_complete_sha256'}
            and document['schema'] == 'sage.lantern.b-provisioning-sleep-release.v1'
            and document['transition_sha256'] == sha(self.raw))
        result = self.helper.verify_completed_native(self.helper.pinned(document['native_expected']),
            document['native_complete_sha256'])
        wanted = dict(verified=True, boot_id=self.expected['boot_id'],
            policy_expected=self.expected['policy_expected'], native_guard_removed=True)
        if 'recovery_evidence' in self.expected:
            wanted['recovery_evidence'] = self.expected['recovery_evidence']
        require(result == wanted)
        self.status()

    def remask_once(self):
        self.record('remask-attempt', {'automatic_resume': False})
        errors = []
        for unit in TARGETS:
            try:
                path = MASK_ROOT / unit
                self.helper.parents(path)
                if os.path.lexists(path):
                    require(stat.S_ISLNK(path.lstat().st_mode) and path.lstat().st_uid == 0
                        and os.readlink(path) == '/dev/null')
                else:
                    os.symlink('/dev/null', path)
                    self.helper.sync(MASK_ROOT)
            except BaseException:
                errors.append(unit)
        try:
            self.reload()
            self.removed = set()
            self.masks = None
            require(set(self.mask_state()) == set(TARGETS))
            self.idle()
        except BaseException:
            errors.append('verification')
        self.record('remask-outcome', {'inhibition_verified': not errors, 'resume_authorized': False})
        return not errors


def serve_holder(environment):
    helper = environment.helper
    require(trusted(STATE / 'expected.json') == environment.raw)
    environment.record('holder-claim', {'pid': os.getpid(), 'parent': os.getppid()})
    emergency = False
    def interrupted(signum, frame):
        nonlocal emergency
        emergency = True
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, interrupted)
    failed = False
    try:
        environment.identify_owner()
        remove_masks(environment)
    except BaseException:
        failed = True
        try:
            environment.record('failure', {'outcome': 'unknown', 'retry_authorized': False})
        except BaseException:
            pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(STATE / 'control.sock'))
    os.chmod(STATE / 'control.sock', 0o600)
    server.listen(1)
    server.settimeout(1)
    remask_attempted = False
    while True:
        try:
            environment.owner_check()
        except BaseException:
            emergency = True
        if emergency and not remask_attempted:
            remask_attempted = True
            try:
                if environment.remask_once():
                    return
            except BaseException:
                pass
            failed = True
        try:
            connection, address = server.accept()
        except socket.timeout:
            continue
        with connection:
            connection.settimeout(5)
            try:
                peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                require(peer[1] == 0 and not failed)
                request = helper.decode(receive(connection))
                require(set(request) == {'release'} and trusted(STATE / 'release-attempt.json') == helper.encoded(request))
                release_holder(environment, request['release'])
                connection.sendall(b'{"release_authorized":true}\n')
                return
            except BaseException:
                try:
                    connection.sendall(b'{"released":false,"retry_authorized":false}\n')
                except BaseException:
                    pass


def holder(environment):
    try:
        serve_holder(environment)
    except BaseException:
        if not os.path.lexists(STATE / 'remask-attempt.json'):
            try:
                if environment.remask_once():
                    return
            except BaseException:
                pass
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, signal.SIG_IGN)
        while True:
            time.sleep(1)


def reconcile_failed(environment, pin, apply=False):
    helper = environment.helper
    raw = helper.pinned(pin)
    document = helper.decode(raw)
    require(set(document) == {'schema', 'boot_id', 'files'}
        and document['schema'] == 'sage.lantern.b-sleep-failed-option.v1'
        and document['boot_id'] == environment.expected['boot_id']
        and document['files'] == FAILED_FILES)
    require(not os.path.lexists(RECONCILIATION))
    def verify():
        environment.inspect()
        require(not any(row[1] == WHO or row[5] == 81168 for row in environment.rows()))
        for index, unit in enumerate(TARGETS):
            identity = environment.mask_state()[unit]
            require(identity[0] == 24 and identity[1] == 2965 + index
                and identity[2] == stat.S_IFLNK | 0o777 and identity[3:5] == [0, 0]
                and identity[7:] == [1788787466285620339, 1788787466285620339])
        info = FAILED_STATE.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        require({path.name for path in FAILED_STATE.iterdir()} == set(document['files']))
        contents = {name: trusted(FAILED_STATE / name) for name in document['files']}
        require(all(sha(contents[name]) == checksum for name, checksum in document['files'].items()))
        old = helper.decode(contents['expected.json'])
        require(old['self_sha256'] == FAILED_HELPER_SHA)
        old['self_sha256'] = environment.expected['self_sha256']
        require(old == environment.expected)
        require(helper.decode(contents['intent.json']) ==
            dict(inhibitor_acquisition_authorized=True, mask_retirement_authorized=True))
        spawned = helper.decode(contents['spawned.json'])
        require(set(spawned) == {'pid'} and type(spawned['pid']) is int and spawned['pid'] > 1
            and spawned['pid'] == 81168
            and not os.path.lexists(Path('/proc') / str(spawned['pid'])))
        for count, path in enumerate(Path('/proc').iterdir()):
            require(count < 65536)
            if not path.name.isdigit() or int(path.name) == os.getpid():
                continue
            try:
                command = (path / 'cmdline').read_bytes()
                require(len(command) <= 65536 and b'transition-b-provisioning-sleep.py' not in command
                    and WHO.encode() not in command)
            except (FileNotFoundError, ProcessLookupError):
                continue
        require({path.name for path in FAILED_STATE.iterdir()} == set(document['files']))
    verify()
    if apply:
        helper.parents(RECONCILIATION)
        RECONCILIATION.mkdir(mode=0o700)
        helper.sync(RECONCILIATION.parent)
        helper.write(RECONCILIATION / 'intent.json', raw)
        masks = environment.mask_state()
        helper.write(RECONCILIATION / 'mask-identities.json', helper.encoded(masks))
        verify()
        archive = RECONCILIATION / 'failed-attempt'
        archive.mkdir(mode=0o700)
        helper.sync(RECONCILIATION)
        for name in document['files']:
            helper.write(archive / name, trusted(FAILED_STATE / name))
        environment.inspect()
        require(environment.mask_state() == masks)
        helper.write(RECONCILIATION / 'complete.json', helper.encoded(
            dict(archived=True, masks_retained=True, automatic_resume=False)))
    return dict(reconciliation_verified=True, archived=apply, automatic_resume=False)


def start(environment):
    require(not os.path.lexists(STATE))
    if os.path.lexists(FAILED_STATE):
        helper = environment.helper
        require(helper.decode(trusted(RECONCILIATION / 'complete.json')) ==
            dict(archived=True, masks_retained=True, automatic_resume=False))
        document = helper.decode(trusted(RECONCILIATION / 'intent.json'))
        require(document['files'] == FAILED_FILES and document['boot_id'] == environment.expected['boot_id']
            and {path.name for path in FAILED_STATE.iterdir()} == set(document['files']))
        for name, checksum in document['files'].items():
            require(sha(trusted(FAILED_STATE / name)) == checksum
                and sha(trusted(RECONCILIATION / 'failed-attempt' / name)) == checksum)
        require(environment.mask_state() == helper.decode(trusted(RECONCILIATION / 'mask-identities.json')))
    environment.inspect()
    environment.helper.parents(STATE)
    STATE.mkdir(mode=0o700)
    environment.helper.sync(STATE.parent)
    environment.helper.write(STATE / 'expected.json', environment.raw)
    environment.record('intent', {'inhibitor_acquisition_authorized': True, 'mask_retirement_authorized': True})
    process = subprocess.Popen(environment.wrapper_argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=ENV, start_new_session=True, close_fds=True)
    environment.record('spawned', {'pid': process.pid})
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        require(process.poll() is None and not os.path.lexists(STATE / 'failure.json'))
        if os.path.lexists(STATE / 'ready.json') and os.path.lexists(STATE / 'control.sock'):
            return environment.status()
        time.sleep(0.2)
    raise TimeoutError('holder_outcome_unknown_do_not_retry')


def release(environment, pin):
    environment.release_check(pin)
    require(not os.path.lexists(STATE / 'release-attempt.json'))
    request = {'release': pin}
    environment.record('release-attempt', request)
    owner = environment.owner
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(180)
        connection.connect(str(STATE / 'control.sock'))
        peer = struct.unpack('3i', connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(peer[1] == 0 and peer[0] == owner['holder']['pid'])
        connection.sendall(environment.helper.encoded(request) + b'\n')
        require(environment.helper.decode(receive(connection)) == {'release_authorized': True})
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        rows = environment.rows()
        if not any(row[1] == WHO for row in rows):
            environment.record('released', {'inhibitor_absent_verified': True, 'sleep_acceptance_verified': False})
            return dict(released=True, sleep_acceptance_verified=False)
        time.sleep(0.2)
    raise TimeoutError('inhibitor_release_unknown_do_not_retry')


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument('--status', action='store_true')
    modes.add_argument('--holder', action='store_true', help=argparse.SUPPRESS)
    modes.add_argument('--release-expected')
    modes.add_argument('--executable-check', action='store_true')
    modes.add_argument('--reconcile-expected')
    parser.add_argument('--release-sha256')
    parser.add_argument('--reconcile-sha256')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    raw = trusted(args.expected)
    require(sha(raw) == args.expected_sha256)
    expected = json.loads(raw)
    environment = Environment(expected, raw)
    require(environment.helper.decode(raw) == expected and bool(args.release_expected) == bool(args.release_sha256))
    require(bool(args.reconcile_expected) == bool(args.reconcile_sha256))
    if args.holder:
        require(not args.apply and args.expected == str(STATE / 'expected.json'))
        holder(environment)
        return
    if args.status or args.release_expected:
        require(trusted(STATE / 'expected.json') == raw)
    if args.executable_check:
        require(not args.apply)
        environment.executable_contract()
        result = dict(executable_contract_verified=True, services_changed=False, attempt_claimed=False)
    elif args.reconcile_expected:
        result = reconcile_failed(environment, dict(path=args.reconcile_expected,
            sha256=args.reconcile_sha256), args.apply)
    elif args.status:
        require(not args.apply)
        result = environment.status()
    elif args.release_expected:
        pin = dict(path=args.release_expected, sha256=args.release_sha256)
        if args.apply:
            result = release(environment, pin)
        else:
            environment.release_check(pin)
            result = dict(release_checked=True, released=False)
    elif args.apply:
        result = start(environment)
    else:
        require(not os.path.lexists(STATE))
        environment.inspect()
        result = dict(inspected=True, masks_removed=False, inhibitor_acquired=False)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B sleep transition refused/unknown; preserve holder, masks and journals; no retry.') from None
