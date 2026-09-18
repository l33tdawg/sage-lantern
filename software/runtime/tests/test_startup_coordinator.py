import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('startup_tests', ROOT / 'scripts/lantern/startup-coordinator.py')
startup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(startup)
BOOT = '11111111-1111-4111-8111-111111111111'
PRIOR = '22222222-2222-4222-8222-222222222222'
EPOCH = 'a' * 32


def lifetime():
    return dict(schema='sage.lantern.voice-bootstrap.v1', phase='complete', boot_id=PRIOR,
        pid=123, inventory={'unit': 'a' * 64}, evidence=dict(broker_id='b' * 32,
        vault_epoch='c' * 64 + ':10', broker_pid=234, voice_pid=345))


def expected():
    return dict(schema='sage.lantern.cross-boot-audio.v1', boot_id=BOOT,
        inventory={'unit': 'a' * 64}, runtime_sources={'module': 'd' * 64}, files={'model': 'e' * 64},
        model_argv=['model'], commission_sha256='f' * 64, actor={'agent_id': 'owner'},
        historical_outcomes='unknown', old_boot_attribution='unverified', storage_epoch='c' * 64 + ':10',
        workflow_sha256='a' * 64, tree={})


def fence():
    return dict(schema='sage.lantern.storage-supervisor.v1', broker_id='b' * 32, phase='active')


def test_real_lifetime_schema_and_current_observer():
    document = lifetime()
    assert startup.lifetime(startup.canonical(document), document['inventory']) == document
    startup.validate_observation(expected(), expected(), BOOT, document, fence())


@pytest.mark.parametrize('field,value', [('phase', 'intent'), ('boot_id', 'unknown'), ('pid', True),
    ('evidence', {}), ('inventory', {})])
def test_lifetime_refuses_incomplete_or_fabricated(field, value):
    document = lifetime()
    document[field] = value
    with pytest.raises((RuntimeError, ValueError)):
        startup.lifetime(startup.canonical(document), {'unit': 'a' * 64})


@pytest.mark.parametrize('field', startup.STABLE)
def test_static_baseline_not_relearned(field):
    changed = expected()
    changed[field] = 'changed'
    with pytest.raises(RuntimeError):
        startup.validate_observation(changed, expected(), BOOT, lifetime(), fence())


def test_prior_broker_and_boot_required():
    changed = fence()
    changed['broker_id'] = 'f' * 32
    with pytest.raises(RuntimeError):
        startup.validate_observation(expected(), expected(), BOOT, lifetime(), changed)
    document = lifetime()
    document['boot_id'] = BOOT
    with pytest.raises(RuntimeError):
        startup.validate_observation(expected(), expected(), BOOT, document, fence())


class Backend:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def event(self, name):
        self.calls.append(name)
        if name == self.fail:
            raise RuntimeError(name)

    def check(self, epoch):
        self.event('check')

    def previous(self):
        self.event('previous')
        return {}, lifetime()

    def observe(self, epoch, prior):
        self.event('observe')
        return {}, expected()

    def apply(self, pin, epoch):
        self.event('apply')
        return {}

    def complete(self, result, value, epoch):
        self.event('complete')
        return {}

    def advance(self, pin):
        self.event('advance')


def test_single_full_owner():
    backend = Backend()
    startup.run_full_attempt(backend, SimpleNamespace(append=lambda *args: None), EPOCH)
    assert backend.calls == ['check', 'previous', 'apply', 'complete', 'advance']


@pytest.mark.parametrize('fail', ['check', 'previous', 'apply', 'complete', 'advance'])
def test_no_retry_or_pointer_promotion_on_failure(fail):
    backend = Backend(fail)
    def append(phase, details):
        if phase.endswith('unknown'):
            raise OSError('disk full')
    with pytest.raises(RuntimeError, match=fail):
        startup.run_full_attempt(backend, SimpleNamespace(append=append), EPOCH)
    assert backend.calls.count(fail) == 1
    if fail != 'advance':
        assert 'advance' not in backend.calls


def test_failed_intent_never_invokes_helper():
    backend = Backend()
    def append(phase, details):
        if phase == 'full_lifecycle_intent':
            raise OSError('disk full')
    with pytest.raises(OSError):
        startup.run_full_attempt(backend, SimpleNamespace(append=append), EPOCH)
    assert 'apply' not in backend.calls


def test_concrete_helper_cli(monkeypatch):
    backend = startup.FullLifecycle.__new__(startup.FullLifecycle)
    backend.config = {'adapter': {'path': '/adapter'}, 'policy': {'path': '/policy', 'sha256': 'f' * 64}}
    backend.boot = BOOT
    backend.check = lambda epoch: None
    calls = []
    backend.child = lambda *args: calls.append(args)
    monkeypatch.setattr(startup, 'pinned', lambda pin: b'{}')
    backend.apply({'path': '/expected', 'sha256': 'a' * 64}, EPOCH)
    assert calls == [(['/adapter', '--policy', '/policy', '--policy-sha256', 'f' * 64,
        '--prior', '/expected', '--prior-sha256', 'a' * 64, '--boot-id', BOOT,
        '--access-epoch', EPOCH, '--apply'], 'apply', 510)]


def test_allowance_reaches_the_adapter_only_when_configured(monkeypatch):
    backend = startup.FullLifecycle.__new__(startup.FullLifecycle)
    backend.config = {'adapter': {'path': '/adapter'}, 'policy': {'path': '/policy', 'sha256': 'f' * 64},
                      'allowance': {'path': '/allowance', 'sha256': 'b' * 64}}
    backend.boot = BOOT
    backend.check = lambda epoch: None
    calls = []
    backend.child = lambda *args: calls.append(args)
    monkeypatch.setattr(startup, 'pinned', lambda pin: b'{}')
    backend.apply({'path': '/expected', 'sha256': 'a' * 64}, EPOCH)
    assert calls == [(['/adapter', '--policy', '/policy', '--policy-sha256', 'f' * 64,
        '--prior', '/expected', '--prior-sha256', 'a' * 64, '--boot-id', BOOT,
        '--access-epoch', EPOCH, '--allowance', '/allowance', '--allowance-sha256', 'b' * 64,
        '--apply'], 'apply', 510)]


def test_completion_requires_durable_receipt_and_live_proof(monkeypatch):
    backend = startup.FullLifecycle.__new__(startup.FullLifecycle)
    backend.boot = BOOT
    backend.baseline = expected()
    backend.check = lambda epoch: None
    document = lifetime()
    document['boot_id'] = BOOT
    monkeypatch.setattr(startup, 'ColdBrokerBootstrap', lambda inventory: SimpleNamespace(
        inputs=lambda: None, active=lambda: document['evidence']))
    prior = {'path': '/prior', 'sha256': 'd' * 64}
    result = dict(schema='sage.lantern.startup-full.v1', boot_id=BOOT, access_epoch=EPOCH,
        storage_epoch=expected()['storage_epoch'], expected_sha256=startup.sha(startup.canonical(expected())),
        prior=prior, historical_outcomes='unknown', old_drain_verified=False, services_started=True,
        lifetime={'path': str(startup.ATTEMPTS / BOOT / 'new-lifetime.json'),
                  'sha256': startup.sha(startup.canonical(document))}, evidence=document['evidence'])
    monkeypatch.setattr(startup, 'read_public', lambda path: startup.canonical(result if path.name == 'startup-complete.json' else document))
    monkeypatch.setattr(startup, 'read_source', lambda path: startup.canonical(expected() if path.name == 'expected.json' else result))
    pin = backend.complete(result, prior, EPOCH)
    assert pin['sha256'] == startup.sha(startup.canonical(document))
    monkeypatch.setattr(startup, 'ColdBrokerBootstrap', lambda inventory: SimpleNamespace(
        inputs=lambda: None, active=lambda: {}))
    with pytest.raises(RuntimeError):
        backend.complete(result, prior, EPOCH)


def test_service_standalone_not_r7_module():
    text = (ROOT / 'software/systemd/sage-lantern-startup.service').read_text()
    assert '-I -B /opt/sage-lantern/libexec/startup-coordinator.py' in text
    assert 'Restart=no' in text and 'TimeoutStopSec=750' in text
    assert 'voice-loop' not in text and 'llm.service' not in text
    assert not (ROOT / 'software/runtime/src/sage_lantern/startup_coordinator.py').exists()


@pytest.mark.parametrize('timeout', [False, True])
def test_cooperative_cleanup_before_kill(timeout):
    calls = []
    def wait(**kwargs):
        calls.append(('wait', kwargs['timeout']))
        if timeout:
            raise startup.subprocess.TimeoutExpired('adapter', kwargs['timeout'])
    process = SimpleNamespace(terminate=lambda: calls.append('terminate'),
        kill=lambda: calls.append('kill'), wait=wait)
    startup.terminate_child(process)
    assert calls[:2] == ['terminate', ('wait', 190)]
    assert ('kill' in calls) is timeout


def test_parent_completion_failure_reports_active_unknown():
    backend = Backend('complete')
    records = []
    with pytest.raises(RuntimeError):
        startup.run_full_attempt(backend, SimpleNamespace(append=lambda *args: records.append(args)), EPOCH)
    phase, details = records[-1]
    assert phase == 'full_lifecycle_outcome_unknown'
    assert details['child_returned'] is True and details['active_or_unknown'] is True
    assert details['containment_verified'] is False and details['operator_recovery_required'] is True
    assert 'advance' not in backend.calls


def test_policy_schema_standalone_pins_only():
    pin = {'path': '/trusted', 'sha256': 'a' * 64}
    config = dict(schema='sage.lantern.startup-coordinator.v1', adapter=pin, policy=pin,
        initial_lifetime=pin, coordinator={'path': str(startup.COORDINATOR), 'sha256': 'b' * 64})
    startup.validate_full_config(config)
    config['helper'] = pin
    with pytest.raises(RuntimeError):
        startup.validate_full_config(config)


def test_allowance_is_an_optional_pinned_field():
    pin = {'path': '/trusted', 'sha256': 'a' * 64}
    config = dict(schema='sage.lantern.startup-coordinator.v1', adapter=pin, policy=pin,
        initial_lifetime=pin, coordinator={'path': str(startup.COORDINATOR), 'sha256': 'b' * 64})
    config['allowance'] = pin
    startup.validate_full_config(config)
    config['allowance'] = {'path': '/trusted', 'sha256': 'nope'}
    with pytest.raises(RuntimeError):
        startup.validate_full_config(config)
    config['allowance'] = {'path': '/trusted', 'sha256': 'a' * 64, 'extra': 'x'}
    with pytest.raises(RuntimeError):
        startup.validate_full_config(config)


def test_latest_lifetime_replaces_seed_each_boot(monkeypatch):
    backend = startup.FullLifecycle.__new__(startup.FullLifecycle)
    backend.boot = BOOT
    backend.baseline = expected()
    backend.config = {'initial_lifetime': {'path': '/obsolete', 'sha256': 'd' * 64}}
    pin = {'path': str(startup.ATTEMPTS / PRIOR / 'new-lifetime.json'), 'sha256': 'a' * 64}
    monkeypatch.setattr(startup.os.path, 'lexists', lambda path: True)
    monkeypatch.setattr(startup, 'read_public', lambda path: startup.canonical(pin))
    monkeypatch.setattr(startup, 'pinned', lambda received: startup.canonical(lifetime()) if received == pin else pytest.fail('stale seed'))
    assert backend.previous() == (pin, lifetime())


def test_prior_inventory_delta_needs_the_exact_pinned_allowance():
    document = lifetime()
    document['inventory'] = {'/etc/sage-lantern/mesh-runtime.json': 'a' * 64}
    raw = startup.canonical(document)
    live = {'/etc/sage-lantern/mesh-runtime.json': 'b' * 64}
    with pytest.raises(RuntimeError):
        startup.lifetime(raw, live)
    allowance = {'inventory_delta': {'/etc/sage-lantern/mesh-runtime.json':
                                     {'from': 'a' * 64, 'to': 'b' * 64}}}
    startup.lifetime(raw, live, allowance=allowance)
    with pytest.raises(RuntimeError):
        startup.lifetime(raw, live, allowance={'inventory_delta': {}})
    with pytest.raises(RuntimeError):
        startup.lifetime(raw, {'/etc/sage-lantern/other.json': 'b' * 64}, allowance=allowance)
    widened = dict(live, **{'/etc/sage-lantern/other.json': 'c' * 64})
    with pytest.raises(RuntimeError):
        startup.lifetime(raw, widened, allowance=allowance)


def test_strict_prior_wins_even_when_an_allowance_is_configured():
    document = lifetime()
    raw = startup.canonical(document)
    # An allowance that would refuse an empty delta must simply never be consulted.
    allowance = {'inventory_delta': {}}
    startup.lifetime(raw, document['inventory'])
    startup.lifetime(raw, document['inventory'], allowance=allowance)


class Clock:
    """Monotonic stand-in for the settle window; sleeping only advances it."""

    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def stability(values, window=3, interval=1, deadline=None, default=None):
    clock = Clock()
    def observed():
        index = int(clock.now)
        return values[index] if index < len(values) else default
    epoch = startup.stable_epoch(observed=observed, clock=clock, sleeper=clock.sleep,
                                 window=window, interval=interval, deadline=deadline)
    return epoch, clock.now


def test_stable_epoch_returns_only_a_held_activation():
    epoch, at = stability(['a' * 32] * 6)
    assert (epoch, at) == ('a' * 32, 3)


def test_stable_epoch_waits_out_a_transient_unlock():
    """An epoch that dies inside the window is never returned; the next held one is."""
    epoch, at = stability(['a' * 32, 'a' * 32, 'b' * 32, 'b' * 32, 'b' * 32, 'b' * 32])
    assert (epoch, at) == ('b' * 32, 5)


def test_stable_epoch_ignores_a_locked_gap_and_restarts_the_window():
    epoch, at = stability([None, None, 'c' * 32, 'c' * 32, 'c' * 32, 'c' * 32],
                          deadline=10)
    assert (epoch, at) == ('c' * 32, 5)


def test_stable_epoch_deadline_is_a_wait_timeout_not_a_refusal():
    with pytest.raises(TimeoutError):
        stability([None] * 20, deadline=2)
    with pytest.raises(TimeoutError):
        # A session that keeps restarting the window never satisfies the deadline.
        stability(['a' * 32, 'b' * 32, 'c' * 32, 'd' * 32], deadline=2)


def test_stable_epoch_window_outlasts_the_presence_lease():
    """The window has to be longer than a lapsed presence lease, or a transient
    unlock could still be adopted and the boot's only attempt burned mid-run."""
    assert startup.STABILITY_SECONDS >= 12
