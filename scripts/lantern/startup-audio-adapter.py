#!/usr/bin/env python3
"""Standalone full-start adapter; never a recovery-only callback.

Root policy pins collector, recovery helper, R7 wheel and reviewed baseline
expected JSON. Prior is the previous durable new-lifetime.json, not a drain
receipt. Default inspection writes nothing. Apply is exclusive per boot and
delegates all model/broker startup and failure containment to the pinned helper.
Coordinator must not start producers afterward. No capture or commissioning.
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
import types
import uuid


STATIC = ('inventory', 'runtime_sources', 'files', 'commission_sha256', 'actor', 'model_argv')
# Repinned 2026-09-21 (was 9d28f920...): the recovery helper now admits an
# owner-cancelled outbox row as retained_unknown, and it verifies the NPU model
# argv the units actually run instead of only the llama.cpp shape - that stale
# check refused every cross-boot transition on this fleet.
HELPER_SHA = '34c0cf9301d1ddaba4c4d2a90302d49549509f0c5207d3565b86ff1cea6528c2'
# Repinned 2026-09-18: the collector gained the digest of the runtime wheel
# rebuilt to match the deployed bytes (previously faked by cf963bd8...).
# Repinned again the same day: it also gained the RELEASE_RUNTIME slot that
# build-release.py fills with each unit's own wheel digest.
COLLECTOR_SHA = 'e37d91cd115aa1ac62cbe40d3aee367a0d9547c358ceae47d70b5d200ebcce72'
ALLOWANCE_FIELDS = {'schema', 'boot_id', 'prior', 'fence', 'inventory_delta',
                    'historical_outcomes', 'authorized_by'}
# The phases the lifecycle itself accepts. A broker that was running when the
# unit was stopped cleanly leaves `stopped_verified` where the review saw
# `active`, so the reviewed deviation is the broker the fence names and the
# digest stays audit evidence.
ALLOWANCE_FENCE_PHASES = ('active', 'stopping', 'stopped_verified')
HEX64 = re.compile(r'[0-9a-f]{64}')


def require(value):
    if not value:
        raise ValueError('startup_audio_adapter_refused')


def unique(items):
    result = {}
    for name, value in items:
        require(name not in result)
        result[name] = value
    return result


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
                and not before.st_mode & 0o022 and before.st_size <= 2097152)
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = trusted(pin['path'])
    require(hashlib.sha256(raw).hexdigest() == pin['sha256'])
    return raw


def load(pin):
    raw = pinned(pin)
    module = types.ModuleType('pinned_startup_dependency')
    module.__file__ = pin['path']
    exec(compile(raw, pin['path'], 'exec'), module.__dict__)
    return module


def delta_names(delta):
    # A tolerance may be fence-only: a maintenance cold start leaves a new broker
    # in the live fence while the prior lifetime names the broker its own
    # lifecycle started, and no inventory file need have changed. An empty delta
    # is strictly narrower than one naming files - the caller still requires the
    # two inventories to agree exactly - so it is allowed.
    require(type(delta) is dict)
    for name, pin in delta.items():
        require(type(name) is str and name.startswith('/') and '..' not in Path(name).parts)
        require(type(pin) is dict and set(pin) == {'from', 'to'}
                and type(pin['from']) is str and HEX64.fullmatch(pin['from']) is not None
                and type(pin['to']) is str and HEX64.fullmatch(pin['to']) is not None)
    return delta


def inventory_agrees(prior_inventory, live_inventory, delta):
    changed = set()
    for name in set(prior_inventory) | set(live_inventory):
        before, after = prior_inventory.get(name), live_inventory.get(name)
        if before == after:
            continue
        pin = delta.get(name)
        require(type(pin) is dict and before is not None and after is not None
                and pin['from'] == before and pin['to'] == after)
        changed.add(name)
    require(changed == set(delta))
    return True


def validate_prior(prior, fence, expected, boot, allowance=None, fence_sha256=None, prior_pin=None):
    require(type(prior) is dict and set(prior) == {
        'schema', 'phase', 'boot_id', 'pid', 'inventory', 'evidence'})
    require(prior['schema'] == 'sage.lantern.voice-bootstrap.v1'
            and type(prior['pid']) is int and prior['pid'] > 0
            and type(prior['boot_id']) is str and type(prior['inventory']) is dict)
    evidence = prior['evidence']
    require(type(evidence) is dict and set(evidence) == {
        'broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'})
    require(type(evidence['broker_id']) is str
            and re.fullmatch(r'[0-9a-f]{32}', evidence['broker_id']) is not None)
    require(type(evidence['vault_epoch']) is str
            and re.fullmatch(r'[0-9a-f]{64}:[1-9][0-9]{0,19}', evidence['vault_epoch']) is not None
            and int(evidence['vault_epoch'].split(':')[1]) <= 2**64 - 1)
    require(all(type(evidence[name]) is int and evidence[name] > 0
                for name in ('broker_pid', 'voice_pid')))
    require(prior['phase'] == 'complete' and str(uuid.UUID(prior['boot_id'])) == prior['boot_id']
            and prior['boot_id'] != boot)
    require(fence['schema'] == 'sage.lantern.storage-supervisor.v1'
            and fence['phase'] in {'active', 'stopped_verified', 'stopping'}
            and set(fence) == {'schema', 'phase', 'broker_id'})
    # Strict binding first. An allowance is only consulted when the prior no
    # longer binds, so leaving one in the configuration cannot weaken a boot
    # whose prior is already exact.
    if (prior['evidence']['broker_id'] == fence['broker_id']
            and prior['inventory'] == expected['inventory']):
        return
    require(allowance is not None)
    require(type(allowance) is dict and set(allowance) == ALLOWANCE_FIELDS
            and allowance['schema'] == 'sage.lantern.startup-allowance.v1'
            # A tolerance names the boot it was reviewed for. That is either the
            # boot that spends it (an operator recovery of this boot) or the boot
            # whose lifetime it excuses (a release installed on one boot and
            # spent by the next one, which is the only way to ship a fix before
            # the reboot that proves it). Both are exact and unguessable, and the
            # prior, fence and delta pins below are what actually bound it.
            and allowance['boot_id'] in (boot, prior['boot_id'])
            and allowance['historical_outcomes'] == 'unknown'
            and type(allowance['authorized_by']) is str and allowance['authorized_by'])
    require(prior_pin is not None and fence_sha256 is not None and allowance['prior'] == prior_pin)
    fence_pin = allowance['fence']
    require(type(fence_pin) is dict and set(fence_pin) == {'sha256', 'phase', 'broker_id'}
            and type(fence_pin['sha256']) is str and HEX64.fullmatch(fence_pin['sha256']) is not None
            and fence_pin['phase'] in ALLOWANCE_FENCE_PHASES
            and re.fullmatch('[0-9a-f]{32}', fence_pin['broker_id']) is not None
            and fence_pin['broker_id'] == fence['broker_id']
            and fence['phase'] in ALLOWANCE_FENCE_PHASES)
    require(inventory_agrees(prior['inventory'], expected['inventory'],
                             delta_names(allowance['inventory_delta'])))


def validate_current(expected, baseline, boot):
    require(expected['boot_id'] == boot and expected['historical_outcomes'] == 'unknown'
            and expected['old_boot_attribution'] == 'unverified')
    for name in STATIC:
        require(expected[name] == baseline[name])


def run(helper, collector, policy, prior_pin, boot, access, apply, access_epoch, allowance=None):
    require(str(uuid.UUID(boot)) == boot and access is not None and access_epoch() == access)
    baseline = helper.decode(pinned(policy['baseline']))
    prior_raw = pinned(prior_pin)
    prior = helper.decode(prior_raw)
    site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
    sources = collector.runtime_sources(helper, Path(policy['runtime_wheel']['path']), site)
    require(helper.sha(trusted(policy['runtime_wheel']['path'])) == policy['runtime_wheel']['sha256'])
    expected = collector.collect(helper, sources, retained_model_files=baseline['files'])
    validate_current(expected, baseline, boot)
    require(access_epoch() == access)
    require(collector.runtime_sources(helper, Path(policy['runtime_wheel']['path']), site) == sources)
    for name in ('helper', 'collector', 'baseline', 'runtime_wheel'):
        pinned(policy[name])

    class BoundRecovery(helper.Recovery):
        def reconcile(self):
            require(access_epoch() == access)
            result = super().reconcile()
            require(access_epoch() == access)
            return result

        def completed(self):
            require(access_epoch() == access)
            result = super().completed()
            require(result['broker']['vault_epoch'] == expected['storage_epoch'])
            receipt = dict(schema='sage.lantern.startup-full.v1', boot_id=boot,
                access_epoch=access, storage_epoch=expected['storage_epoch'],
                expected_sha256=helper.sha(helper.canonical(expected)),
                prior=prior_pin, historical_outcomes='unknown', old_drain_verified=False,
                services_started=True, lifetime=dict(path=str(self.state / 'new-lifetime.json'),
                    sha256=result['lifetime_sha256']), evidence=result['broker'])
            require(access_epoch() == access and self.owner.active() == result['broker'])
            helper.write(self.state / 'startup-complete.json', helper.canonical(receipt))
            self.receipt = receipt
            return result

    backend = BoundRecovery(expected)
    try:
        backend.lock()
        require(pinned(prior_pin) == prior_raw)
        fence_raw = helper.public(helper.SUPERVISOR / 'fence.json')
        validate_prior(prior, helper.decode(fence_raw), expected, boot, allowance,
                       fence_sha256=helper.sha(fence_raw), prior_pin=prior_pin)
        backend.reconcile()
        if not apply:
            return dict(preflight_verified=True, services_started=False, mutation_authorized=False,
                        boot_id=boot, expected_sha256=helper.sha(helper.canonical(expected)))
        helper.parents(helper.ATTEMPTS)
        if not helper.ATTEMPTS.exists():
            helper.ATTEMPTS.mkdir(mode=0o700)
            helper.sync(helper.ATTEMPTS.parent)
        info = helper.ATTEMPTS.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        backend.state.mkdir(mode=0o700)
        helper.sync(helper.ATTEMPTS)
        helper.write(backend.state / 'expected.json', helper.canonical(expected))
        helper.write(backend.state / 'adapter-policy.json', helper.canonical(policy))
        helper.write(backend.state / 'prior-lifetime.json', prior_raw)
        sequence = 0
        def record(phase, details=None):
            nonlocal sequence
            sequence += 1
            helper.write(backend.state / f'{sequence:02d}-{phase}.json', helper.canonical(dict(
                phase=phase, details=details, expected_sha256=helper.sha(helper.canonical(expected)),
                historical_outcomes='unknown')))
        helper.transition(backend, record)
        return backend.receipt
    finally:
        backend.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    for name in ('policy', 'policy-sha256', 'prior', 'prior-sha256', 'boot-id', 'access-epoch'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--allowance')
    parser.add_argument('--allowance-sha256')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require((args.allowance is None) == (args.allowance_sha256 is None))
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    def interrupted(signum, frame):
        raise InterruptedError('startup_audio_interrupted')
    for signum in (signal.SIGINT, signal.SIGTERM, signal.SIGALRM):
        signal.signal(signum, interrupted)
    signal.alarm(300)
    try:
        policy_raw = pinned(dict(path=args.policy, sha256=args.policy_sha256))
        policy = json.loads(policy_raw, object_pairs_hook=unique)
        require(set(policy) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
                and policy['schema'] == 'sage.lantern.startup-audio-policy.v1'
                and policy['helper']['sha256'] == HELPER_SHA
                and policy['collector']['sha256'] == COLLECTOR_SHA)
        collector = load(policy['collector'])
        helper = load(policy['helper'])
        site = Path(sys.prefix) / 'lib' / f'python{sys.version_info.major}.{sys.version_info.minor}' / 'site-packages'
        collector.runtime_sources(helper, Path(policy['runtime_wheel']['path']), site)
        from sage_lantern.device_access import access_epoch
        allowance = None if args.allowance is None else json.loads(
            pinned(dict(path=args.allowance, sha256=args.allowance_sha256)), object_pairs_hook=unique)
        result = run(helper, collector, policy, dict(path=args.prior, sha256=args.prior_sha256),
                     args.boot_id, args.access_epoch, args.apply, access_epoch, allowance)
        print(helper.canonical(result).decode())
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps(dict(error_type=type(error).__name__, recovery_required=True, automatic_retry=False)))
        raise SystemExit(2) from None
