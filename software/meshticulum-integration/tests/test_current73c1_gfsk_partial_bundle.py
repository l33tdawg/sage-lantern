"""Actual expired 73c1 SQL/metadata controls; no device or live admission."""
from copy import deepcopy
from dataclasses import asdict
import json,sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from lantern_meshticulum.bench import pilot_resume_rpc as rpc,pilot_parent_snapshot as snap
from lantern_meshticulum.bench import gfsk_bundle_partial_history as contract,retained_capture as retained
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,hashed
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost

DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('current73c1-gfsk-partial-bundle-metadata.json').read_bytes())


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def typed_rows(tmp_path,request):
    unit=request.param;policy=ParentPolicy.from_dict(DATA['policy']);local=policy.unit(unit)
    observed=DATA['natives'][unit]['observation']
    # Create only a local schema, then replay the actual captured append-only
    # rows into a closed memory image. Runtime authority is not modeled as RF.
    caps=NativeCaps.enroll(tmp_path/'native.sqlite',policy,unit=unit,
        clock=lambda:(policy.issued_epoch_us+1000,1000000000),facts=lambda:NativeFacts(unit,local.host_boot_id,local.peer),
        approve=lambda _:True,prior_check=lambda _:True,history_binding_sha256='e'*64)
    assert caps.close()
    image=bytearray((tmp_path/'native.sqlite').read_bytes());image[18]=image[19]=1
    db=sqlite3.connect(':memory:');db.deserialize(bytes(image))
    e=observed['detail']['enrollment'];enrollment=dict(schema='meshticulum.native-parent-enrollment.v2',policy=DATA['policy'],
        **{k:e[k] for k in ('unit','host_boot_id','original_epoch_us','original_monotonic_us','deadline_monotonic_us','history_binding_sha256')})
    db.execute('UPDATE enrollment SET body=? WHERE id=1',(rpc.canonical(enrollment),))
    sc=observed['detail']['stored_clock'];db.execute('UPDATE clock SET epoch=?,mono=? WHERE id=1',(sc['epoch_us'],sc['monotonic_us']))
    resources={}
    for row in observed['detail']['resources']:
        body={k:v for k,v in row.items() if k not in ('resource_id','row_sha256')};resources[row['resource_id']]=body
        assert rpc.sha(rpc.canonical(body))==row['row_sha256'];db.execute('INSERT INTO resources VALUES(?,?)',(row['resource_id'],rpc.canonical(body)))
    # Actual prepared and start bodies are supplied by the independently
    # captured legacy/native relationship, not synthesized from a new scope.
    for row in observed['detail']['claims']:
        run=row['child']['run_id'];receipt=DATA['prepared'][unit][run];start=DATA['outcomes'][unit][run]['parent']
        body=dict(child=receipt['child_caps'],controller_allocation_id=receipt['controller_allocation_id'],resource_id=row['resource_id'],resource=resources[row['resource_id']])
        assert hashed(body)==row['native_claim_id'] and rpc.sha(rpc.canonical(body))==row['row_sha256']
        db.execute('INSERT INTO claims VALUES(?,?,?)',(row['native_claim_id'],rpc.canonical(body),run))
        db.execute('INSERT INTO prepared VALUES(?,?)',(row['native_claim_id'],rpc.canonical(receipt)))
        db.execute('INSERT INTO starts VALUES(?,?)',(row['native_claim_id'],rpc.canonical(start)))
    db.commit();db.execute('PRAGMA query_only=ON');db.execute('PRAGMA trusted_schema=OFF')
    class Reader:
        name='native.sqlite'
        def read(self,name,maximum):assert name=='native.sqlite.approval';return (hashed(enrollment)+'\n').encode()
    scope=SimpleNamespace(parent_id=policy.parent_id,policy_digest=policy.digest,unit=unit)
    snap._schema(db,'native');parsed,detail,_=snap._native(db,scope,Reader());db.close()
    assert detail==observed['detail']
    return unit,parsed,deepcopy(observed),deepcopy(DATA['rows'][unit])


def test_actual_two_grants_keep_the_full_original_ten_grant_enrollment(typed_rows):
    u,p,o,v=typed_rows
    rpc.qualify_history_claims(p,u,o,v['runs'],[]);rpc.qualify_gfsk_partial_bundle_costs(u,p,o,v)
    count=0 if u=='unit-b' else 2
    assert o['detail']['claim_count']==count and o['detail']['resource_count']==(1 if count else 0)
    assert p.unit(u).limits==p.unit(u).prior_debt.plus(Cost() if not count else Cost(2560,600000000))
    assert v['conservative_unused_enrollment_caps']==asdict(Cost() if not count else Cost(2048,480000000))
    assert v['require_result_complete'] is False
    assert v['failed_started_runs']==(['a0d6a932b22ba84884e03317fde00bc8'] if u=='unit-a' else [])


@pytest.mark.parametrize('fault',['claims','resources','db','refund','double','unused','epoch','mono','run','prepared',
    'failed','complete','resource_clock','unknown','fenced','native_consumption'])
def test_partial_scope_debt_and_original_clocks_cannot_change(typed_rows,fault):
    u,p,o,v=typed_rows
    if fault=='claims':o['detail']['claim_count']+=1
    elif fault=='resources':o['detail']['resource_count']+=1
    elif fault=='db':v['database_sha256']='f'*64
    elif fault=='refund':v['paid_caps']['radio_attempts']-=1
    elif fault=='double':v['paid_caps']['radio_attempts']+=1
    elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']+=1
    elif fault=='epoch':o['observation_original_epoch_us']=v['owner']['phase_end_epoch_us']-1
    elif fault=='mono':o['observation_original_monotonic_us']=v['owner']['native_phase'][u]['deadline_monotonic_us']-1
    elif fault=='run':v['runs'].append(dict(run_id='f'*32))
    elif fault=='prepared':v['preparation_sha256']['f'*32]='f'*64
    elif fault=='failed':v['failed_started_runs']=['f'*32]
    elif fault=='complete':v['require_result_complete']=True
    elif fault=='resource_clock':
        if u=='unit-b':o['detail']['resource_count']=1
        else:o['detail']['resources'][0]['application_expires_epoch_us']+=1
    elif fault=='native_consumption':v['actual_native_paid_caps']['radio_attempts']+=1
    elif u=='unit-b':o['legacy_runs']=[]
    elif fault=='unknown':o['legacy_runs'][0]['journal']['has_unknown_or_pending']=True
    else:o['legacy_runs'][0]['journal']['owner']['fenced']=True
    with pytest.raises((RuntimeError,ValueError,KeyError,TypeError)):
        rpc.qualify_gfsk_partial_bundle_costs(u,p,o,v)


def test_H35_extends_the_exact_H34_prefix_and_preserves_earlier_digest_positions():
    from lantern_meshticulum.bench import gfsk_bundle_history as old
    assert len(retained.PAID_HISTORY)==len(rpc.OLD_DIGESTS)==38
    assert retained.PAID_HISTORY[:34]==contract.H34_PREFIX
    assert retained.PAID_HISTORY[33]==old.PARENT and rpc.OLD_DIGESTS[33]==old.POLICY_DIGEST
    assert retained.PAID_HISTORY[34]==contract.PARENT and rpc.OLD_DIGESTS[34]==contract.POLICY_DIGEST
    assert contract.pending_next_roster()==retained.PAID_HISTORY[:35]


def test_new_final_binding_is_closed_when_actual_receipt_is_missing(monkeypatch):
    monkeypatch.setattr(contract,'NEXT_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(ValueError):contract.require_final_bindings()
