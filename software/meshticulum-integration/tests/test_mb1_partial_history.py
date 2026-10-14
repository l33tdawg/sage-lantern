"""Source-only exact MB1 failed scope; actual expiry/native pins stay mandatory."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import gfsk_mb1_partial_history as history,retained_capture as retained,pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost,canonical
from lantern_meshticulum.bench.profiles import GFSK_BUNDLE_KIND,GFSK_MB1_BUNDLE_KIND
DATA=Path(__file__).with_name('fixtures')/'current3195-mb1-partial-metadata.json'
@pytest.fixture
def original():
    value=json.loads(DATA.read_bytes());return value,ParentPolicy.from_dict(value['policy'])

def test_declared_full_reservations_preserve_every_H35_byte(original):
    value,policy=original;rows=history.declared_partition(policy)
    assert len(retained.PAID_HISTORY)==len(rpc.OLD_DIGESTS)==43
    assert retained.PAID_HISTORY[:35]==history.H35_PREFIX and retained.PAID_HISTORY[34]=='73c1cda8c02939409d0b1e7e2a39be67'
    assert history.PARENT==retained.PAID_HISTORY[35] and rpc.GUARDED_PAID[history.PARENT]==policy.digest
    for unit,row in rows.items():
        added=row['declared_new_reservation']
        assert added['radio_attempts']==(0 if unit=='unit-b' else 2560)
        assert added['estimated_airtime_us']==(0 if unit=='unit-b' else 600000000)
        assert row['actual_consumption'] is None and row['refunds']==0
        assert Cost(**row['full_original_caps'])==Cost(**row['prior_caps']).plus(Cost(**added))

@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_original_MB1_context_remains_typed_and_separate_from_live_scope(original,unit):
    value,policy=original;owner=value['owners'][unit]
    context=history.original_context(owner,policy,unit=unit)
    assert context.transport is not None and owner['qualification_kind']==policy.qualification_kind==GFSK_MB1_BUNDLE_KIND and context.value['whole_bytes']==100000
    assert owner['mb1_bundle_scope_sha256']==history.ORIGINAL_SCOPE_SHA256 and 'gfsk_bundle_scope_sha256' not in owner
    failed=history.FAILED_STARTED_RUNS[unit]
    assert failed==([] if unit=='unit-b' else ['acb48986aeed5a9c0358312e4574a93e'])
    for run,outcome in value['outcomes'][unit].items():
        assert outcome['complete'] is (run not in failed) and outcome['disarmed_on_exit'] is True
        assert history._sha(outcome)==history.NATIVE_OUTCOME_SHA256[unit][run]

@pytest.mark.parametrize('fault',['legacy_kind','new_deadline','different_parent','discounted_caps'])
def test_original_policy_cannot_be_retyped_renewed_or_discounted(original,fault):
    value,policy=original
    if fault=='legacy_kind':policy=replace(policy,qualification_kind=GFSK_BUNDLE_KIND)
    elif fault=='new_deadline':policy=replace(policy,expires_epoch_us=policy.expires_epoch_us+1)
    elif fault=='different_parent':policy=replace(policy,parent_id='a'*32)
    else:policy=replace(policy,units=tuple(replace(u,limits=u.prior_debt) if u.unit=='unit-a' else u for u in policy.units))
    with pytest.raises(WireError):history.declared_partition(policy)

@pytest.mark.parametrize('fault',['old_json_scope','missing_descriptor','clock','runtime','history','profile'])
def test_original_owner_or_descriptor_change_refuses(original,fault):
    value,policy=original;owner=deepcopy(value['owners']['unit-a'])
    if fault=='old_json_scope':owner['gfsk_bundle_scope_sha256']=owner.pop('mb1_bundle_scope_sha256')
    elif fault=='missing_descriptor':owner['bundle_context'].pop('transport_descriptor_sha256')
    elif fault=='clock':owner['phase_end_epoch_us']+=1
    elif fault=='runtime':owner['runtime_sha256']='f'*64
    elif fault=='history':owner['history_receipt_sha256']='f'*64
    else:owner['rf_profile']['bit_rate_bps']=250000
    with pytest.raises(WireError):history.original_context(owner,policy,unit='unit-a')

def test_unknown_native_expiry_and_receipt_pins_cannot_admit_current_scope(monkeypatch):
    monkeypatch.setattr(history,'EXPIRY_COMPLETION_SHA256',None)
    monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(WireError,match='pending_3195'):history.require_final_bindings()


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_actual_expired_native_MB1_costs_and_typed_context_qualify(original,unit):
    value,policy=original;observed=value['native'][unit];row=value['history_rows'][unit]
    assert history.require_final_bindings() is True
    context=history.retained_context(row['owner'],policy,unit=unit,observation=observed)
    assert context.transport is not None
    rpc.qualify_mb1_partial_bundle_costs(unit,policy,observed,row)
    assert row['require_result_complete'] is False and row['retired_unstarted']==[]
    assert history.pending_next_roster()==retained.PAID_HISTORY[:36]


@pytest.mark.parametrize('fault',['kind','unit','epoch','mono','database','unused','actual_credit','failed_partition','raw_wire','descriptor'])
def test_exact_expired_MB1_scope_refuses_retyping_credit_or_missing_proof(original,fault):
    value,policy=original;observed=deepcopy(value['native']['unit-a']);row=deepcopy(value['history_rows']['unit-a'])
    if fault=='kind':policy=replace(policy,qualification_kind=GFSK_BUNDLE_KIND)
    elif fault=='unit':observed['unit']='unit-c'
    elif fault=='epoch':observed['observation_original_epoch_us']=history.ORIGINAL_PHASE_END-1
    elif fault=='mono':observed['observation_original_monotonic_us']=row['owner']['native_phase']['unit-a']['deadline_monotonic_us']-1
    elif fault=='database':row['database_sha256']='f'*64
    elif fault=='unused':row['conservative_unused_enrollment_caps']['radio_attempts']-=1
    elif fault=='actual_credit':row['actual_native_paid_caps']['radio_attempts']-=1
    elif fault=='failed_partition':row['failed_started_runs']=[]
    elif fault=='raw_wire':observed['legacy_runs'][0]['wire']['retired']=0
    else:row['owner']['bundle_context']['transport_descriptor_sha256']='f'*64
    with pytest.raises((WireError,RuntimeError)):rpc.qualify_mb1_partial_bundle_costs('unit-a',policy,observed,row)
