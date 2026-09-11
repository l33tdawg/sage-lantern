import json
from types import SimpleNamespace
import uuid

import pytest

from sage_lantern import audio_readiness as module
from sage_lantern import voice_listener_ack as ack_module
from test_voice_listener_ack import supervisor as supervisor_fixture


@pytest.fixture
def probe(monkeypatch):
    supervisor = supervisor_fixture.__wrapped__()
    supervisor._backend.uid = 994
    clock = [100.0]
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    cache = ack_module.ListenerAcknowledgements()
    cache._ack = (ack_module.binding(supervisor, pid=123, uid=994), 123, 994, 99.5)
    admission = []
    monkeypatch.setattr(module, 'admission_verified', lambda: admission.append(True) or True)
    request = dict(op='verify-audio-operational', nonce=str(uuid.uuid4()))
    def run(**kwargs):
        return module.operational_broker_request(supervisor, request, peer_uid=0, listener_ack=cache, **kwargs)
    return SimpleNamespace(supervisor=supervisor, clock=clock, cache=cache, request=request,
                           admission=admission, run=run)


@pytest.mark.parametrize('lock_name', ['_control', '_mutex'])
def test_default_diagnostics_no_retry_and_no_identity_data(probe, capsys, monkeypatch, lock_name):
    monkeypatch.setattr(module.time, 'sleep', lambda delay: pytest.fail('default retries disabled'))
    lock = getattr(probe.supervisor, lock_name)
    lock.acquire()
    try:
        with pytest.raises(ack_module.ListenerCheckBusy):
            probe.run()
    finally:
        lock.release()
    raw = capsys.readouterr().err
    value = json.loads(raw)
    assert set(value) == {'event', 'stage', 'outcome', 'attempts', 'elapsed_ms', 'busy', 'ack_status', 'ack_age_ms'}
    assert value['stage'] == 'retained_before' and value['outcome'] == 'unavailable'
    assert value['attempts'] == 1 and value['ack_age_ms'] == 500 and value['ack_status'] == 'fresh'
    assert value['busy'] == ('control' if lock_name == '_control' else 'state')
    assert probe.request['nonce'] not in raw and probe.supervisor.broker_id not in raw
    assert probe.supervisor._baseline not in raw
    assert len(probe.admission) == 1


@pytest.mark.parametrize('stage', ['admission_before', 'storage_epoch_before', 'vault_identity_before', 'producer_before'])
def test_non_contention_failure_never_retries_or_logs_exception_data(probe, monkeypatch, capsys, stage):
    def fail():
        raise RuntimeError('PRIVATE_EXCEPTION_CONTENT')
    if stage == 'admission_before':
        monkeypatch.setattr(module, 'admission_verified', fail)
    elif stage == 'storage_epoch_before':
        probe.supervisor._epoch = fail
    elif stage == 'vault_identity_before':
        probe.supervisor._backend.vault_identity = fail
    else:
        probe.supervisor._backend.producer = fail
    monkeypatch.setattr(module.time, 'sleep', lambda delay: pytest.fail('non-contention retry'))
    with pytest.raises(RuntimeError):
        probe.run(busy_retries=2)
    raw = capsys.readouterr().err
    assert 'PRIVATE_EXCEPTION_CONTENT' not in raw
    assert json.loads(raw)['stage'] == stage


def test_expired_ack_is_diagnostic_not_extended(probe, monkeypatch, capsys):
    original = probe.cache._ack
    probe.clock[0] = 103.0
    monkeypatch.setattr(module.time, 'sleep', lambda delay: pytest.fail('expiry retry'))
    with pytest.raises(RuntimeError):
        probe.run(busy_retries=2)
    value = json.loads(capsys.readouterr().err)
    assert value['stage'] == 'listener_before' and value['ack_status'] == 'expired'
    assert value['ack_age_ms'] == 3500 and probe.cache._ack == original


@pytest.mark.parametrize('lock_name', ['_control', '_mutex'])
def test_opt_in_retries_only_contention_and_repeats_checks(probe, monkeypatch, capsys, lock_name):
    lock = getattr(probe.supervisor, lock_name)
    lock.acquire()
    waits = []
    def wait(delay):
        waits.append(delay)
        lock.release()
        probe.clock[0] += delay
    monkeypatch.setattr(module.time, 'sleep', wait)
    try:
        assert probe.run(busy_retries=2)['result']['ready'] is True
    finally:
        if lock.locked():
            lock.release()
    assert waits == [0.01] and len(probe.admission) == 3
    value = json.loads(capsys.readouterr().err)
    assert value['outcome'] == 'verified_after_retry' and value['attempts'] == 2


def test_opt_in_contention_is_bounded(probe, monkeypatch, capsys):
    waits = []
    monkeypatch.setattr(module.time, 'sleep', lambda delay: waits.append(delay))
    probe.supervisor._control.acquire()
    try:
        with pytest.raises(ack_module.ListenerCheckBusy):
            probe.run(busy_retries=2)
    finally:
        probe.supervisor._control.release()
    assert waits == [0.01, 0.01]
    assert json.loads(capsys.readouterr().err)['attempts'] == 3


@pytest.mark.parametrize('change', ['phase', 'broker', 'storage', 'resource', 'producer', 'ack_expired', 'deadline'])
def test_retry_never_accepts_revoked_or_changed_observed_lifetime(probe, monkeypatch, change):
    original = ack_module.ListenerAcknowledgements.verified
    calls = []
    def verified(cache, supervisor, **kwargs):
        calls.append(True)
        if len(calls) == 2:
            raise ack_module.ListenerCheckBusy('state')
        return original(cache, supervisor, **kwargs)
    monkeypatch.setattr(ack_module.ListenerAcknowledgements, 'verified', verified)
    def wait(delay):
        if change == 'phase':
            probe.supervisor.phase = 'draining'
        elif change == 'broker':
            probe.supervisor.broker_id = 'c' * 32
        elif change == 'storage':
            probe.supervisor._baseline = 'new-epoch'
        elif change == 'resource':
            probe.supervisor._resource = object()
        elif change == 'producer':
            from dataclasses import replace
            probe.supervisor._producer = replace(probe.supervisor._producer, pid=456)
        elif change == 'ack_expired':
            probe.clock[0] = 103.0
        else:
            probe.clock[0] = 104.0
    monkeypatch.setattr(module.time, 'sleep', wait)
    with pytest.raises(RuntimeError):
        probe.run(busy_retries=2)


def test_insufficient_original_deadline_does_not_wait(probe, monkeypatch):
    def verified(*args, **kwargs):
        probe.clock[0] = 103.995
        raise ack_module.ListenerCheckBusy('control')
    monkeypatch.setattr(ack_module.ListenerAcknowledgements, 'verified', verified)
    monkeypatch.setattr(module.time, 'sleep', lambda delay: pytest.fail('wait past remaining budget'))
    with pytest.raises(ack_module.ListenerCheckBusy):
        probe.run(busy_retries=2)


def test_diagnostic_output_failure_does_not_change_denial(probe, monkeypatch):
    monkeypatch.setattr(module, 'admission_verified', lambda: False)
    monkeypatch.setattr(module.sys, 'stderr', SimpleNamespace(write=lambda raw: (_ for _ in ()).throw(OSError())))
    with pytest.raises(RuntimeError):
        probe.run()


def test_cache_diagnostic_never_waits_or_reveals_binding(probe):
    probe.cache._mutex.acquire()
    try:
        assert probe.cache.diagnostic() == {'ack_status': 'cache_busy', 'ack_age_ms': None}
    finally:
        probe.cache._mutex.release()
    assert probe.cache.diagnostic() == {'ack_status': 'fresh', 'ack_age_ms': 500}


@pytest.mark.parametrize('retries', [True, -1, 3, '2'])
def test_retry_configuration_is_not_request_control(probe, retries):
    with pytest.raises(RuntimeError):
        probe.run(busy_retries=retries)


def test_extra_request_field_cannot_enable_retry(probe):
    probe.request['busy_retries'] = 2
    with pytest.raises(RuntimeError):
        probe.run()
