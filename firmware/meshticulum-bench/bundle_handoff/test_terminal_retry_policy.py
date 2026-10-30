"""Exact policy binding for the terminal selective-retry experiment; no devices."""
from copy import deepcopy
from unittest.mock import patch

import pytest

import common
import controller
from meshticulum.bench import burst_protocol


def config():
    return dict(comparison_mode='queued',train_depth=8,flow_window_frames=32,
                selective_repair_fill=True,selective_terminal_retry=True)


def selected():
    return common.selected_data_burst(config())


def prepared():
    return dict(comparison_mode='queued',data_burst=selected(),policy=dict(
        operation_attempt_allowance=160,operation_estimated_airtime_allowance_us=8_000_000,
        receiver_operation_attempt_allowance=64,
        receiver_operation_estimated_airtime_allowance_us=3_000_000))


def test_exact_terminal_policy_changes_only_schema_and_terminal_option():
    previous=common.selected_data_burst({k:v for k,v in config().items() if k!='selective_terminal_retry'})
    expected=dict(previous,schema='meshticulum.data-burst-policy.v5',selective_terminal_retry=True)
    assert selected()==expected
    assert common.operation_limits(prepared())==dict(
        sender=dict(attempts=160,estimated_airtime_us=8_000_000),
        receiver=dict(attempts=64,estimated_airtime_us=3_000_000))


@pytest.mark.parametrize('value',[False,None,0,1,'true',{},[]])
def test_terminal_option_requires_literal_true(value):
    with pytest.raises(ValueError,match='invalid_selective_terminal_retry'):
        common.selected_data_burst(dict(config(),selective_terminal_retry=value))


@pytest.mark.parametrize('change',[
    lambda c:c.pop('train_depth'),lambda c:c.pop('flow_window_frames'),
    lambda c:c.pop('selective_repair_fill'),lambda c:c.update(selective_repair_fill=False),
    lambda c:c.update(train_depth=4),lambda c:c.update(flow_window_frames=8),
    lambda c:c.update(comparison_mode='baseline'),
])
def test_terminal_option_requires_explicit_queued_repair_fill(change):
    value=config();change(value)
    with pytest.raises(ValueError):common.selected_data_burst(value)


def test_sdk_support_is_required_and_exact_bytes_are_checked():
    selector=getattr(burst_protocol,'policy_for_terminal_retry',None)
    if callable(selector):
        assert controller.checked_data_burst(config())==selector()==selected()
    else:
        with pytest.raises(ValueError,match='sdk_terminal_retry_policy_unavailable'):
            controller.checked_data_burst(config())
    with patch.object(burst_protocol,'policy_for_terminal_retry',None,create=True):
        with pytest.raises(ValueError,match='sdk_terminal_retry_policy_unavailable'):
            controller.checked_data_burst(config())
    with patch.object(burst_protocol,'policy_for_terminal_retry',return_value=dict(selected(),max_depth=9),create=True):
        with pytest.raises(ValueError,match='sdk_train_policy_mismatch'):
            controller.checked_data_burst(config())


def test_scope_and_each_part_grant_bind_terminal_policy():
    value=prepared();scope={k:deepcopy(value[k]) for k in ('comparison_mode','data_burst')}
    plan=dict(data_burst=selected(),operation_limits=common.operation_limits(value))
    assert common.validate_mode_binding(value,scope)=='queued'
    common.validate_plan_mode(plan,value)
    previous=common.selected_data_burst({k:v for k,v in config().items() if k!='selective_terminal_retry'})
    for old in (common.data_burst_policy(4),common.data_burst_policy(8),
                common.data_burst_policy(8,flow_window_frames=32),previous):
        with pytest.raises(ValueError):common.validate_mode_binding(value,dict(scope,data_burst=old))
        with pytest.raises(ValueError):common.validate_plan_mode(dict(plan,data_burst=old),value)
        with pytest.raises(ValueError):common.validate_mode_binding(dict(value,data_burst=old),scope)
        with pytest.raises(ValueError):common.validate_plan_mode(plan,dict(value,data_burst=old))


@pytest.mark.parametrize('change',[
    lambda p:p.pop('selective_terminal_retry'),lambda p:p.update(selective_terminal_retry=False),
    lambda p:p.update(selective_terminal_retry=1),lambda p:p.update(extra=True),
    lambda p:p.update(schema='meshticulum.data-burst-policy.v4'),
    lambda p:p.update(flow_window_frames=8),lambda p:p.update(train_frames=32),
    lambda p:p.update(inter_packet_rx_us=0),lambda p:p.update(reverse_listen_us=0),
])
def test_partial_or_widened_policy_never_authorizes_scope(change):
    value=prepared();change(value['data_burst'])
    with pytest.raises(ValueError,match='mode_policy_changed'):common.validate_mode_binding(value)
