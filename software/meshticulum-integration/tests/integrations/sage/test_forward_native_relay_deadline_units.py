"""Original integer expiry survives the relay's declared-seconds unit bridge."""
from dataclasses import replace
import math

import pytest

from meshticulum.contracts.identity import ContractError
from lantern_meshticulum.sage.forward_budget import ForwardBudgetError
from lantern_meshticulum.sage.forward_native_relay import ForwardNativeRelayError
from lantern_meshticulum.sage.forward_receive_journal import ForwardJournalError
from lantern_meshticulum.sage.mesh_binary_capture import MAX_TIME_US
from tests.integrations.sage.test_forward_native_relay import relay_rig, prepare_stage, DEBT, LIMITS
from tests.test_meshticulum_bench_runtime import Clock


ABOVE = 1049280873877
BELOW = 1049280873884
EXACT = 1049280873873
COARSE_ALIAS = 9007199254740989
DEADLINES = (
    pytest.param(ABOVE, id='roundtrip-above'),
    pytest.param(BELOW, id='roundtrip-below'),
    pytest.param(EXACT, id='roundtrip-exact'),
    pytest.param(COARSE_ALIAS, id='coarse-valid-alias'),
    pytest.param(MAX_TIME_US, id='maximum-native-time'),
)


def fresh_clock(monkeypatch, deadline):
    # A newly declared source clock before approval, with the original 100s
    # lifetime. Neither an already approved descriptor nor clock is normalized.
    monkeypatch.setattr(Clock, 'mono', deadline - 100000000)


def stage(r):
    return prepare_stage(r, r.sessions[0].start())


def untouched(r, incoming, outgoing):
    assert not incoming.opened and not outgoing.opened and not r.state.rows
    assert r.owner._retained_debt == DEBT and r.owner.limits == LIMITS
    assert r.accounting.actual is None


@pytest.mark.parametrize('deadline', DEADLINES)
def test_equal_original_native_deadline_is_admitted_across_declared_seconds(tmp_path, monkeypatch, deadline):
    fresh_clock(monkeypatch, deadline)
    declared = deadline / 1000000
    with relay_rig(tmp_path, description_deadline=declared) as r:
        assert r.intention.application_deadline_monotonic_us == deadline
        assert r.description.deadlines.application_deadline_monotonic == declared
        assert r.context.expires_epoch_us - r.context.issued_epoch_us == 100000000
        current, incoming, outgoing = stage(r)
        r.owner._validate_pair(current, (incoming.plan, outgoing.plan))
        assert incoming.plan.application_deadline_monotonic_us == deadline
        assert outgoing.plan.application_deadline_monotonic_us == deadline
        untouched(r, incoming, outgoing)


@pytest.mark.parametrize('deadline', DEADLINES)
def test_next_float_descriptor_above_original_is_refused_before_budget(tmp_path, monkeypatch, deadline):
    fresh_clock(monkeypatch, deadline)
    later = math.nextafter(deadline / 1000000, math.inf)
    assert later > deadline / 1000000
    with pytest.raises(ForwardNativeRelayError):
        with relay_rig(tmp_path, description_deadline=later):
            pytest.fail('later descriptor was admitted')


@pytest.mark.parametrize('deadline', DEADLINES[:-1])
def test_child_plus_one_microsecond_is_refused_by_original_integer_cap(tmp_path, monkeypatch, deadline):
    fresh_clock(monkeypatch, deadline)
    with relay_rig(tmp_path, description_deadline=deadline / 1000000) as r:
        current, incoming, outgoing = stage(r)
        late = replace(incoming.plan, application_deadline_monotonic_us=deadline + 1)
        assert late.application_deadline_monotonic_us <= MAX_TIME_US
        if deadline == COARSE_ALIAS:
            assert (deadline + 1) / 1000000 == deadline / 1000000
        with pytest.raises(ForwardNativeRelayError):
            r.owner._validate_pair(current, (late, outgoing.plan))
        untouched(r, incoming, outgoing)


@pytest.mark.parametrize('deadline', DEADLINES)
def test_tightened_descriptor_refuses_original_later_child_and_expires_exclusively(tmp_path, monkeypatch, deadline):
    fresh_clock(monkeypatch, deadline)
    tightened_us = deadline - 1000
    earlier = tightened_us / 1000000
    with relay_rig(tmp_path, description_deadline=earlier) as r:
        assert r.intention.application_deadline_monotonic_us == deadline
        current, incoming, outgoing = stage(r)
        with pytest.raises(ForwardNativeRelayError):
            r.owner._validate_pair(current, (incoming.plan, outgoing.plan))
        r.clock.mono = tightened_us
        assert r.clock()[1] == tightened_us < deadline
        with pytest.raises(ContractError):
            r.owner._sample()
        with pytest.raises(ContractError):
            r.description.deadlines.check(r.clock()[1] / 1000000, r.clock()[0] / 1000000)
        untouched(r, incoming, outgoing)


@pytest.mark.parametrize('deadline', DEADLINES)
@pytest.mark.parametrize('boundary', ('monotonic', 'epoch'))
def test_native_original_integer_expiry_excludes_exact_boundary(tmp_path, monkeypatch, deadline, boundary):
    fresh_clock(monkeypatch, deadline)
    with relay_rig(tmp_path, description_deadline=deadline / 1000000) as r:
        if boundary == 'monotonic':
            r.clock.mono = deadline - 1
            assert r.owner._sample()[1] == deadline - 1
            r.clock.mono = deadline
            assert r.clock.epoch < r.context.expires_epoch_us
        else:
            r.clock.epoch = r.context.expires_epoch_us - 1
            assert r.owner._sample()[0] == r.context.expires_epoch_us - 1
            r.clock.epoch = r.context.expires_epoch_us
            assert r.clock.mono < deadline
        with pytest.raises(ForwardNativeRelayError):
            r.owner._sample()
        assert r.owner._retained_debt == DEBT and r.accounting.actual is None


@pytest.mark.parametrize('target', ('intention', 'child'))
def test_native_application_time_maximum_is_not_widened(tmp_path, monkeypatch, target):
    assert MAX_TIME_US == 2 ** 53 - 1
    fresh_clock(monkeypatch, MAX_TIME_US)
    with relay_rig(tmp_path, description_deadline=MAX_TIME_US / 1000000) as r:
        current, incoming, outgoing = stage(r)
        value = r.intention if target == 'intention' else incoming.plan
        error = ForwardJournalError if target == 'intention' else ForwardBudgetError
        with pytest.raises(error):
            replace(value, application_deadline_monotonic_us=MAX_TIME_US + 1)
        untouched(r, incoming, outgoing)
