"""Strict v7 binding and legacy parity; no devices, issuance or live preparation."""
from copy import deepcopy
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import common
import controller
from meshticulum.bench import burst_protocol


def previous_configs():
    values=[dict(comparison_mode='queued'),dict(comparison_mode='queued',train_depth=8),
            dict(comparison_mode='queued',train_depth=8,flow_window_frames=32)]
    for name in ('selective_repair_fill','selective_terminal_retry','scalar_fill_pair'):
        values.append(dict(values[-1],**{name:True}))
    return values


def config():
    return dict(previous_configs()[-1],selective_repeat_retry=True)


def selected():
    return common.selected_data_burst(config())


def prepared():
    return dict(comparison_mode='queued',data_burst=selected(),policy=dict(
        operation_attempt_allowance=160,operation_estimated_airtime_allowance_us=8_000_000,
        receiver_operation_attempt_allowance=64,
        receiver_operation_estimated_airtime_allowance_us=3_000_000))


LEGACY_CANONICAL_SHA256 = ('706ac28f6a1afae55b0f447362630ee28661ce7e69405c4252aae5fae6bf40f4', '03d065b30f74080fdac969230529b01cebd9ad90219e6deaebd2c3530a4655f5', 'fe1b2f9d4b0529b087cc141ccfda2d6149e33292cc28101b3b6c674a4b278587', '3dee3bb9e373be4a7f177841252edd1b993970e50219b7a6893483a32dbc9282', 'ddfce35a4ffcc7f90141da9dffbb028b2b1817b2679b8b97e211e5768724957d', 'e1c06b42c5ef5c49b781627dd1e01390a5efab6508ffbb8bbf15c87dfbc8dc15')


def test_omitted_repeat_option_preserves_all_six_frozen_serialized_policies():
    for value,pin in zip(previous_configs(),LEGACY_CANONICAL_SHA256):
        policy=common.selected_data_burst(value)
        assert hashlib.sha256(common.canonical(policy)).hexdigest()==pin
        assert 'selective_repeat_retry' not in policy
    assert common.selected_data_burst(dict(comparison_mode='baseline')) is None


def test_v7_adds_only_its_schema_and_literal_flag_without_new_operation_allowance():
    old=common.selected_data_burst(previous_configs()[-1])
    assert selected()==dict(old,schema='meshticulum.data-burst-policy.v7',selective_repeat_retry=True)
    assert common.operation_limits(prepared())==dict(
        sender=dict(attempts=160,estimated_airtime_us=8_000_000),
        receiver=dict(attempts=64,estimated_airtime_us=3_000_000))


@pytest.mark.parametrize('value',[False,None,0,1,'true',{},[],1.0])
def test_repeat_requires_literal_true_in_config_and_direct_helper(value):
    with pytest.raises(ValueError,match='invalid_selective_repeat_retry'):
        common.selected_data_burst(dict(config(),selective_repeat_retry=value))
    with pytest.raises(ValueError,match='invalid_selective_repeat_retry'):
        common.data_burst_policy(8,flow_window_frames=32,selective_repair_fill=True,
            selective_terminal_retry=True,scalar_fill_pair=True,selective_repeat_retry=value)


@pytest.mark.parametrize('name',['train_depth','flow_window_frames','selective_repair_fill',
                                 'selective_terminal_retry','scalar_fill_pair'])
def test_repeat_cannot_infer_any_missing_v6_prerequisite(name):
    value=config();value.pop(name)
    with pytest.raises(ValueError):common.selected_data_burst(value)


@pytest.mark.parametrize('change',[
    lambda c:c.update(comparison_mode='baseline'),lambda c:c.update(train_depth=4),
    lambda c:c.update(train_depth=8.0),lambda c:c.update(flow_window_frames=8),
    lambda c:c.update(flow_window_frames=32.0),lambda c:c.update(selective_repair_fill=False),
    lambda c:c.update(selective_terminal_retry=False),lambda c:c.update(scalar_fill_pair=False),
    lambda c:c.update(selective_repair_fill=1),lambda c:c.update(selective_terminal_retry=1),
    lambda c:c.update(scalar_fill_pair=1)])
def test_repeat_cannot_widen_or_coerce_v6_prerequisites(change):
    value=config();change(value)
    with pytest.raises(ValueError):common.selected_data_burst(value)


def test_direct_helper_requires_existing_scalar_pair_policy():
    with pytest.raises(ValueError,match='repeat_retry_requires_scalar_fill_pair'):
        common.data_burst_policy(8,flow_window_frames=32,selective_repair_fill=True,
            selective_terminal_retry=True,selective_repeat_retry=True)


def test_current_sdk_must_supply_exact_v7_or_refuse_only_that_option():
    selector=getattr(burst_protocol,'policy_for_repeat_retry',None)
    if callable(selector):assert controller.checked_data_burst(config())==selector()==selected()
    else:
        with pytest.raises(ValueError,match='sdk_repeat_retry_policy_unavailable'):
            controller.checked_data_burst(config())
    with patch.object(burst_protocol,'policy_for_repeat_retry',None,create=True):
        with pytest.raises(ValueError,match='sdk_repeat_retry_policy_unavailable'):
            controller.checked_data_burst(config())
        for previous in previous_configs():
            assert controller.checked_data_burst(previous)==common.selected_data_burst(previous)
        assert controller.checked_data_burst(dict(comparison_mode='baseline')) is None


@pytest.mark.parametrize('kind',['old_policy','numeric_flag','missing_flag','extra_key','wrong_guard'])
def test_sdk_partial_or_coerced_v7_bytes_refuse(kind):
    answer=selected()
    if kind=='old_policy':answer=common.selected_data_burst(previous_configs()[-1])
    elif kind=='numeric_flag':answer['selective_repeat_retry']=1
    elif kind=='missing_flag':answer.pop('selective_repeat_retry')
    elif kind=='extra_key':answer['extra']=True
    elif kind=='wrong_guard':answer['reverse_listen_us']=0
    with patch.object(burst_protocol,'policy_for_repeat_retry',return_value=answer,create=True):
        with pytest.raises(ValueError,match='sdk_train_policy_mismatch'):
            controller.checked_data_burst(config())


def test_missing_sdk_selector_refuses_prepare_before_wheel_or_output_or_credentials(tmp_path,monkeypatch):
    value=dict(config(),schema='meshticulum.alpha3-individual-input.v1',sender='unit-b',receiver='unit-a',
               profile_host=True,core=dict(path=str(tmp_path),commit='a'*40))
    path=tmp_path/'input.json';path.write_text(json.dumps(value));output=tmp_path/'uncreated'
    monkeypatch.setattr(controller,'sdk',lambda *args:None)
    monkeypatch.setitem(sys.modules,'radio_bundle',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'radio_file',SimpleNamespace())
    monkeypatch.setattr(controller,'pinned',lambda *args:pytest.fail('wheel/evidence read after unavailable selector'))
    monkeypatch.setattr(controller,'write_new',lambda *args,**kw:pytest.fail('output before SDK qualification'))
    monkeypatch.setattr(controller,'rpc',lambda *args,**kw:pytest.fail('management action'))
    with patch.object(burst_protocol,'policy_for_repeat_retry',None,create=True):
        with pytest.raises(ValueError,match='sdk_repeat_retry_policy_unavailable'):
            controller.prepare(path,output)
    assert not output.exists()


def test_every_scope_and_part_grant_binds_exact_v7_with_legacy_downgrade_refusal():
    value=prepared();scope={key:deepcopy(value[key]) for key in ('comparison_mode','data_burst')}
    plan=dict(data_burst=selected(),operation_limits=common.operation_limits(value))
    common.validate_mode_binding(value,scope);common.validate_plan_mode(plan,value)
    for previous in previous_configs():
        old=common.selected_data_burst(previous)
        with pytest.raises(ValueError,match='scope_mode_changed'):
            common.validate_mode_binding(value,dict(scope,data_burst=old))
        with pytest.raises(ValueError,match='grant_mode_changed'):
            common.validate_plan_mode(dict(plan,data_burst=old),value)
        with pytest.raises(ValueError,match='scope_mode_changed'):
            common.validate_mode_binding(dict(value,data_burst=old),scope)
        with pytest.raises(ValueError,match='grant_mode_changed'):
            common.validate_plan_mode(plan,dict(value,data_burst=old))
    for role in ('sender','receiver'):
        wider=deepcopy(plan);wider['operation_limits'][role]['attempts']+=1
        with pytest.raises(ValueError,match='operation_limit_changed'):
            common.validate_plan_mode(wider,value)


@pytest.mark.parametrize('change',[
    lambda p:p.pop('selective_repeat_retry'),lambda p:p.update(selective_repeat_retry=False),
    lambda p:p.update(selective_repeat_retry=1),lambda p:p.update(extra=True),
    lambda p:p.update(schema='meshticulum.data-burst-policy.v6'),lambda p:p.pop('scalar_fill_pair'),
    lambda p:p.update(scalar_fill_pair=False),lambda p:p.pop('selective_terminal_retry'),
    lambda p:p.update(selective_repair_fill=False),lambda p:p.update(flow_window_frames=8),
    lambda p:p.update(train_frames=32),lambda p:p.update(max_depth=32),
    lambda p:p.update(inter_packet_rx_us=0),lambda p:p.update(reverse_listen_us=0),
    lambda p:p.update(max_hold_us=4_000_000)])
def test_partial_coerced_widened_v7_never_authorizes_prepared_scope(change):
    value=prepared();change(value['data_burst'])
    with pytest.raises(ValueError,match='mode_policy_changed'):
        common.validate_mode_binding(value)
