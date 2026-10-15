"""Actual controller handoff + explicit modeled native transport controls."""
from copy import deepcopy
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
import json
import sys
import time
import shutil
import fcntl

import pytest
from meshticulum.resources import resource
from meshticulum.bench.wire import WireError
from lantern_meshticulum.bench import bundle_checkpoint_root as root,bundle_checkpoint_adapter as adapter,pilot_resume_rpc as rpc
from lantern_meshticulum.bench.pilot_bundle import BundleContext,BundleCampaign
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps
from lantern_meshticulum.bench.pilot_controller import pair_packages
from tests.test_pilot_bundle import data,packet,PEERS,BOOT
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_public32_pilot import tmp_path
from tests.test_pilot_ssh import config
from lantern_meshticulum.bench.pilot_ssh import SSHOperator


@pytest.fixture
def ledger(data,tmp_path,root_observation):
    _,chunks,value,policy=data;context=BundleContext(value,policy);now=[(101000000,802000000)]
    clock=lambda:now[0];boot=lambda:BOOT
    directory=tmp_path/'controller';directory.mkdir(mode=0o700)
    parent=ParentCaps.enroll(directory/'caps.sqlite',policy,clock=clock,boot_reader=boot)
    request_pair=packet(value,policy,0,now[0][0]);request,entry=context.exchange(0);part=chunks[entry['chunk_sha256']]
    response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(part),0)+part
    reply=pair_packages(campaign_id=value['stages'][0]['campaign_id'],resource_sha256=value['manifest_sha256'],
        application_expires_epoch_us=value['stages'][0]['expires_epoch_us'],sender='unit-c',receiver='unit-a',transport='lora',format='MR1.response',
        descriptor=dict(bytes=len(response),sha256=root.sha(response)),source=dict(kind='served_public_chunk',request_run_id=request_pair['run_id']),
        request=None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0][0],
        radio_permission={'country':'MY','antenna_confirmed':True},public32=True)
    for pair in (request_pair,reply):parent.consume(parent.prepay_pair(tuple(pair['packages'].values())))
    try:yield parent,context,now
    finally:
        if not parent._closed:parent.close()


def test_real_owned_controller_close_typed_collect_and_same_ledger_reopen(ledger):
    parent,context,_=ledger;policy=parent.policy;clock,boot=parent._clock,parent._boot
    debt=parent.snapshot();ids=dict(parent._ids)
    reopened,observed=root.controller_observation(parent,context,1)
    try:
        assert parent._closed and reopened is not parent and reopened.policy is policy
        assert reopened._clock is clock and reopened._boot is boot and reopened._ids==ids
        assert observed['detail']['allocation_count']==2 and observed['pending_unknown'] is False
        assert reopened.snapshot()==debt and reopened.current() is True
        assert observed['database_writes'] is False and observed['refund_permitted'] is False
    finally:assert reopened.close()


@pytest.mark.parametrize('fault',('clock','boot','pending','foreign_context','close_refusal','projection'))
def test_wrong_original_clock_owner_or_real_ledger_projection_stops_handoff(ledger,monkeypatch,fault):
    parent,context,now=ledger;original_close=ParentCaps.close
    if fault=='clock':now[0]=(150000000,851000000)
    elif fault=='boot':parent._boot=lambda:'foreign'
    elif fault=='pending':parent.pending.write_bytes(b'unknown');parent.pending.chmod(0o600)
    elif fault=='foreign_context':context=deepcopy(context)
    elif fault=='close_refusal':monkeypatch.setattr(ParentCaps,'close',lambda _:False)
    else:
        # The real collector sees the genuine scoped allocations; a tamper of
        # their declared count must not reopen an owner for further credit.
        actual=root.collect
        class Changed:
            def __init__(self,snapshot):self.value=snapshot.to_dict();self.value['detail']['allocation_count']=1
            def to_dict(self):return self.value
        monkeypatch.setattr(root,'collect',lambda *a,**k:Changed(actual(*a,**k)))
    try:
        with pytest.raises(Exception):root.controller_observation(parent,context,1)
    finally:original_close(parent)


@pytest.mark.parametrize('action,radio',(('checkpoint-audit',False),('store-handoff',False),('checkpoint',True)))
def test_explicit_checkpoint_envelope_never_claims_radio_closed(action,radio):
    assert rpc.response_metadata(action,'unit-a',{})['radio_opened'] is radio
    with pytest.raises(RuntimeError):rpc.response_metadata(action,'unit-a',{'radio_opened':not radio})


def test_missing_original_optin_refuses_before_loading_source_or_opening_device(data,monkeypatch):
    _,_,context,policy=data
    monkeypatch.setattr(adapter.checkpoint_api,'load_primitives',lambda _:pytest.fail('primitives reached'))
    with pytest.raises(RuntimeError):adapter.open_serial('unit-a',policy,{'bundle_context':context},1)


@pytest.fixture
def historical_h42_checkpoint(monkeypatch):
    """Exercise the expired H42 contract under an explicit source-only model."""
    actual = adapter.checkpoint_api.HISTORY
    assert len(actual) == 46
    assert actual[42] == 'bea52c6aa6a5a95501997a46247b514f' and actual[43] == 'd1fd73cd772ff055140f17ca986a07d0' and actual[44] == 'e2449e5bcd4af15d45f7a756dff7d997' and actual[-1] == '360514104bf80fa187276f24d6aa9d9c'
    monkeypatch.setattr(adapter.checkpoint_api, 'HISTORY', actual[:42])
    return actual[:42]


@pytest.mark.parametrize('count',(42,43,44,45,46))
def test_original_h42_checkpoint_contract_stays_closed_with_current_h46(data,count,monkeypatch):
    _,_,value,policy=data
    assert len(adapter.checkpoint_api.HISTORY) == 46
    owner=dict(bundle_context=value,bundle_radio_checkpoint=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',
        producer_sha256='a'*64,primitives_sha256=adapter.checkpoint_api.PRIMITIVES_SHA,
        retained_history_count=count,maximum_checkpoints=4))
    monkeypatch.setattr(adapter.checkpoint_api,'load_primitives',lambda _:pytest.fail('primitives reached'))
    with pytest.raises(RuntimeError,match='original_checkpoint_owner_optin_required'):
        adapter.open_serial('unit-a',policy,owner,1)


@pytest.mark.parametrize('mutation',('old_count','previous_count','wrong_hash','wrong_limit','bool_count'))
def test_original_owner_checkpoint_descriptor_is_explicit_and_typed(data,mutation,historical_h42_checkpoint):
    _,_,value,policy=data
    owner=dict(bundle_context=value,bundle_radio_checkpoint=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',
        producer_sha256='a'*64,primitives_sha256=adapter.checkpoint_api.PRIMITIVES_SHA,retained_history_count=42,maximum_checkpoints=4))
    assert adapter.context_for('unit-a',policy,owner,1)
    key,new={'old_count':('retained_history_count',22),'previous_count':('retained_history_count',38),'wrong_hash':('producer_sha256','not-a-sha'),
        'wrong_limit':('maximum_checkpoints',5),'bool_count':('retained_history_count',True)}[mutation]
    owner['bundle_radio_checkpoint'][key]=new
    with pytest.raises(RuntimeError):adapter.context_for('unit-a',policy,owner,1)


@pytest.fixture
def producer(ledger,monkeypatch):
    parent,context,now=ledger;cfg,source=config();cfg['handoff_end_epoch_us']=context.value['expires_epoch_us']
    events=[]
    def local_argv(self,unit,command,**kwargs):
        # Genuine Job/Popen/wait/reap, fixed modeled metadata only. No SSH.
        assert command[4] is root.REMOTE_TIMEOUT_ENTRY
        action=command[-6];value=dict(schema=rpc.CONTRACT_SCHEMA,unit=unit,action=action,
            radio_opened=action=='checkpoint',private_production=False,global_debt_complete=False,
            checkpoint_callback_completed=True,checkpoint_remote_end_epoch_us=int(command[-2]),
            checkpoint_callback_finished_epoch_us=now[0][0])
        events.append((unit,action))
        return [sys.executable,'-c','print('+repr(json.dumps(value))+')']
    monkeypatch.setattr(SSHOperator,'_argv',local_argv)
    operator=SSHOperator(cfg,provision_source=source,clock=lambda:now[0][1]/1000000,epoch=lambda:now[0][0])
    campaign=BundleCampaign(operator,parent,context.value,peers=PEERS,boots={u:BOOT for u in PEERS},
        handoff_deadline_monotonic=operator.deadline,clock=operator.clock,epoch=operator.epoch,sleep=lambda _:None,lora_sender_first=True)
    owners={u:dict(bundle_radio_checkpoint={'producer_sha256':root.source_pin()},
        runtime_sha256=cfg['runtime_sha256'],manifest_sha256=cfg['manifest_sha256'],
        native_phase={u:dict(epoch_us=now[0][0],monotonic_us=now[0][1],deadline_monotonic_us=context.value['deadline_monotonic_us'])}) for u in cfg['units']}
    value=root.CheckpointProducer(operator,campaign,owners,{u:{} for u in cfg['units']},{u:{} for u in cfg['units']},rpc_sha256='f'*64)
    return value,events


def test_real_local_callback_processes_exit_and_close_before_returned_metadata(producer):
    value,events=producer
    results,inputs,states=value._jobs([(u,'checkpoint-audit',{}) for u in ('unit-a','unit-c')],1)
    assert len(events)==2 and set(results)==set(inputs)=={'unit-a','unit-c'}
    assert all(v['returncode']==0 and type(v['returncode']) is int and v['closed'] is True
               and v['remote_terminal_proven'] is True for v in states)


@pytest.mark.parametrize('fault',('owner','config','entry','context','clock','callback'))
def test_producer_original_refs_source_and_original_clock_refuse_before_process(producer,fault):
    value,events=producer
    if fault=='owner':value.owners['unit-a']['runtime_sha256']='0'*64
    elif fault=='config':value.operator.config['runtime_sha256']='0'*64
    elif fault=='entry':value.rpc_entry='print("forged")'
    elif fault=='context':value.campaign.context=deepcopy(value.campaign.context)
    elif fault=='clock':value.campaign.epoch=lambda:150000000
    else:value.operator._argv=lambda *a,**k:pytest.fail('foreign callback')
    with pytest.raises(Exception):value._jobs([('unit-a','checkpoint-audit',{})],1)
    assert events==[]


@pytest.mark.parametrize('fault',('missing_completion','timeout_exit','false_completion'))
def test_actual_local_reap_cannot_promote_unproved_remote_callback(producer,monkeypatch,fault):
    value,_=producer
    original=value.operator._argv.__func__
    def refused(self,unit,command,**kwargs):
        argv=original(self,unit,command,**kwargs)
        if fault=='timeout_exit':return [sys.executable,'-c','raise SystemExit(124)']
        script=argv[-1]
        if fault=='missing_completion':script=script.replace('"checkpoint_callback_completed": true','"checkpoint_callback_completed": null')
        else:script=script.replace('"checkpoint_callback_completed": true','"checkpoint_callback_completed": false')
        return argv[:-1]+[script]
    monkeypatch.setattr(SSHOperator,'_argv',refused)
    value._refs=(*value._refs[:5],refused,*value._refs[6:])  # Explicit transport failure model.
    with pytest.raises(WireError):value._jobs([('unit-a','checkpoint-audit',{})],1)
    assert value.callback_attempts[-1]['remote_terminal_proven'] is False


def test_real_timeout_kills_owned_term_ignoring_process_and_releases_its_fd(tmp_path):
    timeout=shutil.which('timeout');assert timeout
    source=root.REMOTE_TIMEOUT_ENTRY.replace("'/usr/bin/timeout'",repr(timeout))  # Same GNU API, local macOS path.
    lock=tmp_path/'owned.lock';ready=tmp_path/'ready'
    script='import fcntl,os,signal,time; f=os.open('+repr(str(lock))+',os.O_CREAT|os.O_RDWR,0o600); fcntl.flock(f,fcntl.LOCK_EX); signal.signal(signal.SIGTERM,signal.SIG_IGN); open('+repr(str(ready))+',"w").close(); time.sleep(30)'
    end=time.time_ns()//1000+700000;mono=time.monotonic_ns()//1000+700000
    job=root.Job([sys.executable,'-I','-B','-c',source,str(end),str(mono),sys.executable,'-I','-B','-c',script],
        deadline=time.monotonic()+4,clock=time.monotonic)
    try:
        assert job.wait() in (124,137,-9) and ready.exists()
    finally:job.close()
    assert job.closed and job.process.poll() is not None
    fd=os.open(lock,os.O_RDWR)
    try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    finally:os.close(fd)


@pytest.mark.parametrize('fault',('extended_epoch','extended_mono','mono_expired','epoch_expired','mutated_phase','mutated_payload'))
def test_native_clipped_guard_binds_original_phase_and_never_extends_handoff(data,fault,historical_h42_checkpoint):
    _,_,value,policy=data;now=[(101000000,802000000)]
    owner=dict(bundle_context=value,native_phase={'unit-a':dict(epoch_us=100000000,monotonic_us=801000000,deadline_monotonic_us=1101000000)},
        bundle_radio_checkpoint=dict(schema='meshticulum.root-public100k-checkpoint-owner.v1',producer_sha256='a'*64,
            primitives_sha256=adapter.checkpoint_api.PRIMITIVES_SHA,retained_history_count=42,maximum_checkpoints=4))
    payload=dict(checkpoint_index=1,checkpoint_remote_end_epoch_us=145000000,checkpoint_remote_deadline_monotonic_us=846000000)
    if fault=='extended_epoch':payload['checkpoint_remote_end_epoch_us']+=1
    if fault=='extended_mono':payload['checkpoint_remote_deadline_monotonic_us']+=1
    if fault in ('extended_epoch','extended_mono'):
        with pytest.raises(RuntimeError):adapter.remote_guard(policy,owner,'unit-a',payload,lambda:now[0])
        return
    guard=adapter.remote_guard(policy,owner,'unit-a',payload,lambda:now[0]);guard()
    if fault=='mono_expired':now[0]=(144000000,846000000)
    elif fault=='epoch_expired':now[0]=(145000000,845000000)
    elif fault=='mutated_phase':owner['native_phase']['unit-a']['deadline_monotonic_us']+=1
    else:payload['checkpoint_remote_end_epoch_us']+=1
    with pytest.raises(RuntimeError):guard()


@pytest.mark.parametrize('earlier',('operator','campaign'))
def test_callback_window_clamps_earlier_actual_owner_and_reserves_each_four_second_close(earlier):
    policy=SimpleNamespace(issued_epoch_us=100000000,original_monotonic_us=801000000,
        expires_epoch_us=400000000,deadline_monotonic_us=1101000000)
    operator,campaign=(840.0,850.0) if earlier=='operator' else (850.0,840.0)
    remote,remote_mono,local,end,mono=root.callback_window(policy,150000000,851000000,
        operator,campaign,2,(101000000,802000000))
    assert (end,mono)==(139000000,840000000)
    assert remote==end-11000000 and remote_mono==mono-11000000
    assert local+2*4000000+1000000==mono and remote_mono+2000000==local


@pytest.mark.parametrize('now',((128000000,829000000),(127000000,829000000)))
def test_dual_callback_budget_refuses_before_any_process_when_cleanup_cannot_fit(now):
    policy=SimpleNamespace(issued_epoch_us=100000000,original_monotonic_us=801000000,
        expires_epoch_us=400000000,deadline_monotonic_us=1101000000)
    with pytest.raises(WireError):root.callback_window(policy,150000000,851000000,840.0,850.0,2,now)


def test_two_real_term_ignoring_owned_clients_close_inside_reserved_original_owner_end(tmp_path):
    # Explicit SSH-transport hang model. These are real local children/FDs;
    # their reap never serves as a native callback completion witness.
    start=time.monotonic();jobs=[];now_epoch=time.time_ns()//1000;now_mono=int(start*1000000)
    policy=SimpleNamespace(issued_epoch_us=now_epoch,original_monotonic_us=now_mono,
        expires_epoch_us=now_epoch+12000000,deadline_monotonic_us=now_mono+12000000)
    _,_,local,_,owner_end=root.callback_window(policy,policy.expires_epoch_us,policy.deadline_monotonic_us,
        start+12,start+20,2,(now_epoch,now_mono))
    try:
        for index in range(2):
            lock=tmp_path/('owned-'+str(index)+'.lock');ready=tmp_path/('ready-'+str(index))
            script='import fcntl,os,signal,time; f=os.open('+repr(str(lock))+',os.O_CREAT|os.O_RDWR,0o600); fcntl.flock(f,fcntl.LOCK_EX); signal.signal(signal.SIGTERM,signal.SIG_IGN); open('+repr(str(ready))+',"w").close(); time.sleep(30)'
            jobs.append(root.Job([sys.executable,'-I','-B','-c',script],deadline=local/1000000,clock=time.monotonic))
        with pytest.raises(WireError):jobs[0].wait()
    finally:
        for job in jobs:job.close()
    assert int(time.monotonic()*1000000)<owner_end and all(job.closed and job.process.poll() is not None for job in jobs)
    for index in range(2):
        fd=os.open(tmp_path/('owned-'+str(index)+'.lock'),os.O_RDWR)
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        finally:os.close(fd)
