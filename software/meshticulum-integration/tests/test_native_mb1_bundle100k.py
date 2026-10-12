"""Protected source composition only: modeled host facts/authorization, no RF."""
from copy import deepcopy
from dataclasses import asdict,replace
import base64,hashlib,json
from pathlib import Path
from types import MappingProxyType

import pytest

from meshticulum.bench.public_block import REPRESENTATION_MARKER,TRANSPORT_DESCRIPTOR_MARKER
from meshticulum.resources import knowledge_pack as kp,lamplighter as ll
from lantern_meshticulum.bench import pilot_bundle as bundle,gfsk_mb1_bundle as mode,pilot_resource_node as resource_node
from lantern_meshticulum.bench.profiles import GFSK_MB1_BUNDLE_KIND,GFSK_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from lantern_meshticulum.bench.pilot_parent_caps import ParentCaps,Cost,canonical,child_caps,ParentCapsError
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval,_open_existing
from lantern_meshticulum.bench.pilot_controller import pair_packages
from lantern_meshticulum.bench.pilot_resources import serve_chunk
from lantern_meshticulum.bench.pilot_parent_snapshot import _child
from lantern_meshticulum.ip.node import _write
from tests.test_pilot_bundle import data,postterminal_fixture
from tests.test_public32_pilot import tmp_path,protected_source,protected_records,clock,facts,PEERS,BOOT


@pytest.fixture
def mbdata(data):
    payload,chunks,old,p=data
    stages=[dict(campaign_id=v['campaign_id'],issued_epoch_us=p.issued_epoch_us+(105+i*240)*1000000,
        expires_epoch_us=p.issued_epoch_us+(285+i*240)*1000000,
        handoff_end_epoch_us=p.issued_epoch_us+(345+i*240 if i<4 else 1255)*1000000)
        for i,v in enumerate(old['stages'])]
    value=bundle.context_value(base64.b64decode(old['manifest_base64']),base64.b64decode(old['signature_base64']),
        old['trust'],old['manifest_sha256'],chunks=chunks,
        policy_clock=(p.issued_epoch_us,p.original_monotonic_us,p.issued_epoch_us+1350000000,p.original_monotonic_us+1350000000),
        stages=stages,qualification_kind=GFSK_MB1_BUNDLE_KIND)
    p=replace(p,qualification_kind=GFSK_MB1_BUNDLE_KIND,expires_epoch_us=value['expires_epoch_us'],
        deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=kp.sha256_hex(canonical(value)))
    return payload,chunks,value,p


def pair(value,p,index,now,response=None,source_run=None):
    c=bundle.BundleContext(value,p);request,_=c.exchange(index);stage=value['stages'][index]
    payload=request if response is None else response
    return pair_packages(campaign_id=stage['campaign_id'],resource_sha256=value['manifest_sha256'],
        application_expires_epoch_us=stage['expires_epoch_us'],sender='unit-a' if response is None else 'unit-c',
        receiver='unit-c' if response is None else 'unit-a',transport='lora',format='MR1.request' if response is None else 'MR1.response',
        descriptor=dict(bytes=len(payload),sha256=kp.sha256_hex(payload)),
        source=None if response is None else dict(kind='served_public_chunk',request_run_id=source_run),
        request=request if response is None else None,peers=PEERS,boots={u:BOOT for u in PEERS},now_epoch_us=now,
        radio_permission=dict(country='MY',antenna_confirmed=True),public32=True,profile=GFSK_NAME,
        mb1_descriptor_sha256=value['transport_descriptor_sha256'])


def protected_units(base,p,value,monkeypatch):
    """Actual Reader/Runtime/seals/SQLite, with one shared modeled policy."""
    from lantern_meshticulum.bench.retained_capture import PAID_HISTORY
    roots={};records={};prior_pins={}
    for unit in PEERS:
        root=base/unit;root.mkdir(mode=0o700);roots[unit]=root
        _,directory,binding=protected_records(root,p,unit)
        records[unit]=(directory,binding)
        prior_pins[unit]=kp.sha256_hex((directory/'prior.json').read_bytes())
    registry={u:dict(observation_sha256=pin) for u,pin in prior_pins.items()}
    p=replace(p,units=tuple(replace(u,prior_snapshot_sha256=prior_pins[u.unit]) for u in p.units),
        prior_snapshot_sha256=kp.sha256_hex(canonical(registry)))
    owner0=json.loads((records['unit-a'][0]/'owner.json').read_bytes())
    pins=dict(scope_sha256='f'*64,core_commit='002694c3a7ad3b402891029ded0c82fabecde691',
        history_receipt_sha256='a'*64,preceding_parent=PAID_HISTORY[-1])
    # Model-only source assignment. Default source has every physical pin None.
    monkeypatch.setattr(mode,'PHYSICAL_BINDINGS',MappingProxyType(pins))
    commitment=bundle.public_context_commitment(value,parent_id=p.parent_id)
    for unit,(directory,binding) in records.items():
        captured=json.loads((directory/'capture.json').read_bytes());phase=captured['native_phase']
        phase['deadline_monotonic_us']=p.original_monotonic_us+1355000000
        captured.update(schema=bundle.COMMITTED_CONTRACTS[GFSK_MB1_BUNDLE_KIND][6],bundle_capture_commitment=commitment,
            phase_end_epoch_us=p.issued_epoch_us+1355000000)
        owner=json.loads((directory/'owner.json').read_bytes())
        owner.update(qualification_kind=GFSK_MB1_BUNDLE_KIND,policy_digest=p.digest,prior_registry=registry,
            bundle_context=value,bundle_capture_commitment=commitment,radio_profile_name=GFSK_NAME,radio_modem=GFSK_NAME,
            native_phase={unit:phase},
            rf_profile=profile_record(profile_for(GFSK_NAME)),mb1_bundle_scope_sha256=pins['scope_sha256'],
            core_commit=pins['core_commit'],history_receipt_sha256=pins['history_receipt_sha256'],
            capture_record_sha256=kp.sha256_hex(canonical(captured)),phase_end_epoch_us=captured['phase_end_epoch_us'])
        for name,v in (('capture.json',captured),('owner.json',owner),('policy.json',p.to_dict())):
            (directory/name).write_bytes(canonical(v))
        approve=_Public32Approval(directory,p,unit,facts(unit),clock)
        checked=lambda raw,u=unit:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit(u)),prior_snapshot_sha256=p.prior_snapshot_sha256)
        caps=NativeCaps.enroll(directory/'native.sqlite',p,unit=unit,clock=clock,
            facts=lambda u=unit:NativeFacts(u,BOOT,p.unit(u).peer),approve=approve,prior_check=checked,history_binding_sha256=binding)
        assert caps.close()
    return roots,p


def test_missing_new_physical_scope_fails_closed(monkeypatch):
    assert set(mode.PHYSICAL_BINDINGS)=={'scope_sha256','core_commit','history_receipt_sha256','preceding_parent'}
    monkeypatch.setattr(mode,'PHYSICAL_BINDINGS',MappingProxyType({k:None for k in mode.PHYSICAL_BINDINGS}))
    with pytest.raises(Exception,match='pending_native_mb1_scope'):mode.require_bindings()


@pytest.mark.parametrize('field',['runtime_sha256','manifest_sha256'])
def test_caller_supplied_runtime_or_manifest_must_match_actual_protected_source(mbdata,tmp_path,protected_source,monkeypatch,field):
    _,_,value,p=mbdata;base=tmp_path/'wrong-source';base.mkdir(mode=0o700)
    roots,p=protected_units(base,p,value,monkeypatch)
    directory=roots['unit-a']/'parents'/p.parent_id
    owner=json.loads((directory/'owner.json').read_bytes());owner[field]='f'*64
    (directory/'owner.json').write_bytes(canonical(owner))
    with pytest.raises(Exception):_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)


@pytest.mark.parametrize('fault',['descriptor_pin','stage_length','stage_hash','kind','expiry','one_MB'])
def test_native_context_rejects_relabel_rehash_length_hash_and_window_changes(mbdata,fault):
    _,_,value,p=mbdata;value=deepcopy(value)
    if fault=='descriptor_pin':value['transport_descriptor_sha256']='f'*64
    elif fault=='stage_length':value['stages'][0]['response_bytes']+=1
    elif fault=='stage_hash':value['stages'][0]['response_sha256']='f'*64
    elif fault=='kind':value['schema']=bundle.SCHEMA_GFSK
    elif fault=='expiry':value['expires_epoch_us']+=1
    else:value['whole_bytes']=1000000
    p=replace(p,bundle_context_sha256=kp.sha256_hex(canonical(value)))
    with pytest.raises(Exception):bundle.BundleContext(value,p)


def test_new_grant_capability_kind_and_variable_object_are_bound_before_prepayment(mbdata):
    _,_,value,p=mbdata;c=bundle.BundleContext(value,p);request,_=c.exchange(0)
    reply=bundle.resource.RESPONSE.pack(bundle.resource.MAGIC,hashlib.sha256(request).digest()[:16],0,
        c.transport.part(0)['mb1_bytes'],0)+b'x'*c.transport.part(0)['mb1_bytes']
    current=pair(value,p,0,value['stages'][0]['issued_epoch_us'],reply,'2'*32)
    package=current['packages']['unit-a'];child=child_caps(package,p)
    assert child.cost==Cost(256,60000000) and _child(child.to_dict(),p)==child.cost
    wrong=deepcopy(package);wrong['grant']['service'][TRANSPORT_DESCRIPTOR_MARKER]='f'*64
    with pytest.raises(Exception):c.package(wrong,value['stages'][0]['issued_epoch_us'])
    with pytest.raises(ParentCapsError):child_caps(package,replace(p,qualification_kind=GFSK_BUNDLE_KIND))
    wrong=deepcopy(package);wrong['grant']['service'].pop(REPRESENTATION_MARKER)
    with pytest.raises(ParentCapsError):child_caps(wrong,p)


@pytest.mark.parametrize('fault',[None,'receive_hash','receive_length','expired'])
def test_protected_A_C_native_serve_verify_five_parts_whole_SHA_and_full_caps(mbdata,tmp_path,protected_source,monkeypatch,fault):
    from lantern_meshticulum.ip import node
    payload,chunks,value,p=mbdata;base=tmp_path/'native';base.mkdir(mode=0o700)
    roots,p=protected_units(base,p,value,monkeypatch)
    now=[clock()];local_clock=lambda:now[0]
    monkeypatch.setattr(node,'_facts',lambda unit:facts(unit))
    monkeypatch.setattr(resource_node,'_facts',lambda unit:facts(unit))
    monkeypatch.setattr(resource_node,'clock',local_clock);monkeypatch.setattr(resource_node,'STATE',roots['unit-a'])
    c=bundle.BundleContext(value,p)
    for unit in ('unit-a','unit-c'):
        public=roots[unit]/'public'/value['manifest_sha256'];public.mkdir(parents=True,mode=0o700)
        for parent in (public,public.parent):parent.chmod(0o700)
        directory=public/('received' if unit=='unit-a' else 'pack');directory.mkdir(mode=0o700)
        _write(public/'trust.json',canonical(value['trust']));_write(directory/'manifest.json',c.manifest_raw)
        _write(directory/'manifest.sig.json',c.signature_raw)
        if unit=='unit-c':
            (directory/'chunks').mkdir(mode=0o700)
            for name,raw in chunks.items():_write(directory/'chunks'/(name+'.json'),raw)
    cache=ll.VerifiedCache(roots['unit-c']/'public'/value['manifest_sha256']/'pack',value['trust'],value['manifest_sha256'])
    control=tmp_path/'controller';control.mkdir(mode=0o700)
    parent=ParentCaps.enroll(control/'caps.sqlite',p,clock=local_clock,boot_reader=lambda:BOOT)
    receipts=[];wire_bytes=0
    try:
        for index,stage in enumerate(value['stages']):
            now[0]=(stage['issued_epoch_us']+1000000,p.original_monotonic_us+stage['issued_epoch_us']-p.issued_epoch_us+1000000)
            request,_=c.exchange(index);response=c.transport.response(index,cache);wire_bytes+=len(response)
            request_pair=pair(value,p,index,now[0][0]);response_pair=pair(value,p,index,now[0][0],response,request_pair['run_id'])
            for current,body in ((request_pair,request),(response_pair,response)):
                permit=parent.prepay_pair(tuple(current['packages'].values()));children=parent.consume(permit)
                for unit,packet in current['packages'].items():
                    packet.update(schema='meshticulum.public-pilot-package.v2',parent=dict(schema='meshticulum.public-parent-allocation.v1',
                        parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(v for v in children if v.unit==unit).to_dict()))
                for unit in ('unit-a','unit-c'):postterminal_fixture(roots[unit],p,current,unit,body,local_clock)
            if fault is not None:
                response_directory=roots['unit-a']/'runs'/response_pair['run_id']
                if fault in ('receive_hash','receive_length'):
                    changed=response[:-1] if fault=='receive_length' else response[:-1]+bytes([response[-1]^1])
                    (response_directory/'receiver.bin').write_bytes(changed)
                else:
                    now[0]=(stage['expires_epoch_us'],p.original_monotonic_us+stage['expires_epoch_us']-p.issued_epoch_us)
                with pytest.raises(Exception):resource_node.verify('unit-a',stage['campaign_id'],request_pair['run_id'],response_pair['run_id'])
                assert not (roots['unit-a']/'public'/value['manifest_sha256']/'received/chunks').exists()
                assert not (roots['unit-a']/'campaigns'/p.parent_id/'bundle').exists()
                # Read-only row evidence: failed acceptance does not undo the
                # two complete original reservations or change policy limits.
                import sqlite3
                for unit in ('unit-a','unit-c'):
                    db_path=roots[unit]/'parents'/p.parent_id/'native.sqlite'
                    with sqlite3.connect('file:'+str(db_path)+'?mode=ro',uri=True) as db:
                        rows=[json.loads(body) for body, in db.execute('SELECT body FROM claims')]
                        assert len(rows)==2
                        assert sum(row['child']['cost']['radio_attempts'] for row in rows)==512
                        assert sum(row['child']['cost']['estimated_airtime_us'] for row in rows)==120000000
                    assert p.unit(unit).limits==p.unit(unit).prior_debt.plus(Cost(2560,600000000))
                return
            verified=resource_node.verify('unit-a',stage['campaign_id'],request_pair['run_id'],response_pair['run_id'])
            assert verified['transport_representation']=='MB1' and verified['response_wire_bytes']==len(response)
            assert verified['bundle_part']['useful_decoded_bytes']==c.expected['parts'][index]['raw_bytes']
            receipts.append(verified)
        caps=_open_existing(roots['unit-a'],p.parent_id,facts('unit-a'),local_clock)
        try:done=bundle.finish_native_bundle(caps,state=roots['unit-a'],clock=local_clock)
        finally:assert caps.close()
        assert done['whole_complete'] is True and done['useful_decoded_bytes']==100000
        assert done['whole_file_sha256']==kp.sha256_hex(payload) and wire_bytes==103345
        assert (roots['unit-a']/'campaigns'/p.parent_id/'bundle/bundle.bin').read_bytes()==payload
        for unit in ('unit-a','unit-c'):
            caps=_open_existing(roots[unit],p.parent_id,facts(unit),local_clock)
            try:snapshot=caps.snapshot()
            finally:assert caps.close()
            assert snapshot['claims']==snapshot['prepared']==snapshot['starts']==10
            assert snapshot['debt']==asdict(p.unit(unit).limits)
            assert p.unit(unit).limits==p.unit(unit).prior_debt.plus(Cost(2560,600000000))
    finally:assert parent.close()
