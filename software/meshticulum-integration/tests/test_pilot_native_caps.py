# SPDX-License-Identifier: Apache-2.0
"""Protected SQLite claim/start controls with explicitly approved model facts.

No native host, radio, socket or service is contacted. A local subprocess proves
that prepared claims persist and start tombstones remain one-use across owners.
"""
import base64
from copy import deepcopy
from dataclasses import asdict,replace
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from meshticulum.contracts.identity import PeerID
from meshticulum.resources import resource
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_parent_caps import Cost,UnitCaps,ParentPolicy,child_caps
from lantern_meshticulum.bench.pilot_native_caps import (
    NativeCaps,NativeCapsError,NativeFacts,open_for_provision,open_for_run,
)

BOOT='00000000-0000-4000-8000-000000000001'
OTHER_BOOT='00000000-0000-4000-8000-000000000002'
PEERS={u:dict(chain_id='native-parent-source-controls',agent_id=c*64) for u,c in zip(('unit-a','unit-b'),'ab')}
REQUEST=resource.encode_request('/packs/'+'e'*64+'/chunks/'+'f'*64+'.json',0,16356)
LIMIT=Cost(1024,240000000,8192,4194304,4194304)


def policy():
    units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),LIMIT,Cost(1,1000,1,32,32),c*64)for u,c in zip(PEERS,'ab'))
    return ParentPolicy('1'*32,BOOT,90000000,200000000,40000000,150000000,units,LIMIT.plus(LIMIT),'c'*64)


class Observers:
    def __init__(self,p,unit='unit-a'):
        self.epoch,self.mono=100000000,800000000
        self.calls=0;self.hook=None;self.approvals=0;self.approve_hook=None
        self.allowed=True;self.prior_allowed=True;self.boot=BOOT;self.unit=unit;self.peer=p.unit(unit).peer
    def clock(self):
        self.calls+=1
        if self.hook:self.hook(self.calls)
        return self.epoch,self.mono
    def facts(self):
        if hasattr(self,'facts_hook'):self.facts_hook()
        return NativeFacts(self.unit,self.boot,self.peer)
    def approve(self,binding):
        self.approvals+=1
        if self.approve_hook:self.approve_hook(self.approvals,json.loads(binding))
        return self.allowed
    def prior(self,binding):
        value=json.loads(binding)
        assert value['unit']['prior_snapshot_sha256'] and value['prior_snapshot_sha256']
        return self.prior_allowed


def packet(p,transport='tcp',*,expiry=180000000,campaign='d'*32):
    pair=pair_packages(campaign_id=campaign,resource_sha256='e'*64,application_expires_epoch_us=expiry,
        sender='unit-a',receiver='unit-b',transport=transport,format='MR1.request',
        descriptor=dict(bytes=len(REQUEST),sha256=hashlib.sha256(REQUEST).hexdigest()),source=None,request=REQUEST,
        peers={u.unit:asdict(u.peer)for u in p.units},boots={u.unit:u.host_boot_id for u in p.units},
        now_epoch_us=100000000,endpoints={'unit-a':('127.0.0.1',35001),'unit-b':('127.0.0.1',35002)},
        radio_permission=dict(country='MY',antenna_confirmed=True))
    result=deepcopy(pair['packages']['unit-a']);child=child_caps(result,p).to_dict()
    result['schema']='meshticulum.public-pilot-package.v2'
    result['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,
        policy_digest=p.digest,allocation_id='a'*64,child=child)
    return result


def open_direct(path,p,obs):
    return NativeCaps.open(path,p,unit=obs.unit,clock=obs.clock,facts=obs.facts,approve=obs.approve)


def enroll(path,p,obs):
    return NativeCaps.enroll(path,p,unit=obs.unit,clock=obs.clock,facts=obs.facts,approve=obs.approve,prior_check=obs.prior)


def prepared(caps,p,transport='tcp'):
    package=packet(p,transport);preparation=caps.prepare_claim(package)
    plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=preparation.application_deadline_monotonic_us-1000
    receipt=caps.finalize_provision(preparation,plan)
    return package,plan,receipt


@pytest.fixture
def state(tmp_path):
    p=policy();obs=Observers(p);root=tmp_path/'state';root.mkdir(mode=0o700)
    parents=root/'parents';parents.mkdir(mode=0o700);directory=parents/p.parent_id;directory.mkdir(mode=0o700)
    path=directory/'native.sqlite';caps=enroll(path,p,obs)
    yield caps,obs,p,path,root
    obs.hook=obs.approve_hook=None;caps.close()


@pytest.mark.parametrize('transport,cost',[('tcp',Cost(0,0,2048,1048576,1048576)),('lora',Cost(256,60000000))])
def test_full_local_caps_precede_provision_and_exact_prepared_start_persists(state,transport,cost):
    caps,obs,p,path,root=state;package=packet(p,transport);prep=caps.prepare_claim(package)
    snap=caps.snapshot();assert snap['claims']==1 and snap['prepared']==snap['starts']==0
    assert snap['debt']==asdict(p.unit('unit-a').prior_debt.plus(cost))
    assert not (root/'runs').exists() and not (root/'campaigns').exists()
    assert prep.application_deadline_monotonic_us==880000000 # Native, not controller's 150000000.
    plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us-1000
    receipt=caps.finalize_provision(prep,plan)
    assert receipt['child_caps']==package['parent']['child'] and receipt['prepared'] is True
    assert receipt['native_plan_sha256']==hashlib.sha256(__import__('lantern_meshticulum.bench.pilot_parent_caps',fromlist=['canonical']).canonical(plan)).hexdigest()
    assert caps.close()
    other=open_direct(path,p,obs)
    try:
        assert other.current() and other.snapshot()['debt']==snap['debt']
        start=other.consume_start(prep.run_id,plan,base64.b64decode(package['secret_base64']))
        assert start['started'] is True and other.snapshot()['starts']==1
        assert other.current()
    finally:other.close()
    again=open_direct(path,p,obs)
    try:
        with pytest.raises(NativeCapsError):again.consume_start(prep.run_id,plan,base64.b64decode(package['secret_base64']))
        assert again.snapshot()['starts']==1 and again.snapshot()['debt']==snap['debt']
    finally:again.close()


def test_separate_process_consumes_prepared_claim_once_without_recovered_live_permit(state):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p);assert caps.close()
    payload=dict(state=str(root),facts=dict(unit='unit-a',host_boot_id=BOOT),package=package,plan=plan)
    script='''import base64,json,sys
from lantern_meshticulum.bench.pilot_native_caps import open_for_provision
x=json.load(sys.stdin);caps=open_for_provision(x['state'],x['facts'],lambda:(100000000,800000000),x['package'])
try:
 result=caps.consume_start(x['package']['grant']['session']['session_id'],x['plan'],base64.b64decode(x['package']['secret_base64']))
 print(json.dumps({'started':result['started'],'starts':caps.snapshot()['starts']}))
finally:caps.close()
'''
    run=subprocess.run([sys.executable,'-c',script],input=json.dumps(payload),text=True,capture_output=True,timeout=5)
    assert run.returncode==0,run.stderr
    assert json.loads(run.stdout)==dict(started=True,starts=1)
    other=open_direct(path,p,obs)
    try:
        with pytest.raises(NativeCapsError):other.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
        assert other.snapshot()['starts']==1
    finally:other.close()


def test_existing_protected_adapters_never_create_or_approve_from_package(tmp_path):
    root=tmp_path/'state';root.mkdir(mode=0o700);p=policy();package=packet(p)
    with pytest.raises(NativeCapsError):open_for_provision(root,dict(unit='unit-a',host_boot_id=BOOT),lambda:(100000000,800000000),package)
    assert list(root.iterdir())==[]
    with pytest.raises(NativeCapsError):open_direct(root/'missing.sqlite',p,Observers(p))
    assert list(root.iterdir())==[]


@pytest.mark.parametrize('denied',['prior','approval'])
def test_explicit_enrollment_requires_independent_prior_and_approval(tmp_path,denied):
    directory=tmp_path/'private';directory.mkdir(mode=0o700);p=policy();obs=Observers(p)
    if denied=='prior':obs.prior_allowed=False
    else:obs.allowed=False
    path=directory/'native.sqlite'
    with pytest.raises(NativeCapsError):enroll(path,p,obs)
    with pytest.raises(NativeCapsError):open_direct(path,p,Observers(p))
    assert Path(str(path)+'.approval').read_bytes()==b''


def test_local_enforcement_refuses_unpartitioned_aggregate_budget(tmp_path):
    directory=tmp_path/'private';directory.mkdir(mode=0o700);p=policy()
    p=replace(p,aggregate_limits=Cost(1024,240000000,8192,4194304,4194304))
    with pytest.raises(NativeCapsError):enroll(directory/'native.sqlite',p,Observers(p))
    assert list(directory.iterdir())==[]


@pytest.mark.parametrize('change',['parent','policy','child','unit','v1'])
def test_package_metadata_cannot_substitute_native_authority_or_cost(state,change):
    caps,obs,p,path,root=state;package=packet(p)
    if change=='parent':package['parent']['parent_id']='9'*32
    elif change=='policy':package['parent']['policy_digest']='9'*64
    elif change=='child':package['parent']['child']['cost']['ip_frames']=1
    elif change=='unit':package['unit']='unit-b'
    else:package.pop('parent');package['schema']='meshticulum.public-pilot-package.v1'
    with pytest.raises(NativeCapsError):caps.prepare_claim(package)
    assert caps.snapshot()['claims']==0 and caps.snapshot()['debt']==asdict(p.unit('unit-a').prior_debt)


def test_original_resource_attachment_survives_new_campaign_and_refuses_expiry_renewal(state):
    caps,obs,p,path,root=state;first=caps.prepare_claim(packet(p));deadline=first.application_deadline_monotonic_us
    obs.epoch+=20000000;obs.mono+=40000000
    later=caps.prepare_claim(packet(p,campaign='8'*32))
    assert later.application_deadline_monotonic_us==deadline
    assert caps.db.execute('SELECT COUNT(*) FROM resources').fetchone()==(1,)
    before=caps.snapshot()
    with pytest.raises(NativeCapsError):caps.prepare_claim(packet(p,campaign='9'*32,expiry=181000000))
    assert caps.snapshot()['debt']==before['debt'] and caps.snapshot()['claims']==2


def test_unfinalized_claim_is_debt_and_cannot_be_recovered_to_start(state):
    caps,obs,p,path,root=state;package=packet(p);prep=caps.prepare_claim(package);before=caps.snapshot();assert caps.close()
    other=open_direct(path,p,obs)
    try:
        plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us
        with pytest.raises(NativeCapsError):other.consume_start(prep.run_id,plan,base64.b64decode(package['secret_base64']))
        assert other.snapshot()['claims']==1 and other.snapshot()['starts']==0 and other.snapshot()['debt']==before['debt']
    finally:other.close()


@pytest.mark.parametrize('phase',['prepare','finalize','start'])
@pytest.mark.parametrize('committed',[False,True])
def test_unknown_commit_retains_tombstones_and_fences_reopen(state,monkeypatch,phase,committed):
    caps,obs,p,path,root=state;package=packet(p);prep=plan=receipt=None
    if phase!='prepare':
        prep=caps.prepare_claim(package);plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us
    if phase=='start':receipt=caps.finalize_provision(prep,plan)
    original=caps._commit
    def lost_return():
        if committed:original()
        raise OSError('injected commit return loss')
    monkeypatch.setattr(caps,'_commit',lost_return)
    with pytest.raises(NativeCapsError):
        if phase=='prepare':caps.prepare_claim(package)
        elif phase=='finalize':caps.finalize_provision(prep,plan)
        else:caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    snap=caps.snapshot();assert caps.pending.exists() and snap['uncertain'];assert caps.close() is False
    other=open_direct(path,p,obs)
    try:
        assert other.snapshot()==snap and not other.current()
        with pytest.raises(NativeCapsError):other.prepare_claim(packet(p,campaign='7'*32))
        assert other.snapshot()==snap
    finally:other.close()


def test_last_start_clock_expiry_refuses_result_after_durable_one_use_start(state):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p);before=caps.snapshot();target=obs.calls+4
    def expire_last(count):
        if count==target:obs.epoch=180000000;obs.mono=880000000
    obs.hook=expire_last
    with pytest.raises(NativeCapsError):caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    obs.hook=None
    assert obs.epoch<p.expires_epoch_us and obs.mono<caps._enrollment['deadline_monotonic_us']
    snap=caps.snapshot();assert snap['starts']==1 and snap['debt']==before['debt'] and snap['uncertain']
    assert caps.pending.exists() and not caps.current()


@pytest.mark.parametrize('phase',['prepare','finalize','start'])
def test_final_clock_close_preserves_original_charges_and_unknown_fence(state,phase):
    caps,obs,p,path,root=state;package=packet(p);prep=plan=receipt=None
    if phase!='prepare':
        prep=caps.prepare_claim(package);plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us
    if phase=='start':receipt=caps.finalize_provision(prep,plan)
    target=obs.calls+4
    def close_last(count):
        if count==target:assert caps.close() is False
    obs.hook=close_last
    with pytest.raises(NativeCapsError):
        if phase=='prepare':caps.prepare_claim(package)
        elif phase=='finalize':caps.finalize_provision(prep,plan)
        else:caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    obs.hook=None;assert caps.pending.exists();caps.close();other=open_direct(path,p,obs)
    try:
        snap=other.snapshot();assert snap['claims']==1 and snap['uncertain']
        assert snap['debt']['ip_frames']==2049 and not other.current()
    finally:other.close()


@pytest.mark.parametrize('change',['plan','secret','deadline'])
def test_start_cannot_substitute_actual_native_descriptor_key_or_deadline(state,change):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p);secret=base64.b64decode(package['secret_base64'])
    if change=='plan':plan['limits']['max_frames']+=1
    elif change=='secret':secret=b'X'*32
    else:plan['application_deadline_monotonic_us']+=1
    with pytest.raises(NativeCapsError):caps.consume_start(receipt['run_id'],plan,secret)
    assert caps.snapshot()['starts']==0 and caps.snapshot()['debt']['ip_frames']==2049


def test_preparation_cannot_be_replayed_after_finalize(state):
    caps,obs,p,path,root=state;package=packet(p);prep=caps.prepare_claim(package)
    plan=deepcopy(package['grant']);plan['application_deadline_monotonic_us']=prep.application_deadline_monotonic_us
    caps.finalize_provision(prep,plan)
    with pytest.raises(NativeCapsError):caps.finalize_provision(prep,plan)
    assert caps.snapshot()['prepared']==1 and caps.snapshot()['debt']['ip_frames']==2049


def test_revoked_protected_approval_refuses_start_and_never_refunds(state):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p)
    os.ftruncate(caps._fds['approval'],0)
    assert not caps.current()
    with pytest.raises(NativeCapsError):caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    assert caps.db.execute('SELECT COUNT(*) FROM starts').fetchone()==(0,)
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(1,)


@pytest.mark.parametrize('target,field', [
    ('policy','parent_id'), ('policy','expires_epoch_us'), ('unit','host_boot_id'),
    ('peer','chain_id'), ('peer','agent_id'),
] + [(target, field) for target in ('limits','prior','aggregate')
     for field in asdict(Cost())])
def test_current_reads_every_nested_frozen_policy_value(state,target,field):
    caps,obs,p,path,root=state
    objects={'policy':p,'unit':p.units[0],'peer':p.units[0].peer,
             'limits':p.units[0].limits,'prior':p.units[0].prior_debt,'aggregate':p.aggregate_limits}
    value=objects[target];original=getattr(value,field)
    object.__setattr__(value,field,original+1 if type(original) is int else original+'changed')
    try:assert not caps.current()
    finally:object.__setattr__(value,field,original)
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)


@pytest.mark.parametrize('observer,change', [
    ('facts','policy_cost'), ('clock','enrollment_cost'), ('approval','enrollment_deadline'),
    ('approval','policy_type'), ('approval','enrollment_type'),
])
def test_callback_mutation_cannot_reuse_captured_values_or_refund(state,observer,change):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p)
    caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    before=caps.snapshot();restore=[]
    def mutate():
        if restore:return
        if change.startswith('policy'):
            target=p.units[0].limits;name='radio_attempts';original=getattr(target,name)
            object.__setattr__(target,name,float(original) if change=='policy_type' else original+1)
            restore.append(lambda:object.__setattr__(target,name,original))
        else:
            target=caps._enrollment if change=='enrollment_deadline' else caps._enrollment['policy']['units'][0]['limits']
            name='deadline_monotonic_us' if change=='enrollment_deadline' else 'radio_attempts'
            original=target[name];target[name]=float(original) if change=='enrollment_type' else original+1
            restore.append(lambda:target.__setitem__(name,original))
    if observer=='facts':
        # Supply the genuine observer at construction through its retained hook.
        obs.facts_hook=mutate
    elif observer=='clock':obs.hook=lambda _:mutate()
    else:obs.approve_hook=lambda *_:mutate()
    try:assert not caps.current()
    finally:
        for action in restore:action()
        obs.hook=obs.approve_hook=None
        if observer=='facts':del obs.facts_hook
    after=caps.snapshot()
    assert after['claims']==after['prepared']==after['starts']==1
    assert after['debt']==before['debt'] and after['uncertain'] and not caps.current()


@pytest.mark.parametrize('change', ['extra','missing','container','bool','cached_state','cached_bytes','digest'])
def test_current_refuses_enrollment_shape_type_and_baseline_replacement(state,change):
    caps,obs,p,path,root=state;record=caps._enrollment
    if change=='extra':record['unexpected']=None
    elif change=='missing':record.pop('unit')
    elif change=='container':record['policy']['units']=list(record['policy']['units'])
    elif change=='bool':record['policy']['units'][0]['limits']['radio_attempts']=True
    elif change=='cached_state':caps._enrollment_state=tuple(list(caps._enrollment_state))
    elif change=='cached_bytes':caps._enrollment_bytes=bytes(bytearray(caps._enrollment_bytes))
    else:caps._policy_digest='9'*64
    assert not caps.current()
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(0,)


@pytest.mark.parametrize('table', ['claims','resources'])
def test_current_still_reads_all_protected_sql_bodies(state,table):
    caps,obs,p,path,root=state;caps.prepare_claim(packet(p))
    caps.db.execute('DROP TRIGGER immutable_'+table+'_UPDATE')
    caps.db.execute('UPDATE '+table+' SET body=?',(b'{}',))
    assert not caps.current() and caps._uncertain
    assert caps.db.execute('SELECT COUNT(*) FROM claims').fetchone()==(1,)


def test_current_retains_all_live_observer_and_file_check_boundaries(state,monkeypatch):
    caps,obs,p,path,root=state;checks=[];available=caps._available
    def checked():checks.append(True);available()
    monkeypatch.setattr(caps,'_available',checked)
    before=obs.calls,obs.approvals
    assert caps.current()
    assert len(checks)==9 and (obs.calls-before[0],obs.approvals-before[1])==(2,1)


def test_reentry_caught_before_outer_commit_cannot_rollback_or_return_success(state,monkeypatch):
    caps,obs,p,path,root=state;package=packet(p);commit=caps._commit;seen=[]
    def nested():
        with pytest.raises(NativeCapsError):caps.prepare_claim(package)
        seen.append(caps.db.in_transaction);commit()
    monkeypatch.setattr(caps,'_commit',nested)
    with pytest.raises(NativeCapsError):caps.prepare_claim(package)
    assert seen==[True] and caps.snapshot()['claims']==1 and caps.pending.exists()


def test_pending_source_plan_mutation_after_commit_cannot_return_a_start(state):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p);target=obs.approvals+2
    def replace_after_commit(count,binding):
        if count==target:plan['limits']['max_frames']+=1
    obs.approve_hook=replace_after_commit
    with pytest.raises(NativeCapsError):caps.consume_start(receipt['run_id'],plan,base64.b64decode(package['secret_base64']))
    obs.approve_hook=None
    assert caps.snapshot()['starts']==1 and caps.pending.exists() and not caps.current()


def test_wrong_thread_and_pid_close_cannot_fence_real_owner(state,monkeypatch):
    caps,obs,p,path,root=state;results=[]
    t=threading.Thread(target=lambda:results.append(caps.close()));t.start();t.join(2)
    assert not t.is_alive() and results==[False] and not caps._closed
    with monkeypatch.context() as patch:
        pid=os.getpid();patch.setattr(os,'getpid',lambda:pid+1)
        assert caps.close() is False
    assert not caps._closed and caps.current()


def test_existing_run_adapter_verifies_actual_prepared_record_and_rejects_receipt_forgery(state):
    caps,obs,p,path,root=state;package,plan,receipt=prepared(caps,p);assert caps.close()
    runs=root/'runs';runs.mkdir(mode=0o700);directory=runs/receipt['run_id'];directory.mkdir(mode=0o700)
    provision=dict(schema='meshticulum.provisioned-public-pilot.v2',unit='unit-a',run_id=receipt['run_id'],parent=receipt)
    target=directory/'provision.json';target.write_text(json.dumps(provision));target.chmod(0o600)
    facts=dict(unit='unit-a',host_boot_id=BOOT)
    other=open_for_run(root,directory,facts,obs.clock);assert other.current();assert other.close()
    provision['parent']['native_plan_sha256']='9'*64;target.write_text(json.dumps(provision))
    with pytest.raises(NativeCapsError):open_for_run(root,directory,facts,obs.clock)


def test_native_boot_deadline_and_lifetime_inode_cannot_be_rebound(state):
    caps,obs,p,path,root=state;package=packet(p);caps.prepare_claim(package)
    obs.boot=OTHER_BOOT
    with pytest.raises(NativeCapsError):caps.prepare_claim(packet(p,campaign='7'*32))
    obs.boot=BOOT;assert caps.snapshot()['claims']==1
    assert caps.close() is False;other=open_direct(path,p,obs)
    try:
        obs.mono=other._enrollment['deadline_monotonic_us']
        with pytest.raises(NativeCapsError):other.prepare_claim(packet(p,campaign='8'*32))
        assert other.snapshot()['claims']==1
    finally:other.close()
