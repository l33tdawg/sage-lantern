#!/usr/bin/env python3
"""Read-only Unit A post-onboarding cold-update observation, NOT admission.

Run installed Python -I -B with --expected ROOT_OWNED_JSON --expected-sha256 SHA.
Expected inputs are reviewed public pins, never discovered-and-approved here.
No restart, drain, fence, install, key access, or automatic recovery is performed.
A successful observation expires immediately; the mutation owner must serialize
and revalidate, drain auth, and use the separately reviewed signed request helper.
Linux recovery is manual, including after a new process has entered.

Expected JSON schema sage.lantern.cold-native-preflight.v1 has exactly:
unit_id (unit-a), boot_id (Linux UUID), chain_id, root_id, files (absolute path
to SHA256 map), native_before/native_after (each {binary: path, manifest: path}),
backup (separate old binary path), restart_helper (reviewed helper path), and
services (auth and personal-sage unit names, each with pid, invocation,
start_ticks, executable, argv). All required installed configuration/receipts,
active executables, auth script, both native manifests/binaries, backup, helper,
and every loaded service fragment/drop-in must occur in files. Stage artifacts
under root-controlled directories first. Current YAML must have a separately
reviewed post-onboarding pin; its original commissioning hash is not replaced.
The backup must already exist: this command will not create it.
"""

import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import pwd
import re
import signal
import stat
import sys
import time
import types
import uuid

from sage_lantern.suspend_kiosk import LinuxProcesses, load_kiosk_binding
from sage_lantern.suspend_system import SuspendSystem


AUTH = 'sage-lantern-device-auth.service'
SAGE = 'sage-lantern-personal-sage.service'
COLD = tuple('sage-lantern-' + name + '.service' for name in
             ('voice-loop', 'mesh-runtime', 'llm', 'idle-suspend'))
BINARY = '/opt/sage-lantern/bin/sage-gui'
RESTART_SHA = '4931d2f10a98b69f668b46b471eeb8fb3f84efd0d467ca2349db76cbd976327f'
CONFIG = '/etc/sage-lantern/'
REPORTS = Path('/var/lib/sage-lantern/device-lock/drain')
YAML = '/var/lib/sage-lantern/sage-personal/config.yaml'
REQUIRED = {CONFIG + name for name in ('unit.env', 'initialized-public.json',
    'appliance-installed.json', 'appliance-bindings.json', 'commissioning-complete.json',
    'commissioned-bindings.json', 'lantern.toml')} | {YAML}
ABSENT = (CONFIG + 'mesh-runtime.json', CONFIG + 'commissioning.pending.json',
    '/var/lib/sage-lantern/LOCAL_VOICE_READY', '/run/sage-lantern-native-restart-request.jsonl',
    '/run/sage-lantern-deployment.pending', '/run/sage-lantern-sleep.fenced')
FIELDS = ('LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'Job', 'ControlGroup',
          'Slice', 'KillMode', 'User', 'FragmentPath', 'DropInPaths', 'NeedDaemonReload')


def require(value):
    if not value:
        raise ValueError('cold_preflight_refused')


def pairs(items):
    result = {}
    for key, value in items:
        require(key not in result)
        result[key] = value
    return result


def decode(raw):
    return json.loads(raw, object_pairs_hook=pairs)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def trusted(path, owner=0, limit=128 * 1024 * 1024):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid in (0, owner) and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == owner and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= limit)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(limit + 1)
        def identity(info):
            return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) == before.st_size and identity(before) == identity(os.fstat(descriptor))
                and identity(before) == identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def report(value, invocation, *, current):
    require(type(value) is dict and set(value) == {'version', 'instance_id', 'closing',
        'drained', 'local_revoked', 'upstream_cleanup'})
    require(type(value['version']) is int and value['version'] == 1 and value['instance_id'] == invocation)
    require(all(value[name] is (False if current else True) for name in ('closing', 'drained', 'local_revoked')))
    require(value['upstream_cleanup'] == ('unknown' if current else 'confirmed'))


def governance(envelope, chain_status, chain_id, abci_info):
    """Gate on SAGE currentAppVersion, not Comet's startup P2P advertisement."""
    require(envelope.get('error') is None and chain_status.get('error') is None)
    require(type(abci_info) is dict and abci_info.get('error') is None
            and abci_info['result']['response']['app_version'] == '27')
    response = envelope['result']['response']
    require(type(response['code']) is int and response['code'] == 0)
    value = decode(base64.b64decode(response['value'], validate=True))
    require(set(value) == {'schema', 'current_app_version', 'pending_plan', 'active_proposal'}
        and value['schema'] == 'sage-upgrade-governance-status/v1'
        and type(value['current_app_version']) is int and value['current_app_version'] == 27
        and value['pending_plan'] is None and value['active_proposal'] is None)
    require(chain_status['result']['node_info']['network'] == chain_id
        and chain_status['result']['sync_info']['catching_up'] is False)


class Observation:
    def __init__(self, *, recovery_entry=False):
        require(type(recovery_entry) is bool)
        self.recovery_entry = recovery_entry
        self.unknown_receipts = {}
        self.system = SuspendSystem()
        self.deadline = time.monotonic() + 45
        self.uid = pwd.getpwnam('sage-lantern').pw_uid
        require(self.uid > 0)

    def remaining(self):
        return self.system._remaining(self.deadline)

    def get(self, port, route):
        connection = http.client.HTTPConnection('127.0.0.1', port, timeout=min(5, self.remaining()))
        try:
            connection.request('GET', route)
            response = connection.getresponse()
            raw = response.read(65537)
            self.remaining()
            require(response.status == 200 and len(raw) <= 65536)
            return decode(raw)
        finally:
            connection.close()

    def service(self, name, expected, files):
        value = self.system._show(name, self.deadline, FIELDS)
        canonical = '/system.slice/' + name
        if name == 'sage-lantern-idle-suspend.service' and expected is None and value['LoadState'] == 'not-found':
            require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                and value['Job'] in ('', '0') and value['ControlGroup'] == ''
                and value['FragmentPath'] == '' and value['DropInPaths'] == ''
                and value['InvocationID'] == '' and value['User'] == ''
                and value['NeedDaemonReload'] == 'no' and value['Slice'] in ('', 'system.slice')
                and value['KillMode'] in ('', 'control-group'))
            self.system._empty_tree(self.system._cgroup(canonical), self.deadline)
            require(self.system._show(name, self.deadline, FIELDS) == value)
            return value
        require(value['LoadState'] == 'loaded' and value['NeedDaemonReload'] == 'no'
            and value['Job'] in ('', '0') and value['Slice'] == 'system.slice'
            and value['KillMode'] == 'control-group' and value['FragmentPath'] in files
            and all(path in files for path in value['DropInPaths'].split()))
        if expected is None:
            require(value['ActiveState'] == 'inactive' and value['MainPID'] == '0'
                    and value['ControlGroup'] in ('', canonical))
            self.system._empty_tree(self.system._cgroup(canonical), self.deadline)
        else:
            require(set(expected) == {'pid', 'invocation', 'start_ticks', 'executable', 'argv'})
            require(type(expected['pid']) is int and expected['pid'] > 0
                and type(expected['start_ticks']) is int and expected['start_ticks'] > 0
                and re.fullmatch('[0-9a-f]{32}', expected['invocation'])
                and expected['invocation'] != '0' * 32)
            require(value['ActiveState'] == 'active' and value['MainPID'] == str(expected['pid'])
                and value['InvocationID'] == expected['invocation'] and value['User'] == 'sage-lantern'
                and value['ControlGroup'] == canonical)
            process = LinuxProcesses().read(expected['pid'])
            require(process.uid == self.uid and process.start_ticks == expected['start_ticks']
                and process.executable == expected['executable'] and list(process.argv) == expected['argv']
                and process.state not in ('Z', 'T', 't', 'X'))
            require(expected['executable'] in files)
            running = Path('/proc', str(expected['pid']), 'exe').stat()
            installed = Path(expected['executable']).stat()
            require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
            require(Path('/proc', str(expected['pid']), 'cgroup').read_text() == '0::' + canonical + '\n')
            if name == SAGE:
                require(expected['executable'] == BINARY and expected['argv'] == [BINARY, 'serve'])
            else:
                require(expected['argv'][1:] == ['/usr/local/libexec/sage-lantern-device-auth',
                    '--state-dir', '/var/lib/sage-lantern/device-lock', '--idle-seconds', '120',
                    '--operator-key', '/var/lib/sage-lantern/sage-personal/agent.key',
                    '--setup-marker', '/etc/sage-lantern-vault-setup.enabled',
                    '--drain-report-dir', str(REPORTS)])
                require(expected['argv'][0] == '/opt/sage-lantern/venv/bin/python')
                require('/usr/local/libexec/sage-lantern-device-auth' in files
                    and Path(expected['argv'][0]).resolve() == Path(expected['executable']))
        require(self.system._show(name, self.deadline, FIELDS) == value)
        return value

    def observe(self, expected):
        require(set(expected) == {'schema', 'unit_id', 'boot_id', 'chain_id', 'root_id',
            'files', 'native_before', 'native_after', 'backup', 'restart_helper', 'services'})
        require(expected['schema'] == 'sage.lantern.cold-native-preflight.v1' and expected['unit_id'] == 'unit-a'
            and str(uuid.UUID(expected['boot_id'])) == expected['boot_id'])
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected['boot_id'])
        binding = load_kiosk_binding()
        require((binding.unit_id, binding.account, binding.uid) == ('unit-a', 'radxa', 1000))
        require(all(not os.path.lexists(path) for path in ABSENT))
        require(Path('/sys/fs/cgroup/cgroup.controllers').is_file())
        files = expected['files']
        require(type(files) is dict and REQUIRED <= set(files) and len(files) <= 64)
        require(files[expected['restart_helper']] == RESTART_SHA)
        raw = {}
        for path, digest in files.items():
            require(not any(part.endswith('.key') or 'cookie' in part.lower() or 'xauthority' in part.lower()
                            or part.startswith('priv_validator') or part == 'node_key.json'
                            for part in Path(path).parts))
            require(re.fullmatch('[0-9a-f]{64}', digest))
            raw[path] = trusted(path, self.uid if path == YAML else 0)
            require(sha(raw[path]) == digest)
            self.remaining()
        initialized = decode(raw[CONFIG + 'initialized-public.json'])
        require(initialized['schema'] == 'sage.lantern.initialized.v1'
                and re.fullmatch('[0-9a-f]{64}', expected['root_id']) and expected['root_id'] != '0' * 64
                and initialized['unit_id'] == 'unit-a' and initialized['root_id'] == expected['root_id']
                and initialized['chain_id'] == expected['chain_id'])
        commission = decode(raw[CONFIG + 'commissioning-complete.json'])
        require(commission['schema'] == 'sage.lantern.commission-complete.v1' and commission['unit_id'] == 'unit-a')
        for name, filename in (('lantern.toml', 'lantern.toml'), ('bindings.json', 'commissioned-bindings.json'),
                               ('initialized-public.json', 'initialized-public.json')):
            require(commission['output_sha256'][name] == files[CONFIG + filename])
        for name in ('native_before', 'native_after'):
            artifact = expected[name]
            require(set(artifact) == {'binary', 'manifest'} and artifact['binary'] in files and artifact['manifest'] in files)
            require(decode(raw[artifact['manifest']])['binary_sha256'] == files[artifact['binary']])
        require(expected['native_before']['binary'] == BINARY and expected['native_after']['binary'] != BINARY
            and expected['backup'] not in (BINARY, expected['native_after']['binary'])
            and files[expected['backup']] == files[BINARY]
            and files[expected['native_after']['binary']] != files[BINARY])
        require(set(expected['services']) == {AUTH, SAGE})
        services = {name: self.service(name, expected['services'].get(name), files) for name in (AUTH, SAGE, *COLD)}
        self.system._sleep_idle(self.deadline)
        invocation = expected['services'][AUTH]['invocation']
        info = REPORTS.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == self.uid and stat.S_IMODE(info.st_mode) == 0o700)
        reports = {}
        self.unknown_receipts = {}
        for entry in REPORTS.iterdir():
            require(len(reports) < 1024 and re.fullmatch('[0-9a-f]{32}\\.json', entry.name))
            report_raw = trusted(entry, self.uid, 512)
            require(stat.S_IMODE(entry.lstat().st_mode) == 0o600)
            value = decode(report_raw)
            if entry.stem != invocation and getattr(self, 'recovery_entry', False) and value.get('upstream_cleanup') == 'unknown':
                report(value, entry.stem, current=True)
                self.unknown_receipts[entry.stem] = sha(report_raw)
            else:
                report(value, entry.stem, current=entry.stem == invocation)
            reports[entry.name] = sha(report_raw)
        require(invocation + '.json' in reports)
        locked = self.get(3196, '/state')
        ready = self.get(3196, '/sleep-ready')
        require(set(locked) == {'locked'} and locked['locked'] is True
                and set(ready) == {'ready'} and ready['ready'] is True)
        governance(self.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
                   self.get(26657, '/status'), expected['chain_id'], self.get(26657, '/abci_info'))
        self.remaining()
        return services, reports

    def read_supersession(self, path, expected_sha256, *, helper_sha256, binding, manifest_path):
        """Consume only while Curie's durable update guard and auth stop remain.

        binding is the separately pinned transition context, not fields copied
        from the evidence being checked. No normal active-auth admission here.
        """
        helper_path = Path(__file__).with_name('auth-capability-supersession.py')
        helper_raw = trusted(helper_path, limit=65536)
        require(sha(helper_raw) == helper_sha256)
        validator = types.ModuleType('auth_capability_supersession')
        exec(compile(helper_raw, str(helper_path), 'exec'), validator.__dict__)
        raw = trusted(path, limit=256 * 1024)
        require(sha(raw) == expected_sha256)
        evidence = decode(raw)
        def observe():
            guard = Path('/var/lib/sage-lantern-cold-native-update')
            info = guard.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
            require(load_kiosk_binding().unit_id == binding['unit_id'])
            initialized_raw = trusted(CONFIG + 'initialized-public.json', limit=65536)
            initialized = decode(initialized_raw)
            require(sha(initialized_raw) == binding['initialized_sha256']
                and initialized['root_id'] == binding['root_id'] and initialized['chain_id'] == binding['chain_id'])
            manifest_raw = trusted(manifest_path)
            manifest = decode(manifest_raw)
            image = dict(binary_sha256=sha(trusted(BINARY)), manifest_sha256=sha(manifest_raw),
                         inventory_sha256=manifest['inventory_sha256'], version=manifest['version'])
            require(manifest['binary_sha256'] == image['binary_sha256'] and image == binding['native_after'])
            for service in (AUTH, *COLD):
                value = self.system._show(service, self.deadline, FIELDS + ('Restart',))
                if service == 'sage-lantern-idle-suspend.service' and value['LoadState'] == 'not-found':
                    self.service(service, None, {})
                    continue
                require(value['LoadState'] == 'loaded' and value['ActiveState'] == 'inactive'
                    and value['MainPID'] == '0' and value['Job'] in ('', '0')
                    and value['ControlGroup'] in ('', '/system.slice/' + service)
                    and value['Slice'] == 'system.slice' and value['KillMode'] == 'control-group'
                    and value['Restart'] == 'no' and value['NeedDaemonReload'] == 'no')
                self.system._guard(service, self.deadline, str(guard))
                self.system._empty_tree(self.system._cgroup('/system.slice/' + service), self.deadline)
                require(self.system._show(service, self.deadline, tuple(value)) == value)
            service = self.system._show(SAGE, self.deadline, FIELDS)
            pinned = binding['sage_before']
            require(service['ActiveState'] == 'active' and service['MainPID'] == str(pinned['pid'])
                and service['InvocationID'] == pinned['invocation'] and service['Job'] in ('', '0')
                and service['User'] == 'sage-lantern' and service['ControlGroup'] == '/system.slice/' + SAGE)
            process = LinuxProcesses().read(pinned['pid'])
            require(process.uid == self.uid == pinned['uid'] and process.start_ticks == pinned['start_ticks']
                and process.executable == BINARY and process.argv == (BINARY, 'serve')
                and process.state not in ('Z', 'T', 't', 'X'))
            running, installed = Path('/proc', str(pinned['pid']), 'exe').stat(), Path(BINARY).stat()
            require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
            listener = validator.listener_owner(pinned['pid'], pinned['start_ticks'], binding['boot_id'])
            health = self.get(8080, '/v1/dashboard/health')
            require(validator.listener_owner(pinned['pid'], pinned['start_ticks'], binding['boot_id']) == listener)
            require(health['sage'] == 'running' and health['version'] == image['version'])
            after = dict(pinned, application_boot_id=health['boot_id'])
            report_info = REPORTS.lstat()
            require(stat.S_ISDIR(report_info.st_mode) and report_info.st_uid == self.uid
                    and stat.S_IMODE(report_info.st_mode) == 0o700)
            receipts = {}
            for entry in REPORTS.iterdir():
                require(len(receipts) < 1024 and re.fullmatch('[0-9a-f]{32}\\.json', entry.name))
                receipts[entry.stem] = trusted(entry, self.uid, 512)
                require(stat.S_IMODE(entry.lstat().st_mode) == 0o600)
            live = dict(boot_id=Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                sage_after=after, native_after=image, auth_inactive=True, auth_cgroup_empty=True,
                starts_inhibited=True, encrypted=health['encrypted'], vault_locked=health['vault_locked'])
            result = validator.validate(evidence, binding=binding, receipts=receipts, live=live)
            require(self.system._show(SAGE, self.deadline, FIELDS) == service)
            self.remaining()
            return result, receipts, live
        before = observe()
        require(observe() == before and trusted(path, limit=256 * 1024) == raw)
        self.remaining()
        return before[0]


def verify(expected, observer):
    first = observer.observe(expected)
    require(observer.observe(expected) == first)
    observer.remaining()
    if getattr(observer, 'recovery_entry', False):
        return dict(recovery_entry_verified=True, preflight_verified=False, unit_id='unit-a',
            mutation_authorized=False, current_auth_drain_required=True,
            capability_invalidated=False, historical_request_completion='unknown',
            unknown_receipts=dict(observer.unknown_receipts))
    return dict(preflight_verified=True, unit_id='unit-a', mutation_authorized=False,
                auth_drained=False, completion_verified=False, recovery='manual-only')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', type=Path, required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--recovery-entry', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux')
    def expired(signum, frame):
        raise TimeoutError('cold_preflight_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(45)
    try:
        raw = trusted(args.expected, limit=65536)
        require(sha(raw) == args.expected_sha256)
        print(json.dumps(verify(decode(raw), Observation(recovery_entry=args.recovery_entry)), sort_keys=True))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Cold preflight refused; no mutation authorized.', file=sys.stderr)
        raise SystemExit(2) from None
