"""Actual public metadata replay through genuine SQLite parser; no admission/RF.

NativeCaps creates the local schema. Captured enrollment/claim rows are replayed
in memory as source fixtures, not claimed to be newly authorized native rows.
"""
from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,pilot_parent_snapshot as snap,retained_capture as retained
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,hashed
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence,FenceError

DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('current7746-accepted-part-metadata.json').read_bytes())
OWNER_PHASE=json.loads(Path(__file__).with_name('fixtures').joinpath('current7746-accepted-part-owner-phase.json').read_bytes())

@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def typed_rows(tmp_path,request,monkeypatch):
    unit=request.param;policy=ParentPolicy.from_dict(DATA['policy']);local=policy.unit(unit);actual=DATA['native'][unit]
    # Only schema is freshly enrolled here, under explicit modeled facts/approval.
    caps=NativeCaps.enroll(tmp_path/'native.sqlite',policy,unit=unit,
        clock=lambda:(policy.issued_epoch_us+1000,1000000000),facts=lambda:NativeFacts(unit,local.host_boot_id,local.peer),
        approve=lambda _:True,prior_check=lambda _:True,history_binding_sha256='e'*64)
    assert caps.close()
    image=bytearray((tmp_path/'native.sqlite').read_bytes());image[18]=image[19]=1
    db=sqlite3.connect(':memory:');db.deserialize(bytes(image))
    e=actual['detail']['enrollment'];enrollment=dict(schema='meshticulum.native-parent-enrollment.v2',policy=DATA['policy'],
        **{k:e[k] for k in ('unit','host_boot_id','original_epoch_us','original_monotonic_us','deadline_monotonic_us','history_binding_sha256')})
    db.execute('UPDATE enrollment SET body=? WHERE id=1',(rpc.canonical(enrollment),))
    sc=actual['detail']['stored_clock'];db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1',(sc['epoch_us'],sc['monotonic_us']))
    resources={}
    for row in actual['detail']['resources']:
        body={k:v for k,v in row.items() if k not in ('resource_id','row_sha256')};resources[row['resource_id']]=body
        assert rpc.sha(rpc.canonical(body))==row['row_sha256']
        db.execute('INSERT INTO resources VALUES(?,?)',(row['resource_id'],rpc.canonical(body)))
    for row in actual['detail']['claims']:
        run=row['child']['run_id'];receipt=DATA['prepared'][unit][run]
        body=dict(child=receipt['child_caps'],controller_allocation_id=receipt['controller_allocation_id'],resource_id=row['resource_id'],resource=resources[row['resource_id']])
        assert hashed(body)==row['native_claim_id'] and rpc.sha(rpc.canonical(body))==row['row_sha256']
        db.execute('INSERT INTO claims VALUES(?,?,?)',(row['native_claim_id'],rpc.canonical(body),run))
        db.execute('INSERT INTO prepared VALUES(?,?)',(row['native_claim_id'],rpc.canonical(receipt)))
        db.execute('INSERT INTO starts VALUES(?,?)',(row['native_claim_id'],rpc.canonical(DATA['outcomes'][unit][run]['parent'])))
    db.commit();db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    class Reader:
        name='native.sqlite'
        def read(self,name,maximum):assert name=='native.sqlite.approval';return (hashed(enrollment)+'\n').encode()
    scope=SimpleNamespace(parent_id=policy.parent_id,policy_digest=policy.digest,unit=unit)
    snap._schema(db,'native');parsed,detail,_=snap._native(db,scope,Reader());db.close()
    assert detail==actual['detail']
    observed=deepcopy(actual);observed.update(detail=detail,policy_expired_at_observation=True,
        observation_original_epoch_us=OWNER_PHASE[unit]['phase_end_epoch_us']+1,
        observation_original_monotonic_us=OWNER_PHASE[unit]['native_phase'][unit]['deadline_monotonic_us']+1)
    runs=[dict(run_id=run) for run in DATA['outcomes'][unit]]
    value=dict(owner=dict(OWNER_PHASE[unit],history_binding_sha256=e['history_binding_sha256']),database_sha256=actual['expected_database_sha256'],
        actual_native_paid_caps=detail['prior_plus_prepaid_caps'],actual_controller_paid_caps=detail['prior_plus_prepaid_caps'],
        conservative_unused_enrollment_caps=asdict(Cost() if unit=='unit-b' else Cost(2048,480000000)),
        paid_caps=asdict(local.limits),runs=runs,retired_unstarted=[],controller_endpoint_refs=[dict(child=dict(run_id=x['run_id'])) for x in runs],
        preparation_sha256={x['run_id']:'e'*64 for x in runs},legacy_sha256=None if not runs else rpc.sha(rpc.canonical(observed['legacy_runs'])),
        require_result_complete=True,failed_started_runs=[],accepted_part=deepcopy(DATA['accepted_part']))
    return unit,parsed,observed,value


def test_actual_typed_partial_rows_keep_full_unused_bound_once(typed_rows):
    unit,p,o,v=typed_rows
    rpc.qualify_history_claims(p,unit,o,v['runs'],[])
    rpc.qualify_partial_bundle_costs(unit,p,o,v)
    assert o['detail']['full_prepaid_caps']==asdict(Cost() if unit=='unit-b' else Cost(512,120000000))
    assert v['paid_caps']==asdict(p.unit(unit).limits)
    assert v['paid_caps']!=v['actual_native_paid_caps'] or unit=='unit-b'

@pytest.mark.parametrize('fault',['claim','prepared','start','resource','boolean_count','binding','db','spent','prior','unused','refund','double','epoch','mono','flag','bool_clock','run','duplicate_run','refs','prepared_pin','complete','failed','coverage'])
def test_partial_bundle_changed_rows_or_accounting_refused(typed_rows,fault):
    unit,p,o,v=typed_rows;o=deepcopy(o);v=deepcopy(v);d=o['detail']
    if fault in ('claim','prepared','start','resource'):d[fault+'_count']+=1
    elif fault=='boolean_count':d['claim_count']=False
    elif fault=='binding':d['enrollment']['history_binding_sha256']='f'*64
    elif fault=='db':v['database_sha256']='f'*64
    elif fault=='spent':d['full_prepaid_caps']['radio_attempts']+=256
    elif fault=='prior':v['actual_native_paid_caps']['radio_attempts']+=1
    elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']+=256
    elif fault=='refund':v['paid_caps']=deepcopy(d['prior_plus_prepaid_caps']);v['paid_caps']['radio_attempts']-=1
    elif fault=='double':v['paid_caps']['radio_attempts']+=512
    elif fault=='epoch':o['observation_original_epoch_us']=p.expires_epoch_us-1
    elif fault=='mono':o['observation_original_monotonic_us']=d['enrollment']['deadline_monotonic_us']-1
    elif fault=='flag':o['policy_expired_at_observation']=False
    elif fault=='bool_clock':o['observation_original_monotonic_us']=True
    elif fault=='run':v['runs']=[dict(run_id='f'*32)]
    elif fault=='duplicate_run':v['runs']*=2;v['runs'].append(dict(run_id='f'*32))
    elif fault=='refs':v['controller_endpoint_refs']=[{}]
    elif fault=='prepared_pin':v['preparation_sha256']={'f'*32:'e'*64}
    elif fault=='complete':v['require_result_complete']=False
    elif fault=='failed':v['failed_started_runs']=None
    elif fault=='coverage':o['legacy_relation']=None if unit!='unit-b' else {}
    with pytest.raises((RuntimeError,TypeError,KeyError)):rpc.qualify_partial_bundle_costs(unit,p,o,v)

@pytest.mark.parametrize('unit',['unit-a','unit-c'])
def test_exact_four_canonical_outcomes_are_required(unit):
    for run,result in DATA['outcomes'][unit].items():
        rpc.qualify_partial_bundle_result(unit,run,result,parent_id=rpc.V3_VERIFIED_BUNDLE_PAID)
        for field in ('complete','disarmed_on_exit'):
            wrong=deepcopy(result);wrong[field]=not result[field]
            with pytest.raises(RuntimeError):rpc.qualify_partial_bundle_result(unit,run,wrong,parent_id=rpc.V3_VERIFIED_BUNDLE_PAID)


def test_new_roster_preserves_all_twenty_five_and_capture_prefix():
    old=tuple(DATA['prior_paid_parent_ids'])
    assert len(old)==25 and tuple(retained.PAID_HISTORY[:25])==old
    assert retained.PAID_HISTORY[25]==rpc.V3_VERIFIED_BUNDLE_PAID
    assert retained.UNADMITTED_PAID_HISTORY==old[:12]

@pytest.mark.parametrize('count',[0,2,1,3])
def test_new_fixed_parent_identity_and_capture_seal_scope(tmp_path,count):
    spec=importlib.util.spec_from_file_location('h21_seal_fixture',Path(__file__).with_name('test_history_paid_parent_seal.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    root,files,dirs=helper.fixture(tmp_path,count,'parents/'+rpc.V3_VERIFIED_BUNDLE_PAID)
    if count in (0,2):
        with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as held:held.check()
    else:
        with pytest.raises(FenceError):HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)


def policy_body_unchanged_for_old_twenty_one():
    path=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-native-window-20261011/resume-history-preparation-20/unit-a.json')
    if not path.is_file():return True
    old=json.loads(path.read_bytes())['previous']
    assert [p['policy']['parent_id'] for p in old]==list(retained.PAID_HISTORY[:21])
    for row in old:
        policy=ParentPolicy.from_dict(row['policy'])
        assert rpc.canonical(policy.to_dict())==rpc.canonical(row['policy']) and policy.digest==row['policy_digest']
    return True


@pytest.mark.parametrize('clock_gap',['epoch','monotonic'])
def test_original_v2_whole_expiry_does_not_erase_still_live_five_second_phase(typed_rows,clock_gap):
    unit,p,o,v=typed_rows;o=deepcopy(o)
    if clock_gap=='epoch':o['observation_original_epoch_us']=p.expires_epoch_us+4000000
    else:o['observation_original_monotonic_us']=o['detail']['enrollment']['deadline_monotonic_us']+4000000
    assert o['observation_original_epoch_us']>=p.expires_epoch_us
    assert o['observation_original_monotonic_us']>=o['detail']['enrollment']['deadline_monotonic_us']
    with pytest.raises(RuntimeError,match='v2_partial_bundle_original_phase_clocks_not_expired'):
        rpc.qualify_partial_bundle_costs(unit,p,o,v)


@pytest.mark.parametrize('fault',['index','reused','whole','decoded','context','campaign','request','response','manifest','chunk','resource','resource_clock','resource_count'])
def test_accepted_part_cannot_be_rebound_or_counted_as_whole(typed_rows,fault):
    unit,p,o,v=typed_rows;o=deepcopy(o);v=deepcopy(v);r=v['accepted_part'];part=r['bundle_part']
    if fault=='index':part['part_index']=1
    elif fault=='reused':part['reused_verified_part']=True
    elif fault=='whole':part['native_bundle_complete']=True
    elif fault=='decoded':part['useful_decoded_bytes']+=1
    elif fault=='context':part['bundle_context_sha256']='f'*64
    elif fault=='campaign':r['campaign_id']='f'*32
    elif fault=='request':r['request_run_id']='f'*32
    elif fault=='response':r['response_run_id']='f'*32
    elif fault=='manifest':r['manifest_sha256']='f'*64
    elif fault=='chunk':r['chunk_sha256']='f'*64
    elif fault=='resource':o['detail']['resources']=[dict(campaign_id='f'*32)]
    elif fault=='resource_clock':
        if unit=='unit-b':o['detail']['resources']=[{}]
        else:o['detail']['resources'][0]['bundle_original_deadline_monotonic_us']+=1
    else:o['detail']['resources'].append({})
    with pytest.raises((RuntimeError,TypeError,KeyError)):rpc.qualify_partial_bundle_costs(unit,p,o,v)


@pytest.mark.parametrize('count',[30,31,32,33,34,35,36,37,38,39,40,41,42,43,44,45,46])
def test_original_h42_checkpoint_owner_requires_exact_captured_history(count,monkeypatch):
    from lantern_meshticulum.bench import bundle_checkpoint_adapter as adapter
    policy=ParentPolicy.from_dict(DATA['policy']);owner=deepcopy(OWNER_PHASE['unit-a'])
    owner['bundle_radio_checkpoint']=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',primitives_sha256=adapter.checkpoint_api.PRIMITIVES_SHA,retained_history_count=count,maximum_checkpoints=4,producer_sha256='a'*64)
    assert len(adapter.checkpoint_api.HISTORY)==48
    if count==42:
        # The original checkpoint contract is H42; only this historical positive case uses its captured tuple.
        captured=tuple(retained.PAID_HISTORY[:42])
        assert rpc.sha(rpc.canonical(captured))=='ebf29572ee1b76fd9e14b536a2ad4b1cd0eca4016a195df108df84f7fb4a04b2'
        monkeypatch.setattr(adapter.checkpoint_api,'HISTORY',captured)
        context,_=adapter.context_for('unit-a',policy,owner,1);assert context.policy is policy
    else:
        with pytest.raises(RuntimeError):adapter.context_for('unit-a',policy,owner,1)


@pytest.mark.parametrize('count',[42,43,44,45,46])
def test_original_h42_checkpoint_stays_closed_with_actual_h44(count,monkeypatch):
    from lantern_meshticulum.bench import bundle_checkpoint_adapter as adapter
    assert len(adapter.checkpoint_api.HISTORY)==len(retained.PAID_HISTORY)==48
    policy=ParentPolicy.from_dict(DATA['policy']);owner=deepcopy(OWNER_PHASE['unit-a'])
    owner['bundle_radio_checkpoint']=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',primitives_sha256=adapter.checkpoint_api.PRIMITIVES_SHA,retained_history_count=count,maximum_checkpoints=4,producer_sha256='a'*64)
    monkeypatch.setattr(adapter.checkpoint_api,'load_primitives',lambda _:pytest.fail('primitives reached before H44 refusal'))
    with pytest.raises(RuntimeError,match='original_checkpoint_owner_optin_required'):
        adapter.open_serial('unit-a',policy,owner,1)
