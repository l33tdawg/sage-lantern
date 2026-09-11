#!/usr/bin/env python3
"""One exclusive B auth/UI recovery; no native replay or old journal writes."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import stat
import sys
import types
import time
import uuid


PRIOR_RECOVERY = Path('/var/lib/lantern-b-release-recovery-8c53b540-695e-4661-9599-30272e4b0c94')
PRIOR_FILES = {
    'error.json': 'b24cd313be7e0acbb8061c8b89300e753e8afd4a9088c2de18511c794c4bc02c',
    'expected.json': '5062d3aa02ac2bf94bb310e7e98bc3b8a6256a0c8c8220014e05b0839e3101d7',
    'failed.json': '5c6b5912ec59c3a1151226aebe1e1821226fea1af238c388f70b2405ac174e8a',
    'guard-sage-lantern-device-auth.service': 'd21ce9c4a90c7f9da3a9b82db52b0b878a17b9a8cf2867920c479ac042427635',
    'guard-sage-lantern-fleet-admin.service': 'd21ce9c4a90c7f9da3a9b82db52b0b878a17b9a8cf2867920c479ac042427635',
    'journal.jsonl': '6b8a7435fb510ebd3d0188ac4441f7c5290db34b4a1c915fbfb92d08952650e0',
}
DIAGNOSTIC_RECOVERY = Path('/var/lib/lantern-b-release-recovery-e30159af-a38c-47ad-80d2-ef19f9882453')
DIAGNOSTIC_FILES = dict(PRIOR_FILES, **{
    'error.json': 'a0c917f392ca10edd17b17bbdf3fb633f8b44eb07f4d9011e428eac422c09037',
    'expected.json': '247837480fe2dd6ac539c14f0b32788040ffd9d8c0464f5d05f916f6ade2b7aa',
})


def prior_recovery():
    for directory, files in ((PRIOR_RECOVERY, PRIOR_FILES), (DIAGNOSTIC_RECOVERY, DIAGNOSTIC_FILES)):
        require({path.name for path in directory.iterdir()} == set(files))
        for name, checksum in files.items():
            require(digest(read(directory / name)) == checksum)


def require(value):
    if value is not True:
        raise RuntimeError('b_release_recovery_refused')


def read(path):
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
        raw = os.read(descriptor, 2097153)
        fields = ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink',
            'st_size', 'st_mtime_ns', 'st_ctime_ns')
        require(len(raw) == before.st_size and all(getattr(before, field) == getattr(after, field)
            for after in (os.fstat(descriptor), path.lstat()) for field in fields))
        return raw
    finally:
        os.close(descriptor)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = read(pin['path'])
    require(digest(raw) == pin['sha256'])
    return raw


def backend_for(expected):
    require(set(expected) == {'schema', 'attempt_id', 'self_sha256', 'coordinator',
        'policy_expected', 'journal_sha256', 'complete_sha256', 'auth_reports'})
    require(expected['schema'] == 'sage.lantern.b-release-recovery.v1'
        and str(uuid.UUID(expected['attempt_id'])) == expected['attempt_id']
        and uuid.UUID(expected['attempt_id']).int > 0)
    require(digest(read(Path(__file__).absolute())) == expected['self_sha256'])
    require(expected['policy_expected']['sha256'] == '5a894e97af9acce9a10a1c09190ae9acc9f4ff1580db41e95c0feada5a253223'
        and expected['journal_sha256'] == '1203b2da6d51bbdb0b6864566d39c73630778eba9c19031135e002c61a2fce97'
        and expected['complete_sha256'] == '7eb3c9b47020c926bf42e5ab9a82d9fd51f56e2afcded7cd8f8dd16a3152007f'
        and expected['auth_reports'] == {
            'fdc7a87838fe42e1842e8bd2ddbfb202.json': '694568351d724a7e115ade92052837b1ae5800d05c54d7b8a5cb368c5a929162',
            '896e47024cfe4c929bee3be6cc6ad35a.json': 'edd4f1f7af0265759563b109b7292457e61923e4a4ac1b959cee17f9fed5d572',
            'fbe0ffe0af62451d9c682896ba32fe53.json': '896c4b0efa3f73e674b68bc144143f249ef51e17d2ec488c5b99b2069c872bbd',
            'b4894d28dabd49409f5462299d737b34.json': '3b44405f22afeb2d2220d81c4e728ec36195c5a2c8b4a2da88bbe3beabca0af0'})
    raw = pinned(expected['coordinator'])
    module = types.ModuleType('reviewed_b_policy_recovery_dependency')
    module.__file__ = expected['coordinator']['path']
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    policy_raw = pinned(expected['policy_expected'])

    class RecoveryBackend(module.Backend):
        def anchors(self):
            prior_recovery()
            pinned(expected['coordinator'])
            require(pinned(expected['policy_expected']) == policy_raw
                and module.read(self.state / 'expected.json') == policy_raw
                and digest(module.read(self.state / 'transition.jsonl')) == expected['journal_sha256']
                and digest(module.read(self.state / 'complete.json')) == expected['complete_sha256'])
            require(json.loads(module.read(self.state / 'transition.jsonl').splitlines()[-1]) ==
                {'phase': 'release_failed_processes_stopped_auth_cleanup_unknown'})
            require(not os.path.lexists(self.state / 'released.json'))
            complete = json.loads(module.read(self.state / 'complete.json'))
            health = module.get(8080, '/v1/dashboard/health')
            require(complete['boot_id'] == self.expected['boot_id']
                and complete['native_boot'] == health['boot_id'])
            return health

        def reports(self, drained=False, extra=None):
            historical = expected['auth_reports']
            require(type(historical) is dict and historical
                and set(historical) >= set(self.expected['auth_reports'])
                and not set(historical) & set(extra or {}))
            require({path.name for path in module.REPORTS.iterdir()} == set(historical) | set(extra or {}))
            for name, checksum in {**historical, **(extra or {})}.items():
                require(Path(name).name == name and name.endswith('.json'))
                raw = module.read(module.REPORTS / name, self.uid, 1024, 0o600)
                require(digest(raw) == checksum)
                value = json.loads(raw)
                require(set(value) == {'version', 'instance_id', 'closing', 'drained',
                    'local_revoked', 'upstream_cleanup'} and type(value['version']) is int
                    and value['version'] == 1 and value['instance_id'] == name[:-5]
                    and all(type(value[field]) is bool for field in ('closing', 'drained', 'local_revoked'))
                    and value['upstream_cleanup'] in ('unknown', 'confirmed'))
                if name in (extra or {}):
                    self.preflight.report(value, name[:-5], current=True)

        def check(self, *args, **kwargs):
            self.anchors()
            return super().check(*args, **kwargs)

    backend = RecoveryBackend(json.loads(policy_raw))
    backend.recovery = Path('/var/lib/lantern-b-release-recovery-' + expected['attempt_id'])
    require(not os.path.lexists(backend.recovery))
    return module, backend


class RecoveryCancelled(BaseException):
    pass


class RecoveryDeadline(BaseException):
    pass


class RecoveryBoundary:
    def __init__(self, module, backend):
        self.module, self.backend = module, backend
        self.original_command = module.systemctl
        self.handlers = {}
        self.deadline = time.monotonic() + 180

    def enter(self):
        require(signal.getitimer(signal.ITIMER_REAL) == (0.0, 0.0))
        self.handlers = {number: signal.getsignal(number)
            for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM)}
        def cancel(number, frame):
            raise RecoveryCancelled()
        def expired(number, frame):
            raise RecoveryDeadline()
        signal.signal(signal.SIGTERM, cancel)
        signal.signal(signal.SIGINT, cancel)
        signal.signal(signal.SIGALRM, expired)
        self.backend.deadline = self.deadline
        self.module.systemctl = self.command
        signal.setitimer(signal.ITIMER_REAL, 180)

    def command(self, *args, **kwargs):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise RecoveryDeadline()
        kwargs['timeout'] = min(kwargs.get('timeout', 10), remaining)
        return self.original_command(*args, **kwargs)

    def cleanup(self):
        previous = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGTERM, signal.SIGINT})
        try:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            signal.setitimer(signal.ITIMER_REAL, 0)
            self.deadline = time.monotonic() + 120
            self.backend.deadline = self.deadline
            signal.setitimer(signal.ITIMER_REAL, 120)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, previous)

    def close(self):
        if not self.handlers:
            return
        signal.setitimer(signal.ITIMER_REAL, 0)
        self.module.systemctl = self.original_command
        for number, handler in self.handlers.items():
            signal.signal(number, handler)


def recover(module, backend, raw):
    boundary = RecoveryBoundary(module, backend)
    try:
        boundary.enter()
        return recover_bounded(module, backend, raw, boundary)
    finally:
        boundary.close()


def recover_bounded(module, backend, raw, boundary):
    stopped = (module.AUTH, module.UI, module.IDLE)
    backend.check(stopped, guarded=True, changed=True, completed=True)
    backend.recovery.mkdir(mode=0o700)
    backend.io.sync_directory(backend.recovery.parent)
    stage = 'intent'
    try:
        backend.io.write_new(backend.recovery / 'expected.json', raw)
        backend.io.write_new(backend.recovery / 'journal.jsonl', b'')
        def record(phase):
            descriptor = os.open(backend.recovery / 'journal.jsonl', os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            try:
                backend.restart.record(descriptor, {'phase': phase})
            finally:
                os.close(descriptor)
        record(stage)
        stage = 'release_auth_ui_guards'
        for service in (module.AUTH, module.UI):
            backend.check(stopped, guarded=True, changed=True, completed=True)
            path = backend.guards[service]
            guard = module.read(path)
            require(guard == backend.guard)
            backend.io.write_new(backend.recovery / ('guard-' + service), guard)
            record('remove-' + service)
            require(module.read(path) == guard)
            path.unlink()
            backend.io.sync_directory(path.parent)
            backend.released.add(service)
        module.systemctl('daemon-reload')
        backend.check(stopped, guarded=module.RETAINED, changed=True, completed=True)
        stage = 'start_auth'
        record(stage)
        module.systemctl('start', module.AUTH, timeout=45)
        stage = 'wait_auth_ready'
        auth = backend.wait_auth_ready()
        fresh_raw = module.read(module.REPORTS / (auth['invocation'] + '.json'), backend.uid, 1024, 0o600)
        backend.io.write_new(backend.recovery / 'auth-new-locked.json', fresh_raw)
        fresh = {module.AUTH: auth}
        reports = {auth['invocation'] + '.json': digest(fresh_raw)}
        backend.check((module.UI, module.IDLE), guarded=module.RETAINED, changed=True,
            completed=True, fresh=fresh, extra_reports=reports)
        stage = 'start_ui'
        record(stage)
        module.systemctl('start', module.UI, timeout=45)
        fresh[module.UI] = backend.wait_ui_ready(auth)
        stage = 'final_check'
        health = backend.check((module.IDLE,), guarded=module.RETAINED, changed=True,
            completed=True, fresh=fresh, extra_reports=reports)
        require(module.get(3196, '/state') == {'locked': True})
        receipt = dict(schema='sage.lantern.b-release-recovery-complete.v1',
            expected_sha256=digest(raw), boot_id=backend.expected['boot_id'], native_boot=health['boot_id'],
            owner_unlock_required=True, audio_started=False, historical_cleanup='unclaimed',
            fresh_services=fresh, retained_guards=sorted(module.RETAINED & set(backend.guards)))
        record('complete')
        backend.io.write_new(backend.recovery / 'complete.json', json.dumps(receipt, sort_keys=True).encode())
        return receipt
    except BaseException as error:
        cleanup_protected = False
        try:
            boundary.cleanup()
            cleanup_protected = True
        except BaseException:
            pass
        try:
            backend.io.write_new(backend.recovery / 'error.json', json.dumps(dict(
                stage=stage, error_class=type(error).__name__,
                readiness=getattr(backend, 'readiness_diagnostic', None))).encode())
        except BaseException:
            pass
        contained = False
        try:
            contained = backend.contain_release_failure() is True and cleanup_protected
        except BaseException:
            pass
        try:
            backend.io.write_new(backend.recovery / 'failed.json', json.dumps(dict(
                processes_stopped_verified=contained, historical_cleanup='unclaimed', retry_authorized=False)).encode())
        except BaseException:
            pass
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux')
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    raw = pinned({'path': args.expected, 'sha256': args.expected_sha256})
    module, backend = backend_for(json.loads(raw))
    if args.apply:
        result = recover(module, backend, raw)
    else:
        backend.check((module.AUTH, module.UI, module.IDLE), guarded=True, changed=True, completed=True)
        result = dict(inspected=True, applied=False)
    print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B release recovery refused or unknown; preserve evidence, no retry.') from None
