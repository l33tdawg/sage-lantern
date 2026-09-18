import copy
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location('startup_adapter', Path(__file__).resolve().parents[1] /
                                            'lantern/startup-audio-adapter.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
BOOT = 'fa412850-b01f-4a1f-973b-a4c508f65417'
OLD = '31b687ff-dad6-483f-9369-c9e24ae09ca8'
EPOCH = 'a' * 64 + ':2'


def completed_prior():
    return dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=OLD,
                pid=100, inventory={}, evidence=dict(broker_id='a' * 32, vault_epoch=EPOCH,
                                                     broker_pid=101, voice_pid=102))


@pytest.mark.parametrize('field', MODULE.STATIC)
def test_static_inputs_cannot_be_observed_into_authority(field):
    baseline = {name: {} for name in MODULE.STATIC}
    expected = dict(baseline, boot_id=BOOT, historical_outcomes='unknown', old_boot_attribution='unverified')
    expected[field] = 'changed'
    with pytest.raises(ValueError):
        MODULE.validate_current(expected, baseline, BOOT)


@pytest.mark.parametrize('fault', [None, 'same_boot', 'broker', 'inventory', 'phase'])
def test_prior_is_exact_broker_binding_not_historical_drain(fault):
    prior = completed_prior()
    fence = dict(schema='sage.lantern.storage-supervisor.v1', phase='active', broker_id='a' * 32)
    if fault == 'same_boot':
        prior['boot_id'] = BOOT
    elif fault == 'broker':
        fence['broker_id'] = 'b' * 32
    elif fault == 'inventory':
        prior['inventory'] = {'foreign': 'value'}
    elif fault == 'phase':
        fence['phase'] = 'recovery_required'
    if fault:
        with pytest.raises(ValueError):
            MODULE.validate_prior(prior, fence, {'inventory': {}}, BOOT)
    else:
        MODULE.validate_prior(prior, fence, {'inventory': {}}, BOOT)


@pytest.mark.parametrize('apply,fault', [(False, None), (True, None), (True, 'receipt'),
    (True, 'revoked'), (True, 'collect_changed'), (True, 'transition'), (True, 'epoch_changed')])
def test_full_owner_path_and_failure_boundary(tmp_path, monkeypatch, apply, fault):
    events = []
    baseline = {name: {} for name in MODULE.STATIC}
    expected = dict(baseline, boot_id=BOOT, historical_outcomes='unknown',
                    old_boot_attribution='unverified', storage_epoch=EPOCH)
    prior = completed_prior()
    policy = {name: dict(path=name, sha256='unused') for name in ('baseline', 'helper', 'collector', 'runtime_wheel')}
    policy['runtime_wheel']['sha256'] = hashlib.sha256(b'wheel').hexdigest()
    canonical = lambda value: json.dumps(value, sort_keys=True).encode()
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: canonical(baseline if pin['path'] == 'baseline' else prior))
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'wheel')
    attempts = tmp_path / 'attempts'
    attempts.mkdir(mode=0o700)
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    original_lstat = Path.lstat
    def lstat(path):
        info = original_lstat(path)
        if path == attempts:
            return SimpleNamespace(st_mode=info.st_mode, st_uid=0)
        return info
    monkeypatch.setattr(Path, 'lstat', lstat)
    live = ['access']
    evidence = dict(broker_id='b' * 32, vault_epoch=EPOCH if fault != 'epoch_changed' else 'b' * 64 + ':3',
                    broker_pid=201, voice_pid=202)
    class Recovery:
        def __init__(self, value):
            self.state = attempts / BOOT
            self.owner = SimpleNamespace(active=lambda: evidence)
        def lock(self):
            events.append('lock')
        def reconcile(self):
            events.append('reconcile')
        def completed(self):
            events.append('completed')
            if fault == 'revoked':
                live[0] = None
            return dict(lifetime_sha256='digest', broker=evidence)
        def close(self):
            events.append('close')
    def write(path, raw):
        if fault == 'receipt' and path.name == 'startup-complete.json':
            raise OSError('disk')
        path.write_bytes(raw)
    def transition(backend, record):
        events.append('single_full_start')
        try:
            if fault == 'transition':
                raise RuntimeError('failed')
            backend.completed()
            record('bootstrap_done')
        except BaseException:
            events.append('contain')
            raise
    helper = SimpleNamespace(Recovery=Recovery, decode=json.loads, canonical=canonical,
        sha=lambda raw: hashlib.sha256(raw).hexdigest(), ATTEMPTS=attempts, SUPERVISOR=tmp_path,
        public=lambda path: canonical(dict(schema='sage.lantern.storage-supervisor.v1',
            phase='active', broker_id='a' * 32)), parents=lambda path: None, sync=lambda path: None,
        write=write, transition=transition)
    def collect(*args, retained_model_files):
        assert retained_model_files == baseline['files']
        value = copy.deepcopy(expected)
        if fault == 'collect_changed':
            value['files'] = {'unknown': 'digest'}
        return value
    collector = SimpleNamespace(runtime_sources=lambda *args: {}, collect=collect)
    if fault:
        with pytest.raises((ValueError, OSError, RuntimeError)):
            MODULE.run(helper, collector, policy, dict(path='prior', sha256='pin'), BOOT,
                       'access', apply, lambda: live[0])
        assert ('contain' in events) == (fault != 'collect_changed')
        assert not (attempts / BOOT / 'startup-complete.json').exists()
    else:
        result = MODULE.run(helper, collector, policy, dict(path='prior', sha256='pin'), BOOT,
                            'access', apply, lambda: live[0])
        assert result['services_started'] is apply
        assert events.count('single_full_start') == int(apply)
        if apply:
            assert result['old_drain_verified'] is False
            assert (attempts / BOOT / 'prior-lifetime.json').read_bytes() == canonical(prior)
        else:
            assert list(attempts.iterdir()) == []


def test_dependencies_remain_frozen():
    root = Path(__file__).resolve().parents[1] / 'lantern'
    for name, digest in [('recover-cross-boot-audio.py', MODULE.HELPER_SHA),
                         ('collect-cross-boot-audio-pins.py', MODULE.COLLECTOR_SHA)]:
        assert hashlib.sha256((root / name).read_bytes()).hexdigest() == digest


def test_hash_checked_before_exec(monkeypatch):
    monkeypatch.setattr(MODULE, 'trusted', lambda path: b'raise AssertionError("executed")')
    with pytest.raises(ValueError):
        MODULE.load(dict(path='/helper', sha256='0' * 64))


@pytest.mark.parametrize('field', ['schema', 'phase', 'boot_id', 'pid', 'inventory', 'evidence'])
def test_prior_rejects_each_missing_field(field):
    prior = completed_prior()
    del prior[field]
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field,value', [('schema', 'wrong'), ('pid', True), ('pid', 0), ('pid', '100'),
    ('extra', None), ('inventory', [])])
def test_prior_rejects_invalid_top_level(field, value):
    prior = completed_prior()
    prior[field] = value
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field,value', [('broker_id', 'short'), ('vault_epoch', True),
    ('vault_epoch', 'epoch'), ('vault_epoch', 'a' * 64 + ':0'),
    ('vault_epoch', 'a' * 64 + ':' + str(2**64)), ('broker_pid', True), ('voice_pid', '102'),
    ('broker_pid', 0), ('voice_pid', -1), ('extra', None)])
def test_prior_rejects_invalid_evidence(field, value):
    prior = completed_prior()
    prior['evidence'][field] = value
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('field', ['broker_id', 'vault_epoch', 'broker_pid', 'voice_pid'])
def test_prior_rejects_truncated_evidence(field):
    prior = completed_prior()
    del prior['evidence'][field]
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, {}, {}, BOOT)


@pytest.mark.parametrize('raw', [b'{"schema":"a","schema":"b"}',
    b'{"helper":{"path":"a","path":"b"}}'])
def test_policy_duplicate_keys_refused_before_loading(monkeypatch, raw):
    monkeypatch.setattr(MODULE.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(MODULE.sys, 'platform', 'linux')
    monkeypatch.setattr(MODULE.sys, 'prefix', '/opt/sage-lantern/venv')
    monkeypatch.setattr(MODULE.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(MODULE.signal, 'alarm', lambda *args: None)
    monkeypatch.setattr(MODULE, 'pinned', lambda pin: raw)
    def load(pin):
        pytest.fail('must not load dependency with ambiguous policy')
    monkeypatch.setattr(MODULE, 'load', load)
    with pytest.raises(ValueError):
        MODULE.main(['--policy', '/policy', '--policy-sha256', '0' * 64,
                     '--prior', '/prior', '--prior-sha256', '0' * 64,
                     '--boot-id', BOOT, '--access-epoch', 'access'])


PRIOR_PIN = dict(path='/var/lib/sage-lantern-cross-boot-audio/old/new-lifetime.json',
                 sha256='d' * 64)
FENCE = dict(schema='sage.lantern.storage-supervisor.v1', phase='active', broker_id='c' * 32)
FENCE_SHA = 'e' * 64
PATH = '/etc/sage-lantern/mesh-runtime.json'


def drifted():
    """A prior lifetime and live state that differ in exactly the two pinned ways."""
    prior = completed_prior()
    prior['inventory'] = {PATH: 'a' * 64}
    live = {PATH: 'b' * 64}
    fence = dict(FENCE)
    delta = {PATH: {'from': 'a' * 64, 'to': 'b' * 64}}
    return prior, fence, live, delta


def allowance_of(prior_pin=PRIOR_PIN, fence=FENCE, fence_sha256=FENCE_SHA, delta=None,
                 boot=BOOT, **extra):
    value = dict(schema='sage.lantern.startup-allowance.v1', boot_id=boot, prior=prior_pin,
                 fence=dict(sha256=fence_sha256, phase=fence['phase'], broker_id=fence['broker_id']),
                 inventory_delta=delta, historical_outcomes='unknown', authorized_by='owner-approved')
    value.update(extra)
    return value


def admits(prior, fence, live, allowance, fence_sha256=FENCE_SHA, prior_pin=PRIOR_PIN):
    MODULE.validate_prior(prior, fence, {'inventory': live}, BOOT, allowance,
                          fence_sha256=fence_sha256, prior_pin=prior_pin)


def test_allowance_admits_exactly_the_pinned_deviation():
    prior, fence, live, delta = drifted()
    admits(prior, fence, live, allowance_of(delta=delta))


def test_allowance_refuses_without_the_argument():
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        MODULE.validate_prior(prior, fence, {'inventory': live}, BOOT)


def test_allowance_refuses_an_unpinned_inventory_change():
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta={}))


def test_allowance_refuses_an_extra_inventory_delta():
    prior, fence, live, delta = drifted()
    prior['inventory']['/etc/sage-lantern/other.json'] = 'f' * 64
    live['/etc/sage-lantern/other.json'] = '0' * 64
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta))


def test_allowance_refuses_a_dead_pin():
    prior, fence, live, delta = drifted()
    live[PATH] = 'a' * 64
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta))


def test_allowance_binds_the_fence_broker_not_its_phase():
    """A clean stop between the review and the boot moves active -> stopped_verified.
    The reviewed deviation is the broker the fence names, so the phase may move and
    the reviewed digest is audit evidence rather than a binding."""
    prior, fence, live, delta = drifted()
    admits(prior, fence, live, allowance_of(delta=delta), fence_sha256='1' * 64)
    stopped = dict(fence, phase='stopped_verified')
    admits(prior, stopped, live, allowance_of(delta=delta, fence=fence))


def test_allowance_refuses_a_pinned_fence_phase_the_lifecycle_rejects():
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta,
               fence=dict(fence, phase='recovery_required')))
    with pytest.raises(ValueError):
        admits(prior, dict(fence, phase='drained'), live, allowance_of(delta=delta, fence=fence))


def test_allowance_refuses_a_foreign_broker():
    prior, fence, live, delta = drifted()
    foreign = dict(fence, broker_id='9' * 32)
    with pytest.raises(ValueError):
        admits(prior, foreign, live, allowance_of(delta=delta))


def test_allowance_refuses_a_foreign_boot():
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta,
               boot='5cbe6e0e-0000-4000-8000-000000000000'))


def test_allowance_may_name_the_prior_boot_it_excuses():
    """A release installed on one boot is spent by the next: the tolerance can
    name the prior lifetime's boot instead of the boot that consumes it."""
    prior, fence, live, delta = drifted()
    admits(prior, fence, live, allowance_of(delta=delta, boot=prior['boot_id']))


def test_allowance_refuses_a_foreign_prior():
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta),
               prior_pin=dict(PRIOR_PIN, sha256='2' * 64))


def test_allowance_refuses_when_nothing_actually_drifted():
    prior, fence, live, delta = drifted()
    live[PATH] = 'a' * 64
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta))


@pytest.mark.parametrize('bad', [dict(authorized_by=''), dict(historical_outcomes='drained'),
    dict(schema='sage.lantern.startup-allowance.v2'), dict(extra='field')])
def test_allowance_refuses_a_malformed_document(bad):
    prior, fence, live, delta = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta, **bad))


@pytest.mark.parametrize('delta', [
    {PATH: {'from': 'a' * 64}},
    {PATH: {'from': 'a' * 64, 'to': 'b' * 64, 'why': 'x'}},
    {PATH: {'from': 'nope', 'to': 'b' * 64}},
    {'relative/path': {'from': 'a' * 64, 'to': 'b' * 64}},
])
def test_allowance_refuses_a_malformed_delta(delta):
    prior, fence, live, _ = drifted()
    with pytest.raises(ValueError):
        admits(prior, fence, live, allowance_of(delta=delta))


def test_allowance_may_pin_a_fence_only_deviation():
    """A maintenance cold start changes the fence broker and no inventory file:
    the tolerance then names no delta, which is narrower than one that does."""
    prior, fence, live, _ = drifted()
    prior['inventory'] = dict(live)
    admits(prior, fence, live, allowance_of(delta={}))


def test_strict_prior_wins_even_when_an_allowance_is_supplied():
    """A configured allowance must not make an already-exact prior refuse."""
    prior = completed_prior()
    prior['inventory'] = {PATH: 'a' * 64}
    live = {PATH: 'a' * 64}
    fence = dict(FENCE, broker_id='a' * 32)
    exact = dict(PRIOR_PIN)
    allowance = dict(schema='sage.lantern.startup-allowance.v1', boot_id=BOOT, prior=exact,
                     fence=dict(sha256=FENCE_SHA, phase='active', broker_id='a' * 32),
                     inventory_delta={}, historical_outcomes='unknown', authorized_by='owner-approved')
    # The document is deliberately malformed (empty delta, unpinned fence). Strict
    # validation must return before it is ever consulted, so this call succeeds.
    admits(prior, fence, live, allowance, prior_pin=exact)
