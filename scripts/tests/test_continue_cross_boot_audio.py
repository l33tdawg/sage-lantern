import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import types

import pytest


ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load('continuation', ROOT / 'scripts/lantern/continue-cross-boot-audio.py')
HELPER = load('continuation_original', ROOT / 'scripts/lantern/recover-cross-boot-audio.py')
BOOT = '2701621b-86ee-4302-8a90-98f70cff9046'


def checkpoint():
    return dict(schema='sage.lantern.archive-complete-continuation.v1', boot_id=BOOT,
        access_epoch='a' * 32, original_files={name: 'b' * 64 for name in MODULE.FILES},
        coordinator_journal=dict(path='/var/lib/sage-lantern-startup/' + BOOT + '.jsonl', sha256='c' * 64))


@pytest.mark.parametrize('fault', [None, 'missing', 'extra', 'epoch', 'boot', 'digest', 'journal_path'])
def test_checkpoint_exact_schema(fault):
    value = checkpoint()
    if fault == 'missing':
        value['original_files'].pop('04-archive_done.json')
    elif fault == 'extra':
        value['original_files']['07-model_intent.json'] = 'c' * 64
    elif fault == 'epoch':
        value['access_epoch'] = None
    elif fault == 'boot':
        value['boot_id'] = 'not-boot'
    elif fault == 'digest':
        value['original_files']['expected.json'] = 'short'
    elif fault == 'journal_path':
        value['coordinator_journal']['path'] = '/foreign'
    if fault:
        with pytest.raises((ValueError, TypeError)):
            MODULE.validate_checkpoint(value)
    else:
        MODULE.validate_checkpoint(value)


@pytest.fixture
def original_files(tmp_path, monkeypatch):
    value = checkpoint()
    state = tmp_path / BOOT
    state.mkdir()
    (state / 'supervisor').mkdir()
    for name in ('expected.json', 'adapter-policy.json', 'prior-lifetime.json'):
        (state / name).write_text('{}')
    value['original_files']['expected.json'] = hashlib.sha256(b'{}').hexdigest()
    for index, phase in enumerate(MODULE.PHASES, 1):
        details = None
        if phase == 'retain_failure_guards_done':
            details = dict(guards_retained=True, containment_verified=False,
                           producer_outcome='unverified', historical_outcomes='unknown')
        (state / f'{index:02d}-{phase}.json').write_text(json.dumps(dict(phase=phase, details=details,
            expected_sha256=value['original_files']['expected.json'], historical_outcomes='unknown')))
    journal = tmp_path / 'coordinator.jsonl'
    journal.write_text('\n'.join(json.dumps(dict(phase=phase)) for phase in
        ('intent', 'prior_lifetime_verified', 'full_lifecycle_intent', 'full_lifecycle_outcome_unknown')))
    value['coordinator_journal'] = dict(path=str(journal), sha256=hashlib.sha256(journal.read_bytes()).hexdigest())
    for path in state.glob('*.json'):
        value['original_files'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.setattr(MODULE, 'read', lambda path: Path(path).read_bytes())
    return types.SimpleNamespace(ATTEMPTS=tmp_path), value, state


@pytest.mark.parametrize('fault', [None, 'changed_bytes', 'added_start', 'wrong_phase', 'wrong_binding', 'claimed_drain'])
def test_original_checkpoint_not_rewritten_or_broadly_adopted(original_files, fault):
    helper, value, state = original_files
    if fault == 'added_start':
        (state / '07-model_intent.json').write_text('{}')
    elif fault:
        path = state / ('06-retain_failure_guards_done.json' if fault == 'claimed_drain' else '04-archive_done.json')
        row = json.loads(path.read_bytes())
        if fault == 'wrong_phase':
            row['phase'] = 'model_done'
        elif fault == 'wrong_binding':
            row['expected_sha256'] = 'e' * 64
        elif fault == 'claimed_drain':
            row['details']['containment_verified'] = True
        else:
            row['extra'] = True
        path.write_text(json.dumps(row))
        if fault != 'changed_bytes':
            value['original_files'][path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    before = {path.name: path.read_bytes() for path in state.glob('*.json')}
    if fault:
        with pytest.raises(ValueError):
            MODULE.originals(helper, value)
    else:
        assert set(MODULE.originals(helper, value)) == MODULE.FILES
    assert {path.name: path.read_bytes() for path in state.glob('*.json')} == before


class Backend:
    def __init__(self, fault=None):
        self.events = []
        self.fault = fault
        self.owner = types.SimpleNamespace(fresh=lambda: self.action('fresh'), start=self.start,
                                           contain=lambda: self.action('contain') or True)
    def action(self, name):
        self.events.append(name)
        if name == self.fault:
            raise RuntimeError('synthetic checkpoint failure')
    def __getattr__(self, name):
        assert name not in ('archive', 'inhibit')
        return lambda: self.action(name)
    def start(self):
        self.action('broker_start')
        return dict(broker_active=True, voice_active=True)
    def completed(self):
        self.action('completed')
        return {'synthetic': True}


def transition(backend, record):
    return types.FunctionType(HELPER.transition.__code__, dict(HELPER.__dict__, execute=MODULE.remaining))(backend, record)


def test_only_remaining_steps_run_once():
    backend = Backend()
    phases = []
    transition(backend, lambda phase, details=None: phases.append(phase))
    assert phases == ['model_intent', 'model_done', 'release_intent', 'release_done', 'bootstrap_intent', 'bootstrap_done']
    assert backend.events == ['reconcile', 'model', 'current', 'reconcile', 'release', 'reconcile', 'fresh', 'broker_start', 'completed']


@pytest.mark.parametrize('fault', ['reconcile', 'model', 'current', 'release', 'fresh', 'broker_start', 'completed'])
def test_failure_retains_original_guards_and_contains_after_start(fault):
    backend = Backend(fault)
    with pytest.raises(RuntimeError):
        transition(backend, lambda *args: None)
    assert 'retain' in backend.events
    assert ('contain' in backend.events) == (fault in ('broker_start', 'completed'))
    assert backend.events.count('broker_start') <= 1


def test_completion_journal_failure_uses_original_containment():
    backend = Backend()
    def record(phase, details=None):
        if phase == 'bootstrap_done':
            raise OSError('disk')
    with pytest.raises(OSError):
        transition(backend, record)
    assert backend.events[-2:] == ['retain', 'contain']


def test_hash_mismatch_never_loads_source(monkeypatch):
    monkeypatch.setattr(MODULE, 'read', lambda path: b'raise AssertionError("unreviewed")')
    with pytest.raises(ValueError):
        MODULE.load(dict(path='/source', sha256='0' * 64))


@pytest.mark.parametrize('digest', [MODULE.ORIGINAL_HELPER_SHA, '5f96803ce6a8fc5014f77dff8bff7e4d0d0a94ac70a7eece16f27331cfcdba6a'])
def test_only_actual_v4_helper_can_be_reconstructed(monkeypatch, digest):
    calls = []
    monkeypatch.setattr(MODULE, 'load', lambda pin: calls.append(pin) or types.SimpleNamespace())
    policy = dict(helper=dict(path='/original-v4', sha256=digest))
    if digest == MODULE.ORIGINAL_HELPER_SHA:
        MODULE.original_helper(policy)
        assert len(calls) == 1
    else:
        with pytest.raises(ValueError):
            MODULE.original_helper(policy)
        assert calls == []


@pytest.mark.parametrize('apply,fault', [(False, None), (True, None), (True, 'epoch'),
    (True, 'evidence'), (True, 'old_pointer'), (True, 'missing_done')])
def test_latest_publication_separate_and_verified(tmp_path, monkeypatch, apply, fault):
    from sage_lantern import deployment_bringup, device_access
    checkpoint_value = checkpoint()
    state = tmp_path / BOOT
    state.mkdir()
    continuation = tmp_path / 'continuation'
    continuation.mkdir()
    journal_dir = tmp_path / 'startup'
    journal_dir.mkdir()
    boot_file = tmp_path / 'boot'
    boot_file.write_text(BOOT)
    original_path = MODULE.Path
    def mapped(path):
        return {'/var/lib/sage-lantern-startup': journal_dir,
                '/proc/sys/kernel/random/boot_id': boot_file}.get(str(path), original_path(path))
    monkeypatch.setattr(MODULE, 'Path', mapped)
    monkeypatch.setattr(MODULE, 'read', lambda path: Path(path).read_bytes())
    evidence = dict(broker_id='a' * 32, vault_epoch='b' * 64 + ':2', broker_pid=101, voice_pid=102)
    expected = dict(inventory={}, storage_epoch=evidence['vault_epoch'])
    raw = HELPER.canonical(checkpoint_value)
    (continuation / 'checkpoint.json').write_bytes(raw)
    (state / 'expected.json').write_bytes(HELPER.canonical(expected))
    for name in MODULE.FILES - {'expected.json'}:
        (state / name).write_bytes(b'{}')
    checkpoint_value['original_files'] = {name: HELPER.sha((state / name).read_bytes()) for name in MODULE.FILES}
    raw = HELPER.canonical(checkpoint_value)
    (continuation / 'checkpoint.json').write_bytes(raw)
    document = dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=BOOT,
                    pid=100, inventory={}, evidence=evidence)
    lifetime_raw = HELPER.canonical(document)
    (state / 'new-lifetime.json').write_bytes(lifetime_raw)
    lifetime = dict(boot_id=BOOT, broker=evidence, lifetime_sha256=HELPER.sha(lifetime_raw))
    (continuation / 'complete.json').write_bytes(HELPER.canonical(dict(
        schema='sage.lantern.archive-complete-continuation-result.v1', boot_id=BOOT,
        original_expected_sha256=checkpoint_value['original_files']['expected.json'],
        historical_outcomes='unknown', old_drain_verified=False, lifetime=lifetime)))
    (continuation / '06-bootstrap_done.json').write_bytes(HELPER.canonical(dict(phase='bootstrap_done',
        checkpoint_sha256=HELPER.sha(raw), historical_outcomes='unknown', details=dict(lifetime=lifetime))))
    latest = journal_dir / 'latest.json'
    previous = HELPER.canonical(dict(path=str(state / 'prior-lifetime.json'),
                                     sha256=checkpoint_value['original_files']['prior-lifetime.json']))
    latest.write_bytes(previous)
    system = types.SimpleNamespace(clock=lambda: 0, _no_job=lambda job: job == '',
        _show=lambda *args: dict(ActiveState='failed', MainPID='0', Job=''))
    owner = types.SimpleNamespace(inputs=lambda: None, active=lambda: evidence, system=system)
    monkeypatch.setattr(deployment_bringup, 'ColdBrokerBootstrap', lambda inventory: owner)
    monkeypatch.setattr(device_access, 'access_epoch', lambda: None if fault == 'epoch' else checkpoint_value['access_epoch'])
    def write(path, content):
        with path.open('xb') as stream:
            stream.write(content)
    helper = types.SimpleNamespace(ATTEMPTS=tmp_path, sha=HELPER.sha, canonical=HELPER.canonical,
                                   write=write, sync=lambda path: None)
    if fault == 'evidence':
        evidence = dict(evidence, broker_pid=999)
    elif fault == 'old_pointer':
        latest.write_bytes(b'changed')
    elif fault == 'missing_done':
        (continuation / '06-bootstrap_done.json').unlink()
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            MODULE.publish_latest(helper, checkpoint_value, raw, continuation, HELPER.sha(previous), apply)
        assert not (continuation / 'publish-latest-done.json').exists()
    else:
        result = MODULE.publish_latest(helper, checkpoint_value, raw, continuation, HELPER.sha(previous), apply)
        assert json.loads(latest.read_bytes()) == (result['lifetime'] if apply else json.loads(previous))
        assert (continuation / 'publish-latest-intent.json').exists() is apply
        if apply:
            assert (continuation / 'previous-latest.json').read_bytes() == previous


@pytest.mark.parametrize('fault', [None, 'epoch', 'coordinator_active', 'source_hash', 'completion_epoch'])
def test_reconstruction_rechecks_epoch_sources_and_coordinator(tmp_path, monkeypatch, fault):
    from sage_lantern import audio_readiness, deployment_bringup, device_access
    value = checkpoint()
    source = tmp_path / 'source'
    source.write_bytes(b'reviewed')
    receipt = dict(binding={'inputs': {}})
    expected = dict(schema='sage.lantern.cross-boot-audio.v1', boot_id=BOOT, tree={}, inventory={},
        runtime_sources={}, files={str(source): hashlib.sha256(b'reviewed').hexdigest()},
        commission_sha256=HELPER.sha(HELPER.canonical(receipt)), storage_epoch='e' * 64 + ':2',
        workflow_sha256='f' * 64, actor={}, historical_outcomes='unknown',
        old_boot_attribution='unverified', model_argv=['/reviewed/model'])
    monkeypatch.setattr(MODULE, 'originals', lambda *args: {'expected.json': HELPER.canonical(expected)})
    monkeypatch.setattr(audio_readiness, 'commissioned_receipt', lambda: receipt)
    epoch = [value['access_epoch']]
    monkeypatch.setattr(device_access, 'access_epoch', lambda: epoch[0])
    events = []
    evidence = dict(vault_epoch=expected['storage_epoch'])
    coordinator = dict(ActiveState='failed', MainPID='0', Job='')
    system = types.SimpleNamespace(clock=lambda: 0, _show=lambda *args: coordinator,
                                   _no_job=lambda job: job in ('', '0'))
    monkeypatch.setattr(deployment_bringup, 'ColdBrokerBootstrap', lambda inventory:
        types.SimpleNamespace(system=system, active=lambda: evidence))
    class Recovery:
        def verify_sources(self):
            events.append('verify_sources')
        def current(self):
            events.append('real_current')
        def completed(self):
            events.append('completed')
            return dict(broker=evidence)
    helper = types.SimpleNamespace(Recovery=Recovery, ATTEMPTS=tmp_path, canonical=HELPER.canonical,
        sha=HELPER.sha, metadata=HELPER.metadata, file_hash=lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        BROKER=HELPER.BROKER, VOICE=HELPER.VOICE, write=lambda *args: events.append('write_completion'))
    if fault == 'source_hash':
        source.write_bytes(b'drift')
        with pytest.raises(ValueError):
            MODULE.reconstruct(helper, value, tmp_path / 'new')
        assert 'real_current' not in events
        return
    backend = MODULE.reconstruct(helper, value, tmp_path / 'new')
    if fault == 'epoch':
        epoch[0] = 'b' * 32
    elif fault == 'coordinator_active':
        coordinator.update(ActiveState='active', MainPID='42')
    elif fault == 'completion_epoch':
        evidence['vault_epoch'] = 'e' * 64 + ':3'
    if fault:
        with pytest.raises(ValueError):
            backend.completed() if fault == 'completion_epoch' else backend.current()
        assert 'write_completion' not in events
    else:
        backend.current()
        backend.completed()
        assert events.count('real_current') == 2
        assert events[-1] == 'write_completion'
