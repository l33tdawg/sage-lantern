"""Actual file/SQLite/held-record composition; host facts modeled, no RF."""
from copy import deepcopy
from dataclasses import replace,asdict
import base64,hashlib,json,sys
from pathlib import Path
import pytest
from lantern_meshticulum.bench import pilot_bundle as bundle,gfsk_bundle as mode
from lantern_meshticulum.bench.profiles import GFSK_BUNDLE_KIND,GFSK_NAME,profile_for,profile_record
from lantern_meshticulum.bench.pilot_parent_caps import ParentPolicy,ParentCaps,Cost,canonical,child_caps,ParentCapsError
from lantern_meshticulum.bench.pilot_native_caps import NativeCaps,NativeFacts,_Public32Approval,_open_existing
from lantern_meshticulum.bench.pilot_controller import pair_packages,Controller
from meshticulum.resources import byte_bundle,resource
from tests import test_pilot_bundle as previous
from tests.test_pilot_bundle import data,postterminal_fixture
from tests.test_public32_pilot import tmp_path,protected_source,protected_records,clock,facts,PEERS,BOOT
from tests.test_pilot_parent_snapshot import root_observation


@pytest.fixture
def gfdata(data):
    payload,chunks,value,p=data
    value=deepcopy(value);value.update(schema=bundle.SCHEMA_GFSK,
        expires_epoch_us=p.issued_epoch_us+1350000000,
        deadline_monotonic_us=p.original_monotonic_us+1350000000)
    for i,stage in enumerate(value['stages']):
        stage.update(issued_epoch_us=p.issued_epoch_us+(105+i*240)*1000000,
            expires_epoch_us=p.issued_epoch_us+(285+i*240)*1000000,
            handoff_end_epoch_us=p.issued_epoch_us+(345+i*240 if i<4 else 1255)*1000000)
    p=replace(p,qualification_kind=GFSK_BUNDLE_KIND,expires_epoch_us=value['expires_epoch_us'],
        deadline_monotonic_us=value['deadline_monotonic_us'],bundle_context_sha256=hashlib.sha256(canonical(value)).hexdigest())
    return payload,chunks,value,p


def pair(value,p,index,now,response=None,source_run=None,profile=GFSK_NAME):
    c=bundle.BundleContext(value,p);request,entry=c.exchange(index);stage=value['stages'][index]
    return pair_packages(campaign_id=stage['campaign_id'],resource_sha256=value['manifest_sha256'],
        application_expires_epoch_us=stage['expires_epoch_us'],sender='unit-c' if response is not None else 'unit-a',
        receiver='unit-a' if response is not None else 'unit-c',transport='lora',format='MR1.response' if response is not None else 'MR1.request',
        descriptor=dict(bytes=len(response if response is not None else request),sha256=hashlib.sha256(response if response is not None else request).hexdigest()),
        source=dict(kind='served_public_chunk',request_run_id=source_run) if response is not None else None,
        request=None if response is not None else request,peers=PEERS,boots={u:BOOT for u in PEERS},
        now_epoch_us=now,radio_permission=dict(country='MY',antenna_confirmed=True),public32=True,profile=profile)


def test_explicit_GFSK_bundle_grants_do_not_change_old_SF7_admission(gfdata,data):
    _,_,value,p=gfdata;package=pair(value,p,0,value['stages'][0]['issued_epoch_us'])['packages']['unit-a']
    child=child_caps(package,p);assert child.cost==Cost(256,60000000)
    assert child.to_dict()['metadata']['radio_modem']==GFSK_NAME
    with pytest.raises(ParentCapsError):child_caps(package,data[3])
    wrong=deepcopy(package);wrong['grant'].pop('radio_modem')
    with pytest.raises(ParentCapsError):child_caps(wrong,p)


def test_GFSK_public32_snapshot_requires_exact_full_profile_caps(gfdata):
    from lantern_meshticulum.bench.pilot_parent_snapshot import _child
    from lantern_meshticulum.bench.pilot_parent_caps import DOMAIN
    _,_,value,p=gfdata;package=pair(value,p,0,value['stages'][0]['issued_epoch_us'])['packages']['unit-a']
    original=child_caps(package,p).to_dict();assert _child(original,p)==Cost(256,60000000)
    changed=deepcopy(original);changed['metadata']['profile']['max_boot_attempts']=128
    changed['cost']['radio_attempts']=128
    changed['digest']=hashlib.sha256(DOMAIN+b'child\0'+canonical(changed['metadata'])).hexdigest()
    with pytest.raises(Exception):_child(changed,p)
    wrong=pair(value,p,0,value['stages'][0]['issued_epoch_us'],profile='sf7-500')['packages']['unit-a']
    with pytest.raises(ParentCapsError):child_caps(wrong,p)


def test_GFSK_whole_context_retains_exact_v3_clock_schedule_and_useful_denominator(gfdata,tmp_path):
    payload,chunks,value,p=gfdata;c=bundle.BundleContext(value,p)
    assert c.value['expires_epoch_us']-c.value['original_epoch_us']==1350000000
    receiver=tmp_path/'receiver';receiver.mkdir(mode=0o700)
    now=[p.issued_epoch_us,p.original_monotonic_us]
    with byte_bundle.Receiver(receiver,c.manifest_raw,c.signature_raw,value['trust'],value['manifest_sha256'],
        expires_epoch_us=p.expires_epoch_us,clock=lambda:tuple(now),boot_reader=lambda:BOOT) as r:
        for i,part in enumerate(c.expected['parts']):r.accept(i,chunks[part['chunk_sha256']])
        done=r.finish()
    assert done['useful_decoded_bytes']==100000 and done['serialized_chunk_bytes']==163700
    assert (receiver/'bundle.bin').read_bytes()==payload and done['whole_file_sha256']==hashlib.sha256(payload).hexdigest()
    assert done['native_transport_qualified'] is False


@pytest.mark.parametrize('field,value',[('whole_bytes',100001),('expires_epoch_us',1450000001),('schema',bundle.SCHEMA_V3)])
def test_new_kind_cannot_relabel_whole_or_renew_clock(gfdata,field,value):
    _,_,v,p=gfdata;v=deepcopy(v);v[field]=value;p=replace(p,bundle_context_sha256=hashlib.sha256(canonical(v)).hexdigest())
    with pytest.raises(Exception):bundle.BundleContext(v,p)


def test_pending_scope_stays_closed_and_fresh_boot_does_not_refund_debt(gfdata,monkeypatch):
    from lantern_meshticulum.bench import profiles,retained_capture
    actual=tuple(retained_capture.PAID_HISTORY)
    assert len(actual)==43 and actual[-1]=='bea52c6aa6a5a95501997a46247b514f'
    # Only this historical bundle model uses H42; production stays closed on43.
    monkeypatch.setattr(retained_capture,'PAID_HISTORY',actual[:42])
    monkeypatch.setattr(profiles,'GFSK_HISTORY_RECEIPT_SHA256',None)
    with pytest.raises(Exception,match='pending_gfsk_firmware_source_and_history_bindings'):
        mode.require_bundle_bindings()
    monkeypatch.setattr(profiles,'GFSK_HISTORY_RECEIPT_SHA256','e'*64)
    monkeypatch.setattr(profiles,'GFSK_PRECEDING_PARENT',actual[41])
    monkeypatch.setattr(mode,'GFSK_BUNDLE_SCOPE_SHA256',None)
    with pytest.raises(Exception,match='pending_gfsk_whole_bundle_scope'):mode.require_bundle_bindings()
    monkeypatch.setattr(mode,'GFSK_BUNDLE_SCOPE_SHA256','f'*64)
    assert mode.require_bundle_bindings()=='f'*64
    status=dict(firmware='mtb1-b433927ba6',usb_serial='F8:5B:1B:A5:F6:08',disarmed=True,
        mcu_boot='000008f6a51b5bf8000000000000002d',boot_attempts=0,boot_reserved_estimated_airtime_us=0)
    assert mode.fresh_boot_status(status)['clean_model_spare_attempts']==31
    status['boot_attempts']=39
    with pytest.raises(Exception):mode.fresh_boot_status(status)


def test_five_part_native_SQLite_composition_GFSK_whole_SHA_and_full_debt(gfdata,tmp_path,protected_source,monkeypatch):
    from lantern_meshticulum.ip import node
    payload,chunks,value,p=gfdata;root=tmp_path/'native';root.mkdir(mode=0o700)
    p,directory,binding=protected_records(root,p,'unit-a')
    monkeypatch.setattr(mode,'require_bundle_bindings',lambda:'f'*64)
    commitment=bundle.public_context_commitment(value,parent_id=p.parent_id)
    owner=json.loads((directory/'owner.json').read_bytes());owner.update(qualification_kind=GFSK_BUNDLE_KIND,
        bundle_context=value,bundle_capture_commitment=commitment,radio_profile_name=GFSK_NAME,radio_modem=GFSK_NAME,
        rf_profile=profile_record(profile_for(GFSK_NAME)),gfsk_bundle_scope_sha256='f'*64)
    phase=owner['native_phase']['unit-a'];phase['deadline_monotonic_us']=p.original_monotonic_us+1355000000
    captured=json.loads((directory/'capture.json').read_bytes());captured.update(schema=bundle.COMMITTED_CONTRACTS[GFSK_BUNDLE_KIND][6],bundle_capture_commitment=commitment)
    captured['native_phase']=phase;captured['phase_end_epoch_us']=p.issued_epoch_us+1355000000
    owner['capture_record_sha256']=hashlib.sha256(canonical(captured)).hexdigest()
    for name,v in [('owner.json',owner),('capture.json',captured)]:(directory/name).write_bytes(canonical(v))
    approval=_Public32Approval(directory,p,'unit-a',facts('unit-a'),clock)
    checked=lambda raw:json.loads(raw)==dict(parent_id=p.parent_id,policy_digest=p.digest,unit=asdict(p.unit('unit-a')),prior_snapshot_sha256=p.prior_snapshot_sha256)
    caps=NativeCaps.enroll(directory/'native.sqlite',p,unit='unit-a',clock=clock,
        facts=lambda:NativeFacts('unit-a',BOOT,p.unit('unit-a').peer),approve=approval,prior_check=checked,history_binding_sha256=binding)
    assert caps.close();monkeypatch.setattr(node,'_facts',lambda unit:facts(unit))
    received=root/'public'/value['manifest_sha256']/'received';received.mkdir(mode=0o700,parents=True)
    control=tmp_path/'controller';control.mkdir(mode=0o700);now=[clock()];local_clock=lambda:now[0]
    parent=ParentCaps.enroll(control/'caps.sqlite',p,clock=local_clock,boot_reader=lambda:BOOT);records=[]
    try:
        for i,stage in enumerate(value['stages']):
            now[0]=(stage['issued_epoch_us']+1000000,p.original_monotonic_us+stage['issued_epoch_us']-p.issued_epoch_us+1000000)
            c=bundle.BundleContext(value,p);request,part=c.exchange(i);raw=chunks[part['chunk_sha256']]
            response=resource.RESPONSE.pack(resource.MAGIC,hashlib.sha256(request).digest()[:16],0,len(raw),0)+raw
            request_pair=pair(value,p,i,now[0][0]);response_pair=pair(value,p,i,now[0][0],response,request_pair['run_id'])
            for current,body in [(request_pair,request),(response_pair,response)]:
                permit=parent.prepay_pair(tuple(current['packages'].values()));children=parent.consume(permit)
                for unit,packet in current['packages'].items():
                    packet.update(schema='meshticulum.public-pilot-package.v2',parent=dict(schema='meshticulum.public-parent-allocation.v1',parent_id=p.parent_id,policy_digest=p.digest,allocation_id=permit.key,child=next(c for c in children if c.unit==unit).to_dict()))
                postterminal_fixture(root,p,current,'unit-a',body,local_clock)
            caps=_open_existing(root,p.parent_id,facts('unit-a'),local_clock)
            try:
                verified=dict(schema='meshticulum.pilot-verified-piece.v1',publisher_verified=True,campaign_id=stage['campaign_id'],response_run_id=response_pair['run_id'],manifest_sha256=value['manifest_sha256'],chunk_sha256=part['chunk_sha256'])
                records.append(bundle.accept_native_part(caps,state=root,facts=facts('unit-a'),campaign_id=stage['campaign_id'],request_run_id=request_pair['run_id'],response_run_id=response_pair['run_id'],verified=verified,clock=local_clock))
            finally:assert caps.close()
        caps=_open_existing(root,p.parent_id,facts('unit-a'),local_clock)
        try:done=bundle.finish_native_bundle(caps,state=root,clock=local_clock);snapshot=caps.snapshot()
        finally:assert caps.close()
        assert done['whole_complete'] and done['useful_decoded_bytes']==100000 and done['whole_file_sha256']==hashlib.sha256(payload).hexdigest()
        assert len(records)==5 and (root/'campaigns'/p.parent_id/'bundle/bundle.bin').read_bytes()==payload
        assert snapshot['claims']==snapshot['prepared']==snapshot['starts']==10
        assert snapshot['debt']==asdict(p.unit('unit-a').limits)
    finally:assert parent.close()
