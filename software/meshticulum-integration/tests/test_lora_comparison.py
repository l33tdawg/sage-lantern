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
from lantern_meshticulum.bench.lora_comparison import ComparisonContext,KIND,SCHEMA
from lantern_meshticulum.bench.profiles import profile_for
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,UnitCaps,Cost,canonical
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,open_for_provision,NativeCapsError
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_resume_rpc import endpoint_budget,owner_radio_profile,stable
from tests.test_pilot_parent_snapshot import root_observation
from tests.test_ip_node import facts

BOOT='00000000-0000-4000-8000-000000000001'
PEERS={u:dict(chain_id='batch-source-control',agent_id=c*64) for u,c in zip(('unit-a','unit-b','unit-c'),'abc')}
sha=lambda raw:hashlib.sha256(raw).hexdigest()

@pytest.fixture(autouse=True)
def source_scope(monkeypatch):
    from types import MappingProxyType
    from lantern_meshticulum.bench import mcu_artifact as mcu
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    # Pure source model only. Production pins remain pending and unchanged.
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(dict(
        scope_sha256='e'*64,history_receipt_sha256='f'*64,preceding_parent=PAID_HISTORY[-1])))


def owner_for(c):
    from lantern_meshticulum.bench import mcu_artifact as mcu
    return dict(schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,
        radio_profile_name='lora-sf7-sf5',rf_profile=asdict(profile_for('sf7-500')),
        lora_mcu_artifact=mcu.lora_artifact(),core_commit=mcu.lora_artifact()['runtime_core_revision'],
        comparison_scope_sha256='e'*64,history_receipt_sha256='f'*64,policy_digest=c.policy.digest,
        complete_global_debt=False,actual_cost=None,lora_comparison=c.descriptor,
        phase_start_epoch_us=c.policy.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us)


def value():
    epoch,mono=1000000000,2000000000;issued=epoch-1000000;end=issued+600000000
    units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),Cost() if u=='unit-b' else Cost(1024,240000000),Cost(),'a'*64) for u in PEERS)
    p=ParentPolicy('1'*32,BOOT,issued,end-5000000,mono-1000000,mono+594000000,units,Cost(2048,480000000),'b'*64,KIND)
    rows=[];payloads=[]
    for i in range(2):
        mf=sha(('manifest'+str(i)).encode());chunk=sha(('chunk'+str(i)).encode());req=resource.encode_request('/packs/'+mf+'/chunks/'+chunk+'.json',0,8164);reply=resource.BlockServer(lambda _:bytes([65+i])*8164).handle(req)
        rows.append(dict(index=i,radio_profile_name=['sf7-500','sf5-500'][i],campaign_id=str(i+2)*32,resource_sha256=mf,chunk_sha256=chunk,request=dict(bytes=74,sha256=sha(req)),response=dict(bytes=8192,sha256=sha(reply))));payloads.append((req,reply))
    d=dict(schema=SCHEMA,sample_count=2,phase_seconds=600,application_seconds=180,publisher='unit-c',requester='unit-a',radio_profile_names=['sf7-500','sf5-500'],prepared_endpoint_limit=8,samples=rows)
    return ComparisonContext(d,p,phase_end_epoch_us=end),payloads,[epoch,mono]

@pytest.mark.parametrize('field,new',[('sample_count',3),('phase_seconds',900),('application_seconds',181),('prepared_endpoint_limit',20),('publisher','unit-b')])
def test_exact_batch_descriptor_refuses_growth(field,new):
    c,_,_=value();d=deepcopy(c.descriptor);d[field]=new
    with pytest.raises(WireError):ComparisonContext(d,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)

def test_declared_resource_and_context_cannot_be_reused_or_rebound():
    c,_,_=value();d=deepcopy(c.descriptor);d['samples'][1]['resource_sha256']=d['samples'][0]['resource_sha256']
    with pytest.raises(WireError):ComparisonContext(d,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)
    c.descriptor['samples'][0]['campaign_id']='f'*32
    with pytest.raises(WireError):c.check()

def test_budget_and_policy_optin_are_exact():
    from lantern_meshticulum.bench import mcu_artifact as mcu
    c,_,_=value();o=owner_for(c)
    assert owner_radio_profile(o,c.policy)==profile_for('sf7-500')
    assert endpoint_budget('unit-a',KIND,Cost(256,60000000),Cost())==Cost(1024,240000000)
    assert endpoint_budget('unit-b',KIND,Cost(256,60000000),Cost())==Cost()
    old=c.policy.to_dict();old.pop('qualification_kind')
    with pytest.raises(WireError):ComparisonContext.from_owner(o,ParentPolicy.from_dict(old))


def test_profile_integer_fields_cannot_be_replaced_with_boolean_aliases():
    from lantern_meshticulum.bench.pilot_parent_caps import child_caps
    c,payloads,now=value();package=pair(c,payloads[0][0],0,now)['packages']['unit-a']
    metadata=child_caps(package,c.policy).to_dict()['metadata'];metadata['profile']['chip_power_dbm']=False
    with pytest.raises(WireError,match='comparison_child_profile_changed'):c.metadata(metadata)


def pair(c,payload,index,now,response=False):
    r=c.descriptor['samples'][index];sender,receiver=('unit-c','unit-a') if response else ('unit-a','unit-c')
    return pair_packages(campaign_id=r['campaign_id'],resource_sha256=r['resource_sha256'],application_expires_epoch_us=now[0]+180000000,sender=sender,receiver=receiver,transport='lora',format='MR1.response' if response else 'MR1.request',descriptor=r['response' if response else 'request'],source=dict(kind='served_public_chunk',request_run_id='e'*32) if response else None,request=None if response else payload,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0],radio_permission=dict(country='MY',antenna_confirmed=True),profile=r['radio_profile_name'])

@pytest.fixture(params=['unit-a','unit-c'])
def native(root_observation,monkeypatch,request):
    unit=request.param
    base=Path(tempfile.mkdtemp(prefix='.batch-native-control-',dir=Path(__file__).parent));state=base/'state';state.mkdir(mode=0o700);(state/'parents').mkdir(mode=0o700)
    c,payloads,now=value();parent=state/'parents'/c.policy.parent_id;parent.mkdir(mode=0o700);clock=lambda:tuple(now)
    prior=dict(full_caps=asdict(Cost()),legacy_inventory={},complete_global_debt=False,actual_cost=None,refunds=0)
    strict,legacy=b'{}',b'{}';st=parent.stat();binding=dict(schema='meshticulum.private-resume-history-binding.v2',parent_id=c.policy.parent_id,unit=unit,strict_sha256=sha(strict),legacy_sha256=sha(legacy),parent_identity=[st.st_dev,st.st_ino,st.st_mode,st.st_uid]);br=canonical(binding);prior['history_binding_sha256']=sha(br)
    registry={u:dict(observation_sha256=sha(canonical(prior))) for u in PEERS};units=tuple(UnitCaps(u,BOOT,PeerID(**PEERS[u]),Cost() if u=='unit-b' else Cost(1024,240000000),Cost(),sha(canonical(prior))) for u in PEERS)
    p=c.policy;p=ParentPolicy(p.parent_id,p.controller_boot_id,p.issued_epoch_us,p.expires_epoch_us,p.original_monotonic_us,p.deadline_monotonic_us,units,p.aggregate_limits,sha(canonical(registry)),KIND);c=ComparisonContext(c.descriptor,p,phase_end_epoch_us=c.phase_end_epoch_us)
    phase=dict(epoch_us=p.issued_epoch_us,monotonic_us=p.original_monotonic_us,deadline_monotonic_us=p.original_monotonic_us+600000000);host=facts(unit);host['host_boot_id']=BOOT
    from lantern_meshticulum.bench import pilot_resume_rpc as rpc, mcu_artifact as mcu
    from tests.test_lora_mcu_admission import manifest
    class ModelRuntime:
        def __init__(self,root,pin):
            self.root=Path('runtime-'+'d'*16);self.manifest=manifest();self.closed=False
        def check(self):assert not self.closed;return True
        def close(self):self.closed=True
    monkeypatch.setattr(rpc,'Runtime',ModelRuntime)
    cap=dict(parent_id=p.parent_id,unit=unit,actual_history_verified=True,history_binding_sha256=sha(br),phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us,native_phase=phase,facts=stable(host),review_sha256='a'*64,history=dict(previous=[dict(paid_caps=asdict(Cost()))],legacy_inventory={}))
    owner=dict(runtime_sha256='d'*64,manifest_sha256='e'*64,lora_mcu_artifact=mcu.lora_artifact(),core_commit=mcu.lora_artifact()['runtime_core_revision'],comparison_scope_sha256='e'*64,history_receipt_sha256='f'*64,complete_global_debt=False,actual_cost=None,schema='meshticulum.finite-public-resume-owner.v1',qualification_kind=KIND,radio_profile_name='lora-sf7-sf5',rf_profile=asdict(profile_for('sf7-500')),lora_comparison=c.descriptor,policy_digest=p.digest,phase_start_epoch_us=p.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us,native_phase={unit:phase},facts={unit:stable(host)},history_binding_sha256=sha(br),capture_record_sha256=sha(canonical(cap)),prior_registry=registry,review_sha256='a'*64,authorization=dict(human_statement='run it bro',count_authorized=1,public_only=True,private_production=False),rf_record_sha256='40345bc09cb448f07b803318d69e0dec7b3d5ce1d1b8cba54bfd00ff14cecff9',rf_record=dict(country='MY',C_transmission_allowed=True,A_B_transmission_allowed=True,units={u:dict(antenna_connected=True) for u in PEERS}))
    for name,v in [('owner.json',owner),('prior.json',prior),('policy.json',p.to_dict()),('capture.json',cap),('history-binding.json',binding)]:f=parent/name;f.write_bytes(canonical(v));f.chmod(0o600)
    for name,v in [('history-identity-seal.json',strict),('legacy-grant-seal.json',legacy)]:f=parent/name;f.write_bytes(v);f.chmod(0o600)
    n=NativeCaps.enroll(parent/'native.sqlite',p,unit=unit,clock=clock,facts=lambda:NativeFacts(unit,BOOT,p.unit(unit).peer),approve=lambda _:True,prior_check=lambda _:True,history_binding_sha256=sha(br));assert n.close()
    folder=base/'controller';folder.mkdir(mode=0o700);ctrl=ParentCaps.enroll(folder/'caps.sqlite',p,clock=clock,boot_reader=lambda:BOOT)
    try:yield state,parent,c,payloads,now,clock,host,ctrl
    finally:ctrl.close();shutil.rmtree(base)

def qualified(ctrl,p):
    permit=ctrl.prepay_pair(tuple(p['packages'].values()));children=ctrl.consume(permit);out={}
    for ch in children:
        v=deepcopy(p['packages'][ch.unit]);v.update(schema='meshticulum.public-pilot-package.v2',parent=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=ctrl.policy.parent_id,policy_digest=ctrl.policy.digest,allocation_id=permit.key,child=ch.to_dict()));out[ch.unit]=v
    return out

def test_actual_controller_native_sqlite_claims_cover_two_profiles_without_refunds(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;seen=[]
    from lantern_meshticulum.bench.lora_comparison import ComparisonApproval
    a=ComparisonApproval(parent,c.policy,host['unit'],host,clock);a.close()
    for i,(req,reply) in enumerate(payloads):
        if i:now[0]+=200000000;now[1]+=200000000
        for response,data in [(False,req),(True,reply)]:
            q=qualified(ctrl,pair(c,data,i,now,response))[host['unit']];caps=open_for_provision(state,host,clock,q)
            try:
                pre=caps.prepare_claim(q);seen.append(pre.application_expires_epoch_us);plan=deepcopy(q['grant']);plan['application_deadline_monotonic_us']=pre.application_deadline_monotonic_us
                receipt=caps.finalize_provision(pre,plan);caps.consume_start(receipt['run_id'],plan,base64.b64decode(q['secret_base64']));snap=caps.snapshot()
            finally:assert caps.close()
    assert seen[::2]==[seen[0],seen[0]+200000000]
    assert snap['claims']==snap['prepared']==snap['starts']==4 and snap['debt']==asdict(Cost(1024,240000000))

def test_owner_removal_cannot_downgrade_to_single_sample_approval(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))[host['unit']];(parent/'owner.json').unlink()
    with pytest.raises(NativeCapsError):open_for_provision(state,host,clock,q)

def test_duplicate_request_refuses_before_another_native_claim(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))[host['unit']];caps=open_for_provision(state,host,clock,q)
    try:caps.prepare_claim(q)
    finally:assert caps.close()
    q=qualified(ctrl,pair(c,payloads[0][0],0,now))[host['unit']];caps=open_for_provision(state,host,clock,q)
    try:
        with pytest.raises(NativeCapsError):caps.prepare_claim(q)
        assert caps.snapshot()['claims']==1
    finally:caps.close()

def test_expired_resource_cannot_buy_a_new_native_deadline(native):
    state,parent,c,payloads,now,clock,host,ctrl=native;q=qualified(ctrl,pair(c,payloads[0][0],0,now))[host['unit']];caps=open_for_provision(state,host,clock,q)
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
        from lantern_meshticulum.bench.lora_comparison import SSH_SCHEMA
        from lantern_meshticulum.bench.mcu_artifact import lora_artifact
        config=dict(schema=SSH_SCHEMA,lora_mcu_artifact=lora_artifact(),comparison_clock=dict(phase_start_epoch_us=c.policy.issued_epoch_us,phase_end_epoch_us=c.phase_end_epoch_us,original_monotonic_us=c.policy.original_monotonic_us,deadline_monotonic_us=c.policy.deadline_monotonic_us+5000000))
        def prepare(self,*args):raise AssertionError('native prepare reached')
    operator=NoDeviceOperator();row=c.descriptor['samples'][0]
    opts=dict(campaign_id=row['campaign_id'],resource_sha256=row['resource_sha256'],expires_epoch_us=now[0]+180000000,handoff_deadline_monotonic=now[1]/1000000+600,peers=PEERS,boots={u:BOOT for u in PEERS},parent=ctrl,clock=lambda:now[1]/1000000,epoch=lambda:now[0],radio_permission=dict(country='MY',antenna_confirmed=True),radio_profile='sf7-500',lora_sender_first=True)
    with pytest.raises(WireError):Controller(operator,**opts)
    controller=Controller(operator,**opts,comparison_context=c);before=ctrl.snapshot()
    wrong=bytearray(payloads[0][0]);wrong[-1]^=1
    with pytest.raises(WireError):controller.link('unit-a','unit-c','lora','MR1.request',bytes(wrong))
    assert ctrl.snapshot()==before

def test_parallel_metadata_threads_create_local_contexts_and_never_reuse_main_owner():
    from concurrent.futures import ThreadPoolExecutor
    c,payloads,now=value();package=pair(c,payloads[0][0],0,now)['packages']['unit-a']
    def run():
        with pytest.raises(WireError):c.package(package)
        local=ComparisonContext(c.descriptor,c.policy,phase_end_epoch_us=c.phase_end_epoch_us)
        return local.package(package)['index']
    with ThreadPoolExecutor(max_workers=2) as pool:assert list(pool.map(lambda _:run(),range(2)))==[0,0]


@pytest.mark.parametrize('field,new',[('scope_sha256',None),('scope_sha256','d'*64),
    ('history_receipt_sha256','c'*64),('preceding_parent','b'*32)])
def test_context_refuses_current_binding_drift_before_local_prepayment(monkeypatch,field,new):
    from types import MappingProxyType
    from lantern_meshticulum.bench import mcu_artifact as mcu
    context,_,_=value();pins=dict(mcu.LORA_NATIVE_BINDINGS);pins[field]=new
    monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(pins))
    with pytest.raises(ValueError):context.check()


@pytest.mark.parametrize('callback',['clock','reader','runtime'])
@pytest.mark.parametrize('damage',['scope','context','last'])
def test_original_approval_after_checks_callback_mutation_and_closes_original_handles(native,monkeypatch,callback,damage):
    from types import MappingProxyType
    from lantern_meshticulum.bench import mcu_artifact as mcu
    from lantern_meshticulum.bench.lora_comparison import ComparisonApproval
    state,directory,c,payloads,now,clock,host,ctrl=native
    hook=[lambda:None]
    def guarded_clock():hook[0]();return clock()
    approval=ComparisonApproval(directory,c.policy,host['unit'],host,guarded_clock)
    reader,runtime=approval.reader,approval.runtime
    def mutate():
        if damage=='scope':
            pins=dict(mcu.LORA_NATIVE_BINDINGS);pins['scope_sha256']='d'*64
            monkeypatch.setattr(mcu,'LORA_NATIVE_BINDINGS',MappingProxyType(pins))
        elif damage=='context':approval.context=deepcopy(c)
        else:approval._last=None
    if callback=='clock':hook[0]=mutate
    else:
        target=reader if callback=='reader' else runtime;original=target.check
        def after():original();mutate();return True
        monkeypatch.setattr(target,'check',after)
    with pytest.raises((ValueError,WireError)):approval.check()
    assert reader._closed and runtime.closed
    assert ctrl.snapshot()['allocations']==0


def test_selected_ssh_deadline_retains_original_monotonic_end_when_epoch_rolls_back(monkeypatch):
    from tests.test_pilot_ssh import config
    from lantern_meshticulum.bench.pilot_ssh import SSHOperator
    from lantern_meshticulum.bench import mcu_artifact as mcu
    from lantern_meshticulum.bench.lora_comparison import SSH_SCHEMA
    import subprocess
    cfg,source=config();cfg.update(schema=SSH_SCHEMA,lora_mcu_artifact=mcu.lora_artifact(),
        handoff_end_epoch_us=1500000000,comparison_clock=dict(phase_start_epoch_us=900000000,
            phase_end_epoch_us=1500000000,original_monotonic_us=10000000,deadline_monotonic_us=610000000))
    monkeypatch.setattr(subprocess,'Popen',lambda *a,**k:pytest.fail('source control opened a connection'))
    op=SSHOperator(cfg,provision_source=source,epoch=lambda:1000000000,clock=lambda:500)
    assert op.deadline==610
    cfg['comparison_clock']['deadline_monotonic_us']+=1
    with pytest.raises(WireError):SSHOperator(cfg,provision_source=source,epoch=lambda:1000000000,clock=lambda:500)
