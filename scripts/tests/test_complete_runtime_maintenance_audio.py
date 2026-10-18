import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts/lantern' / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('maintenance_audio_tail', 'complete-runtime-maintenance-audio.py')
COORDINATOR = load('maintenance_audio_coordinator', 'startup-coordinator.py')
BOOT = '2701621b-86ee-4302-8a90-98f70cff9046'
TRANSITION = '3701621b-86ee-4302-8a90-98f70cff9046'
EPOCH = 'a' * 64 + ':9'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode()


def helper():
    def write(path, raw):
        with Path(path).open('xb') as stream:
            stream.write(raw)
    return types.SimpleNamespace(sha=sha, canonical=canonical, decode=json.loads, write=write,
        parents=lambda path: None, sync=lambda path: None,
        public=lambda path: Path(path).read_bytes(),
        pinned=lambda pin: checked(pin),
        metadata=lambda info: {key: getattr(info, key) for key in
            ('st_dev', 'st_ino', 'st_mode', 'st_uid', 'st_gid', 'st_nlink', 'st_size', 'st_mtime_ns', 'st_ctime_ns')})


def checked(pin):
    raw = Path(pin['path']).read_bytes()
    assert sha(raw) == pin['sha256']
    return raw


def test_candidate_snapshot_changes_only_actual_runtime_input_hashes():
    before = dict(unit_id='unit-a', inputs={'/site/sage_lantern/a.py': 'old', '/etc/audio.conf': 'config'},
                  hardware={'card': 2}, boot_id=BOOT, services={'native': 'same'})
    result = MODULE.predicted_capture(before, {'sage_lantern/a.py': b'new'}, '/site', sha)
    assert before['inputs']['/site/sage_lantern/a.py'] == 'old'
    assert result == dict(before, inputs={'/site/sage_lantern/a.py': sha(b'new'), '/etc/audio.conf': 'config'})
    with pytest.raises(ValueError):
        MODULE.predicted_capture(before, {'sage_lantern/extra.py': b'new'}, '/site', sha)


@pytest.mark.parametrize('path,accepted', [
    ('/var/lib/sage-lantern-cross-boot-audio/' + BOOT + '/new-lifetime.json', True),
    ('/var/lib/sage-lantern-maintained-audio/' + TRANSITION + '/new-lifetime.json', True),
    ('/var/lib/sage-lantern-maintained-audio/not-uuid/new-lifetime.json', False),
    ('/tmp/' + TRANSITION + '/new-lifetime.json', False),
    ('/var/lib/sage-lantern-maintained-audio/' + TRANSITION + '/other.json', False)])
def test_coordinator_accepts_only_exact_lifetime_namespaces(path, accepted):
    assert COORDINATOR.prior_path(path, BOOT) is accepted


@pytest.mark.parametrize('digest', [COORDINATOR.R7, COORDINATOR.RETRIEVAL_V1, COORDINATOR.COMPACT_PROMPT_V1, '0' * 64])
def test_coordinator_keeps_r7_but_rejects_unreviewed_wheels(monkeypatch, digest):
    calls = []
    monkeypatch.setattr(COORDINATOR, 'pinned', lambda pin: calls.append(pin))
    pin = dict(path='/retained/runtime.whl', sha256=digest)
    if digest == '0' * 64:
        with pytest.raises(RuntimeError):
            COORDINATOR.verify_wheel(pin)
        assert not calls
    else:
        COORDINATOR.verify_wheel(pin)
        assert calls == [pin]


def test_real_archive_preserves_bytes_modes_and_refuses_retry(tmp_path, monkeypatch):
    source = tmp_path / 'voice'
    source.mkdir(mode=0o750)
    (source / 'state.json').write_bytes(b'synthetic-private-fixture')
    (source / 'state.json').chmod(0o600)
    (source / 'dictation').mkdir(mode=0o700)
    monkeypatch.setattr(MODULE, 'VOICE_RUN', source)
    tool = helper()
    tool.SUPERVISOR = tmp_path / 'supervisor'
    calls = []
    backend = types.SimpleNamespace(helper=tool, expected={'services': {
        'sage-lantern-voice-loop.service': {'process': {'uid': os.getuid()}}}},
        current_after_auth=lambda: None, record=lambda *args: calls.append(args))
    moved, durable = tmp_path / 'moved', tmp_path / 'durable'
    MODULE.preserve(backend, source, moved, durable)
    assert not source.exists()
    assert (moved / 'state.json').read_bytes() == (durable / 'state.json').read_bytes() == b'synthetic-private-fixture'
    assert (moved / 'state.json').stat().st_mode & 0o777 == 0o600
    assert calls[0][0].endswith('_intent') and calls[-1][0].endswith('_done')
    with pytest.raises(FileNotFoundError):
        MODULE.preserve(backend, source, moved, durable)


def test_archive_rejects_symlink_without_reading_target(tmp_path, monkeypatch):
    source = tmp_path / 'voice'
    source.mkdir(mode=0o700)
    (source / 'escape').symlink_to('/not-read')
    monkeypatch.setattr(MODULE, 'VOICE_RUN', source)
    tool = helper()
    tool.SUPERVISOR = tmp_path / 'supervisor'
    with pytest.raises(ValueError):
        MODULE.records(source, os.getuid(), tool)


@pytest.fixture
def full_tail(tmp_path, monkeypatch):
    import sage_lantern.device_access as access
    import sage_lantern.deployment_bringup as bringup
    for name in ('ROOT', 'VOICE_RUN', 'CONTROL', 'BOOTSTRAP', 'COMMISSION', 'CAPTURE', 'MARKER',
                 'STARTUP', 'LATEST', 'COORDINATOR'):
        monkeypatch.setattr(MODULE, name, tmp_path / name.lower())
    monkeypatch.setattr(MODULE, 'RUN_ROOT', tmp_path)
    monkeypatch.setattr(access, 'access_epoch', lambda: 'f' * 32)
    real_lstat = Path.lstat
    def lstat(path):
        info = real_lstat(path)
        return types.SimpleNamespace(st_mode=info.st_mode, st_uid=0) if path == MODULE.ROOT else info
    monkeypatch.setattr(Path, 'lstat', lstat)
    tool = helper()
    tool.SUPERVISOR = tmp_path / 'supervisor'
    tool.tree = lambda path: {'retained': True}
    tool.Recovery = types.SimpleNamespace(model_ready=lambda backend: 'model-pin')
    evidence = dict(broker_id='b' * 32, vault_epoch=EPOCH, broker_pid=101, voice_pid=102)
    inventory = {'/etc/exact-voice.conf': 'c' * 64}
    lifetime = canonical(dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=BOOT,
        pid=100, inventory=inventory, evidence=evidence))
    for path in (MODULE.MARKER, MODULE.CAPTURE, MODULE.COMMISSION, MODULE.BOOTSTRAP,
                 MODULE.STARTUP, MODULE.LATEST, MODULE.COORDINATOR):
        path.write_bytes(b'old-record')
    calls = []
    backend = types.SimpleNamespace(helper=tool, voice_transition=TRANSITION, activation_access=None,
        guarded={'sage-lantern-voice-loop.service', 'sage-lantern-mesh-runtime.service'},
        post_auth=True, stopped={'sage-lantern-device-auth.service'},
        reset_services=set(), model_pin='model-pin', running={'sage-lantern-device-auth.service': {}}, file_metadata={}, audio_owner=None,
        current=lambda: calls.append('current'),
        current_after_auth=lambda: calls.append('current'), reconcile_audio=lambda **kwargs: calls.append('reconcile'),
        process=lambda value: dict(pid=101), restored_identity=lambda *args: None,
        retain_audio_failure=lambda owner: calls.append(('contained', owner is not None)),
        collector=types.SimpleNamespace(model_argv=lambda *args: ['model']),
        system=types.SimpleNamespace(clock=lambda: 0, _command=lambda *args: calls.append('systemctl')),
        expected=dict(boot_id=BOOT, capture_snapshot={'inputs': {}}, audio_inventory=inventory,
            storage_epoch=EPOCH, model_argv=['model'], model_files=['/model'], files={'/model': 'd' * 64},
            actor={'chain_id': 'chain', 'agent_id': 'actor'}, extra_services={}),
        sources={'/site/sage_lantern/a.py': 'e' * 64})
    backend.expected['startup_before'] = {key: dict(path=str(path), sha256=sha(path.read_bytes()))
        for key, path in (('config', MODULE.STARTUP), ('latest', MODULE.LATEST), ('coordinator', MODULE.COORDINATOR))}
    backend.show = lambda service: {'ActiveState': 'inactive', 'InvocationID': 'c' * 32}
    backend.record = lambda phase, details=None: calls.append(phase)
    backend.audio_coordinator = types.SimpleNamespace(lifetime=lambda *args: json.loads(args[0]))
    coordinator = tmp_path / 'candidate-coordinator.py'
    coordinator.write_bytes(b'new-coordinator')
    tools = {}
    for name in ('helper', 'collector', 'runtime_wheel', 'adapter'):
        source = tmp_path / ('staged-' + name)
        source.write_bytes(('pinned-' + name).encode())
        tools[name] = dict(path=str(source), sha256=sha(source.read_bytes()))
    tools['coordinator'] = dict(path=str(coordinator), sha256=sha(coordinator.read_bytes()))
    def preserve(backend, source, destination, durable_copy=None):
        calls.append(('preserve', source))
        if source.exists() and source.is_file():
            source.rename(destination)
    monkeypatch.setattr(MODULE, 'preserve', preserve)
    monkeypatch.setattr(MODULE, 'release', lambda backend, service: (calls.append(('release', service)), backend.guarded.remove(service)))
    def audio(backend, args):
        calls.append(tuple(args))
        if args == ['--inspect']:
            return dict(snapshot=backend.expected['capture_snapshot'], input_sha256=sha(canonical(backend.expected['capture_snapshot'])))
        if args[0] == '--capture':
            MODULE.COMMISSION.write_bytes(canonical({'binding': 'real-candidate-fixture'}))
            return dict(audio_commissioned=True, retained_audio=False)
    monkeypatch.setattr(MODULE, 'installed_audio', audio)
    monkeypatch.setattr(MODULE, 'operational', lambda backend: calls.append('live-ready-check'))
    class Owner:
        def __init__(self, supplied):
            assert supplied == inventory
        def fresh(self):
            assert ('--capture', '--expected-inputs-sha256', sha(canonical(backend.expected['capture_snapshot'])),
                    '--approve-microphone-capture') in calls
            calls.append('fresh')
        def start(self):
            calls.append('broker-owned-start')
            MODULE.BOOTSTRAP.write_bytes(lifetime)
        def active(self):
            return evidence
    monkeypatch.setattr(bringup, 'ColdBrokerBootstrap', Owner)
    def reconcile_audio(*, prestart_owner=None):
        assert 'broker-owned-start' not in calls
        if prestart_owner is None:
            assert backend.guarded == {'sage-lantern-voice-loop.service', 'sage-lantern-mesh-runtime.service'}
            assert 'capture_done' not in calls
            calls.append('guarded-admission')
        else:
            assert type(prestart_owner) is Owner and not backend.guarded
            assert 'capture_done' in calls and 'fresh' in calls
            calls.append('prestart-admission')
    backend.reconcile_audio = reconcile_audio
    return backend, tools, calls


def test_full_tail_orders_capture_bootstrap_and_next_boot_publication(full_tail):
    backend, tools, calls = full_tail
    result = MODULE.run(backend, tools, approved_capture=True, wait_seconds=10)
    assert result['audio_activated'] is True and result['inference_verified'] is False
    assert calls.index('capture_done') < calls.index('broker-owned-start') < calls.index('new_lifetime_start_done')
    assert calls.index('guarded-admission') < calls.index('capture_intent')
    assert calls.index('capture_done') < calls.index('prestart-admission') < calls.index('broker-owned-start')
    latest = json.loads(MODULE.LATEST.read_bytes())
    assert latest == result['lifetime']
    assert checked(latest) == MODULE.BOOTSTRAP.read_bytes()
    config = json.loads(MODULE.STARTUP.read_bytes())
    assert config['initial_lifetime'] == latest
    assert config['coordinator']['sha256'] == sha(MODULE.COORDINATOR.read_bytes())
    policy = json.loads(checked(config['policy']))
    for name in ('adapter', 'helper', 'collector', 'runtime_wheel'):
        pin = config[name] if name == 'adapter' else policy[name]
        assert Path(pin['path']).parent == Path(latest['path']).parent
        assert pin['path'] != tools[name]['path']
        assert checked(pin) == checked(tools[name])
    assert not any(isinstance(value, tuple) and value[0] == 'contained' for value in calls)


@pytest.mark.parametrize('phase', ['capture_intent', 'capture_done', 'new_lifetime_start_done',
                                 'publish_startup_intent', 'publish_latest_done'])
def test_tail_failures_contain_even_after_real_bootstrap_return(full_tail, phase):
    backend, tools, calls = full_tail
    original = backend.record
    def record(name, details=None):
        original(name, details)
        if name == phase:
            raise OSError('injected-journal-failure')
    backend.record = record
    with pytest.raises(OSError, match='injected-journal-failure'):
        MODULE.run(backend, tools, approved_capture=True, wait_seconds=10)
    assert calls[-1] == ('contained', 'broker-owned-start' in calls)
    if phase == 'capture_intent':
        assert not any(isinstance(value, tuple) and value[0] == '--capture' for value in calls)


def test_no_capture_without_explicit_approval(full_tail):
    backend, tools, calls = full_tail
    with pytest.raises(ValueError):
        MODULE.run(backend, tools, approved_capture=False, wait_seconds=10)
    assert calls == []


@pytest.mark.parametrize('fault', [None, 'revoked', 'storage'])
def test_unlock_interleaving_preserves_real_post_auth_checks(monkeypatch, fault):
    import sage_lantern.device_access as access
    maintenance = load('unlock_interleaving_maintenance', 'prepare-runtime-maintenance.py')
    backend = maintenance.Maintenance.__new__(maintenance.Maintenance)
    backend.post_auth = True
    backend.activation_access = None
    backend.stopped = {maintenance.AUTH}
    backend.running = {maintenance.AUTH: {}}
    live = {'epoch': None, 'checks': 0}
    monkeypatch.setattr(access, 'access_epoch', lambda: live['epoch'])
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    backend.locked = lambda: pytest.fail('unlock wait must not reimpose the restore-only lock gate')
    def current():
        live['checks'] += 1
        if fault == 'storage':
            raise ValueError('storage-changed')
        if live['checks'] == 1:
            live['epoch'] = 'new-owner-epoch'
        if backend.activation_access is not None:
            if fault == 'revoked':
                live['epoch'] = None
            maintenance.require(access.access_epoch() == backend.activation_access)
    backend.current = current
    if fault:
        with pytest.raises(ValueError):
            MODULE.wait_owner(backend, 10)
    else:
        MODULE.wait_owner(backend, 10)
        assert backend.activation_access == 'new-owner-epoch'
        assert live['checks'] == 3


def test_owner_wait_is_bounded_while_locked(monkeypatch):
    import sage_lantern.device_access as access
    backend = types.SimpleNamespace(post_auth=True, activation_access=None,
        stopped={'sage-lantern-device-auth.service'}, running={'sage-lantern-device-auth.service': {}},
        current=lambda: None)
    monkeypatch.setattr(access, 'access_epoch', lambda: None)
    ticks = iter((0, 0, 11))
    monkeypatch.setattr(MODULE.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: None)
    with pytest.raises(ValueError):
        MODULE.wait_owner(backend, 10)
    assert backend.activation_access is None


@pytest.mark.parametrize('returncode', [0, 2])
def test_operational_uses_installed_root_check_and_rejects_errors(monkeypatch, returncode):
    checks = []
    backend = types.SimpleNamespace(current_after_auth=lambda: checks.append('current'))
    def run(arguments, **kwargs):
        assert arguments[:4] == ['/opt/sage-lantern/venv/bin/python', '-I', '-B', '-c']
        assert 'operational_ready() is True' in arguments[4]
        assert kwargs['timeout'] == 5
        return types.SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(MODULE.subprocess, 'run', run)
    if returncode:
        with pytest.raises(ValueError):
            MODULE.operational(backend)
    else:
        MODULE.operational(backend)
    assert checks == ['current', 'current']


def test_operational_retry_does_not_mask_access_revocation(monkeypatch):
    calls = []
    def current():
        calls.append('current')
        if len(calls) == 2:
            raise ValueError('access-revoked')
    monkeypatch.setattr(MODULE.subprocess, 'run', lambda *args, **kwargs: types.SimpleNamespace(returncode=1))
    monkeypatch.setattr(MODULE.time, 'sleep', lambda seconds: pytest.fail('must not retry revoked access'))
    with pytest.raises(ValueError, match='access-revoked'):
        MODULE.operational(types.SimpleNamespace(current_after_auth=current))
