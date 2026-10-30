"""Explicit repair-fill authority binding; local only, no grants or devices."""
from copy import deepcopy
from unittest.mock import patch

import pytest

import common
import controller
from meshticulum.bench import burst_protocol


def config():
    return dict(comparison_mode='queued',train_depth=8,flow_window_frames=32,
                selective_repair_fill=True)


def selected():
    return common.selected_data_burst(config())


def prepared():
    return dict(comparison_mode='queued',data_burst=selected(),policy=dict(
        operation_attempt_allowance=160,operation_estimated_airtime_allowance_us=8_000_000,
        receiver_operation_attempt_allowance=64,
        receiver_operation_estimated_airtime_allowance_us=3_000_000))


def test_repair_fill_is_an_exact_policy_without_larger_native_or_role_limits():
    expected=common.data_burst_policy(8,flow_window_frames=32)
    expected.update(schema='meshticulum.data-burst-policy.v4',selective_repair_fill=True)
    assert selected()==expected
    assert common.operation_limits(prepared())==dict(sender=dict(attempts=160,
        estimated_airtime_us=8_000_000),receiver=dict(attempts=64,estimated_airtime_us=3_000_000))


@pytest.mark.parametrize('value',[None,False,0,1,'true',[],{}])
def test_explicit_option_does_not_coerce_false_or_other_types(value):
    with pytest.raises(ValueError,match='invalid_selective_repair_fill'):
        common.selected_data_burst(dict(config(),selective_repair_fill=value))


@pytest.mark.parametrize('change',[
    lambda c:c.pop('train_depth'),lambda c:c.pop('flow_window_frames'),
    lambda c:c.update(train_depth=4),lambda c:c.update(flow_window_frames=8),
    lambda c:c.update(flow_window_frames=32.0),lambda c:c.update(comparison_mode='baseline'),
])
def test_fill_requires_explicit_queued_eight_and_logical32(change):
    value=config();change(value)
    with pytest.raises(ValueError):common.selected_data_burst(value)


def test_old_policy_selection_is_unchanged_and_old_sdk_refuses_new_opt_in():
    for depth in (4,8):
        assert controller.checked_data_burst(dict(comparison_mode='queued',train_depth=depth))==burst_protocol.policy_for_depth(depth)
    window_selector=getattr(burst_protocol,'policy_for_flow_window',None)
    if window_selector is not None:
        assert controller.checked_data_burst(dict(comparison_mode='queued',train_depth=8,
            flow_window_frames=32))==window_selector(32)
    assert controller.checked_data_burst(dict(comparison_mode='baseline')) is None
    selector=getattr(burst_protocol,'policy_for_repair_fill',None)
    if selector is None:
        with pytest.raises(ValueError,match='sdk_repair_fill_policy_unavailable'):
            controller.checked_data_burst(config())
    else:
        assert controller.checked_data_burst(config())==selector()==selected()
    with patch.object(burst_protocol,'policy_for_repair_fill',None,create=True):
        with pytest.raises(ValueError,match='sdk_repair_fill_policy_unavailable'):
            controller.checked_data_burst(config())
    with patch.object(burst_protocol,'policy_for_repair_fill',return_value=common.data_burst_policy(8),create=True):
        with pytest.raises(ValueError,match='sdk_train_policy_mismatch'):
            controller.checked_data_burst(config())


def test_original_scope_and_each_part_grant_must_bind_the_same_fourth_policy():
    value=prepared();scope={k:deepcopy(value[k]) for k in ('comparison_mode','data_burst')}
    plan=dict(data_burst=selected(),operation_limits=common.operation_limits(value))
    assert common.validate_mode_binding(value,scope)=='queued'
    common.validate_plan_mode(plan,value)
    for legacy in (common.data_burst_policy(4),common.data_burst_policy(8),
                   common.data_burst_policy(8,flow_window_frames=32)):
        with pytest.raises(ValueError,match='scope_mode_changed'):
            common.validate_mode_binding(value,dict(scope,data_burst=legacy))
        with pytest.raises(ValueError,match='grant_mode_changed'):
            common.validate_plan_mode(dict(plan,data_burst=legacy),value)
        with pytest.raises(ValueError,match='scope_mode_changed'):
            common.validate_mode_binding(dict(value,data_burst=legacy),scope)
        with pytest.raises(ValueError,match='grant_mode_changed'):
            common.validate_plan_mode(plan,dict(value,data_burst=legacy))
    changed=deepcopy(plan);changed['operation_limits']['receiver']['attempts']+=1
    with pytest.raises(ValueError,match='operation_limit_changed'):
        common.validate_plan_mode(changed,value)


@pytest.mark.parametrize('change',[
    lambda p:p.pop('selective_repair_fill'),lambda p:p.update(selective_repair_fill=False),
    lambda p:p.update(selective_repair_fill=1),lambda p:p.update(extra=True),
    lambda p:p.update(schema='meshticulum.data-burst-policy.v3'),
    lambda p:p.update(flow_window_frames=8),lambda p:p.update(train_frames=32),
    lambda p:p.update(reverse_listen_us=0),lambda p:p.update(max_hold_us=4_000_000),
])
def test_partial_or_widened_fourth_policy_never_authorizes_a_scope(change):
    value=prepared();change(value['data_burst'])
    with pytest.raises(ValueError,match='mode_policy_changed'):
        common.validate_mode_binding(value)
