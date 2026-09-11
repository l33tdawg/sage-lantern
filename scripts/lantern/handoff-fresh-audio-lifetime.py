#!/usr/bin/env python3
"""Fresh explicitly bound A/B audio lifetime -> independent startup policy handoff.

Default inspection is read-only. Apply consumes an exact reviewed inspection
hash, retains the real bootstrap receipt and writes new policy candidates.
Never starts/enables services, changes commissioning, reads key files or creates
workflow records. Requires independently commissioned and already active unit.
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
import uuid


HANDOFF = Path('/var/lib/sage-lantern-first-audio-handoff')
LIFETIMES = Path('/var/lib/sage-lantern-cross-boot-audio')
RECORD = Path('/run/sage-lantern-voice-bootstrap.json')


def require(value):
    if value is not True:
        raise ValueError('first_audio_handoff_refused')


def unique(pairs):
    value = {}
    for key, member in pairs:
        require(key not in value)
        value[key] = member
    return value


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


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
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, key) == getattr(after, key)
            for after in (os.fstat(descriptor), path.lstat()) for key in fields))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = trusted(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def load(pin):
    raw = pinned(pin)
    module = types.ModuleType('reviewed_first_handoff_dependency')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def validate_unit(metadata, config, unit_id):
    require(unit_id in ('unit-a', 'unit-b'))
    pairs = [line.split('=', 1) for line in metadata.decode().splitlines()]
    require(all(len(pair) == 2 for pair in pairs))
    require(unique(pairs) == {'UNIT_ID': unit_id, 'PROVISIONING_SCHEMA': 'sage-lantern-unit-v1'}
            and config['unit_id'] == unit_id)


def identity_boundary(tools, config, plan, receipt, account, unit_id, plan_sha, receipt_sha):
    expected = tools['identity']
    require(tools['unit_id'] in ('unit-a', 'unit-b') and unit_id == tools['unit_id'])
    require(plan['schema'] == 'sage.lantern.unit-plan.v1' and plan['unit_id'] == unit_id
        and receipt['schema'] == 'sage.lantern.fresh-app-install.v1' and receipt['unit_id'] == unit_id
        and receipt['installed'] is True and receipt['lock_required'] is True)
    require(expected == dict(unit_id=unit_id, operator_account=account,
        chain_id=config['chain_id'], agent_id=config['agent_id'])
        and plan['operator_account'] == account and config['unit_id'] == unit_id)
    return dict(identity=expected, appliance_plan_sha256=plan_sha, appliance_receipt_sha256=receipt_sha)


def documents(plan, tools, record):
    require(plan['unit_id'] in ('unit-a', 'unit-b') and plan['unit_id'] == tools['unit_id']
        and sha(record) == plan['bootstrap_sha256'])
    boot = plan['boot_id']
    require(str(uuid.UUID(boot)) == boot)
    directory = HANDOFF / boot
    lifetime = LIFETIMES / boot / 'new-lifetime.json'
    baseline_raw = canonical(plan['baseline'])
    policy = dict(schema='sage.lantern.startup-audio-policy.v1',
        **{name: tools[name] for name in ('helper', 'collector', 'runtime_wheel')},
        baseline=dict(path=str(directory / 'baseline.json'), sha256=sha(baseline_raw)))
    policy_raw = canonical(policy)
    config = dict(schema='sage.lantern.startup-coordinator.v1', adapter=tools['adapter'],
        coordinator=tools['coordinator'], policy=dict(path=str(directory / 'policy.json'), sha256=sha(policy_raw)),
        initial_lifetime=dict(path=str(lifetime), sha256=sha(record)))
    return {lifetime: record, directory / 'baseline.json': baseline_raw,
        directory / 'policy.json': policy_raw, directory / 'startup.json': canonical(config)}


class Native:
    def __init__(self, tools, tools_raw):
        require(type(tools) is dict and set(tools) == {'schema', 'unit_id', 'coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel', 'identity', 'setup'}
                and tools['schema'] == 'sage.lantern.fresh-audio-tools.v1' and tools['unit_id'] in ('unit-a', 'unit-b'))
        self.tools, self.tools_raw = tools, tools_raw
        require(canonical(tools) == tools_raw)
        require(type(tools['identity']) is dict and set(tools['identity']) == {'unit_id', 'operator_account', 'chain_id', 'agent_id'}
            and tools['identity']['unit_id'] == tools['unit_id'])
        self.coordinator = load(tools['coordinator'])
        self.adapter = load(tools['adapter'])
        require(tools['helper']['sha256'] == self.adapter.HELPER_SHA
                and tools['collector']['sha256'] == self.adapter.COLLECTOR_SHA)
        self.helper, self.collector = load(tools['helper']), load(tools['collector'])
        self.coordinator.verify_wheel(tools['runtime_wheel'])
        self.site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
        self.sources = self.collector.runtime_sources(self.helper, Path(tools['runtime_wheel']['path']), self.site)

    def identity(self, config):
        from sage_lantern.suspend_kiosk import load_kiosk_binding
        binding = load_kiosk_binding()
        plan_raw = trusted('/etc/sage-lantern/appliance-bindings.json')
        receipt_raw = trusted('/etc/sage-lantern/appliance-installed.json')
        return identity_boundary(self.tools, config, decode(plan_raw), decode(receipt_raw),
            binding.account, binding.unit_id, sha(plan_raw), sha(receipt_raw))

    def setup(self):
        from sage_lantern.suspend_kiosk import LinuxProcesses
        setup = self.tools['setup']
        require(type(setup) is dict and set(setup) == {'observer', 'auth_process', 'auth_dropins', 'auth_source'})
        require(setup['observer']['sha256'] == '81142b2354e07c61962d291bd79e3d81571451d8749fd41e8d1fb4d71c3aa542'
            and setup['auth_source']['path'] == '/usr/local/libexec/sage-lantern-device-auth')
        pinned(setup['auth_source'])
        observer = load(setup['observer'])
        backend = object.__new__(observer.Native)
        backend.expected = {'auth_process': setup['auth_process'], 'auth_dropins': setup['auth_dropins'],
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
        backend.native = types.SimpleNamespace(processes=LinuxProcesses())
        backend.deadline = __import__('time').monotonic() + 15
        backend.setup()
        pinned(setup['auth_source'])

    def snapshot(self):
        from sage_lantern import audio_readiness
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        from sage_lantern.device_access import access_epoch
        from sage_lantern.mesh_daemon import load_config
        from sage_lantern.suspend_system import SuspendSystem
        boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        epoch = access_epoch()
        require(epoch is not None)
        config = load_config()
        metadata = trusted('/etc/sage-lantern/unit.env')
        validate_unit(metadata, config, self.tools['unit_id'])
        boundary = self.identity(config)
        self.setup()
        record_raw = trusted(RECORD)
        record = decode(record_raw)
        self.coordinator.lifetime(record_raw, record['inventory'], boot)
        owner = ColdBrokerBootstrap(record['inventory'])
        owner.inputs()
        require(owner.active() == record['evidence'])
        commissioned = audio_readiness.commissioned_receipt()
        require(commissioned['binding']['unit_id'] == self.tools['unit_id'] and audio_readiness.admission_verified() is True)
        system = SuspendSystem()
        argv = self.collector.model_argv(system, self.helper)
        probe = types.SimpleNamespace(system=system, expected={'model_argv': argv})
        model = self.helper.Recovery.model_ready(probe)
        require(model is not None)
        unit = system._show(self.helper.MODEL, system.clock() + 5, ('FragmentPath', 'DropInPaths', 'NeedDaemonReload'))
        require(unit['FragmentPath'] == '/etc/systemd/system/' + self.helper.MODEL and unit['NeedDaemonReload'] == 'no')
        paths = {unit['FragmentPath'], *unit['DropInPaths'].split(), argv[0]}
        for flag in ('--model', '--mmproj'):
            if flag in argv:
                require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv))
                paths.add(argv[argv.index(flag) + 1])
        require('--model' in argv)
        baseline = dict(inventory=record['inventory'], runtime_sources=self.sources,
            files={name: self.helper.file_hash(name) for name in sorted(paths)}, model_argv=argv,
            commission_sha256=sha(self.helper.canonical(commissioned)),
            actor=dict(chain_id=config['chain_id'], agent_id=config['agent_id']))
        require(self.collector.runtime_sources(self.helper, Path(self.tools['runtime_wheel']['path']), self.site) == self.sources)
        for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel'):
            pinned(self.tools[name])
        owner.inputs()
        require(owner.active() == record['evidence'] and access_epoch() == epoch
                and audio_readiness.commissioned_receipt() == commissioned and audio_readiness.admission_verified() is True
                and trusted(RECORD) == record_raw and trusted('/etc/sage-lantern/unit.env') == metadata
                and load_config() == config and self.helper.Recovery.model_ready(probe) == model
                and self.collector.model_argv(system, self.helper) == argv
                and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == boot)
        require(self.identity(config) == boundary)
        self.setup()
        require(access_epoch() == epoch)
        plan = dict(schema='sage.lantern.fresh-audio-handoff.v1', unit_id=self.tools['unit_id'], boot_id=boot,
            access_epoch=epoch, baseline=baseline, bootstrap_sha256=sha(record_raw), model=model,
            tools_sha256=sha(self.tools_raw), identity_sha256=sha(canonical(boundary)), historical_drain_verified=False)
        return plan, record_raw

    def claim(self, plan):
        for parent in (HANDOFF, LIFETIMES):
            self.helper.parents(parent)
            if not os.path.lexists(parent):
                parent.mkdir(mode=0o700)
                self.helper.sync(parent.parent)
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        directory = HANDOFF / plan['boot_id']
        directory.mkdir(mode=0o700)
        self.helper.sync(HANDOFF)
        self.helper.write(directory / 'intent.json', canonical(plan))
        (LIFETIMES / plan['boot_id']).mkdir(mode=0o700)
        self.helper.sync(LIFETIMES)

    def write(self, path, raw):
        self.helper.write(path, raw)


def apply(backend, reviewed):
    current, record = backend.snapshot()
    require(current == reviewed)
    output = documents(current, backend.tools, record)
    backend.coordinator.validate_full_config(decode(output[HANDOFF / current['boot_id'] / 'startup.json']))
    backend.claim(current)
    startup_path = HANDOFF / current['boot_id'] / 'startup.json'
    for path, raw in output.items():
        if path != startup_path:
            backend.write(path, raw)
    require(backend.snapshot() == (current, record))
    backend.write(startup_path, output[startup_path])
    result = dict(schema='sage.lantern.fresh-audio-handoff-result.v1', unit_id=current['unit_id'],
        boot_id=current['boot_id'], files={str(path): sha(raw) for path, raw in output.items()},
        services_started=False, services_enabled=False, reboot_accepted=False)
    backend.write(HANDOFF / current['boot_id'] / 'complete.json', canonical(result))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--tools', required=True)
    parser.add_argument('--tools-sha256', required=True)
    parser.add_argument('--expected')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    require(bool(args.expected) == bool(args.expected_sha256) == args.apply)
    def interrupted(signum, frame):
        raise InterruptedError('first_audio_handoff_interrupted')
    for number in (signal.SIGALRM, signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    signal.alarm(300)
    try:
        raw = pinned(dict(path=args.tools, sha256=args.tools_sha256))
        backend = Native(decode(raw), raw)
        result = apply(backend, decode(pinned(dict(path=args.expected, sha256=args.expected_sha256)))) if args.apply else backend.snapshot()[0]
        print(canonical(result).decode())
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    main()

