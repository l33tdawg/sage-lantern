"""Prepared descriptor integrity; inert cases supply no native authority."""
import base64
from dataclasses import replace
import json

import pytest
from nacl.signing import SigningKey

from meshticulum.privacy.forward_session import ForwardContext
from meshticulum.privacy.mesh_envelope import Peer
from lantern_meshticulum.sage.forward_native_owner import ForwardNativeOwner
from lantern_meshticulum.sage.forward_receive_journal import ReceiveIntention
from lantern_meshticulum.sage import forward_radio as radio
from lantern_meshticulum.sage import radio_native_adoption as adoption
from lantern_meshticulum.bench import profiles
from tests.integrations.sage.test_forward_radio_native_action import active, retained


def inert_case(mode):
    owner=ForwardNativeOwner.__new__(ForwardNativeOwner)
    a=Peer('descriptor-model',bytes(SigningKey(bytes([1])*32).verify_key))
    b=Peer('descriptor-model',bytes(SigningKey(bytes([2])*32).verify_key))
    owner.context=ForwardContext(a,b,b'x'*16,b'b'*32,100000000,180000000,64,
        bytes([9])+bytes(31),bytes([10])+bytes(31))
    owner.intention=ReceiveIntention('00000000-0000-0000-0000-000000000001','e'*64,512,
        owner.context.binding_digest.hex(),'00000000-0000-0000-0000-000000000002',180000000,130000000)
    owner.local_initiator=True;owner.local=a;owner.remote=b;owner._approval='f'*64
    profile=profiles.profile_for(mode);peer=lambda p:dict(chain_id=p.chain_id,agent_id=p.public_key.hex())
    plan=dict(schema='meshticulum.service-run.v1',unit='unit-a',host_boot_id=owner.intention.host_boot_id,
        local_peer=peer(a),initiator=peer(a),responder=peer(b),session_id=(b's'*16).hex(),generation=(b'g'*16).hex(),
        issued_epoch_us=100000000,expires_epoch_us=180000000,profile=profiles.profile_record(profile),
        object=dict(bytes=74,sha256='d'*64,chunk_index=0,object_id=owner.context.digest.hex()),
        sender_unit='unit-a',country='MY',antenna_confirmed=True,max_frames=64,
        application_deadline_monotonic_us=129000000,
        service=dict(kind='protected_message',format='MPF2.record',application_expires_epoch_us=180000000))
    if mode=='gfsk250':plan['radio_modem']='gfsk250'
    described=radio.plan_stage(owner,plan,b'k'*32,slot=0,kind=1)
    factory=radio.ForwardRadioFactory.__new__(radio.ForwardRadioFactory)
    factory.owner,factory.profile=owner,described
    factory._descriptor=radio._compile_descriptor(owner,described)
    factory._refs=(owner,described,None,None,None,factory._descriptor,radio.ForwardRadioFactory._descriptor_current)
    return factory


@pytest.mark.parametrize('mode',['sf7-500','gfsk250'])
def test_exact_description_repeats_without_planner_or_authority(mode):
    factory=inert_case(mode)
    before=(factory.owner.context,factory.owner.intention,factory.profile)
    assert factory.profile==radio.plan_stage(factory.owner,json.loads(factory.profile.plan_bytes),
        factory.profile.grant.secret,slot=0,kind=1)
    assert factory._descriptor_current() is None
    assert factory._descriptor_current() is None
    assert (factory.owner.context,factory.owner.intention,factory.profile)==before
    assert not hasattr(factory,'authority') and not hasattr(factory.owner,'resource')


@pytest.mark.parametrize('mode',['sf7-500','gfsk250'])
@pytest.mark.parametrize('drift',['context-ref','intention-ref','local-ref','role','approval','cost-type',
    'sends-type','radio-type','session-type','grant-ref','grant-secret','peer-field','context-field',
    'intention-field','plan-bytes','policy-ref','policy-type','policy-tuple'])
def test_nested_refs_and_exact_scalar_types_refuse(mode,drift,monkeypatch):
    f=inert_case(mode);p=f.profile;o=f.owner;restore=None
    if drift=='context-ref':o.context=replace(o.context)
    elif drift=='intention-ref':o.intention=replace(o.intention)
    elif drift=='local-ref':o.local=replace(o.local)
    elif drift=='role':o.local_initiator=1
    elif drift=='approval':o._approval='a'*64
    elif drift=='cost-type':object.__setattr__(p.child.cost,'ip_frames',False)
    elif drift=='sends-type':object.__setattr__(p.child,'sends',1)
    elif drift=='radio-type':object.__setattr__(p.radio,'lease_ms',90000.0)
    elif drift=='session-type':object.__setattr__(p.grant.session,'session_id',bytearray(p.grant.session.session_id))
    elif drift=='grant-ref':object.__setattr__(p,'grant',replace(p.grant))
    elif drift=='grant-secret':object.__setattr__(p.grant,'secret',b'z'*32)
    elif drift=='peer-field':object.__setattr__(p.grant.session.initiator,'agent_id','a'*64)
    elif drift=='context-field':object.__setattr__(o.context,'expires_epoch_us',180000001)
    elif drift=='intention-field':object.__setattr__(o.intention,'application_deadline_monotonic_us',130000001)
    elif drift=='plan-bytes':object.__setattr__(p,'plan_bytes',p.plan_bytes+b' ')
    else:
        name='GFSK250_ADMISSION' if mode=='gfsk250' else 'RADIO_ADMISSION';policy=getattr(profiles,name)
        if drift=='policy-ref':monkeypatch.setattr(profiles,name,replace(policy))
        elif drift=='policy-type':
            restore=(policy,'max_boot_attempts',policy.max_boot_attempts)
            object.__setattr__(policy,'max_boot_attempts',256.0)
        else:
            name='frequencies_hz' if mode=='gfsk250' else 'waveforms'
            restore=(policy,name,getattr(policy,name));object.__setattr__(policy,name,list(getattr(policy,name)))
    try:
        with pytest.raises(radio.ForwardRadioError,match='^forward_radio_unavailable$'):f._descriptor_current()
    finally:
        if restore is not None:object.__setattr__(*restore)


@pytest.mark.parametrize('drift',['validate','policy-check','property','property-code','instance-method',
    'selector','parser','parser-default','constructor','constructor-default','schema','field-property',
    'owner-getattribute','owner-field-property','domain','modulation'])
def test_removed_planner_dependencies_are_still_fenced(drift,monkeypatch):
    f=inert_case('gfsk250');p=f.profile
    if drift=='validate':monkeypatch.setattr(adoption.NativeRadioAdoption,'validate_plan',staticmethod(lambda *_:None))
    elif drift=='policy-check':monkeypatch.setattr(type(profiles.GFSK250_ADMISSION),'check',lambda *_:None)
    elif drift=='property':monkeypatch.setattr(ForwardContext,'digest',property(lambda _:p.context_digest))
    elif drift=='property-code':
        original=ForwardContext.digest.fget
        def changed(self):return b'changed'
        monkeypatch.setattr(original,'__code__',changed.__code__)
    elif drift=='instance-method':object.__setattr__(f.owner.local,'to_dict',lambda:dict(chain_id='same'))
    elif drift=='selector':monkeypatch.setattr(profiles,'admission_for',lambda _:profiles.GFSK250_ADMISSION)
    elif drift=='parser':monkeypatch.setattr(profiles,'profile_from_plan',lambda *_:p.radio)
    elif drift=='parser-default':monkeypatch.setitem(profiles.profile_from_plan.__kwdefaults__,'board_id',2)
    elif drift=='constructor':monkeypatch.setattr(radio.Cost,'__post_init__',lambda _:None)
    elif drift=='constructor-default':monkeypatch.setattr(radio.Cost.__init__,'__defaults__',(0,0,1,0,0))
    elif drift=='schema':
        field=radio.Cost.__dataclass_fields__['ip_frames'];monkeypatch.setattr(field,'type',bool)
    elif drift=='field-property':monkeypatch.setattr(Peer,'chain_id',property(lambda _:'descriptor-model'),raising=False)
    elif drift=='owner-getattribute':
        def getattribute(self,name):return object.__getattribute__(self,name)
        monkeypatch.setattr(ForwardNativeOwner,'__getattribute__',getattribute)
    elif drift=='owner-field-property':
        monkeypatch.setattr(ForwardNativeOwner,'context',property(lambda _:f.owner.__dict__['context']),raising=False)
    elif drift=='domain':monkeypatch.setattr(radio._forward_module,'DOMAIN',b'changed-domain')
    else:monkeypatch.setattr(radio._gfsk250_module,'BIT_RATE_BPS',125000)
    with pytest.raises(radio.ForwardRadioError):f._descriptor_current()


def test_constructor_still_runs_complete_planner(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        bad=replace(r.profile,child=replace(r.profile.child,packet_sha256='a'*64))
        observer=radio.NativeRadioObserver(r.owner,bad,r.transport)
        with pytest.raises(radio.ForwardRadioError):
            radio.ForwardRadioFactory(r.owner,bad,r.directory,r.transport,observer=observer)
        retained(r)


def test_comparison_entry_code_cannot_be_replaced(tmp_path,monkeypatch):
    with active(tmp_path,monkeypatch) as r:
        def permissive(self):return None
        monkeypatch.setattr(radio.ForwardRadioFactory._descriptor_current,'__code__',permissive.__code__)
        with pytest.raises(radio.ForwardRadioError):r.stage._authorize()
        assert r.stage._unknown and r.action['calls']==0
        retained(r)


@pytest.mark.parametrize('drift',['scope','vault','deadline','grant','signed-record','descriptor-ref'])
def test_prepared_description_never_caches_live_authority(tmp_path,monkeypatch,drift):
    with active(tmp_path,monkeypatch) as r:
        assert r.stage._authorize() is True
        r.action['calls']=0
        completed=[]
        def callback(r,at):
            if at!=1:return
            if drift=='scope':
                with r.authority._mutex:
                    row=r.authority._grants[id(r.native_context)]
                    row['operations']=row['operations']-{adoption.SCOPE}
            elif drift=='vault':r.state.instance_id='c'*64
            elif drift=='deadline':r.state.now=(r.state.now[0],r.profile.child.application_deadline_monotonic_us)
            elif drift=='grant':(r.directory/'active').write_bytes(b'retired\n')
            elif drift=='signed-record':
                row=r.state.rows[r.stage.adoption.record_id];body=row['payload']['body']
                row['revision']+=1;body['revision']=row['revision'];body['state']='revoked'
                row['payload']['signature']=base64.b64encode(r.hardware.identity._signing_key.sign(
                    adoption.DOMAIN+radio._canonical(body)).signature).decode()
            else:r.stage._descriptor=tuple(list(r.stage._descriptor))
            completed.append(True)
        r.action['mutate']=callback
        with pytest.raises(radio.ForwardRadioError):r.stage._authorize()
        assert completed==[True] and r.stage._unknown and r.stage.adoption._uncertain
        retained(r)
