"""Actual expired e8aa SQLite/metadata controls; no native admission."""
from copy import deepcopy
from dataclasses import asdict
import json,os,sqlite3
from pathlib import Path
from types import SimpleNamespace
import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc,pilot_parent_snapshot as snap,retained_capture as retained,gfsk_bundle_history as contract
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,hashed
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,Cost
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence,FenceError
DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('current-e8aa-completed-gfsk-bundle-metadata.json').read_bytes())
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
    value=deepcopy(DATA['values'][unit])
    return unit,parsed,observed,value



def test_actual_ten_grants_five_resources_and_all_caps_once(typed_rows):
    u,p,o,v=typed_rows
    rpc.qualify_history_claims(p,u,o,v['runs'],[]);rpc.qualify_gfsk_bundle_costs(u,p,o,v)
    count=0 if u=='unit-b' else 10
    assert o['detail']['claim_count']==count and o['detail']['resource_count']==(5 if count else 0)
    assert p.unit(u).limits==p.unit(u).prior_debt.plus(Cost() if u=='unit-b' else Cost(2560,600000000))
    assert v['conservative_unused_enrollment_caps']==asdict(Cost())
    assert DATA['whole_verification']['useful_decoded_bytes']==100000
    assert len(DATA['verified_pieces'])==5


@pytest.mark.parametrize('fault',['claims','resources','db','refund','double','unused','epoch','mono','run','prepared','failed','complete','resource_clock','unknown'])
def test_completed_bundle_debt_scope_and_original_clocks_cannot_change(typed_rows,fault):
    u,p,o,v=typed_rows;o=deepcopy(o);v=deepcopy(v)
    if fault=='claims':o['detail']['claim_count']+=1
    elif fault=='resources':o['detail']['resource_count']+=1
    elif fault=='db':v['database_sha256']='f'*64
    elif fault=='refund':v['paid_caps']['radio_attempts']-=1
    elif fault=='double':v['paid_caps']['radio_attempts']+=1
    elif fault=='unused':v['conservative_unused_enrollment_caps']['radio_attempts']=1
    elif fault=='epoch':o['observation_original_epoch_us']=v['owner']['phase_end_epoch_us']-1
    elif fault=='mono':o['observation_original_monotonic_us']=v['owner']['native_phase'][u]['deadline_monotonic_us']-1
    elif fault=='run':v['runs'].append(dict(run_id='f'*32))
    elif fault=='prepared':v['preparation_sha256']['f'*32]='f'*64
    elif fault=='failed':v['failed_started_runs']=['f'*32]
    elif fault=='complete':v['require_result_complete']=False
    elif fault=='resource_clock':
        if u=='unit-b':o['detail']['resource_count']=1
        else:o['detail']['resources'][0]['application_expires_epoch_us']+=1
    else:
        if u=='unit-b':o['legacy_runs']=[]
        else:o['legacy_runs'][0]['journal']['has_unknown_or_pending']=True
    with pytest.raises((RuntimeError,ValueError,KeyError,TypeError)):
        rpc.qualify_gfsk_bundle_costs(u,p,o,v)


def test_H34_keeps_exact_H33_prefix_and_old_capture_boundary():
    old=tuple(DATA['prior_paid_parent_ids']);assert len(old)==33
    assert retained.PAID_HISTORY[:33]==old and retained.PAID_HISTORY[33]==contract.PARENT
    assert len(rpc.OLD_DIGESTS)==45 and rpc.OLD_DIGESTS[33]==contract.POLICY_DIGEST
    assert retained.UNADMITTED_PAID_HISTORY==old[:12]


def test_fence_admits_only_original_e8aa_prepared_namespace(typed_rows,tmp_path):
    unit,p,o,v=typed_rows;base=tmp_path/'state';base.mkdir(mode=0o700);path=base/'parents'/p.parent_id/'prepared-identities';path.mkdir(parents=True,mode=0o700)
    for folder in (base/'parents',path.parent):folder.chmod(0o700)
    pins={}
    for run in contract.NATIVE_OUTCOME_SHA256[unit]:
        raw=b'{}';file=path/(run+'.json');file.write_bytes(raw);file.chmod(0o600);pins[str(file.relative_to(base))]=rpc.sha(raw)
    dirs={'': ['parents'],'parents':[p.parent_id],'parents/'+p.parent_id:['prepared-identities'],'parents/'+p.parent_id+'/prepared-identities':[run+'.json' for run in contract.NATIVE_OUTCOME_SHA256[unit]]}
    with HistoricalMetadataFence(base,owner_uid=os.geteuid(),expected_files=pins,expected_directories=dirs):pass
