#!/usr/bin/env python3
"""B-only fresh-prebroker policy/native transition; default inspection is read-only.

Apply inhibits producers, drains the pinned current auth invocation, preserves
its prior receipt/config/binary, and asks native to exec itself once. Unknown
outcomes retain all guards and journals. Native is never stopped by systemd.
The root-owned helper closure and complete installed runtime source inventory
are part of the expected document. No A lifecycle evidence is accepted.
"""

import argparse
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import resource
import re
import stat
import subprocess
import sys
import time
import types
import traceback
import uuid


SAGE = 'sage-lantern-personal-sage.service'
AUTH = 'sage-lantern-device-auth.service'
UI = 'sage-lantern-fleet-admin.service'
IDLE = 'sage-lantern-idle-suspend.service'
COLD = tuple('sage-lantern-' + name + '.service' for name in ('llm', 'mesh-runtime', 'voice-loop'))
SERVICES = (SAGE, AUTH, UI, IDLE) + COLD
PYTHON = '/opt/sage-lantern/venv/bin/python'
BINARY = Path('/opt/sage-lantern/bin/sage-gui')
CONFIG = Path('/var/lib/sage-lantern/sage-personal/config.yaml')
REPORTS = Path('/var/lib/sage-lantern/device-lock/drain')
RUNTIME = Path('/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern')
SYSTEMD_IMAGE = Path('/usr/lib/systemd/systemd')
SYSTEMD_SHA = 'f322ba1392c81bf128cc473a86596acda15d915a0a7c2988a8e03218ac4f2a95'
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}
HELPERS = {'deploy-resident-vault-native.py', 'install-prepared-fresh-mesh.py',
    'preflight-fresh-native-update.py', 'update-fresh-native.py',
    'prepare-b-private-policy.py', 'inspect-fresh-sage-policy.py', 'request-fresh-sage-restart.py'}
RETAINED = set(SERVICES) - {AUTH, UI}
SCAN_BINDING = dict(unit_id='unit-b', endpoint='http://127.0.0.1:8080', voice_uid=994,
    chain_id='sage-personal-twcvr4higeb6u3khrhzc2mz734',
    agent_id='09b27a90d5a8bcb8f0a0a84930f95e965334d4c2194405fc547419edd91c1311',
    identity_file='/var/lib/sage-lantern/identity/lantern-agent.key')


def require(value):
    if value is not True:
        raise RuntimeError('b_private_transition_refused')


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def signature(info):
    return tuple(getattr(info, key) for key in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def valid_boot(value):
    try:
        return type(value) is str and str(uuid.UUID(value)) == value and uuid.UUID(value).int > 0
    except (ValueError, AttributeError, TypeError):
        return False


def validate_scan(config):
    require(type(config) is dict and all(type(config.get(name)) is type(value)
        and config[name] == value for name, value in SCAN_BINDING.items()))
    require(config.get('messaging_enabled') is False and config.get('dispatch_enabled') is False
        and config.get('receive_while_locked') is False and config.get('dispatch_while_locked') is False
        and config.get('peers') == [])


def validate_health(health, old_boot, version, completed):
    require(type(health) is dict and valid_boot(health.get('boot_id'))
        and health.get('encrypted') is True and health.get('sage') == 'running'
        and health.get('version') == version)
    require(health['boot_id'] != old_boot if completed else health['boot_id'] == old_boot)
    if completed:
        require(health.get('vault_locked') is True)


def absent_idle_pin(service, pin):
    if 'absent' not in pin:
        return False
    require(service == IDLE and pin == dict(absent=True, pid=0, start_ticks=0,
        invocation='', image_sha256='') and pin['absent'] is True
        and type(pin['pid']) is int and type(pin['start_ticks']) is int)
    return True


def missing(path):
    try:
        Path(path).lstat()
    except FileNotFoundError:
        return True
    return False


def read(path, owner=0, maximum=1048576, mode=None):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in {0, owner} and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner and before.st_nlink == 1
            and not before.st_mode & 0o022 and before.st_size <= maximum
            and (mode is None or stat.S_IMODE(before.st_mode) == mode))
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(maximum + 1)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
            == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def systemctl(*args, timeout=10):
    require(args[0] in ('show', 'daemon-reload', 'stop', 'start'))
    if args[0] in ('stop', 'start'):
        require(len(args) == 2 and args[1] in (AUTH, UI, IDLE))
    return subprocess.run(['/usr/bin/systemctl', '--no-pager', *args], env=ENV,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        timeout=timeout, check=True).stdout.decode()


def get(port, route, *, diagnostics=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    try:
        connection.request('GET', route)
        response = connection.getresponse()
        raw = response.read(1048577)
        if diagnostics is not None:
            diagnostics.update(status=response.status, response_bytes=len(raw))
        require(response.status == 200 and len(raw) <= 1048576)
        return json.loads(raw)
    finally:
        connection.close()


MASKED_SLEEP_TARGETS = ('sleep.target', 'suspend.target', 'hibernate.target', 'hybrid-sleep.target')
SLEEP_UNITS = MASKED_SLEEP_TARGETS + ('suspend-then-hibernate.target', 'systemd-suspend.service',
    'systemd-hibernate.service', 'systemd-hybrid-sleep.service', 'systemd-suspend-then-hibernate.service')


def runtime_mask_snapshot(unit, paths):
    require(unit in MASKED_SLEEP_TARGETS)
    path = Path('/run/systemd/system') / unit
    parents = []
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        parents.append(signature(info))
    info = path.lstat()
    require(stat.S_ISLNK(info.st_mode) and info.st_uid == 0 and info.st_gid == 0
        and info.st_nlink == 1 and os.readlink(path) == '/dev/null')
    for directory in paths:
        require(missing(Path(directory) / (unit + '.d')))
        if directory not in ('/run/systemd/system', '/lib/systemd/system', '/usr/lib/systemd/system'):
            require(missing(Path(directory) / unit))
    require(signature(path.lstat()) == signature(info))
    return signature(info), tuple(parents)


def b_suspend_system(base):
    class BProvisioningSuspendSystem(base):
        def _sleep_idle(self, deadline):
            manager = self._command(['show', '--no-pager', '--property=UnitPath'], deadline)
            require(manager.startswith('UnitPath=') and len(manager) <= 32768)
            paths = manager.strip().removeprefix('UnitPath=').split()
            require(0 < len(paths) <= 64 and len(paths) == len(set(paths))
                and {'/etc/systemd/system', '/run/systemd/system', '/run/systemd/transient'} <= set(paths)
                and all(Path(path).is_absolute() and '..' not in Path(path).parts for path in paths))
            fields = ('LoadState', 'ActiveState', 'Job', 'FragmentPath', 'UnitFileState',
                'DropInPaths', 'NeedDaemonReload', 'Transient')
            observations = {}
            masks = {}
            for unit in SLEEP_UNITS:
                value = self._show(unit, deadline, fields)
                require(value['ActiveState'] == 'inactive' and self._no_job(value['Job'])
                    and value['NeedDaemonReload'] == 'no' and value['Transient'] == 'no')
                if value['LoadState'] == 'masked':
                    require(unit in MASKED_SLEEP_TARGETS and value['UnitFileState'] == 'masked-runtime'
                        and value['FragmentPath'] == '/run/systemd/system/' + unit and value['DropInPaths'] == '')
                    masks[unit] = runtime_mask_snapshot(unit, paths)
                else:
                    required = unit in ('sleep.target', 'suspend.target', 'systemd-suspend.service')
                    require(value['LoadState'] in (('loaded',) if required else ('loaded', 'not-found')))
                observations[unit] = value
            for unit, before in masks.items():
                require(runtime_mask_snapshot(unit, paths) == before)
            require(self._command(['show', '--no-pager', '--property=UnitPath'], deadline) == manager)
            for unit, value in observations.items():
                require(self._show(unit, deadline, fields) == value)
            for unit, before in masks.items():
                require(runtime_mask_snapshot(unit, paths) == before)

        def suspend_verified(self):
            raise RuntimeError('b_provisioning_suspend_forbidden')

    return BProvisioningSuspendSystem()


def validate(expected):
    require(type(expected) is dict and set(expected) == {'schema', 'unit_id', 'transition_id',
        'boot_id', 'native_boot', 'services', 'files', 'runtime_files', 'helpers', 'auth_reports',
        'before', 'after', 'scan_config', 'storage_epoch'})
    require(expected['schema'] == 'sage.lantern.b-private-transition.v1' and expected['unit_id'] == 'unit-b')
    for field in ('transition_id', 'boot_id', 'native_boot'):
        require(str(uuid.UUID(expected[field])) == expected[field] and uuid.UUID(expected[field]).int > 0)
    require(set(expected['services']) == set(SERVICES) and set(expected['helpers']) == HELPERS)
    require(set(expected['files']) >= {'/etc/sage-lantern/initialized-public.json',
        '/etc/sage-lantern/unit.env', '/usr/local/libexec/sage-lantern-device-auth'})
    require(expected['before']['binary_sha256'] == '11f9a5cb9299cee10ad4bd91afbb98d8d84778d60f7906d52a5d35097c60d99c')
    require(expected['after']['binary_sha256'] == '5b8542bdfbc508451f9fa90cd886e828bf301ab46ef659321897cf221ca0c072')
    require(expected['before']['binary'] != str(BINARY) and expected['after']['binary'] != str(BINARY))
    require(str(CONFIG) not in expected['files'] and str(BINARY) not in expected['files'])
    for service, pin in expected['services'].items():
        if type(pin) is dict and absent_idle_pin(service, pin):
            require(not any(Path(path).name == IDLE or IDLE + '.d' in Path(path).parts
                for path in expected['files']))
            continue
        require(type(pin) is dict and set(pin) == {'pid', 'start_ticks', 'invocation', 'image_sha256'})
        require(type(pin['pid']) is int and pin['pid'] >= 0 and type(pin['start_ticks']) is int)
        if service in COLD:
            require(pin['pid'] == 0)
        if service in (SAGE, AUTH, UI):
            require(pin['pid'] > 0 and pin['start_ticks'] > 0
                and re.fullmatch('[0-9a-f]{32}', pin['invocation']) is not None)
    validate_scan(expected['scan_config'])
    require(type(expected['storage_epoch']) is str and 0 < len(expected['storage_epoch']) <= 512)


class Backend:
    def __init__(self, expected):
        validate(expected)
        self.expected = expected
        self.uid = pwd.getpwnam('sage-lantern').pw_uid
        require(self.uid == 994)
        self.state = Path('/var/lib/lantern-b-policy-transition-' + expected['transition_id'])
        self.deadline = time.monotonic() + 300
        self.guards = {name: Path('/etc/systemd/system') / (name + '.d') /
            ('zzzz-b-policy-' + expected['transition_id'] + '.conf') for name in SERVICES
            if not absent_idle_pin(name, expected['services'][name])}
        self.guard = ('[Unit]\nConditionPathExists=!' + str(self.state) + '\n[Service]\nRestart=no\n').encode()
        self.guard_native = b'[Service]\nRestart=no\n'
        self.modules = {}
        self.released = set()
        self.closure()
        for name in HELPERS:
            pin = expected['helpers'][name]
            module = types.ModuleType(name)
            module.__file__ = pin['path']
            exec(compile(read(pin['path']), pin['path'], 'exec'), module.__dict__)
            self.modules[name] = module
        self.io = self.modules['deploy-resident-vault-native.py']
        self.mesh = self.modules['install-prepared-fresh-mesh.py']
        self.policy = self.modules['prepare-b-private-policy.py']
        self.preflight = self.modules['preflight-fresh-native-update.py']
        self.listener = self.modules['update-fresh-native.py']
        self.restart = self.modules['request-fresh-sage-restart.py']
        self.system = b_suspend_system(self.preflight.SuspendSystem)

    def closure(self):
        for name, pin in self.expected['helpers'].items():
            require(Path(pin['path']).name == name and digest(read(pin['path'])) == pin['sha256'])
        parents = {str(Path(pin['path']).parent) for pin in self.expected['helpers'].values()}
        require(len(parents) == 1)
        actual = {str(path) for path in RUNTIME.rglob('*.py')}
        require(actual == set(self.expected['runtime_files']) and bool(actual))
        for filename, checksum in self.expected['runtime_files'].items():
            require(digest(read(filename)) == checksum)

    def properties(self, service):
        fields = ('ActiveState', 'MainPID', 'InvocationID', 'Job', 'ControlGroup', 'FragmentPath',
            'DropInPaths', 'NeedDaemonReload', 'Restart', 'KillMode', 'Slice', 'LoadState', 'Transient')
        return dict(line.split('=', 1) for line in systemctl('show', service,
            *[item for field in fields for item in ('-p', field)]).splitlines())

    def absent_idle(self, value):
        required = dict(LoadState='not-found', ActiveState='inactive', MainPID='0',
            InvocationID='', FragmentPath='', DropInPaths='', ControlGroup='', Transient='no',
            NeedDaemonReload='no')
        require(all(value.get(name) == wanted for name, wanted in required.items())
            and value.get('Job') in ('', '0'))
        path_property = systemctl('show', '-p', 'UnitPath')
        require(path_property.startswith('UnitPath='))
        paths = path_property.strip().removeprefix('UnitPath=').split()
        require(0 < len(paths) <= 64 and len(paths) == len(set(paths))
            and {'/etc/systemd/system', '/run/systemd/system', '/run/systemd/transient'} <= set(paths))
        def absent_paths():
            for directory in paths:
                require(Path(directory).is_absolute() and '..' not in Path(directory).parts)
                require(missing(Path(directory) / IDLE) and missing(Path(directory) / (IDLE + '.d')))
            require(missing(Path('/sys/fs/cgroup/system.slice') / IDLE))
        absent_paths()
        for count, process in enumerate(Path('/proc').iterdir()):
            require(count < 65536)
            if not process.name.isdigit():
                continue
            try:
                cgroups = (process / 'cgroup').read_text()
                require(len(cgroups) <= 65536 and all(IDLE not in line.split(':', 2)[-1].split('/')
                    for line in cgroups.splitlines()))
                with (process / 'cmdline').open('rb') as stream:
                    raw = stream.read(65537)
                require(len(raw) <= 65536)
                require(not any(part.rsplit(b'/', 1)[-1] in
                    (b'sage-lantern-idle-suspend', b'lantern-idle-suspend.py', b'sage_lantern.idle_suspend')
                    for part in raw.split(b'\0')))
            except (FileNotFoundError, ProcessLookupError):
                continue
        absent_paths()
        require(systemctl('show', '-p', 'UnitPath') == path_property and self.properties(IDLE) == value)

    def services(self, stopped=(), guarded=False, completed=False, fresh=None, initial_exec=None):
        initial_image = None
        if initial_exec is not None:
            require(initial_exec in (AUTH, UI) and completed is True and guarded == RETAINED
                and initial_exec in (fresh or {}) and fresh[initial_exec]['image_sha256'] == SYSTEMD_SHA
                and digest(read(SYSTEMD_IMAGE, maximum=536870912)) == SYSTEMD_SHA)
        guarded_services = set(SERVICES) if guarded is True else set(guarded or ())
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        for service, pin in self.expected['services'].items():
            pin = (fresh or {}).get(service, pin)
            has_guard = service in guarded_services
            value = self.properties(service)
            if absent_idle_pin(service, pin):
                self.absent_idle(value)
                continue
            fragment = '/etc/systemd/system/' + service
            require(value['LoadState'] == 'loaded' and value['Transient'] == 'no'
                and value['FragmentPath'] == fragment and fragment in self.expected['files']
                and (value['NeedDaemonReload'] == 'no' or (guarded and bool(self.released)
                    and value['NeedDaemonReload'] == 'yes')) and value['Job'] in ('', '0')
                and value['KillMode'] == 'control-group' and value['Slice'] == 'system.slice')
            originals = {path for path in self.expected['files'] if path.startswith(fragment + '.d/')}
            require(set(value['DropInPaths'].split()) == originals | ({str(self.guards[service])} if has_guard else set()))
            if has_guard:
                guard_path = self.state / ('released-' + service) if service in self.released else self.guards[service]
                require(read(guard_path) == (self.guard_native if service == SAGE else self.guard)
                    and value['Restart'] == 'no')
                if service != SAGE:
                    self.system._guard(service, self.deadline, str(self.state))
            if service in COLD or service in stopped or pin['pid'] == 0:
                require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                    and value['ControlGroup'] in ('', '/system.slice/' + service))
                group = Path('/sys/fs/cgroup/system.slice') / service
                if group.exists():
                    require(all(not path.read_text().strip() for path in group.rglob('cgroup.procs'))
                        and not (group / 'cgroup.procs').read_text().strip())
            else:
                require(value['ActiveState'] == 'active' and value['MainPID'] == str(pin['pid'])
                    and value['InvocationID'] == pin['invocation']
                    and value['ControlGroup'] == '/system.slice/' + service)
                process = Path('/proc') / str(pin['pid'])
                require(int((process / 'stat').read_text().rsplit(')', 1)[1].split()[19]) == pin['start_ticks'])
                with (process / 'exe').open('rb') as stream:
                    before = os.fstat(stream.fileno())
                    checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
                    require(signature(before) == signature(os.fstat(stream.fileno())))
                expected_image = self.expected['after']['binary_sha256'] if completed and service == SAGE else pin['image_sha256']
                require(checksum == expected_image or (service == initial_exec
                    and checksum == self.expected['services'][service]['image_sha256']))
                if service == initial_exec:
                    require(self.properties(service) == value
                        and int((process / 'stat').read_text().rsplit(')', 1)[1].split()[19]) == pin['start_ticks'])
                    initial_image = checksum
        return initial_image

    def files(self, changed=False):
        self.closure()
        for filename, checksum in self.expected['files'].items():
            require(digest(read(filename)) == checksum)
        require(read('/etc/sage-lantern/unit.env') == b'UNIT_ID=unit-b\nPROVISIONING_SCHEMA=sage-lantern-unit-v1\n')
        for name in ('before', 'after'):
            self.io.checked_artifact(self.expected[name])
        require(digest(read(BINARY, maximum=536870912)) == self.expected['after' if changed else 'before']['binary_sha256'])
        if changed:
            require(read(CONFIG, self.uid, mode=0o600) == read(self.state / 'candidate.yaml'))
        else:
            raw = read(CONFIG, self.uid, mode=0o600)
            self.policy.corrected(raw, read('/etc/sage-lantern/initialized-public.json'))

    def reports(self, drained=False, extra=None):
        invocation = self.expected['services'][AUTH]['invocation']
        current = invocation + '.json'
        require({path.name for path in REPORTS.iterdir()} == set(self.expected['auth_reports']) | set(extra or {}))
        for name, checksum in (extra or {}).items():
            require(Path(name).name == name and name not in self.expected['auth_reports'])
            require(digest(read(REPORTS / name, self.uid, 1024, 0o600)) == checksum)
        for name, checksum in self.expected['auth_reports'].items():
            require(Path(name).name == name)
            raw = read(REPORTS / name, self.uid, 1024, 0o600)
            if name == current and drained:
                self.preflight.report(json.loads(raw), invocation, current=False)
            else:
                require(digest(raw) == checksum)
                if name == current:
                    self.preflight.report(json.loads(raw), invocation, current=True)
        require(current in self.expected['auth_reports'])
        return read(REPORTS / current, self.uid, 1024, 0o600)

    def check(self, stopped=(), guarded=False, changed=False, completed=False, fresh=None, extra_reports=None):
        self.files(changed)
        self.mesh.b_no_prior_lifetime()
        self.services(stopped, guarded, completed, fresh)
        self.system._sleep_idle(self.deadline)
        self.reports(AUTH in stopped or AUTH in (fresh or {}), extra_reports)
        self.preflight.governance(get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
            get(26657, '/status'), self.policy.CHAIN, get(26657, '/abci_info'))
        require(get(26657, '/unconfirmed_txs?limit=1')['result']['n_txs'] == '0')
        pin = self.expected['services'][SAGE]
        self.listener.listener_owner(pin['pid'], pin['start_ticks'], self.expected['boot_id'])
        health = get(8080, '/v1/dashboard/health')
        validate_health(health, self.expected['native_boot'],
            self.expected['after' if completed else 'before']['version'], completed)
        if UI in stopped:
            self.mesh.b_other_producers()
        return health

    def record(self, phase):
        descriptor = os.open(self.state / 'transition.jsonl', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            self.restart.record(descriptor, {'phase': phase})
        finally:
            os.close(descriptor)

    def apply(self, expected_path, expected_sha):
        self.check()
        require(get(3196, '/state') == {'locked': False})
        self.state.mkdir(mode=0o700)
        self.io.sync_directory(self.state.parent)
        self.io.write_new(self.state / 'transition.jsonl', b'')
        expected_raw = read(expected_path)
        require(digest(expected_raw) == expected_sha and json.loads(expected_raw) == self.expected)
        self.io.write_new(self.state / 'expected.json', expected_raw)
        self.record('intent')
        before = read(CONFIG, self.uid, mode=0o600)
        candidate = self.policy.corrected(before, read('/etc/sage-lantern/initialized-public.json'))
        self.io.write_new(self.state / 'before.yaml', before)
        self.io.write_new(self.state / 'candidate.yaml', candidate)
        self.io.write_new(self.state / 'auth-before.json', self.reports())
        keys = {name: digest(read(name, self.uid, 32, 0o600)) for name in
            ('/var/lib/sage-lantern/sage-personal/agent.key', '/var/lib/sage-lantern/identity/lantern-agent.key')}
        try:
            for service, path in self.guards.items():
                path.parent.mkdir(mode=0o755, exist_ok=True)
                directory = self.policy.directory(path.parent, {0})
                os.close(directory)
                self.io.write_new(path, self.guard_native if service == SAGE else self.guard, 0o644)
            systemctl('daemon-reload')
            self.check(guarded=True)
            stopped = []
            for service in (UI, IDLE):
                if service not in self.guards:
                    continue
                self.check(stopped, guarded=True)
                systemctl('stop', service, timeout=45)
                stopped.append(service)
                self.check(stopped, guarded=True)
            self.mesh.b_empty_workflows(self.expected['scan_config'], self.expected['storage_epoch'],
                lambda: self.check(stopped, guarded=True))
            self.record('empty_workflows_verified')
            self.check(stopped, guarded=True)
            systemctl('stop', AUTH, timeout=45)
            stopped.append(AUTH)
            self.check(stopped, guarded=True)
            self.io.write_new(self.state / 'auth-drained.json', self.reports(True))
            self.io.write_new(self.state / 'before-native', read(BINARY, maximum=536870912), 0o700)
            self.io.write_new(self.state / 'before-manifest.json', read(self.expected['before']['manifest'], maximum=2097152))
            require(digest(read(self.state / 'before-native', maximum=536870912)) == self.expected['before']['binary_sha256'])
            self.record('swap_intent')
            self.check(stopped, guarded=True)
            temporary = BINARY.with_name('.b-policy-' + self.expected['transition_id'])
            self.io.write_new(temporary, read(self.expected['after']['binary'], maximum=536870912), 0o755)
            config_temp = CONFIG.with_name('.b-policy-' + self.expected['transition_id'])
            self.io.write_new(config_temp, candidate)
            os.chown(config_temp, self.uid, pwd.getpwnam('sage-lantern').pw_gid)
            self.check(stopped, guarded=True)
            os.replace(config_temp, CONFIG)
            self.io.sync_directory(CONFIG.parent)
            os.replace(temporary, BINARY)
            self.io.sync_directory(BINARY.parent)
            self.check(stopped, guarded=True, changed=True)
            self.record('swapped')
            result = subprocess.run([PYTHON, '-I', '-B', str(Path(__file__).absolute()),
                '--expected', expected_path, '--expected-sha256', expected_sha, '--request-child'],
                env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=40)
            require(result.returncode == 0 and json.loads(result.stdout) == {'accepted': True, 'completed': False})
            while time.monotonic() < self.deadline:
                try:
                    health = self.check(stopped, guarded=True, changed=True, completed=True)
                    break
                except (ConnectionError, OSError, RuntimeError, ValueError):
                    time.sleep(0.25)
            else:
                raise TimeoutError('completion_unknown')
            require(all(digest(read(name, self.uid, 32, 0o600)) == checksum for name, checksum in keys.items()))
            self.io.write_new(self.state / 'complete.json', json.dumps(dict(
                schema='sage.lantern.b-private-transition-complete.v1', boot_id=self.expected['boot_id'],
                native_boot=health['boot_id'], owner_unlock_required=True, audio_ready=False)).encode())
            self.record('complete_locked_guards_retained')
            return dict(completed=True, owner_unlock_required=True, guards_retained=True)
        except BaseException:
            try:
                systemctl('daemon-reload')
            except BaseException:
                pass
            try:
                self.record('outcome_unknown_guards_retained')
            except BaseException:
                pass
            raise

    def release(self):
        require(json.loads(read(self.state / 'transition.jsonl').splitlines()[-1]) ==
            {'phase': 'complete_locked_guards_retained'})
        receipt = json.loads(read(self.state / 'complete.json'))
        health = self.check((UI, IDLE, AUTH), guarded=True, changed=True, completed=True)
        require(receipt['native_boot'] == health['boot_id'] and receipt['boot_id'] == self.expected['boot_id'])
        self.record('release_intent')
        stage = 'remove_guards'
        try:
            for service in (AUTH, UI):
                path = self.guards[service]
                self.check((UI, IDLE, AUTH), guarded=True, changed=True, completed=True)
                raw = read(path)
                self.io.write_new(self.state / ('released-' + service), raw)
                require(read(path) == raw)
                path.unlink()
                self.io.sync_directory(path.parent)
                self.released.add(service)
            systemctl('daemon-reload')
            self.check((UI, IDLE, AUTH), guarded=RETAINED, changed=True, completed=True)
            stage = 'start_auth'
            systemctl('start', AUTH, timeout=45)
            stage = 'wait_auth_ready'
            auth_pin = self.wait_auth_ready()
            auth = self.properties(AUTH)
            require(auth['ActiveState'] == 'active' and int(auth['MainPID']) > 0
                and auth['InvocationID'] != self.expected['services'][AUTH]['invocation'])
            fresh = read(REPORTS / (auth['InvocationID'] + '.json'), self.uid, 1024, 0o600)
            self.preflight.report(json.loads(fresh), auth['InvocationID'], current=True)
            self.io.write_new(self.state / 'auth-new-locked.json', fresh)
            fresh_pins = {AUTH: auth_pin}
            extra_reports = {auth['InvocationID'] + '.json': digest(fresh)}
            self.check((UI, IDLE), guarded=RETAINED, changed=True, completed=True,
                fresh=fresh_pins, extra_reports=extra_reports)
            stage = 'start_ui'
            systemctl('start', UI, timeout=45)
            require(self.properties(UI)['ActiveState'] == 'active'
                and get(3196, '/state') == {'locked': True})
            fresh_pins[UI] = self.wait_ui_ready(auth_pin)
            final_health = self.check((IDLE,), guarded=RETAINED, changed=True, completed=True,
                fresh=fresh_pins, extra_reports=extra_reports)
            require(final_health['boot_id'] == health['boot_id'] and get(3196, '/state') == {'locked': True})
            self.io.write_new(self.state / 'released.json', json.dumps(dict(
                schema='sage.lantern.b-private-release.v1', boot_id=self.expected['boot_id'],
                native_boot=health['boot_id'], locked=True, owner_unlock_required=True,
                audio_started=False, retained_guards=sorted(RETAINED & set(self.guards)))).encode())
            self.record('released_owner_unlock_required')
            return dict(released=True, owner_unlock_required=True, audio_started=False,
                cold_guards_retained=True)
        except BaseException as error:
            try:
                self.io.write_new(self.state / 'release-error.json', json.dumps(dict(
                    stage=stage, error_class=type(error).__name__,
                    readiness=getattr(self, 'readiness_diagnostic', None))).encode())
            except BaseException:
                pass
            contained = self.contain_release_failure()
            try:
                self.record('release_failed_processes_stopped_auth_cleanup_unknown' if contained
                    else 'release_failed_containment_unverified')
            except BaseException:
                pass
            raise

    def readiness_step(self, tag, operation):
        if not hasattr(self, 'readiness_diagnostic'):
            self.readiness_diagnostic = {'samples': []}
        diagnostic = self.readiness_diagnostic
        sample = {'tag': tag}
        def metadata():
            try:
                value = self.properties(AUTH)
                return {field: value.get(field, '')[:128] for field in ('LoadState', 'ActiveState', 'MainPID',
                    'InvocationID', 'Job', 'NeedDaemonReload', 'Transient')}
            except Exception as error:
                return {'error_class': type(error).__name__}
        sample['before'] = metadata()
        diagnostic['samples'].append(sample)
        del diagnostic['samples'][:-32]
        try:
            result = operation()
            sample['completed'] = True
            return result
        except BaseException as error:
            sample['error_class'] = type(error).__name__
            sample['frames'] = [{'source': Path(frame.filename).name[:128], 'function': frame.name[:128], 'line': frame.lineno}
                for frame in traceback.extract_tb(error.__traceback__)[-8:]]
            diagnostic['failed_tag'] = tag
            raise
        finally:
            sample['after'] = metadata()

    def wait_auth_ready(self):
        deadline = min(self.deadline, time.monotonic() + 30)
        pinned = self.readiness_step('capture_initial', lambda: self.wait_initial_exec(AUTH))
        while time.monotonic() < deadline:
            self.readiness_step('capture_before', lambda: require(self.capture_process(AUTH) == pinned))
            self.readiness_step('services', lambda: self.services((UI, IDLE), guarded=RETAINED,
                completed=True, fresh={AUTH: pinned}))
            self.readiness_step('sleep_gate', lambda: self.system._sleep_idle(deadline))
            http_metadata = {}
            self.readiness_diagnostic['http'] = http_metadata
            try:
                state = self.readiness_step('http_state', lambda: get(3196, '/state', diagnostics=http_metadata))
            except (ConnectionRefusedError, TimeoutError, ConnectionResetError, http.client.RemoteDisconnected):
                state = None
            self.readiness_step('capture_after', lambda: require(self.capture_process(AUTH) == pinned))
            if state is not None:
                self.readiness_step('locked_response', lambda: require(state == {'locked': True}))
                raw = read(REPORTS / (pinned['invocation'] + '.json'), self.uid, 1024, 0o600)
                self.readiness_step('current_receipt', lambda: self.preflight.report(json.loads(raw),
                    pinned['invocation'], current=True))
                return pinned
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError('auth_readiness_deadline')

    def wait_ui_ready(self, auth_pin):
        deadline = min(self.deadline, time.monotonic() + 30)
        pinned = self.readiness_step('capture_ui_initial', lambda: self.wait_initial_exec(UI, auth_pin))
        while time.monotonic() < deadline:
            require(self.capture_process(UI) == pinned and self.capture_process(AUTH) == auth_pin)
            self.services((IDLE,), guarded=RETAINED, completed=True, fresh={AUTH: auth_pin, UI: pinned})
            self.system._sleep_idle(deadline)
            connection = http.client.HTTPConnection('127.0.0.1', 3100, timeout=5)
            status = None
            try:
                connection.request('GET', '/')
                response = connection.getresponse()
                status = response.status
            except (ConnectionRefusedError, TimeoutError, ConnectionResetError, http.client.RemoteDisconnected):
                pass
            finally:
                connection.close()
            require(self.capture_process(UI) == pinned and self.capture_process(AUTH) == auth_pin)
            if status is not None:
                require(status in (200, 307) and get(3196, '/state') == {'locked': True})
                return pinned
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        raise TimeoutError('ui_readiness_deadline')

    def capture_process(self, service):
        pin = self.observe_process(service)
        require(pin['image_sha256'] == self.expected['services'][service]['image_sha256'])
        return pin

    def wait_initial_exec(self, service, auth_pin=None):
        require(service in (AUTH, UI) and (service != UI or type(auth_pin) is dict))
        deadline = min(self.deadline, time.monotonic() + 10)
        require(digest(read(SYSTEMD_IMAGE, maximum=536870912)) == SYSTEMD_SHA)
        initial = self.observe_process(service)
        identity = {key: initial[key] for key in ('pid', 'start_ticks', 'invocation')}
        require(initial['invocation'] != self.expected['services'][service]['invocation'])
        current = initial
        while time.monotonic() < deadline:
            require(all(current[key] == value for key, value in identity.items())
                and current['image_sha256'] in (SYSTEMD_SHA, self.expected['services'][service]['image_sha256']))
            if current['image_sha256'] == self.expected['services'][service]['image_sha256']:
                final = self.capture_process(service)
                require(final == current)
                return final
            require(digest(read(SYSTEMD_IMAGE, maximum=536870912)) == SYSTEMD_SHA)
            fresh = {service: current}
            if service == UI:
                require(self.capture_process(AUTH) == auth_pin)
                fresh[AUTH] = auth_pin
            observed_image = self.services((UI, IDLE) if service == AUTH else (IDLE,),
                guarded=RETAINED, completed=True, fresh=fresh, initial_exec=service)
            self.system._sleep_idle(deadline)
            time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            current = self.observe_process(service)
            if observed_image == self.expected['services'][service]['image_sha256']:
                require(current['image_sha256'] == observed_image)
        raise TimeoutError('preexec_deadline')

    def observe_process(self, service):
        require(service in (AUTH, UI))
        value = self.properties(service)
        public_fields = ('LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'Job', 'NeedDaemonReload', 'Transient')
        capture = {'service': service,
            'properties_before': {field: value.get(field, '')[:128] for field in public_fields}}
        if hasattr(self, 'readiness_diagnostic'):
            self.readiness_diagnostic['capture'] = capture
        require(value['ActiveState'] == 'active' and value['Job'] in ('', '0')
            and re.fullmatch('[1-9][0-9]*', value['MainPID']) is not None
            and re.fullmatch('[0-9a-f]{32}', value['InvocationID']) is not None
            and value['InvocationID'] != '0' * 32
            and value['ControlGroup'] == '/system.slice/' + service)
        process = Path('/proc') / value['MainPID']
        start = int((process / 'stat').read_text().rsplit(')', 1)[1].split()[19])
        with (process / 'exe').open('rb') as stream:
            before = os.fstat(stream.fileno())
            checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
            require(signature(before) == signature(os.fstat(stream.fileno())))
        capture.update(start_ticks=start, executable_sha256=checksum,
            expected_executable_sha256=self.expected['services'][service]['image_sha256'])
        def properties_unchanged():
            after = self.properties(service)
            capture['properties_after'] = {field: after.get(field, '')[:128] for field in public_fields}
            return after == value
        require(properties_unchanged()
            and int((process / 'stat').read_text().rsplit(')', 1)[1].split()[19]) == start)
        return dict(pid=int(value['MainPID']), start_ticks=start,
            invocation=value['InvocationID'], image_sha256=checksum)

    def contain_release_failure(self):
        failures = []
        for service in (AUTH, UI):
            try:
                path = self.guards[service]
                if os.path.lexists(path):
                    require(read(path) == self.guard)
                else:
                    descriptor = self.policy.directory(path.parent, {0})
                    os.close(descriptor)
                    self.io.write_new(path, self.guard, 0o644)
                self.released.discard(service)
            except BaseException:
                failures.append(service)
        try:
            systemctl('daemon-reload')
        except BaseException:
            failures.append('reload')
        for service in (UI, AUTH):
            try:
                value = self.properties(service)
                if value['MainPID'] != '0':
                    pinned = self.capture_process(service)
                    require(self.capture_process(service) == pinned)
                    systemctl('stop', service, timeout=45)
            except BaseException:
                failures.append(service)
        try:
            self.files(changed=True)
            self.services((UI, IDLE, AUTH), guarded=True, completed=True)
            self.mesh.b_other_producers()
            pin = self.expected['services'][SAGE]
            self.listener.listener_owner(pin['pid'], pin['start_ticks'], self.expected['boot_id'])
            validate_health(get(8080, '/v1/dashboard/health'), self.expected['native_boot'],
                self.expected['after']['version'], True)
        except BaseException:
            failures.append('verification')
        return not failures

    def request_child(self):
        require(json.loads(read(self.state / 'expected.json')) == self.expected)
        require(json.loads(read(self.state / 'transition.jsonl').splitlines()[-1]) == {'phase': 'swapped'})
        self.check((UI, IDLE, AUTH), guarded=True, changed=True)
        key, info = self.restart.opened(self.restart.KEY, self.uid, 0o600, 32, parent_owners={0, self.uid})
        journal = None
        try:
            require(info.st_size == 32)
            journal = os.open(self.state / 'signed-request.jsonl', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            self.restart.record(journal, {'phase': 'intent', 'unit_id': 'unit-b'})
            self.io.sync_directory(self.state)
            os.setgroups([])
            os.setgid(pwd.getpwnam('sage-lantern').pw_gid)
            os.setuid(self.uid)
            os.environ.clear()
            os.environ.update(ENV)
            from sage_sdk import AgentIdentity
            identity = AgentIdentity.from_file('/proc/self/fd/' + str(key))
            require(identity.agent_id == self.policy.ROOT
                and signature(info) == signature(os.fstat(key)) == signature(self.restart.KEY.lstat()))
            result = self.restart.request_restart(identity)
            self.restart.record(journal, result)
            return result
        finally:
            os.close(key)
            if journal is not None:
                os.close(journal)


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--request-child', action='store_true')
    mode.add_argument('--release', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    read(Path(__file__).absolute())
    raw = read(args.expected)
    require(digest(raw) == args.expected_sha256)
    require(json.loads(raw)['files'].get(str(Path(__file__).absolute())) == digest(read(Path(__file__).absolute())))
    backend = Backend(json.loads(raw))
    if args.request_child:
        result = backend.request_child()
    elif args.apply:
        result = backend.apply(args.expected, args.expected_sha256)
    elif args.release:
        result = backend.release()
    else:
        backend.check()
        result = dict(inspected=True, applied=False, quiescence_pending=True)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B transition refused or unknown; retain journals/guards, do not retry.') from None
