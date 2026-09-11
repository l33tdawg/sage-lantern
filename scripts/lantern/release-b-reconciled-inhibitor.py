#!/usr/bin/env python3
"""Explicit final inhibitor ownership transfer; default read-only, never retry."""

import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


ROOT = Path('/var/lib/sage-lantern-b-reconciled-inhibitor-release')
VERIFIER_SHA = '5d172fb6f3a8650f46d88a6b66f954ac5fadebb57bfdb08dc104744ba145f2d2'
OLD_HELPER_SHA = '56ee0cc1616a022afd919676cdff6805a8a705d5d2e58da0de0a568d460aa4a3'
WHO = 'sage-lantern-b-reconciled-final-release'


def require(value):
    if value is not True:
        raise RuntimeError('b_reconciled_inhibitor_release_refused')


def load_verifier(path):
    import hashlib
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, 'rb') as stream:
        raw = stream.read(2097153)
    require(hashlib.sha256(raw).hexdigest() == VERIFIER_SHA)
    spec = importlib.util.spec_from_loader('reviewed_final_verifier', loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = path
    exec(compile(raw, path, 'exec'), module.__dict__)
    require(module.read(path) == raw)
    return module


def transfer(backend):
    backend.acquire_check()
    backend.validate_completion()
    backend.old_status()
    backend.record('old-holder-stop-intent', {'signal': 'SIGTERM', 'remask_required': True})
    backend.stop_old()
    backend.wait_old_remasked()
    backend.acquire_check()
    backend.archive_masks()
    for unit in backend.targets:
        backend.acquire_check()
        backend.record('unlink-' + unit, {'unit': unit})
        backend.remove_mask(unit)
        backend.acquire_check()
    backend.validate_completion()
    backend.final_idle()
    backend.acquire_check()
    backend.record('release-authorized', {'new_completion_verified': True, 'sleep_acceptance_verified': False})


class Backend:
    def __init__(self, helper, raw):
        self.helper, self.raw = helper, raw
        self.expected = helper.decode(raw)
        expected = self.expected
        require(set(expected) == {'schema', 'self_sha256', 'old_helper', 'old_expected', 'old_owner',
            'native_expected', 'native_complete_sha256'}
            and expected['schema'] == 'sage.lantern.b-reconciled-inhibitor-release.v1'
            and expected['self_sha256'] == helper.sha(helper.read(Path(__file__).absolute()))
            and expected['old_helper']['sha256'] == OLD_HELPER_SHA)
        self.old = helper.load(expected['old_helper'])
        old_raw = helper.pinned(expected['old_expected'])
        require(expected['old_expected']['path'] == str(self.old.STATE / 'expected.json')
            and expected['old_owner']['path'] == str(self.old.STATE / 'owner.json'))
        self.environment = self.old.Environment(helper.decode(old_raw), old_raw)
        self.old_owner = helper.decode(helper.pinned(expected['old_owner']))
        self.targets = self.old.TARGETS
        self.owner = None
        self.masks = None

    def record(self, name, value):
        self.helper.write(ROOT / (name + '.json'), self.helper.encoded(value))

    def validate_completion(self):
        require(self.helper.sha(self.helper.read(Path(__file__).absolute())) == self.expected['self_sha256'])
        native_raw = self.helper.pinned(self.expected['native_expected'])
        native = self.helper.decode(native_raw)
        require('mesh_reconciliation' in native)
        result = self.helper.verify_completed_native(native_raw, self.expected['native_complete_sha256'])
        old = self.environment.expected
        require(result == dict(verified=True, boot_id=old['boot_id'], policy_expected=old['policy_expected'],
            native_guard_removed=True, recovery_evidence=old['recovery_evidence']))

    def old_status(self):
        require(self.helper.pinned(self.expected['old_owner']) == self.helper.encoded(self.old_owner))
        self.environment.status()
        require(self.environment.owner == self.old_owner)

    def argv(self):
        return [self.old.INHIBIT, '--what=sleep', '--mode=block', '--who=' + WHO,
            '--why=' + self.helper.sha(self.raw), self.old.PYTHON, '-I', '-B',
            str(Path(__file__).absolute()), '--verifier', self.helper.__file__,
            '--expected', str(ROOT / 'expected.json'), '--expected-sha256', self.helper.sha(self.raw), '--holder']

    def acquire_check(self):
        self.environment.integrity()
        if self.owner is None:
            wrapper = self.environment.process(os.getppid())
            holder = self.environment.process(os.getpid())
            require(wrapper['argv'] == self.argv() and holder['argv'] == self.argv()[5:]
                and wrapper['image_sha256'] == self.environment.expected['systemd_inhibit_sha256']
                and holder['image_sha256'] == self.environment.expected['python_sha256']
                and holder['parent'] == wrapper['pid'])
            wrapper.pop('parent')
            self.owner = dict(wrapper=wrapper, holder=holder)
            self.record('owner', self.owner)
        for name, pin in self.owner.items():
            current = self.environment.process(pin['pid'])
            if name == 'wrapper':
                current.pop('parent')
            require(current == pin)
        rows = self.environment.rows()
        require([row for row in rows if row[1] == WHO] ==
            [['sleep', WHO, self.helper.sha(self.raw), 'block', 0, self.owner['wrapper']['pid']]])

    def stop_old(self):
        descriptor = os.pidfd_open(self.old_owner['holder']['pid'])
        try:
            self.old_status()
            self.acquire_check()
            signal.pidfd_send_signal(descriptor, signal.SIGTERM)
        finally:
            os.close(descriptor)

    def wait_old_remasked(self):
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            self.acquire_check()
            if all(not os.path.lexists(Path('/proc') / str(self.old_owner[name]['pid'])) for name in ('holder', 'wrapper')):
                require(not any(row[1] == self.old.WHO for row in self.environment.rows()))
                require(self.helper.decode(self.helper.read(self.old.STATE / 'remask-outcome.json')) ==
                    dict(inhibition_verified=True, resume_authorized=False))
                self.environment.removed = set()
                self.environment.masks = None
                require(set(self.environment.mask_state()) == set(self.targets))
                self.environment.idle()
                return
            time.sleep(0.2)
        raise TimeoutError('old_holder_outcome_unknown')

    def archive_masks(self):
        self.masks = self.environment.mask_state()
        self.environment.masks = self.masks
        self.record('remasked-identities', self.masks)
        archive = ROOT / 'masks'
        archive.mkdir(mode=0o700)
        self.helper.sync(ROOT)
        for unit in self.targets:
            os.symlink('/dev/null', archive / unit)
        self.helper.sync(archive)

    def remove_mask(self, unit):
        self.environment.idle()
        self.environment.mask_state()
        path = self.old.MASK_ROOT / unit
        require(list(self.old.signature(path.lstat())) == self.masks[unit] and os.readlink(path) == '/dev/null')
        path.unlink()
        self.helper.sync(path.parent)
        self.environment.removed.add(unit)
        self.environment.reload()

    def final_idle(self):
        require(not any(row[1] == self.old.WHO for row in self.environment.rows()))
        self.environment.mask_state()
        self.environment.idle()
        self.environment.policy.preflight.SuspendSystem()._sleep_idle(time.monotonic() + 30)

    def fail_closed(self):
        self.record('remask-intent', {'automatic_resume': False})
        for unit in self.targets:
            path = self.old.MASK_ROOT / unit
            self.helper.parents(path)
            if os.path.lexists(path):
                require(path.is_symlink() and os.readlink(path) == '/dev/null')
            else:
                os.symlink('/dev/null', path)
                self.helper.sync(path.parent)
        self.environment.reload()
        self.environment.removed = set()
        self.environment.masks = None
        require(set(self.environment.mask_state()) == set(self.targets))
        self.record('remasked', {'inhibitor_release_authorized': False})


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--verifier', required=True)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--holder', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    helper = load_verifier(args.verifier)
    raw = helper.pinned(dict(path=args.expected, sha256=args.expected_sha256))
    backend = Backend(helper, raw)
    if args.holder:
        require(not args.apply and args.expected == str(ROOT / 'expected.json'))
        require(helper.decode(helper.read(ROOT / 'intent.json')) ==
            dict(ownership_transfer_authorized=True, automatic_retry=False))
        for number in (signal.SIGINT, signal.SIGTERM):
            signal.signal(number, signal.SIG_IGN)
        def expired(signum, frame):
            raise TimeoutError('final_transfer_deadline')
        signal.signal(signal.SIGALRM, expired)
        signal.alarm(240)
        try:
            transfer(backend)
        except BaseException as error:
            signal.alarm(0)
            try:
                backend.record('failed-held', {'exception': type(error).__name__, 'retry_authorized': False})
                backend.fail_closed()
            finally:
                while True:
                    time.sleep(1)
        signal.alarm(0)
        return
    require(not os.path.lexists(ROOT))
    require(hasattr(os, 'pidfd_open') and hasattr(signal, 'pidfd_send_signal'))
    backend.environment.executable_contract()
    backend.old_status()
    backend.validate_completion()
    require(not any(row[1] == WHO for row in backend.environment.rows()))
    if not args.apply:
        print('{"inspected":true,"released":false}')
        return
    helper.parents(ROOT)
    ROOT.mkdir(mode=0o700)
    helper.sync(ROOT.parent)
    helper.write(ROOT / 'expected.json', raw)
    backend.record('intent', {'ownership_transfer_authorized': True, 'automatic_retry': False})
    process = subprocess.Popen(backend.argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=backend.old.ENV, start_new_session=True, close_fds=True)
    backend.record('spawned', {'pid': process.pid})
    process.wait(timeout=300)
    require(process.returncode == 0 and not any(row[1] in (WHO, backend.old.WHO) for row in backend.environment.rows()))
    require(helper.decode(helper.read(ROOT / 'release-authorized.json')) ==
        dict(new_completion_verified=True, sleep_acceptance_verified=False))
    backend.record('complete', {'released': True, 'sleep_acceptance_verified': False})
    print('{"released":true,"sleep_acceptance_verified":false}')


if __name__ == '__main__':
    try:
        main()
    except Exception:
        raise SystemExit('B final release refused/unknown; preserve both holders and journals; no retry.') from None
