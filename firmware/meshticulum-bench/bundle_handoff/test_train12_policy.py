"""Exact v8/v9 admission bindings; synthetic local controls, no device work."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import campaign
import common
import controller
from meshticulum.bench import burst_protocol
from test_repeat_retry_policy import previous_configs, config as v7_config, LEGACY_CANONICAL_SHA256
import test_preamble_symbols as profile_fixture


def config(version=9):
    value=dict(v7_config(),selective_repair_train=True)
    if version==9:value['train_frames']=12
    return value


def selected(version=9):return common.selected_data_burst(config(version))


def prepared(version=9):
    return dict(comparison_mode='queued',data_burst=selected(version),policy=dict(
        operation_attempt_allowance=160,operation_estimated_airtime_allowance_us=8_000_000,
        receiver_operation_attempt_allowance=64,receiver_operation_estimated_airtime_allowance_us=3_000_000))


def test_all_seven_prior_canonical_policies_remain_byte_identical():
    configs=previous_configs()+[v7_config()]
    pins=LEGACY_CANONICAL_SHA256+('d9a2ae7e41d1888874dab35241611c0f8676d79f3d3b4c0bab4302dd6ef2b2c6',)
    for value,pin in zip(configs,pins):
        assert hashlib.sha256(common.canonical(common.selected_data_burst(value))).hexdigest()==pin
        assert controller.checked_data_burst(value)==common.selected_data_burst(value)
    assert controller.checked_data_burst(dict(comparison_mode='baseline')) is None


def test_v8_and_v9_add_only_the_exact_explicit_differences():
    assert selected(8)==dict(common.selected_data_burst(v7_config()),
        schema='meshticulum.data-burst-policy.v8',selective_repair_train=True)
    assert selected(9)==dict(selected(8),schema='meshticulum.data-burst-policy.v9',train_frames=12)
    for version in (8,9):
        assert selected(version)['max_depth']==8 and selected(version)['flow_window_frames']==32
        assert common.operation_limits(prepared(version))==dict(
            sender=dict(attempts=160,estimated_airtime_us=8_000_000),
            receiver=dict(attempts=64,estimated_airtime_us=3_000_000))


@pytest.mark.parametrize('value',[None,False,0,1,1.0,'true',{},[]])
def test_repair_train_requires_literal_true(value):
    with pytest.raises(ValueError):common.selected_data_burst(dict(config(),selective_repair_train=value))
    args={k:v for k,v in config().items() if k not in ('comparison_mode','train_depth')}
    args['selective_repair_train']=value
    with pytest.raises(ValueError):common.data_burst_policy(8,**args)


@pytest.mark.parametrize('value',[None,False,True,0,4,8,16,32,12.0,'12',{},[]])
def test_train_twelve_requires_exact_integer(value):
    with pytest.raises(ValueError):common.selected_data_burst(dict(config(),train_frames=value))
    args={k:v for k,v in config().items() if k not in ('comparison_mode','train_depth')}
    args['train_frames']=value
    with pytest.raises(ValueError):common.data_burst_policy(8,**args)


@pytest.mark.parametrize('version',[8,9])
@pytest.mark.parametrize('name',['train_depth','flow_window_frames','selective_repair_fill',
    'selective_terminal_retry','scalar_fill_pair','selective_repeat_retry'])
def test_no_implicit_earlier_prerequisite(version,name):
    value=config(version);value.pop(name)
    with pytest.raises(ValueError):common.selected_data_burst(value)


def test_train12_cannot_skip_explicit_repair_train():
    value=config();value.pop('selective_repair_train')
    with pytest.raises(ValueError):common.selected_data_burst(value)


@pytest.mark.parametrize('change',[dict(comparison_mode='baseline'),dict(train_depth=4),
    dict(train_depth=8.0),dict(flow_window_frames=32.0),dict(flow_window_frames=12),
    dict(selective_repair_fill=False),dict(selective_terminal_retry=1),
    dict(scalar_fill_pair=False),dict(selective_repeat_retry=1)])
def test_no_coercion_or_widened_earlier_prerequisite(change):
    with pytest.raises(ValueError):common.selected_data_burst(dict(config(),**change))


@pytest.mark.parametrize('version,api',[(8,'policy_for_repair_train'),(9,'policy_for_train12')])
def test_sdk_must_have_the_exact_selected_policy(version,api):
    helper=getattr(burst_protocol,api,None)
    if callable(helper):assert controller.checked_data_burst(config(version))==helper()==selected(version)
    else:
        with pytest.raises(ValueError,match='policy_unavailable'):controller.checked_data_burst(config(version))
    with patch.object(burst_protocol,api,None,create=True):
        with pytest.raises(ValueError,match='policy_unavailable'):controller.checked_data_burst(config(version))
        assert controller.checked_data_burst(v7_config())==common.selected_data_burst(v7_config())


@pytest.mark.parametrize('version,api',[(8,'policy_for_repair_train'),(9,'policy_for_train12')])
@pytest.mark.parametrize('change',[dict(train_frames=16),dict(max_depth=12),dict(max_hold_us=3_000_000),
    dict(reverse_listen_us=0),dict(selective_repair_train=1),dict(extra=True)])
def test_sdk_returned_mismatch_cannot_enter_prepare(version,api,change):
    with patch.object(burst_protocol,api,return_value=dict(selected(version),**change),create=True):
        with pytest.raises(ValueError,match='sdk_train_policy_mismatch'):controller.checked_data_burst(config(version))


@pytest.mark.parametrize('version,api',[(8,'policy_for_repair_train'),(9,'policy_for_train12')])
def test_missing_sdk_refuses_before_wheel_output_or_management(tmp_path,monkeypatch,version,api):
    value=dict(config(version),schema='meshticulum.alpha3-individual-input.v1',sender='unit-b',receiver='unit-a',
        profile_host=True,core=dict(path=str(tmp_path),commit='a'*40))
    source=tmp_path/'input.json';source.write_text(json.dumps(value));output=tmp_path/'uncreated'
    monkeypatch.setattr(controller,'sdk',lambda *a:None)
    monkeypatch.setitem(sys.modules,'radio_bundle',SimpleNamespace())
    monkeypatch.setitem(sys.modules,'radio_file',SimpleNamespace())
    monkeypatch.setattr(controller,'pinned',lambda *a:pytest.fail('evidence read'))
    monkeypatch.setattr(controller,'write_new',lambda *a,**k:pytest.fail('output write'))
    monkeypatch.setattr(controller,'rpc',lambda *a,**k:pytest.fail('management'))
    with patch.object(burst_protocol,api,None,create=True):
        with pytest.raises(ValueError,match='policy_unavailable'):controller.prepare(source,output)
    assert not output.exists()


@pytest.mark.parametrize('version',[8,9])
def test_scope_and_each_part_grant_bind_exact_policy_and_original_allowances(version):
    value=prepared(version);scope={k:deepcopy(value[k]) for k in ('comparison_mode','data_burst')}
    plan=dict(data_burst=selected(version),operation_limits=common.operation_limits(value))
    common.validate_mode_binding(value,scope);common.validate_plan_mode(plan,value)
    for other in previous_configs()+[v7_config(),config(17-version)]:
        policy=common.selected_data_burst(other)
        with pytest.raises(ValueError,match='scope_mode_changed'):common.validate_mode_binding(value,dict(scope,data_burst=policy))
        with pytest.raises(ValueError,match='grant_mode_changed'):common.validate_plan_mode(dict(plan,data_burst=policy),value)
        with pytest.raises(ValueError,match='scope_mode_changed'):common.validate_mode_binding(dict(value,data_burst=policy),scope)
    wider=deepcopy(plan);wider['operation_limits']['receiver']['attempts']+=1
    with pytest.raises(ValueError,match='operation_limit_changed'):common.validate_plan_mode(wider,value)


@pytest.mark.parametrize('change',[dict(train_frames=12.0),dict(max_depth=12),dict(flow_window_frames=12),
    dict(max_hold_us=2_000_001),dict(reverse_listen_us=99999),dict(inter_packet_rx_us=4999),
    dict(selective_repair_train=False),dict(selective_repeat_retry=False),dict(extra=True)])
def test_malformed_prepared_policy_never_authorizes_scope(change):
    value=prepared();value['data_burst'].update(change)
    with pytest.raises(ValueError,match='mode_policy_changed'):common.validate_mode_binding(value)


@pytest.mark.parametrize('preamble',[12,32])
@pytest.mark.parametrize('sync',[66,18])
def test_fixed_cold_claim_and_profile_are_independent_of_train_length(preamble,sync):
    costs=campaign.part_cost(24565,extra_data_attempts=31,receiver_ack_allowance=64,preamble_symbols=preamble)
    assert costs['data_frames']==128 and costs['sender']['attempts']==160 and costs['receiver']['attempts']==64
    assert costs['receiver_ack_allowance']==64 and costs['max_frames']==160
    expected={12:(6212641,5502400,835648,701440),32:(6437921,5707200,925760,783360)}[preamble]
    assert tuple(costs[r][k] for r in ('sender','receiver') for k in ('host_reserved_us','native_estimated_us'))==expected
    profile,admission=controller.sf5_radio_parameters(profile_fixture.POLICY,preamble_symbols=preamble,sync_word=sync)
    for version in (8,9):
        assert selected(version)['max_hold_us']==2_000_000
        assert common.operation_limits(prepared(version))['receiver']==dict(attempts=64,estimated_airtime_us=3_000_000)
        assert controller.sf5_radio_parameters(profile_fixture.POLICY,preamble_symbols=preamble,sync_word=sync)==(profile,admission)


@pytest.mark.skipif(not callable(getattr(burst_protocol,'policy_for_train12',None)),reason='new SDK only; old SDK refuses new selector')
@pytest.mark.parametrize('preamble',[12,32])
@pytest.mark.parametrize('sync',[66,18])
def test_actual_sdk_binds_distinct_peer_grants_with_same_finite_limits(tmp_path,preamble,sync):
    profile,admission=controller.sf5_radio_parameters(profile_fixture.POLICY,preamble_symbols=preamble,sync_word=sync)
    with ExitStack() as stack:
        pairs={}
        for version in (7,8,9):
            policy=common.selected_data_burst(v7_config()) if version==7 else selected(version)
            with patch.object(profile_fixture,'data_burst_policy',return_value=policy):
                pair=[profile_fixture.synthetic_authority(tmp_path/f'{version}-{unit}',unit,profile,admission)
                      for unit in ('unit-a','unit-b')]
            for authority in pair:stack.callback(authority.close)
            for role,authority in zip(('sender','receiver'),pair):
                assert authority.data_burst_policy==policy
                assert authority.deadline_us==181000000 and authority.carrier_profile.min_rto_seconds==2.22
                assert authority.contract.limits.window_bytes==6656 and authority.contract.limits.max_reassembly_frames==32
                expected=common.operation_limits(prepared())[role]
                assert authority.operation_limits.attempts==expected['attempts']
                assert authority.operation_limits.estimated_airtime_us==expected['estimated_airtime_us']
            assert pair[0].grant.digest==pair[1].grant.digest
            pairs[version]=pair[0]
        assert len({authority.grant.digest for authority in pairs.values()})==3
        assert pairs[7].contract.limits==pairs[8].contract.limits==pairs[9].contract.limits
