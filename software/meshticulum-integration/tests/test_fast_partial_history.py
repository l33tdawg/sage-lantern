"""Pinned local outcomes and explicit local models; no native expiry is presumed."""
from copy import deepcopy
from dataclasses import asdict, replace
import hashlib
import json
import os
from pathlib import Path

import pytest
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import fast_bundle_partial_history as history, pilot_resume_rpc as rpc
from lantern_meshticulum.bench import fast_bundle, gfsk250_mb1_partial_history as h38, fast_bundle_unstarted_history as h40, fast_bundle_zero_history as h39
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy, Cost, canonical
from lantern_meshticulum.bench.history_metadata_fence import HistoricalMetadataFence, FenceError

DATA=Path(__file__).with_name('fixtures')/'current1d43-fast-partial-inputs.json'


@pytest.fixture
def original():
    value=json.loads(DATA.read_bytes())
    return value,ParentPolicy.from_dict(value['policy'])


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_original_context_and_full_caps_are_retained_without_live_scope(original,unit,monkeypatch):
    value,policy=original
    monkeypatch.setattr(fast_bundle,'require_bindings',lambda:pytest.fail('history used live authority'))
    context=history.original_context(value['owners'][unit],policy,unit=unit)
    assert context.value['whole_bytes']==100000 and len(context.value['stages'])==5
    assert history.declared_partition(policy)[unit]['declared_new_reservation']==asdict(Cost() if unit=='unit-b' else Cost(2560,600000000))
    assert rpc.HISTORY[:40]==history.H40_PREFIX and rpc.HISTORY[40]==history.PARENT
    assert len(rpc.HISTORY)==len(rpc.OLD_DIGESTS)==48 and rpc.GUARDED_PAID[history.PARENT]==policy.digest
    assert value['native_observations'] is value['history_receipt'] is None
    assert all(outcome['complete'] and outcome['disarmed_on_exit'] for outcome in value['outcomes'][unit].values())


@pytest.mark.parametrize('other',[h38,h39,h40])
def test_reused_predicates_preserve_each_historical_module_parent_scope(original,other):
    value,policy=original
    with pytest.raises(WireError):other.original_context(value['owners']['unit-a'],policy,unit='unit-a')


@pytest.mark.parametrize('fault',['policy_deadline','parent','discount','owner_deadline','runtime','scope','kind'])
def test_original_context_refuses_retyping_renewal_or_credit(original,fault):
    value,policy=original;owner=deepcopy(value['owners']['unit-a'])
    if fault=='policy_deadline':policy=replace(policy,expires_epoch_us=policy.expires_epoch_us+1)
    elif fault=='parent':policy=replace(policy,parent_id='f'*32)
    elif fault=='discount':policy=replace(policy,units=tuple(replace(u,limits=u.prior_debt) if u.unit=='unit-a' else u for u in policy.units))
    elif fault=='owner_deadline':owner['phase_end_epoch_us']+=1
    elif fault=='runtime':owner['runtime_sha256']='f'*64
    elif fault=='scope':owner['mb1_bundle_scope_sha256']='f'*64
    else:policy=replace(policy,qualification_kind=h38.QUALIFICATION_KIND)
    with pytest.raises(WireError):history.original_context(owner,policy,unit='unit-a')


@pytest.mark.parametrize('field',['EXPIRY_COMPLETION_SHA256','NEXT_HISTORY_RECEIPT_SHA256','NATIVE_DATABASE_SHA256','NATIVE_OUTCOME_SHA256','FAST_STAGE_SHA256','FINAL_ACK_SHA256','VERIFIED_PIECE_SHA256'])
def test_pending_actual_pins_cannot_qualify_or_issue(field,original,monkeypatch):
    # Populate only this test's format-valid model, then remove one predicate's pin.
    for key in ('EXPIRY_COMPLETION_SHA256','NEXT_HISTORY_RECEIPT_SHA256'):
        monkeypatch.setattr(history,key,'a'*64)
    for key in ('NATIVE_DATABASE_SHA256','FAST_STAGE_SHA256'):
        monkeypatch.setattr(history,key,{u:None if key=='FAST_STAGE_SHA256' and u=='unit-b' else 'b'*64 for u in ('unit-a','unit-b','unit-c')})
    for key in ('NATIVE_OUTCOME_SHA256','FINAL_ACK_SHA256'):
        monkeypatch.setattr(history,key,{u:{run:'c'*64 for run in values} for u,values in getattr(history,key).items()})
    monkeypatch.setattr(history,'VERIFIED_PIECE_SHA256',{u:{} for u in ('unit-a','unit-b','unit-c')})
    assert history.require_final_bindings() is True
    if field.endswith('SHA256') and isinstance(getattr(history,field),dict):
        monkeypatch.setattr(history,field,dict.fromkeys(getattr(history,field)))
    else:monkeypatch.setattr(history,field,None)
    with pytest.raises(WireError):history.require_final_bindings()
    with pytest.raises(WireError):rpc.qualify_gfsk250_mb1_partial_costs('unit-a',original[1],{}, {})


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
@pytest.mark.parametrize('fault',['epoch','mono','status','unexpired'])
def test_pure_expiry_model_requires_both_original_absolute_clocks(original,unit,fault):
    value,policy=original;owner=value['owners'][unit]
    observation=dict(status='scoped_rows_observed',parent_id=history.PARENT,policy_digest=policy.digest,unit=unit,
        policy_expired_at_observation=True,observation_original_epoch_us=history.ORIGINAL_ABSOLUTE_END,
        observation_original_monotonic_us=owner['native_phase'][unit]['deadline_monotonic_us']+history.ORIGINAL_ABSOLUTE_END-history.ORIGINAL_PHASE_END)
    history.retained_context(owner,policy,unit=unit,observation=observation)
    if fault=='epoch':observation['observation_original_epoch_us']-=1
    elif fault=='mono':observation['observation_original_monotonic_us']-=1
    elif fault=='status':observation['status']='unavailable'
    else:observation['policy_expired_at_observation']=False
    with pytest.raises(WireError):history.retained_context(owner,policy,unit=unit,observation=observation)


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def modeled_metadata(tmp_path,request,monkeypatch):
    """Only this fixture supplies model pins; production pins stay untouched."""
    unit=request.param;parent=tmp_path/'parents'/history.PARENT;parent.mkdir(parents=True)
    monkeypatch.setattr(history,'require_final_bindings',lambda:True)
    stage=b'{"local_model":"stage"}';proof=b'{"local_model":"receiver-proof"}'
    sha=lambda raw:hashlib.sha256(raw).hexdigest()
    monkeypatch.setitem(history.FAST_STAGE_SHA256,unit,None if unit=='unit-b' else sha(stage))
    monkeypatch.setitem(history.FINAL_ACK_SHA256,unit,{run:sha(proof) for run in history.FINAL_ACK_SHA256[unit]})
    monkeypatch.setitem(history.VERIFIED_PIECE_SHA256,unit,{})
    if unit!='unit-b':(parent/'fast-stage-0.json').write_bytes(stage)
    for run in history.NATIVE_OUTCOME_SHA256[unit]:
        target=tmp_path/'runs'/run;target.mkdir(parents=True)
        if run in history.FINAL_ACK_SHA256[unit]:(target/'fast-final-ack.json').write_bytes(proof)
    class Reader:
        opened=[]
        def directory(self,path):assert path.is_dir()
        def read(self,path,maximum):
            self.opened.append(path);raw=path.read_bytes();assert len(raw)<=maximum;return raw
        def check(self):return True
    value=dict(fast_stage_sha256={} if unit=='unit-b' else {'fast-stage-0.json':sha(stage)},
        fast_final_ack_sha256=deepcopy(history.FINAL_ACK_SHA256[unit]),verified_piece_sha256={})
    return unit,tmp_path,Reader(),value


def test_model_metadata_reads_only_stage_and_receiver_final_ack(modeled_metadata):
    unit,state,reader,value=modeled_metadata
    files=history.metadata_files(reader,state,unit,value)
    assert len(files)==(0 if unit=='unit-b' else 2)
    assert all(path.name in ('fast-stage-0.json','fast-final-ack.json') for path in reader.opened)


@pytest.mark.parametrize('fault',['stage_extra','proof_scope','optional_piece_scope'])
def test_model_metadata_refuses_unobserved_records(modeled_metadata,fault):
    unit,state,reader,value=modeled_metadata
    if fault=='stage_extra':(state/'parents'/history.PARENT/'fast-stage-1.json').write_bytes(b'{}')
    elif fault=='proof_scope':value['fast_final_ack_sha256']['f'*32]='f'*64
    else:value['verified_piece_sha256']['f'*32]='f'*64
    with pytest.raises(WireError):history.metadata_files(reader,state,unit,value)


@pytest.mark.parametrize('name',['fast-final-ack.json','verified-piece.json'])
def test_only_exact_record_paths_are_fenced_and_changed_bytes_refuse(tmp_path,name):
    root=tmp_path/'state';root.mkdir(mode=0o700);(root/'runs').mkdir(mode=0o700)
    run='9913129134d9d676b7d8768284f9cfff';target=root/'runs'/run;target.mkdir(mode=0o700)
    path=target/name;path.write_bytes(b'{}');path.chmod(0o600)
    relative='runs/'+run+'/'+name
    dirs={'':['runs'],'runs':[run],'runs/'+run:[name]};files={relative:hashlib.sha256(b'{}').hexdigest()}
    with HistoricalMetadataFence(root,owner_uid=os.geteuid(),expected_files=files,expected_directories=dirs) as fence:
        fence.check();path.write_bytes(b'{"changed":true}')
        with pytest.raises(FenceError):fence.check()


def test_next_scope_is_independently_required(monkeypatch):
    from types import MappingProxyType
    monkeypatch.setattr(fast_bundle,'PHYSICAL_BINDINGS',MappingProxyType(dict(fast_bundle.PHYSICAL_BINDINGS,scope_sha256=None,history_receipt_sha256=None)))
    with pytest.raises(WireError,match='pending_fast_bundle_scope'):fast_bundle.require_bindings()


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def modeled_costs(original,request,monkeypatch):
    """Model only the native projection; original policy, owner and run IDs are real."""
    value,policy=original;unit=request.param;owner=value['owners'][unit];local=policy.unit(unit)
    count=0 if unit=='unit-b' else 2;claimed=Cost() if not count else Cost(512,120000000)
    runs=[dict(run_id=run) for run in value['outcomes'][unit]];epoch=history.ORIGINAL_ABSOLUTE_END+1
    mono=owner['native_phase'][unit]['deadline_monotonic_us']+epoch-history.ORIGINAL_PHASE_END
    legacy=[dict(wire={'retired':1},journal={'has_unknown_or_pending':False,'owner':{'fenced':False}}) for _ in runs] if count else None
    monkeypatch.setattr(history,'require_final_bindings',lambda:True)
    monkeypatch.setitem(history.NATIVE_DATABASE_SHA256,unit,'d'*64)
    monkeypatch.setitem(history.FAST_STAGE_SHA256,unit,None if not count else 'e'*64)
    resource=dict(campaign_id=owner['bundle_context']['stages'][0]['campaign_id'],
        resource_sha256=owner['bundle_context']['manifest_sha256'],bundle_context_sha256=policy.bundle_context_sha256,
        application_expires_epoch_us=history.ORIGINAL_APPLICATION_END,fast_stage_sha256='e'*64,
        fast_stage_deadline_monotonic_us=mono-1)
    detail=dict(claim_count=count,prepared_count=count,start_count=count,resource_count=1 if count else 0,
        full_prepaid_caps=asdict(claimed),approval_bytes_match_enrollment=True,
        enrollment={'history_binding_sha256':owner['history_binding_sha256']},
        prior_plus_prepaid_caps=asdict(local.prior_debt.plus(claimed)),limit=asdict(local.limits),resources=[resource] if count else [])
    observed=dict(status='scoped_rows_observed',parent_id=policy.parent_id,policy_digest=policy.digest,unit=unit,
        policy_expired_at_observation=True,observation_original_epoch_us=epoch,observation_original_monotonic_us=mono,
        expected_database_sha256='d'*64,detail=detail,legacy_scoped_rows_complete=True,legacy_runs=legacy,
        legacy_relation=dict(scoped_linkage_complete=True,native_claims_without_scoped_actuals=[]) if count else None)
    row=dict(owner=owner,database_sha256='d'*64,actual_native_paid_caps=asdict(local.prior_debt.plus(claimed)),
        actual_controller_paid_caps=asdict(local.prior_debt.plus(claimed)),paid_caps=asdict(local.limits),
        conservative_unused_enrollment_caps=asdict(Cost() if not count else Cost(2048,480000000)),runs=runs,
        controller_endpoint_refs=[{'child':r} for r in runs],preparation_sha256={r['run_id']:'a'*64 for r in runs},
        retired_unstarted=[],require_result_complete=False,failed_started_runs=[],
        legacy_sha256=hashlib.sha256(canonical(legacy)).hexdigest() if count else None)
    return unit,policy,observed,row


def test_modeled_partial_pair_uses_dynamic_fast_stage_expiry_and_full_reservation(modeled_costs):
    unit,policy,observed,row=modeled_costs
    rpc.qualify_gfsk250_mb1_partial_costs(unit,policy,observed,row)
    assert row['paid_caps']==asdict(policy.unit(unit).limits) and row['require_result_complete'] is False


@pytest.mark.parametrize('fault',['credit','unused','boolean_count','wrong_run','unstarted','whole_success','database'])
def test_modeled_partial_costs_refuse_refunds_or_completed_whole_claim(modeled_costs,fault):
    unit,policy,observed,row=modeled_costs
    if fault=='credit':row['actual_native_paid_caps']['radio_attempts']-=1
    elif fault=='unused':row['conservative_unused_enrollment_caps']['radio_attempts']+=1
    elif fault=='boolean_count':observed['detail']['start_count']=bool(observed['detail']['start_count'])
    elif fault=='wrong_run':row['runs'].append({'run_id':'f'*32})
    elif fault=='unstarted':row['retired_unstarted'].append({})
    elif fault=='whole_success':row['require_result_complete']=True
    else:row['database_sha256']='f'*64
    with pytest.raises(RuntimeError):rpc.qualify_gfsk250_mb1_partial_costs(unit,policy,observed,row)


@pytest.fixture(params=['unit-a','unit-b','unit-c'])
def actual_h41(request):
    base=Path('/Users/l33tdawg/nodejs-projects/sage-commons/work/meshticulum-history41-inputs-20261013')
    observations=base/'expired-parent-observation-1791873988888371';unit=request.param
    raw=(observations/'COMPLETION.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==history.EXPIRY_COMPLETION_SHA256
    completion=json.loads(raw);raw=(observations/('NATIVE-'+unit+'.json')).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==completion['observation_sha256'][unit]
    native=json.loads(raw);output=base/'resume-history-preparation-41'
    raw=(output/'RECEIPT.json').read_bytes()
    assert hashlib.sha256(raw).hexdigest()==history.NEXT_HISTORY_RECEIPT_SHA256
    receipt=json.loads(raw);raw=(output/(unit+'.json')).read_bytes()
    assert len(raw)<=2500000 and hashlib.sha256(raw).hexdigest()==receipt['payload_sha256'][unit]
    payload=json.loads(raw);row=payload['previous'][-1]
    assert len(payload['previous'])==41 and tuple(r['policy']['parent_id'] for r in payload['previous'][:-1])==history.H40_PREFIX
    return unit,native,row,ParentPolicy.from_dict(row['policy'])


def test_actual_expired_native_costs_and_full_reservation(actual_h41):
    unit,native,row,policy=actual_h41
    assert history.require_final_bindings() is True and history.pending_next_roster()==rpc.HISTORY[:41]
    rpc.qualify_gfsk250_mb1_partial_costs(unit,policy,native['observation'],row)
    assert native['observation']['expected_database_sha256']==history.NATIVE_DATABASE_SHA256[unit]
    assert native['terminal_outcome_sha256']==history.NATIVE_OUTCOME_SHA256[unit]
    assert native['verified_piece_metadata_present'] is False and row['verified_piece_sha256']==history.VERIFIED_PIECE_SHA256[unit]=={}
    assert row['require_result_complete'] is False and row['failed_started_runs']==[]


def test_actual_public_metadata_bytes_and_piece_absence(actual_h41,tmp_path):
    import base64
    unit,native,row,policy=actual_h41
    (tmp_path/'parents'/history.PARENT).mkdir(parents=True)
    for relative,record in native['public_metadata'].items():
        target=tmp_path/relative;target.parent.mkdir(parents=True,exist_ok=True)
        raw=base64.b64decode(record['base64'],validate=True)
        assert hashlib.sha256(raw).hexdigest()==record['sha256'];target.write_bytes(raw)
    class Reader:
        def directory(self,path):assert path.is_dir()
        def read(self,path,maximum):
            raw=path.read_bytes();assert len(raw)<=maximum;return raw
        def check(self):return True
    files=history.metadata_files(Reader(),tmp_path,unit,row)
    assert len(files)==(0 if unit=='unit-b' else 2)
    assert all(name.endswith(('fast-stage-0.json','fast-final-ack.json')) for name in files)
