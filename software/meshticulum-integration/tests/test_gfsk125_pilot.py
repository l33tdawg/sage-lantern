"""Real SQLite/controller/held-record controls; modeled approval and host facts, no RF."""
import base64
from copy import deepcopy
from dataclasses import asdict
import hashlib,json,shutil,tempfile
from pathlib import Path
import pytest
from meshticulum.resources import resource
from meshticulum.bench.wire import WireError
from meshticulum.contracts.identity import PeerID
from lantern_meshticulum.bench.gfsk_batch import BatchContext,KIND,SCHEMA
from lantern_meshticulum.bench.profiles import profile_for,profile_record
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,UnitCaps,Cost,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,open_for_provision,NativeCapsError
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_resume_rpc import endpoint_budget,owner_radio_profile,stable
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_ip_node import facts

BOOT='00000000-0000-4000-8000-000000000001'
PEERS={u:dict(chain_id='batch-source-control',agent_id=c*64) for u,c in zip(('unit-a','unit-b','unit-c'),'abc')}
sha=lambda raw:hashlib.sha256(raw).hexdigest()

def value():
    epoch,mono=1000000000,2000000000;issued=epoch-1000000;end=issued+899000000
    units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),Cost() if u=='unit-b' else Cost(1536,360000000),Cost(),'a'*64) for u in PEERS)
    p=ParentPolicy('1'*32,BOOT,issued,end-5000000,mono-1000000,mono+893000000,units,Cost(3072,720000000),'b'*64,KIND)
    rows=[];payloads=[]
    for i in range(3):
        mf=sha(('manifest'+str(i)).encode());chunk=sha(('chunk'+str(i)).encode());req=resource.encode_request('/packs/'+mf+'/chunks/'+chunk+'.json',0,8164);reply=resource.BlockServer(lambda _:bytes([65+i])*8164).handle(req)
        rows.append(dict(index=i,campaign_id=str(i+2)*32,resource_sha256=mf,chunk_sha256=chunk,request=dict(bytes=74,sha256=sha(req)),response=dict(bytes=8192,sha256=sha(reply))));payloads.append((req,reply))
    d=dict(schema=SCHEMA,sample_count=3,phase_seconds=899,application_seconds=180,publisher='unit-c',requester='unit-a',radio_profile_name='gfsk125',radio_modem='gfsk125',prepared_endpoint_limit=12,samples=rows)
    return BatchContext(d,p,phase_end_epoch_us=end),payloads,[epoch,mono]

@pytest.mark.parametrize('field,new',[('sample_count',2),('phase_seconds',900),('application_seconds',181),('prepared_endpoint_limit',20),('publisher','unit-b')])
def test_exact_batch_descriptor_refuses_growth(field,new):
    c,_,_=value();d=deepcopy(c.descriptor);d[field]=new
    with pytest.raises(WireError):BatchContext(d,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)

def test_declared_resource_and_context_cannot_be_reused_or_rebound():
    c,_,_=value();d=deepcopy(c.descriptor);d['samples'][1]['resource_sha256']=d['samples'][0]['resource_sha256']
    with pytest.raises(WireError):BatchContext(d,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)
    c.descriptor['samples'][0]['campaign_id']='f'*32
    with pytest.raises(WireError):c.check()

def test_budget_and_policy_optin_are_exact():
    c,_,_=value();o=dict(qualification_kind=KIND,radio_profile_name='gfsk125',radio_modem='gfsk125',rf_profile=profile_record(profile_for('gfsk125')),gfsk_batch=c.descriptor,phase_start_epoch_us=c.policy.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us)
    assert owner_radio_profile(o,c.policy)==profile_for('gfsk125')
    assert endpoint_budget('unit-a',KIND,Cost(256,60000000),Cost())==Cost(1536,360000000)
    assert endpoint_budget('unit-b',KIND,Cost(256,60000000),Cost())==Cost()
    old=c.policy.to_dict();old.pop('qualification_kind')
    with pytest.raises(WireError):BatchContext.from_owner(o,ParentPolicy.from_dict(old))

def pair(c,payload,index,now,response=False):
    r=c.descriptor['samples'][index];sender,receiver=('unit-c','unit-a') if response else ('unit-a','unit-c')
    return pair_packages(campaign_id=r['campaign_id'],resource_sha256=r['resource_sha256'],application_expires_epoch_us=now[0]+180000000,sender=sender,receiver=receiver,transport='lora',format='MR1.response' if response else 'MR1.request',descriptor=r['response' if response else 'request'],source=dict(kind='served_public_chunk',request_run_id='e'*32) if response else None,request=None if response else payload,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0],radio_permission=dict(country='MY',antenna_confirmed=True),profile='gfsk125')

@pytest.fixture
def native(root_observation):
    base=Path(tempfile.mkdtemp(prefix='.batch-native-control-',dir=Path(__file__).parent));state=base/'state';state.mkdir(mode=0o700);(state/'parents').mkdir(mode=0o700)
    c,payloads,now=value();parent=state/'parents'/c.policy.parent_id;parent.mkdir(mode=0o700);clock=lambda:tuple(now)
    prior=dict(full_caps=asdict(Cost()),legacy_inventory={},complete_global_debt=False,actual_cost=None,refunds=0)
    strict,legacy=b'{}',b'{}';st=parent.stat();binding=dict(schema='meshticulum.private-resume-history-binding.v2',parent_id=c.policy.parent_id,unit='unit-a',strict_sha256=sha(strict),legacy_sha256=sha(legacy),parent_identity=[st.st_dev,st.st_ino,st.st_mode,st.st_uid]);br=canonical(binding);prior['history_binding_sha256']=sha(br)
    registry={u:dict(observation_sha256=sha(canonical(prior))) for u in PEERS};units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),Cost() if u=='unit-b' else Cost(1536,360000000),Cost(),sha(canonical(prior))) for u in PEERS)
    p=c.policy;p=ParentPolicy(p.parent_id,p.controller_boot_id,p.issued_epoch_us,p.expires_epoch_us,p.original_monotonic_us,p.deadline_monotonic_us,units,p.aggregate_limits,sha(canonical(registry)),KIND);c=BatchContext(c.descriptor,p,phase_end_epoch_us=c.phase_end_epoch_us)
    phase=dict(epoch_us=p.issued_epoch_us,monotonic_us=p.original_monotonic_us,deadline_monotonic_us=p.original_monotonic_us+899000000);host=facts('unit-a');host['host_boot_id']=BOOT
    cap=dict(parent_id=p.parent_id,unit='unit-a',actual_history_verified=True,history_binding_sha256=sha(br),phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us,native_phase=phase,facts=stable(host),review_sha256='a'*64,history=dict(previous=[dict(paid_caps=asdict(Cost()))],legacy_inventory={}))
    owner=dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,radio_profile_name='gfsk125',radio_modem='gfsk125',rf_profile=profile_record(profile_for('gfsk125')),gfsk_batch=c.descriptor,policy_digest=p.digest,phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us,native_phase={'unit-a':phase},facts={'unit-a':stable(host)},history_binding_sha256=sha(br),capture_record_sha256=sha(canonical(cap)),prior_registry=registry,review_sha256='a'*64,authorization=dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False),rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',rf_record=dict(country='MY',C_transmission_allowed=True,A_B_transmission_allowed=True,units={u:dict(antenna_connected=True) for u in PEERS}))
    for name,v in [('owner.json',owner),('prior.json',prior),('policy.json',p.to_dict()),('capture.json',cap),('history-binding.json',binding)]:f=parent/name;f.write_bytes(canonical(v));f.chmod(0o600)
    for name,v in [('history-identity-seal.json',strict),('legacy-grant-seal.json',legacy)]:f=parent/name;f.write_bytes(v);f.chmod(0o600)
    n=NativeCaps.enroll(parent/'native.sqlite',p,unit='unit-a',clock=clock,facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=lambda _:True,prior_check=lambda _:True,history_binding_sha256=sha(br));assert n.close()
    folder=base/'controller';folder.mkdir(mode=0o700);ctrl=ParentCaps.enroll(folder/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:yield state,parent,c,payloads,now,clock,host,ctrl
    finally:ctrl.close();shutil.rmtree(base)

def qualified(ctrl,p):
    permit=ctrl.prepay_pair(tuple(p['packages'].values()));children=ctrl.consume(permit);out={}
    for ch in children:
        v=deepcopy(p['packages'][ch.unit]);v.update(schema='meshticulum.public-pilot-package.v2',parent=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=ctrl.policy.parent_id,policy_digest=ctrl.policy.digest,allocation_id=permit.key,child=ch.to_dict()));out[ch.unit]=v
    return out

def test_actual_controller_native_sqlite_claims_cover_three_expiries_without_refunds(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;seen=[]
    from lantern_meshticulum.bench.gfsk_batch import BatchApproval
    a=BatchApproval(parent,c.policy,'unit-a',host,clock);a.close()
    for i,(req,reply) in enumerate(payloads):
        if i:now[0]+=200000000;now[1]+=200000000
        for response,data in [(False,req),(True,reply)]:
            q=qualified(ctrl,pair(c,data,i,now,response))['unit-a'];caps=open_for_provision(state,host,clock,q)
            try:
                pre=caps.prepare_claim(q);seen.append(pre.application_expires_epoch_us);plan=deepcopy(q['grant']);plan['application_deadline_monotonic_us']=pre.application_deadline_monotonic_us
                receipt=caps.finalize_provision(pre,plan);caps.consume_start(receipt['run_id'],plan,base64.b64decode(q['secret_base64']));snap=caps.snapshot()
            finally:assert caps.close()
    assert seen[::2]==[seen[0],seen[0]+200000000,seen[0]+400000000]
    assert snap['claims']==snap['prepared']==snap['starts']==6 and snap['debt']==asdict(Cost(1536,360000000))

def test_owner_removal_cannot_downgrade_to_single_sample_approval(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))['unit-a'];(parent/'owner.json').unlink()
    with pytest.raises(NativeCapsError):open_for_provision(state,host,clock,q)

def test_duplicate_request_refuses_before_another_native_claim(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))['unit-a'];caps=open_for_provision(state,host,clock,q)
    try:caps.prepare_claim(q)
    finally:assert caps.close()
    q=qualified(ctrl,pair(c,payloads[0][0],0,now))['unit-a'];caps=open_for_provision(state,host,clock,q)
    try:
        with pytest.raises(NativeCapsError):caps.prepare_claim(q)
        assert caps.snapshot()['claims']==1
    finally:caps.close()

def test_expired_resource_cannot_buy_a_new_native_deadline(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))['unit-a'];caps=open_for_provision(state,host,clock,q)
    try:pre=caps.prepare_claim(q);original=pre.application_expires_epoch_us
    finally:assert caps.close()
    now[0]+=200000000;now[1]+=200000000
    caps=open_for_provision(state,host,clock,q)
    try:
        with pytest.raises(NativeCapsError):caps.prepare_claim(q)
        assert caps.snapshot()['claims']==1 and original<now[0]
    finally:caps.close()


def test_actual_controller_refuses_undeclared_batch_object_before_local_prepayment(native):
    from lantern_meshticulum.bench.pilot_controller import Controller
    state,parent,c,payloads,now,clock,host,ctrl=native
    class NoDeviceOperator:
        def prepare(self,*args):raise AssertionError('native prepare reached')
    operator=NoDeviceOperator();row=c.descriptor['samples'][0]
    opts=dict(campaign_id=row['campaign_id'],resource_sha256=row['resource_sha256'],expires_epoch_us=now[0]+180000000,handoff_deadline_monotonic=now[1]/1000000+899,peers=PEERS,boots={u:BOOT for u in PEERS},parent=ctrl,clock=lambda:now[1]/1000000,epoch=lambda:now[0],radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile='gfsk125',lora_sender_first=True)
    with pytest.raises(WireError):Controller(operator,**opts)
    controller=Controller(operator,**opts,batch_context=c);before=ctrl.snapshot()
    wrong=bytearray(payloads[0][0]);wrong[-1]^=1
    with pytest.raises(WireError):controller.link('unit-a','unit-c','lora','MR1.request',bytes(wrong))
    assert ctrl.snapshot()==before

def test_parallel_metadata_threads_create_local_contexts_and_never_reuse_main_owner():
    from concurrent.futures import ThreadPoolExecutor
    c,payloads,now=value();package=pair(c,payloads[0][0],0,now)['packages']['unit-a']
    def run():
        with pytest.raises(WireError):c.package(package)
        local=BatchContext(c.descriptor,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)
        return local.package(package)['index']
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:run(),range(2)))==[0,0]


@pytest.fixture(autouse=True)
def synthetic_deployment_bindings(monkeypatch):
    """Explicit historical H42 model; never current native GFSK admission."""
    from lantern_meshticulum.bench import profiles,retained_capture
    actual=tuple(retained_capture.PAID_HISTORY)
    assert len(actual)==43 and actual[-1]=='bea52c6aa6a5a95501997a46247b514f'
    historical=actual[:42]
    assert historical[-1]=='60d9ca2aa760cb4460d0af637101c997'
    monkeypatch.setattr(profiles,'GFSK_FIRMWARE','mtb1-0000000000')
    monkeypatch.setattr(profiles,'GFSK_CORE_COMMIT','a'*40)
    monkeypatch.setattr(profiles,'GFSK_HISTORY_RECEIPT_SHA256','b'*64)
    monkeypatch.setattr(profiles,'GFSK_PRECEDING_PARENT',historical[-1])
    monkeypatch.setattr(retained_capture,'PAID_HISTORY',historical)
    return actual


@pytest.mark.parametrize('preceding',['60d9ca2aa760cb4460d0af637101c997','bea52c6aa6a5a95501997a46247b514f'])
def test_actual_current_H43_keeps_legacy_scope_closed_before_device_open(
        synthetic_deployment_bindings,monkeypatch,preceding):
    from lantern_meshticulum.bench import profiles,retained_capture,node,mcu_artifact
    selected_before=dict(mcu_artifact.PENDING_NATIVE_BINDINGS)
    monkeypatch.setattr(retained_capture,'PAID_HISTORY',synthetic_deployment_bindings)
    monkeypatch.setattr(profiles,'GFSK_PRECEDING_PARENT',preceding)
    monkeypatch.setattr(node,'ExclusiveSerial',lambda *a,**k:pytest.fail('legacy device opened'))
    with pytest.raises(WireError,match='pending_gfsk_firmware_source_and_history_bindings'):
        node._serial('unit-a',radio_modem='gfsk125')
    assert dict(mcu_artifact.PENDING_NATIVE_BINDINGS)==selected_before


def test_pending_deployment_refuses_before_open(monkeypatch):
    from lantern_meshticulum.bench import profiles,node
    monkeypatch.setattr(profiles,'GFSK_FIRMWARE',None)
    def forbidden(*a,**k):raise AssertionError('pending source must not open a device')
    monkeypatch.setattr(node,'ExclusiveSerial',forbidden)
    with pytest.raises(WireError,match='pending_gfsk_firmware_source_and_history_bindings'):
        node._serial('unit-a',radio_modem='gfsk125')


def test_mode_is_distinct_JSON_safe_and_bound_into_child_context():
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps,ParentCapsError
    from lantern_meshticulum.bench.pilot_parent_snapshot import _child
    c,payloads,now=value();p=pair(c,payloads[0][0],0,now)['packages']['unit-a']
    profile=profile_for('gfsk125');assert json.loads(canonical(profile_record(profile)))==profile.to_dict()
    child=child_caps(p,c.policy);meta=child.to_dict()['metadata']
    assert meta['radio_modem']=='gfsk125' and meta['profile']==profile.to_dict()
    assert _child(child.to_dict(),c.policy)==Cost(256,60000000)
    old=c.policy.to_dict();old['qualification_kind']='c-bulk-8k-batch-v1'
    with pytest.raises(ParentCapsError):child_caps(p,ParentPolicy.from_dict(old))
    p['grant'].pop('radio_modem')
    with pytest.raises(ParentCapsError):child_caps(p,c.policy)


@pytest.mark.parametrize('marker',['lora','gfsk500',None,True])
def test_foreign_marker_refuses_typed_child(marker):
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps,ParentCapsError
    c,payloads,now=value();p=pair(c,payloads[0][0],0,now)['packages']['unit-a'];p['grant']['radio_modem']=marker
    with pytest.raises(ParentCapsError):child_caps(p,c.policy)


def test_independent_core_authority_and_Lantern_grant_digest_match(tmp_path):
    from meshticulum.bench.authority import RunAuthority
    from lantern_meshticulum.bench.profiles import GFSK_ADMISSION
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps
    c,payloads,now=value();p=pair(c,payloads[0][0],0,now)['packages']['unit-a'];child=child_caps(p,c.policy)
    folder=tmp_path/'authority';folder.mkdir(mode=0o700)
    for name,raw in [('grant.json',canonical(p['grant'])),('secret.bin',base64.b64decode(p['secret_base64'])),('active',b'active\n')]:
        f=folder/name;f.write_bytes(raw);f.chmod(0o600)
    authority=RunAuthority(folder,expected_unit='unit-a',host_boot_id=BOOT,clock=lambda:tuple(now),owner_probe=lambda:True,radio_admission=GFSK_ADMISSION)
    try:
        assert authority.route_record['radio_modem']=='gfsk125'
        assert authority.grant.digest.hex()==child.to_dict()['metadata']['grant_digest']
    finally:authority.close()


def status_worker(mode,*,refusal=1,flags=b'\0\0\0',boot=None):
    import struct
    from types import SimpleNamespace
    from meshticulum.bench.serial_protocol import Frame,Message
    identity=SimpleNamespace(boot=bytes.fromhex('1'*32),board_id=1)
    worker=SimpleNamespace(channel=SimpleNamespace(monotonic=lambda:10),sent=[])
    worker._send=lambda frame,end:worker.sent.append(frame)
    def wait(request,end):
        actual=identity.boot if boot is None else boot
        if len(worker.sent)==1:
            if mode=='lora':return Frame(Message.STATUS_REPLY,actual,request,bytes(30)+struct.pack('!IQI',7,1000,97)+flags)
            return Frame(Message.REFUSED,actual,request,bytes([refusal]))
        return Frame(Message.GFSK_STATUS_REPLY,actual,request,profile_for('gfsk125').encode(board_id=1)+struct.pack('!IQI',7,1000,97)+flags)
    worker._wait=wait;return worker,identity


def test_initial_and_persisted_modem_STATUS_never_ARM(monkeypatch):
    from lantern_meshticulum.bench import node
    from meshticulum.bench.serial_protocol import Message
    monkeypatch.setattr(node.time,'time_ns',lambda:1000000)
    worker,identity=status_worker('lora');old=node._disarmed_status(worker,identity)
    assert old['boot_attempts']==7 and old['dropped_receive']==97
    assert [v.type for v in worker.sent]==[Message.STATUS]
    worker,identity=status_worker('gfsk125');new=node._disarmed_status(worker,identity)
    assert new==dict(old,radio_modem='gfsk125')
    assert [v.type for v in worker.sent]==[Message.STATUS,Message.GFSK_STATUS]


@pytest.mark.parametrize('refusal',[2,3,4,5,6,7,8,9])
def test_other_STATUS_refusal_never_means_modem_switch(monkeypatch,refusal):
    from lantern_meshticulum.bench import node
    monkeypatch.setattr(node.time,'time_ns',lambda:1000000)
    worker,identity=status_worker('gfsk125',refusal=refusal)
    with pytest.raises(WireError,match='radio_status_refused'):node._disarmed_status(worker,identity)
    assert len(worker.sent)==1


@pytest.mark.parametrize('flags',[b'\1\0\0',b'\0\1\0',b'\0\0\1'])
def test_STATUS_armed_transmitting_fenced_are_refused(monkeypatch,flags):
    from lantern_meshticulum.bench import node
    monkeypatch.setattr(node.time,'time_ns',lambda:1000000)
    worker,identity=status_worker('gfsk125',flags=flags)
    with pytest.raises(WireError):node._disarmed_status(worker,identity)


def test_wrong_STATUS_boot_refuses_before_fallback(monkeypatch):
    from lantern_meshticulum.bench import node
    monkeypatch.setattr(node.time,'time_ns',lambda:1000000)
    worker,identity=status_worker('gfsk125',boot=bytes(16))
    with pytest.raises(WireError,match='unbound_radio_status'):node._disarmed_status(worker,identity)
    assert len(worker.sent)==1


@pytest.mark.parametrize('value',[True,False,None])
def test_parent_check_timing_retains_every_actual_outcome_without_cache(value):
    from lantern_meshticulum.bench.node import _timed_parent_check
    stamps=iter([1000000,1002500,2000000,2009000]);calls=[]
    def check():calls.append(value);return value
    measured,timing=_timed_parent_check(check,monotonic_ns=lambda:next(stamps))
    assert measured() is value and measured() is value and calls==[value,value]
    assert timing==dict(clock_basis='host_monotonic_ns',count=2,total_elapsed_us=11,max_elapsed_us=9)


def test_parent_check_timing_counts_and_propagates_original_exception():
    from lantern_meshticulum.bench.node import _timed_parent_check
    stamps=iter([1000000,1004000]);error=RuntimeError('modeled native revocation')
    def check():raise error
    measured,timing=_timed_parent_check(check,monotonic_ns=lambda:next(stamps))
    with pytest.raises(RuntimeError) as caught:measured()
    assert caught.value is error and timing['count']==1 and timing['total_elapsed_us']==4


@pytest.mark.parametrize('goodput,passed',[(4999.0,False),(5000.0,True)])
def test_stretch_goal_uses_primary_sender_to_confirmed_ACK_interval(monkeypatch,goodput,passed):
    import importlib.util
    bench=Path(__file__).resolve().parents[3]/'firmware/meshticulum-bench'
    import sys
    monkeypatch.syspath_prepend(str(bench))
    spec=importlib.util.spec_from_file_location('gfsk_metrics_source_control',bench/'run_public_gfsk125_batch.py')
    runner=importlib.util.module_from_spec(spec);spec.loader.exec_module(runner)
    monkeypatch.setattr(runner,'_lora_metrics',lambda *a:dict(stretch_target_met=True,useful_encrypted_rf_goodput_Bps=goodput))
    row=runner.gfsk_metrics(dict(outcomes={u:dict(radio_modem='gfsk125') for u in ('unit-a','unit-c')}),{}, {})
    assert row['stretch_target_met'] is passed and row['data_span_stretch_target_met'] is True
