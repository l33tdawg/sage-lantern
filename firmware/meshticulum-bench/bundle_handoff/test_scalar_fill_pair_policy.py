"""Strict scalar-fill-pair admission binding; no devices or scope issuance."""
from copy import deepcopy
from unittest.mock import patch

import pytest

import common
import controller
from meshticulum.bench import burst_protocol


def config():
    return dict(comparison_mode='queued', train_depth=8, flow_window_frames=32,
                selective_repair_fill=True, selective_terminal_retry=True,
                scalar_fill_pair=True)


def previous_configs():
    return [dict(comparison_mode='queued'),
            dict(comparison_mode='queued', train_depth=8),
            dict(comparison_mode='queued', train_depth=8, flow_window_frames=32),
            dict(comparison_mode='queued', train_depth=8, flow_window_frames=32,
                 selective_repair_fill=True),
            {key: value for key, value in config().items() if key != 'scalar_fill_pair'}]


def selected():
    return common.selected_data_burst(config())


def prepared():
    return dict(comparison_mode='queued', data_burst=selected(), policy=dict(
        operation_attempt_allowance=160, operation_estimated_airtime_allowance_us=8_000_000,
        receiver_operation_attempt_allowance=64,
        receiver_operation_estimated_airtime_allowance_us=3_000_000))


def test_pair_option_adds_only_its_exact_schema_and_boolean_to_v5():
    previous = common.selected_data_burst(previous_configs()[-1])
    assert selected() == dict(previous, schema='meshticulum.data-burst-policy.v6',
                              scalar_fill_pair=True)
    assert common.operation_limits(prepared()) == dict(
        sender=dict(attempts=160, estimated_airtime_us=8_000_000),
        receiver=dict(attempts=64, estimated_airtime_us=3_000_000))


@pytest.mark.parametrize('value', [False, None, 0, 1, 'true', {}, []])
def test_pair_option_requires_literal_true_in_config_and_helper(value):
    with pytest.raises(ValueError, match='invalid_scalar_fill_pair'):
        common.selected_data_burst(dict(config(), scalar_fill_pair=value))
    with pytest.raises(ValueError, match='invalid_scalar_fill_pair'):
        common.data_burst_policy(8, flow_window_frames=32, selective_repair_fill=True,
                                 selective_terminal_retry=True, scalar_fill_pair=value)


@pytest.mark.parametrize('change', [
    lambda c: c.pop('train_depth'), lambda c: c.pop('flow_window_frames'),
    lambda c: c.pop('selective_repair_fill'), lambda c: c.pop('selective_terminal_retry'),
    lambda c: c.update(selective_repair_fill=False),
    lambda c: c.update(selective_terminal_retry=False),
    lambda c: c.update(selective_terminal_retry=1),
    lambda c: c.update(train_depth=4), lambda c: c.update(train_depth=8.0),
    lambda c: c.update(flow_window_frames=8), lambda c: c.update(flow_window_frames=32.0),
    lambda c: c.update(comparison_mode='baseline'),
])
def test_pair_option_requires_explicit_queued_eight_window32_terminal_fill(change):
    value = config()
    change(value)
    with pytest.raises(ValueError):
        common.selected_data_burst(value)


def test_direct_helper_cannot_skip_the_terminal_prerequisite():
    with pytest.raises(ValueError, match='scalar_fill_pair_requires_terminal_retry'):
        common.data_burst_policy(8, flow_window_frames=32, selective_repair_fill=True,
                                 scalar_fill_pair=True)


def test_installed_sdk_must_supply_the_exact_v6_bytes():
    selector = getattr(burst_protocol, 'policy_for_scalar_fill_pair', None)
    if callable(selector):
        assert controller.checked_data_burst(config()) == selector() == selected()
    else:
        with pytest.raises(ValueError, match='sdk_scalar_fill_pair_policy_unavailable'):
            controller.checked_data_burst(config())
    with patch.object(burst_protocol, 'policy_for_scalar_fill_pair', None, create=True):
        with pytest.raises(ValueError, match='sdk_scalar_fill_pair_policy_unavailable'):
            controller.checked_data_burst(config())
        for previous in previous_configs():
            assert controller.checked_data_burst(previous) == common.selected_data_burst(previous)
        assert controller.checked_data_burst(dict(comparison_mode='baseline')) is None
    with patch.object(burst_protocol, 'policy_for_scalar_fill_pair',
                      return_value=dict(selected(), scalar_fill_pair=1), create=True):
        with pytest.raises(ValueError, match='sdk_train_policy_mismatch'):
            controller.checked_data_burst(config())


def test_old_policy_scope_and_grant_cannot_substitute_for_pair_policy_in_either_direction():
    value = prepared()
    scope = {key: deepcopy(value[key]) for key in ('comparison_mode', 'data_burst')}
    plan = dict(data_burst=selected(), operation_limits=common.operation_limits(value))
    assert common.validate_mode_binding(value, scope) == 'queued'
    common.validate_plan_mode(plan, value)
    for previous in previous_configs():
        old = common.selected_data_burst(previous)
        with pytest.raises(ValueError, match='scope_mode_changed'):
            common.validate_mode_binding(value, dict(scope, data_burst=old))
        with pytest.raises(ValueError, match='grant_mode_changed'):
            common.validate_plan_mode(dict(plan, data_burst=old), value)
        with pytest.raises(ValueError, match='scope_mode_changed'):
            common.validate_mode_binding(dict(value, data_burst=old), scope)
        with pytest.raises(ValueError, match='grant_mode_changed'):
            common.validate_plan_mode(plan, dict(value, data_burst=old))
    widened = deepcopy(plan)
    widened['operation_limits']['receiver']['attempts'] += 1
    with pytest.raises(ValueError, match='operation_limit_changed'):
        common.validate_plan_mode(widened, value)


@pytest.mark.parametrize('change', [
    lambda p: p.pop('scalar_fill_pair'), lambda p: p.update(scalar_fill_pair=False),
    lambda p: p.update(scalar_fill_pair=1), lambda p: p.update(extra=True),
    lambda p: p.update(schema='meshticulum.data-burst-policy.v5'),
    lambda p: p.pop('selective_terminal_retry'), lambda p: p.update(selective_repair_fill=False),
    lambda p: p.update(flow_window_frames=8), lambda p: p.update(train_frames=32),
    lambda p: p.update(max_depth=32), lambda p: p.update(inter_packet_rx_us=0),
    lambda p: p.update(reverse_listen_us=0), lambda p: p.update(max_hold_us=4_000_000),
])
def test_partial_coerced_or_widened_v6_never_authorizes_a_scope(change):
    value = prepared()
    change(value['data_burst'])
    with pytest.raises(ValueError, match='mode_policy_changed'):
        common.validate_mode_binding(value)
