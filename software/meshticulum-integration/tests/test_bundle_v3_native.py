"""New original v3 scope; genuine local records, explicitly modeled host/radio facts."""
from copy import deepcopy
from dataclasses import asdict,replace
import json
import os
import sqlite3
import struct

import pytest
from lantern_meshticulum.bench import pilot_bundle as bundle,pilot_resume_rpc as rpc,pilot_parent_snapshot as snapshot
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,ParentPolicy,canonical,Cost
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval
from lantern_meshticulum.bench.pilot_provision import provision
from lantern_meshticulum.bench.pilot_ssh import SSHOperator
from lantern_meshticulum.bench.profiles import profile_for
from meshticulum.bench.serial_worker import SerialWorker,journal_binding
from meshticulum.bench.serial_protocol import Decoder,Frame,Message
from meshticulum.bench.attempts import AttemptJournal
from meshticulum.bench.linux_hat import HardwareError
from tests.test_pilot_bundle import data,packet
from tests.test_bundle_v2_native import v2
from tests.test_public32_pilot import tmp_path,protected_source,protected_records,clock,facts,BOOT
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_pilot_ssh import config


@pytest.fixture
def v3(data):
    payload,chunks,old,p=data;value=deepcopy(old)
    value.update(schema=bundle.SCHEMA_V3,expires_epoch_us=p.issued_epoch_us+1350000000,
                 deadline_monotonic_us=p.original_monotonic_us+1350000000)
    for i,s in enumerate(value['stages']):
        a=p.issued_epoch_us+(105+i*240)*1000000
        s.update(issued_epoch_us=a,expires_epoch_us=a+180000000,
                 handoff_end_epoch_us=a+240000000 if i<4 else a+190000000)
    p=replace(p,qualification_kind=bundle.KIND_V3,expires_epoch_us=value['expires_epoch_us'],
              deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=rpc.sha(canonical(value)))
    return payload,chunks,value,p


def records(root,p,value,unit):
    root.mkdir(mode=0o700);p,directory,binding=protected_records(root,p,unit)
    owner=json.loads((directory/'owner.json').read_bytes());captured=json.loads((directory/'capture.json').read_bytes())
    commitment=bundle.public_context_commitment(value,parent_id=p.parent_id)
    phase=dict(epoch_us=clock()[0],monotonic_us=clock()[1],deadline_monotonic_us=clock()[1]+commitment['phase_end_epoch_us']-clock()[0])
    captured.update(schema='meshticulum.private-resume-actual-capture.v3',bundle_capture_commitment=commitment,native_phase=phase)
    owner.update(qualification_kind=bundle.KIND_V3,bundle_context=value,bundle_capture_commitment=commitment,
                 native_phase={unit:phase},capture_record_sha256=rpc.sha(canonical(captured)))
    for name,v in [('owner.json',owner),('capture.json',captured)]:
        (directory/name).write_bytes(canonical(v))
    return p,directory,binding


def test_fresh_v3_has_exact_original_geometry_and_same_full_caps(v3):
    _,_,value,p=v3;before=canonical(value);commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    assert canonical(value)==before and commit['context_sha256']==p.bundle_context_sha256
    assert commit['schema']==bundle.COMMITMENT_SCHEMA_V3 and commit['phase_end_epoch_us']-commit['phase_start_epoch_us']==1355000000
    assert all(s['expires_epoch_us']-s['issued_epoch_us']==180000000 for s in value['stages'])
    assert value['stages'][-1]['handoff_end_epoch_us']+60000000==p.issued_epoch_us+1315000000
    assert p.expires_epoch_us-(p.issued_epoch_us+1315000000)==35000000
    assert bundle.BundleContext(value,p).current(clock(),controller_clock=True)
    assert p.unit('unit-a').limits==p.unit('unit-a').prior_debt.plus(Cost(2560,600000000))
    assert p.unit('unit-b').limits==p.unit('unit-b').prior_debt


@pytest.mark.parametrize('fault',['v2_schema','v2_kind','whole_epoch','whole_mono','stage179','stage181','signature','context_hash'])
def test_v3_cannot_upgrade_old_version_or_extend_original_clocks(v3,fault):
    _,_,value,p=v3;v=deepcopy(value)
    if fault=='v2_schema':v['schema']=bundle.SCHEMA_V2
    elif fault=='v2_kind':p=replace(p,qualification_kind=bundle.KIND_V2)
    elif fault=='whole_epoch':v['expires_epoch_us']+=1
    elif fault=='whole_mono':v['deadline_monotonic_us']+=1
    elif fault=='stage179':v['stages'][0]['expires_epoch_us']-=1000000
    elif fault=='stage181':v['stages'][0]['expires_epoch_us']+=1000000
    elif fault=='signature':v['signature_base64']='e30='
    else:p=replace(p,bundle_context_sha256='f'*64)
    if fault!='context_hash':p=replace(p,bundle_context_sha256=rpc.sha(canonical(v)))
    with pytest.raises(Exception):bundle.BundleContext(v,p)


def test_v1_v2_context_policy_and_commitment_bytes_remain_unchanged(data,v2):
    for sample in (data,v2):
        _,_,value,p=sample;raw=canonical(value);body=canonical(p.to_dict());digest=p.digest
        c=bundle.BundleContext(value,p);assert c.raw==raw
        assert canonical(ParentPolicy.from_dict(p.to_dict()).to_dict())==body and p.digest==digest
        if p.qualification_kind==bundle.KIND_V2:
            commitment=bundle.public_context_commitment(value,parent_id=p.parent_id)
            assert commitment['schema']==bundle.COMMITMENT_SCHEMA and commitment['phase_end_epoch_us']==p.issued_epoch_us+1205000000


@pytest.mark.parametrize('fault',[None,'kind','schema','phase','mono'])
def test_capture_requires_explicit_v3_precommitted_original_scope(v3,fault):
    _,_,value,p=v3;commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    request=dict(parent_id=p.parent_id,qualification_kind=bundle.KIND_V3,bundle_context=value,bundle_capture_commitment=commit,
        phase_start_epoch_us=commit['phase_start_epoch_us'],phase_end_epoch_us=commit['phase_end_epoch_us'])
    if fault=='kind':request['qualification_kind']=bundle.KIND_V2
    elif fault=='schema':request['bundle_capture_commitment']['schema']=bundle.COMMITMENT_SCHEMA
    elif fault=='phase':request['phase_end_epoch_us']+=1
    elif fault=='mono':request['bundle_context']['deadline_monotonic_us']+=1
    if fault is None:assert rpc.capture_request_commitment(request)==commit
    else:
        with pytest.raises(Exception):rpc.capture_request_commitment(request)


@pytest.mark.parametrize('fault',[None,'v2_schema','v2_context','extended','mono'])
def test_ssh_v3_schema_and_both_original_clocks_are_bound(v3,v2,fault):
    _,_,value,p=v3;cfg,source=config();commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    cfg.update(schema=bundle.SSH_SCHEMA_V3,bundle_parent_id=p.parent_id,bundle_context=value,bundle_capture_commitment=commit,handoff_end_epoch_us=commit['phase_end_epoch_us'])
    if fault=='v2_schema':cfg['schema']=bundle.SSH_SCHEMA_V2
    elif fault=='v2_context':
        value=v2[2];commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
        cfg.update(bundle_context=value,bundle_capture_commitment=commit,handoff_end_epoch_us=commit['phase_end_epoch_us'])
    elif fault=='extended':cfg['handoff_end_epoch_us']+=1
    mono=p.original_monotonic_us/1000000-(1 if fault=='mono' else 0)
    if fault is None:
        owner=SSHOperator(cfg,provision_source=source,clock=lambda:mono,epoch=lambda:p.issued_epoch_us)
        assert owner.deadline==(p.original_monotonic_us+1355000000)/1000000 and not owner.jobs
    else:
        with pytest.raises(Exception):SSHOperator(cfg,provision_source=source,clock=lambda:mono,epoch=lambda:p.issued_epoch_us)


@pytest.mark.parametrize('fault',[None,'legacy','phase','epoch','mono','wrong_schema'])
def test_v3_phase_guard_keeps_original_epoch_and_native_monotonic(v3,monkeypatch,fault):
    _,_,value,p=v3;commit=bundle.public_context_commitment(value,parent_id=p.parent_id)
    start=p.issued_epoch_us;end=start+1355000000;now=[(start+1,1000000001)]
    phase=dict(epoch_us=start,monotonic_us=1000000000,deadline_monotonic_us=2355000000)
    monkeypatch.setattr(rpc,'clock',lambda:now[0]);monkeypatch.setattr(rpc,'boot',lambda:BOOT);monkeypatch.setattr(rpc,'native_facts',lambda u:facts(u))
    if fault=='legacy':commit=None
    elif fault=='phase':end+=1
    elif fault=='epoch':now[0]=(end,1000000001)
    elif fault=='mono':now[0]=(start+1,phase['deadline_monotonic_us'])
    elif fault=='wrong_schema':commit['schema']=bundle.COMMITMENT_SCHEMA
    if fault is None:assert rpc.phase_check('unit-a',rpc.stable(facts('unit-a')),start,end,phase,commitment=commit)
    else:
        with pytest.raises(Exception):rpc.phase_check('unit-a',rpc.stable(facts('unit-a')),start,end,phase,commitment=commit)


def test_actual_v3_provisioned_claims_and_resources_collect_through_typed_native_parser(v3,tmp_path,protected_source,root_observation):
    _,_,value,p=v3;root=tmp_path/'native';p,directory,binding=records(root,p,value,'unit-a');now=[clock()]
    approve=_Public32Approval(directory,p,'unit-a',facts('unit-a'),lambda:now[0])
    checked=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit('unit-a')),prior_snapshot_sha256=p.prior_snapshot_sha256)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=lambda:now[0],facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=approve,prior_check=checked,history_binding_sha256=binding);assert caps.close()
    control=tmp_path/'controller';control.mkdir(mode=0o700);owner=ParentCaps.enroll(control/'caps.sqlite',p,clock=lambda:now[0],boot_reader=lambda:BOOT)
    try:
        for i in (0,1):
            epoch=value['stages'][i]['issued_epoch_us'];now[0]=(epoch,p.original_monotonic_us+epoch-p.issued_epoch_us)
            pair=packet(value,p,i,epoch);permit=owner.prepay_pair(tuple(pair['packages'].values()));children=owner.consume(permit)
            package=deepcopy(pair['packages']['unit-a']);package['schema']='meshticulum.public-pilot-package.v2'
            package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit=='unit-a').to_dict())
            done=provision(package,state=root,facts=facts('unit-a'),clock=lambda:now[0]);assert done['parent']['prepared']
    finally:owner.close()
    with sqlite3.connect(directory/'native.sqlite') as db:assert db.execute('SELECT COUNT(*) FROM resources').fetchone()==(2,)
    db.close();now[0]=(value['stages'][1]['expires_epoch_us']-1,p.original_monotonic_us+value['stages'][1]['expires_epoch_us']-p.issued_epoch_us-1)
    scope=snapshot.Scope('meshticulum.public-parent-snapshot-scope.v1','native',os.geteuid(),p.parent_id,p.digest,rpc.sha((directory/'native.sqlite').read_bytes()),'unit-a',BOOT,now[0][0]+10000000)
    observed=snapshot.collect(root,scope,clock=lambda:now[0],boot_reader=lambda:BOOT).to_dict()
    assert observed['status']=='scoped_rows_observed' and observed['detail']['claim_count']==2
    assert all(row['bundle_context_sha256']==p.bundle_context_sha256 for row in observed['detail']['resources'])


@pytest.mark.parametrize('fault',['owner','capture_schema','capture_phase'])
def test_wrong_v3_protected_capture_owner_refuses_before_charge(v3,tmp_path,protected_source,fault):
    _,_,value,p=v3;p,directory,_=records(tmp_path/'native',p,value,'unit-a')
    owner=json.loads((directory/'owner.json').read_bytes());capture=json.loads((directory/'capture.json').read_bytes())
    if fault=='owner':owner['qualification_kind']=bundle.KIND_V2
    elif fault=='capture_schema':capture['schema']='meshticulum.private-resume-actual-capture.v2'
    else:capture['phase_end_epoch_us']+=1
    owner['capture_record_sha256']=rpc.sha(canonical(capture))
    for n,v in [('owner.json',owner),('capture.json',capture)]: (directory/n).write_bytes(canonical(v))
    with pytest.raises(Exception):_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)
    assert not (directory/'native.sqlite').exists()


class ArmChannel:
    """Protocol replies only; models the recorded pre-ARM setup cost, emits no RF."""
    def __init__(self):self.time=165.216966;self.inbound=b'';self.writes=[]
    def monotonic(self):return self.time
    def write(self,raw,deadline):
        frame=Decoder().feed(raw)[0];self.writes.append(frame.type)
        if frame.type is Message.HELLO:body=struct.pack('!BBH',1,1,255)+b'mtb1-9816f92488\0';kind=Message.HELLO_REPLY
        elif frame.type is Message.DISARM:self.time+=.490874;body=b'\0';kind=Message.DISARM_REPLY
        else:assert frame.type is Message.ARM;body=frame.body;kind=Message.ARM_REPLY
        self.inbound+=Frame(kind,b'b'*16,frame.request,body).encode()
    def read(self,timeout):raw=self.inbound;self.inbound=b'';return raw


@pytest.mark.parametrize('stage_end,lease_ms,accept',[(255.,89105,True),(285.,90000,True),(165.7,89105,False)])
def test_serial_ARM_clips_recorded_setup_preserves_fresh_headroom_and_refuses_expiry(tmp_path,stage_end,lease_ms,accept):
    channel=ArmChannel();worker=SerialWorker(channel);identity=worker.discover(expected_board_id=1,expected_version='mtb1-9816f92488')
    profile=replace(profile_for('sf7-500'),lease_ms=lease_ms);context=b'c'*32
    journal=AttemptJournal(tmp_path/'attempts.sqlite',binding=journal_binding(context,identity,profile),max_attempts=256,max_airtime_us=60000000,create=True,request_clock=lambda:100000000)
    original_body=profile.encode(board_id=1);original_binding=journal.binding
    try:
        if accept:
            worker.arm(journal=journal,profile=profile,context_digest=context,authorize=lambda:True,authority_deadline=stage_end)
            assert worker.armed_until<worker.arm_lease_end_upper_bound<stage_end and worker.deadline==stage_end
            assert Message.ARM in channel.writes and 1000<=worker.arm_lease_ms<=lease_ms
            assert (worker.arm_lease_ms<lease_ms) is (stage_end==255.)
        else:
            with pytest.raises(HardwareError,match='radio_authority_expired'):worker.arm(journal=journal,profile=profile,context_digest=context,authorize=lambda:True,authority_deadline=stage_end)
            assert Message.ARM not in channel.writes and worker.fenced
        assert Message.TRANSMIT not in channel.writes and journal.snapshot()['attempts']==0
        assert worker.profile is profile and profile.encode(board_id=1)==original_body and journal.binding==original_binding
        assert journal.max_attempts==256 and journal.max_airtime_us==60000000
        assert journal.snapshot()['reserved_estimated_airtime_us']==0
    finally:journal.close()
