"""Actual expired ea4d rows and metadata; no radio or current authority."""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import lora_endpoint_incomplete_history as history,pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,canonical,Cost


@pytest.fixture(scope='module')
def inputs():
    base=Path(os.environ['MESHTICULUM_H48_FIXTURE'])
    def load(name,path):
        spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module);return module
    old=load('H48_original_input_reader',base/'original_assemble_history44.py')
    prepared=load('H48_actual_inputs',base/'prepare_history48_inputs.py')
    return base,prepared.inputs(old.read,old.load,old.need,old.sha,old.canonical)


@pytest.fixture(params=('unit-a','unit-b','unit-c'))
def actual(request,inputs):
    base,values=inputs;unit=request.param;row=deepcopy(values['latest'][unit])
    return unit,row,ParentPolicy.from_dict(row['policy']),values


def test_actual_claims_start_and_whole_paid_reservation_are_separate(actual):
    unit,row,policy,values=actual;before=canonical(row)
    assert history.qualify_row(unit,policy,row)==row['paid_caps']
    assert rpc.retained_owner_view(row['owner'],policy,unit)is row['owner']
    assert canonical(row)==before and row['runs']==row['retired_unstarted']==[]
    assert row['endpoint_incomplete']['actual_claim_resource_prepared_start_counts']==history.NATIVE_COUNTS[unit]
    assert history.NATIVE_COUNTS[unit]['start_count']==(1 if unit=='unit-a'else 0)
    allocated=Cost()if unit=='unit-b'else Cost(256,60000000)
    unused=Cost()if unit=='unit-b'else Cost(768,180000000)
    assert Cost(**row['actual_native_paid_caps'])==Cost(**row['actual_controller_paid_caps'])==policy.unit(unit).prior_debt.plus(allocated)
    assert Cost(**row['actual_native_paid_caps']).plus(unused)==policy.unit(unit).limits
    assert row['native_controller_debt_views_may_be_summed']is row['original_cleanup_proved']is False
    assert row['endpoint_incomplete']['actual_cost']is row['failed_started_runs']is None
    assert row['endpoint_incomplete']['callback_terminal_proved']is row['endpoint_incomplete']['physical_quiescence_observed']is False
    old=values['histories'][unit]
    assert tuple(v['policy']['parent_id']for v in old['previous'])==history.H47_PREFIX
    assert old['previous'][-1]['actual_native_paid_caps']is None
    assert old['previous'][-1]['enrollment_incomplete']['actual_claim_resource_prepared_start_counts']is None
    assert history.verify_public_bytes(unit,policy,row,values['selected_public'][unit],old,values['observed'][unit]['native_snapshot'])


@pytest.mark.parametrize('damage',('paid','allocated','unused','summed','refund','terminal','start','schema',
    'scope','owner_flag','policy','prior','runs','identities','database','public_file'))
def test_actual_endpoint_debt_and_historical_binding_cannot_be_rewritten(actual,damage):
    unit,row,policy,values=actual
    if damage=='paid':row['paid_caps']['radio_attempts']-=1
    elif damage=='allocated':row['actual_native_paid_caps']['radio_attempts']+=1
    elif damage=='unused':row['conservative_unused_controller_caps']['radio_attempts']+=1
    elif damage=='summed':row['native_controller_debt_views_may_be_summed']=True
    elif damage=='refund':row['endpoint_incomplete']['no_refund']=False
    elif damage=='terminal':row['endpoint_incomplete']['callback_terminal_proved']=True
    elif damage=='start':row['endpoint_incomplete']['actual_claim_resource_prepared_start_counts']['start_count']+=1
    elif damage=='schema':row['owner']['schema']='meshticulum.private-resume-owner.v1'
    elif damage=='scope':row['owner']['comparison_scope_sha256']='f'*64
    elif damage=='owner_flag':row['owner']['full_source_green']=True
    elif damage=='policy':row['policy']['expires_epoch_us']+=1
    elif damage=='prior':row['prior_metadata']['full_caps']['radio_attempts']-=1
    elif damage=='runs':row['runs']=[{'run_id':history.RUN_ID}]
    elif damage=='identities':row['preparation_sha256']={history.RUN_ID:'f'*64}
    elif damage=='database':row['database_sha256']='f'*64
    else:row['public_metadata_sha256'].pop(next(iter(row['public_metadata_sha256'])))
    with pytest.raises((WireError,RuntimeError,KeyError)):
        history.qualify_row(unit,ParentPolicy.from_dict(row['policy']),row)


@pytest.mark.parametrize('damage',('claim','prepared','start','database','pending','complete','expired','write','projection','allocated'))
def test_fresh_native_projection_cannot_replace_actual_snapshot(actual,damage):
    unit,row,policy,values=actual;n=deepcopy(values['observed'][unit]['native_snapshot'])
    if damage in('claim','prepared','start'):n['detail'][damage+'_count']+=1
    elif damage=='database':n['expected_database_sha256']='f'*64
    elif damage=='pending':n['pending_unknown']=True
    elif damage=='complete':n['scoped_rows_complete']=False
    elif damage=='expired':n['policy_expired_at_observation']=False
    elif damage=='write':n['database_writes']=True
    elif damage=='projection':n['policy_row_sha256']='f'*64
    else:n['detail']['prior_plus_prepaid_caps']['radio_attempts']+=1
    with pytest.raises((WireError,RuntimeError,KeyError)):
        history.verify_public_bytes(unit,policy,row,values['selected_public'][unit],values['histories'][unit],n)


@pytest.mark.parametrize('damage',('public_bytes','public_missing','old_prefix'))
def test_actual_public_files_and_all47_predecessors_are_required(actual,damage):
    unit,row,policy,values=actual;raws=dict(values['selected_public'][unit]);old=deepcopy(values['histories'][unit])
    if damage=='public_bytes':
        key=next(iter(raws));raws[key]+=b'changed'
    elif damage=='public_missing':raws.pop(next(iter(raws)))
    else:old['previous'][-1]['actual_native_paid_caps']={k:0 for k in row['paid_caps']}
    with pytest.raises((WireError,RuntimeError,KeyError)):
        history.verify_public_bytes(unit,policy,row,raws,old,values['observed'][unit]['native_snapshot'])


def test_historical_owner_does_not_reinterpret_current_selector(actual,monkeypatch):
    from lantern_meshticulum.bench import mcu_artifact as mcu
    unit,row,policy,values=actual
    def forbid(*args,**kwargs):raise AssertionError('historical owner requested live authority')
    monkeypatch.setattr(mcu,'require_lora_selector_scope',forbid)
    monkeypatch.setattr(mcu,'validate_lora_artifact',forbid)
    assert history.owner_view(row['owner'],policy,unit=unit)is row['owner']
    monkeypatch.setattr(history,'NEXT_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(WireError,match='pending_ea4d_H48'):history.require_final_bindings()


def test_actual_root48_controllers_preserve47_prefix_and_separate_allocated_views(actual,inputs):
    base,values=inputs;unit,row,policy,_=actual;folder=base/'resume-history-preparation-48'
    receipt=json.loads((folder/'RECEIPT.json').read_bytes())
    assert hashlib.sha256((folder/'RECEIPT.json').read_bytes()).hexdigest()==history.NEXT_HISTORY_RECEIPT_SHA256
    assert receipt['local_controllers_reobserved']==len(receipt['controller_sha256'])==48
    observed=json.loads((folder/history.PARENT/'controller.json').read_bytes())
    assert history.controller_caps(unit,row,observed)==row['paid_caps']
    payload=json.loads((folder/(unit+'.json')).read_bytes())
    assert canonical(payload['previous'][:-1])==canonical(values['histories'][unit]['previous'])
    assert canonical(payload['legacy_inventory'])==canonical(values['histories'][unit]['legacy_inventory'])
    assert len(canonical(payload))<=2500000
    assert rpc.HISTORY==history.H47_PREFIX+(history.PARENT,) and len(rpc.OLD_DIGESTS)==48
    assert tuple(v['policy_digest']for v in payload['previous'])==rpc.OLD_DIGESTS


@pytest.mark.parametrize('damage',('allocation','limit','paid','full','expiry','pending'))
def test_fresh_controller_allocation_and_whole_limits_are_mandatory(actual,inputs,damage):
    base,values=inputs;unit,row,policy,_=actual
    observed=json.loads((base/'resume-history-preparation-48'/history.PARENT/'controller.json').read_bytes())
    if damage=='allocation':observed['detail']['allocations'][0]['allocation_id']='f'*64
    elif damage=='limit':observed['detail']['units'][0]['limit']['radio_attempts']-=1
    elif damage=='paid':observed['detail']['units'][0]['prior_plus_prepaid_caps']['radio_attempts']+=1
    elif damage=='full':observed['detail']['units'][0]['full_prepaid_caps']['radio_attempts']+=1
    elif damage=='expiry':observed['observation_original_epoch_us']=history.ORIGINAL_ABSOLUTE_END
    else:observed['pending_unknown']=True
    with pytest.raises((WireError,RuntimeError)):history.controller_caps(unit,row,observed)


def test_directory_listing_does_not_move_retained_reader_token(tmp_path):
    folder=tmp_path/'held';folder.mkdir(mode=0o700);path=folder/'public.json';path.write_bytes(b'public');path.chmod(0o600)
    with rpc.Reader()as reader:
        reader.directory(folder);fd=reader._nodes[folder][0];token=os.lseek(fd,0,os.SEEK_CUR)
        assert history._names(reader,folder)==['public.json']
        assert os.lseek(fd,0,os.SEEK_CUR)==token
        reader.check()
