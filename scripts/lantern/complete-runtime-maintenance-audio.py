"""Trusted same-process tail for prepare-runtime-maintenance; no standalone CLI.

This tail requires an explicit one-capture approval, a newly observed owner
unlock and the pinned candidate snapshot. It preserves old records before
moving them, invokes the installed capture validator once, starts only through
ColdBrokerBootstrap, and publishes a distinct lifetime and matched next-boot
policy. No old receipt is relabelled and no old lifetime is overwritten.
"""

import os
from pathlib import Path
import signal
import stat
import subprocess
import time


ROOT = Path('/var/lib/sage-lantern-maintained-audio')
RUN_ROOT = Path('/run')
VOICE_RUN = Path('/run/sage-lantern-voice')
CONTROL = Path('/run/sage-lantern-mesh-control')
BOOTSTRAP = Path('/run/sage-lantern-voice-bootstrap.json')
COMMISSION = Path('/etc/sage-lantern/audio-commissioned.json')
CAPTURE = Path('/etc/sage-lantern/audio-capture.json')
MARKER = Path('/var/lib/sage-lantern/LOCAL_VOICE_READY')
STARTUP = Path('/etc/sage-lantern/startup.json')
LATEST = Path('/var/lib/sage-lantern-startup/latest.json')
COORDINATOR = Path('/opt/sage-lantern/libexec/startup-coordinator.py')
ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent', 'LANG': 'C', 'PYTHONDONTWRITEBYTECODE': '1'}
UNIX_TABLE = Path('/proc/net/unix')


def require(value):
    if not value:
        raise ValueError('maintenance_audio_completion_refused')


def installed_audio(backend, arguments):
    backend.current_after_auth()
    result = subprocess.run(['/opt/sage-lantern/venv/bin/python', '-I', '-B', '-m',
        'sage_lantern.audio_readiness', *arguments], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, env=ENV, timeout=35, check=True)
    require(len(result.stdout) <= 262144)
    backend.current_after_auth()
    return backend.helper.decode(result.stdout) if result.stdout.strip() else None


def operational(backend):
    deadline = time.monotonic() + 15
    while True:
        backend.current_after_auth()
        result = subprocess.run(['/opt/sage-lantern/venv/bin/python', '-I', '-B', '-c',
            'from sage_lantern.audio_readiness import operational_ready; '
            'raise SystemExit(0 if operational_ready() is True else 1)'],
            env=ENV, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=5, check=False)
        backend.current_after_auth()
        require(result.returncode in (0, 1))
        if result.returncode == 0:
            return
        require(time.monotonic() < deadline)
        time.sleep(0.2)


def predicted_capture(before, candidate_files, site, sha):
    result = dict(before, inputs=dict(before['inputs']))
    names = {str(Path(site) / name): raw for name, raw in candidate_files.items()
             if name.startswith('sage_lantern/') and name.endswith('.py')}
    require(set(names) == {name for name in result['inputs'] if name.startswith(site + '/sage_lantern/')})
    result['inputs'].update({name: sha(raw) for name, raw in names.items()})
    return result


def records(root, uid, helper):
    """Bounded same-filesystem custody inventory; bytes never leave the device."""
    root = Path(root)
    require(root in {VOICE_RUN, CONTROL, BOOTSTRAP, MARKER, CAPTURE, COMMISSION, helper.SUPERVISOR})
    root_info = root.lstat()
    result = {}
    pending = [root]
    total = 0
    while pending:
        path = pending.pop()
        info = path.lstat()
        require(len(result) < 256 and info.st_dev == root_info.st_dev and info.st_uid in (0, uid)
                and not info.st_mode & 0o022 and not path.is_mount())
        relative = str(path.relative_to(root))
        metadata = helper.metadata(info)
        if stat.S_ISDIR(info.st_mode):
            result[relative] = dict(metadata=metadata)
            pending.extend(path.iterdir())
        elif stat.S_ISSOCK(info.st_mode):
            require(root == CONTROL and relative == 'broker.sock' and info.st_uid == 0)
            with UNIX_TABLE.open('rb') as stream:
                table = stream.read(1048577)
            require(len(table) <= 1048576 and not any(line.split()[-1:] == [str(path).encode()]
                    for line in table.splitlines()[1:]))
            result[relative] = dict(metadata=metadata, stale_socket=True)
        else:
            require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_size <= 32 * 1024**2)
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                require(helper.metadata(os.fstat(descriptor)) == metadata)
                with os.fdopen(os.dup(descriptor), 'rb') as stream:
                    raw = stream.read(info.st_size + 1)
                require(len(raw) == info.st_size and helper.metadata(os.fstat(descriptor)) == metadata
                        == helper.metadata(path.lstat()))
            finally:
                os.close(descriptor)
            total += len(raw)
            require(total <= 64 * 1024**2)
            result[relative] = dict(metadata=metadata, sha256=helper.sha(raw))
    return result


def preserve(backend, root, destination, durable_copy=None):
    uid = backend.expected['services']['sage-lantern-voice-loop.service']['process']['uid']
    observed = records(root, uid, backend.helper)
    backend.current_after_auth()
    require(root.lstat().st_dev == destination.parent.lstat().st_dev and not os.path.lexists(destination))
    backend.record('archive_' + root.name + '_intent', dict(path=str(root), inventory=observed))
    if durable_copy is not None:
        require(not os.path.lexists(durable_copy))
        for relative, value in sorted(observed.items(), key=lambda item: (len(Path(item[0]).parts), item[0])):
            source = root if relative == '.' else root / relative
            target = durable_copy if relative == '.' else durable_copy / relative
            if value.get('stale_socket') is True:
                continue
            if 'sha256' not in value:
                target.mkdir(mode=0o700)
                backend.helper.sync(target.parent)
            else:
                descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    require(backend.helper.metadata(os.fstat(descriptor)) == value['metadata'])
                    with os.fdopen(os.dup(descriptor), 'rb') as stream:
                        raw = stream.read(value['metadata']['st_size'] + 1)
                    require(backend.helper.sha(raw) == value['sha256']
                            and backend.helper.metadata(os.fstat(descriptor)) == value['metadata'])
                finally:
                    os.close(descriptor)
                backend.helper.write(target, raw)
                os.chmod(target, stat.S_IMODE(value['metadata']['st_mode']))
                backend.helper.sync(target.parent)
    for relative, value in observed.items():
        source = root if relative == '.' else root / relative
        if 'sha256' in value:
            descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
            try:
                require(backend.helper.metadata(os.fstat(descriptor)) == value['metadata'])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    require(records(root, uid, backend.helper) == observed)
    os.rename(root, destination)
    backend.helper.sync(root.parent)
    backend.helper.sync(destination.parent)
    require(not os.path.lexists(root))
    for relative, original in observed.items():
        target = destination if relative == '.' else destination / relative
        now = backend.helper.metadata(target.lstat())
        require(all(now[key] == value for key, value in original['metadata'].items()
                    if relative != '.' or key != 'st_ctime_ns'))
    backend.record('archive_' + root.name + '_done', dict(destination=str(destination), inventory=observed))


def release(backend, service):
    backend.current_after_auth()
    require(service in backend.guarded)
    path = backend.guard_path(service)
    require(backend.helper.public(path) == backend.guard())
    backend.record('audio_release_' + service + '_intent')
    path.unlink()
    backend.helper.sync(path.parent)
    backend.guarded.remove(service)
    backend.system._command(['daemon-reload'], backend.system.clock() + 10)
    backend.current_after_auth()
    backend.record('audio_release_' + service + '_done')


def documents(root, tools, baseline, lifetime, helper):
    # Staging may live under /root, which ProtectHome hides from the startup
    # service. Retain the exact pinned bytes beside the new lifetime instead.
    retained = {}
    boot_tools = {}
    for name in ('adapter', 'helper', 'collector', 'runtime_wheel'):
        filename = 'startup-' + name + ('.whl' if name == 'runtime_wheel' else '.py')
        retained[filename] = helper.pinned(tools[name])
        boot_tools[name] = dict(path=str(root / filename), sha256=tools[name]['sha256'])
    baseline_raw = helper.canonical(baseline)
    policy = dict(schema='sage.lantern.startup-audio-policy.v1',
        **{name: boot_tools[name] for name in ('helper', 'collector', 'runtime_wheel')},
        baseline=dict(path=str(root / 'baseline.json'), sha256=helper.sha(baseline_raw)))
    policy_raw = helper.canonical(policy)
    config = dict(schema='sage.lantern.startup-coordinator.v1', adapter=boot_tools['adapter'],
        coordinator=dict(path=str(COORDINATOR), sha256=tools['coordinator']['sha256']),
        policy=dict(path=str(root / 'policy.json'), sha256=helper.sha(policy_raw)),
        initial_lifetime=dict(path=str(root / 'new-lifetime.json'), sha256=helper.sha(lifetime)))
    return {**retained, 'baseline.json': baseline_raw, 'policy.json': policy_raw,
            'startup.json': helper.canonical(config), 'new-lifetime.json': lifetime}


def replace_public(backend, path, raw, old_pin, root):
    require(path in (STARTUP, LATEST, COORDINATOR) and old_pin['path'] == str(path))
    old = backend.helper.pinned(old_pin)
    backend.current_after_auth()
    backend.helper.write(root / (path.name + '.before'), old)
    temporary = path.with_name(path.name + '.maintenance-new')
    backend.helper.write(temporary, raw)
    require(backend.helper.pinned(old_pin) == old)
    backend.record('publish_' + path.name + '_intent')
    os.replace(temporary, path)
    backend.helper.sync(path.parent)
    require(backend.helper.public(path) == raw)
    if str(path) in backend.file_metadata:
        backend.file_metadata[str(path)] = backend.helper.metadata(path.lstat())
    backend.current_after_auth()
    backend.record('publish_' + path.name + '_done')


def wait_owner(backend, wait_seconds):
    require(type(wait_seconds) is int and 1 <= wait_seconds <= 300
            and backend.post_auth and backend.activation_access is None
            and 'sage-lantern-device-auth.service' in backend.stopped
            and 'sage-lantern-device-auth.service' in backend.running)
    from sage_lantern.device_access import access_epoch
    until = time.monotonic() + wait_seconds
    while True:
        require(time.monotonic() < until)
        observed = access_epoch()
        backend.current()
        if observed is not None and access_epoch() == observed:
            backend.activation_access = observed
            backend.current_after_auth()
            require(access_epoch() == observed and time.monotonic() < until)
            return
        time.sleep(0.5)


def run(backend, tools, *, approved_capture, wait_seconds):
    require(approved_capture is True and type(wait_seconds) is int and 1 <= wait_seconds <= 300)
    from sage_lantern.deployment_bringup import ColdBrokerBootstrap
    wait_owner(backend, wait_seconds)
    backend.reconcile_audio()
    helper = backend.helper
    root = ROOT / backend.voice_transition
    helper.parents(ROOT)
    if not ROOT.exists():
        ROOT.mkdir(mode=0o700)
        helper.sync(ROOT.parent)
    require(stat.S_ISDIR(ROOT.lstat().st_mode) and ROOT.lstat().st_uid == 0
            and stat.S_IMODE(ROOT.lstat().st_mode) == 0o700)
    root.mkdir(mode=0o700)
    helper.sync(ROOT)
    archive = root / 'archive'
    archive.mkdir(mode=0o700)
    helper.sync(root)
    owner = None
    attempted = False
    try:
        for path in (MARKER, CAPTURE, COMMISSION):
            preserve(backend, path, archive / path.name)
        for path in (VOICE_RUN, CONTROL, BOOTSTRAP):
            destination = RUN_ROOT / ('sage-lantern-maintained-' + backend.voice_transition + '-' + path.name)
            preserve(backend, path, destination, archive / path.name)
        destination = archive / 'supervisor'
        preserve(backend, helper.SUPERVISOR, destination)
        backend.tree_location = destination
        backend.tree = helper.tree(destination)
        for service in ('sage-lantern-voice-loop.service', 'sage-lantern-mesh-runtime.service'):
            value = backend.show(service)
            if value['ActiveState'] == 'failed':
                backend.record('reset_' + service + '_intent')
                backend.system._command(['reset-failed', service], backend.system.clock() + 5)
                backend.reset_services.add(service)
                backend.current_after_auth()
                backend.record('reset_' + service + '_done')
        release(backend, 'sage-lantern-voice-loop.service')
        observed = installed_audio(backend, ['--inspect'])
        require(observed['snapshot'] == backend.expected['capture_snapshot']
                and observed['input_sha256'] == helper.sha(helper.canonical(backend.expected['capture_snapshot'])))
        backend.record('capture_intent', dict(input_sha256=observed['input_sha256'], local_seconds=2, retain_audio=False))
        result = installed_audio(backend, ['--capture', '--expected-inputs-sha256', observed['input_sha256'],
                                          '--approve-microphone-capture'])
        require(result.get('audio_commissioned') is True and result.get('retained_audio') is False)
        installed_audio(backend, ['--admit'])
        backend.record('capture_done', result)
        release(backend, 'sage-lantern-mesh-runtime.service')
        require(helper.Recovery.model_ready(backend) == backend.model_pin
                and backend.collector.model_argv(backend.system, helper) == backend.expected['model_argv'])
        inventory = backend.expected['audio_inventory']
        owner = ColdBrokerBootstrap(inventory)
        owner.fresh()
        backend.reconcile_audio(prestart_owner=owner)
        backend.record('new_lifetime_start_intent')
        attempted = True
        owner.start()
        evidence = owner.active()
        require(evidence['vault_epoch'] == backend.expected['storage_epoch'])
        for service in ('sage-lantern-mesh-runtime.service', 'sage-lantern-voice-loop.service'):
            value = backend.show(service)
            process = backend.process(value)
            backend.restored_identity(service, value, process)
            backend.running[service] = dict(invocation=value['InvocationID'], process=process)
        backend.audio_owner = owner
        backend.audio_evidence = evidence
        backend.current_after_auth()
        operational(backend)
        lifetime = helper.public(BOOTSTRAP)
        coordinator = backend.audio_coordinator
        coordinator.lifetime(lifetime, inventory, backend.expected['boot_id'])
        require(helper.decode(lifetime)['evidence'] == evidence)
        commissioned = helper.decode(helper.public(COMMISSION))
        require(helper.Recovery.model_ready(backend) == backend.model_pin)
        baseline = dict(inventory=inventory, runtime_sources=backend.sources,
            files={name: backend.expected['files'][name] for name in backend.expected['model_files']},
            model_argv=backend.expected['model_argv'], commission_sha256=helper.sha(helper.canonical(commissioned)),
            actor=backend.expected['actor'])
        output = documents(root, tools, baseline, lifetime, helper)
        for name, raw in output.items():
            helper.write(root / name, raw)
        backend.current_after_auth()
        backend.record('new_lifetime_start_done', dict(lifetime_sha256=helper.sha(lifetime), evidence=evidence))
        for path, raw, key in ((COORDINATOR, helper.pinned(tools['coordinator']), 'coordinator'),
                              (STARTUP, output['startup.json'], 'config'),
                              (LATEST, helper.canonical(dict(path=str(root / 'new-lifetime.json'),
                                                            sha256=helper.sha(lifetime))), 'latest')):
            replace_public(backend, path, raw, backend.expected['startup_before'][key], root)
        for service in backend.expected['extra_services']:
            if service in backend.guarded:
                release(backend, service)
        backend.current_after_auth()
        operational(backend)
        backend.record('audio_and_next_boot_policy_complete', dict(lifetime_sha256=helper.sha(lifetime),
            historical_drain_verified=False, capture_retained=False, inference_verified=False))
        return dict(audio_activated=True, operational_ready_verified=True,
                    next_boot_policy_updated=True, inference_verified=False,
                    lifetime=dict(path=str(root / 'new-lifetime.json'), sha256=helper.sha(lifetime)))
    except BaseException:
        backend.retain_audio_failure(owner if attempted else None)
        raise
