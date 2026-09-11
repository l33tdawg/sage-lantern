#!/usr/bin/env python3
"""Read-only stale storage-epoch diagnosis; never containment or restart authority.

--expected and --expected-sha256 pin a root-owned JSON object with exact fields:
schema (sage.lantern.stale-audio-inspection.v1), unit_id, boot_id, chain_id,
agent_id, bootstrap_path, bootstrap_sha256, fence_sha256, files (absolute path
to SHA256), runtime_sha256 (installed module basename to SHA256), and services.
services contains each SERVICES unit with pid, start_ticks, uid, invocation,
argv (exact list), image_sha256, and dropins (exact absolute path list).
files must include CONFIG, every service fragment/drop-in and VOICE_HELPER.
The archived completed bootstrap must identify these same live producers.

Native execution reads the existing local identity and makes two signed storage
status GETs, never exports keys or response bodies. No supervisor is constructed,
no IPC drain is requested, no lock/journal/receipt is written or removed. A
different instance, backward generation, changed process or unstable observation
refuses. Evidence proves an observed mismatch, not its cause or work completion.
Use installed Python with -I -B. There is deliberately no --apply option.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import time
import uuid


BROKER = 'sage-lantern-mesh-runtime.service'
VOICE = 'sage-lantern-voice-loop.service'
SERVICES = (BROKER, VOICE, 'sage-lantern-personal-sage.service',
            'sage-lantern-device-auth.service', 'sage-lantern-llm.service')
SYSTEMD = '/etc/systemd/system/'
CONFIG = '/etc/sage-lantern/mesh-runtime.json'
VOICE_HELPER = '/usr/local/libexec/sage-lantern-voice-loop'
FENCE = '/var/lib/sage-lantern-mesh-supervisor/fence.json'
RUNTIME = Path('/opt/sage-lantern/venv/lib/python3.11/site-packages/sage_lantern')
FIELDS = ('LoadState', 'ActiveState', 'MainPID', 'InvocationID', 'Job', 'ControlGroup',
          'FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'KillMode', 'Slice')


def require(value):
    if not value:
        raise ValueError('stale_audio_inspection_refused')


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


def hexadecimal(value, length=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(length) + '}', value) is not None


def epoch(value):
    require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}:[1-9][0-9]{0,19}', value))
    instance, generation = value.split(':')
    require(int(generation) <= 2**64 - 1)
    return instance, int(generation)


def absolute(value):
    return type(value) is str and value.startswith('/') and str(Path(value)) == value and '..' not in Path(value).parts


def validate(value):
    from sage_lantern.deployment_bringup import REQUIRED
    require(type(value) is dict and set(value) == {'schema', 'unit_id', 'boot_id', 'chain_id', 'agent_id',
        'bootstrap_path', 'bootstrap_sha256', 'fence_sha256', 'files', 'runtime_sha256', 'services'})
    require(value['schema'] == 'sage.lantern.stale-audio-inspection.v1' and value['unit_id'] in ('unit-a', 'unit-b'))
    require(str(uuid.UUID(value['boot_id'])) == value['boot_id'])
    require(type(value['chain_id']) is str and re.fullmatch(r'sage-personal-[A-Za-z0-9._-]{1,110}', value['chain_id']))
    require(hexadecimal(value['agent_id']) and absolute(value['bootstrap_path'])
            and hexadecimal(value['bootstrap_sha256']) and hexadecimal(value['fence_sha256']))
    require(type(value['services']) is dict and set(value['services']) == set(SERVICES))
    required_files = set(REQUIRED) | {VOICE_HELPER}
    for service, pin in value['services'].items():
        require(type(pin) is dict and set(pin) == {'pid', 'start_ticks', 'uid', 'invocation', 'argv', 'image_sha256', 'dropins'})
        require(type(pin['pid']) is int and pin['pid'] > 0 and type(pin['uid']) is int and pin['uid'] >= 0)
        require(type(pin['start_ticks']) is str and re.fullmatch('[1-9][0-9]{0,19}', pin['start_ticks']))
        require(hexadecimal(pin['invocation'], 32) and hexadecimal(pin['image_sha256']))
        require(type(pin['argv']) is list and 1 <= len(pin['argv']) <= 128
                and all(type(part) is str and '\x00' not in part and len(part) <= 4096 for part in pin['argv']))
        require(type(pin['dropins']) is list and len(pin['dropins']) <= 32 and len(set(pin['dropins'])) == len(pin['dropins']))
        require(all(absolute(path) and str(Path(path).parent) == SYSTEMD + service + '.d' for path in pin['dropins']))
        require({path for path in REQUIRED if path.startswith(SYSTEMD + service + '.d/')} <= set(pin['dropins']))
        required_files |= {SYSTEMD + service, *pin['dropins']}
    require(value['services'][BROKER]['uid'] == 0 and value['services'][VOICE]['uid'] > 0)
    require(type(value['files']) is dict and set(value['files']) == required_files
            and all(hexadecimal(checksum) for checksum in value['files'].values()))
    modules = value['runtime_sha256']
    require(type(modules) is dict and 1 <= len(modules) <= 128
            and all(re.fullmatch(r'[a-z_][a-z0-9_]*\.py', name) and hexadecimal(checksum) for name, checksum in modules.items()))


def file_identity(info):
    return tuple(getattr(info, field) for field in ('st_dev', 'st_ino', 'st_uid', 'st_mode', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns'))


def public_bytes(name):
    require(absolute(str(name)))
    path = Path(name)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        require(len(raw) == before.st_size and file_identity(before) == file_identity(os.fstat(descriptor))
                and file_identity(before) == file_identity(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


class Native:
    def __init__(self, timeout):
        self.deadline = time.monotonic() + timeout

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0)
        return remaining

    def files(self, expected):
        for name, checksum in expected['files'].items():
            require(sha(public_bytes(name)) == checksum)
            self.remaining()
        require({path.name for path in RUNTIME.glob('*.py')} == set(expected['runtime_sha256']))
        for name, checksum in expected['runtime_sha256'].items():
            require(sha(public_bytes(RUNTIME / name)) == checksum)
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == expected['boot_id'])

    def service(self, service, pin):
        command = ['/usr/bin/systemctl', 'show', '--no-pager']
        for field in FIELDS:
            command += ['-p', field]
        result = subprocess.run(command + [service], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, timeout=min(5, self.remaining()), check=True,
            env={'PATH': '/usr/bin:/bin', 'LANG': 'C'})
        require(len(result.stdout) <= 16384)
        rows = result.stdout.decode().splitlines()
        require(all('=' in row for row in rows))
        values = decode(json.dumps(dict(row.split('=', 1) for row in rows)))
        require(len(rows) == len(values) and set(values) == set(FIELDS))
        require(values['LoadState'] == 'loaded' and values['ActiveState'] == 'active'
                and values['MainPID'] == str(pin['pid']) and values['InvocationID'] == pin['invocation']
                and values['Job'] in ('', '0') and values['NeedDaemonReload'] == 'no'
                and values['FragmentPath'] == SYSTEMD + service and values['KillMode'] == 'control-group'
                and values['Slice'] == 'system.slice' and set(values['DropInPaths'].split()) == set(pin['dropins']))
        group = '/system.slice/' + service
        require(values['ControlGroup'] == group)
        process = Path('/proc') / str(pin['pid'])
        before = (process / 'stat').read_text().rsplit(')', 1)[1].split()[19]
        require(before == pin['start_ticks'] and process.stat().st_uid == pin['uid'])
        require((process / 'cmdline').read_bytes() == b'\x00'.join(part.encode() for part in pin['argv']) + b'\x00')
        require((process / 'cgroup').read_text().splitlines() == ['0::' + group])
        with (process / 'exe').open('rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_size <= 536870912)
            digest = hashlib.sha256()
            while chunk := stream.read(1048576):
                self.remaining()
                digest.update(chunk)
            require(digest.hexdigest() == pin['image_sha256'] and file_identity(info) == file_identity(os.fstat(stream.fileno())))
            current = (process / 'exe').stat()
            require((current.st_dev, current.st_ino) == (info.st_dev, info.st_ino))
        require((process / 'stat').read_text().rsplit(')', 1)[1].split()[19] == before)

    def records(self, expected):
        bootstrap = public_bytes(expected['bootstrap_path'])
        fence = public_bytes(FENCE)
        require(sha(bootstrap) == expected['bootstrap_sha256'] and sha(fence) == expected['fence_sha256'])
        return decode(bootstrap), decode(fence)

    def snapshot(self, expected):
        from sage_lantern.mesh_daemon import load_config, load_identity
        from sage_lantern.message_contacts import RecipientID
        from sage_lantern.message_storage import MessageStorageProbe
        from sage_sdk import SageClient
        config = load_config()
        require(config['unit_id'] == expected['unit_id'] and config['chain_id'] == expected['chain_id']
                and config['agent_id'] == expected['agent_id'] and config['voice_uid'] == expected['services'][VOICE]['uid'])
        identity, descriptor = load_identity(config)
        try:
            with SageClient(config['endpoint'], identity, timeout=min(5, self.remaining()), trust_env=False) as client:
                probe = MessageStorageProbe(client, actor=RecipientID(config['chain_id'], identity.agent_id), endpoint=config['endpoint'])
                return probe.snapshot().epoch
        finally:
            os.close(descriptor)


def inspect(expected, native):
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    validate(expected)
    def observe():
        native.files(expected)
        for service in SERVICES:
            native.service(service, expected['services'][service])
        return native.records(expected)
    bootstrap, fence = observe()
    require(type(bootstrap) is dict and set(bootstrap) == {'schema', 'phase', 'boot_id', 'pid', 'inventory', 'evidence'}
            and bootstrap['schema'] == 'sage.lantern.voice-bootstrap.v1' and bootstrap['phase'] == 'complete'
            and bootstrap['boot_id'] == expected['boot_id'])
    evidence = bootstrap['evidence']
    require(type(evidence) is dict and set(evidence) == {'broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'}
            and hexadecimal(evidence['broker_id'], 32))
    require(type(evidence['broker_pid']) is int and type(evidence['voice_pid']) is int
            and evidence['broker_pid'] == expected['services'][BROKER]['pid']
            and evidence['voice_pid'] == expected['services'][VOICE]['pid'])
    require(fence == dict(schema='sage.lantern.storage-supervisor.v1', phase='active', broker_id=evidence['broker_id']))
    require(type(bootstrap['inventory']) is dict and bool(bootstrap['inventory']))
    ColdBrokerBootstrap(bootstrap['inventory'])
    require(all(expected['files'].get(name) == checksum for name, checksum in bootstrap['inventory'].items()))
    before = native.snapshot(expected)
    prior_instance, prior_generation = epoch(evidence['vault_epoch'])
    instance, generation = epoch(before)
    require(instance == prior_instance and generation > prior_generation)
    require(observe() == (bootstrap, fence) and native.snapshot(expected) == before)
    require(observe() == (bootstrap, fence))
    native.remaining()
    return dict(schema='sage.lantern.stale-audio-observation.v1', unit_id=expected['unit_id'],
        stale_epoch_verified=True, same_storage_instance=True, baseline_epoch=evidence['vault_epoch'],
        current_epoch=before, bootstrap_sha256=expected['bootstrap_sha256'], fence_sha256=expected['fence_sha256'],
        mutations_performed=False, containment_verified=False, drain_verified=False,
        historical_work_disposition='unknown', rebootstrap_authorized=False, native_update_authorized=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--timeout', type=int, default=30)
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and 1 <= args.timeout <= 60)
    def expired(signum, frame):
        raise TimeoutError('stale_audio_inspection_deadline')
    previous = signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, args.timeout)
    try:
        raw = public_bytes(args.expected)
        require(hexadecimal(args.expected_sha256) and sha(raw) == args.expected_sha256)
        result = inspect(decode(raw), Native(args.timeout))
        require(public_bytes(args.expected) == raw)
        result['expected_sha256'] = args.expected_sha256
        print(json.dumps(result, sort_keys=True))
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(verified=False, error_type=type(error).__name__, mutations_performed=False)))
        raise SystemExit(1)
