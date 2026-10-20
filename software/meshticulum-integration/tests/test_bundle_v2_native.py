"""New original v2 contract; protected local SQLite and signed data, modeled host facts only."""
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import json
import os
import sqlite3

import pytest
from lantern_meshticulum.bench import pilot_bundle as bundle,pilot_resume_rpc as rpc,pilot_parent_snapshot as snapshot
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,ParentPolicy,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.bench.pilot_ssh import SSHOperator
from tests.test_pilot_bundle import data,packet
from tests.test_public32_pilot import tmp_path,protected_source,protected_records,clock,facts,BOOT
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_pilot_ssh import config


@pytest.fixture
def v2(data):
    payload,chunks,old,p=data;value=deepcopy(old)
    value.update(schema=bundle.SCHEMA_V2,expires_epoch_us=p.issued_epoch_us+1200000000,
                 deadline_monotonic_us=p.original_monotonic_us+1200000000)
    for i,s in enumerate(value['stages']):
        a=100000000+i*190000000;s.update(issued_epoch_us=a,expires_epoch_us=a+150000000,handoff_end_epoch_us=a+190000000)
    p=replace(p,qualification_kind=bundle.KIND_V2,expires_epoch_us=value['expires_epoch_us'],
              deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=rpc.sha(canonical(value)))
    return payload,chunks,value,p


def records(root,p,value,unit):
    root.mkdir(mode=0o700);p,directory,binding=protected_records(root,p,unit)
    owner=json.loads((directory/'owner.json').read_bytes());captured=json.loads((directory/'capture.json').read_bytes())
    commitment=bundle.public_context_commitment(value,parent_id=p.parent_id)
    phase=dict(epoch_us=clock()[0],monotonic_us=clock()[1],deadline_monotonic_us=clock()[1]+commitment['phase_end_epoch_us']-clock()[0])
    captured.update(schema='meshticulum.private-resume-actual-capture.v2',bundle_capture_commitment=commitment,native_phase=phase)
    owner.update(qualification_kind=bundle.KIND_V2,bundle_context=value,bundle_capture_commitment=commitment,
                 native_phase={unit:phase},capture_record_sha256=rpc.sha(canonical(captured)))
    for n,v in [('owner.json',owner),('capture.json',captured)]:
        (directory/n).write_bytes(canonical(v))
    return p,directory,binding


def test_signed_prepolicy_context_commitment_and_matching_policy(v2):
    _,_,value,p=v2;before=canonical(value);commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    assert canonical(value)==before and commit['context_sha256']==p.bundle_context_sha256
    assert commit['phase_end_epoch_us']-commit['phase_start_epoch_us']==1205000000
    assert bundle.BundleContext(value,p).current(clock(),controller_clock=True)
    assert ParentPolicy.from_dict(p.to_dict())==p


@pytest.mark.parametrize('fault',['v1_schema','v1_kind','whole_epoch','whole_mono','stage151','signature','parent_reuse','context_hash'])
def test_v2_never_relabels_or_extends_an_old_or_unreviewed_context(v2,fault):
    _,_,value,p=v2;v=deepcopy(value)
    if fault=='v1_schema':v['schema']=bundle.SCHEMA
    elif fault=='v1_kind':p=replace(p,qualification_kind=bundle.KIND)
    elif fault=='whole_epoch':v['expires_epoch_us']+=1
    elif fault=='whole_mono':v['deadline_monotonic_us']+=1
    elif fault=='stage151':v['stages'][0]['expires_epoch_us']+=1000000
    elif fault=='signature':v['signature_base64']='e30='
    elif fault=='parent_reuse':v['stages'][0]['campaign_id']=p.parent_id
    else:p=replace(p,bundle_context_sha256='f'*64)
    if fault!='context_hash':p=replace(p,bundle_context_sha256=rpc.sha(canonical(v)))
    with pytest.raises(Exception):bundle.BundleContext(v,p)


def test_v1_original_bytes_policy_digest_and_window_remain_v1(data):
    _,_,value,p=data;raw=canonical(value);body=canonical(p.to_dict());digest=p.digest
    bundle.BundleContext(value,p)
    assert canonical(value)==raw and canonical(ParentPolicy.from_dict(p.to_dict()).to_dict())==body and p.digest==digest
    v=deepcopy(value);v.update(expires_epoch_us=p.issued_epoch_us+1200000000,deadline_monotonic_us=p.original_monotonic_us+1200000000)
    p=replace(p,expires_epoch_us=v['expires_epoch_us'],deadline_monotonic_us=v['deadline_monotonic_us'],bundle_context_sha256=rpc.sha(canonical(v)))
    with pytest.raises(Exception):bundle.BundleContext(v,p)


@pytest.mark.parametrize('fault',[None,'kind','missing','context','phase'])
def test_capture_selection_requires_original_signed_v2_commitment(v2,fault):
    _,_,value,p=v2;commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    request=dict(parent_id=p.parent_id,qualification_kind=bundle.KIND_V2,bundle_context=value,bundle_capture_commitment=commit,
                 phase_start_epoch_us=commit['phase_start_epoch_us'],phase_end_epoch_us=commit['phase_end_epoch_us'])
    if fault=='kind':request['qualification_kind']=bundle.KIND
    elif fault=='missing':request.pop('bundle_capture_commitment')
    elif fault=='context':request['bundle_context']=deepcopy(value);request['bundle_context']['stages'][0]['campaign_id']='e'*32
    elif fault=='phase':request['phase_end_epoch_us']+=1
    if fault is None:assert rpc.capture_request_commitment(request)==commit
    else:
        with pytest.raises(Exception):rpc.capture_request_commitment(request)


def test_actual_native_claims_keep_separate_stage_resources_and_typed_v2_metadata(v2,tmp_path,protected_source,root_observation):
    _,_,value,p=v2;root=tmp_path/'native';p,directory,binding=records(root,p,value,'unit-a');now=[clock()]
    approve=_Public32Approval(directory,p,'unit-a',facts('unit-a'),lambda:now[0])
    checked=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit('unit-a')),prior_snapshot_sha256=p.prior_snapshot_sha256)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=lambda:now[0],facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=approve,prior_check=checked,history_binding_sha256=binding)
    assert caps.close()
    path=tmp_path/'controller';path.mkdir(mode=0o700);owner=ParentCaps.enroll(path/'caps.sqlite',p,clock=lambda:now[0],boot_reader=lambda:BOOT)
    try:
        for index in (0,1):
            epoch=value['stages'][index]['issued_epoch_us'];now[0]=(epoch,800000000+epoch-100000000)
            pair=packet(value,p,index,epoch);permit=owner.prepay_pair(tuple(pair['packages'].values()));children=owner.consume(permit)
            package=deepcopy(pair['packages']['unit-a']);package['schema']='meshticulum.public-pilot-package.v2'
            package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit=='unit-a').to_dict())
            done=provision(package,state=root,facts=facts('unit-a'),clock=lambda:now[0]);assert done['parent']['prepared']
    finally:owner.close()
    with sqlite3.connect(directory/'native.sqlite') as db:
        assert db.execute('SELECT COUNT(*) FROM resources').fetchone()==(2,)
        assert db.execute('SELECT COUNT(*) FROM claims').fetchone()==(2,)
    db.close()
    now[0]=(500000000,1200000000)
    scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',os.geteuid(),p.parent_id,p.digest,rpc.sha((directory/'native.sqlite').read_bytes()),'unit-a',BOOT,510000000)
    observed=snapshot.collect(root,scope,clock=lambda:now[0],boot_reader=lambda:BOOT).to_dict()
    assert observed['status']=='scoped_rows_observed' and observed['detail']['claim_count']==2, {k:v for k,v in observed.items() if 'reason' in k}
    assert all(r['bundle_context_sha256']==p.bundle_context_sha256 for r in observed['detail']['resources'])


@pytest.mark.parametrize('fault',['missing','owner','capture','policy','phase'])
def test_protected_v2_owner_refuses_before_enrollment_or_claim(v2,tmp_path,protected_source,fault):
    _,_,value,p=v2;p,directory,_=records(tmp_path/'native',p,value,'unit-a')
    owner=json.loads((directory/'owner.json').read_bytes());captured=json.loads((directory/'capture.json').read_bytes())
    if fault=='missing':owner.pop('bundle_capture_commitment')
    elif fault=='owner':owner['bundle_capture_commitment']['context_sha256']='f'*64
    elif fault=='capture':captured['bundle_capture_commitment']['original_monotonic_us']+=1;owner['capture_record_sha256']=rpc.sha(canonical(captured))
    elif fault=='policy':p=replace(p,qualification_kind=bundle.KIND)
    else:captured['phase_end_epoch_us']+=1;owner['capture_record_sha256']=rpc.sha(canonical(captured))
    (directory/'owner.json').write_bytes(canonical(owner));(directory/'capture.json').write_bytes(canonical(captured))
    with pytest.raises(Exception):_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)
    assert not (directory/'native.sqlite').exists()


@pytest.mark.parametrize('fault',[None,'legacy','extended','context','parent','mono'])
def test_ssh_v2_is_explicit_and_pins_original_deadline(v2,fault):
    _,_,value,p=v2;cfg,source=config();commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    cfg.update(schema=bundle.SSH_SCHEMA_V2,bundle_parent_id=p.parent_id,bundle_context=value,bundle_capture_commitment=commit,
               handoff_end_epoch_us=commit['phase_end_epoch_us'])
    if fault=='legacy':cfg['schema']='meshticulum.finite-ssh-pilot.v1'
    elif fault=='extended':cfg['handoff_end_epoch_us']+=1
    elif fault=='context':cfg['bundle_capture_commitment']['context_sha256']='f'*64
    elif fault=='parent':cfg['bundle_parent_id']='f'*32
    now_mono=800.0 if fault!='mono' else 799.0
    if fault is None:
        operator=SSHOperator(cfg,provision_source=source,clock=lambda:now_mono,epoch=lambda:100000000)
        assert operator.deadline<=(p.original_monotonic_us+1205000000)/1000000 and not operator.jobs
    else:
        with pytest.raises(Exception):SSHOperator(cfg,provision_source=source,clock=lambda:now_mono,epoch=lambda:100000000)


@pytest.mark.parametrize('fault',[None,'legacy','phase_extend','epoch_expired','mono_expired'])
def test_phase_ceiling_is_selected_only_by_original_v2_commitment(v2,monkeypatch,fault):
    _,_,value,p=v2;commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    now=[(p.issued_epoch_us+1,1000000001)];start=p.issued_epoch_us;end=start+1205000000
    phase=dict(epoch_us=start,monotonic_us=1000000000,deadline_monotonic_us=2205000000)
    monkeypatch.setattr(rpc,'clock',lambda:now[0]);monkeypatch.setattr(rpc,'boot',lambda:BOOT)
    monkeypatch.setattr(rpc,'native_facts',lambda unit:facts(unit))
    if fault=='legacy':commit=None
    elif fault=='phase_extend':end+=1
    elif fault=='epoch_expired':now[0]=(end,1000000001)
    elif fault=='mono_expired':now[0]=(start+1,phase['deadline_monotonic_us'])
    if fault is None:assert rpc.phase_check('unit-a',rpc.stable(facts('unit-a')),start,end,phase,commitment=commit)
    else:
        with pytest.raises(Exception):rpc.phase_check('unit-a',rpc.stable(facts('unit-a')),start,end,phase,commitment=commit)


def test_mutated_original_v2_context_refuses_even_with_live_clock(v2):
    _,_,value,p=v2;context=bundle.BundleContext(value,p)
    context.value['deadline_monotonic_us']+=1
    with pytest.raises(Exception):context.current(clock(),controller_clock=True)


def test_real_revocation_refuses_v2_before_new_native_charge(v2,tmp_path,protected_source):
    _,_,value,p=v2;root=tmp_path/'native';p,directory,binding=records(root,p,value,'unit-a')
    approve=_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)
    checked=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit('unit-a')),prior_snapshot_sha256=p.prior_snapshot_sha256)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=clock,facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=approve,prior_check=checked,history_binding_sha256=binding)
    assert caps.close();(directory/'native.sqlite.approval').write_bytes(b'')
    pair=packet(value,p,0,clock()[0]);control=tmp_path/'controller';control.mkdir(mode=0o700)
    owner=ParentCaps.enroll(control/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:
        permit=owner.prepay_pair(tuple(pair['packages'].values()));children=owner.consume(permit)
        package=deepcopy(pair['packages']['unit-a']);package['schema']='meshticulum.public-pilot-package.v2'
        package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit=='unit-a').to_dict())
        with pytest.raises(Exception):provision(package,state=root,facts=facts('unit-a'),clock=clock)
        assert owner.snapshot()['allocations']==1
    finally:owner.close()
    with sqlite3.connect(directory/'native.sqlite') as db:assert db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)
    db.close();assert not (root/'runs'/pair['run_id']).exists()
