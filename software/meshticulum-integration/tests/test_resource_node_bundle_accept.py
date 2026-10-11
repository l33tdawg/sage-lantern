"""Real verify→protected NativeCaps→part publication; host/RF results modeled only."""
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import os

import pytest
from meshticulum.resources import resource
from lantern_meshticulum.bench import pilot_resource_node as node,pilot_bundle as bundle
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,canonical
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.ip.node import _write
from tests.test_pilot_bundle import data,packet,protected_bundle,postterminal_fixture
from tests.test_bundle_v2_native import v2,records
from tests.test_public32_pilot import tmp_path,protected_source,clock,facts,PEERS,BOOT


@pytest.mark.parametrize('kind',['v1','v2'])
def test_verify_actual_signed_piece_requests_and_publishes_bundle_part_via_protected_owner(data,v2,tmp_path,protected_source,monkeypatch,kind):
    payload,chunks,value,p=data if kind=='v1' else v2
    root=tmp_path/'native'
    if kind=='v1':p,directory=protected_bundle(root,p,value,'unit-a')
    else:
        p,directory,binding=records(root,p,value,'unit-a')
        approve=_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)
        prior=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit('unit-a')),prior_snapshot_sha256=p.prior_snapshot_sha256)
        caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=clock,facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=approve,prior_check=prior,history_binding_sha256=binding)
        assert caps.close()
    c=bundle.BundleContext(value,p);stage=value['stages'][0];request,entry=c.exchange(0);raw=chunks[entry['chunk_sha256']]
    response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(raw),0)+raw
    public=root/'public'/value['manifest_sha256'];received=public/'received';received.mkdir(mode=0o700,parents=True)
    for parent in (public,public.parent):parent.chmod(0o700)
    _write(public/'trust.json',canonical(value['trust']))
    _write(received/'manifest.json',c.manifest_raw);_write(received/'manifest.sig.json',c.signature_raw)
    now=(stage['issued_epoch_us']+1000000,801000000);local_clock=lambda:now
    request_pair=packet(value,p,0,now[0])
    reply_pair=pair_packages(campaign_id=stage['campaign_id'],resource_sha256=value['manifest_sha256'],application_expires_epoch_us=stage['expires_epoch_us'],
        sender='unit-c',receiver='unit-a',transport='lora',format='MR1.response',descriptor=dict(bytes=len(response),sha256=hashlib.sha256(response).hexdigest()),
        source=dict(kind='served_public_chunk',request_run_id=request_pair['run_id']),request=None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now[0],
        radio_permission={'country':'MY','antenna_confirmed':True},public32=True)
    control=tmp_path/'controller';control.mkdir(mode=0o700);parent=ParentCaps.enroll(control/'caps.sqlite',p,clock=local_clock,boot_reader=lambda:BOOT)
    try:
        for pair,body in ((request_pair,request),(reply_pair,response)):
            permit=parent.prepay_pair(tuple(pair['packages'].values()));children=parent.consume(permit)
            for unit,package in pair['packages'].items():
                package['schema']='meshticulum.public-pilot-package.v2';package['parent']=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,
                    policy_digest=p.digest,allocation_id=permit.key,child=next(x for x in children if x.unit==unit).to_dict())
            postterminal_fixture(root,p,pair,'unit-a',body,local_clock)
    finally:assert parent.close()
    from lantern_meshticulum.ip import node as ipnode
    monkeypatch.setattr(ipnode,'_facts',facts);monkeypatch.setattr(node,'_facts',facts);monkeypatch.setattr(node,'STATE',root);monkeypatch.setattr(node,'clock',local_clock)
    actual=bundle.accept_native_part;calls=[]
    def checked(caps,**kwargs):
        assert type(caps) is NativeCaps and caps.policy==p and caps.current() is True
        calls.append(caps);return actual(caps,**kwargs)
    monkeypatch.setattr(bundle,'accept_native_part',checked)
    result=node.verify('unit-a',stage['campaign_id'],request_pair['run_id'],reply_pair['run_id'])
    assert len(calls)==1 and calls[0]._closed
    assert result['publisher_verified'] is True and result['bundle_part']['useful_decoded_bytes']==entry['raw_bytes']==23900
    assert result['bundle_part']['bundle_context_sha256']==p.bundle_context_sha256 and result['bundle_part']['part_index']==0
    assert not result['bundle_part']['native_transport_qualified']
    assert json.loads((root/'runs'/reply_pair['run_id']/'verified-piece.json').read_bytes())==result
