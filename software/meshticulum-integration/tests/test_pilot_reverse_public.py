"""Local signed-resource and real controller-ledger controls; no native/RF."""
import base64
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys

import pytest
from lantern_meshticulum.bench import pilot_resume_rpc as rpc, pilot_resource_node as node
from lantern_meshticulum.bench.pilot_controller import Controller
from lantern_meshticulum.bench.pilot_seed import install
from lantern_meshticulum.bench.pilot_resources import serve_chunk
from lantern_meshticulum.bench.pilot_context import original_context
from lantern_meshticulum.bench.public_cache_observer import _observe_local_anchor
from lantern_meshticulum.ip.node import _write, clock
from meshticulum.bench.wire import WireError
from meshticulum.resources import resource, lamplighter as ll
from tests.test_meshticulum_resources import pack
from tests.test_ip_node import facts
from tests import test_pilot_controller as source_model
from tests.test_pilot_controller import REQUEST, RESPONSE, release_model_parents
from tests.test_pilot_seed import package


def controller():
    tick=source_model.Clock();op=source_model.ModelOperator(tick)
    parent=source_model.model_parent(tick)
    pilot=Controller(op,campaign_id='d'*32,resource_sha256=source_model.MANIFEST,
        expires_epoch_us=1160000000,handoff_deadline_monotonic=200,peers=source_model.PEERS,
        boots=source_model.BOOTS,radio_permission=source_model.PERMISSION,parent=parent,
        clock=tick.mono,epoch=tick.epoch,sleep=tick.sleep)
    collected,verified=op.collect,op.verify
    op.collect=lambda unit,run:dict(collected(unit,run),start_monotonic_us=10000000)
    op.verify=lambda unit,campaign,request,response:dict(verified(unit,campaign,request,response),
        verified_monotonic_us=11000000,useful_verified_bytes=1000,reused=False)
    return pilot,op


@pytest.mark.parametrize('publisher,requester',[('unit-b','unit-c'),('unit-a','unit-c'),('unit-c','unit-a')])
def test_direct_route_keeps_real_pair_debt_source_provenance_and_original_recipient(publisher,requester):
    pilot,op=controller();verified=pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher=publisher,requester=requester)
    assert [(v['sender'],v['receiver']) for v in pilot.links]==[(requester,publisher),(publisher,requester)]
    assert all(v['phase']=='complete' for v in pilot.links)
    assert verified['unit']==requester and verified['publisher_verified'] and not verified['reused']
    assert verified['request_to_verification_us']==1000000 and verified['useful_verified_bytes_per_second']==1000
    packets=[e[2] for e in op.events if e[0]=='prepare']
    reply=next(p for p in packets if p['unit']==publisher and p['grant']['service']['format']=='MR1.response')
    assert reply['payload_base64'] is None and reply['source_run_id']==dict(kind='served_public_chunk',request_run_id=pilot.links[0]['run_id'])
    assert {p['unit'] for p in packets}=={publisher,requester} and len(packets)==4
    assert all(p['application_expires_epoch_us']==pilot.expires_epoch_us for p in packets)
    assert pilot.parent.snapshot()['allocations']==2
    if publisher=='unit-c':assert not any(e[0]=='prepare' and e[1]=='unit-b' for e in op.events)


@pytest.mark.parametrize('publisher,requester',[('unit-c','unit-c'),('unit-a','unit-a'),('unit-b','unit-a'),('unit-c','unit-b'),('foreign','unit-a'),(True,'unit-a'),('unit-c',None)])
def test_unreviewed_routes_never_pay_or_prepare(publisher,requester):
    pilot,op=controller()
    with pytest.raises(WireError):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher=publisher,requester=requester)
    assert not op.events and not pilot.links and pilot.parent.snapshot()['allocations']==0


def test_reverse_refuses_missing_requester_and_rebound_verification():
    pilot,op=controller();pilot.parent.policy=replace(pilot.parent.policy,units=tuple(u for u in pilot.parent.policy.units if u.unit!='unit-a'))
    with pytest.raises(WireError):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher='unit-c',requester='unit-a')
    assert not op.events and not pilot.links
    pilot,op=controller();verify=op.verify
    op.verify=lambda *args:dict(verify(*args),unit='unit-c')
    with pytest.raises(WireError,match='verification_missing_or_rebound'):pilot.direct_piece(REQUEST,RESPONSE,'lora',publisher='unit-c',requester='unit-a')
    assert not pilot.verified and pilot.parent.snapshot()['allocations']==2


@pytest.mark.parametrize('unit',['unit-a','unit-b','unit-c'])
def test_reverse_owner_scope_preserves_exact_caps_and_no_B(unit):
    radio=rpc.Cost(256,60000000);tcp=rpc.Cost(ip_frames=2048,tx_stream_bytes=1048576,rx_stream_bytes=1048576)
    assert rpc.endpoint_budget(unit,'c-bulk-v1',radio,tcp)==(rpc.Cost() if unit=='unit-b' else radio.plus(radio))
    assert rpc.endpoint_budget(unit,'c-bulk-v1',radio,tcp)==rpc.endpoint_budget(unit,'a-bulk-v1',radio,tcp)


@pytest.mark.parametrize('unit,payload,accepted',[('unit-c',{},True),('unit-c',{'recipient':'unit-c'},True),('unit-a',{'recipient':'unit-a'},True),('unit-a',{},False),('unit-c',{'recipient':'unit-a'},False),('unit-b',{'recipient':'unit-b'},False),('unit-a',{'recipient':True},False)])
def test_readonly_observer_requires_selected_recipient(unit,payload,accepted):
    if accepted:rpc.cache_recipient(unit,payload)
    else:
        with pytest.raises(RuntimeError):rpc.cache_recipient(unit,payload)


def captured(root,run,payload,format,unit,original,*,sends=False):
    directory=root/'runs'/run;directory.mkdir(parents=True,mode=0o700);directory.parent.chmod(0o700)
    receipt=dict(schema='meshticulum.physical-service-run.v1',unit=unit,host_boot_id=facts(unit)['host_boot_id'],
        complete=True,sends=sends,service_format=format,service_object_durable=True,
        service_object_sha256=hashlib.sha256(payload).hexdigest(),
        original_application_expires_epoch_us=original['application_expires_epoch_us'],
        original_application_deadline_us=original['application_deadline_monotonic_us'])
    _write(directory/'active',b'active\n');_write(directory/'result.json',json.dumps(receipt).encode())
    _write(directory/('sender.bin' if sends else 'receiver.bin'),payload)
    if sends:_write(directory/'grant.json',json.dumps(dict(object=dict(bytes=len(payload),sha256=hashlib.sha256(payload).hexdigest()))).encode())
    return directory


def native_fixture(tmp_path,pack,monkeypatch):
    state={unit:tmp_path/unit for unit in ('unit-a','unit-c')};contexts={}
    expiry=clock()[0]+20000000
    for unit,role in [('unit-c','source'),('unit-a','recipient')]:
        root=state[unit];root.mkdir(mode=0o700);install(package(pack,unit,role),state=root,facts=facts(unit))
        directory=root/'campaigns'/('c'*32);directory.mkdir(parents=True,mode=0o700);directory.parent.chmod(0o700)
        contexts[unit]=original_context(directory,unit=unit,host_boot_id=facts(unit)['host_boot_id'],resource_sha256=pack[2],expires_epoch_us=expiry,clock=clock)
    request=resource.encode_request('/packs/'+pack[2]+'/chunks/'+pack[3]['chunks'][0]['sha256']+'.json',0,16356)
    captured(state['unit-c'],'1'*32,request,'MR1.request','unit-c',contexts['unit-c'])
    response=serve_chunk(state['unit-c'],'1'*32,facts=facts('unit-c'),clock=clock,seed=state['unit-c']/'public'/pack[2]/'pack',trust=pack[1],manifest_sha256=pack[2])
    sent=captured(state['unit-a'],'1'*32,request,'MR1.request','unit-a',contexts['unit-a'],sends=True)
    received=captured(state['unit-a'],'2'*32,response,'MR1.response','unit-a',contexts['unit-a'])
    monkeypatch.setattr(node,'STATE',state['unit-a']);monkeypatch.setattr(node,'_facts',facts)
    return state,sent,received,response


def test_actual_signed_C_store_serves_authenticated_request_and_A_durable_verifier_and_observer(pack,tmp_path,monkeypatch):
    state,sent,received,response=native_fixture(tmp_path,pack,monkeypatch)
    assert not (state['unit-a']/'public'/pack[2]/'received'/'chunks').exists()
    value=node.verify('unit-a','c'*32,'1'*32,'2'*32)
    assert value['unit']=='unit-a' and value['publisher_verified'] and not value['reused'] and not value['private_admission'] and not value['sage_imported']
    assert json.loads((received/'verified-piece.json').read_bytes())==value
    raw=(pack[0]/'manifest.json').read_bytes();sig=(pack[0]/'manifest.sig.json').read_bytes();trust=json.dumps(pack[1]).encode()
    pins=dict(manifest=pack[2],signature=hashlib.sha256(sig).hexdigest(),trust=hashlib.sha256(trust).hexdigest())
    observed=_observe_local_anchor(state['unit-a']/'public',raw,sig,trust,pins,(value['chunk_sha256'],))
    assert observed['status']=='complete' and observed['verified_present']==[value['chunk_sha256']]
    assert not observed['radio_opened'] and not observed['socket_opened'] and not observed['private_store_opened']


@pytest.mark.parametrize('fault',['binding','payload','wrong_recipient','expired_request','retired_response','revoked_publisher'])
def test_reverse_signed_capture_checks_refuse_before_verified_piece(pack,tmp_path,monkeypatch,fault):
    state,sent,received,response=native_fixture(tmp_path,pack,monkeypatch)
    if fault in ('binding','payload'):
        if fault=='binding':
            h=resource.RESPONSE.unpack(response[:resource.RESPONSE.size]);response=resource.RESPONSE.pack(h[0],b'z'*16,*h[2:])+response[resource.RESPONSE.size:]
        else:response=response[:-1]+bytes([response[-1]^1])
        (received/'receiver.bin').write_bytes(response);receipt=json.loads((received/'result.json').read_bytes());receipt['service_object_sha256']=hashlib.sha256(response).hexdigest();(received/'result.json').write_text(json.dumps(receipt))
    elif fault=='wrong_recipient':
        receipt=json.loads((received/'result.json').read_bytes());receipt['unit']='unit-c';(received/'result.json').write_text(json.dumps(receipt))
    elif fault=='expired_request':
        receipt=json.loads((sent/'result.json').read_bytes());receipt['original_application_deadline_us']=clock()[1]-1;(sent/'result.json').write_text(json.dumps(receipt))
    elif fault=='retired_response':(received/'active').write_bytes(b'retired\n')
    else:
        trust=deepcopy(pack[1]);trust['keys'][0]['status']='revoked';(state['unit-a']/'public'/pack[2]/'trust.json').write_text(json.dumps(trust))
    with pytest.raises(ValueError):node.verify('unit-a','c'*32,'1'*32,'2'*32)
    assert not (received/'verified-piece.json').exists() and not (state['unit-a']/'public'/pack[2]/'received'/'chunks').exists()


def test_A_recipient_refuses_management_chunk_and_B_recipient_role(pack,tmp_path):
    for unit,value in [('unit-a',package(pack,'unit-a','source')),('unit-b',package(pack,'unit-b','recipient'))]:
        root=tmp_path/unit;root.mkdir(mode=0o700)
        if unit=='unit-a':value['role']='recipient'
        with pytest.raises(ValueError):install(value,state=root,facts=facts(unit))
        assert not (root/'public').exists()


def test_actual_observer_dispatch_A_uses_selected_public_bytes_and_checks_facts(pack,tmp_path,monkeypatch):
    state,sent,received,response=native_fixture(tmp_path,pack,monkeypatch)
    node.verify('unit-a','c'*32,'1'*32,'2'*32)
    raw=(pack[0]/'manifest.json').read_bytes();sig=(pack[0]/'manifest.sig.json').read_bytes();trust=json.dumps(pack[1]).encode()
    payload=dict(recipient='unit-a',manifest=base64.b64encode(raw).decode(),signature=base64.b64encode(sig).decode(),trust=base64.b64encode(trust).decode(),
        pins=dict(manifest=pack[2],signature=hashlib.sha256(sig).hexdigest(),trust=hashlib.sha256(trust).hexdigest()),wanted=[pack[3]['chunks'][0]['sha256']])
    class RuntimeControl:
        def __init__(self,*args):self.checked=False
        def __enter__(self):return self
        def __exit__(self,*args):assert self.checked or args[0] is not None
        def check(self):self.checked=True
    from lantern_meshticulum.bench import public_cache_observer as observer
    from types import SimpleNamespace
    monkeypatch.setattr(rpc,'Runtime',RuntimeControl);monkeypatch.setattr(rpc,'os',SimpleNamespace(geteuid=lambda:0))
    monkeypatch.setattr(rpc,'native_facts',facts)
    monkeypatch.setattr(observer,'observe_native',lambda *args:_observe_local_anchor(state['unit-a']/'public',*args))
    result=rpc.dispatch('observe-cache','unit-a',payload,runtime_root=tmp_path,manifest_sha256='f'*64)
    assert result['unit']=='unit-a' and result['action']=='observe-cache' and result['observation']['status']=='complete'
    assert result['radio_opened'] is result['private_production'] is result['global_debt_complete'] is False
    samples=[facts('unit-a'),dict(facts('unit-a'),host_boot_id='00000000-0000-4000-8000-000000000001')]
    monkeypatch.setattr(rpc,'native_facts',lambda _:samples.pop(0))
    with pytest.raises(RuntimeError,match='cache_observation_facts_changed'):
        rpc.dispatch('observe-cache','unit-a',payload,runtime_root=tmp_path,manifest_sha256='f'*64)
