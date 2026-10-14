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

DATA=json.loads(Path(__file__).with_name('fixtures').joinpath('current4aa3-sf5-completed-metadata.json').read_bytes())

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
        paid_caps=asdict(local.limits),
        runs=runs,retired_unstarted=[],controller_endpoint_refs=[dict(child=dict(run_id=x['run_id'])) for x in runs],
        preparation_sha256={x['run_id']:'e'*64 for x in runs},legacy_sha256=None if not runs else rpc.sha(rpc.canonical(observed['legacy_runs'])),
        require_result_complete=True,failed_started_runs=[])
    return unit,parsed,observed,value


def test_actual_completed_two_link_sample_has_exact_full_bound_and_no_unused_refund(typed_rows):
    u,p,o,v=typed_rows
    rpc.qualify_history_claims(p,u,o,v['runs'],[])
    local=p.unit(u);extra=Cost() if u=='unit-b' else Cost(512,120000000)
    assert o['detail']['full_prepaid_caps']==asdict(extra)
    assert o['detail']['prior_plus_prepaid_caps']==asdict(local.limits)==v['paid_caps']
    assert local.limits==local.prior_debt.plus(extra)
    assert o['detail']['resource_count']==(0 if u=='unit-b' else 1)
    assert o['detail']['claim_count']==o['detail']['prepared_count']==o['detail']['start_count']==(0 if u=='unit-b' else 2)
    assert all(x['complete'] and x['disarmed_on_exit'] for x in DATA['outcomes'][u].values())
    assert DATA['verified_piece']['useful_verified_bytes']==8164 and DATA['verified_piece']['publisher_verified'] is True


@pytest.mark.parametrize('fault',['extra','missing','unstarted','duplicate'])
def test_complete_sample_claim_coverage_cannot_lose_or_add_paid_endpoint(typed_rows,fault):
    u,p,o,v=typed_rows;o=deepcopy(o);v=deepcopy(v)
    if fault=='extra':v['runs'].append(dict(run_id='f'*32))
    elif fault=='missing':
        if v['runs']:v['runs'].pop()
        else:o['detail']['claim_count']=1
    elif fault=='unstarted':
        if o['detail']['claims']:o['detail']['claims'][0]['started']=False
        else:o['detail']['start_count']=1
    else:o['detail']['claim_count']+=1
    with pytest.raises(RuntimeError):rpc.qualify_history_claims(p,u,o,v['runs'],[])


def test_roster_preserves_old27_and_only_adds_successful_sample():
    old=tuple(DATA['prior_paid_parent_ids']);assert len(old)==27 and retained.PAID_HISTORY[:27]==old
    assert len(retained.PAID_HISTORY)==len(rpc.OLD_DIGESTS)==44
    assert retained.PAID_HISTORY[27]==rpc.SF5_COMPLETED_PAID
    assert retained.UNADMITTED_PAID_HISTORY==old[:12]
    for unit,rows in DATA['outcomes'].items():
        assert set(rows)==set(rpc.SF5_COMPLETED_OUTCOMES[unit])
        assert all(rpc.sha(rpc.canonical(v))==rpc.SF5_COMPLETED_OUTCOMES[unit][run] for run,v in rows.items())


@pytest.mark.parametrize('count',[0,2,1,3])
def test_completed_sample_prepared_identity_coverage_is_exact(tmp_path,count):
    import importlib.util
    spec=importlib.util.spec_from_file_location('seal_fixture4aa3',Path(__file__).with_name('test_history_paid_parent_seal.py'))
    helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
    root,files,dirs=helper.fixture(tmp_path,count,'parents/'+rpc.SF5_COMPLETED_PAID)
    if count in (0,2):
        with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as held:held.check()
    else:
        with pytest.raises(FenceError):HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs)
