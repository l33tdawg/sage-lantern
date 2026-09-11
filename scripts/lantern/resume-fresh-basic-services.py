#!/usr/bin/env python3
"""Explicit continuation of Unit A's failed post-auth-start basic restore.

Expected checkpoint schema sage.lantern.basic-restore-resume.v1:
original_helper and original_expected are {path, sha256}; journal maps the
exact nine original journal filenames to SHA256; auth is {pid, start_ticks,
invocation, uid}. Default inspects only. --apply exclusively journals in a new
directory and restores BASIC only. No auth restart, reinstallation, or cleanup.
key_resident_basic_restore must be literal true: this explicitly admits an
encrypted, already-unlocked serving vault while device auth remains locked.
Supersession is historical evidence, not a current cryptographic-lock proof.
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


STATE = Path('/var/lib/sage-lantern-basic-restore-resume')
ORIGINAL = Path('/var/lib/sage-lantern-basic-restore')
ORIGINAL_SHA = '5e463d96ec64864d4b5e752c3d7726a5714f2a8e147697ac154d4da37a00f322'
AUTH = 'sage-lantern-device-auth.service'


def require(value):
    if not value:
        raise ValueError('basic_resume_refused')


def read(path, limit=262144):
    path = Path(path)
    require(path.is_absolute() and '..' not in path.parts)
    for parent in path.parents:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not info.st_mode & 0o022)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(descriptor)
        require(stat.S_ISREG(before.st_mode) and before.st_uid == 0 and before.st_nlink == 1
                and not before.st_mode & 0o022 and 0 < before.st_size <= limit)
        raw = os.read(descriptor, limit + 1)
        def signature(info):
            return (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
                    info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
                and signature(before) == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def pinned(item):
    require(type(item) is dict and set(item) == {'path', 'sha256'})
    raw = read(item['path'])
    require(hashlib.sha256(raw).hexdigest() == item['sha256'])
    return raw


def original_journal(base, checkpoint):
    info = ORIGINAL.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and stat.S_IMODE(info.st_mode) == 0o700)
    phases = ['install_voice_loop_intent', 'install_voice_loop_done', 'install_status_intent',
        'install_status_done', 'release_' + AUTH + '_intent',
        'remove_' + AUTH + '_zzzz-fresh-native-update.conf_intent',
        'remove_' + AUTH + '_zzzz-fresh-native-update.conf_done',
        'release_' + AUTH + '_done', 'start_' + AUTH + '_intent']
    names = {f'{index:02d}-{phase}.json': phase for index, phase in enumerate(phases, 1)}
    require(set(checkpoint['journal']) == set(names))
    require({entry.name for entry in ORIGINAL.iterdir()} == set(names) | {'expected.json', 'voice_loop.before', 'status.before'})
    raw = pinned(checkpoint['original_expected'])
    require(read(ORIGINAL / 'expected.json') == raw)
    for name, phase in names.items():
        content = pinned(dict(path=str(ORIGINAL / name), sha256=checkpoint['journal'][name]))
        require(base.decode(content) == dict(phase=phase, expected_sha256=checkpoint['original_expected']['sha256']))
    expected = base.decode(raw)
    for key in base.TARGETS:
        require(hashlib.sha256(read(ORIGINAL / (key + '.before'))).hexdigest()
                == expected['old_helpers'][key]['sha256'])
        require(stat.S_IMODE((ORIGINAL / (key + '.before')).lstat().st_mode) == 0o755)
    return expected


def listener(pid, start_ticks, boot_id, *, proc=Path('/proc')):
    process = proc / str(pid)
    def identity():
        require((process / 'stat').read_text().rsplit(')', 1)[1].split()[19] == str(start_ticks)
                and (proc / 'sys/kernel/random/boot_id').read_text().strip() == boot_id)
    identity()
    with (proc / 'net/tcp').open('rb') as stream:
        raw = stream.read(1048577)
    require(len(raw) <= 1048576)
    rows = [row.split() for row in raw.decode('ascii').splitlines()[1:]]
    require(all(len(row) >= 10 for row in rows))
    matches = [row for row in rows if row[3] == '0A' and row[1].endswith(':0C7C')]
    require(len(matches) == 1 and matches[0][1] == '0100007F:0C7C'
            and re.fullmatch('[1-9][0-9]*', matches[0][9]))
    inode = matches[0][9]
    owned = None
    for index, entry in enumerate((process / 'fd').iterdir()):
        require(index < 4096)
        try:
            if os.readlink(entry) == 'socket:[' + inode + ']':
                owned = entry
        except FileNotFoundError:
            continue
    require(owned is not None and os.readlink(owned) == 'socket:[' + inode + ']')
    identity()
    return matches[0][1], matches[0][3], inode


def resume_type(base, checkpoint):
    class Resume(base.Restore):
        def current(self):
            self.locked()
            require(pinned(checkpoint['original_helper']) == self.helper_raw)
            require(original_journal(base, checkpoint) == self.expected)
            require(not any(checkpoint['original_helper']['path'] in process.argv
                for process in self.preflight.LinuxProcesses().snapshot().values()))
            super().current()

        def native(self):
            require(checkpoint['key_resident_basic_restore'] is True)
            require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.binding['boot_id'])
            self.runtime.pinned(self.sup['evidence'])
            self.runtime.pinned(self.sup['binding'])
            require(self.runtime.sha(self.runtime.read(self.preflight.CONFIG + 'initialized-public.json'))
                    == self.binding['initialized_sha256'])
            require(self.runtime.sha(self.runtime.read(self.preflight.BINARY))
                    == self.binding['native_after']['binary_sha256'])
            expected = self.evidence['sage_after']
            process = self.preflight.LinuxProcesses().read(expected['pid'])
            require(process.uid == expected['uid'] and process.start_ticks == expected['start_ticks']
                    and process.executable == self.preflight.BINARY
                    and process.argv == (self.preflight.BINARY, 'serve')
                    and process.state not in ('Z', 'T', 't', 'X'))
            running = Path('/proc', str(expected['pid']), 'exe').stat()
            installed = Path(self.preflight.BINARY).stat()
            require((running.st_dev, running.st_ino) == (installed.st_dev, installed.st_ino))
            owner = self.validator.listener_owner(expected['pid'], expected['start_ticks'], self.binding['boot_id'])
            health = self.observer.get(8080, '/v1/dashboard/health')
            require(health['sage'] == 'running' and health['version'] == self.binding['native_after']['version']
                    and health['boot_id'] == expected['application_boot_id']
                    and health['encrypted'] is True and health['vault_locked'] is False)
            require(self.validator.listener_owner(expected['pid'], expected['start_ticks'], self.binding['boot_id']) == owner)
            self.preflight.governance(self.observer.get(26657, '/abci_query?path=%22/upgrade/governance-status%22'),
                self.observer.get(26657, '/status'), self.binding['chain_id'], self.observer.get(26657, '/abci_info'))

        def locked(self):
            value = base.snapshot(self.show(AUTH), AUTH)
            require(value == self.running[AUTH])
            process = self.process(AUTH, value)
            auth = checkpoint['auth']
            require(process.pid == auth['pid'] and process.start_ticks == auth['start_ticks']
                    and process.uid == auth['uid'] and value['InvocationID'] == auth['invocation'])
            before = listener(process.pid, process.start_ticks, self.binding['boot_id'])
            require(self.observer.get(3196, '/state').get('locked') is True)
            require(listener(process.pid, process.start_ticks, self.binding['boot_id']) == before)
            after = self.process(AUTH, self.show(AUTH))
            require(after.pid == process.pid and after.start_ticks == process.start_ticks
                    and base.snapshot(self.show(AUTH), AUTH) == value)

        def release(self, service, record):
            require(service in base.BASIC)
            return super().release(service, record)

        def start(self, service):
            require(service in base.BASIC)
            return super().start(service)

        def install(self, key):
            raise ValueError('resume_never_installs')

    return Resume


def build(base, checkpoint):
    Resume = resume_type(base, checkpoint)

    require(set(checkpoint) == {'schema', 'original_helper', 'original_expected', 'journal', 'auth',
                                'key_resident_basic_restore'}
            and checkpoint['schema'] == 'sage.lantern.basic-restore-resume.v1'
            and checkpoint['key_resident_basic_restore'] is True
            and checkpoint['original_helper']['sha256'] == ORIGINAL_SHA)
    auth = checkpoint['auth']
    require(set(auth) == {'pid', 'start_ticks', 'invocation', 'uid'}
            and all(type(auth[key]) is int and auth[key] > 0 for key in ('pid', 'start_ticks', 'uid'))
            and re.fullmatch('[0-9a-f]{32}', auth['invocation']))
    backend = Resume.__new__(Resume)
    backend.helper_raw = pinned(checkpoint['original_helper'])
    backend.expected = original_journal(base, checkpoint)
    expected = backend.expected
    backend.runtime = base.module(expected['runtime_updater'])
    backend.prior_runtime = base.module(expected['prior_runtime_updater'])
    require(backend.runtime.STATE == Path('/var/lib/sage-lantern-cold-runtime-update-r7')
            and backend.prior_runtime.STATE == Path('/var/lib/sage-lantern-cold-runtime-update')
            and backend.runtime.GUARD_NAME == 'zzzz-fresh-runtime-r7-update.conf'
            and backend.prior_runtime.GUARD_NAME == 'zzzz-fresh-runtime-update.conf')
    require(hashlib.sha256(backend.prior_runtime.GUARD).hexdigest() == base.R6_GUARD_SHA
            and backend.runtime.GUARD == b'[Unit]\nConditionPathExists=!/var/lib/sage-lantern-cold-runtime-update-r7\n[Service]\nRestart=no\n')
    backend.raw = pinned(expected['runtime_expected'])
    backend.prior_raw = pinned(expected['prior_runtime_expected'])
    backend.context = base.completion(backend.runtime, backend.raw)
    backend.prior_context = base.completion(backend.prior_runtime, backend.prior_raw, installed=False)
    base.lineage(backend.prior_context, backend.context, backend.runtime.CONSUMERS)
    backend.preflight = backend.runtime.module(backend.context['preflight'])
    backend.observer = backend.preflight.Observation()
    backend.observer.deadline = time.monotonic() + 180
    backend.sup = backend.context['supersession']
    backend.binding = base.decode(backend.runtime.pinned(backend.sup['binding']))
    backend.evidence = base.decode(backend.runtime.pinned(backend.sup['evidence']))
    backend.validator = base.module(dict(path=str(Path(backend.context['preflight']['path']).with_name(
        'auth-capability-supersession.py')), sha256=backend.sup['helper_sha256']))
    backend.installed, backend.released = set(base.TARGETS), {AUTH}
    backend.removed = {backend.guard(AUTH)}
    value = base.snapshot(backend.show(AUTH), AUTH)
    require(value['MainPID'] == str(auth['pid']) and value['InvocationID'] == auth['invocation'])
    backend.running, backend.ticks = {AUTH: value}, {AUTH: auth['start_ticks']}
    backend.receipts = {entry: backend.preflight.trusted(entry, backend.observer.uid, 512)
                        for entry in backend.preflight.REPORTS.iterdir()}
    for instance, digest in backend.binding['unknown_receipts'].items():
        require(hashlib.sha256(backend.receipts[backend.preflight.REPORTS / (instance + '.json')]).hexdigest() == digest)
    previous = backend.binding['current_auth']['instance_id']
    require(hashlib.sha256(backend.receipts[backend.preflight.REPORTS / (previous + '.json')]).hexdigest()
            == backend.evidence['current_auth_report_sha256'])
    backend.current()
    return backend


def transition(backend, basic, record):
    for service in basic:
        backend.current()
        record('release_' + service + '_intent')
        backend.release(service, record)
        backend.current()
        record('release_' + service + '_done')
        record('start_' + service + '_intent')
        backend.start(service)
        backend.current()
        record('start_' + service + '_done')
    return dict(basic_services_restored=True, auth_restarted=False, auth_locked=True,
                native_changed=False, voice_activated=False, audio_readiness_claimed=False,
                vault_keys_resident=True, cryptographic_lock_verified=False)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--checkpoint-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args(argv)
    require(os.geteuid() == 0 and sys.platform == 'linux' and not os.path.lexists(STATE))
    def expired(signum, frame):
        raise TimeoutError('basic_resume_deadline')
    signal.signal(signal.SIGALRM, expired)
    signal.alarm(240)
    try:
        raw = pinned(dict(path=args.checkpoint, sha256=args.checkpoint_sha256))
        checkpoint = json.loads(raw)
        helper_raw = pinned(checkpoint['original_helper'])
        require(hashlib.sha256(helper_raw).hexdigest() == ORIGINAL_SHA)
        base = types.ModuleType('frozen_basic_restore')
        base.__file__ = checkpoint['original_helper']['path']
        exec(compile(helper_raw, base.__file__, 'exec'), base.__dict__)
        checkpoint = base.decode(raw)
        backend = build(base, checkpoint)
        if not args.apply:
            print(json.dumps(dict(checkpoint_verified=True, mutation_authorized=False, auth_restarted=False,
                                  vault_keys_resident=True, cryptographic_lock_verified=False)))
            return
        STATE.mkdir(mode=0o700)
        backend.runtime.sync(STATE.parent)
        backend.runtime.durable(STATE / 'checkpoint.json', raw)
        counter = 0
        def record(phase):
            nonlocal counter
            counter += 1
            backend.runtime.durable(STATE / f'{counter:02d}-{phase}.json', json.dumps(dict(
                phase=phase, checkpoint_sha256=args.checkpoint_sha256)).encode())
        print(json.dumps(transition(backend, base.BASIC, record)))
    finally:
        signal.alarm(0)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Basic resume refused/incomplete; retain both journals. No automatic retry.', file=sys.stderr)
        raise SystemExit(2) from None
