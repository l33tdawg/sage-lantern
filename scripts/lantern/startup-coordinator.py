"""Standalone post-unlock coordinator for the reviewed full audio lifecycle.

Run beside, never inside, the pinned R7 package. The observer gathers fresh
current-boot evidence; the full helper alone owns model/broker startup and
failure containment. One retained attempt per boot; no automatic retry.
"""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import resource
import signal
import stat
import subprocess
import time
import uuid

from sage_lantern.deployment_bringup import ColdBrokerBootstrap, read_public
from sage_lantern.device_access import access_epoch
from sage_lantern.suspend_io import FENCE, unique_object
from sage_lantern.suspend_system import DEPLOYMENT_FENCE


CONFIG = Path('/etc/sage-lantern/startup.json')
JOURNALS = Path('/var/lib/sage-lantern-startup')
PYTHON = '/opt/sage-lantern/venv/bin/python'
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C'}

# The lifecycle asserts one access epoch across its whole run, and the boot gets
# exactly one attempt. An unlock that is only transiently present - an activated
# session whose presence lease lapses, a broker still settling at boot - reports
# locked:false with an epoch that rotates within seconds, which would refuse the
# adapter mid-run and burn that attempt. The lease is six seconds and is renewed
# roughly every four, so one epoch has to survive a whole settle window before
# the coordinator charges the boot for it.
STABILITY_SECONDS = 30
STABILITY_INTERVAL = 1.0


def require(value):
    if value is not True:
        raise RuntimeError('startup_coordinator_refused')


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def hexvalue(value, size=64):
    return type(value) is str and re.fullmatch('[0-9a-f]{' + str(size) + '}', value) is not None


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'} and hexvalue(pin['sha256']))
    raw = read_source(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def read_source(path):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= 2 * 1024 * 1024)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(2 * 1024 * 1024 + 1)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


class Journal:
    def __init__(self, boot, config_sha256):
        require(str(uuid.UUID(boot)) == boot)
        info = JOURNALS.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
        for parent in JOURNALS.parents:
            info = parent.lstat()
            require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
        self.path = JOURNALS / (boot + '.jsonl')
        self.descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        self.append('intent', {'boot_id': boot, 'config_sha256': config_sha256})
        directory = os.open(JOURNALS, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def append(self, phase, details=None):
        raw = (json.dumps(dict(phase=phase, details=details), sort_keys=True) + '\n').encode()
        with os.fdopen(os.dup(self.descriptor), 'ab') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())

    def close(self):
        os.close(self.descriptor)


def best_effort_record(journal, phase, details):
    try:
        journal.append(phase, details)
    except BaseException:
        pass


STABLE = ('inventory', 'runtime_sources', 'files', 'model_argv', 'commission_sha256', 'actor')
ATTEMPTS = Path('/var/lib/sage-lantern-cross-boot-audio')
COORDINATOR = Path('/opt/sage-lantern/libexec/startup-coordinator.py')
R7 = '6a8e99c7e62ba5b767e1a23258fa25d97567364d08bddeccbabbfc3053754440'
RETRIEVAL_V1 = '36a94a18a83889ceb5860d40ace998410d1bc3fcfbb806af9731199f331306a3'
OWNER_LEDGERS_V2 = 'b0cfc4f94333960a579fda7e1c3b41bfcd4c7206c2a0441715af980133e36d17'
RADIO_OWNER_COMBINED = '2175cd19e275597860cdd877a1007156366667021d721766023a569d83bee519'
COMPACT_PROMPT_V1 = 'fd39c10773de0726e333b296a1dfc5dab3058753edfb5925dd2b46abfcd4ddd3'
RADIO_FOUNDATION_SOURCES = '9c2a317ae7c3779c023ef216f7c8ce730b2fea08f2c6a3650b96cfa699140f7b'
RADIO_DIRECTORY_SOURCES = 'd9ba5b90c315f902be77fbd23e515547a8332c533e7afcadfe911c4984b2bbf0'
RADIO_DIRECTORY_A_SOURCES = '1ca41a3400aff258767aff50f8cb26f87108150e69a833ce330d296244763a88'
RADIO_FOUNDATION_A_SOURCES = '0ef48243d2351fea35db7d4714bc3089d9ae032f394f4dd177bf939bb0a10ae8'
# Published 2026-09-18: runtime wheel rebuilt to match the deployed bytes on the
# units (answer suggestions + the 2026-09-12 messaging receive fix).
DEPLOYED_MESSAGING_RUNTIME = '290c0e67ff391084dc11e47bda0058aca946286828f56d7a809b811d014f94fb'
# A unit release describes its own runtime. build-release.py replaces this line
# with the digest of the wheel it built from that unit's deployed bytes; the
# repository copy stays None and accepts only the digests published above.
RELEASE_RUNTIME = None
MAINTAINED_LIFETIMES = Path('/var/lib/sage-lantern-maintained-audio')


def verify_wheel(pin):
    require(pin['sha256'] in (R7, RETRIEVAL_V1, COMPACT_PROMPT_V1, OWNER_LEDGERS_V2, RADIO_OWNER_COMBINED, RADIO_FOUNDATION_SOURCES, RADIO_FOUNDATION_A_SOURCES, RADIO_DIRECTORY_SOURCES, RADIO_DIRECTORY_A_SOURCES, DEPLOYED_MESSAGING_RUNTIME, RELEASE_RUNTIME, '355ade85f6fc2ee307a079261a532631b5799d5ac4c3e21d735c8fa644a81581', 'fd4828e004a91e516bb7fd24fdd603fa098389c908923247c693777c63919edc', 'cf963bd87e9a838ec926f2da80fc4aad3b0946b2ebdc1827a6228734ac0b214a', '3d90611e780b36d59e8d91b230f7f202c8e44076e23a3f610c914856ee743b03'))
    pinned(pin)


def prior_path(path, boot):
    if path == str(ATTEMPTS / boot / 'new-lifetime.json'):
        return True
    value = Path(path)
    if value.parent.parent != MAINTAINED_LIFETIMES or value.name != 'new-lifetime.json':
        return False
    try:
        return str(uuid.UUID(value.parent.name)) == value.parent.name
    except ValueError:
        return False


def decode(raw):
    return json.loads(raw, object_pairs_hook=unique_object)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


PINNED_FIELDS = ('adapter', 'policy', 'initial_lifetime', 'coordinator')


def validate_full_config(config):
    require(type(config) is dict
            and set(config) in (set(PINNED_FIELDS) | {'schema'},
                                set(PINNED_FIELDS) | {'schema', 'allowance'}))
    require(config['schema'] == 'sage.lantern.startup-coordinator.v1')
    for name in (*PINNED_FIELDS, *(['allowance'] if 'allowance' in config else [])):
        pin = config[name]
        require(type(pin) is dict and set(pin) == {'path', 'sha256'} and hexvalue(pin['sha256'])
                and type(pin['path']) is str and Path(pin['path']).is_absolute()
                and '..' not in Path(pin['path']).parts)
    require(config['coordinator']['path'] == str(COORDINATOR))


def exclusive_public(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def sync_directory(path):
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def terminate_child(process):
    try:
        process.terminate()
        process.wait(timeout=190)
        return
    except BaseException:
        pass
    try:
        process.kill()
        process.wait(timeout=10)
    except BaseException:
        pass


def lifetime(raw, inventory, boot=None, allowance=None):
    value = decode(raw)
    require(type(value) is dict and set(value) == {'schema', 'phase', 'boot_id', 'pid', 'inventory', 'evidence'})
    require(value['schema'] == 'sage.lantern.voice-bootstrap.v1' and value['phase'] == 'complete'
            and type(value['boot_id']) is str and str(uuid.UUID(value['boot_id'])) == value['boot_id']
            and uuid.UUID(value['boot_id']).int != 0
            and type(value['pid']) is int and value['pid'] > 0)
    if allowance is None:
        require(value['inventory'] == inventory)
    else:
        # The prior lifetime may predate exactly the deltas a reviewed allowance
        # pins; nothing else may differ. The new lifetime written by complete()
        # is always checked against the baseline with no allowance.
        delta = allowance['inventory_delta']
        changed = set()
        for name in set(value['inventory']) | set(inventory):
            before, after = value['inventory'].get(name), inventory.get(name)
            if before == after:
                continue
            pin = delta.get(name)
            require(type(pin) is dict and before is not None and after is not None
                    and pin['from'] == before and pin['to'] == after)
            changed.add(name)
        require(changed == set(delta))
    if boot is not None:
        require(value['boot_id'] == boot)
    evidence = value['evidence']
    require(type(evidence) is dict and set(evidence) == {'broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'}
            and hexvalue(evidence['broker_id'], 32)
            and type(evidence['vault_epoch']) is str
            and re.fullmatch('[0-9a-f]{64}:[1-9][0-9]{0,19}', evidence['vault_epoch']) is not None
            and int(evidence['vault_epoch'].split(':')[1]) <= 2**64 - 1
            and all(type(evidence[key]) is int and evidence[key] > 0 for key in ('broker_pid', 'voice_pid')))
    return value


def validate_observation(expected, baseline, boot, prior, fence):
    require(type(expected) is dict and set(expected) == set(baseline)
            and expected['schema'] == 'sage.lantern.cross-boot-audio.v1' and expected['boot_id'] == boot
            and prior['boot_id'] != boot and expected['historical_outcomes'] == 'unknown'
            and expected['old_boot_attribution'] == 'unverified')
    require(all(expected[name] == baseline[name] for name in STABLE))
    require(type(fence) is dict and set(fence) == {'schema', 'broker_id', 'phase'}
            and fence['schema'] == 'sage.lantern.storage-supervisor.v1'
            and fence['broker_id'] == prior['evidence']['broker_id']
            and fence['phase'] in ('active', 'recovery_required', 'drained', 'vault_closed', 'stopped_verified', 'stopping'))


class FullLifecycle:
    def __init__(self, config, raw):
        validate_full_config(config)
        self.config, self.raw = config, raw
        self.boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        require(str(uuid.UUID(self.boot)) == self.boot)
        self.policy = decode(pinned(config['policy']))
        require(type(self.policy) is dict and set(self.policy) == {'schema', 'helper', 'collector', 'runtime_wheel', 'baseline'}
                and self.policy['schema'] == 'sage.lantern.startup-audio-policy.v1')
        self.baseline = decode(pinned(self.policy['baseline']))

    def check(self, epoch):
        require(hexvalue(epoch, 32) and access_epoch() == epoch and read_public(CONFIG) == self.raw
                and Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.boot)
        for name in ('adapter', 'policy', 'coordinator',
                     *(['allowance'] if 'allowance' in self.config else [])):
            pinned(self.config[name])
        for name in ('helper', 'collector', 'baseline'):
            pinned(self.policy[name])
        verify_wheel(self.policy['runtime_wheel'])
        require(Path(__file__).resolve() == COORDINATOR)
        require(not os.path.lexists(FENCE) and not os.path.lexists(DEPLOYMENT_FENCE))

    def previous(self):
        pointer = JOURNALS / 'latest.json'
        pin = decode(read_public(pointer)) if os.path.lexists(pointer) else self.config['initial_lifetime']
        raw = pinned(pin)
        try:
            prior = lifetime(raw, self.baseline['inventory'])
        except RuntimeError:
            # Strict binding failed; consult the reviewed allowance if one is
            # configured. Leaving one in place cannot weaken an exact prior.
            require('allowance' in self.config)
            allowance = decode(read_public(self.config['allowance']['path']))
            prior = lifetime(raw, self.baseline['inventory'], allowance=allowance)
        require(prior_path(pin['path'], prior['boot_id']) and prior['boot_id'] != self.boot)
        return pin, prior

    def child(self, args, tag, timeout):
        output = JOURNALS / (self.boot + '-' + tag + '.stdout')
        errors = JOURNALS / (self.boot + '-' + tag + '.stderr')
        descriptors = []
        try:
            for path in (output, errors):
                descriptors.append(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
            process = subprocess.Popen([PYTHON, '-I', '-B', *args], env=ENV, stdin=subprocess.DEVNULL,
                stdout=descriptors[0], stderr=descriptors[1])
            try:
                returncode = process.wait(timeout=timeout)
            except BaseException:
                terminate_child(process)
                raise
            for descriptor in descriptors:
                os.fsync(descriptor)
            require(returncode == 0)
        finally:
            for descriptor in descriptors:
                os.close(descriptor)
        return decode(read_source(output))

    def apply(self, pin, epoch):
        self.check(epoch)
        pinned(pin)
        command = [self.config['adapter']['path'], '--policy', self.config['policy']['path'],
            '--policy-sha256', self.config['policy']['sha256'], '--prior', pin['path'],
            '--prior-sha256', pin['sha256'], '--boot-id', self.boot, '--access-epoch', epoch]
        if 'allowance' in self.config:
            command += ['--allowance', self.config['allowance']['path'],
                        '--allowance-sha256', self.config['allowance']['sha256']]
        return self.child([*command, '--apply'], 'apply', 510)

    def complete(self, result, prior, epoch):
        self.check(epoch)
        require(type(result) is dict and set(result) == {'schema', 'boot_id', 'access_epoch', 'storage_epoch',
            'expected_sha256', 'prior', 'historical_outcomes', 'old_drain_verified', 'services_started', 'lifetime', 'evidence'}
            and result['schema'] == 'sage.lantern.startup-full.v1' and result['boot_id'] == self.boot
            and result['access_epoch'] == epoch and result['prior'] == prior
            and result['historical_outcomes'] == 'unknown' and result['old_drain_verified'] is False
            and result['services_started'] is True)
        completion_raw = read_public(ATTEMPTS / self.boot / 'startup-complete.json')
        stdout_raw = read_source(JOURNALS / (self.boot + '-apply.stdout'))
        require(stdout_raw in (completion_raw, completion_raw + b'\n') and decode(completion_raw) == result)
        expected_raw = read_source(ATTEMPTS / self.boot / 'expected.json')
        require(sha(expected_raw) == result['expected_sha256'])
        expected = decode(expected_raw)
        require(expected['boot_id'] == self.boot and all(expected[name] == self.baseline[name] for name in STABLE)
                and expected['storage_epoch'] == result['storage_epoch'])
        path = ATTEMPTS / self.boot / 'new-lifetime.json'
        raw = read_public(path)
        document = lifetime(raw, expected['inventory'], self.boot)
        require(result['lifetime'] == {'path': str(path), 'sha256': sha(raw)}
                and document['evidence'] == result['evidence']
                and document['evidence']['vault_epoch'] == expected['storage_epoch'])
        owner = ColdBrokerBootstrap(expected['inventory'])
        owner.inputs()
        require(owner.active() == document['evidence'])
        self.check(epoch)
        return {'path': str(path), 'sha256': sha(raw)}

    def advance(self, pin):
        temporary = JOURNALS / (self.boot + '-next.json')
        exclusive_public(temporary, canonical(pin))
        os.replace(temporary, JOURNALS / 'latest.json')
        sync_directory(JOURNALS)


def run_full_attempt(backend, journal, epoch):
    child_returned = False
    try:
        backend.check(epoch)
        prior_pin, prior = backend.previous()
        journal.append('prior_lifetime_verified', prior_pin)
        journal.append('full_lifecycle_intent', prior_pin)
        result = backend.apply(prior_pin, epoch)
        child_returned = True
        completed = backend.complete(result, prior_pin, epoch)
        journal.append('full_lifecycle_verified', completed)
        backend.advance(completed)
        journal.append('operational', completed)
        return completed
    except BaseException:
        best_effort_record(journal, 'full_lifecycle_outcome_unknown',
            {'automatic_retry': False, 'active_or_unknown': True, 'containment_verified': False,
             'child_returned': child_returned, 'operator_recovery_required': True})
        raise


def stable_epoch(observed=access_epoch, clock=time.monotonic, sleeper=time.sleep,
                 window=STABILITY_SECONDS, interval=STABILITY_INTERVAL, deadline=None):
    """Return an access epoch only once it has held for the whole settle window.

    A changed, absent or newly observed epoch restarts the window; a transient
    unlock therefore delays the lifecycle instead of spending the boot's single
    attempt on it. ``deadline`` is a monotonic instant, or None to wait forever.
    """
    require(window > 0 and interval > 0)
    epoch = None
    held = None
    while True:
        now = clock()
        if deadline is not None and now >= deadline:
            raise TimeoutError('owner activation not observed')
        value = observed()
        if value is None or value != epoch:
            epoch, held = value, now
        elif now - held >= window:
            return epoch
        sleeper(interval)


def main():
    parser = argparse.ArgumentParser(description='Bounded post-unlock full lifecycle coordinator; standalone beside R7.')
    parser.add_argument('--wait-seconds', type=int, default=0, help='0 waits indefinitely for activated owner unlock')
    args = parser.parse_args()
    require(os.geteuid() == 0 and 0 <= args.wait_seconds <= 86400)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    raw = read_public(CONFIG)
    config = decode(raw)
    validate_full_config(config)
    backend = FullLifecycle(config, raw)
    deadline = None if args.wait_seconds == 0 else time.monotonic() + args.wait_seconds
    while True:
        epoch = stable_epoch(deadline=deadline)
        try:
            backend.check(epoch)
            break
        except RuntimeError:
            # A session that lapsed between the settle window and this check has
            # not been charged yet: wait for the next stable activation. Any
            # refusal that is not the epoch moving stays a refusal.
            if access_epoch() == epoch:
                raise
    journal = Journal(backend.boot, sha(raw))
    def interrupted(signum, frame):
        raise InterruptedError('startup_coordinator_interrupted')
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, interrupted)
    try:
        fcntl.flock(journal.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        run_full_attempt(backend, journal, epoch)
    finally:
        journal.close()


if __name__ == '__main__':
    main()
