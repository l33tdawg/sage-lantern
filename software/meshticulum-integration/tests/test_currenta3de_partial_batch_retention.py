"""Actual captured SF5 metadata through genuine SQLite parser; no native authority.

Only the local schema is freshly enrolled with modeled approval/facts. The
captured actual enrollment, claims, preparation and start rows are reconstructed
in memory and checked against the genuine parser before cost predicates run.
"""
from copy import deepcopy
from dataclasses import asdict
import json,os,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,pilot_parent_snapshot as snap,retained_capture as retained
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,hashed
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence,FenceError

DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('currenta3de-partial-batch-metadata.json').read_bytes())

@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def typed_rows(tmp_path,request):
    unit=request.param;policy=ParentPolicy.from_dict(DATA['policy']);local=policy.unit(unit);actual=DATA['native'][unit]
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
        assert rpc.sha(rpc.canonical(body))==row['row_sha256'];db.execute('INSERT INTO resources VALUES(?,?)',(row['resource_id'],rpc.canonical(body)))
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
    snap._schema(db,'native');parsed,detail,_=snap._native(db,scope,Reader());db.close();assert detail==actual['detail']
    owner=DATA['owners'][unit];observed=deepcopy(actual)
    assert observed['policy_expired_at_observation'] and observed['observation_original_epoch_us']>=owner['phase_end_epoch_us']
    runs=[dict(run_id=run) for run in DATA['outcomes'][unit]]
    value=dict(owner=owner,database_sha256=actual['expected_database_sha256'],
        actual_native_paid_caps=detail['prior_plus_prepaid_caps'],actual_controller_paid_caps=detail['prior_plus_prepaid_caps'],
        conservative_unused_enrollment_caps=asdict(Cost() if unit=='unit-b' else Cost(512,120000000)),paid_caps=asdict(local.limits),
        runs=runs,retired_unstarted=[],controller_endpoint_refs=[dict(child=dict(run_id=x['run_id'])) for x in runs],
        preparation_sha256={x['run_id']:'e'*64 for x in runs},legacy_sha256=None if not runs else rpc.sha(rpc.canonical(observed['legacy_runs'])),
        require_result_complete=False,failed_started_runs=[] if unit=='unit-b' else rpc.PARTIAL_BATCH_FAILED)
    return unit,parsed,observed,value


def test_actual_four_paid_grants_keep_all_original_six_grant_caps_once(typed_rows):
    u,p,o,v=typed_rows
    rpc.qualify_history_claims(p,u,o,v['runs'],[]);rpc.qualify_partial_batch_costs(u,p,o,v)
    actual=Cost() if u=='unit-b' else Cost(1024,240000000)
    full=Cost() if u=='unit-b' else Cost(1536,360000000)
    assert o['detail']['full_prepaid_caps']==asdict(actual)
    assert p.unit(u).limits==p.unit(u).prior_debt.plus(full)
    assert v['conservative_unused_enrollment_caps']==asdict(Cost() if u=='unit-b' else Cost(512,120000000))
    assert DATA['verified_piece']['useful_verified_bytes']==8164


@pytest.mark.parametrize('fault',['claim','resources','db','spent','unused','refund','double','epoch','mono','failed','complete','run','refs','pending'])
def test_partial_batch_cannot_lose_debt_rebind_original_clock_or_hide_failed_endpoint(typed_rows,fault):
    u,p,o,v=typed_rows;o=deepcopy(o);v=deepcopy(v)
    if fault=='claim':o['detail']['claim_count']+=1
    elif fault=='resources':o['detail']['resource_count']+=1
    elif fault=='db':v['database_sha256']='f'*64
    elif fault=='spent':o['detail']['full_prepaid_caps']['radio_attempts']+=256
    elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']-=1
    elif fault=='refund':v['paid_caps']['radio_attempts']-=256
    elif fault=='double':v['paid_caps']['radio_attempts']+=256
    elif fault=='epoch':o['observation_original_epoch_us']=v['owner']['phase_end_epoch_us']-1
    elif fault=='mono':o['observation_original_monotonic_us']=v['owner']['native_phase'][u]['deadline_monotonic_us']-1
    elif fault=='failed':v['failed_started_runs']=[] if u!='unit-b' else ['f'*32]
    elif fault=='complete':v['require_result_complete']=True
    elif fault=='run':v['runs'].append(dict(run_id='f'*32))
    elif fault=='refs':v['controller_endpoint_refs']=[{}]
    else:
        if u=='unit-b':o['legacy_runs']=[]
        else:o['legacy_runs'][0]['journal']['has_unknown_or_pending']=True
    with pytest.raises((RuntimeError,KeyError,TypeError)):rpc.qualify_partial_batch_costs(u,p,o,v)


def test_roster_preserves_all_old28_and_only_appends_partial_batch():
    old=tuple(DATA['prior_paid_parent_ids']);assert len(old)==28 and retained.PAID_HISTORY[:28]==old
    assert len(retained.PAID_HISTORY)==len(rpc.OLD_DIGESTS)==41
    assert retained.PAID_HISTORY[28]==rpc.PARTIAL_BATCH_PAID
    assert retained.UNADMITTED_PAID_HISTORY==old[:12]
