#!/usr/bin/env python3
"""One explicit reconciliation of a retained failed B inhibitor; inspect by default."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time
import types
import uuid


ROOT = Path('/var/lib/sage-lantern-b-failed-inhibitor-reconciliation')
PRIOR = Path('/var/lib/sage-lantern-b-reconciled-inhibitor-release')
RELEASER_SHA = '241d1a93e8b01d1c4d4ec44a1c3031f6077c72eced3996df2de366f0ee58ab0e'
VERIFIER_SHA = '5d172fb6f3a8650f46d88a6b66f954ac5fadebb57bfdb08dc104744ba145f2d2'
WHO = 'sage-lantern-b-failed-release-reconciliation'
ENV = dict(PATH='/usr/sbin:/usr/bin:/sbin:/bin', LANG='C', LC_ALL='C')


def require(value):
    if value is not True:
        raise ValueError('reconciliation_refused')


def signature(info):
    return [getattr(info, name) for name in ('st_dev', 'st_ino', 'st_mode', 'st_uid',
        'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')]


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
            and not before.st_mode & 0o022 and before.st_size <= 2097152)
        with os.fdopen(os.dup(descriptor), 'rb') as stream:
            raw = stream.read(2097153)
        require(len(raw) == before.st_size and signature(before) == signature(os.fstat(descriptor))
            == signature(path.lstat()))
        return raw
    finally:
        os.close(descriptor)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def decode(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def pinned(pin):
    require(type(pin) is dict and set(pin) == {'path', 'sha256'})
    raw = read(pin['path'])
    require(sha(raw) == pin['sha256'])
    return raw


def load(pin):
    raw = pinned(pin)
    module = types.ModuleType('reconcile_' + pin['sha256'])
    module.__file__ = pin['path']
    sys.modules[module.__name__] = module
    exec(compile(raw, module.__file__, 'exec'), module.__dict__)
    return module


def failure(error, stage):
    frames = []
    trace = error.__traceback__
    while trace:
        frames.append(dict(source=Path(trace.tb_frame.f_code.co_filename).name[:100],
            function=trace.tb_frame.f_code.co_name[:100], line=trace.tb_lineno))
        trace = trace.tb_next
    return dict(stage=stage, error_class=type(error).__name__[:80], frames=frames[-8:],
        historical_cause='unknown', retry_authorized=False)


def broker_inputs_non_sleep(owner):
    from sage_lantern import deployment_bringup as module
    from sage_lantern.mesh_daemon import MeshDaemon, device_policy, load_config
    from sage_lantern.device_access import MARKER, _marker_state
    require('verify-deployment-active' in MeshDaemon.handle.__code__.co_consts)
    require(all(sha(module.read_public(path)) == digest for path, digest in owner.inventory.items()))
    config = load_config()
    require(module.read_public(module.SYSTEMD + module.BROKER + '.d/20-radio.conf') == device_policy(config).encode())
    require(_marker_state(MARKER) is True)
    require(not os.path.lexists(module.FENCE) and not os.path.lexists(module.DEPLOYMENT_FENCE))
    for service in (module.BROKER, module.VOICE):
        value = owner.system._show(service, owner.system.clock() + 5,
            ('FragmentPath', 'DropInPaths', 'NeedDaemonReload', 'Restart', 'LoadState'))
        require(value['FragmentPath'] == module.SYSTEMD + service and value['LoadState'] == 'loaded'
            and value['NeedDaemonReload'] == 'no' and value['Restart'] == 'no')
        expected = {path for path in owner.inventory if path.startswith(module.SYSTEMD + service + '.d/')}
        require(set(value['DropInPaths'].split()) == expected)


def current_handoff(historical, baseline, model, tools_sha256, boot, current_epoch, expected_epoch):
    require(historical['unit_id'] == 'unit-b' and historical['boot_id'] == boot
        and historical['baseline'] == baseline and historical['model'] == model
        and historical['tools_sha256'] == tools_sha256
        and type(current_epoch) is str and bool(current_epoch) and current_epoch == expected_epoch)


class Backend:
    def __init__(self, raw):
        self.raw, self.expected = raw, decode(raw)
        require(encoded(self.expected) == raw)
        expected = self.expected
        require(set(expected) == {'schema', 'attempt_id', 'boot_id', 'access_epoch', 'self',
            'releaser', 'verifier', 'failed_tree', 'masks', 'historical_plan', 'processes',
            'current_files', 'display_receipt'})
        require(expected['schema'] == 'sage.lantern.b-failed-inhibitor-reconciliation.v1')
        for name in ('attempt_id', 'boot_id'):
            require(str(uuid.UUID(expected[name])) == expected[name] and uuid.UUID(expected[name]).int > 0)
        require(type(expected['access_epoch']) is str and bool(expected['access_epoch']))
        require(expected['self']['path'] == str(Path(__file__).absolute()))
        pinned(expected['self'])
        require(expected['releaser']['sha256'] == RELEASER_SHA and expected['verifier']['sha256'] == VERIFIER_SHA)
        self.helper = load(expected['verifier'])
        self.releaser = load(expected['releaser'])
        self.prior_raw = read(PRIOR / 'expected.json')
        self.prior = self.releaser.Backend(self.helper, self.prior_raw)
        self.environment, self.old = self.prior.environment, self.prior.old
        self.directory = ROOT / expected['attempt_id']
        self.owner = None
        self.retired = False
        self.removed = set()
        self.unlink_intent = set()
        self.stage = 'constructed'
        self.started_at = time.monotonic()
        self.deadline = time.monotonic() + 240
        self.native_raw = self.helper.pinned(self.prior.expected['native_expected'])
        self.native = self.helper.Backend(self.helper.decode(self.native_raw), self.native_raw,
            completed_sha256=self.prior.expected['native_complete_sha256'])
        self.native.audio_check = self.audio_check
        self.ordinary = False
        self.historical = decode(pinned(expected['historical_plan']))
        require(sha(self.native.audio.canonical(self.historical)) == self.native.expected['audio_plan_sha256'])
        require(self.historical['unit_id'] == 'unit-b' and self.historical['boot_id'] == expected['boot_id'])
        self.old_owner = decode(read(PRIOR / 'owner.json'))

    def record(self, name, value):
        self.helper.write(self.directory / (name + '.json'), encoded(value))

    def history(self):
        expected = self.expected['failed_tree']
        require(type(expected) is dict and 4 <= len(expected) <= 128)
        observed = {}
        for path in PRIOR.rglob('*'):
            require(len(observed) < 128)
            info = path.lstat()
            require(info.st_uid == 0 and not info.st_mode & 0o022 if not stat.S_ISLNK(info.st_mode) else info.st_uid == 0)
            relative = str(path.relative_to(PRIOR))
            if stat.S_ISREG(info.st_mode):
                observed[relative] = dict(identity=signature(info), sha256=sha(read(path)))
            elif stat.S_ISLNK(info.st_mode):
                require(path.parent == PRIOR / 'masks' and path.name in self.old.TARGETS
                    and os.readlink(path) == '/dev/null')
                observed[relative] = dict(identity=signature(info), target='/dev/null')
            else:
                require(stat.S_ISDIR(info.st_mode))
                observed[relative] = dict(identity=signature(info), directory=True)
        require(observed == expected and 'failed-held.json' in expected and 'owner.json' in expected
            and 'expected.json' in expected and 'remasked.json' in expected)
        require(not os.path.lexists(PRIOR / 'complete.json') and not os.path.lexists(PRIOR / 'release-authorized.json'))
        require(decode(read(PRIOR / 'failed-held.json')) == dict(exception='DeploymentDrainError', retry_authorized=False))
        require(decode(read(PRIOR / 'remasked.json')) == dict(inhibitor_release_authorized=False))

    def pins(self):
        require(time.monotonic() < self.deadline)
        self.history()
        for name in ('self', 'releaser', 'verifier', 'historical_plan'):
            pinned(self.expected[name])
        require(Path('/proc/sys/kernel/random/boot_id').read_text().strip() == self.expected['boot_id'])
        from sage_lantern.device_access import access_epoch
        require(access_epoch() == self.expected['access_epoch'])
        self.environment.integrity()
        files = self.expected['current_files']
        require(type(files) is dict and len(files) <= 128 and {
            '/etc/sage-lantern-display-power.enabled',
            '/usr/local/libexec/sage-lantern-display-power',
            '/usr/local/libexec/sage-lantern-display-session'} <= set(files))
        require(all(sha(read(path)) == digest for path, digest in files.items()))
        require(read('/etc/sage-lantern-display-power.enabled') == b'fresh unit-b\n')
        receipt_path = Path(self.expected['display_receipt']['path'])
        require(receipt_path.name == 'complete.json'
            and receipt_path.parent.parent == Path('/var/lib/sage-lantern-display-activation'))
        receipt = decode(pinned(self.expected['display_receipt']))
        require(receipt['unit_id'] == 'unit-b' and receipt['boot_id'] == self.expected['boot_id']
            and receipt['display_active'] is True and receipt['boot_enabled'] is False)
        processes = self.expected['processes']
        required = {'sage-lantern-personal-sage.service', 'sage-lantern-device-auth.service',
            'sage-lantern-llm.service', 'sage-lantern-mesh-runtime.service', 'sage-lantern-voice-loop.service',
            'sage-lantern-display-power.service'}
        require(set(processes) == required)
        for unit, expected in processes.items():
            fields = ('MainPID', 'InvocationID', 'ActiveState', 'Job', 'NRestarts', 'ControlGroup',
                'NeedDaemonReload', 'FragmentPath', 'DropInPaths', 'Restart', 'KillMode', 'Slice', 'Transient')
            before = self.native.policy.system._show(unit, time.monotonic() + 5, fields)
            require(before == expected['manager'] and before['ActiveState'] == 'active'
                and before['Job'] in ('', '0') and before['NeedDaemonReload'] == 'no'
                and before['Transient'] == 'no' and before['ControlGroup'] == '/system.slice/' + unit
                and before['KillMode'] == 'control-group' and before['Slice'] == 'system.slice')
            require({before['FragmentPath'], *before['DropInPaths'].split()} <= set(files))
            pid = int(before['MainPID'])
            root = Path('/proc') / str(pid)
            info = (root / 'stat').read_text().rsplit(')', 1)[1].split()
            require(int(info[19]) == expected['start_ticks'] and info[0] not in ('Z', 'X'))
            with (root / 'exe').open('rb') as stream:
                require(hashlib.file_digest(stream, 'sha256').hexdigest() == expected['image_sha256'])
            require(self.native.policy.system._show(unit, time.monotonic() + 5, fields) == before)
            if unit == 'sage-lantern-display-power.service':
                require(receipt['display_process'] == dict(pid=pid, start_ticks=expected['start_ticks'],
                    invocation=before['InvocationID'], image_sha256=expected['image_sha256']))

    def owned(self, owner, who, why):
        for name in ('wrapper', 'holder'):
            value = self.environment.process(owner[name]['pid'])
            if name == 'wrapper':
                value.pop('parent')
            require(value == owner[name])
        require([row for row in self.environment.rows() if row[1] == who] ==
            [['sleep', who, why, 'block', 0, owner['wrapper']['pid']]])

    def masks(self):
        self.environment.removed = set(self.removed)
        self.environment.masks = self.expected['masks']
        require(set(self.expected['masks']) == set(self.old.TARGETS))
        result = self.environment.mask_state()
        require(set(result) == set(self.old.TARGETS) - self.removed)
        self.environment.idle()

    def protection(self):
        self.masks()
        if not self.retired:
            self.owned(self.old_owner, self.releaser.WHO, sha(self.prior_raw))
        if self.owner is not None:
            self.owned(self.owner, WHO, sha(self.raw))

    def audio_check(self):
        from sage_lantern import audio_readiness
        from sage_lantern.deployment_bringup import ColdBrokerBootstrap
        from sage_lantern.mesh_daemon import load_config
        audio, owner = self.native.audio, self.native.audio_owner
        record_raw = audio.trusted(audio.RECORD)
        require(sha(record_raw) == self.historical['bootstrap_sha256'])
        record = audio.decode(record_raw)
        owner.coordinator.lifetime(record_raw, record['inventory'], self.expected['boot_id'])
        broker = ColdBrokerBootstrap(record['inventory'])
        if self.ordinary:
            broker.inputs()
        else:
            self.protection()
            broker_inputs_non_sleep(broker)
        require(broker.active() == record['evidence'])
        config = load_config()
        audio.validate_unit(audio.trusted('/etc/sage-lantern/unit.env'), config)
        commissioned = audio_readiness.commissioned_receipt()
        require(commissioned['binding']['unit_id'] == 'unit-b' and audio_readiness.admission_verified() is True)
        argv = owner.collector.model_argv(broker.system, owner.helper)
        model = owner.helper.Recovery.model_ready(types.SimpleNamespace(system=broker.system, expected={'model_argv': argv}))
        require(model is not None and model == self.historical['model'])
        unit = broker.system._show(owner.helper.MODEL, time.monotonic() + 5,
            ('FragmentPath', 'DropInPaths', 'NeedDaemonReload'))
        require(unit['FragmentPath'] == '/etc/systemd/system/' + owner.helper.MODEL and unit['NeedDaemonReload'] == 'no')
        paths = {unit['FragmentPath'], *unit['DropInPaths'].split(), argv[0]}
        for flag in ('--model', '--mmproj'):
            if flag in argv:
                require(argv.count(flag) == 1 and argv.index(flag) + 1 < len(argv))
                paths.add(argv[argv.index(flag) + 1])
        require('--model' in argv)
        sources = owner.collector.runtime_sources(owner.helper, Path(owner.tools['runtime_wheel']['path']), owner.site)
        baseline = dict(inventory=record['inventory'], runtime_sources=sources,
            files={path: owner.helper.file_hash(path) for path in sorted(paths)}, model_argv=argv,
            commission_sha256=audio.sha(audio.canonical(commissioned)),
            actor=dict(chain_id=config['chain_id'], agent_id=config['agent_id']))
        from sage_lantern.device_access import access_epoch
        current_handoff(self.historical, baseline, model, audio.sha(owner.tools_raw), self.expected['boot_id'],
            access_epoch(), self.expected['access_epoch'])
        for name in ('coordinator', 'adapter', 'helper', 'collector', 'runtime_wheel'):
            audio.pinned(owner.tools[name])
        documents = audio.documents(self.historical, owner.tools, record_raw)
        directory = audio.HANDOFF / self.expected['boot_id']
        require(decode(read(directory / 'complete.json')) == dict(schema='sage.lantern.first-audio-handoff-result.v1',
            unit_id='unit-b', boot_id=self.expected['boot_id'], files={str(path): sha(raw) for path, raw in documents.items()},
            services_started=False, services_enabled=False, reboot_accepted=False))
        require(all(read(path) == raw for path, raw in documents.items()))
        require(read(self.helper.STARTUP) == documents[directory / 'startup.json'])
        owner.coordinator.validate_full_config(decode(documents[directory / 'startup.json']))
        self.helper.pinned(self.native.expected['startup_service'])
        system = self.native.policy.system
        system._empty_tree(system._cgroup('/system.slice/sage-lantern-startup.service'), time.monotonic() + 5)
        value = self.native.policy.properties('sage-lantern-startup.service')
        require(value['LoadState'] == 'loaded' and value['Transient'] == 'no'
            and value['FragmentPath'] == self.helper.STARTUP_SERVICE and value['DropInPaths'] == ''
            and value['NeedDaemonReload'] == 'no' and value['Restart'] == 'no'
            and value['ActiveState'] == 'inactive' and value['MainPID'] == '0' and value['Job'] in ('', '0'))
        link = Path('/etc/systemd/system/multi-user.target.wants/sage-lantern-startup.service')
        self.helper.parents(link)
        require(link.is_symlink() and link.resolve() == Path(self.helper.STARTUP_SERVICE))
        require(audio.trusted(audio.RECORD) == record_raw and load_config() == config
            and audio_readiness.commissioned_receipt() == commissioned
            and audio_readiness.admission_verified() is True and broker.active() == record['evidence'])
        require(owner.helper.Recovery.model_ready(types.SimpleNamespace(system=broker.system,
            expected={'model_argv': argv})) == model
            and owner.collector.model_argv(broker.system, owner.helper) == argv
            and owner.collector.runtime_sources(owner.helper, Path(owner.tools['runtime_wheel']['path']), owner.site) == sources)
        if self.ordinary:
            broker.inputs()
        else:
            broker_inputs_non_sleep(broker)
        self.pins()
        self.protection()

    def inspect(self):
        if self.owner is None:
            require(not any(row[1] == WHO for row in self.environment.rows()))
        self.pins()
        self.protection()
        self.environment.executable_contract()
        self.native.check()
        self.pins()
        self.protection()

    def holder_argv(self):
        return [self.old.PYTHON, '-I', '-B', self.expected['self']['path'], '--expected',
            str(self.directory / 'expected.json'), '--expected-sha256', sha(self.raw), '--holder']

    def argv(self):
        return [self.old.INHIBIT, '--what=sleep', '--mode=block', '--who=' + WHO,
            '--why=' + sha(self.raw), *self.holder_argv()]

    def identify(self):
        wrapper, holder = self.environment.process(os.getppid()), self.environment.process(os.getpid())
        require(wrapper['argv'] == self.argv() and holder['argv'] == self.holder_argv()
            and holder['parent'] == wrapper['pid']
            and wrapper['image_sha256'] == self.environment.expected['systemd_inhibit_sha256']
            and holder['image_sha256'] == self.environment.expected['python_sha256'])
        wrapper.pop('parent')
        self.owner = dict(wrapper=wrapper, holder=holder)
        self.protection()
        self.record('owner', self.owner)

    def retire(self):
        descriptor = os.pidfd_open(self.old_owner['holder']['pid'])
        try:
            self.pins()
            self.protection()
            require(self.owner is not None)
            self.record('retire-intent', dict(pid=self.old_owner['holder']['pid'], signal='SIGKILL'))
            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
        finally:
            os.close(descriptor)
        deadline = min(self.deadline, time.monotonic() + 15)
        while time.monotonic() < deadline:
            self.owned(self.owner, WHO, sha(self.raw))
            if all(not os.path.lexists(Path('/proc') / str(self.old_owner[name]['pid'])) for name in ('wrapper', 'holder')):
                require(not any(row[1] == self.releaser.WHO for row in self.environment.rows()))
                self.retired = True
                self.record('retired', {'prior_history_unchanged': True})
                return
            time.sleep(0.1)
        raise TimeoutError('retirement_unknown')

    def unmask(self, unit):
        self.pins()
        self.protection()
        require(self.retired and self.owner is not None and unit not in self.removed)
        path = self.old.MASK_ROOT / unit
        self.record('unlink-' + unit, {'unit': unit})
        require(list(self.old.signature(path.lstat())) == self.expected['masks'][unit]
            and os.readlink(path) == '/dev/null')
        self.unlink_intent.add(unit)
        path.unlink()
        self.removed.add(unit)
        self.helper.sync(path.parent)
        self.environment.reload()
        self.protection()

    def cleanup(self):
        self.deadline = time.monotonic() + 40
        require(self.owner is not None)
        self.owned(self.owner, WHO, sha(self.raw))
        for unit in self.old.TARGETS:
            path = self.old.MASK_ROOT / unit
            self.helper.parents(path)
            if unit in self.unlink_intent and not os.path.lexists(path):
                os.symlink('/dev/null', path)
                self.helper.sync(path.parent)
            else:
                require(list(self.old.signature(path.lstat())) == self.expected['masks'][unit]
                    and os.readlink(path) == '/dev/null')
        self.environment.reload()
        self.environment.removed = set()
        self.environment.masks = None
        masks = self.environment.mask_state()
        require(set(masks) == set(self.old.TARGETS))
        self.environment.idle()
        self.owned(self.owner, WHO, sha(self.raw))
        self.record('remasked', {'masks': masks, 'inhibitor_retained': True})


def transition(backend):
    for stage, action in (('identify', backend.identify), ('masked_completion', backend.inspect),
        ('retire_failed_holder', backend.retire)):
        backend.stage = stage
        action()
    for unit in backend.old.TARGETS:
        backend.stage = 'unmask:' + unit
        backend.unmask(unit)
    backend.stage = 'ordinary_completion'
    backend.ordinary = True
    backend.inspect()
    backend.stage = 'ordinary_sleep'
    system = backend.native.policy.preflight.SuspendSystem()
    require(system.verify_sleep_idle().confirmed is True)
    backend.pins()
    backend.protection()
    backend.stage = 'release_authorization'
    backend.record('release-authorized', {'ordinary_completion_verified': True,
        'historical_cause': 'unknown', 'physical_verified': False, 'reboot_verified': False})


def protected_failure(backend, error):
    signal.alarm(0)
    for number in (signal.SIGTERM, signal.SIGINT):
        signal.signal(number, signal.SIG_IGN)
    evidence = failure(error, backend.stage)
    started = getattr(backend, 'started_at', None)
    if type(started) in (int, float):
        evidence['elapsed_seconds'] = round(time.monotonic() - started, 3)
    try:
        signal.alarm(40)
        backend.cleanup()
        evidence['containment_verified'] = True
    except BaseException as cleanup_error:
        evidence['containment_verified'] = False
        evidence['cleanup'] = failure(cleanup_error, 'cleanup')
    finally:
        signal.alarm(0)
    try:
        backend.record('failed-held', evidence)
    except BaseException:
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--expected', required=True)
    parser.add_argument('--expected-sha256', required=True)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--holder', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    require(os.geteuid() == 0 and sys.platform == 'linux' and sys.prefix == '/opt/sage-lantern/venv')
    def cancelled(signum, frame):
        raise InterruptedError('reconciliation_cancelled')
    for number in (signal.SIGTERM, signal.SIGINT, signal.SIGALRM):
        signal.signal(number, cancelled)
    signal.alarm(240)
    backend = Backend(pinned({'path': args.expected, 'sha256': args.expected_sha256}))
    if args.holder:
        require(not args.apply and args.expected == str(backend.directory / 'expected.json'))
        require(read(backend.directory / 'expected.json') == backend.raw)
        try:
            transition(backend)
        except BaseException as error:
            protected_failure(backend, error)
            while True:
                time.sleep(1)
        return
    backend.inspect()
    if not args.apply:
        print('{"non_sleep_completion_verified":true,"retained_inhibition_verified":true,"released":false}')
        return
    backend.helper.parents(ROOT)
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
        backend.helper.sync(ROOT.parent)
    require(not ROOT.is_symlink() and ROOT.stat().st_uid == 0 and stat.S_IMODE(ROOT.stat().st_mode) == 0o700)
    descriptor = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    backend.directory.mkdir(mode=0o700)
    backend.helper.sync(ROOT)
    backend.record('expected', backend.expected)
    backend.record('intent', {'separate_reconciliation': True, 'automatic_retry': False})
    child = subprocess.Popen(backend.argv(), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, env=ENV, start_new_session=True)
    try:
        backend.record('spawned', {'pid': child.pid})
        status = child.wait(timeout=300)
        require(status == 0)
        require(decode(read(backend.directory / 'release-authorized.json')) ==
            dict(ordinary_completion_verified=True, historical_cause='unknown', physical_verified=False, reboot_verified=False))
        require(not any(row[1] in (WHO, backend.releaser.WHO) for row in backend.environment.rows()))
        backend.retired = True
        backend.removed = set(backend.old.TARGETS)
        backend.ordinary = True
        backend.inspect()
        backend.record('complete', {'released': True, 'physical_verified': False, 'reboot_verified': False})
        print('{"released":true,"physical_verified":false,"reboot_verified":false}')
    except BaseException as error:
        try:
            backend.record('parent-unknown', {**failure(error, 'parent_completion'),
                'active_state_unknown': True, 'child_cleanup_claimed': False})
        except BaseException:
            pass
        raise


if __name__ == '__main__':
    main()
